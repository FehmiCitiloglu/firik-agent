# Modular audio engine validation — 1 November 2026

Implemented in the existing checkout, preserving the earlier voice UI and
unrelated uncommitted work. No release, commit or new installed bundle was made.
Architecture, configuration and dependency rationale are in [the voice guide](../../../voice.md).

| Check | Result and artifact |
| --- | --- |
| Final focused voice tests | **297 passed**; `voice-tests.log` |
| Broader non-GUI checkpoint | **1,640 passed, 3 skipped**; `non-gui-tests.log` |
| Native desktop cases | **66 passed** across isolated runs; `desktop-final-results.json` |
| Native macOS synthesis | Real English/Turkish system voices produced valid mono PCM16 WAV, 22,050 Hz, 3.015/3.097 seconds; `native-synthesis.json` |
| Python 3.10 grammar | **21 application modules passed**; `grammar.json` |
| Whitespace | `git diff --check` passed |

The final focused run includes the output-rate fallback, retained ownership until
native handles close, streamed-error cancellation, both participants' duplex
capability checks, and invalid audio configuration checks. The broader checkpoint
preceded those last audio-only refinements; their dedicated regressions are in
the final focused run.

Native desktop evidence combines the initial 64-case run with a corrected
placeholder-WAV fixture rerun and two new native cases for streamed speech and
device selection. The first device-picker test needed recursive traversal of
CustomTkinter's scroll-frame wrappers; its corrected rerun passed. Original logs
retain these test-fixture failures and successful reruns. The final JSON maps
every distinct case to its verified final exit code. Reproduce cleanly with:

```sh
.venv/bin/python docs/Evidence/Voice/20261101/verify-desktop.py
```

macOS requires WindowServer access. Running native Tk inside the restricted
sandbox aborts before widgets initialize; isolated tests ran outside it. System
synthesis inside that sandbox produced empty WAV output, which the decoder
correctly rejected. The recorded synthesis evidence comes from outside it.

Tests use injected capture/playback callbacks, HTTP and controllable workers.
They cover stable device reordering/removal/ambiguity, disconnect watchdogs,
overflow, cancellation, pre-roll/endpointing and Turkish/English synthetic speech
cadence, resampling duration/filtering, actual output-format decoding, output
controls, sentence queues, Markdown boundaries, generations, shutdown, and
text-only imports with optional audio and ML libraries blocked.

Real microphone quality, audible output, OS permission prompts, and Windows/Linux
hardware remain unverified. The supplied stack has no AEC and therefore uses
half-duplex capture/playback; simultaneous barge-in is unavailable for it.
No FIRIK TTS endpoint or ASR maximum duration is advertised by the checked-in
provider OpenAPI. Requests use a conservative, configurable client budget of at
most 30 seconds, sequential segments, a 120-second total and an eight-request cap.
The provider's actual maximum remains unverified; 30 seconds is not a claimed
provider limit. No live ASR request or microphone recording was made in this pass.
