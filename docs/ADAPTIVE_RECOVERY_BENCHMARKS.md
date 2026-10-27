# Phase 2 recovery and completion validation

The validation has two distinct experiments. The original Phase 1 suite measures
legacy-to-candidate behavior with frozen task/oracle/control fingerprints. A
supplementary fault-then-success suite measures the candidate runtime with
recovery disabled and enabled. Its results must not be presented as a historical
legacy-source comparison or as production provider performance.

## Reproduce

From the repository root, using the existing virtual environment:

```sh
# Historical baseline was run before Phase 2 runtime edits.
.venv/bin/python -m firik_agent.benchmarks bench --suite offline-smoke --repeat 3 --output docs/Evidence/Recovery/20261027/baseline
# Run only against stable sources; every output directory must be new.
.venv/bin/python -m firik_agent.benchmarks bench --suite offline-smoke --repeat 3 --output docs/Evidence/Recovery/20261027/candidate
.venv/bin/python scripts/phase2_recovery_report.py docs/Evidence/Recovery/20261027/baseline/report.json docs/Evidence/Recovery/20261027/candidate/report.json docs/Evidence/Recovery/20261027/comparison
.venv/bin/python scripts/phase2_scripted_benchmark.py docs/Evidence/Recovery/20261027/scripted --repeat 3
.venv/bin/python -m pytest -q tests/test_phase2_recovery_report.py tests/test_phase2_scripted_benchmark.py
```

Neither experiment reads provider credentials or makes a paid model request.
Phase 1 fixtures create temporary Git/SQLite workspaces and run the guarded
in-process source/AST oracle, never executable fixture commands. Supplemental
probes use a scripted provider and an in-memory external-operation ledger with
the real `Agent.run_stream()` loop, native recovery events and tool attempts.

## Frozen Phase 1 suite

The baseline has 20 tasks repeated three times, 60/60 objective fixture checks,
30/60 independently verified completed tasks, 6 false native completion claims
among 36 claims, 105 model requests, 51 native tool-start events and zero safety
violations. Synthetic or missing usage cannot establish provider cost.

The runtime now emits a native blocked completion classification before DONE
when the authoritative completion gate fails. The runner adapter recognizes an
ERROR with `completion_classification=blocked` as blocked with
`terminal_reason=completion_gate`; other ERROR events remain failed. This is
necessary to retain the same task expectation after moving enforcement earlier.
No fixture expected source, pass criteria, starting revision or evaluator was
changed to accommodate the implementation.

A frozen ask-mode fixture can still produce false prose about hidden acceptance
criteria. The native host has no access to that independent oracle; it treats
ask mode as read-only and does not invent a build/test requirement. The
supplementary unit/integration matrix separately tests auto-mode incomplete
acceptance criteria and evidence freshness. The objective evaluator remains the
source of truth for benchmark task success.

The original rate-limit and context-error scripts authorize stopping, contain no
successful continuation turn, and have no normalized HTTP metadata. Their
absence of recovery is expected. A recovery success rate cannot honestly be
estimated from this denominator.

## Supplemental scripted opportunities

Ten cases, each repeated three times under two profiles, exercise:

- HTTP-style 429 with valid Retry-After, 503, and a connection failure known not
  to have submitted, each followed by a successful scripted response.
- A submitted read timeout, partial response interruption, unauthorized access
  and provider policy refusal, which must stop without a replay.
- Cancellation during backoff, which must stop before another request.
- An uncertain external effect, whose ledger must contain exactly one entry.
- A confirmed external effect followed by a model 503, which must recover the
  model request without executing the external operation a second time.

The successful-recovery denominator is four explicitly eligible operations per
repetition, including the model failure after a confirmed tool operation. False
retry count measures actual provider request dispatches in the six ineligible
cases. A decision event alone is not counted as a retry. Repeated external
effects are measured from the independent ledger, not inferred from model text
or tool-start intent. The disabled profile uses retry limits zero; all other
source/provider/script settings match the adaptive profile.

Backoff is deliberately 1–10 ms in these bounded probes. Added latency includes
local scheduling and event construction; it is not a full provider round trip.
Synthetic success-frame usage and unknown failure usage do not establish cost.
The suite is a deterministic correctness check, not a representative estimate
of production error frequencies or recovery rates.

## Evidence

- [Baseline native report](Evidence/Recovery/20261027/baseline/report.json)
- [Runtime boundary audit](ADAPTIVE_RECOVERY_AUDIT.md)

Final candidate, paired and supplemental native evidence are retained in the
same dated evidence directory. Reports reject sources changing during execution
and preserve per-attempt identities, error phases and native trace data. Local
source fingerprints establish provenance within the checkout; they are not
cryptographic authenticity signatures or an OS sandbox.

## Measured results — 2026-10-27

Both Phase 1 runs passed **60/60** objective checks with identical task/oracle and
controlled-setting fingerprints. Each source fingerprint was stable throughout
its run. The candidate fingerprint is
`1f33caf817f21d6930d541a46efd04e56448237623214c2839b6ca5d001935b4`.

| Frozen Phase 1 measure | Before | After |
| --- | ---: | ---: |
| Independently verified completed tasks | 30/60 | 30/60 |
| Native completion claims | 36 | 33 |
| False completion claims | 6 | 3 |
| Model requests | 105 | 105 |
| Native tool-start events | 51 | 51 |
| Safety violations | 0 | 0 |
| Actual request retries | 0 | 0 |
| False retries | 0 | 0 |
| Mean wall time, ms/trial | 101.61 | 106.84 |

The earlier completion gate removes the three workspace-conflict DONE claims.
Three false claims remain in the frozen ask-mode hidden-oracle fixture, as
explained above. No task-success or objective-fixture regression was observed.
Measured mean wall time increased **5.23 ms/trial**; the descriptive task-cluster
95% interval for the paired mean delta is **−3.12 to +12.55 ms**. Other local test
processes were running during candidate capture. This is an observed elapsed
difference, not an isolated or statistically established engine latency cost.
There were no added model requests in this frozen suite, and cost is unknown.

| Supplemental same-source measure | Recovery disabled | Adaptive |
| --- | ---: | ---: |
| Objective probe checks | 30/30 | 30/30 |
| Eligible recovery successes | 0/12 | 12/12 |
| False retry dispatches | 0 | 0 |
| Model requests | 33 | 45 |
| External ledger effects | 6 | 6 |
| Repeated external effects | 0 | 0 |
| Mean elapsed time, ms/trial | 0.32 | 1.72 |

The twelve extra model requests are the twelve successful bounded recovery
attempts. Both profiles record one effect per external-operation trial; uncertain
effects stop and confirmed effects are retained through later model recovery.
The adaptive probes add **1.40 ms/trial** on average with the deliberately short
scripted backoff. This finite synthetic 12/12 recovery result does not estimate
production reliability. Token/cost changes cannot be measured from these fake
failure responses and remain null.

Raw evidence:

- [Candidate native report](Evidence/Recovery/20261027/candidate/report.json)
- [Validated paired comparison](Evidence/Recovery/20261027/comparison/report.json)
- [Paired comparison summary](Evidence/Recovery/20261027/comparison/report.md)
- [Supplemental native trace/ledger report](Evidence/Recovery/20261027/scripted/report.json)
- [Supplemental summary](Evidence/Recovery/20261027/scripted/report.md)
- [Final command/exit-code receipts](Evidence/Recovery/20261027/benchmark-receipts.json)
- [Earlier capture preserved before final MCP/UI fixes](Evidence/Recovery/20261027/pre-final-fixes/README.md)
