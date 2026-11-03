# Push-to-talk and conversational voice verification — 31 October 2026

This supplements the earlier dictation-only evidence in this directory.

## Current result

- Full non-GUI checkpoint: **1,536 passed, 3 skipped**, Python 3.13.2/macOS ARM64.
- After final lifecycle/transition fixes: **173 focused voice tests passed**
  (`test_voice_controller`, `test_voice_audio`, `test_voice_service`,
  `test_voice_commands`, `test_voice_speech`, `test_voice_packaging`).
- Final isolated native desktop run: **64 cases, zero failures**. See
  `modes-desktop-tests.log`; reproduce with `python verify-modes.py` from here
  or its repository-relative path. Native macOS window-server access is required.
- Additional installation/signing/package checks: **33 passed**.
- All 11 new/affected application modules parse with Python 3.10 grammar.
  `git diff --check` passed.

The native suite covers real hold/release and shifted-key events, accessible
Space/Return toggle, duration, reviewed edit/copy/retry/discard, sensitive/busy
submission guards, cancellation versus ongoing execution, old session/provider
results, existing coding-session navigation and nonblocking app close. A full
pipeline test uses real app/service/controller/runtime/HTTP-client/chat submission
with only native audio and HTTP transport injected. Two conversational follow-ups
retain the same session; pause/resume/exit close capture, and speech-disabled
completion still rearms local VAD. No microphone or live provider is used by tests.

Review caught and fixed silent VAD renewal stopping long playback, execution-state
transition rejection, playback errors leaving barge-in capture open, and ongoing
tool events discarding a user's new spoken interruption.

## Native development artifact

`/tmp/firik-voice-modes-dist/firik-agent.app` (0.2.34, ARM64, about 58 MiB)
was built from the final code. `modes-build.log` records PyInstaller output.
`codesign --verify --deep --strict` passed. Its plist describes both hold-to-talk
and explicitly activated conversational microphone use; its audio-input
entitlement is present. It uses ad-hoc signing with no TeamIdentifier. It is a
local development artifact, not a Developer ID signed/notarized release.

Final packaged startup/shutdown from `/tmp` with `--smoke-test` exited 0.
Smoke mode creates a disposable profile, disables external MCPs and makes no
microphone/live-provider request. The launcher emitted a nonfatal
`sandbox_extension_issue_file_to_process` diagnostic before successful exit.

Visual captures: `modes-chat.png` at 1120×800 logical pixels and
`modes-compact.png` at 330px width. Transcript controls and composer fit.

## Limits

Real microphone accuracy, noise/echo behavior, audible playback, permission
prompts and Windows/Linux hardware remain unverified. VAD is configurable local
RMS/onset/silence segmentation, with no acoustic echo cancellation. Wake word
remains hard-gated off pending local phrase-specific accuracy validation.
