# Voice implementation checkpoint, 4 November 2026

The current repository implements explicit push-to-talk and conversational VAD,
reviewed transcripts, independent Evren ASR/TTS, ordinary chat and project coding
execution, deterministic commands, operation approval, response summaries,
playback controls, device settings and local input/output tests. The changes
include the secure-voice work already present at the start of this continuation.
The comparison baseline is commit `8775ff55aa8096afdaa61f555a7eafea31ca901d`.

Actual microphone recognition, remote inference and physical output remain open.
Live inference was kept disabled at the user's direction. The automated
acceptance tests use fake capture/output, mock audio HTTP and a scripted LLM
while executing the real desktop composer, request router, ChatSession, Agent,
coding bridge, history and speech queue.

| Phase | Implementation and evidence |
| --- | --- |
| Audit and contracts | [Architecture, reusable modules, state/events and capability matrix](../../../VOICE_ARCHITECTURE.md). Current read-only schema and authenticated catalogue [evidence](provider-discovery.json). |
| Core engine | Bounded capture, stable device selection, PCM/resampling, energy/optional WebRTC VAD, normalized ASR/TTS, synthesis/playback, generation isolation and resource ownership in `firik_agent/voice/`. |
| Evren | `qwen3-asr-1.7b` multipart ASR, `voxcpm2` binary TTS, independent conversation model, explicit catalogue discovery, safe failures/cooldowns and one ASR client per segmented utterance. The current public audio schemas match the vendored contract. |
| Agent | Existing chat/coding services, immutable scoped requests, exact local commands, existing tool/MCP policy plus separate trusted operation approval and concise coding summaries. |
| Desktop | Composer hold/toggle, conversational window, editable review, independent listening/speech/agent stops, playback pause/resume/replay, shared voice settings, explicit local input meter and synthetic output tone. |
| Hardening | Safe plugin logs, cancelled-ASR byte release, permission recovery, session busy-state reset, bounded/cancellable native ownership, hardware-free tests, stricter packaged audio discovery and documented deployment checks. |

Additional fixes in this continuation include a public retry API that captures
fresh audio, safe thread-start failure cleanup and local speaker testing.
Settings changes, navigation, save, close and shutdown cancel device tests.
Default plugin logging now records sizes/status and safe errors, with content
removed from prompt, tool arguments and tool-result messages.

## Automated acceptance

Every row below has automated coverage. Physical speech/device and remote
inference behavior still require the [manual platform checklist](../../../VOICE_PACKAGING.md).

| Scenario | Coverage and observed result |
| --- | --- |
| A | `test_desktop_and_voice_modules_import_without_any_optional_audio_or_ml_packages`, saved-conversation startup and disabled-device tests preserve text paths without opening audio. Native settings tests verify lazy initialization. |
| B | Native composer hold/release tests enter the visible recording state and finalize once. The full pipeline tests assert this state through a fake capture device. |
| C | Full native pipeline recognizes the mocked Turkish transcript, reviews or safely auto-sends it once to the existing session and persists its user message. |
| D | The real Agent executes through the preselected scripted chat/coding model. Production Evren adapters send mocked ASR/TTS HTTP, validate returned WAV and play through fake PortAudio. |
| E | Playback cancellation, pause/resume, focus loss and manual interruption tests stop audio independently. Full pipeline checks preserve assistant text after stopping speech. |
| F | Controller and native routing tests reject delayed transcripts after chat/project/workspace/revision changes. |
| G | Full native pipeline routes a spoken project-test summary into the existing coding run, records it as completed and synthesizes a concise summary. The scripted answer explicitly says tests were not run. |
| H | Router, service, coding policy and native exact-operation dialog tests require trusted review, reject spoken grants and preserve existing deny decisions. |
| I | Fake permission refusal shows actionable guidance and text fallback. Controller regressions also recover to playback and typed-agent phases after refusal. |
| J | Full disconnected-ASR pipeline shows a safe error and continues through typed chat in the same session. Mock TTS/provider cases cover network/timeout/auth/quota/rate-limit failures without billable retry. |
| K | Shutdown, repeated cycles, native-handle close, cancelled-Future byte release, stale callbacks and both device-test lifecycles have focused tests. Stuck fake drivers retain ownership until they actually close. |
| L | The default suite uses no real device or provider account. The live test requires an explicit flag and separate smoke key and skips by default. |

The full pipeline cases are in
[`test_ui_voice_acceptance.py`](../../../../tests/test_ui_voice_acceptance.py).
Other focused contracts remain in the voice/controller/provider/router/execution
and existing chat/coding test modules.

## Actual test output

