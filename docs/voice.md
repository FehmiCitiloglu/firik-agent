# Desktop voice interaction

Voice is an optional input/output layer over the existing chat and coding services.
It uses the selected conversation, execution policy, stream, history and approval
controls. ASR and TTS have their own providers/models; changing a voice never
changes the model selected for chat or coding. It does not create a parallel agent session.

## Assistant console

The voice window is a session-bound assistant console. Its animated orb follows
listening, transcription, review, thinking, execution, playback and error states.
The center bars and input meter follow actual microphone levels; output playback
has a state animation, not a fabricated audio waveform. **Hareketi azalt** stops
decorative motion, and hidden/closed windows stop animation timers. Dark and
light palettes follow the application's theme. Microphone, speech and task stop
controls remain visible when the transcript area scrolls.
The header identifies the current chat or project. **Konuş** also permits a new
push-to-talk command while a coding task is running and conversational listening
is inactive; it never cancels that task.

Say a complete phrase while recording or in an explicitly started conversation:

| Example | Behavior |
| --- | --- |
| Jarvis, ne yapıyorsun? / Görev ne durumda? | Report actual selected-session activity; no model request. |
| Projelerimi listele / Sohbetlerimi listele | List existing names, without creating projects or conversations. |
| Evren projesine geç / Planlama sohbetine geç | Resolve a unique exact existing name and navigate after review. |
| Kodlamaya geç | Open an existing coding session; choose when multiple targets exist. |
| Görevi duraklat / Göreve devam et / Görevi durdur | Use the selected coding run's ordinary control service. Pause/resume are coding-only. |
| Görev planını göster / Son görevi özetle | Read the recorded plan or latest assistant result; an unfinished task is identified as unfinished. |
| Ayarları aç / Sohbete geç | Navigate through the application's normal views. |
| Neler yapabilirsin? | Explain available application controls locally. |

**Firik** and **Jarvis** are optional transcript prefixes, including `Hey Firik`;
they do not enable wake-word or background listening. `Lütfen` / `please` can
surround a complete command. Compound or arbitrary phrases are not guessed as
navigation commands; task descriptions continue through the normal selected
chat/coding agent, with its context, tools and approvals.

**Sesle neler yapabilirim?** exposes contextual command suggestions. Selecting a
suggestion cancels unfinished audio and opens an editable review, even when
ordinary voice auto-send is enabled. It executes only after **Gönder**. Exact
read-only status/inventory/plan commands use local application state; reading
their answer aloud uses the configured TTS provider. Speech cannot approve a
tool or plan.
When **Sıradan istekleri otomatik gönder** is enabled and **Göndermeden önce her
transkripti incele** is disabled, spoken read-only status, inventory, plan and
summary queries can answer and return to listening without a send click.
Navigation, pause/resume and task cancellation still require transcript review.

Reviewed navigation to another chat/project continues an active voice
conversation with the destination's actual session/project scope. Source audio
is cancelled first. A busy destination opens its console without starting
listening or a new task. Leaving for a non-conversation view, such as settings,
does not keep an invisible microphone session running.

The console shows real tool progress and approval waits without copying tool
arguments or hidden reasoning. Visible assistant prose streams into its answer
area (bounded to 8,000 characters); the normal chat retains the full history and
technical evidence. Reopening the console applies the same secret/hidden-text
filters as live updates.

## Start here

Install `python -m pip install -e '.[voice]'`, launch `firik-gui`, then open
**Ses ayarları** beside a conversation composer and enable voice. Voice starts
disabled and never opens the microphone at application startup.

```sh
python -m pip install -e ".[voice]"
python -m firik_agent.ui
```

Use the application's ordinary **Ayarlar** page to configure the Evren endpoint
and credential. Enable voice in **Ayarlar → Ses** and choose the input/output
devices. The checked-in `config.yaml` shows the default voice block. ASR and
TTS share the existing endpoint-scoped credential service. The selected
conversation or project keeps its own LLM.

- **Basılı tut** is the default push-to-talk control. Hold it while speaking;
  release to finalize. The configurable shortcut defaults to **Ctrl+Shift+V**
  while the application is focused. Releasing the key/modifier or losing
  application focus ends the hold. **Aç / bitir** is the click-to-toggle and
  keyboard-accessible alternative. Recording state and elapsed time are visible.
- **Sesli sohbet** explicitly begins a conversational session. Local VAD detects
  a sustained speech onset and the configured trailing silence. Only a completed
  utterance goes to transcription. Silent windows make no transcription request.
  **Duraklat / Devam et** pauses/resumes the same conversation; **Sohbetten çık**
  ends the voice session. Follow-ups retain the conversation's session ID.
  Starting a conversation clears a previous **Sesli komut** selection so ordinary
  questions can be sent from its review window. Exact supported commands still work.
- Final recognition appears in an editable review field. **Gönder / Uygula**,
  **Kopyala**, **Yeniden kaydet**, and **Vazgeç** send/apply, copy, start a new
  recording, or discard. Transcripts stay outside history until submitted through
  the normal composer/service. Existing typed drafts and attachments are never
  overwritten by voice. A busy conversation keeps the transcript for later.
- Review is the default. To auto-send ordinary explanatory requests, enable
  **Sıradan istekleri otomatik gönder** and disable **Göndermeden önce her
  transkripti incele**. Unknown or action-bearing requests remain in review.
  This conservative text policy is an input convenience; existing tool/plan
  approvals remain authoritative and cannot be granted by speech.
