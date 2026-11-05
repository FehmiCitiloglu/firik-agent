# Voice assistant console validation — 5 November 2026

The console now exposes reviewed local application/task commands, exact existing
project/chat navigation with scoped voice continuation, opt-in hands-free
read-only queries, actual execution/approval progress, and independent input,
speech and task controls. Its vector orb uses real input levels and canonical
controller states; motion can be reduced and its timer stops on hide/close.
The header identifies the owning chat/project. Full command examples and
interaction details are in [voice.md](../../../../voice.md).

Final focused validation: **320 headless checks passed**, and **20 native cases
passed in separate macOS processes**. `validation.json` lists every native case
and the headless command; `native-tests.log` records their actual results.
`verify_native.py` reproduces the focused native group from this checkout,
writing its report, log and synthetic window captures under `/private/tmp`.
Ten changed production modules also parse with Python 3.10 grammar.

The native cases cover capture → reviewed/automatic local command → actual
selected-session controls, pause/resume/cancel without an extra agent run,
project voice handoff, cancellation of stale handoffs, rejected queued-ASR
scope rebinding, early window close, long filtered answers, coding progress
without raw-stream narration, actual approval waits, both themes, small
windows, motion reduction and animation cleanup. Audio devices, ASR and TTS
are injected or mocked. No live provider inference or physical audio test was
performed; native Windows/Linux execution and packaging were not exercised.

A preceding aggregate capture process stopped at a Settings light-tab pixel
check (104 passed / 1 failed): mapped settings content appeared blank after
many Tk interpreter lifecycles. The same screenshot check passed in a fresh
process, as did its dark variant. Final verification follows the existing
`scripts/test_desktop.py` policy of isolating native window lifecycles; a green
aggregate capture run is not claimed.

The existing reviewed-send bug fix remains covered. No commit, release or
installation is part of this validation.