| Check | Result | Saved output |
| --- | --- | --- |
| Broad backend, CLI, chat/coding and voice regression | **2,067 passed, 4 skipped in 158.37s** | [non-gui-tests.log](non-gui-tests.log) |
| Applicable native Tk voice, settings, chat, coding, history and model selection | **141 passed in 116.14s** | [desktop-tests.log](desktop-tests.log) |
| Full desktop capture → ASR → real agent → TTS acceptance with simulated I/O | **4 passed in 5.87s**, also included in the native run | [acceptance-tests.log](acceptance-tests.log) |
| Default skip reasons | Docker live opt-in, Evren live opt-in and two Windows-only guards | [skips.log](skips.log) |
| Changed Python/spec grammar | **44 files parsed as Python 3.10** | [grammar.json](grammar.json) |
| Current macOS arm64 development package | Strict ad-hoc signature, input entitlement/purpose, native PortAudio, frozen startup/shutdown and device enumeration passed | [package-check.json](package-check.json), [build-smoke.log](build-smoke.log) |

The desktop test process had macOS WindowServer access. Its audio capture,
speaker output and inference were simulated. The frozen smoke opened zero
audio streams and made zero provider requests. Developer ID distribution,
the OS microphone consent prompt, physical capture/playback and native
Windows/Linux execution remain unverified.

Reproduce the broad test with an active development environment:

```sh
python -m pip install -e ".[dev,voice]"
python -m pytest -q --ignore-glob='tests/test_ui*.py'
python -m pytest -q tests/test_ui_voice.py tests/test_ui_voice_acceptance.py tests/test_ui_audio_engine.py tests/test_ui_voice_modes.py tests/test_ui_voice_conversation.py tests/test_ui_voice_experience.py tests/test_ui_voice_provider_settings.py tests/test_ui_voice_settings_section.py tests/test_ui_voice_routing.py tests/test_ui_chat.py tests/test_ui_chat_workspace.py tests/test_ui_coding_workspace.py tests/test_ui_coding_history.py tests/test_ui_model_selection.py
```

Native UI tests need an interactive desktop or an appropriate display server.
The [voice guide](../../../voice.md) supplies source launch, settings, privacy,
safe timing logging and the separate opt-in live test instructions. The
[packaging guide](../../../VOICE_PACKAGING.md) supplies source/frozen build
commands and OS troubleshooting.

## Measured local timings

[synthetic-timings.json](synthetic-timings.json) contains 32 safe measurements from
the full pipeline run. They describe local application scheduling and simulated
inference. User review was automated, capture was a short synthetic hold and
PortAudio output was a fake callback.

| Scenario | ASR completion | Agent first text | First synthesis payload | Capture-end to first output buffer |
| --- | --- | --- | --- | --- |
| Reviewed Turkish chat | 3.2 ms | 26.5 ms | 1.5 ms | 151.2 ms |
| Auto-sent Turkish chat | 2.7 ms | 10.0 ms | 1.1 ms | 100.5 ms |
| Reviewed coding summary | 5.0 ms | 212.9 ms | 1.3 ms | 494.0 ms |

The synthetic VAD clock regression separately verifies the configured 700 ms
silence endpoint. The coding case includes context/database work and startup
through the ordinary bridge. In deployment, silence endpointing, sequential ASR
segments, provider networking, full first-sentence synthesis, sample conversion
and OS device buffers contribute latency. The current adapter uses a fresh TTS
HTTP context for each sentence. Live inference and acoustic latency are
unmeasured. These values establish no real-time guarantee.

## Compatibility and remaining work

Voice and reply reading default off. Transcript review defaults on. Existing
legacy config aliases and TTS-provider migration remain supported. Invalid
optional voice configuration leaves text startup available. Base text imports
work with every optional audio package absent.

`VoiceController.retry_transcription()` remains callable for existing embeddings
and now captures fresh audio. The UI labels this **Yeniden kaydet**. Same-audio
retries have ended to release private microphone buffers after each attempt.
Enabling persistent raw-audio storage still fails validation. Output replay
keeps response text and can incur a new TTS charge.

Audio HTTP handling requests identity encoding to enforce response bounds and
rejects compressed HTTP audio responses. Error diagnostics retain safe status
and cooldown metadata. The desktop build extra adds PyInstaller/sounddevice,
and optional compressed audio decoding/WebRTC VAD still require their extras.

Public schema and authenticated model catalogue access are verified. Actual
ASR/TTS inference, Turkish/English quality, service capacity and latency,
physical audio, permission prompts and native Windows/Linux deployment remain
open. Production acoustic echo cancellation, simultaneous barge-in, partial
ASR, remote streaming TTS and local wake word remain unavailable.

A destroyed settings form remained reachable while its Tk root lived, including
after event processing and garbage collection. Root destruction allowed
collection while stored output-test callbacks remained. CustomTkinter global
scroll bindings are an identified possible retaining mechanism. The complete
reference graph was not proven. This existing widget-lifetime issue remains
open. Audio-test callbacks use weak references and independent handle, worker
and native-lock cleanup tests passed.

The full [changed file list](changed-files.txt) includes source, tests,
dependencies, packaging, examples, documentation and captured evidence relative
to the baseline commit.
