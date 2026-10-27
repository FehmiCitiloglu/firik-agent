# ADR: request-scoped passive accounting and native replay benchmarks

Status: accepted for the first adaptive harness slice, 2026-10-26.

## Context

The native runtime already enforces tools, verifies revisions and journals runs.
Replacing those mechanisms would create two permission/verification authorities.
Existing usage streams are cumulative and may omit usage or pricing. Failed
requests can still cost money. Compaction is also a provider request.

## Decision

- Add `model_request_finished` alongside existing events. Each actual provider
  attempt has its own `request_id`; compaction is identified by `request_kind`.
  Capture provider usage outside model-visible message serialization.
- Use `core.metrics.RunMetrics` as a passive reducer shared by the desktop and
  benchmark. Repeated request frames replace measurements, not add charges.
  Failed, cancelled, unfinished and missing-usage attempts make exhaustive totals
  unknown; retain the reported subtotal separately with coverage.
- Persist `usage.harness_metrics` in existing coding-session JSON. Keep old
  numeric schema fields and old histories compatible. Historical aggregate
  snapshots remain authoritative when event replay is truncated.
- Bound retained request identities. If the bound is exceeded, mark accounting
  limited and make complete totals unknown. Never silently evict billed usage.
- Exercise native `Agent.run_stream`, `CodingRunControl`, `CodingToolFilter` and
  `CodingSessionStore` in fresh temporary repositories. Supply only an offline
  replay provider and explicitly bounded evaluators. Do not load host config,
  keyring, skills, plugins, MCP or live provider catalogs.

## Consequences

No dependency, database migration or new paid-provider requirement is needed.
New events are additive; consumers must accept extension events. Request IDs and
latency naturally vary between repeats; task data, scripts, settings and evaluator
identity are fingerprinted for controlled comparison. Nullable totals can differ
from legacy partial integer counters; the UI labels new coverage explicitly.

The reducer does not decide permission, recovery, billing, completion, or sandbox
availability. It records evidence. Native permission and verification remain
authoritative. Real model performance and exactly-once side effects are outside
the guarantees of replay benchmarks.

## Upgrade / rollback

No startup work is resumed and no pending tool is replayed. Old versions can
ignore the additive usage JSON and extension event. Roll back source/package
changes to stop emitting telemetry; retain databases and journals. Old sessions
without the new snapshot continue to use their existing presentation. Do not
convert legacy zero counters into claimed measured totals.
