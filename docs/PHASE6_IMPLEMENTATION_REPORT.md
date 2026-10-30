# Phase 6 — implementation and cross-phase review

Implemented on 29 October 2026 in `/Users/alien/Development/evren`, on the existing
`main` working tree. The verified base commit was
`aa0a5ea6eb469f818dabd20d42674e469211f62f`. Pre-existing Phase 4/5, installer and
other local changes were preserved. No commit, push, credential-store lookup,
login, runtime installation or paid inference was performed for this phase.

## Delivered behavior

1. Typed backend-neutral lifecycle/session/task/policy/event/tool/usage/error/
   completion records and a `HarnessBackend` protocol.
2. Working native adapter over the original agent loop, with original events,
   provider selection, permissions, MCP, context/recovery, subagents, durable
   journal, checkpoints and quality gates retained. Streaming CLI/desktop flows
   pass through the adapter; the existing non-streaming loop remains compatible
   and rejects incompatible selection before dispatch.
3. Working official Pi 1.4.0 JSONL RPC adapter for explicitly authorized
   **tool-free prompts only**. Clean environment/private cwd/home/config,
   supported explicit provider keys, cancellation, usage, normalized diagnostics,
   bounded protocol handling and process disposal are implemented and tested.
4. Explicit unavailable Codex adapter. Official supported interfaces were
   researched, but a complete filesystem/configuration/approval mapping was not
   verified. It never substitutes unrestricted execution or fabricated results.
5. Backward-compatible native-default harness configuration, per-project
   preferences, disabled external defaults, no automatic switching, and honest
   installed/enabled/supported distinctions. Disabled external executables are
   never launched by passive inventory; their version stays unprobed.
6. CLI `harness settings/backends/experiment/recommend/prompt` and a native
   desktop **Harness / Benchmark** dialog with backend details, execution trace,
   validated report analysis, explicit isolated experiments and cancellation.
7. Comparative runner using the existing Lab's pinned Git fixtures and host-held
   source/AST evaluator, fresh repositories per candidate, checked observed
   identities, nullable accounting, failed-row evidence and conservative cleanup.
8. Advisory evidence-based recommendations with success/cost/latency/balanced/
   local-only objectives. Insufficient, synthetic, stale or mismatched evidence
   never becomes a production ranking or automatic switch.
9. Security/interface analysis, architecture/migration notes, user documentation,
   offline fake-process integration tests and retained benchmark/test receipts.

Guides: [architecture](PLUGGABLE_HARNESSES.md), [desktop/CLI](HARNESS_DESKTOP.md),
[official interface research](HARNESS_BACKEND_RESEARCH.md),
[comparison methodology](HARNESS_COMPARISON.md), [security](HARNESS_SECURITY.md).

## Actual validation

The final immutable-source non-GUI run passed **1,255 tests with 2 skipped**
in 114.73 seconds. Its receipt and source binding are recorded in
`Evidence/Harness/20261029/phase6/validation/validation-summary.json`.
The preceding broad run passed 1,254 tests with 2 skipped; the final
harness-focused suite passed **106/106**. These suites overlap and their counts
must not be added. The two skips are the opt-in real Docker integration and a
Windows-only command quoting test on macOS.

Native desktop regression completed **95/95 tests across all 15 `test_ui*.py`
files**, using a separate native macOS process per file to isolate Tk lifecycle.
One initial history-file timeout was diagnosed as missing Tcl/Tk initialization
in its test fixture. Only the fixture was corrected; all five history tests then
passed. Initial observations and final receipts are retained, rather than
discarding the failed attempt. The new Harness view was exercised in a real Tk
process; this is not a live provider or Windows assertion.

Compilation and `git diff --check` passed. Passive CLI discovery/settings and
the full comparison report's CLI/desktop analysis path were also checked.
The report is about 12.2 MB, so its bounded reader now uses the existing Lab's
64 MiB report limit while configuration remains bounded to 8 MiB. This prevents
the desktop from rejecting its own full-suite report.

Receipts: [validation directory](Evidence/Harness/20261029/phase6/validation/),
[GUI aggregate](Evidence/Harness/20261029/phase6/validation/gui-summary.json),
[report reopening](Evidence/Harness/20261029/phase6/validation/firik-phase6-reopen.json).

## Reproducible benchmark evidence

The final frozen-source run executed **40 tasks × 3 repetitions = 120/120**
verified exact-source transformations through the native adapter. All 600 tool
calls completed without observed tool errors, false completions or terminal
diagnostics. Reported synthetic counts were 14,400 input and 2,880 output tokens;
cost and cache usage remain unknown. This is canonical scripted replay, not
evidence of a real coding model's ability or live provider cost/performance.

The same source also ran 25 samples each of the original current native loop and
its lifecycle wrapper, with alternating pair order and identical fixture/model/
evaluator controls. Both completed 25/25 with independently verified source:

