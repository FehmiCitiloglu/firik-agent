# Self-improvement validation — 2026-11-05

Environment: macOS arm64, Python 3.13.2. Tests use temporary data and stubbed OS
credential storage. No paid model requests, dependency installs, image pulls,
commits, pushes or releases were performed. Existing unrelated working-tree changes
were preserved. Docker Desktop was already installed; starting it was attempted,
but the selected local daemon remained unavailable. Candidate execution remained
blocked, with no host fallback.

## Executed results

| Check | Actual result |
| --- | --- |
| Complete suite excluding `tests/test_ui*.py` | **2210 passed, 4 skipped** in 187.73 s |
| Selected native desktop/learning/chat/context UI suites | **70 passed** in 25.84 s |
| Native app-title/view-registration tests after temporary-home isolation | **2 passed** in 1.31 s (overlap with native group; not added to its total) |
| Native voice-interruption test in its own process | **1 passed** in 1.51 s |
| Native learning dashboard fixture smoke | Passed, real CustomTkinter; status, evidence, exact revision and diff; no mutation submitted |
| Real CLI across separate processes | Stage → evaluate → approve → restart/recall → pin → forget passed |
| Real offline coordinator paired retrieval fixture | Baseline 2/5; candidate 5/5; no paired regression; explicit promotion and rollback restored budget 200 |
| Wheel build in temporary source copy | Passed; learning modules, dashboard and benchmark module present with byte-identical current source |
| `compileall` and `git diff --check` | Passed |

The four headless skips are actual Docker integration (opt-in), live Evren audio
(opt-in), Windows-only workspace protection, and Windows `cmd` shell quoting.
See [machine-readable results](validation.json).

The single-process aggregate native run **did not complete**: it exited 138 with
a Tk bus error during
`test_ui_voice.py::test_running_agent_events_do_not_discard_new_spoken_interruption`.
It had also emitted a failure before that fatal abort, so no aggregate native
pass is claimed. The same voice test passed in a fresh process, as did the selected
70-test native group. This leaves aggregate Tk/voice process stability unresolved;
the implementation does not attribute the cause without further evidence.

## Reproduction

```sh
.venv/bin/python -m pytest -q --ignore-glob='tests/test_ui*.py'
.venv/bin/python -m pytest tests/test_ui.py tests/test_ui_improvement.py tests/test_ui_chat.py tests/test_ui_chat_workspace.py tests/test_ui_coding_history.py tests/test_ui_cockpit_explorer.py tests/test_ui_model_selection.py tests/test_ui_reasoning_effort.py tests/test_ui_git.py -q
.venv/bin/python scripts/smoke_improvement_ui.py
.venv/bin/python -m firik_agent.benchmarks.learning --output /tmp/learning-retrieval.json
.venv/bin/python -m compileall -q firik_agent/self_improvement
git diff --check
```

Native checks require a working desktop/Tcl environment; the smoke reuses the
application's existing Tk initialization. The Docker prerequisite can be inspected
without executing candidate code using `firik-agent sandbox doctor`.

## Acceptance coverage

`tests/test_self_improvement.py` covers evidence trust, duplicate processing,
approval/rejection/stale revisions, correction and rollback, forgetting historical
copies, private/project scope, default and bounded retrieval, unknown measurements,
OFF/cancellation/periodic review, migration/restart, recurring verified workflows,
later real project-skill selection, live feedback provenance, paired experiment
promotion/staleness/regression, and cross-coordinator candidate leases/capacity.

`test_improvement_candidates.py` and `test_improvement_generation.py` exercise real
temporary repositories, existing Agent requests through a fake provider, zero-tool
generation, forbidden tool attempts, policy/evaluator paths, stale source, skills,
authenticated receipts and isolation refusal. Fake test receipts establish lifecycle
boundaries only; they are not evidence of real Docker quality or production gains.

`test_improvement_cli.py`, `test_improvement_integration.py` and
`test_ui_improvement.py` cover actual command routing, offline construction,
configured storage, permission-safe runtime hooks, main-thread dispatch and GUI
decision revision binding. Existing text/coding/harness/skill/voice tests are
included in the broader run.

The [reference comparison](learning-retrieval-v1.json) is a five-case deterministic
fixture using real service/storage paths. It demonstrates recall at different
budgets; it is not evidence of general answer quality, lower paid usage, an
autonomous production improvement or live-source promotion. Full Linux/Windows
runtime, power-loss, live provider and Docker execution validation remain open.

An independent source-backed threat model was saved through Codex Security's
managed artifact store. The repository's [feature guide](../../SELF_IMPROVEMENT.md)
documents its enforcing boundaries, residual limitations and capability matrix.
