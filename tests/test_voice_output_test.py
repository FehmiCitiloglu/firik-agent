"""Local output setup checks use synthetic PCM and fake PortAudio devices."""
from dataclasses import replace
import struct
import threading
from types import SimpleNamespace
import weakref

import pytest

from firik_agent.voice.audio_devices import AudioDeviceCatalog
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.diagnostics import OUTPUT_TEST_SECONDS, OutputToneTest
from firik_agent.voice.errors import VoiceAudioError
from firik_agent.voice.playback import SoundDevicePlayer
from firik_agent.voice.session import CaptureCoordinator
from test_voice_ui_services import Dispatch, eventually, minimal_service


class OutputBackend:
    """Drive every callback deterministically, with no speaker or wall clock."""
    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    default = SimpleNamespace(device=(-1, 0))

    def __init__(self):
        self.streams = []
        self.blocks = []

    def query_hostapis(self):
        return [{"name": "Synthetic host"}]

    def query_devices(self):
        return [{"name": "Synthetic output", "hostapi": 0, "max_input_channels": 0,
                 "max_output_channels": 1, "default_samplerate": 24000}]

    def check_output_settings(self, **options):
        self.settings = options

    def RawInputStream(self, **options):
        pytest.fail("Output test must never open a microphone")

    def RawOutputStream(self, **options):
        backend = self

        class Stream:
            active = False
            closed = False

            def start(self):
                self.active = True
                while self.active:
                    block = bytearray(options["blocksize"] * 2)
                    try:
                        options["callback"](block, options["blocksize"], None, False)
                    except backend.CallbackStop:
                        backend.blocks.append(bytes(block))
                        break
                    except backend.CallbackAbort:
                        break
                    backend.blocks.append(bytes(block))
                self.active = False
                options["finished_callback"]()

            def abort(self):
                self.active = False

            def close(self):
                self.closed = True

        stream = Stream()
        self.streams.append(stream)
        return stream


def make_test(factory, *, coordinator=None, timeout=1):
    dispatch = Dispatch()
    done, errors = [], []
    test = OutputToneTest(coordinator=coordinator or CaptureCoordinator(), dispatch=dispatch,
        settings=VoiceSettings(), on_done=lambda: done.append(threading.get_ident()),
        on_error=errors.append, player_factory=factory, timeout=timeout)
    return test, dispatch, done, errors