| Current runtime path | Median | p95 |
| --- | ---: | ---: |
| Original native loop | 20.662 ms | 21.574 ms |
| Native lifecycle adapter | 23.911 ms | 24.582 ms |

The median paired wrapper difference was **+3.341 ms** in this local sample.
This measures added lifecycle/event accounting work, with fixture construction
and the final independent evaluator excluded equally. Complete comparison
trials, including fixture/cleanup work, averaged 83.298 ms. Short local filesystem
measurements are noisy and do not establish production latency or a universal
speedup. No additional model call or delegation was introduced by the wrapper.

Both reports recorded `source_changed: false`. Reopening the full report through
the same desktop analysis loader succeeded and verified the current source hash.
The recommendation remained null, correctly refusing to rank production models
from synthetic replay. Automatic selection remained false.

Raw evidence: [comparison](Evidence/Harness/20261029/phase6/comparison.json),
[overhead](Evidence/Harness/20261029/phase6/overhead.json),
[readable timing summary](Evidence/Harness/20261029/phase6/overhead.md).

Reproduce with:

```sh
.venv/bin/python scripts/benchmark_harness_backends.py \
  --output /tmp/firik-phase6-new-evidence \
  --tasks 40 --repeat 3 --overhead-tasks 5 --overhead-repeat 5
```

Historical recovery-only/context-only ablations were **not** fabricated from the
current runtime. No immutable matched historical environment was supplied.
Pi/Codex coding comparisons were not executed: the supported Pi mode cannot
admit a workspace/tools and Codex execution is unavailable. No paid backend
ranking or live-provider result is claimed.

## Evidence-based cross-phase findings

| Integration | Review and verification |
| --- | --- |
| Recovery, durable intents and streaming | Crash exceptions propagate unchanged; unknown operations are not replayed; original journal ownership stays intact. Durable/recovery/boundary regressions passed. |
| Permission updates and event streaming | Every native event is acknowledged before another dispatch. Live revocation tests exposed and fixed speculative continuation. |
| Context/profile/token budgets | Existing profile/context bounds remain in the original loop. Unsupported hard pre-billing caps fail explicitly; nullable usage is preserved. |
| Subagents, sandbox and worktrees | Existing inherited scope, isolated worktree contracts, reviewed integration and post-integration evidence rules are retained. Whole-runtime native sandbox promises are rejected. |
| Completion and verification | Native DONE is separate from verified artifacts. Existing fresh quality/evidence gates remain authoritative; independent comparison evaluation checks actual source. |
| Metrics and duplicate events | Existing native request reducer remains the accounting owner. External final-message usage is deduplicated. Tool failures, terminal errors and recovery-attempt rates have explicit separate formulas. |
| Cancellation, persistence and teardown | Finished runs are not marked cancelled on close. Repeated cancellation shares shielded cleanup, preserves final request accounting and cannot release a live native session early. |
| External authority and credentials | No import-time dotenv read during passive discovery; native authorized setup retains legacy dotenv. Disabled executable probes and replay enablement bypasses were removed. Pi imports only explicitly handed-over credentials. |
| Comparative identity and report integrity | Actual backend/session/run, model/revision and provider sets are checked, including same-model cross-provider fallback; bounded traces and recomputed summaries reject inconsistent evidence. |
| Desktop integration | Native rendering/settings/report/trace/experiments coexist with existing cockpit flows; callbacks cross the existing GUI queue and close propagates cancellation. |

The first wide run found six native wrapper regressions, which were fixed and
covered by focused and repeated broad validation. Independent review found
additional lifecycle, policy, identity and passive-discovery defects; those were
fixed before final measurement. The source/runtime was not duplicated to work
around these failures.

## Remaining limitations

Native host tools and remote MCP are not OS confinement; selected Docker command
isolation does not cover the entire agent. Pi tool-free execution trusts the
installed host runtime and explicitly supplied prompt/provider key. Process-group
cleanup does not contain deliberate escapes, and power-loss/SIGKILL persistent
orphan recovery is unverified. Windows external execution is disabled. Existing
Phase 5 real-container/platform coverage gaps remain; no new live-daemon or
real-provider assertion is made.

External coding permissions, Codex execution, external durable resume, active
backend state transfer, automatic switching and competing artifact integration
remain unavailable. These operations fail clearly. Live native comparison
factories need explicit host binding and authorization; a JSON plan alone cannot
construct a provider or import credentials. Tool-free Pi has a working explicit
CLI entrypoint but cannot satisfy coding-comparison policy.

The optimizer cannot establish a production best configuration from the supplied
offline data. Unknown charges remain unknown. Recovery/context historical
ablations, cross-platform process enforcement and live provider compatibility
need separate authorized evidence. These limitations are operationally visible
and do not change the default native desktop or CLI behavior.
