# Durable execution and safe resume (Phase 4)

Firik Agent records coding execution intents and outcomes in the existing Projects
SQLite database. Opening history or restarting the desktop does not dispatch a
model, tool, shell command, MCP call, patch or child task. An interrupted dispatch
is an **unknown outcome** until inspected; it is not evidence that the operation
failed or never ran.

This is local crash-consistent bookkeeping and conservative continuation. It does
not provide distributed exactly-once effects, a general transaction across SQLite
and external services, or automatic reconnection to an interrupted child process.

## Existing persistence audit

| Owning source | Existing mechanism retained | Phase 4 integration |
| --- | --- | --- |
| `projects/db.py` | One Projects database; `coding_sessions.id == run_id`; stable conversation `session_id`; project/task scoping; additive v1 columns and ordered `coding_run_events`; WAL, busy timeout, per-instance lock and transactions. | Additive v2 step, owner and resume-claim tables in the same migration transaction. Connections explicitly use `synchronous=FULL`. |
| `projects/coding_sessions.py` | Creates identity-scoped runs, preserves historical rows, sanitizes messages/events/file journals, rejects owner identity changes, persists checkpoints/decisions/test results and marks active runs interrupted. Individual mutations use `BEGIN IMMEDIATE`. | Explicit executor ownership, live-owner protection, orphaned journal recovery and repeated startup handling. Construction remains passive. |
| `projects/checkpoints.py` | Local bounded source snapshots, run/project/root checks, content hashes, reviewed diffs/hunks, pre-restore backup and restore-operation journal. Restore compares expected hashes and rolls back only its own bytes. | Resume inspects existing checkpoints and unfinished restore operations. Recovery never invokes destructive Git reset/clean or automatic snapshot restore. |
| `projects/coding_runtime.py` | Live policy checks and per-operation approval, conditional file writes, guarded paths, completed plan steps, tool outcome classification, workspace revision and stale verification detection. | `CodingRunControl` binds a journal to the durable run; intent/outcome wrappers fail closed. Agent-driven and manual desktop tools share the intent boundary. File publication flushes file/directory state where supported. |
| `projects/subagents.py` | Stable child identities, parent-scoped permissions/budgets, bounded concurrency, child sessions, dependency/result/artifact lifecycle. | Child creation intent precedes task creation. Child requests/tools reference the child parent step; child outcome and delivery are recorded separately. Interrupted children require reconciliation when reconnection is unavailable. |
| `projects/cockpit.py` | Passive event projection and persisted run summaries; history restoration does not execute actions. | Recovery views use the same run/events plus journal records, rather than reconstructing execution from transient UI objects. Historical absence of evidence remains absence. |
| `core/agent.py` | Shared model recovery loop for streaming/nonstreaming, unique attempt accounting, tool attempt fingerprints and bounded recovery, completion gating. | Durable model/tool/completion records surround dispatch and terminal persistence. Completed logical identities cannot silently acquire another attempt. Model projection gaps are exposed for reconciliation. |
| `core/runtime.py` | One background asyncio loop for desktop tasks/MCP clients; shutdown cooperatively cancels in-memory tasks. | Retained as an execution host, not treated as durable state. Restart safety comes from SQLite; Python futures/tasks are not restored. |

Paths in the table are relative to `firik_agent/`. Existing chat history alone
never establishes that a side effect completed. The run/session identifiers,
execution journal, source manifest and checkpoint evidence are considered together.

## Execution state and transaction guarantees

Each journal attempt identifies its run/session/project/task, logical step,
unique attempt, optional parent step and agent, operation, input SHA-256,
side-effect classification, permission snapshot, workspace revision, timestamps,
retry classification, bounded result/reference, error and recovery metadata.

The persisted states are `created`, `planned`, `ready`, `dispatching`, `running`,
`completed`, `failed`, `cancelled`, `outcome_unknown`, `needs_reconciliation`.
`begin()` writes a `ready` intent. Dispatch normally enters `dispatching` and then
`running`; a local decision such as an approval may enter `running` directly from
the already committed intent. Allowed transitions are checked in code. Unknown
states and uninterpretable durable JSON block recovery.

A state change and its ordered event are committed in one SQLite transaction.
Repeated exact intent identities and duplicate identical terminal outcomes are
idempotent. Rebinding an attempt to a different input/owner, replacing a terminal
result, or creating a new attempt for a completed/unfinished logical step fails.
A previously failed/cancelled logical attempt may receive a new explicit attempt;
the journal itself never initiates a retry.

`BEGIN IMMEDIATE`, SQLite WAL/foreign keys/busy timeout, and the existing database
lock serialize writes within and across store instances. Exceptions before commit
roll back the state and event together. A committed dispatch without a terminal
result becomes `outcome_unknown` on recovery. Only `reconcile()` can resolve an
unknown result into a terminal outcome, and inspection evidence is required.