- Enable **Tamamlanan yanıtları sesli oku** for response playback through the selected TTS provider.
  **Sesi duraklat / Sese devam et** pauses/resumes output, **Konuşmayı durdur**
  cancels it, and **Yeniden dinle** reads the last response again. Pressing the
  microphone cancels playback and waits for its native handle to close before
  opening input. Select a microphone and speaker in **Ses ayarları**; each can
  follow the system default. Input gain and response volume are separate controls.
  The response volume changes this output stream without changing system volume.
  Optionally enable **Yanıt cümleleri tamamlandıkça oku** to start reading stable
  sentences while the assistant's text is still arriving. Leave it off to read
  only completed replies.

The supplied capture and playback stack has no acoustic echo cancellation (AEC).
It suppresses capture during output across all conversations in the application.
Simultaneous voice barge-in is available only to an injected stack whose recorder
and speaker both explicitly advertise reliable support; setting `barge_in: true`
alone cannot enable it. The settings dialog explains this restriction and keeps
it off. Headphones are recommended for reducing room echo and feedback.

**Dinlemeyi durdur**, **Konuşmayı durdur**, and **Ajanı durdur** are independent.
Stopping input cancels unsubmitted audio/transcription and pauses conversation
listening; it never cancels a coding task. Stopping speech cancels local playback.
The agent stop button and reviewed stop-agent command use the existing stream or
coding-run cancellation service. Exiting a voice session also leaves agent work
running. Application close still cancels active application work as usual.

## Commands and language

A spoken `Firik,` prefix is stripped after transcription. It is not a wake word:
the user must first hold the control or explicitly start a conversational session.
Command parsing uses complete allowlisted phrases, never fuzzy intent matching.
Commands work in both modes; the separate **Sesli komut** selection restricts
push-to-talk submission outside a voice conversation to supported commands.
Unknown command text stays available to edit. Review errors also appear next to
**Gönder** in the voice window, with the rejected transcript preserved for editing.

| Spoken example | Actual application path |
| --- | --- |
| Firik, explain the current project architecture. | Normal selected chat/coding request. |
| Firik, summarize the test failures. | Normal selected chat/coding request, with its existing context. |
| Firik, switch to the existing coding conversation. | Navigate to an existing project coding session; no new run, resume, or cancellation. Prefer the selected project, otherwise use the sole existing session or show a project chooser. |
| Firik, read your last answer aloud. | Read the most recent completed assistant answer through the local speaker. |
| Firik, stop speaking. | Cancel only local speech playback. |
| Firik, explain that again in Turkish. | Normal follow-up requesting Turkish in the same conversation. |
| stop response / yanıtı durdur | Reviewed cancellation through the existing session/run service. |
| new chat / yeni sohbet | Reviewed navigation through the normal chat workspace (chat surface only). |

Auto-send can apply exact read-only status/inventory/plan/summary queries as well
as read-last and stop-speaking commands. Navigation,
new-chat and agent-cancellation commands always require review. No voice command
approves a tool or plan. Transcription uses `/audio/transcriptions`, never a
translation endpoint. `auto` omits the language hint; `tr` / `en` supply one.
Transcripts retain the recognized language; only explicit user requests translate.
No partial transcription is displayed because this API returns finalized text.

## Evren speech providers

The configured remote origin is `https://evren-llmapi.ssyz.org.tr/v1`. Default ASR
is `qwen3-asr-1.7b`; default TTS is `voxcpm2`. Fresh profiles use Evren with voice
and reply speech disabled. Existing voice sections without a TTS provider retain
system speech until the user explicitly selects Evren. **Ses ayarları** exposes
separate provider/model choices and a GET-only model-access check, which never
opens the microphone. The conversational LLM is preserved. `qwen3.8-flash-next`
is an optional fast conversational preset; `glm-5.3` and `deepseek-v4.1-flash` are
coding candidates, not speech models. `auto`, OCR, embedding, reranking, guard,
`clef` and `laya` are excluded from speech model choices. Command classification
remains local and deterministic; no unverified SystemOne contract is assumed.

`SpeechToTextProvider` and `TextToSpeechProvider` are independent async protocols.
Evren adapters implement each separately; system TTS is an explicit local choice.
A future local ASR adapter can implement the same contract; selecting `local`
today reports that the adapter is unavailable. No failure selects another remote
provider or a local backend automatically. Text input remains available after
voice errors and stopping listening leaves agent execution running.

On 4 November 2026 read-only verification returned HTTP 200 for the public
OpenAPI and authenticated `GET /v1/models`, using an existing endpoint-scoped
credential. Both audio request schemas match the checked-in contract. The
catalogue declares `qwen3-asr-1.7b` with `task: audio_transcription` and `voxcpm2`
with `task: tts`. **ASR/TTS inference, response media types and Turkish/English
audio quality remain unverified.** Live inference remains disabled at the user's
direction. The [architecture and capability matrix](VOICE_ARCHITECTURE.md)
records the current evidence. The earlier
[2 November discovery](Evidence/Voice/20261102/API_DISCOVERY.md) retains its
historical unauthenticated result. Discovery grants speech roles from explicit
task metadata. Ordinary chat lists filter specialized models.

