"""Native speech stays data-only, cancellable, bounded and platform independent."""
import base64
import io
import json
import subprocess
import threading
import time

import pytest

from firik_agent.voice.speech import LocalSpeaker, VoiceSpeechError, _parse_mac_voices, speech_text


class InputPipe:
    def __init__(self):
        self.written = bytearray()
        self.closed = False

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *, returncode=0, cancel=None, timeout=False, stubborn=False):
        self.returncode = None
        self.result_code = returncode
        self.cancel = cancel
        self.timeout = timeout
        self.stubborn = stubborn
        self.inputs = []
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.stdin = InputPipe()
        self.stdout = None
        self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits += 1
        if self.cancel:
            self.cancel.set()
        if self.timeout:
            self.timeout = False
            raise subprocess.TimeoutExpired("native", timeout)
        if self.stubborn and not self.killed:
            raise subprocess.TimeoutExpired("native", timeout)
        self.returncode = self.result_code
        return self.returncode


def install_process(monkeypatch, platform, process):
    calls = []
    monkeypatch.setattr("firik_agent.voice.speech.sys.platform", platform)
    monkeypatch.setattr("firik_agent.voice.speech.shutil.which", lambda name: "/trusted/" + name)
    def popen(args, **options):
        calls.append((args, options))
        return process
    monkeypatch.setattr("firik_agent.voice.speech.subprocess.Popen", popen)
    return calls


