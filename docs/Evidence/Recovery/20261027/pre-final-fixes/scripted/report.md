# Scripted native recovery probes

| Measure | Recovery disabled | Adaptive |
| --- | ---: | ---: |
| checks_passed | 30 | 30 |
| recoverable_opportunities | 12 | 12 |
| recovered | 0 | 12 |
| recovery_success_rate | 0.0 | 1.0 |
| false_retry_count | 0 | 0 |
| requests | 33 | 45 |
| external_effects | 6 | 6 |
| repeated_external_effects | 0 | 0 |
| mean_elapsed_ms | 0.27683203346290003 | 1.806598633750885 |

These profiles run the same candidate source. The frozen Phase 1 before/after comparison is separate.

- Synthetic offline fault probes; no actual service or network call.
- Disabled and adaptive profiles use the candidate source; this is not a reconstructed legacy baseline.
- Latency includes event construction and scheduler noise; backoff is deliberately 1–10ms for bounded probes.
- The external ledger is a deterministic in-memory side effect, not proof of remote-service idempotency.
- Usage is synthetic on successful terminal requests, missing on injected failures; monetary cost remains unknown.
