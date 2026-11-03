# Voice implementation validation — 31 October 2026

Validated in the existing macOS ARM64 checkout with Python 3.13.2. Changes are
local and uncommitted; pre-existing diagnostics/CLI changes were preserved.
The architecture audit, integration decisions and user controls are documented
in [the voice guide](../../../voice.md).

| Check | Observed result |
| --- | --- |
| Complete non-GUI suite | 1,495 passed, 3 skipped; `non-gui-tests.log` |
| Final focused voice suite | 124 passed; `voice-tests.log` |
| Selected native desktop regressions | 50 passed in separate processes; `desktop-tests.log` |
| Final desktop pipeline rerun | Passed; real service/controller/runtime/HTTP client/Tk queue, injected microphone/network/speaker |
| Python 3.10 grammar | 16 application modules passed; execution used Python 3.13.2 |
| Native dependency | sounddevice 0.5.6 and PortAudio 19.7.0 loaded |
| Native macOS freeze | Version 0.2.34, microphone purpose and audio-input entitlement, bundled PortAudio, no ASIO DLLs; `bundle.json`, `pyinstaller.log` |
| Frozen startup/shutdown | Exit 0 outside the checkout, disposable profile and no configured MCP autostarts |
| Native synthesis / live FIRIK STT | Both Turkish and English synthesized samples transcribed successfully; `synthetic-audio.json` |
| Patch whitespace | `git diff --check` passed |

The development bundle is at `/tmp/firik-voice-dist/firik-agent.app`; it uses
ad-hoc signing. Its successful smoke launch emitted the macOS diagnostic
`sandbox_extension_issue_file_to_process failed ... Operation not permitted`
on stderr. Developer ID signing, notarization and signed permission-prompt
behavior were not validated.

The normal all-in-one GUI run encountered a native Tcl/Tk abort/segfault when
several interpreters shared one process on this host. `verify-desktop.py`
reproduces the successful isolated run without changing product behavior:

```sh
python docs/Evidence/Voice/20261031/verify-desktop.py
```

The live transcription check used only two locally synthesized, non-sensitive
test clips, the existing FIRIK credential and the documented transcription
endpoint. No microphone was opened for this check. Raw audio was not added to
this repository. Synthetic sample accuracy does not establish real-room or
microphone quality. Audible speaker output, physical microphone capture and
Windows/Linux hardware execution remain unverified.

Desktop tests cover transcript review before submission, preservation of
existing drafts/model/session/project identity, one-time reading on successful
completion, cancellation and hidden-session isolation, exact command review,
rejection of spoken approvals, disabled/error text fallback, settings saving,
and the supported 330-pixel coding pane. Backend tests cover bounded PCM/WAV,
silence/overflow/errors, stale callbacks, cross-controller device serialization,
HTTP cancellation, subprocess injection resistance and cleanup, Windows blocked
stdin cancellation, and releasing UI references while native workers unwind.
