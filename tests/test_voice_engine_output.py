"""Output audio contracts, completion, interruption and bounded sentence queues."""
from array import array
import io
import json
from pathlib import Path
import struct
import threading
import time
import types
import wave

import pytest

from firik_agent.voice.errors import VoicePlaybackError, VoiceSynthesisError
from firik_agent.voice.models import AudioFormat, PCMBuffer
from firik_agent.voice.playback import EncodedAudio, SoundDevicePlayer, decode_audio
from firik_agent.voice.playback import _resample_pcm
from firik_agent.voice.synthesis import EngineSpeaker, NativeSynthesizer, SentenceBoundaryBuffer, SentenceSpeechQueue
from firik_agent.voice.speech import speech_text


def eventually(predicate, seconds=2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.002)
    assert predicate()


def wav_bytes(samples=None, width=2, channels=1, rate=16000):
    samples = samples if samples is not None else struct.pack("<h", 12000) * 1600
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(samples)
    return stream.getvalue()


class Backend:
    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    def __init__(self, *, drain=None, status=False):
        self.streams = []
        self.drain = drain
        self.status = status
        self.maximum_active = 0
        self.active_count = 0

    def check_output_settings(self, **kwargs):
        self.settings = kwargs

    def RawOutputStream(self, **options):
        backend = self

        class Stream:
            def __init__(self):
                self.active = False
                self.closed = False
                self.aborted = False
                self.blocks = []

            def start(self):
                self.active = True
                backend.active_count += 1
                backend.maximum_active = max(backend.maximum_active, backend.active_count)
                self.thread = threading.Thread(target=self.run)
                self.thread.start()

            def run(self):
                try:
                    while self.active:
                        output = bytearray(options["blocksize"] * options["channels"] * 2)
                        try:
                            options["callback"](output, options["blocksize"], None, backend.status)
                        except backend.CallbackStop:
                            self.blocks.append(bytes(output))
                            if backend.drain is not None:
                                backend.drain.wait(2)
                            break
                        except backend.CallbackAbort:
                            break
                        self.blocks.append(bytes(output))
                        time.sleep(.003)
                finally:
                    self.active = False
                    backend.active_count -= 1
                    options["finished_callback"]()

            def abort(self):
                self.aborted = True
                self.active = False
                if backend.drain is not None:
                    backend.drain.set()

            def close(self):
                self.closed = True
                self.thread.join(2)

        stream = Stream()
        self.streams.append(stream)
        return stream


def test_decode_sniffs_wav_even_with_incorrect_provider_content_type():
    audio = decode_audio(EncodedAudio(wav_bytes(), "audio/mpeg"))
    assert audio.format == AudioFormat(16000)
    assert audio.frames == 1600 and audio.duration == .1


@pytest.mark.parametrize("width,data,expected", [
    (1, bytes([0, 128, 255]), [-32768, 0, 32512]),
    (3, bytes.fromhex("000080000000ffff7f"), [-32768, 0, 32767]),
    (4, struct.pack("<iii", -2147483648, 0, 2147483647), [-32768, 0, 32767]),
])
def test_decode_integer_wav_widths_preserve_scaling(width, data, expected):
    decoded = decode_audio(wav_bytes(data, width=width))
    assert list(struct.unpack("<" + "h" * len(expected), decoded.data)) == expected


def test_headerless_pcm_requires_explicit_format():
    data = b"\x00\x01" * 500
    with pytest.raises(VoicePlaybackError, match="Ham PCM"):
        decode_audio(data, media_type="audio/pcm")
    assert decode_audio(EncodedAudio(data, "audio/pcm", AudioFormat(16000))).frames == 500


def test_explicit_raw_format_prevents_accidental_magic_sniffing():
    pcm = b"ID3\x00" * 20
    assert decode_audio(EncodedAudio(pcm, raw_format=AudioFormat(16000))).data == pcm


def test_empty_truncated_and_excessive_duration_are_rejected_before_playback():
    for data in (b"", wav_bytes()[:-3], b"garbage"):
        with pytest.raises(VoicePlaybackError):
            decode_audio(data)
    with pytest.raises(VoicePlaybackError):
        decode_audio(wav_bytes(), max_seconds=.05)