def test_prose_filter_removes_hidden_reasoning_code_links_and_bounds_text():
    source = """<think>private thinking</think>
## Merhaba **dünya**
```python
dangerous_code()
```
[belge](https://example.com) https://example.com/secret
![image](https://example.com/image)
`internal_code` ve Türkçe yanıt.
"""
    text = speech_text(source)
    assert text == "Merhaba dünya belge ve Türkçe yanıt."
    assert len(speech_text("a" * 10000)) == 8000
    assert speech_text("yanıt\n```python\nunclosed code") == "yanıt"
    assert speech_text("başla <analysis>hidden and unclosed") == "başla"
    assert speech_text("Oku [[slnc 1000000]] normal [[rate 1") == "Oku normal"


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_untrusted_non_ascii_text_only_goes_to_stdin(platform, monkeypatch):
    process = FakeProcess()
    calls = install_process(monkeypatch, platform, process)
    source = "Türkçe İngilizce; $(touch /tmp/pwned); --voice=x; 'quoted'"
    LocalSpeaker().speak(source, threading.Event(), "auto")
    args, options = calls[0]
    assert options["stdin"] == subprocess.PIPE
    assert options["stdout"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL
    assert not options.get("shell", False)
    assert all(source not in item and "touch /tmp/pwned" not in item for item in args)
    if platform == "win32":
        payload = json.loads(process.stdin.written.decode("utf-8"))
        assert payload == {"text": source, "language": "auto", "rate": 175}
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        assert "ConvertFrom-Json" in script
        assert source not in script
        assert "Invoke-Expression" not in script
        assert options["creationflags"] == 0x08000000
    else:
        assert process.stdin.written == source.encode("utf-8")
    assert process.stdin.closed
    assert process.waits


def test_cancel_terminates_kills_and_reaps_stubborn_backend(monkeypatch):
    cancel = threading.Event()
    process = FakeProcess(cancel=cancel, timeout=True, stubborn=True)
    install_process(monkeypatch, "linux", process)
    LocalSpeaker().speak("Yanıtı oku", cancel, "tr")
    assert process.terminated
    assert process.killed
    assert process.waits == 3
    assert process.stdin.closed


def test_windows_cancel_does_not_wait_for_a_blocked_stdin_write(monkeypatch):
    cancel = threading.Event()
    released = threading.Event()
    entered = threading.Event()

    class BlockingPipe(InputPipe):
        def write(self, data):
            assert len(data) > 8192
            entered.set()
            cancel.set()
            assert released.wait(2), "writer stayed blocked after child cancellation"
            raise BrokenPipeError("child reader closed")

        def close(self):
            if entered.is_set():
                assert released.is_set(), "stdin closed before child termination released writer"
            super().close()

    class BlockingProcess(FakeProcess):
        def kill(self):
            super().kill()
            released.set()

    process = BlockingProcess(stubborn=True)
    process.stdin = BlockingPipe()
    install_process(monkeypatch, "win32", process)
    started = time.monotonic()
    LocalSpeaker().speak("ğ" * 8000, cancel, "tr")
    assert time.monotonic() - started < 1
    assert process.terminated and process.killed
    assert entered.is_set() and released.is_set() and process.stdin.closed
    assert not any(t.name == "FirikSpeechInput" and t.is_alive() for t in threading.enumerate())


def test_process_wait_retry_does_not_send_duplicate_payload(monkeypatch):
    process = FakeProcess(timeout=True)
    install_process(monkeypatch, "linux", process)
    LocalSpeaker().speak("Yanıtı oku", threading.Event(), "en")
    assert process.stdin.written == "Yanıtı oku".encode()


def test_linux_selects_language_as_fixed_argument(monkeypatch):
    process = FakeProcess()
    calls = install_process(monkeypatch, "linux", process)
    LocalSpeaker().speak("Hello", threading.Event(), "en", 200)
    assert calls[0][0] == ["/trusted/espeak-ng", "--stdin", "-s", "200", "-v", "en"]


def test_missing_linux_backend_is_actionable(monkeypatch):
    monkeypatch.setattr("firik_agent.voice.speech.sys.platform", "linux")
    monkeypatch.setattr("firik_agent.voice.speech.shutil.which", lambda name: None)
    with pytest.raises(VoiceSpeechError, match="espeak-ng kurun"):
        LocalSpeaker().speak("Oku", threading.Event())


@pytest.mark.parametrize("language,rate", [("tr;payload", 175), ("tr", True), ("auto", 0), ("en", "175")])
def test_invalid_options_never_spawn(language, rate, monkeypatch):
    monkeypatch.setattr("firik_agent.voice.speech.subprocess.Popen", lambda *a, **k: pytest.fail("unexpected process"))
    with pytest.raises(VoiceSpeechError):
        LocalSpeaker().speak("Oku", threading.Event(), language, rate)


def test_no_process_for_empty_or_cancelled_speech(monkeypatch):
    monkeypatch.setattr("firik_agent.voice.speech.subprocess.Popen", lambda *a, **k: pytest.fail("unexpected process"))
    cancel = threading.Event()
    cancel.set()
    LocalSpeaker().speak("Oku", cancel)
    LocalSpeaker().speak("```python\ncode\n```", threading.Event())


def test_nonzero_backend_error_is_sanitized_and_cleaned_up(monkeypatch):
    process = FakeProcess(returncode=1)
    install_process(monkeypatch, "linux", process)
    with pytest.raises(VoiceSpeechError, match="sistem sesi yükleyin"):
        LocalSpeaker().speak("private text", threading.Event(), "tr")
    assert process.waits
    assert process.stdin.closed


def test_mac_voice_parser_supports_spaces_and_non_ascii_names():
    listing = b"English Voice     en_US    # Hello\n" + "Türkçe Ses    tr_TR    # Merhaba\n".encode()
    assert _parse_mac_voices(listing, "tr") == "Türkçe Ses"
    assert _parse_mac_voices(listing, "en") == "English Voice"
    assert _parse_mac_voices(listing, "de") is None


def test_mac_voice_parser_prefers_natural_installed_voices_over_novelty_order():
    listing = ("Albert en_US # Hello\nOther Turkish tr_TR # Merhaba\n"
               "Samantha (English (United States)) en_US # Hello\n"
               "Yelda (Turkish (Türkiye)) tr_TR # Merhaba\n").encode()
    assert _parse_mac_voices(listing, "en") == "Samantha (English (United States))"
    assert _parse_mac_voices(listing, "tr") == "Yelda (Turkish (Türkiye))"
    assert _parse_mac_voices(b"Albert en_US # Hello\n", "en") == "Albert"


def test_mac_missing_language_voice_is_actionable_and_reaped(monkeypatch):
    process = FakeProcess()
    process.stdout = io.BytesIO(b"English Voice en_US # Hello\n")
    install_process(monkeypatch, "darwin", process)
    with pytest.raises(VoiceSpeechError, match="sistem sesi bulunamadı"):
        LocalSpeaker().speak("Merhaba", threading.Event(), "tr")
    assert process.waits
    assert process.stdout.closed


def test_mac_voice_list_capture_is_bounded(monkeypatch):
    process = FakeProcess()
    process.stdout = io.BytesIO(b"x" * 100000)
    install_process(monkeypatch, "darwin", process)
    with pytest.raises(VoiceSpeechError, match="Sistem sesleri okunamadı"):
        LocalSpeaker().speak("Merhaba", threading.Event(), "tr")
    assert process.stdout.closed


def test_mac_selects_installed_language_voice_and_caches_lookup(monkeypatch):
    listing = FakeProcess()
    listing.stdout = io.BytesIO("Türkçe Ses tr_TR # Merhaba\n".encode())
    speech1, speech2 = FakeProcess(), FakeProcess()
    processes = iter([listing, speech1, speech2])
    calls = []
    monkeypatch.setattr("firik_agent.voice.speech.sys.platform", "darwin")
    def popen(args, **options):
        calls.append(args)
        return next(processes)
    monkeypatch.setattr("firik_agent.voice.speech.subprocess.Popen", popen)
    speaker = LocalSpeaker()
    speaker.speak("Merhaba", threading.Event(), "tr")
    speaker.speak("Günaydın", threading.Event(), "tr")
    assert calls == [["/usr/bin/say", "-v", "?"],
                     ["/usr/bin/say", "-r", "175", "-v", "Türkçe Ses", "-f", "-"],
                     ["/usr/bin/say", "-r", "175", "-v", "Türkçe Ses", "-f", "-"]]
    assert listing.stdout.closed
    assert speech1.stdin.closed and speech2.stdin.closed
