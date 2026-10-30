# Native adapter overhead

Current original loop and wrapper use the same complete runtime and synthetic replay.

- original-native-loop: 25/25 passed, median 20.662 ms, p95 21.574 ms.
- native-harness-adapter: 25/25 passed, median 23.911 ms, p95 24.582 ms.

Paired median wrapper difference: 3.341 ms. Local timing only; no production speedup claim.