ASR uses validated multipart PCM WAV, with automatic language or explicit `tr` /
`en` hints. The normalized result has `text`, optional provider-supplied
`language` / `duration`, and separate metadata. It never fabricates confidence
or timings. TTS sends original UTF-8 text; the API has no language parameter.
Turkish and English text may be requested, but language-specific quality needs
authenticated testing. The only exposed voice is `default`; documented aliases
map to that voice. Formats are WAV, MP3, FLAC, Opus and raw PCM (16-bit mono,
48 kHz); WAV is the desktop default. The documented speed range is 0.25–4.
The low-level client supports the documented optional voice-style instruction;
voice cloning and reference audio are absent.

Credentials reuse the endpoint-scoped OS store/environment lookup and never enter
voice configuration. ASR/TTS POSTs are sent once, with no automatic billable
retry. `Retry-After` (seconds or HTTP date) blocks subsequent requests until its
deadline; 429 without a valid header uses a 30-second cooldown. The shared service
allows one ASR and one TTS request in flight. Authentication, credits, rate limits,
server errors and malformed audio produce safe Turkish messages. Cancelling
pending synthesis stops delivery/playback and cancels HTTP; the server may already
have completed billable work. Explicit retry/replay can incur a new charge.

Client budgets are 25 MiB/120 seconds of input, at most eight sequential ASR
segments, 256 KiB of transcription response, 1,600 characters/8,192 UTF-8 bytes
per synthesis sentence, eight queued speech items and 8,000 characters per reply.
Encoded audio is bounded to 25 MiB and decoded playback to 120 seconds. These
are client policies; provider limits and prices are not inferred.

## Modular engine

All units live under `firik_agent/voice/`. Imports do not open devices, start
listening or import optional native audio/ML packages. Tests can inject devices,
detectors, inference calls, synthesizers, players and dispatchers independently.

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Public controller, settings and command exports. |
| `models.py` | Validated audio format and immutable PCM/WAV values. |
| `config.py` | Additive, validated preferences without credentials. |
| `providers.py` | Separate ASR/TTS protocols, capability evidence, remote/local adapters, request budgets. |
| `controller.py` | Conversation orchestration, generations, review and worker lifecycle. |
| `audio_capture.py` | Bounded capture queue, gain, levels and stateful resampling. |
| `audio_devices.py` | Input/output enumeration and saved device resolution. |
| `vad.py` | Energy/WebRTC detectors and utterance endpointing. |
| `transcription.py` | WAV validation, request budgets and sequential ASR inference. |
| `synthesis.py` | Stable sentence extraction, bounded synthesis queues and default `EngineSpeaker`. |
| `playback.py` | Actual format decoding, per-stream volume and native output ownership. |
| `intent_router.py` | Exact allowlisted intent API, shared with transcript review. |
| `session.py` | State transitions and application-wide capture/output coordination. |
| `events.py` | Coalesced worker telemetry for main-thread dispatch. |
| `errors.py` | Typed permission, device, overflow, format, synthesis and playback errors. |
| `audio.py`, `speech.py`, `commands.py` | Compatible entry points and existing native-speaker/command helpers. |

## Capture, VAD and remote inference

