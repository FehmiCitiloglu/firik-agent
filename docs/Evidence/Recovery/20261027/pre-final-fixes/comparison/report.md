# Phase 2 matched offline validation

| Measure | Baseline | Candidate |
| --- | ---: | ---: |
| Fixture checks passed | 60 | 60 |
| Verified tasks | 30 | 30 |
| Native completion claims | 36 | 33 |
| False completion claims | 6 | 3 |
| Model requests | 105 | 105 |
| Native tool starts | 51 | 51 |
| Safety violations | 0 | 0 |
| Actual retry requests | 0 | 0 |
| False retries | 0 | 0 |
| Required reconciliations | 0 | 6 |
| Observed uncertain tools with no later replay | 0 | 0 |
| Repeated mutation dispatch after unknown result | 0 | 0 |

Added mean wall time: 8.65 ms/trial.
Recovery-opportunity success and cost changes: **unmeasured** in this frozen suite.

## Limits

- Deterministic offline replay; no live model requests or provider billing measurement.
- False retries are actual retry dispatches after denial/partial output or without safe policy authorization; unclassified legacy attempts are separate.
- Repeat-mutation counts are diagnostic observations, not a universal proof: intentional user-authorized identical operations can be valid.
- A reconcile decision prevents automatic replay; it does not establish the remote operation outcome.
- The original Phase 1 task/evaluator/control fingerprints are preserved; native ask-mode prose can claim unknown work that only the independent oracle can disprove.
- Added wall time includes local fixture/persistence/host scheduling noise and is not an isolated engine microbenchmark.

See raw JSON for per-task outcomes, request evidence, descriptive task-cluster intervals and diagnostic counts.
