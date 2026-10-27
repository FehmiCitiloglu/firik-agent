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
| False completion rate (claims) | 0.167 |
| P50 / P95 elapsed ms | 88.864 / 158.462 |
| Model requests / tool calls / tool errors | 105 / 51 / 30 |
| Retries / repeated tool errors | 0 / 6 |
| Input / output / cached tokens | unknown / unknown / unknown |
| Cost USD / task / successful task | unknown / unknown / unknown |
| Cost evidence | unknown  |
| Crashes / interrupted runs | 0 / 3 |

| Task | Passed | Expected runtime status | Mean ms | Stdev ms |
| --- | ---: | --- | ---: | ---: |
| guarded-edit | 3/3 | completed | 153.179 | 3.963 |
| read-only | 3/3 | completed | 104.569 | 15.212 |
| denied-write | 3/3 | completed | 148.864 | 57.298 |
| path-traversal | 3/3 | completed | 131.899 | 41.558 |
| partial-stream | 3/3 | failed | 87.137 | 11.412 |
| cancellation | 3/3 | cancelled | 81.626 | 5.822 |
| unknown-usage | 3/3 | completed | 122.810 | 77.054 |
| tool-failure | 3/3 | completed | 109.293 | 34.873 |
| malformed-arguments | 3/3 | completed | 96.465 | 11.692 |
| incomplete-tool-stream | 3/3 | failed | 83.182 | 9.046 |
| rate-limit | 3/3 | failed | 77.001 | 2.673 |
| timeout | 3/3 | failed | 124.680 | 1.440 |
| context-exhaustion | 3/3 | failed | 79.319 | 1.828 |
| repeated-tool-loop | 3/3 | failed | 102.568 | 4.092 |
| workspace-conflict | 3/3 | blocked | 108.733 | 3.597 |
| interrupted-execution | 3/3 | interrupted | 78.083 | 2.985 |
| false-completion | 3/3 | blocked | 78.727 | 2.909 |
| evaluator-tampering | 3/3 | completed | 91.096 | 6.171 |
| streaming-usage-dedup | 3/3 | completed | 84.109 | 1.119 |
| network-denial | 3/3 | completed | 88.851 | 0.222 |

## Failure breakdown

No unexpected fixture failures.

## Provenance

Source SHA256: `3da2bb25cfcba6f38c1bf4faa9ed1326db178927e8d175424b13d729988e4c12`
Controlled settings SHA256: `4f2682b5e2998a0e581af4bed1ed52b03f02e28783b9ce0278511454bd897e36`
Evaluator: `exact-source-ast-v1`

Expected failure/cancellation fixtures count as safe harness checks; they are excluded from completed tasks.
Offline token counts are synthetic usage, not tokenizer measurements. Missing usage and cost remain unknown.
Fixed source/AST verification parses repository code as data and does not execute it. It is not a semantic test/build pass.
Wall timings include Git/SQLite setup, native runtime, evaluation and persistence; cleanup is excluded.