@pytest.mark.parametrize("signature", [b"ID3", b"OggS", b"fLaC", b"\xff\xfb"])
def test_optional_codec_missing_is_actionable(signature, monkeypatch):
    def missing(name):
        assert name == "soundfile"
        raise ImportError("unavailable")
    monkeypatch.setattr("firik_agent.voice.playback.importlib.import_module", missing)
    with pytest.raises(VoicePlaybackError, match="voice-codecs"):
        decode_audio(signature + b"\0" * 40)


def test_compressed_audio_uses_bounded_decoder_metadata(monkeypatch):
    calls = []
    class Source:
        samplerate, channels, frames = 16000, 1, 100
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append("closed")
        def buffer_read(self, frames, dtype):
            calls.append((frames, dtype))
            return b"\0\1" * frames
    monkeypatch.setattr("firik_agent.voice.playback.importlib.import_module", lambda name: types.SimpleNamespace(SoundFile=lambda _: Source()))
    assert decode_audio(b"OggS" + b"\0" * 50).frames == 100
    assert calls == [(100, "int16"), "closed"]
    Source.frames = 10**12
    calls.clear()
    with pytest.raises(VoicePlaybackError, match="sınır"):
        decode_audio(b"fLaC" + b"\0" * 50)
    assert calls == ["closed"]


def test_completion_waits_until_native_buffers_drain_and_handle_closes():
    drain, complete = threading.Event(), threading.Event()
    backend = Backend(drain=drain)
    player = SoundDevicePlayer(backend=backend)
    thread = threading.Thread(target=player.play, args=(wav_bytes(), threading.Event()), kwargs={"on_complete": complete.set})
    thread.start()
    eventually(lambda: backend.streams and len(backend.streams[0].blocks) == 5)
    assert not complete.is_set() and thread.is_alive()
    drain.set()
    thread.join(2)
    assert complete.is_set() and backend.streams[0].closed and not player.playing
    assert not backend.streams[0].aborted


def test_output_volume_is_per_stream_and_replay_uses_same_bounded_audio():
    backend = Backend()
    player = SoundDevicePlayer(backend=backend, volume=.25)
    player.play(wav_bytes(), threading.Event())
    assert set(struct.unpack("<" + "h" * 1600, b"".join(backend.streams[0].blocks))) == {3000}
    assert player.replay(threading.Event())
    assert backend.maximum_active == 1 and len(backend.streams) == 2
    player.shutdown()
    assert not player.replay(threading.Event())


class NativeRateBackend(Backend):
    def __init__(self, *, output_channels=2, native_rate=48000):
        super().__init__()
        self.output_channels = output_channels
        self.native_rate = native_rate
        self.checked_rates = []
        self.queries = []
        self.stream_options = []

    def check_output_settings(self, **kwargs):
        self.checked_rates.append(kwargs["samplerate"])
        if kwargs["samplerate"] != self.native_rate or kwargs["channels"] > self.output_channels:
            raise ValueError("unsupported output format")
        super().check_output_settings(**kwargs)

    def query_devices(self, device, kind):
        self.queries.append((device, kind))
        return {"default_samplerate": self.native_rate, "max_output_channels": self.output_channels}

    def RawOutputStream(self, **options):
        self.stream_options.append(options)
        return super().RawOutputStream(**options)


@pytest.mark.parametrize("channels", [1, 2])
def test_playback_falls_back_from_synthesis_rate_to_native_rate_without_timing_or_channel_loss(channels):
    backend = NativeRateBackend()
    player = SoundDevicePlayer(backend=backend)
    frame = struct.pack("<h", 10000) if channels == 1 else struct.pack("<hh", 10000, -10000)
    source = wav_bytes(frame * 2205, channels=channels, rate=22050)
    player.play(source, threading.Event())
    assert backend.checked_rates == [22050, 48000]
    assert backend.queries == [(None, "output")]
    assert backend.stream_options[0]["samplerate"] == 48000
    assert backend.stream_options[0]["channels"] == channels
    output = b"".join(backend.streams[0].blocks)
    assert len(output) == 4800 * channels * 2
    assert output == frame * 4800
    # Replay retains source PCM so selecting another device will not resample
    # an already adapted cache and accumulate conversion error.
    assert player._last.format.sample_rate == 22050


def test_matching_output_format_never_queries_default_rate():
    backend = NativeRateBackend(native_rate=22050)
    SoundDevicePlayer(backend=backend).play(wav_bytes(rate=22050), threading.Event())
    assert backend.checked_rates == [22050] and not backend.queries