`FULL` synchronization asks SQLite/filesystem to durably flush commits. The test
suite exercises process death and rollback; it does not emulate power loss,
controller failure, broken storage firmware, SQLite corruption repair or network
filesystem semantics.

## Crash boundaries

| Interruption boundary | Durably known / unknown | Safe inspection and continuation rule |
| --- | --- | --- |
| Before model request | No intent, or a committed `ready`/`dispatching` intent. A dispatch marker alone cannot prove provider acceptance. | Inspect journal/session. Restart submits nothing. An explicit continuation may make a new authorized request only after blockers are resolved. |
| During model request | Request attempt and paid/external classification; usage/output may be partial. Provider acceptance/billing can be unknown. | Mark an unconfirmed dispatch unknown. Inspect provider evidence; do not assume a timeout means no request ran. |
| Model output received before persistence | Process memory may have output while SQLite still shows running; or terminal journal exists but conversation projection is missing. | Unknown dispatch or unconfirmed model projection blocks resume. Review/acknowledge the gap explicitly; do not silently regenerate output. |
| Before tool dispatch | Committed ready intent, input fingerprint, permissions and revision. | Undispatched work is distinguishable from completed work. Continuation still requires explicit action and live policy checks. |
| Tool dispatched before result | Tool identity/arguments summary and dispatch state; external result is not confirmed. | Mark unknown, inspect files or external receipts, and record evidence before continuation. |
| During filesystem mutation | Tool intent and pre-change checkpoint when available; file may contain old/new bytes or a publication may be interrupted. | Compare current hashes and checkpoint; never blindly reapply a patch. Preserve unrelated user changes. |
| During Git operation | Shell/tool intent, policy and workspace/Git manifest; Git may already have mutated state. | Read-only Git inspection is permitted. No automatic commit/push/reset/clean replay. |
| During quality gate | Verification tool intent; an incomplete command is not passing evidence. | Surface pending/stale verification. Re-running a command is a new authorized operation. |
| During checkpoint creation/restore | Snapshot transaction may be absent/committed; restore has a durable operation record and pre-restore backup. | Missing checkpoints and unfinished restore operations block continuation. Inspect comparison/backup before an explicit restore; do not infer all-file atomicity. |
| During child execution | Parent/child stable identities and step relationships; child requests/tools may have their own unknown outcomes. | Do not recreate the old child on open. Expose unknown child state and retained outcomes; require reconciliation if reconnection is unavailable. |
| Waiting for approval | Pending approval intent, not a reusable permission grant. | History remains pending/requires fresh per-operation approval. A prior decision grants only its original live operation. |
| Writing completion metadata | Completion inspection intent plus previously durable tool/model outcomes; final summary may be missing. | Mark incomplete completion work honestly, re-inspect evidence after approval, and never infer verified completion from a missing summary. |

All inspection in the resume controller is passive except read-only filesystem/Git
observations. A user reconciliation assertion is recorded as evidence; Firik does
not independently prove an external receipt merely because text was supplied.

## Resume and workspace consistency

`DurableResumeController` loads the existing run, validates project/session
identity, checks executor ownership, loads passive history, inspects
journal uncertainty, child outcomes, model projection gaps, checkpoints and
verification, and compares the saved workspace manifest with the current one.

The manifest records resolved root, file hashes/modes/sizes and Git HEAD/branch,
index/status hashes. Dirty and untracked files are included. It detects changed,
missing and added files within the scan scope. Captures are bounded to 2,000 files
and 20 MiB by default; an incomplete, unreadable or racing capture blocks resume.
Dependency/build directories excluded by the existing indexer remain outside this
manifest. Unsupported filesystem entries also block a complete capture.

Workspace changes require explicit evidence/approval before accepting a new
baseline. Missing/corrupt checkpoints, corrupt journals, root identity changes
and unfinished restore operations require repair/inspection rather than an
automatic override. Verification from an older workspace is not promoted to a
fresh passing result.

Preparation creates one `queued` continuation in the same session, atomically
claimed by source run. Repeating preparation returns the same unused continuation
and can only narrow its permissions. A consumed continuation is not started again.
The desktop performs a fresh inspection and executor claim before starting it.
Preparation itself dispatches no paid model request or tool.

Permissions are intersected with today's settings; missing grants are denied,
and a prior run cannot widen the active policy. Pending approvals are not copied
as blanket approvals. Prior completed plan steps and conversation/tool outcomes
remain available so continuation is informed by durable completed work.

Before a resumed model can dispatch a mutation, the runtime also searches the
validated source chain for a matching completed tool fingerprint. It returns the
recorded result without executing the operation again. Missing/truncated results
require reconciliation. Completed plan steps reject further edits. A completed
child result can be delivered from its original journal record without creating
or reconnecting another child.

Ownership is local-process evidence (PID/start identity/token/host), not a
cross-machine distributed lease. A live or unavailable-host owner is treated
conservatively; stale ownership can require manual investigation. Multiple
application instances must not recover a known live executor as interrupted.

## CLI and desktop