`SoundDeviceRecorder` uses PortAudio through `sounddevice`. Its raw callback API
works with byte buffers and does not require NumPy. This supports the host's
CoreAudio, Windows and Linux PortAudio devices without introducing a local ASR
model. Backend installation and stream semantics are documented by
[sounddevice](https://python-sounddevice.readthedocs.io/en/latest/installation.html)
and its [stream API](https://python-sounddevice.readthedocs.io/en/latest/api/streams.html).

Device pickers enumerate input and output separately on a worker. New saved IDs
derive from host API and exact device name, so device index reordering does not
change the selected device. PortAudio has no universal persistent hardware UUID:
identical names on the same host API are treated as ambiguous and require a new
selection. Missing devices fail instead of silently opening another microphone.
Legacy numeric indexes and exact names remain readable; indexes are session-local.
`null` resolves the current system default at every stream open.

Capture requests one channel of signed 16-bit PCM at the device's validated
native rate. The callback checks frame length and status, then uses a nonblocking
write to a finite queue (25 blocks by default). The consumer handles gain,
resampling, levels and segmentation. Queue/native overflow, short data, explicit
permission denial and a stopped or unresponsive stream fail closed. Cancellation
and errors abort and close the owning native stream. Frame and byte budgets cap
recordings independently; a stalled callback is detected after two seconds.

The controller normally converts to 16 kHz mono PCM16 WAV. Configured rates are
8, 16, 32 or 48 kHz. Resampling uses integer positions across blocks, a low-pass
filter when downsampling and a bounded sample history. Finalization produces
`round(input_frames * output_rate / input_rate)` samples, including 44.1 kHz
conversions, to avoid cumulative timing drift. Adjustable gain clips safely to
the PCM range. A DC-corrected RMS meter updates at most ten times per second;
`LatestEvent` keeps at most one pending Tk telemetry job.

The default energy detector adapts its threshold conservatively to the idle noise
floor. Sustained onset rejects isolated impulses such as single key presses;
pre-roll preserves initial speech and trailing silence debounces short pauses.
Minimum speech duration, silence duration, threshold and pre-roll are configurable.
The maximum window includes idle waiting as well as speech. Empty/no-speech
windows never reach ASR. Persistent keyboard noise or loud background sound can
still cause false positives: this detector is not noise removal or AEC.

For a CPU-friendly speech classifier, install `voice-vad` and select `webrtc`.
The [WebRTC VAD interface](https://github.com/wiseman/py-webrtcvad) accepts mono
PCM16 at 8/16/32/48 kHz in 10, 20 or 30 ms frames. The engine feeds 20 ms frames
with the same pre-roll and endpointing controls; its standalone `VADConfig`
also exposes aggressiveness 0–3. A missing optional package produces a setup
error; it does not silently select another detector.
[Silero VAD](https://github.com/snakers4/silero-vad) was considered for its learned
speech detector, but would add model assets and a Torch or ONNX inference runtime.
It is not installed or downloaded by this engine. Turkish/English synthetic
cadence tests exercise pause handling; they do not establish real speech accuracy
or room-specific thresholds for either detector.

Before any HTTP request, `TranscriptionAdapter` validates the entire WAV container,
channels, PCM width, sample rate, declared frames, actual payload and total length.
It sends sequential pieces of at most `asr_request_seconds` (default 30 seconds),
preferring a low-energy boundary near the cap. Each sample is sent once in order;
overlap is avoided because this endpoint does not provide reliable alignment for
deduplication. Total audio is limited to 120 seconds/25 MiB and the default policy
permits at most eight pieces. All pieces must fit these limits before the first
upload. Empty results, cancellation or a failed request stop the sequence;
partial text is not silently submitted. Lowering the request budget may also
require lowering the recording window to stay within the segment-count limit.

**Provider limit verification remains open.** The checked-in FIRIK OpenAPI defines
`/v1/audio/transcriptions` but publishes no maximum audio duration. Thirty seconds
is a conservative client budget. Set `asr_request_seconds` to a lower value
when the configured provider publishes a lower limit.

## Synthesis and playback

The default `EngineSpeaker` separates synthesis from playback. Evren synthesis
uses the verified non-streaming speech contract and holds binary audio in memory.
For explicitly selected system TTS, installed
OS voices synthesize one stable prose sentence into a temporary WAV, which is
then decoded and played through the selected PortAudio output. macOS uses `say`,
Windows uses `System.Speech` WAV output, and Linux uses `espeak-ng`/`espeak` WAV
output. Dynamic text travels on stdin. Scratch audio is size/time checked and
its temporary directory is removed after synthesis or cancellation.

The public API documents streaming PCM, but runtime streaming behavior remains
unverified and this adapter uses non-streaming requests. `SentenceSpeechQueue`
accepts text deltas, retains incomplete Markdown
and emits stable sentences in order. Code fences, inline code, URLs, hidden
reasoning and unfinished markup are excluded from speech; Markdown links retain
their visible labels. Its nonblocking feed
returns backpressure when full. Defaults cap the queue at eight sentences and
the reply at 8,000 characters, with 1,600 characters per sentence; only one
sentence is synthesized/decoded at a time. The desktop reads completed assistant
replies by default. With `read_replies` and `stream_replies` enabled, chat/coding
text deltas enter a separate stable-sentence buffer and completed prose sentences
can begin playback before text generation finishes. Completion flushes any safe
final prose without replaying earlier sentences. The controller also bounds
consecutive pending replies and cancels all unplayed speech on interruption.
Overflow stops the spoken stream while text generation continues normally.

`decode_audio` inspects the actual bytes rather than trusting a filename or
Content-Type. Integer PCM WAV uses the standard library; optional codecs handle
MP3, Ogg, FLAC and supported float WAV through `soundfile`/libsndfile. Headerless
PCM requires an explicit validated `AudioFormat`. Encoded bytes, decoded frame
count, decoded size and duration are checked before playback allocation; output
is capped at 25 MiB and 120 seconds per audio unit. Unsupported containers or
missing codecs produce an actionable error.

If the selected output rejects the source sample rate, playback validates its
native rate and resamples each channel with bounded scratch space and exact
rounded duration. Unsupported channel layouts fail explicitly. The standalone
player can replay the source PCM without accumulating resampling error.

The output callback advances a bounded PCM block, applies stream volume and
writes silence while paused without advancing the cursor. PortAudio's finished
callback confirms completion after queued samples drain. Stop/cancellation
aborts and closes the native stream. Desktop replay keeps the last response
text and synthesizes it again. Native output ownership and the application speech lock prevent
overlapping responses. A disconnected output, underflow or stalled callback
reports failure. A device that cannot accept the decoded output format also
reports a setup error.

## State machine and ownership

`VoiceController` owns state. Tk consumes generation-checked queued callbacks;
views own finalized submission and authorized agent cancellation. `state_id` is
the canonical `VoiceState`; the legacy `state` property retains `recording` and
`idle` aliases for existing dictation embeddings.

| State | Normal next steps |
| --- | --- |
| DISABLED | Explicit enable → READY/IDLE. No capture. |
| IDLE / READY | Explicit PTT/session start → LISTENING; playback → SPEAKING; accepted request → THINKING. |
| PERMISSION_REQUIRED | Native permission/setup failure; grant permission and explicitly retry. |
| LISTENING | VAD onset → SPEECH_DETECTED; PTT release → TRANSCRIBING; silent VAD window → READY then local listening. |
| SPEECH_DETECTED | End silence / duration bound → TRANSCRIBING. |
| TRANSCRIBING | Final result → AWAITING_CONFIRMATION; failure → ERROR. |
| AWAITING_CONFIRMATION | Send → THINKING; record again → LISTENING; discard → READY then resume if active. |
| THINKING / EXECUTING | Existing agent events update the state; completion → SPEAKING or READY then resume. |
| SPEAKING | Completion / stop-speaking → READY; explicit PTT stops output before capture. Simultaneous VAD requires verified stack capabilities. |
| INTERRUPTED | Paused session; explicit resume → LISTENING, or wait for current agent completion. |
| ERROR | Display actionable failure; pause conversation to avoid retry loops; explicit retry/resume recovers. |

The transition map is explicit in `voice/session.py`. Cancellation, disable,
error, and interruption are accepted from any state. New explicit capture and
playback actions reset their operation generations. A transcript is resolved once;
repeated button/key events cannot create another submission.

`FirikService` owns one `CaptureCoordinator` per application instance. Its logical
lease permits only one active capture controller; its native lock remains held
until an old stream finishes closing. Per-surface controllers bind to an immutable
conversation ID for each operation. A generation, session ID and speech ticket
protect queued UI delivery, transcription and playback independently. Page
switches, new/cleared sessions, provider/credential changes, settings changes and
widget destruction invalidate old work. If both voice surfaces are hidden, the
panel stops active capture. A playback failure leaves separately supported
duplex input running with its recording indicator visible.
A late HTTP result cannot resurrect a cancelled session or submit into another.
Device removal/overflow fails closed, releases the stream and requires recovery.
Capture and output coordination enforces half-duplex operation for the supplied
stack, including when different conversation controllers own input and output.
The native activity gate remains held until the old handle actually closes.

Microphone/VAD and local speech run on workers. Transcription uses the existing
`AsyncRuntime`. Tk gets queue callbacks only; voice cancellation never joins a
native worker. Application shutdown invalidates voice immediately and runs network
shutdown and worker joins outside the UI event loop.
`close()` immediately invalidates callbacks and signals cancellation;
`shutdown(timeout=3.0)` joins the controller's tracked workers with a shared
deadline and reports whether they finished. `FirikService.shutdown()` invokes it
on the application's cleanup worker before stopping `AsyncRuntime`. A backend
that ignores cancellation cannot freeze Tk; a timed-out shutdown can be joined
again. Callback, decoder and network workers never mutate widgets.

## Configuration and installation

Preferences live in the normal configuration's `voice` object; credentials remain
in the existing credential store. Defaults include:

```yaml
voice:
  enabled: false
  interaction_mode: push_to_talk
  asr_provider: evren
  asr_model: qwen3-asr-1.7b
  tts_provider: evren
  tts_model: voxcpm2
  tts_voice: default
  tts_format: wav
  auto_send: false
  review_before_send: true
  speak_responses: false
  stream_replies: false
  push_to_talk_shortcut: <Control-Shift-v>
  barge_in: false
  language: auto
  max_utterance_duration: 60
  input_device: null
  output_device: null
  input_gain: 1.0
  output_volume: 1.0
  sample_rate: 16000
  asr_request_seconds: 30
  speech_rate: 175
  vad_engine: energy
  vad_enabled: false
  vad_threshold: 350
  silence_timeout: 0.7
  vad_min_speech_ms: 200
  vad_pre_roll_ms: 200
  wake_word_enabled: false
  retain_audio: false
  playback_speed: 1.0
```

Settings reject nonfinite numbers, booleans masquerading as numbers and invalid
ranges. Recording windows are 5–120 seconds, gain 0.1–4, response volume 0–1,
ASR request budget 1–30 seconds, VAD silence 250–3,000 ms, minimum speech
100–1,000 ms and pre-roll 0–1,000 ms. Microphone audio stays in memory during
capture and ASR. The app releases it after completion, cancellation or failure.
Transcript review and chat history hold text. Generated local TTS audio uses
temporary scratch files that the synthesis worker removes on exit.

Legacy `model`, `read_replies`, `shortcut`, `max_record_seconds` and
`vad_silence_ms` keys still load; canonical aliases are emitted on save and
conflicting aliases fail validation. `silence_timeout` is seconds; the old field
is milliseconds. Invalid optional voice configuration disables voice while text
startup remains usable. The preferred interaction mode affects **Aç / bitir**;
the hold control and **Sesli sohbet** remain explicit mode selections. No saved
mode starts recording on launch. `retain_audio: true` is rejected.

| Optional extra | Dependency and purpose |
| --- | --- |
| `voice` | `sounddevice>=0.5,<0.6`: PortAudio microphone/output devices and raw callback streams. No NumPy or local ML model is needed for this path. |
| `voice-vad` | Adds `webrtcvad-wheels>=2.0.14,<3`: native CPU WebRTC classification with Python wheels, exposed as `webrtcvad`; includes `sounddevice`. |
| `voice-codecs` | Adds `soundfile>=0.13,<0.14`: optional compressed/float-format decoding through libsndfile; includes `sounddevice`. Its package dependencies include NumPy and CFFI, although this engine uses its byte-buffer API. |

For example, `python -m pip install -e '.[voice-vad,voice-codecs]'` installs both
optional capabilities. `soundfile` wheels commonly include libsndfile; source
installs may need a system library. See the
[soundfile installation documentation](https://python-soundfile.readthedocs.io/en/latest/).
Base text-only imports remain supported with all audio packages absent. Selecting
an unavailable optional capability shows a recoverable error. Controls never
install packages or download model weights implicitly. The desktop installer and
current release packaging include `voice`; the other extras remain opt-in.

| Platform | Input/output backend | Local synthesis |
| --- | --- | --- |
| macOS | PortAudio/CoreAudio; pip wheels include PortAudio. Grant Microphone permission to the app or source-run Python/terminal. | System `say` writes PCM WAV; install the required Turkish/English voice. |
| Windows | PortAudio Windows host APIs/devices; pip wheels include PortAudio. Enable desktop microphone access. | PowerShell and .NET `System.Speech`, with installed language voices. Text/settings are JSON on stdin. |
| Linux | Install PortAudio (e.g. `libportaudio2`) and use a working ALSA/audio-server device exposed by that build. | Install `espeak-ng` (or `espeak`) and its language data. Native packages recommend `espeak-ng`. |

Automatic ASR language does not guarantee automatic TTS language matching: local
speech uses the OS default voice. Select Turkish/English to require that language.
The PyInstaller spec includes the microphone purpose string, macOS audio-input
entitlement, standard PortAudio runtime and notices. The frozen smoke loads
PortAudio and enumerates input and output without opening streams.
See [Voice packaging and device verification](VOICE_PACKAGING.md) for build
commands, troubleshooting, and the remaining OS permission and device checks.

## Wake word gate and privacy

Wake word remains unavailable: `wake_word_enabled: true` fails validation. No
installed recognizer or verified local `Firik` accuracy is assumed. There is no
ambient microphone at startup or remote continuously streaming recognizer. A future
opt-in detector needs local phrase-specific accuracy evidence, a bounded listening
window, and an obvious off switch before this gate can be opened. This optional
work does not block push-to-talk or conversational mode.

Explicit conversational mode does locally monitor microphone audio until paused
or exited. Only segmented utterances are sent to the configured FIRIK endpoint,
using its existing credential; retention/billing follow that endpoint's terms.
Evren TTS sends response prose to that same endpoint. Explicit system TTS uses
installed local voices and sends no response text remotely for synthesis.

## Timing diagnostics

Enable INFO for the Python logger `firik_agent.voice.telemetry` to collect
stage, status and monotonic elapsed milliseconds. The records contain no audio,
transcripts, prompts, response text, credentials, endpoint URLs or user/device
identifiers. The voice stages measure:

| Stage | Interval |
| --- | --- |
| `capture` | Capture worker start to finalized capture or a terminal capture outcome. |
| `vad_endpoint` | Last frame classified as speech to the trailing-silence decision. |
| `asr`, `asr_request`, `tts_request` | Full ASR and individual Evren request durations, with safe failure and cancellation statuses. |
| `agent_first_token`, `agent` | Accepted voice-origin service submission to first visible text delta and to request termination, respectively. |
| `tts_first_audio` | First sentence synthesis start to its audio payload becoming available. |
| `speech` | Speech worker start through synthesis and playback, including failures and cancellation. |
| `voice_end_to_end` | Finalized microphone utterance to the first reply buffer delivered to PortAudio. This includes transcript review time when enabled. |

The PortAudio callback records the first-buffer timestamp and the worker logs it.
These are application timings. Acoustic output and provider streaming latency
require separate measurements.

For a source launch with only voice timing INFO records enabled, run:

```sh
python -c "import logging; logging.basicConfig(level=logging.WARNING); logging.getLogger('firik_agent.voice.telemetry').setLevel(logging.INFO); from firik_agent.ui.app import main; raise SystemExit(main())"
```

The default logger plugin records prompt/result sizes and lifecycle information.
Plugin hook failures use the existing safe failure formatter. Transcript,
tool-argument and result contents stay out of those logs.

## Verification

The [4 November implementation checkpoint](Evidence/Voice/20261104/README.md)
records all phases, acceptance scenarios A–L, exact automated output, safe local
timings, current packaging checks, compatibility changes and unfinished native
or inference validation. The [architecture document](VOICE_ARCHITECTURE.md)
contains the model/provider capability matrix.

The [3 November integration checkpoint](Evidence/Voice/20261103/README.md)
documents the unified request path, workspace identity checks and validation
ordering after the earlier audio-engine/provider checkpoints.

The default tests inject microphones, speech processes and HTTP responses.
Unit cases cover VAD silence,
onset/end segmentation, callback/queue overflow, device reordering and ambiguity,
resampling length and filtering, actual format decoding, output controls,
sentence order/backpressure, bounds, cancellation, session generations,
application capture leases, replay/submission guards, review policy and speech
interruption. Integration cases exercise sequential ASR without sample loss,
cross-conversation echo suppression, FIFO completion races, input meter
coalescing, repeated bounded shutdown and imports with optional audio/ML
packages blocked.
Native Tk cases exercise actual controls and chat/coding service wiring, busy
execution protection, repeated follow-ups and navigation with injected audio.

### Optional live Evren smoke

With an explicitly supplied `FIRIK_LIVE_AUDIO_API_KEY` in the environment, run:

```sh
python -m pytest -q --run-live-evren-audio tests/test_evren_audio_live.py
```

This can make two billable requests to Evren. It synthesizes a fixed English
phrase and submits the returned WAV for transcription, then checks for expected
words. It uses no microphone, speaker, temporary file, or stored credential.
The default suite skips this test, even when a smoke key is present. This
checkpoint exercised the opt-in guards and mocked contracts. Live inference
and audio quality remain unverified.

Run backend checks with `python -m pytest -q --ignore-glob='tests/test_ui*.py'`.
Run `python -m pytest -q tests/test_voice_engine_input.py tests/test_voice_engine_output.py tests/test_voice_engine_integration.py`
for the modular engine tests.
Run `python docs/Evidence/Voice/20261101/verify-desktop.py` for isolated native desktop
checks; macOS needs window-server access, Linux needs a display such as Xvfb.
The historical 1 November engine checkpoint passed **297 focused voice tests**, a broader
**1,640 non-GUI tests with 3 skipped**, and **66 native desktop cases** across
isolated runs. See [the 1 November evidence](Evidence/Voice/20261101/README.md)
for logs, final case statuses, validation ordering and limitations.
The evidence directory retains the earlier dictation build/API checks as historical
snapshots; those do not establish the new conversational mode's hardware quality.
Real microphone accuracy, acoustic barge-in, speaker quality, signed permission
prompts and Windows/Linux hardware still require device testing.

On **1 November 2026**, the new native macOS synthesizer produced and decoded
English and Turkish PCM16 WAV clips at 22,050 Hz, mono, approximately 3.02 and
3.10 seconds long. This exercises the real installed voices and output container
without opening a microphone or playing audible output. See
[native synthesis evidence](Evidence/Voice/20261101/native-synthesis.json).

The **historical 31 October 2026** checkpoint passed **173 focused voice tests**
and **64 isolated native desktop cases**. Its broader non-GUI checkpoint passed
**1,536 tests** with **3 skipped**, and an ARM64 macOS development bundle passed
signature verification and packaged startup/shutdown. These totals and packaging
results predate the modular engine changes and are not fresh validation of them.
See [the Modes A/B evidence](Evidence/Voice/20261031/MODES.md) for scope, artifacts
and hardware limitations.

## Scoped requests and application integration

Final recognition enters [`VoiceRequestRouter`](../firik_agent/voice/intent_router.py),
which produces an immutable [`VoiceRequest`](../firik_agent/voice/requests.py).
The request carries an utterance ID, the existing session ID, optional project
and workspace IDs, the intended chat/coding target, UTC creation time, optional
recognized language and a cancellation token. The original transcript remains
separate from the reviewed request text. Editing reclassifies the request and
resets its transcript confirmation; it preserves the original utterance, scope
and cancellation token. These objects are temporary routing state, not another
conversation store or agent executor.

`VoiceScope` captures the session/project/workspace target and host revision.
The panel retains the scope from capture and compares it with the current scope
before dispatch. A changed scope/revision, cancelled request or obsolete review
object is rejected. The router also guards against dispatching an accepted
utterance twice. Only finalized ASR text can enter this path; partial recognition
does not trigger commands or tools.

For coding, `workspace_id` is the canonical project root captured from the
existing project record. The service checks that identity again, and the coding
bridge requires the execution CWD to resolve to the same root. Replacing a
workspace under an unchanged project/session ID requires new capture. Ordinary
chat cannot add a different prompt, attachment or CWD to its reviewed request.
Recognized language remains actual provider metadata, distinct from a configured
language hint; missing evidence stays absent, and segmented results retain one
language only when every segment reports the same nonempty value.

Navigation uses the application's actual inventory in
[`ui/app.py`](../firik_agent/ui/app.py): mounted views, existing chat pages/history
and projects in the project database. IDs match exactly; names must have one
exact normalized match. Missing or duplicate destinations require clarification.
Selecting an existing conversation/project calls its normal UI navigation path.
The explicit new-chat command uses the normal new-chat action; ordinary voice
submissions do not manufacture a different session or project.

Chat requests pass through the normal composer in
[`ChatView`](../firik_agent/ui/views/chat_view.py),
[`FirikService.chat_agent_stream_async`](../firik_agent/ui/service.py), the
existing `ChatSession` and agent loop. Coding requests use the existing coding
composer, bridge and runtime control. The selected provider/model, context,
skills, plugins, enabled MCP servers, history and execution infrastructure remain
those of that session. The ordinary
[`SessionToolFilter`](../firik_agent/core/session.py) still enforces enabled tool
names and MCP server/tool policies. Voice adds request metadata and narrower
review rules around those paths.

### Transcript review and operation approval

The local impact classifier determines transcript review requirements; it is a
conservative text rule, not a proof of an operation's effects. Medium/high impact
requests require the trusted **Gönder / Uygula** review control. Spoken approval
phrases cannot approve a tool or plan. Ambiguous destructive requests such as
“delete it” require a specific target before submission.

Transcript confirmation, including confirmation of a **HIGH** impact request,
does not authorize a later tool call. For ordinary chat,
[`VoiceToolFilter`](../firik_agent/voice/execution.py) wraps the existing session
filter: unknown/effectful tools require a separate asynchronous UI decision for
the exact tool name and full JSON arguments. Its small read-only exception list
includes handler source provenance; an MCP/plugin with a familiar tool name does
not inherit that exception. High impact requests require operation review even
for that list. An already-disabled tool remains disabled.

Chat and coding share
[`OperationApprovalDialog`](../firik_agent/ui/components/operation_approval.py).
The operation's JSON is read-only and the buttons approve or reject that one
operation. Closing the dialog rejects it. Chat rechecks cancellation, session
tool permissions and tool definition/handler identity before delegating execution;
changed approval arguments are rejected. Coding retains its existing runtime
authorization and inherited policies, and voice-origin filesystem writes require
at least `ask`, even when typed policy would allow unattended edits. Existing
`deny` decisions remain authoritative. These controls do not grant a voice
command permission to bypass MCP, sandbox, path or tool policies.

### Spoken results and independent controls

Coding completion and coding **read last answer** use
[`coding_speech_summary`](../firik_agent/voice/summaries.py) by default. This is a
deterministic summary of at most 600 characters, roughly three sentences, based
on completed prose and the supplied outcome, project, activity and agent state.
It filters recognized credential, URL and path patterns and excludes hidden
reasoning and recognizable source, diff, log and stack-trace blocks from speech.
Failure summaries use recorded failure counts or a safe exception class.
A completed run is not treated as proof that tests passed.
Full technical output remains in the ordinary coding UI. Agent-status commands
report actual supplied orchestrator/subagent state, without inventing agents.
Coding waits for this completed summary instead of reading raw streamed coding
paragraphs; ordinary chat retains its optional stable-sentence streaming mode.

Stopping TTS only stops speech. Agent stop uses the existing chat/coding
cancellation path. Agent pause/resume calls supported live coding-control APIs
at their existing operation boundaries; it does not resume a persisted run or
invoke recovery. Unsupported controls report failure instead of simulating a
state change.

Routing, execution and summary tests use injected state and fake dependencies
for deterministic checks; production handlers query the live application state
and invoke its real entry points. See
[`test_voice_request_router.py`](../tests/test_voice_request_router.py),
[`test_voice_execution.py`](../tests/test_voice_execution.py) and
[`test_voice_summaries.py`](../tests/test_voice_summaries.py) for focused contracts.
Those tests do not establish microphone accuracy, acoustic quality or successful
execution against a user's external services.

## Desktop controls and shared voice settings

The composer has a compact **🎙 Ses** entry point. Push-to-talk, the accessible
recording toggle, conversational mode and settings delegate to the existing
panel/controller; opening a voice surface creates no additional agent session.
The dedicated **Sesli sohbet** window shows microphone state, input level,
finalized transcript review and a short response preview. Microphone mute,
stop-listening, stop-speaking and exit controls remain outside its scrollable
content so they are available in a compact window. Empty transcript controls stay
hidden until there is text to review. This ASR contract returns finalized text;
the interface does not invent live recognition or confidence values.
The configured push-to-talk shortcut also works while this window has focus.

**Ayarlar → Ses** and the composer settings dialog use the same
[`VoiceSettingsFrame`](../firik_agent/ui/components/voice_settings.py). Its
**Giriş**, **Yanıt**, and **Gizlilik ve yardım** pages expose device selection,
language, separate ASR/TTS choices, VAD bounds, shortcut, response volume/speed,
review preferences and optional completion notification. Opening settings or
saving preferences does not start recording. Device enumeration runs through the
service worker and retains saved stable device IDs rather than replacing them
with a newly ordered PortAudio index.

**Mikrofon düzeyini sına** is an explicit local measurement with a three-second
budget. It opens no ASR/TTS request, uploads no audio and retains no recording.
The meter uses the service's Tk-dispatched, coalesced level callbacks. Stop,
save, leaving the visible form or closing the window cancels the test; delayed
callbacks cannot update a cancelled form. Permission/backend failures remain
visible after the worker's completion callback.

**Yanıt → Hoparlörü sına** explicitly plays a short, faded synthetic tone through
the selected output at the draft volume. It makes no provider request. Stop,
output/volume changes, leaving the page, saving and closing settings cancel it.
The shared audio coordinator serializes it with input and normal playback.
Application shutdown waits for both test workers to release their native handles.

**Tanı bilgisini göster** produces read-only offline information about optional
packages, client limits and platform permission guidance. It does not open an
audio device, fetch credentials or contact a provider. The separate model-access
button performs the existing GET-only capability check. Unsupported local ASR,
native voice enumeration, wake word, persistent raw-audio retention and the
built-in stack's simultaneous barge-in are disabled or explained; the interface
does not offer invented voices or silently choose another provider.

Saved preferences notify mounted controls through weak service listeners. A
pristine settings form reloads current values without restarting the app. If
another form saves while this form contains edits, the visible draft is preserved
with an explicit reload action. Saving merges this form's changed fields into
the latest preferences, preserving unrelated changes from the other form.
Settings saves use the existing additive service configuration path and do not
change the selected chat/coding model.

Automatic reply speech and the optional coding completion chime respect the
master voice preference and foreground/privacy checks. The chime is off by
default, contains no response text and uses the operating system's notification
volume; it is independent of reading replies aloud. A background, hidden or
unfocused conversation, an open settings/review dialog, microphone activity or
an explicitly muted voice controller suppresses automatic audio.
The same checks stop already queued automatic speech when focus is lost or the
window is minimized, without cancelling the agent's ongoing work. Manual controls
remain separate from agent execution: stopping TTS does not stop the agent, and
**Metinle devam et** returns to the existing text composer.