def test_output_channel_mismatch_is_rejected_without_silent_downmixing():
    backend = NativeRateBackend(output_channels=1)
    player = SoundDevicePlayer(backend=backend)
    source = wav_bytes(struct.pack("<hh", 10000, -10000) * 2205, channels=2, rate=22050)
    with pytest.raises(VoicePlaybackError, match="kanal sayısı"):
        player.play(source, threading.Event())
    assert backend.checked_rates == [22050] and not backend.streams


def test_output_resampling_checks_expanded_allocation_before_conversion():
    backend = NativeRateBackend(native_rate=192000)
    source = PCMBuffer(b"\0\1" * (8000 * 120), AudioFormat(8000))
    with pytest.raises(VoicePlaybackError, match="boyut sınır"):
        SoundDevicePlayer(backend=backend).play(source, threading.Event())
    assert not backend.streams


@pytest.mark.parametrize("source_rate,target_rate,channels", [(44100, 16000, 2), (22050, 48000, 2), (48000, 44100, 1)])
def test_output_resampling_preserves_fractional_duration_across_bounded_blocks(source_rate, target_rate, channels):
    source = PCMBuffer(struct.pack("<" + "h" * channels, *range(channels)) * 3001, AudioFormat(source_rate, channels))
    output = _resample_pcm(source, target_rate, threading.Event())
    expected = (source.frames * target_rate * 2 + source_rate) // (2 * source_rate)
    assert output.frames == expected
    assert abs(source.duration - output.duration) <= .5 / target_rate


def test_output_resampling_observes_cancellation_without_opening_stream():
    source = PCMBuffer(b"\0\1" * 2205, AudioFormat(22050))
    cancel = threading.Event()
    cancel.set()
    assert _resample_pcm(source, 48000, cancel) is None


@pytest.mark.parametrize("volume", [-.1, 1.1, float("nan"), True, "1"])
def test_invalid_volume_is_rejected(volume):
    with pytest.raises(VoicePlaybackError):
        SoundDevicePlayer(volume=volume)


def test_pause_outputs_silence_without_consuming_audio_then_resumes():
    backend = Backend()
    player = SoundDevicePlayer(backend=backend)
    data = wav_bytes(struct.pack("<h", 9000) * 16000)
    thread = threading.Thread(target=player.play, args=(data, threading.Event()))
    thread.start()
    eventually(lambda: backend.streams and len(backend.streams[0].blocks) >= 2)
    player.pause()
    eventually(lambda: backend.streams[0].blocks[-1] == bytes(640))
    assert thread.is_alive()
    player.resume()
    thread.join(2)
    assert not thread.is_alive()
    assert sum(block.count(struct.pack("<h", 9000)) for block in backend.streams[0].blocks) == 16000


def test_pause_before_stream_open_is_preserved_across_synthesis_boundaries():
    backend = Backend()
    player = SoundDevicePlayer(backend=backend)
    player.pause()
    thread = threading.Thread(target=player.play, args=(wav_bytes(), threading.Event()))
    thread.start()
    eventually(lambda: backend.streams and len(backend.streams[0].blocks) > 2)
    assert all(block == bytes(640) for block in backend.streams[0].blocks)
    player.resume()
    thread.join(2)
    assert not thread.is_alive() and backend.streams[0].closed


def test_cancel_aborts_closes_and_does_not_report_completion():
    backend = Backend()
    cancel, complete = threading.Event(), threading.Event()
    player = SoundDevicePlayer(backend=backend)
    thread = threading.Thread(target=player.play, args=(wav_bytes(b"\0\1" * 16000), cancel), kwargs={"on_complete": complete.set})
    thread.start()
    eventually(lambda: player.playing and backend.streams)
    cancel.set()
    thread.join(2)
    assert not thread.is_alive() and backend.streams[0].closed
    assert not complete.is_set()


def test_underflow_does_not_report_success_and_releases_native_handle():
    backend = Backend(status=True)
    player = SoundDevicePlayer(backend=backend)
    with pytest.raises(VoicePlaybackError, match="kesildi"):
        player.play(wav_bytes(), threading.Event())
    assert backend.streams[0].closed and not player.playing


