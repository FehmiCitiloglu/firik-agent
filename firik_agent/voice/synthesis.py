"""Sentence-level synthesis with bounded queues and explicit cancellation.

The current provider has no verified streaming TTS contract. Native synthesis
therefore produces one bounded WAV at a time, which is decoded and played by a
separate output unit. The queue never sends unfinished Markdown to a voice.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from firik_agent.voice.errors import VoiceAudioError, VoiceSynthesisError
from firik_agent.voice.models import MAX_AUDIO_BYTES
from firik_agent.voice.playback import EncodedAudio, SoundDevicePlayer
from firik_agent.voice.speech import LocalSpeaker, MAX_SPEECH_CHARS, _WINDOWS_SCRIPT, _stop_process, speech_text
from firik_agent.voice.telemetry import log_voice_timing

MAX_SENTENCE_CHARS = 1600
_WINDOWS_WAV_SCRIPT = _WINDOWS_SCRIPT.replace(
    "$speaker.Speak([string]$data.text)",
    "$speaker.SetOutputToWaveFile([string]$data.path)\n    $speaker.Speak([string]$data.text)")


def _prose(text):
    # The legacy final-text filter accepts permissive fence closers. Streaming
    # output must honor Markdown's stricter closer grammar before using it.
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if match:
            token, suffix = match.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                fence = None
            continue
        if fence is None and not line.startswith(("    ", "\t")):
            lines.append(line)
    return speech_text("".join(lines))


def _boundaries(text):
    """Yield stable sentence endpoints only outside balanced Markdown syntax."""
    fence = None
    inline = None
    hidden = None
    bracket = 0
    paren = 0
    emphasis = {}
    index = 0
    punctuation = False
    balanced = True
    while index < len(text):
        char = text[index]
        line_start = index == 0 or text[index - 1] == "\n"
        if line_start:
            if text.startswith(("    ", "\t"), index):
                end = text.find("\n", index)
                index = len(text) if end == -1 else end + 1
                punctuation = False
                continue
            match = re.match(r" {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)", text[index:])
            if match:
                token = match.group(1)
                if fence is None:
                    fence = token
                elif (token[0] == fence[0] and len(token) >= len(fence)
                      and re.fullmatch(r" {0,3}" + re.escape(token) + r"[ \t]*(?:\n|$)", match.group(0))):
                    fence = None
                index += len(match.group(0))
                punctuation = False
                continue
        if fence is not None:
            index += 1
            continue
        if char == "<" and inline is None:
            end = text.find(">", index)
            if end == -1:
                balanced = False
                break
            token = text[index:end + 1].lower()
            match = re.fullmatch(r"<(think|analysis|reasoning)(?:\s[^>]*)?>", token)
            if match:
                hidden = match.group(1)
            elif hidden and token == "</" + hidden + ">":
                hidden = None
            index = end + 1
            continue
        if hidden:
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == "`":
            match = re.match(r"`+", text[index:])
            token = match.group(0)
            if inline is None:
                inline = token
            elif inline == token:
                inline = None
            index += len(token)
            continue
        if inline:
            index += 1
            continue
        if char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char in "*_~":
            match = re.match(re.escape(char) + "+", text[index:])
            token = match.group(0)
            # A list marker is not an opening emphasis delimiter.
            bullet = line_start and len(token) == 1 and index + 1 < len(text) and text[index + 1].isspace()
            if not bullet and not bracket and not paren and (char != "~" or len(token) >= 2):
                emphasis[token] = not emphasis.get(token, False)
            index += len(token)
            continue
        elif char in ".!?…" and not bracket and not paren:
            punctuation = True
        elif char.isspace():
            if punctuation and not bracket and not paren and not any(emphasis.values()):
                yield index + 1, True
                punctuation = False
        elif char not in '"\'”’':
            punctuation = False
        index += 1
    balanced = balanced and fence is None and inline is None and hidden is None and not bracket and not paren and not any(emphasis.values())
    yield len(text), bool(balanced)


class SentenceBoundaryBuffer:
    """Bounded incremental Markdown accumulator; unfinished fragments stay local."""
    def __init__(self, max_chars=MAX_SPEECH_CHARS * 4):
        self.pending = ""
        self.max_chars = max_chars

    def feed(self, chunk: str, *, final=False):
        if not isinstance(chunk, str) or len(self.pending) + len(chunk) > self.max_chars:
            raise VoiceSynthesisError("Sesli yanıtın bekleyen metni boyut sınırını aşıyor.")
        self.pending += chunk
        sentences = []
        endpoints = list(_boundaries(self.pending))
        consumed = 0
        for endpoint, safe in endpoints[:-1]:
            prose = _prose(self.pending[consumed:endpoint])
            if prose:
                if len(prose) > MAX_SENTENCE_CHARS:
                    raise VoiceSynthesisError("Sesli yanıt cümlesi çok uzun. Yanıtı kısaltın.")
                sentences.append(prose)
            consumed = endpoint
        if final:
            # A final unclosed code fence/reasoning/inline fragment is discarded,
            # never guessed into speech. Earlier stable sentences survive.
            if endpoints[-1][1]:
                prose = _prose(self.pending[consumed:])
                if prose:
                    if len(prose) > MAX_SENTENCE_CHARS:
                        raise VoiceSynthesisError("Sesli yanıt cümlesi çok uzun. Yanıtı kısaltın.")
                    sentences.append(prose)
            self.pending = ""
        else:
            self.pending = self.pending[consumed:]
        return sentences


class NativeSynthesizer:
    """Installed OS voices to bounded WAV; dynamic text travels only on stdin."""
    supports_streaming = False

    def __init__(self):
        self._voices = LocalSpeaker()

    def synthesize(self, text, cancel, language="auto", rate=175):
        text = speech_text(text)
        if cancel.is_set() or not text:
            return None
        if len(text) > MAX_SENTENCE_CHARS or language not in ("auto", "tr", "en") or isinstance(rate, bool) or not isinstance(rate, int) or not 80 <= rate <= 350:
            raise VoiceSynthesisError("Geçersiz veya çok uzun konuşma sentezi isteği.")
        with tempfile.TemporaryDirectory(prefix="firik-speech-") as temporary:
            path = os.path.join(temporary, "speech.wav")
            if sys.platform == "darwin":
                args = ["/usr/bin/say", "-r", str(rate), "--file-format=WAVE", "--data-format=LEI16@22050", "-o", path]
                if language != "auto":
                    voice = self._voices._mac_voice(language, cancel)
                    if cancel.is_set():
                        return None
                    args.extend(["-v", voice])
                args.extend(["-f", "-"])
                payload = text.encode("utf-8")
            elif sys.platform == "win32":
                executable = shutil.which("powershell.exe")
                if not executable:
                    raise VoiceSynthesisError("Windows konuşma hizmeti için PowerShell bulunamadı.")
                script = base64.b64encode(_WINDOWS_WAV_SCRIPT.encode("utf-16-le")).decode("ascii")
                args = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", script]
                payload = json.dumps({"text": text, "language": language, "rate": rate, "path": path}, ensure_ascii=False).encode("utf-8")
            else:
                executable = shutil.which("espeak-ng") or shutil.which("espeak")
                if not executable:
                    raise VoiceSynthesisError("Sesli yanıt için espeak-ng kurun.")
                args = [executable, "--stdin", "-s", str(rate), "-w", path]
                if language != "auto":
                    args.extend(["-v", language])
                payload = text.encode("utf-8")
            if cancel.is_set():
                return None
            options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                options["creationflags"] = 0x08000000
            try:
                process = subprocess.Popen(args, **options)
            except OSError as error:
                raise VoiceSynthesisError("Sistem konuşma hizmeti başlatılamadı.") from error
            errors = []

            def write():
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (OSError, ValueError) as error:
                    errors.append(error)
                finally:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass

            writer = threading.Thread(target=write, name="FirikSynthesisInput", daemon=True)
            writer.start()
            deadline = time.monotonic() + 120
            try:
                while not cancel.is_set():
                    if time.monotonic() >= deadline:
                        raise VoiceSynthesisError("Konuşma sentezi zaman aşımına uğradı.")
                    if os.path.exists(path) and os.path.getsize(path) > MAX_AUDIO_BYTES:
                        raise VoiceSynthesisError("Sentezlenen sesli yanıt boyut sınırını aşıyor.")
                    try:
                        process.wait(timeout=.025)
                        if process.returncode:
                            raise VoiceSynthesisError("Sesli yanıt sentezlenemedi. Seçili dilin sistem sesini kontrol edin.")
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                try:
                    _stop_process(process)
                finally:
                    writer.join(timeout=1)
            if cancel.is_set():
                return None
            if writer.is_alive() or errors:
                raise VoiceSynthesisError("Konuşma metni sistem ses hizmetine iletilemedi.")
            try:
                if not 0 < os.path.getsize(path) <= MAX_AUDIO_BYTES:
                    raise VoiceSynthesisError("Sentezlenen sesli yanıt boş veya boyut sınırını aşıyor.")
                with open(path, "rb") as source:
                    data = source.read(MAX_AUDIO_BYTES + 1)
                if len(data) > MAX_AUDIO_BYTES:
                    raise VoiceSynthesisError("Sentezlenen sesli yanıt boyut sınırını aşıyor.")
            except OSError as error:
                raise VoiceSynthesisError("Sistem sesi WAV çıktısı oluşturamadı.") from error
            return EncodedAudio(data, "audio/wav")


class SentenceSpeechQueue:
    """FIFO text → synthesis → playback on one worker, with backpressure.

    ``feed`` is nonblocking and returns False if the bounded queue has no room;
    its caller retains the delta and retries after the consumer advances. The
    caller splits large deltas that contain more sentences than queue capacity.
    final callback/error must be dispatched by the caller before touching Tk.
    Only one sentence is ever synthesized/decoded outside the text queue.
    """
    def __init__(self, synthesize, play, cancel=None, *, max_queue=8, max_chars=MAX_SPEECH_CHARS):
        if isinstance(max_queue, bool) or not isinstance(max_queue, int) or not 1 <= max_queue <= 64:
            raise ValueError("max_queue must be 1..64")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= MAX_SPEECH_CHARS * 4:
            raise ValueError("max_chars must be bounded")
        self.cancel_event = cancel if cancel is not None else threading.Event()
        self._synthesize, self._play = synthesize, play
        self._queue = queue.Queue(maxsize=max_queue)
        self._buffer = SentenceBoundaryBuffer()
        self._max_chars, self._chars = max_chars, 0
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._done = threading.Event()
        self.error = None
        self._thread = threading.Thread(target=self._consume, name="FirikSpeechQueue", daemon=True)
        self._thread.start()

    def feed(self, chunk: str, *, final=False):
        with self._lock:
            if self.cancel_event.is_set() or self._finished.is_set():
                return False
            if not isinstance(chunk, str) or self._chars + len(chunk) > self._max_chars:
                self.cancel()
                raise VoiceSynthesisError("Sesli yanıt metni boyut sınırını aşıyor.")
            pending = self._buffer.pending
            sentences = self._buffer.feed(chunk, final=final)
            if len(sentences) > self._queue.maxsize - self._queue.qsize():
                self._buffer.pending = pending
                return False
            self._chars += len(chunk)
            for sentence in sentences:
                self._queue.put_nowait(sentence)
            if final:
                self._finished.set()
            return True

    def finish(self):
        return self.feed("", final=True)

    def cancel(self):
        self.cancel_event.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def wait(self, timeout=None):
        done = self._done.wait(timeout)
        if done and self.error is not None:
            raise self.error
        return done

    @property
    def done(self):
        return self._done.is_set()

    def _consume(self):
        try:
            while not self.cancel_event.is_set():
                try:
                    sentence = self._queue.get(timeout=.025)
                except queue.Empty:
                    if self._finished.is_set():
                        return
                    continue
                if self.cancel_event.is_set():
                    return
                audio = self._synthesize(sentence, self.cancel_event)
                if audio is not None and not self.cancel_event.is_set():
                    self._play(audio, self.cancel_event)
        except Exception as error:
            self.error = error if isinstance(error, VoiceAudioError) else VoiceSynthesisError("Sesli yanıt sırası işlenemedi.")
            self.cancel()
        finally:
            self._done.set()


class EngineSpeaker:
    """LocalSpeaker-compatible synthesis/output facade with stream controls."""
    supports_barge_in = False
    supports_audio_timing = True

    def __init__(self, *, output_device=None, volume=1.0, backend=None, synthesizer=None, player=None):
        self.synthesizer = synthesizer or NativeSynthesizer()
        self.player = player or SoundDevicePlayer(device=output_device, volume=volume, backend=backend, cache_replay=False)
        self._queue = None
        self._last_text = None
        self._closed = False
        self._serial = threading.Lock()

    def pause(self):
        self.player.pause()

    def configure(self, *, output_device=None, volume=1.0):
        self.player.device = output_device
        self.player.volume = volume

    def resume(self):
        if hasattr(self.player, "resume"):
            self.player.resume()

    def stop(self):
        if self._queue is not None:
            self._queue.cancel()
        self.player.stop()

    def shutdown(self):
        self._closed = True
        self.stop()
        self.player.shutdown()
        self._last_text = None

    def replay(self, cancel):
        last = self._last_text
        if last is None or self._closed:
            return False
        self.speak(last[0], cancel, last[1], last[2])
        return not cancel.is_set()

    def speak(self, text, cancel, language="auto", rate=175, *, utterance_ended_at=None):
        if self._closed or cancel.is_set():
            return
        # Retain raw Markdown until boundaries are stable; stripping first could
        # turn incomplete inline code into ordinary speech.
        text = str(text or "")[:MAX_SPEECH_CHARS]
        if not text:
            return
        while not self._serial.acquire(timeout=.025):
            if cancel.is_set() or self._closed:
                return
        pipeline = None
        try:
            if cancel.is_set() or self._closed:
                return
            self._last_text = text, language, rate
            synthesis_started = None

            def synthesize(sentence, event):
                nonlocal synthesis_started
                first = synthesis_started is None
                if first:
                    synthesis_started = time.monotonic()
                audio = self.synthesizer.synthesize(sentence, event, language, rate)
                if first and audio is not None and not event.is_set():
                    log_voice_timing("tts_first_audio", "ok", synthesis_started)
                return audio

            def first_audio(played_at):
                nonlocal utterance_ended_at
                log_voice_timing("voice_end_to_end", "ok", utterance_ended_at, finished_at=played_at)
                utterance_ended_at = None

            def play(audio, event):
                if utterance_ended_at is not None and isinstance(self.player, SoundDevicePlayer):
                    self.player.play(audio, event, on_first_audio=first_audio)
                else:
                    self.player.play(audio, event)

            pipeline = SentenceSpeechQueue(synthesize, play, cancel)
            self._queue = pipeline
            # Small deltas keep a complete reply within queue backpressure even
            # when it contains many short sentences.
            for offset in range(0, len(text), 8):
                chunk = text[offset:offset + 8]
                while not pipeline.feed(chunk):
                    if cancel.wait(.025) or self._closed:
                        return
                    if pipeline.error:
                        raise pipeline.error
            while not pipeline.finish():
                if cancel.wait(.025) or self._closed:
                    return
                if pipeline.error:
                    raise pipeline.error
            while not pipeline.wait(.025):
                if cancel.is_set() or self._closed:
                    return
        finally:
            try:
                if pipeline is not None:
                    if not pipeline.done:
                        pipeline.cancel()
                    # The caller is an audio worker, never Tk. Native cancellation
                    # is observed promptly by synthesis and output owners.
                    # Keep the controller's native ownership gate until every
                    # handle really closes. Its shutdown(timeout) may report a
                    # stuck driver without abandoning this owning worker.
                    while not pipeline.wait(.05):
                        pass
            finally:
                self._queue = None
                self._serial.release()
