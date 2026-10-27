# Phase 1 implementation and verification

Validated on 2026-10-27 in the existing Firik Agent checkout. This extends the
pre-existing uncommitted observability/benchmark slice; it preserves the user's
other changes. No commit, push, installation or model API evaluation was made.

## Delivered

- Modular strict contracts, inert fixture catalogs, scripted providers,
  host-controlled evaluators, runner, analytics, paired comparison, reporting,
  bounded storage and opt-in live orchestration under `firik_agent/benchmarks/`.
- Twenty objective offline fixtures through the actual native streaming loop,
  guarded tools, run/session IDs, ordered event persistence and shared metrics.
- Forty explicitly curated small canonical-source coding tasks across six
  categories, each with a deterministic immutable Git starting revision and
  an independent source/AST oracle. No fabricated live evaluation results.
- `firik harness doctor/bench/compare/list` and `firik-agent harness` alongside
  compatible legacy benchmark commands. JSON/Markdown results, failure and
  regression breakdown, side-by-side efficiency/success and provenance.
- Read-only benchmark summary import in the existing coding cockpit.
- Native provider parsers reject incomplete tool calls/truncated SSE instead of
  treating a dropped operation as completion, and emit a completed call once.
- Shared `RunMetrics` retains cumulative per-request deduplication and adds a
  per-request known-token lower bound. Mixed sparse reports across requests
  cannot silently evade the live wrapper's reported-token limit.

The existing database JSON fields and redaction infrastructure are reused.
No additional database schema or incompatible execution event stream was added.

## Actual validation

Final complete suite, including native macOS GUI tests:

```sh
.venv/bin/python -m pytest -q
```

**765 passed, 1 skipped in 319.67 seconds.** The command ran with normal macOS
GUI-session access. No failing test remains. The existing platform-specific
skip is unchanged. A focused run over the benchmark/native accounting,
request, CLI, SQLite and metrics-integration tests also passed **159 tests**;
a final analytics/accounting/live subset passed **42 tests**.

A normal sandboxed all-tests attempt initially aborted in macOS native Tk
construction. With normal macOS GUI-session access, tests execute. The first
complete GUI-enabled run reported 758 passed, one skipped and one failure: an
existing keyboard test sent Control on macOS while the application binds
Command. The test now uses the application's actual platform modifier; the
14 cockpit/evidence tests passed after that correction. The application shortcut
behavior was preserved.

Additional focused coverage verifies deterministic replay/revisions, native
streaming usage deduplication, sparse/unknown tokens and cost, strict contracts,
invalid/corrupted suites/results, cancellation/deadlines, concurrent roots,
host-controlled evaluators, guarded permissions, secret redaction, paired A/B
controls, bootstrap/report correctness, CLI live opt-in, successful injected
read/edit/verify/complete flow and old SQLite history compatibility.

All 137 package Python modules parse with Python 3.10 grammar. Execution uses the
local CPython 3.13 macOS environment; this is not Windows/Linux or Python 3.10
runtime coverage. `git diff --check` passes.

## Benchmark evidence

The final implementation was run twice over 20 offline tasks with three repeats:
60/60 objective fixture checks passed in each control. These same-source runs
establish repeatability only. Token counts are synthetic; cost remains unknown;
`performance_claim` is null. Expected safe failures are separate from successful
coding completions.

A deliberate request-policy regression was then run over the same task dataset,
model, permissions, token/time/output budgets and evaluator: the baseline's
16-request policy passed 60/60 checks, while a one-request candidate passed
30/60. The paired comparator identified the ten affected tasks and 24 fewer
completed verified tasks. This demonstrates detection of a failing harness
policy without asserting a ranking over real models.

[Machine-readable receipts, summaries and raw artifact SHA-256 values](Evidence/Harness/20261027/phase1/receipts.json).
[Versioned 40-task suite manifest](Evidence/Harness/20261027/phase1/coding-suite.json).
Raw JSON/Markdown reports are retained in the local paths recorded in the
receipts; they include redacted native traces and validated individual records.

## Limits and next phases

Exact canonical source/AST evaluation is objective but does not establish
semantic correctness, executable build/test success or production-repository
coverage. It can reject equivalent solutions. Fixture code is never executed;
there is no claim of an OS sandbox. A real sandbox or explicit execution
boundary is needed before admitting arbitrary repository commands.

Live mode is implemented and tested with injected providers, including a fully
verified edit. It was not run against a paid provider. Only explicit
`--allow-live` enables configured model traffic. Unknown usage stays null;
request/output/time bounds are hard limits, while reported-token bounds cannot
reverse usage already billed or impose an exact monetary cap.

The lab disables compaction, fallback, arbitrary commands, MCP and subagents.
Future recovery, context and routing changes should freeze these suite/control
fingerprints, compare repeated paired trials, investigate per-task regressions,
and require evidence of safety, verified success and efficiency improvements.
The recovery/adaptation policy itself remains a later phase.

See [usage, contracts, metrics and interpretation](HARNESS_BENCHMARKS.md).