def test_concurrent_play_never_overlaps_native_streams():
    backend = Backend()
    player = SoundDevicePlayer(backend=backend)
    threads = [threading.Thread(target=player.play, args=(wav_bytes(), threading.Event())) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert backend.maximum_active == 1 and len(backend.streams) == 3
    assert all(stream.closed for stream in backend.streams)


def test_sentence_boundaries_keep_split_markdown_and_hidden_reasoning_local():
    text = "Merhaba **dünya.**\n```python\nprint('private. code')\n```\n<think>secret. thought</think> Hello [world](https://example.com). Last sentence!"
    buffer = SentenceBoundaryBuffer()
    emitted = []
    for char in text:
        emitted.extend(buffer.feed(char))
    assert emitted == ["Merhaba dünya.", "Hello world."]
    emitted.extend(buffer.feed("", final=True))
    assert emitted == ["Merhaba dünya.", "Hello world.", "Last sentence!"]


@pytest.mark.parametrize("fragment", ["`unfinished code.", "[unfinished link.", "**unfinished prose.", "<analysis>private.", "```python\nprivate."])
def test_incomplete_final_markdown_never_reaches_synthesis(fragment):
    buffer = SentenceBoundaryBuffer()
    assert buffer.feed("Safe sentence. " + fragment) == ["Safe sentence."]
    assert buffer.feed("", final=True) == []


def test_invalid_fence_closer_does_not_leak_code():
    buffer = SentenceBoundaryBuffer()
    text = "```python\nprivate.\n```invalid\nstill_private.\n```\nReadable. "
    assert buffer.feed(text) == ["Readable."]


def test_sentence_buffer_and_sentence_length_are_bounded():
    with pytest.raises(VoiceSynthesisError):
        SentenceBoundaryBuffer(max_chars=10).feed("x" * 11)
    with pytest.raises(VoiceSynthesisError):
        SentenceBoundaryBuffer().feed("x" * 1601 + ". ")


def test_indented_markdown_code_is_not_spoken_and_link_url_underscores_do_not_hide_prose():
    buffer = SentenceBoundaryBuffer()
    text = "    private_code().\n\tmore_code().\nRead [this](https://example.com/my_document). "
    assert buffer.feed(text) == ["Read this."]


def test_sentence_queue_preserves_fifo_and_waits_for_final_input():
    order = []
    pipeline = SentenceSpeechQueue(lambda text, cancel: order.append(("synth", text)) or text,
                                   lambda audio, cancel: order.append(("play", audio)))
    try:
        assert pipeline.feed("Merhaba. Hello! ")
        eventually(lambda: len(order) == 4)
        assert not pipeline.wait(.02)
        assert pipeline.finish()
        assert pipeline.wait(2)
        assert order == [("synth", "Merhaba."), ("play", "Merhaba."), ("synth", "Hello!"), ("play", "Hello!")]
    finally:
        pipeline.cancel()


def test_sentence_queue_applies_backpressure_and_cancels_unplayed_work():
    entered, release = threading.Event(), threading.Event()
    order = []
    def synthesize(text, cancel):
        entered.set()
        release.wait(2)
        order.append(text)
        return text
    pipeline = SentenceSpeechQueue(synthesize, lambda audio, cancel: order.append("played"), max_queue=1)
    assert pipeline.feed("First. ")
    assert entered.wait(1)
    assert pipeline.feed("Second. ")
    assert not pipeline.feed("Third. ")
    pipeline.cancel()
    release.set()
    assert pipeline.wait(2)
    assert order == ["First."]


def test_sentence_queue_bounds_total_text_and_surfaces_safe_worker_failures():
    pipeline = SentenceSpeechQueue(lambda *args: (_ for _ in ()).throw(RuntimeError("secret")), lambda *args: None)
    assert pipeline.feed("Hello. ")
    with pytest.raises(VoiceSynthesisError, match="işlenemedi"):
        pipeline.wait(2)
    another = SentenceSpeechQueue(lambda *args: None, lambda *args: None, max_chars=3)
    with pytest.raises(VoiceSynthesisError, match="boyut"):
        another.feed("xxxx")
    assert another.wait(2)


def test_engine_speaker_uses_bounded_sentence_pipeline_and_replays_full_reply():
    order = []
    synth = types.SimpleNamespace(synthesize=lambda text, cancel, language, rate: order.append((text, language, rate)) or text)
    player = types.SimpleNamespace(play=lambda audio, cancel: None, pause=lambda: None, resume=lambda: None,
                                   stop=lambda: None, shutdown=lambda: None)
    speaker = EngineSpeaker(synthesizer=synth, player=player)
    text = "Hello. " * 30 + "```python\nsecret code\n```\nMerhaba!"
    cancel = threading.Event()
    speaker.speak(text, cancel, "tr", 180)
    assert len(order) == 31 and order[-1] == ("Merhaba!", "tr", 180)
    assert speaker.replay(threading.Event()) and len(order) == 62
    speaker.configure(output_device="pa:chosen", volume=.5)
    assert player.device == "pa:chosen" and player.volume == .5
    speaker.shutdown()
    assert not speaker.replay(threading.Event())


def test_engine_shutdown_cancels_synthesis_and_never_plays_queued_reply():
    entered = threading.Event()
    played = []
    def synthesize(text, cancel, language, rate):
        entered.set()
        cancel.wait(2)
        return text
    player = types.SimpleNamespace(play=lambda audio, cancel: played.append(audio),
                                   stop=lambda: None, shutdown=lambda: None)
    speaker = EngineSpeaker(synthesizer=types.SimpleNamespace(synthesize=synthesize), player=player)
    thread = threading.Thread(target=speaker.speak, args=("First. Second. ", threading.Event()))
    thread.start()
    assert entered.wait(1)
    speaker.shutdown()
    thread.join(2)
    assert not thread.is_alive() and not played


def test_engine_keeps_native_ownership_until_slow_output_close_really_finishes():
    closing, release, returned = threading.Event(), threading.Event(), threading.Event()
    class SlowCloseBackend(Backend):
        def RawOutputStream(self, **options):
            stream = super().RawOutputStream(**options)
            original_close = stream.close
            def close():
                closing.set()
                release.wait(6)
                original_close()
            stream.close = close
            return stream
    backend = SlowCloseBackend()
    synth = types.SimpleNamespace(synthesize=lambda *args: EncodedAudio(wav_bytes()))
    speaker = EngineSpeaker(backend=backend, synthesizer=synth)
    def speak():
        try:
            speaker.speak("Hello.", threading.Event())
        finally:
            returned.set()
    owner = threading.Thread(target=speak)
    owner.start()
    try:
        assert closing.wait(1)
        speaker.stop()
        # Exceed the former three-second pipeline wait: returning here would
        # release the controller's global half-duplex gate with a live handle.
        assert not returned.wait(3.1)
        assert not backend.streams[0].closed
    finally:
        release.set()
        owner.join(2)
        speaker.shutdown()
    assert returned.is_set() and backend.streams[0].closed


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_native_synthesis_uses_data_only_stdin_private_wav_and_cleans_files(platform, monkeypatch):
    calls = []
    class Process:
        def __init__(self, args, **options):
            self.args = args
            self.stdin = io.BytesIO()
            self.stdout = self.stderr = None
            self.returncode = None
            calls.append((args, options))
        def wait(self, timeout=None):
            if self.returncode is None:
                if platform == "win32":
                    path = json.loads(payloads[0])["path"]
                else:
                    path = self.args[self.args.index("-o" if platform == "darwin" else "-w") + 1]
                Path(path).write_bytes(wav_bytes())
                paths.append(path)
            self.returncode = 0
            return 0
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -1
    payloads, paths = [], []
    class Pipe(io.BytesIO):
        def close(self):
            payloads.append(self.getvalue())
            super().close()
    def popen(args, **options):
        process = Process(args, **options)
        process.stdin = Pipe()
        return process
    monkeypatch.setattr("firik_agent.voice.synthesis.sys.platform", platform)
    monkeypatch.setattr("firik_agent.voice.synthesis.subprocess.Popen", popen)
    monkeypatch.setattr("firik_agent.voice.synthesis.shutil.which", lambda name: "/trusted/" + name)
    text = "Türkçe $(do_not_execute); quoted ' words."
    encoded = NativeSynthesizer().synthesize(text, threading.Event())
    assert decode_audio(encoded).frames == 1600
    assert all(text not in arg for arg in calls[0][0])
    assert not calls[0][1].get("shell")
    assert not Path(paths[0]).exists()
    if platform == "win32":
        assert json.loads(payloads[0])["text"] == speech_text(text)
    else:
        assert payloads[0] == speech_text(text).encode()
