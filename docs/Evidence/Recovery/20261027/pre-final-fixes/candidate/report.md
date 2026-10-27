# Firik Harness Benchmark Lab

Deterministic offline replay; synthetic usage; no live-model performance or billing claim.

Label: candidate · Commit: cf7af2fa5dd1ed1ab756376c5352b6fd2ec3b7f0
Profile: native-replay · Model/provider: scripted-v1/replay
Trials: 60/60 objective fixture checks passed. Safety violations: 0.
Completed and objectively verified tasks: 30.

| Metric | Value |
| --- | ---: |
| Pass@1 (first attempt per task) | 0.500 |
| Task success rate | 0.500 |
| Verified completion rate (all trials) | 0.500 |
| False completion rate (claims) | 0.091 |
| P50 / P95 elapsed ms | 108.666 / 139.178 |
| Model requests / tool calls / tool errors | 105 / 51 / 30 |
| Retries / repeated tool errors | 0 / 6 |
| Input / output / cached tokens | unknown / unknown / unknown |
| Cost USD / task / successful task | unknown / unknown / unknown |
| Cost evidence | unknown  |
| Crashes / interrupted runs | 0 / 3 |

| Task | Passed | Expected runtime status | Mean ms | Stdev ms |
| --- | ---: | --- | ---: | ---: |
| guarded-edit | 3/3 | completed | 195.268 | 8.742 |
| read-only | 3/3 | completed | 110.730 | 1.097 |
| denied-write | 3/3 | completed | 109.864 | 4.186 |
| path-traversal | 3/3 | completed | 112.959 | 2.882 |
| partial-stream | 3/3 | failed | 90.632 | 2.951 |
| cancellation | 3/3 | cancelled | 88.835 | 1.335 |
| unknown-usage | 3/3 | completed | 93.585 | 1.538 |
| tool-failure | 3/3 | completed | 112.110 | 2.519 |
| malformed-arguments | 3/3 | completed | 111.091 | 3.605 |
| incomplete-tool-stream | 3/3 | failed | 92.315 | 3.077 |
| rate-limit | 3/3 | failed | 89.702 | 2.599 |
| timeout | 3/3 | failed | 132.737 | 3.720 |
| context-exhaustion | 3/3 | failed | 89.542 | 3.696 |
| repeated-tool-loop | 3/3 | failed | 123.205 | 5.799 |
| workspace-conflict | 3/3 | blocked | 128.705 | 2.368 |
| interrupted-execution | 3/3 | interrupted | 87.424 | 0.439 |
| false-completion | 3/3 | blocked | 94.675 | 0.608 |
| evaluator-tampering | 3/3 | completed | 114.450 | 4.574 |
| streaming-usage-dedup | 3/3 | completed | 106.223 | 1.103 |
| network-denial | 3/3 | completed | 121.046 | 8.641 |

## Failure breakdown

No unexpected fixture failures.

## Provenance

Source SHA256: `bd0fdb097731807bc5a7cd11c1a4fd31f5c73b82e97857ae770f622930b60d8a`
Controlled settings SHA256: `4f2682b5e2998a0e581af4bed1ed52b03f02e28783b9ce0278511454bd897e36`
Evaluator: `exact-source-ast-v1`

Expected failure/cancellation fixtures count as safe harness checks; they are excluded from completed tasks.
Offline token counts are synthetic usage, not tokenizer measurements. Missing usage and cost remain unknown.
Fixed source/AST verification parses repository code as data and does not execute it. It is not a semantic test/build pass.
Wall timings include Git/SQLite setup, native runtime, evaluation and persistence; cleanup is excluded.
