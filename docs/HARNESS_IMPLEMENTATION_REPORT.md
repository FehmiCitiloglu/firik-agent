> Historical first-slice report retained below. The completed Phase 1 extension and current validation are documented in [HARNESS_PHASE1.md](HARNESS_PHASE1.md).

# Adaptive harness: first vertical slice implementation report

Validated 2026-10-27. Base `cf7af2fa5dd1ed1ab756376c5352b6fd2ec3b7f0`, package
0.2.21, uncommitted source fingerprint
`39199680f962259374dda401772e7f52266567467e238da0b6c0dbe37f52ae9c`.
This report covers audit + Phase A + a bounded safe-fallback correction. The
remaining adaptive-harness phases are **not implemented** by this change.

## Working code

| Component | Change / public interface |
| --- | --- |
| Native streaming runtime | `model_request_started`, cumulative `model_usage`, additive `model_request_finished`; separate request IDs for stream, eligible fallback and each summary; success/failure/cancellation, model/provider, client latency/TTFT, usage and cost provenance |
| Providers and compaction | Optional `Message.usage` preserves nonstream provider receipts while excluded from model-visible wire/history serialization; compaction exposes optional `on_request_event` callback |
| Shared measurement | `core.metrics.RunMetrics.observe(event)` / `snapshot()`; canonical token/cache aliases, sparse cumulative snapshots, nullable complete totals, reported subtotals/coverage, error/tool counts, bounded retention and explicit overflow; non-USD amounts are not relabeled as dollars |
| Durable coding / cockpit | Existing `usage.harness_metrics` JSON; live and passive historical projection, persisted aggregate preferred over truncated event replay; completion/model views show unknown totals and per-agent cards explicitly label reported subtotals |
| Benchmark Lab | Typed bounded `TaskManifest`, `builtin_tasks`, async `run_trial` / `run_suite`, `compare_reports`, `export_report`; real native loop, tools, authorization, verification gate and SQLite |
| CLI | `firik-agent benchmark` and `firik benchmark`: `list`, `run`, `compare`; early dispatch before credentials or live agent construction |

No new dependency or database migration. Configuration, old session/status
consumers and existing CLI routes are preserved. Existing numeric usage columns
remain compatibility counters; new nullable aggregates carry coverage evidence.

Fallback compatibility is intentionally narrowed: only an explicit local
pre-frame `NotImplementedError` permits the nonstream API. Connection/HTTP,
authentication, authorization, refusal, schema and unknown exceptions do not
implicitly replay a request. Cancellation is checked at the boundary. There is
no automatic provider switching or side-effectful tool replay.

## Actual validation

The pre-edit full suite aborted in native Tk construction, exit 134. The
pre-edit non-UI baseline was **526 passed, 1 skipped in 70.21 s**. The same native
Tk setup issue was reproduced during focused UI selection. No test was disabled
to hide it, and no packaged-desktop success is claimed.

Final broad command:

```sh
.venv/bin/python -m pytest -q $(rg --files tests -g 'test_*.py' -g '!test_ui*.py' | sort)
```

Result: **617 passed, 1 skipped in 53.05 s**. This includes **91 added cases**
across the five new metric/benchmark/dispatch/integration test files, plus the
existing native-runtime, SQLite/migration, permission, verification, context,
checkpoint and multi-agent regressions. The platform-specific existing skip
remains. Test-suite timing is not a controlled performance comparison.

Three explicitly selected pure tests in `test_ui_cockpit_evidence.py` also
passed. Native widgets remain unverified because of the Tk abort. All 127 package
Python modules parsed using Python 3.10 grammar; execution used CPython 3.13.2,
Darwin arm64 and SQLite 3.47.1. This is syntax evidence, not a Python 3.10 or
Windows/Linux runtime test. `git diff --check` passed.

