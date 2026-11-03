"""Offline diagnostics, local microphone-test ownership and weak UI refresh."""
from dataclasses import replace
import gc
import json
import queue
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from firik_agent.ui.service import FirikService
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.diagnostics import INPUT_TEST_SECONDS, InputLevelTest, voice_diagnostics
from firik_agent.voice.errors import VoiceAudioError, VoicePermissionError
from firik_agent.voice.session import CaptureCoordinator


class Dispatch:
    def __init__(self):
        self.jobs = queue.Queue()
        self.owner = threading.get_ident()
    def __call__(self, callback, *args):
        self.jobs.put((callback, args))
    def drain(self):
        assert threading.get_ident() == self.owner
        while not self.jobs.empty():
            callback, args = self.jobs.get_nowait()
            callback(*args)


def eventually(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


class Recorder:
    def __init__(self, *, wait=True, error=None, silent=False):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.wait, self.error, self.silent = wait, error, silent
        self.options = None
        self.call = None
    def configure(self, **options):
        self.options = options
    def record_utterance(self, stop, cancel, seconds, **options):
        self.call = (seconds, options, threading.get_ident())
        for index in range(1000):
            self.options["on_level"](index / 1000)
        self.started.set()
        try:
            if self.error is not None:
                raise self.error
            if self.wait:
                assert cancel.wait(2)
            return b"" if self.silent else b"bounded PCM stays on worker"
        finally:
            self.finished.set()


def make_handle(recorder, *, coordinator=None, seconds=.2):
    dispatch = Dispatch()
    levels, done, errors, callback_threads = [], [], [], []
    def receive(target, value=None):
        callback_threads.append(threading.get_ident())
        target.append(value)
    value = InputLevelTest(coordinator=coordinator or CaptureCoordinator(), dispatch=dispatch,
        settings=replace(VoiceSettings(), input_device="pa:chosen", input_gain=1.5),
        on_level=lambda level: receive(levels, level), on_done=lambda: receive(done),
        on_error=lambda message: receive(errors, message), recorder_factory=lambda: recorder, seconds=seconds)
    return value, dispatch, levels, done, errors, callback_threads


def test_explicit_input_test_is_worker_only_bounded_and_has_no_retained_audio():
    recorder = Recorder()
    test, dispatch, levels, done, errors, threads = make_handle(recorder)
    test.start()
    assert recorder.started.wait(1)
    assert not levels and not done and not errors
    assert dispatch.jobs.qsize() == 1  # 1000 measurements coalesce into one Tk job.
    dispatch.drain()
    assert levels == [.999]
    assert recorder.call[0] <= 5
    assert recorder.call[1]["device"] == "pa:chosen"
    assert recorder.call[2] != threading.get_ident()
    assert recorder.options["input_gain"] == 1.5
    assert recorder.options["vad_engine"] == "energy"
    assert test.done.wait(1)
    dispatch.drain()
    assert len(done) == 1 and not errors
    assert threads and set(threads) == {threading.get_ident()}
    assert not hasattr(test, "audio") and not hasattr(test, "pcm") and not hasattr(test, "result")


def test_cancel_suppresses_queued_levels_and_releases_capture_owner():
    coordinator = CaptureCoordinator()
    test, dispatch, levels, done, errors, _ = make_handle(Recorder(), coordinator=coordinator)
    test.start()
    eventually(lambda: dispatch.jobs.qsize() == 1)
    test.cancel()
    assert test.shutdown(timeout=1)
    dispatch.drain()
    assert not levels and not errors and len(done) == 1
    replacement = object()
    assert coordinator.claim(replacement)
    coordinator.release(replacement)


def test_silent_input_test_completes_without_asr_or_speech_error():
    recorder = Recorder(wait=False, silent=True)
    test, dispatch, _, done, errors, _ = make_handle(recorder)
    test.start()
    assert test.done.wait(1)
    dispatch.drain()
    assert len(done) == 1 and not errors


@pytest.mark.parametrize("error,visible", [
    (VoicePermissionError("Mikrofon izni gerekli. Sistem ayarlarını kontrol edin."), "Mikrofon izni"),
    (VoiceAudioError("Ses paketi eksik; firik-agent[voice] kurun."), "firik-agent[voice]"),
    (RuntimeError("private native device detail"), "metinle devam"),
])
def test_input_failures_are_actionable_but_private_native_details_are_not_exposed(error, visible):
    test, dispatch, _, done, errors, _ = make_handle(Recorder(error=error))
    test.start()
    assert test.done.wait(1)
    dispatch.drain()
    assert len(done) == 1 and visible in errors[0]
    assert "private native" not in errors[0]


def test_other_capture_owner_refuses_input_test_without_opening_or_releasing_it():
    coordinator = CaptureCoordinator()
    owner = object()
    assert coordinator.claim(owner)
    recorder = Recorder()
    test, dispatch, _, done, errors, _ = make_handle(recorder, coordinator=coordinator)
    test.start()
    assert test.done.is_set() and not recorder.started.is_set()
    dispatch.drain()
    assert len(done) == 1 and "başka bir ses işlemi" in errors[0]
    assert not coordinator.claim(object())
    coordinator.release(owner)


def test_native_capture_close_keeps_ownership_even_after_cancelled_test_timeout():
    release = threading.Event()
    class SlowClose(Recorder):
        def record_utterance(self, stop, cancel, seconds, **options):
            self.started.set()
            assert release.wait(2)
            return b""
    coordinator = CaptureCoordinator()
    recorder = SlowClose()
    test, _, _, _, _, _ = make_handle(recorder, coordinator=coordinator)
    test.start()
    assert recorder.started.wait(1)
    started = time.monotonic()
    assert test.shutdown(timeout=.02) is False
    assert time.monotonic() - started < .3
    assert coordinator.capture_lock.locked() and not coordinator.claim(object())
    release.set()
    assert test.shutdown(timeout=1)
    assert not coordinator.capture_lock.locked()


def test_input_test_waiting_for_old_native_close_cancels_without_opening_new_device():
    coordinator = CaptureCoordinator()
    coordinator.capture_lock.acquire()
    recorder = Recorder()
    test, dispatch, _, done, errors, _ = make_handle(recorder, coordinator=coordinator, seconds=.05)
    try:
        test.start()
        assert test.done.wait(1)
        dispatch.drain()
        assert not recorder.started.is_set() and len(done) == 1
        assert errors and coordinator.capture_lock.locked()
    finally:
        coordinator.capture_lock.release()


def test_input_test_never_overlaps_playback_and_cancellation_does_not_stop_speech():
    coordinator = CaptureCoordinator()
    speech_cancel = threading.Event()
    assert coordinator.begin_speech(speech_cancel)
    recorder = Recorder()
    test, dispatch, _, done, errors, _ = make_handle(recorder, coordinator=coordinator)
    test.start()
    time.sleep(.025)
    assert not recorder.started.is_set()
    test.cancel()
    assert test.shutdown(timeout=1)
    dispatch.drain()
    assert not recorder.started.is_set() and len(done) == 1 and not errors
    assert not speech_cancel.is_set()
    coordinator.end_speech()


def test_expired_test_waiting_for_playback_gives_retry_guidance():
    coordinator = CaptureCoordinator()
    assert coordinator.begin_speech(threading.Event())
    recorder = Recorder()
    test, dispatch, _, _, errors, _ = make_handle(recorder, coordinator=coordinator, seconds=.05)
    try:
        test.start()
        assert test.done.wait(1)
        dispatch.drain()
        assert not recorder.started.is_set() and "Konuşmayı durdurup" in errors[0]
    finally:
        coordinator.end_speech()


def test_missing_optional_backend_is_reported_without_native_capture(monkeypatch):
    def missing(name):
        raise ImportError("not installed")
    monkeypatch.setattr("firik_agent.voice.audio.importlib.import_module", missing)
    dispatch = Dispatch()
    errors, done = [], []
    test = InputLevelTest(coordinator=CaptureCoordinator(), dispatch=dispatch, settings=VoiceSettings(),
        on_level=lambda _: None, on_done=lambda: done.append(True), on_error=errors.append)
    test.start()
    assert test.done.wait(1)
    dispatch.drain()
    assert done == [True] and "firik-agent[voice]" in errors[0]


def test_offline_diagnostics_do_not_import_backend_query_devices_or_read_credentials(monkeypatch):
    seen = []
    def spec(name):
        seen.append(name)
        return object() if name == "sounddevice" else None
    monkeypatch.setattr("importlib.util.find_spec", spec)
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test-version")
    monkeypatch.setattr("importlib.import_module", lambda *args: pytest.fail("diagnostics must not import audio backend"))
    service = object.__new__(FirikService)
    service.config = {"voice": {"enabled": False, "input_device": "private-secret-device"},
        "providers": {"firik": {"api_key": "private-secret-key", "base_url": "https://private.invalid/key"}}}
    service.get_key = lambda: pytest.fail("diagnostics must not read credential store")
    result = service.voice_diagnostics()
    assert seen == ["sounddevice", "webrtcvad", "soundfile"]
    assert result["packages"]["sounddevice"]["installed"]
    assert result["backend"]["status"] == "not_opened"
    assert result["microphone_permission"]["status"] == "not_checked"
    assert result["settings"]["input_selection"] == "selected_device"
    assert result["client_limits"]["input_test_seconds"] == INPUT_TEST_SECONDS
    assert not result["capabilities"]["partial_transcripts"]
    assert not result["capabilities"]["simultaneous_barge_in"]
    serialized = json.dumps(result)
    assert "private-secret" not in serialized and "private.invalid" not in serialized


@pytest.mark.parametrize("system,expected", [("Darwin", "Sistem Ayarları"), ("Windows", "masaüstü"), ("Linux", "PortAudio")])
def test_diagnostics_offer_os_specific_guidance_and_missing_packages_do_not_break_text_fallback(monkeypatch, system, expected):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    result = voice_diagnostics()
    assert expected in result["microphone_permission"]["guidance"]
    assert not result["backend"]["package_present"]
    assert any("Metin sohbeti" in note for note in result["notes"])


def minimal_service(dispatch):
    service = object.__new__(FirikService)
    service.config = {"voice": VoiceSettings().to_dict()}
    service._dispatch = dispatch
    service._voice_controllers = weakref.WeakSet()
    return service


def test_service_input_test_accepts_unsaved_device_gain_while_voice_disabled_and_never_calls_asr(monkeypatch):
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    recorder = Recorder()
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", lambda: recorder)
    service._transcribe_microphone = lambda *args: pytest.fail("local level test must not transcribe")
    service._voice_provider = lambda *args: pytest.fail("local level test must not access remote provider")
    unsaved = replace(VoiceSettings(), input_device="pa:unsaved", input_gain=2)
    test = service.start_voice_input_test(lambda _: None, lambda: None, pytest.fail, settings=unsaved)
    assert recorder.started.wait(1)
    assert recorder.call[0] == 3 and recorder.call[1]["device"] == "pa:unsaved"
    assert recorder.options["input_gain"] == 2
    assert not service.voice_settings.enabled
    service.stop_voice()
    assert test.shutdown(timeout=1)


def test_service_cleanup_worker_cancels_joins_and_prevents_new_input_test(monkeypatch):
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    recorder = Recorder()
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", lambda: recorder)
    events = []
    test = service.start_voice_input_test(lambda _: None, lambda: events.append("done"), pytest.fail)
    assert recorder.started.wait(1)
    async def shutdown_mcp():
        pass
    class Runtime:
        def run_sync(self, coroutine, timeout):
            coroutine.close()
        def shutdown(self, wait):
            events.append("runtime_closed")
    service.cancel_active_stream = lambda: None
    service.mcp_manager = SimpleNamespace(shutdown=shutdown_mcp)
    service.runtime = Runtime()
    worker = threading.Thread(target=service.shutdown)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and test.done.is_set()
    assert recorder.finished.is_set() and "runtime_closed" in events
    errors = []
    replacement = service.start_voice_input_test(lambda _: None, lambda: None, errors.append)
    assert replacement.done.is_set()
    dispatch.drain()
    assert "Uygulama kapanıyor" in errors[0]


def test_settings_save_cancels_test_and_weak_listener_dispatches_refresh_without_restart(tmp_path):
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    service.config_path = tmp_path / "settings.yaml"
    cancelled = []
    class Test:
        def cancel(self):
            cancelled.append(True)
    test = Test()
    service._voice_input_tests = weakref.WeakSet([test])
    calls = []
    class Panel:
        def refresh(self):
            calls.append(service.voice_settings.shortcut)
    panel = Panel()
    service.add_voice_settings_listener(panel.refresh)
    service.add_voice_settings_listener(panel.refresh)
    service.update_voice_settings(replace(VoiceSettings(), shortcut="<Alt-space>", completion_chime=True))
    assert cancelled == [True] and not calls
    dispatch.drain()
    assert calls == ["<Alt-space>"] and service.voice_settings.completion_chime


def test_removed_or_garbage_collected_settings_listener_is_not_called_by_queued_dispatch():
    dispatch = Dispatch()
    service = minimal_service(dispatch)
    calls = []
    class Panel:
        def refresh(self):
            calls.append(True)
    panel = Panel()
    service.add_voice_settings_listener(panel.refresh)
    service._notify_voice_settings_listeners()
    service.remove_voice_settings_listener(panel.refresh)
    dispatch.drain()
    assert not calls
    service.add_voice_settings_listener(panel.refresh)
    service._notify_voice_settings_listeners()
    reference = weakref.ref(panel)
    del panel
    gc.collect()
    assert reference() is None
    dispatch.drain()
    assert not calls
    service._notify_voice_settings_listeners()
    assert not service._voice_settings_listeners
