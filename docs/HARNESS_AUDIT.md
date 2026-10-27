> Phase 1 follow-up: the lab gaps identified in this initial audit are implemented and validated in [HARNESS_PHASE1.md](HARNESS_PHASE1.md). Later recovery/adaptation phases remain outside this change.

# Adaptive harness audit — 2026-10-26

Base checkout: `cf7af2fa5dd1ed1ab756376c5352b6fd2ec3b7f0` (`main`), remote
`https://github.com/FehmiCitiloglu/firik-agent.git`. The only pre-existing working
tree change was untracked `requirements.txt`; it is outside this implementation.
No reset, checkout, cleanup, push or database conversion was performed.

## Baseline evidence

- `.venv/bin/python -m pytest -q`: progressed beyond 81%, then process exit 134
  (`Fatal Python error: Aborted`) while constructing Tk in
  `tests/test_ui.py::test_app_title_is_firik_agent`. This happened before edits.
- `.venv/bin/python -m pytest -q $(rg --files tests -g 'test_*.py' -g '!test_ui*.py' | sort)`:
  **526 passed, 1 skipped, 70.21 s**, before implementation.
- Local interpreter: CPython 3.13 on macOS arm64. Python 3.10 remains the supported
  minimum; this run is not evidence of execution on every supported platform.

## Existing execution path and boundaries

The audit covered README, pyproject, config, core agent/runtime/events/compaction/
session/tools, provider base/registry/OpenAI and FIRIK implementations, coding
runtime/service/sessions, quality gates, context builder/inspector/indexer/model
capabilities, subagents/specialists, checkpoints, cockpit, SQLite, MCP, desktop
bridge/evidence/history/actions, tests and the three existing coding/context docs.

`FirikService -> CodingBridge._coding_stream_async -> Agent.run_stream -> provider`
uses a run-local `CodingRunControl` and `CodingToolFilter`. The filter, rather than
model prose, authorizes tools. The bridge redacts and records events before UI
dispatch and checks the completion gate before publishing completion. The older
nonstream `Agent.run` is a separate compatibility surface.

| Area | Reuse / current implementation | Gap / consequence |
| --- | --- | --- |
| Context | Weighted budgets, bounded lexical symbol/content retrieval, direct Python import expansion, pins/exclusions, provenance, archive-preserving compaction | No semantic/LSP retrieval; reindex rereads unchanged files and does not prune deleted entries |
| Verification | Real command exit status, workspace digest/revision, stale-evidence rejection, required gates and bounded repeated tool failures | Plain persisted `completed` does not distinguish verified/unverified task outcomes |
| Persistence | Additive retryable coding schema migration v1, serialized transactions, project/session/run identities, redacted events and snapshots | No general durable tool-attempt intent/outcome reconciliation; child jobs remain in memory |
| Recovery | Partial streams are not replayed; cancellation checkpoints; passive interrupted-run recovery | Pre-first-frame stream failure historically invokes nonstream fallback without typed classification, Retry-After or charge reconciliation |
| Parallelism | Parent live gate, narrowed role/tools/write scopes, shared cancellation and process-local overlapping file leases | Shared checkout/host process, no OS sandbox or cross-process lease; worktrees alone would not provide one |
| MCP | Structured subprocess argv, explicit client lifecycle, tool gating | Host environment/network/filesystem trusted; timeout does not establish absence of side effects; initialization failure can leave local client cleanup incomplete |
| Paths | Credential exclusions, symlink/hardlink/traversal rejection, descriptor anchoring, hash-checked edits/undo/restore | TOCTOU/FIFO blocking needs separate hardening; not an OS filesystem isolation boundary |
| Metrics | Existing stream usage frames, per-iteration replacement, successful latency/TTFT, optional pricing evidence | Summary/nonstream usage lost; failed requests lack final metrics; partial known usage misleadingly sums as full totals; multiple UI aggregation implementations |
| Desktop/CLI | Event cockpit, passive historical restore, native approval/checkpoint/test UI, early credentials-free CLI dispatch | New observability must use these seams, not a new application or schema |

## First vertical slice decision

Implement Phase A through the real native path: request identities and passive
accounting, durable aggregate snapshots, desktop evidence, and an offline replay
lab with objective bounded evaluators. Preserve existing session status enums,
CLI routes, permissions, configuration and full conversation history. Use the
existing JSON usage field, so no migration is required. Add no dependencies.

This slice measures overhead and behavior under deterministic provider replay.
It cannot establish real-model quality, provider billing or successful tasks per
dollar. It precedes adaptive recovery, context changes, resume and sandboxes.

## Subsequent work, in required order

1. Typed failure/recovery decisions, safe explicit fallback eligibility, deadlines,
   idempotency/reconciliation and additive evidence-based completion outcomes.
2. Profile-driven retrieval/schema budgeting, incremental index invalidation and
   optional LSP with evidence-backed model capability caching.
3. Tool attempt journal, crash/fault-injection reconciliation, workspace-bound
   manual resume and child state reconciliation.
4. Optional required/available sandbox policy, Docker capability detection,
   worktree lifecycle and explicit merge conflict review. No host fallback when
   a required sandbox is unavailable.
5. Verify official interfaces and licenses before implementing external harness
   adapters; keep incompatible enforcement modes disabled.
6. Cross-platform packaged desktop validation and real-model opt-in comparative
   trials. No performance improvement is claimed without matched evidence.
