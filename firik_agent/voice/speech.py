"""Local, cancellable speech output through fixed native programs.

Response text travels only over stdin, never through a shell or a script
template. Native stdout/stderr are discarded, and text size/duration are bounded.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import threading
import time


MAX_SPEECH_CHARS = 8000


class VoiceSpeechError(RuntimeError):
    """A speech-backend failure suitable for the Turkish UI."""


def speech_text(text: str) -> str:
    """Extract bounded prose without hidden reasoning, fenced code or URLs."""
    text = str(text or "")
    text = re.sub(r"<(think|analysis|reasoning)\b[^>]*>.*?(?:</\1\s*>|$)", " ", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"(```|~~~)[^\n]*\n.*?(?:\1[^\n]*(?:\n|$)|$)", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"`[^`]*`", " ", text)
    # macOS's native synthesizer recognizes embedded [[rate ...]] commands.
    # Assistant prose never gets to change synthesizer settings this way.
    text = re.sub(r"\[\[.*?(?:\]\]|$)", " ", text, flags=re.DOTALL)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+)", "", text)
    text = re.sub(r"[*_~]{1,3}", "", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_SPEECH_CHARS].rstrip()


# The script is constant. All dynamic values, including language and rate,
# arrive in a JSON object on stdin; response text cannot become PowerShell code.
_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($data.language -ne 'auto') {
        $voices = @($speaker.GetInstalledVoices() | Where-Object {
            $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $data.language
        })
        if ($voices.Count -eq 0) { throw 'FIRIK_VOICE_MISSING' }
        $speaker.SelectVoice($voices[0].VoiceInfo.Name)
    }
    $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int](($data.rate - 175) / 15)))
    $speaker.Speak([string]$data.text)
} finally { $speaker.Dispose() }
"""


def _stop_process(process) -> None:
    """Terminate and reap even when a backend ignores the first signal."""
    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    except ProcessLookupError:
        process.wait(timeout=1)
    finally:
        for name in ("stdin", "stdout", "stderr"):
            pipe = getattr(process, name, None)
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass


def _parse_mac_voices(data: bytes, language: str) -> str | None:
    names = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(.+?)\s+([a-z]{2})_[A-Z]{2}\s+#", line)
        if match and match.group(2) == language:
            names.append(match.group(1).strip())
    # Catalog order includes novelty voices such as Albert before ordinary
    # English voices. Prefer natural installed voices without inventing names;
    # modern macOS may append a localized language description to each name.
    preferred = {"en": ("Samantha", "Alex", "Daniel", "Karen", "Moira", "Tessa",
                        "Fiona", "Serena", "Ava", "Allison", "Tom"),
                 "tr": ("Yelda",)}
    for candidate in preferred.get(language, ()):
        for name in names:
            if name == candidate or name.startswith(candidate + " ("):
                return name
    return names[0] if names else None


