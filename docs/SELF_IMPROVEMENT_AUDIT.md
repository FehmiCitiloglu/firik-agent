# Self-improvement: initial repository audit

Audited 2026-11-04 against `8775ff55aa8096afdaa61f555a7eafea31ca901d`
and the existing dirty working tree, before implementation. Voice, packaging,
plugin and desktop edits already present are outside this change's ownership.

## Architecture and reusable owners

| Concern | Existing owner | Integration |
| --- | --- | --- |
| Agent execution | `core/agent.py`, `core/session.py`, `core/events.py` | Observe bounded structural events; never infer success from assistant text/DONE. |
| Requests and recovery | `core/metrics.py`, `core/recovery.py`, `core/compaction.py` | Reuse nullable measurements and typed recovery classifications. No provider switching. |
| Durable coding evidence | `projects/coding_sessions.py`, `execution_journal.py`, `coding_runtime.py` | Reference durable event/run IDs; preserve host completion and unknown-outcome semantics. |
| Data ownership | `projects/db.py` | Add namespaced tables through the existing WAL/FULL-sync database; no copied chat/project store. |
| Context | `projects/context_builder.py`, `coding_service.py`, `retrieval.py` | Add approved scoped knowledge to the existing memory slice and inspector. |
| Skills | `skills/manager.py`, `skill.py`, `projects/skill_selection.py` | Stage compatible files; approval before writing; retain existing progressive selection. |
| Tools | `plugins/manager.py`, MCP registry | Never call dynamic code loading from reflection; propose typed capability gaps. |
| Candidate development | `projects/isolated_workspaces.py`, `workspace.py`, `quality_gates.py`, `subagents.py` | Reuse revision-bound worktrees and restricted Docker execution; worktrees alone are not sandboxes. |
| Evaluation | `harness/`, `benchmarks/` | Reuse pinned comparison reports, host evaluators, unknown accounting and regression gates. |
| Desktop | `ui/service.py`, `coding_bridge.py`, `ui/app.py`, `ui/views/` | Service worker bridge and queued main-thread callbacks; CustomTkinter only. |
| CLI/config | `cli.py`, `config.py` | Provider-free early dispatch; validated conservative settings separate from execution authority. |

Inspected the requested recovery, compaction, cockpit, harness desktop/security and
pluggable harness documentation. Existing specialists inherit parent permissions;
deterministic reflection needs no specialist runtime, credentials, shell or network.

## Gaps and sequence

1. Add versioned evidence/proposal/memory/job/audit records and idempotent migrations.
2. Collect metadata, deduplicate durable observations, bound reflection jobs and
   generate facts versus hypotheses without transcript replay.
3. Stage scoped memories, corrections and skill revisions; enforce revision-bound
   human decisions, deletion, retrieval budgets and rollback.
4. Add typed tool proposals, isolated code patches and trustworthy harness
   comparisons using existing execution owners.
5. Connect CLI, desktop, context inspector and runtime collection; run negative,
   restart, migration, cancellation and compatibility tests.

## Risks and enforcement

- Tool output, logs, skills and candidate patches are untrusted data. Evidence
  allowlists exclude transcript, arguments, credentials and arbitrary metadata.
- Unknown execution outcomes remain unknown. Restart marks interrupted learning
  work for review; it never replays a pending external mutation.
- Memory and skill writes are staged by default. Trusted CLI/GUI decisions bind
  exact proposal revision/digest; model tools cannot grant approval.
- Project-local evidence cannot become global knowledge implicitly. Explicit
  project instructions/settings and confirmed preferences outrank inference.
- Evaluators, tests, security, approvals, credentials, audit and release paths are
  protected from experimental source patches. Unverified isolation blocks execution.
- Multi-resource application records an intent before dispatch and an outcome
  afterward. An interrupted apply requires reconciliation, never blind replay.

## Migration and rollout

Namespaced `improvement_*` tables are additive and independently versioned.
Opening an old database neither rewrites existing project records nor applies
pending changes. FTS5 is optional; lexical fallback remains available. `off`
disables collection/reflection/retrieval, while existing chat/coding still works.
Default `observe` records permitted metadata with automatic reflection off.
All experimentation is opt-in; neither background services nor provider support
are assumed. No release, push, dependency installation or live paid request is
part of this implementation.

## Hermes reference

Primary references reviewed: [persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory),
[skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), and
[curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator).
The useful patterns are bounded retention, progressive skill disclosure, staged
writes and maintenance. Firik keeps its own database/context/permission owners
and uses stricter default approval and project-isolation boundaries.
