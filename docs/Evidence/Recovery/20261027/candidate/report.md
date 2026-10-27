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
| P50 / P95 elapsed ms | 102.742 / 139.772 |
| Model requests / tool calls / tool errors | 105 / 51 / 30 |
| Retries / repeated tool errors | 0 / 6 |
| Input / output / cached tokens | unknown / unknown / unknown |
| Cost USD / task / successful task | unknown / unknown / unknown |
| Cost evidence | unknown  |
| Crashes / interrupted runs | 0 / 3 |

| Task | Passed | Expected runtime status | Mean ms | Stdev ms |
| --- | ---: | --- | ---: | ---: |
| guarded-edit | 3/3 | completed | 189.329 | 10.603 |
| read-only | 3/3 | completed | 108.571 | 7.521 |
| denied-write | 3/3 | completed | 109.409 | 2.758 |
| path-traversal | 3/3 | completed | 110.756 | 3.789 |
| partial-stream | 3/3 | failed | 97.793 | 12.500 |
| cancellation | 3/3 | cancelled | 81.518 | 3.315 |
| unknown-usage | 3/3 | completed | 90.772 | 8.578 |
| tool-failure | 3/3 | completed | 103.237 | 5.972 |
| malformed-arguments | 3/3 | completed | 105.606 | 5.845 |
| incomplete-tool-stream | 3/3 | failed | 89.880 | 7.865 |
| rate-limit | 3/3 | failed | 89.023 | 5.199 |
| timeout | 3/3 | failed | 133.555 | 4.911 |
| context-exhaustion | 3/3 | failed | 87.924 | 2.115 |
| repeated-tool-loop | 3/3 | failed | 120.093 | 5.197 |
| workspace-conflict | 3/3 | blocked | 126.351 | 9.197 |
| interrupted-execution | 3/3 | interrupted | 85.484 | 2.982 |
| false-completion | 3/3 | blocked | 94.207 | 4.694 |
| evaluator-tampering | 3/3 | completed | 107.054 | 5.115 |
| streaming-usage-dedup | 3/3 | completed | 100.742 | 4.174 |
| network-denial | 3/3 | completed | 105.570 | 5.649 |

## Failure breakdown

No unexpected fixture failures.

## Provenance

Source SHA256: `1f33caf817f21d6930d541a46efd04e56448237623214c2839b6ca5d001935b4`
Controlled settings SHA256: `4f2682b5e2998a0e581af4bed1ed52b03f02e28783b9ce0278511454bd897e36`
Evaluator: `exact-source-ast-v1`

Expected failure/cancellation fixtures count as safe harness checks; they are excluded from completed tasks.
Offline token counts are synthetic usage, not tokenizer measurements. Missing usage and cost remain unknown.
Fixed source/AST verification parses repository code as data and does not execute it. It is not a semantic test/build pass.
Wall timings include Git/SQLite setup, native runtime, evaluation and persistence; cleanup is excluded.