class LocalSpeaker:
    """Speak on a background worker using an installed OS voice."""

    def __init__(self):
        self._mac_voices: dict[str, str] = {}

    def _mac_voice(self, language: str, cancel: threading.Event) -> str:
        if language in self._mac_voices:
            return self._mac_voices[language]
        # Bound voice-list capture as well. This reader stores at most 64 KiB;
        # cancellation/timeout terminates the process and releases its pipe.
        process = subprocess.Popen(["/usr/bin/say", "-v", "?"], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        result = []
        finished = threading.Event()

        def read():
            try:
                result.append(process.stdout.read(65537))
            except (OSError, ValueError):
                pass
            finally:
                finished.set()

        reader = threading.Thread(target=read, name="FirikVoiceList", daemon=True)
        reader.start()
        deadline = time.monotonic() + 3
        try:
            while not finished.wait(.05):
                if cancel.is_set() or time.monotonic() >= deadline:
                    break
            if cancel.is_set():
                return ""
            if not finished.is_set() or not result or len(result[0]) > 65536:
                raise VoiceSpeechError("Sistem sesleri okunamadı. Sistem konuşma ayarlarını kontrol edin.")
            voice = _parse_mac_voices(result[0], language)
            if voice is None:
                raise VoiceSpeechError(
                    "Bu dil için sistem sesi bulunamadı. macOS Erişilebilirlik > Seslendirilen İçerik "
                    "ayarlarından Türkçe veya İngilizce bir ses yükleyin."
                )
            self._mac_voices[language] = voice
            return voice
        finally:
            _stop_process(process)
            reader.join(timeout=1)

    def speak(self, text: str, cancel: threading.Event, language: str = "auto", rate: int = 175) -> None:
        text = speech_text(text)
        if cancel.is_set() or not text:
            return
        if language not in {"auto", "tr", "en"}:
            raise VoiceSpeechError("Konuşma dili otomatik, Türkçe veya İngilizce olmalıdır.")
        if isinstance(rate, bool) or not isinstance(rate, int) or not 80 <= rate <= 350:
            raise VoiceSpeechError("Konuşma hızı dakikada 80 ile 350 sözcük arasında olmalıdır.")
        try:
            if sys.platform == "darwin":
                args = ["/usr/bin/say", "-r", str(rate)]
                if language != "auto":
                    voice = self._mac_voice(language, cancel)
                    if cancel.is_set():
                        return
                    args.extend(["-v", voice])
                args.extend(["-f", "-"])
                payload = text.encode("utf-8")
            elif sys.platform == "win32":
                executable = shutil.which("powershell.exe")
                if not executable:
                    raise VoiceSpeechError("Windows PowerShell bulunamadı. Sistem konuşma bileşenlerini kontrol edin.")
                encoded = base64.b64encode(_WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")
                args = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
                payload = json.dumps({"text": text, "language": language, "rate": rate}, ensure_ascii=False).encode("utf-8")
            else:
                executable = shutil.which("espeak-ng") or shutil.which("espeak")
                if not executable:
                    raise VoiceSpeechError("Sesli yanıt için espeak-ng kurun ve ses çıkış aygıtını kontrol edin.")
                args = [executable, "--stdin", "-s", str(rate)]
                if language != "auto":
                    args.extend(["-v", language])
                payload = text.encode("utf-8")
            if cancel.is_set():
                return
            options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL,
                       "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                # A packaged GUI must not open a console for each response.
                options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            process = subprocess.Popen(args, **options)
            write_errors = []

            def write_input():
                # On Windows Python 3.10–3.13 communicate(input, timeout) can
                # block writing stdin before its timeout starts. Keep even that
                # bounded write separate from the cancellation owner.
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (OSError, ValueError) as error:
                    write_errors.append(error)
                finally:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass

            writer = threading.Thread(target=write_input, name="FirikSpeechInput", daemon=True)
            writer.start()
            deadline = time.monotonic() + min(600, max(30, len(text) / rate * 30))
            try:
                while not cancel.is_set():
                    if time.monotonic() >= deadline:
                        raise VoiceSpeechError("Sesli yanıt zaman aşımına uğradı. Sistem ses çıkışını kontrol edin.")
                    try:
                        process.wait(timeout=.05)
                        if process.returncode:
                            raise VoiceSpeechError(
                                "Sesli yanıt oluşturulamadı. Seçili dil için bir sistem sesi yükleyin "
                                "ve ses çıkış aygıtını kontrol edin."
                            )
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Reaping the reader process releases a writer blocked on a
                # full OS pipe. Do this before closing stdin or joining it.
                try:
                    _stop_process(process)
                finally:
                    writer.join(timeout=1)
            if not cancel.is_set() and (writer.is_alive() or write_errors):
                raise VoiceSpeechError("Sesli yanıt metni sistem konuşma hizmetine iletilemedi. Yeniden deneyin.")
        except VoiceSpeechError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            if not cancel.is_set():
                raise VoiceSpeechError("Sistem konuşma hizmeti başlatılamadı. Ses bileşenlerini kontrol edin.") from error
