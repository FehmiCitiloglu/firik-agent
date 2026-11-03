# Native voice interface — 3 November 2026

This checkpoint extends the existing CustomTkinter chat and coding composers.
It adds a keyboard-accessible microphone, a compact transcript review, an optional
voice-session window, and a shared **Ayarlar → Ses** settings form. These controls
use the existing sessions, controller, service, runtime, approval UI and tool views.

The session window keeps microphone mute, stop listening, stop speaking and exit
visible in a small window. Its configured push-to-talk shortcut works while the
window is focused. Finalized speech can be edited, discarded or submitted through
the existing chat/coding path; unsupported live ASR text is explicitly explained.
Settings scroll content is restacked after a tab remaps, correcting a macOS Tk
repaint failure while preserving the existing controls and edited values. The
capture tests inspect a visible menu pixel as well as geometry to catch blank
content with otherwise valid widget bounds.

Settings include stable input/output device selection, a bounded explicit local
level test, language, independent speech models, VAD controls, playback controls,
privacy explanations and offline diagnostics. Weak service listeners apply saved
preferences without restarting. Concurrent settings forms retain edited drafts
and merge changed fields into the latest preferences.

Automatic speech requires the master voice option, response-reading opt-in and an
eligible foreground context. Losing that context cancels active and queued
automatic speech. Manual read/replay replaces the old queue. None of those actions
cancels an agent task. The optional coding completion chime contains no response
text and defaults to off.

## Verification

macOS, Python 3.13.2, real Tk windows, one foreground test process at a time.

| Check | Observed result |
| --- | --- |
| Broad non-GUI suite | 1,999 passed, 3 skipped; `non-gui-tests.log` |
| Native regression sweep and focused follow-up | 121 distinct cases passed; `desktop-final-results.json` |
| Python 3.10 syntax compatibility | All changed Python modules/scripts parsed successfully |
| Visual checks | Light/dark composer, session and settings captures; runtime theme switching and compact-window bounds tested |

`desktop-regressions.log` records the 118-case sweep. Its one outdated checkbox
label selector was corrected and passed in `desktop-followup.log`. That follow-up
also verifies the final interaction preference, manual queue replacement and
offscreen keyboard focus. `settings-capture.log` verifies the final native-window
capture helper and refreshes the settings images. `settings-render-regressions.log`
checks the remap repair with theme, focus and embedded-settings cases. The final
JSON uses each case's latest result across those four logs. Earlier checkpoints and
failed initial runs remain preserved with their original results.

The native fixtures use real application/service/controller/session paths with
synthetic projects, microphone frames, recognition and speaker I/O. No real audio
is recorded or sent and no privileged tool is executed. The final capture helper
uses macOS window IDs owned by the test process and refuses desktop capture when
the target cannot be uniquely identified. These checks do not establish microphone
accuracy, real acoustic echo handling, Windows/Linux hardware behavior, remote
provider uptime, or screen-reader compatibility.

## Reproduce

From the checkout with the existing development environment:

```sh
.venv/bin/python docs/Evidence/Voice/20261103/UI/verify-desktop.py
```

The runner collects the current affected native tests, runs each in its own Tk
process, and records screenshots when supported by the fixture. It supports
focused node IDs and `--log-name` for subsequent checks. Run it in a logged-in
desktop session without another foreground test runner.

The non-GUI suite can be reproduced without microphone or provider access:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import subprocess, sys
tests = sorted(str(path) for path in Path('tests').glob('test_*.py')
               if not path.name.startswith('test_ui_'))
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', *tests]))
PY
```

See [the voice guide](../../../../voice.md) for capability and dependency details.