Focused failure-injection coverage includes failed/partial/cancelled model and
summary requests, refusal/auth/HTTP no-replay, pre-fallback cancellation, sparse
token/cache alias replacement, stale cost invalidation, unknown metrics,
truncated history, overflow, deadline enforcement, token-budget lower bounds,
invalid/mutated manifests, traversal/credential paths, parser resource failures,
no fixture code/shell execution, exclusive exports and malformed/incomplete
comparison reports. Existing compaction/environment tests now model additive
events and the explicit unsupported-stream exception while retaining their
behavioral assertions.

## Benchmarks actually run

The CLI was exercised through both entry points with 7 tasks × 3 trials:

| Same-source control | Objective fixture checks | Completed verified tasks | Safety violations | Mean wall ms | Sample stdev ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| control-a (`firik-agent` route) | 21/21 | 15 | 0 | 91.072 | 25.799 |
| control-b (`firik` route) | 21/21 | 15 | 0 | 90.975 | 25.869 |

Expected stream failure and cancellation account for the other six passing
fixtures; they are not completed tasks. All usage values are synthetic replay
receipts; cost and successful tasks per dollar are unknown. The fixed source/AST
evaluator does not claim pytest/build/semantic correctness of fixture code.

The comparator accepted identical task/settings/source fingerprints and complete
paired trials. Paired wall-time delta (B−A): mean **−0.096 ms**, sample standard
deviation **1.948 ms**; no outcome delta. These are repeatability controls on the
same implementation. They do **not** show a speedup over the old harness, real
model quality, or live-provider billing. `performance_claim` is explicitly null.

[Evidence summary with metadata, raw artifact paths and SHA-256 receipts](Evidence/Harness/20261027/summary.json).
Each raw JSON report is about 491 KB and includes redacted event traces; its
Markdown companion and comparison JSON remain in the local artifact directory.
The evidence summary keeps the source patch compact.

## Security, upgrade and precise remaining work

Benchmark execution is explicit and offline. The trusted runner narrows the
native permission gate, uses temporary Git/SQLite state, disables provider
fallback and summary calls, denies shell/network/MCP/delegation, and only accepts
the literal `builtin:verify` for a fixed in-process source evaluator. Repository
Python is parsed as data. Git subprocesses use structured argv with hooks,
external config/templates/signing disabled. Logs/exports use existing redaction.
Temporary repositories are not represented as an OS sandbox.

Rollback preserves all databases/journals: revert the package/source and let old
clients ignore additive metrics/events. Opening history never replays tools or
resumes work. Pre-existing untracked `requirements.txt` was not edited. No commit,
push, PR, release, live-provider call or deployment was performed.

Still unfinished, in order:

1. Full typed Recovery Engine: failure categories, bounded Retry-After/backoff,
   budget/deadline decisions, explicit approved provider compatibility and
   reconciliation for uncertain side effects; additive verified/unverified task
   outcomes. The narrow fallback correction is not this full phase.
2. Incremental index pruning/skip logic, richer relevance/dependency/LSP evidence,
   dynamic schemas and the six adaptive model-aware profiles.
3. General durable tool-attempt journal, crash-injection reconciliation, safe
   workspace-bound manual resume and child-state reconciliation. Summary events
   currently flush after compaction returns/fails; a crash before that boundary
   can leave summary usage absent. Direct `Agent.run()` has no event metrics API.
4. Required/optional OS sandbox and worktree lifecycle, resource/network
   isolation, cross-process conflicts and explicit result merge review. Existing
   host shell and MCP execution are not sandboxed by this patch.
5. Official-interface/license verification for Pi/Codex adapters and matched
   native-versus-external benchmarks. No adapter was fabricated or enabled.
6. Native desktop/package, Python 3.10 runtime and cross-platform smoke tests;
   opt-in live-model comparative trials with genuine token/billing evidence.

See [initial audit](HARNESS_AUDIT.md), [ADR / rollback](ADR_HARNESS_OBSERVABILITY.md)
and [benchmark methodology](HARNESS_BENCHMARKS.md) for boundaries and reproduction.