The provider-free CLI uses the same database and controller:

```sh
firik-agent runs --db /path/to/projects.db history --project PROJECT_ID
firik-agent runs --db /path/to/projects.db inspect RUN_ID --project PROJECT_ID
firik-agent runs --db /path/to/projects.db compare RUN_ID --project PROJECT_ID
firik-agent runs --db /path/to/projects.db reconcile RUN_ID --project PROJECT_ID \
  --attempt ATTEMPT_ID --outcome completed --evidence 'Observed receipt or inspected file state'
firik-agent runs --db /path/to/projects.db reconcile RUN_ID --project PROJECT_ID \
  --workspace --approve --evidence 'Reviewed changed files and accepted current baseline'
firik-agent runs --db /path/to/projects.db resume RUN_ID --project PROJECT_ID --approve
firik-agent runs --db /path/to/projects.db cancel RUN_ID --project PROJECT_ID
firik-agent runs --db /path/to/projects.db export RUN_ID --project PROJECT_ID --output report.json
```

`resume --approve` prepares a queued continuation and reports
`execution_started: false`; open it in the desktop and explicitly continue.
Export writes sanitized JSON and refuses to overwrite an existing output file.

The desktop run-history recovery view shows lifecycle state, last checkpoint and
successful operation, uncertain attempts, pending verification/approval, conflicts
and available actions. Eligibility is computed from controller preconditions;
unresolved or conflicted work is not labeled safe. The existing in-memory pause/
resume control remains distinct from resuming a persisted interrupted run.

## Privacy and bounded storage

Existing structured credential/key/pattern redaction and configured known secrets
are applied before journal/session/event/export storage. Inputs are SHA-256
fingerprinted and summarized to 4 KiB; result payloads are bounded to 8 KiB,
errors to 4 KiB and ordinary metadata to 16 KiB. Oversized payloads retain a
sanitized preview, digest and explicit truncation marker instead of another full
model transcript. This is best-effort redaction, not a guarantee that arbitrary
unknown secrets can be recognized.

Permission context and workspace evidence use strict bounds; they are never
silently replaced by an unusable preview. Safety manifests are protected up to
1 MiB; a larger one fails closed. The journal defaults to 20,000 intents per run;
capacity exhaustion stops new dispatch. `compact()` may remove older result/input
bodies only for fully terminal runs without unfinished attempts. It retains
terminal identities/outcomes, references, safety metadata and event projection.
Uncertain/resumable data is protected. These are per-run bounds; total database
size can still grow with the number of retained runs and legacy history.

## Fault injection and validation

`ExecutionJournal(..., fault_hook=...)` provides explicit
`journal.before_commit` and `journal.after_commit` hooks with operation/run/attempt
context. `CodingRunControl.durability_fault_hook` provides named intent,
transition, model output, tool result, filesystem, child and completion boundaries.
Production execution has no random fault injection.

The focused journal tests exercise actual subprocess `os._exit()` before/after
commit and after a synthetic external effect, SQLite rollback, duplicate outcomes,
identity conflicts, concurrent runs/store instances, startup uncertainty,
secret-bearing arguments, bounded storage, protected manifests, corruption and
representative historical v1 migration. Additional controller/runtime/CLI/desktop
checks belong to the Phase 4 integration test receipt; test results should be
reported from an actual current run rather than inferred from this document.

The current receipt is
[`Evidence/Durability/20261028/verification.json`](Evidence/Durability/20261028/verification.json).
The final GUI-excluded run passed 1,018 tests with one platform skip. A focused
integration selection passed 129 tests, and the final desktop-action selection
passed nine tests after undo journaling was added. These selections overlap and
must not be added together. Raw outputs are retained beside the receipt.
Real Tk rendering could not be established on this host: `CTk()` aborted during
environment initialization before recovery view rendering. The recovery dialog's
passive-open, approval and conflict actions are exercised with headless widgets;
this is not a claim of visual desktop or Windows coverage.

Reproduce the offline overhead measurement:

```sh
.venv/bin/python scripts/benchmark_durability.py \
  --output docs/Evidence/Durability/20261028/persistence-benchmark.json
```

The JSON includes raw samples for two existing event writes, the **additional**
four-transaction journal lifecycle, and separate read-only capture cases with
10/100/1,000 generated source files in temporary Git repositories. These timings
exclude provider/network latency, large checkpoint bodies and desktop rendering.
They do not establish live paid-provider behavior, device coverage, or recovery
across external service failures.

The captured local receipt reports medians of 2.9737 ms for the two existing event
writes and 7.6898 ms for the additional four-transition journal lifecycle.
Separate workspace-capture medians are 40.4090 ms (10 files), 47.3455 ms (100)
and 101.4758 ms (1,000). Raw samples, p95, fixture byte sizes and environment are
in `docs/Evidence/Durability/20261028/persistence-benchmark.json`. This is a local
synthetic cost measurement, not an end-to-end coding benchmark.