def test_local_tone_uses_selected_output_and_unsaved_gain_on_worker_without_replay_cache():
    backend = OutputBackend()
    selection = AudioDeviceCatalog(backend).list_devices("output")[0].id
    players, workers = [], []
    coordinator = CaptureCoordinator()

    def factory(**options):
        assert coordinator.speech_lock.locked()
        workers.append(threading.get_ident())
        player = SoundDevicePlayer(**options, backend=backend, clock=lambda: 42.)
        players.append(player)
        return player

    test, dispatch, done, errors = make_test(factory, coordinator=coordinator)
    test.settings = replace(VoiceSettings(), output_device=selection, output_volume=.25)
    assert not backend.streams
    test.start()
    assert test.done.wait(1)
    assert not done and not errors
    dispatch.drain()
    assert done == [threading.get_ident()] and not errors
    assert workers and workers[0] != threading.get_ident()
    assert backend.settings["device"] == 0
    output = b"".join(backend.blocks)
    samples = struct.unpack("<" + "h" * (len(output) // 2), output)
    assert len(samples) == round(24000 * OUTPUT_TEST_SECONDS)
    assert samples[0] == samples[-1] == 0
    assert 900 <= max(abs(value) for value in samples) <= 1000
    assert all(stream.closed for stream in backend.streams)
    assert players[0]._last is None and players[0]._closed
    assert not coordinator.speech_lock.locked()
    assert coordinator.claim(object())


@pytest.mark.parametrize("failure", [VoiceAudioError("Çıkış aygıtı yok. Yeniden seçin."),
                                    RuntimeError("private native credential detail")])
def test_output_failure_cleans_native_ownership_and_keeps_private_errors_safe(failure):
    class Player:
        closed = False
        def play(self, audio, cancel):
            raise failure
        def shutdown(self):
            self.closed = True
    player = Player()
    coordinator = CaptureCoordinator()
    test, dispatch, done, errors = make_test(lambda **options: player, coordinator=coordinator)
    test.start()
    assert test.done.wait(1)
    dispatch.drain()
    assert player.closed and len(done) == 1 and len(errors) == 1
    assert "private native" not in errors[0]
    assert not coordinator.speech_lock.locked()
    assert coordinator.begin_capture(threading.Event())
    coordinator.end_capture()
    assert coordinator.claim(object())


def test_cancel_keeps_lease_and_output_lock_until_native_player_closes():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Player:
        def play(self, audio, cancel):
            entered.set()
            assert release.wait(2)
        def shutdown(self):
            closed.set()
    coordinator = CaptureCoordinator()
    test, dispatch, done, errors = make_test(lambda **options: Player(), coordinator=coordinator)
    test.start()
    assert entered.wait(1)
    test.cancel()
    assert not test.shutdown(timeout=.01)
    assert coordinator.speech_lock.locked() and not coordinator.claim(object())
    release.set()
    assert test.shutdown(timeout=1)
    dispatch.drain()
    assert closed.is_set() and len(done) == 1 and not errors
    assert not coordinator.speech_lock.locked() and coordinator.claim(object())


def test_other_capture_owner_refuses_output_test_without_playback_or_cancelling_capture():
    coordinator = CaptureCoordinator()
    owner = object()
    assert coordinator.claim(owner)
    test, dispatch, done, errors = make_test(
        lambda **options: pytest.fail("Output device opened during another capture"), coordinator=coordinator)
    test.start()
    assert test.done.is_set()
    dispatch.drain()
    assert len(done) == 1 and "mikrofon kaydı" in errors[0]
    assert not coordinator.claim(object())
    coordinator.release(owner)


def test_cancel_waiting_for_output_never_stops_unrelated_playback():
    coordinator = CaptureCoordinator()
    unrelated_cancel = threading.Event()
    coordinator.speech_lock.acquire()
    assert coordinator.begin_speech(unrelated_cancel)
    test, dispatch, done, errors = make_test(
        lambda **options: pytest.fail("Output device opened concurrently"), coordinator=coordinator)
    try:
        test.start()
        eventually(lambda: coordinator._owner is test)
        assert test.shutdown(timeout=1)
        dispatch.drain()
        assert len(done) == 1 and not errors and not unrelated_cancel.is_set()
        assert coordinator.speech_lock.locked()
    finally:
        coordinator.end_speech()
        coordinator.speech_lock.release()


def test_output_wait_deadline_is_actionable_without_opening_device():
    coordinator = CaptureCoordinator()
    coordinator.speech_lock.acquire()
    test, dispatch, done, errors = make_test(
        lambda **options: pytest.fail("Busy output opened"), coordinator=coordinator, timeout=.03)
    try:
        test.start()
        assert test.done.wait(1)
        dispatch.drain()
        assert len(done) == 1 and "meşgul" in errors[0]
    finally:
        coordinator.speech_lock.release()


def test_missing_audio_library_is_actionable_without_a_native_stream(monkeypatch):
    def missing(name):
        raise ImportError("private loader path")
    monkeypatch.setattr("firik_agent.voice.audio_devices.importlib.import_module", missing)
    test, dispatch, done, errors = make_test(lambda **options: SoundDevicePlayer(**options))
    test.start()
    assert test.done.wait(1)
    dispatch.drain()
    assert len(done) == 1 and len(errors) == 1
    assert "firik-agent[voice]" in errors[0] and "private loader" not in errors[0]


def test_repeated_tone_tests_close_streams_and_workers_without_replay_retention():
    coordinator = CaptureCoordinator()
    backend = OutputBackend()
    players = []
    def factory(**options):
        player = SoundDevicePlayer(**options, backend=backend, clock=lambda: 42.)
        players.append(player)
        return player
    for _ in range(8):
        test, dispatch, done, errors = make_test(factory, coordinator=coordinator)
        test.start()
        assert test.done.wait(1)
        assert test.shutdown(timeout=1)
        dispatch.drain()
        assert len(done) == 1 and not errors
    assert len(backend.streams) == 8 and all(stream.closed for stream in backend.streams)
    assert all(player._last is None and player._closed for player in players)
    assert not any(thread.name == "FirikVoiceOutputTest" for thread in threading.enumerate())


def test_service_output_test_is_explicit_offline_and_settings_changes_cancel_it(monkeypatch, tmp_path):
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    service.config_path = tmp_path / "config.yaml"
    started, stopped = threading.Event(), threading.Event()
    options = []
    class Player:
        def __init__(self, **kwargs):
            options.append(kwargs)
        def play(self, audio, cancel):
            started.set()
            assert cancel.wait(2)
        def shutdown(self):
            stopped.set()
    monkeypatch.setattr("firik_agent.voice.playback.SoundDevicePlayer", Player)
    service._voice_provider = lambda *args: pytest.fail("Output test accessed provider")
    service.get_key = lambda: pytest.fail("Output test accessed credentials")
    unsaved = replace(VoiceSettings(), output_device="pa:unsaved", output_volume=.3)
    test = service.start_voice_output_test(lambda: None, pytest.fail, settings=unsaved)
    assert started.wait(1)
    assert options == [{"device": "pa:unsaved", "volume": .3, "cache_replay": False}]
    assert not service.voice_settings.enabled
    service.update_voice_settings(VoiceSettings())
    assert test.done.wait(1) and stopped.is_set()
    assert test.shutdown(timeout=1)


def test_service_shutdown_joins_output_and_rejects_late_activation(monkeypatch):
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    entered, closed = threading.Event(), threading.Event()
    class Player:
        def __init__(self, **options):
            pass
        def play(self, audio, cancel):
            entered.set()
            assert cancel.wait(2)
        def shutdown(self):
            closed.set()
    monkeypatch.setattr("firik_agent.voice.playback.SoundDevicePlayer", Player)
    test = service.start_voice_output_test(lambda: None, pytest.fail)
    assert entered.wait(1)
    async def shutdown_mcp():
        pass
    class Runtime:
        def run_sync(self, operation, timeout):
            operation.close()
        def shutdown(self, wait):
            assert closed.is_set()
    service.cancel_active_stream = lambda: None
    service.mcp_manager = SimpleNamespace(shutdown=shutdown_mcp)
    service.runtime = Runtime()
    service.shutdown()
    assert test.done.is_set() and closed.is_set()
    errors = []
    replacement = service.start_voice_output_test(lambda: None, errors.append)
    assert replacement.done.is_set()
    dispatch.drain()
    assert len(errors) == 1 and "kapanıyor" in errors[0]


def test_stop_voice_cancels_output_test_without_touching_agent_request():
    service = minimal_service(Dispatch())
    cancelled = []
    class Test:
        def cancel(self):
            cancelled.append(True)
    test = Test()
    service._voice_output_tests = weakref.WeakSet([test])
    service.cancel_active_stream = lambda *args: pytest.fail("Output cancellation stopped agent")
    service.stop_voice()
    assert cancelled == [True]
