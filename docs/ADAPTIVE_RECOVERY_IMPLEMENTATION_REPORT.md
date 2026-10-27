# Phase 2 — Adaptive recovery and verified completion

Implemented on top of the existing Phase 1 working tree. Existing permissions,
quality gates, session storage, file checkpoints and native event ownership are
retained. No provider switching or policy-refusal bypass is introduced.

## A–I delivery map

| Part | Delivered behavior | Primary evidence |
| --- | --- | --- |
| A. Runtime audit | Both entry points, request preparation/submission, SSE lifecycle, partial/tool assembly, execution owner, permission/cancellation boundaries, failure counters, persistence, error events and completion decisions are documented. Five outcome states remain distinct. | [Runtime audit](ADAPTIVE_RECOVERY_AUDIT.md) |
| B. Typed taxonomy | Typed failure kinds and request phases; safe status/code/request identity and Retry-After retained. Shared provider normalization covers HTTP errors, transport, SSE truncation and explicit policy refusal, including partial-output phase preservation. | `core/failures.py`, `providers/_chat.py`, `tests/test_provider_failures.py` |
| C. Recovery policy | Explicit context/decision API, exponential jittered backoff, provider Retry-After, request/run/time/token/cost limits, bounded context reduction/compaction and one read-only capability refresh. Per-attempt host checkpoints and external budget hooks remain authoritative. | `core/recovery.py`, `Agent._request_with_recovery()`, `tests/test_recovery_engine.py` |
| D. Idempotency/side effects | Trusted built-in semantics are separated from unknown third-party tools. Host attempt IDs and normalized fingerprints are journaled separately from provider context. Conditional file writes retain expected hashes; uncertain external effects block replay and coding completion. Successful tools are not replayed because the next model request fails. | `Message.execution`, agent attempt helpers, coding filter/history repair, timeout/mutation/legacy-history tests |
| E. Safety refusals | Policy/permission denials stop; credentials require resolution. No hidden adapter retry, prompt rewrite, safety downgrade or provider switch. Partial output cannot erase a later denial. | Parameterized provider and both-path denial tests |
| F. Completion | Native coding completion checks host-issued execution evidence, exit codes, revision/hash freshness, acceptance criteria, required gates and unresolved operations before a successful final event. Lifecycle classifications map to existing statuses; repairs require new evidence and are bounded. Read-only modes do not require tests. | `core/completion.py`, existing coding quality-gate/evidence owner, `tests/test_completion_controller.py` |
| G. Events/cockpit | Recovery is additive native `AgentEvent` metadata: classification, phases, decisions, attempts, delay, budgets, repeat safety and reconciliation. Final classification is stored/projected without a second event stream. Non-stream callers can use `on_event`. | Native trace artifacts; cockpit/session compatibility tests |
| H. Test matrix | Scripted providers/tools and mock HTTP/SSE cover the twenty requested scenarios plus cleanup errors, capability refresh, journal integrity and budget reservation. | Matrix below and final pytest receipts |
| I. Benchmark validation | Original Phase 1 suite measured before/after, with frozen oracles/controls. Separate same-source disabled/adaptive fault probes measure eligible recovery, forbidden retries, ledger effects and request/latency overhead. Monetary changes remain unknown. | [Benchmark method/results](ADAPTIVE_RECOVERY_BENCHMARKS.md) and dated raw JSON |

Paths in the table are repository-relative module references. Public behavior,
configuration and restrictions are described in [Adaptive recovery](ADAPTIVE_RECOVERY.md).

## Requested test matrix

| # | Scenario | Executable coverage |
| ---: | --- | --- |
| 1 | HTTP 429, valid Retry-After | `test_adapter_classifies_without_retaining_body`, `test_retry_after_seconds`, `test_retry_after_http_date_is_relative_and_rejects_past`, `test_429_retry_after_and_503_recovery_both_paths` |
| 2 | HTTP 503 then recovery | `test_429_retry_after_and_503_recovery_both_paths`; supplemental `overloaded`/`confirmed-external-then-overload` ledger probes |
| 3 | Unauthorized access | Adapter HTTP401 cases and `test_denials_and_schema_failures_never_switch_or_replay` in both entry points |
| 4 | Provider refusal | Adapter structured-error, HTTP200 refusal and late-refusal cases; both-path denial tests |
| 5 | Context overflow | Adapter HTTP context case, explicit policy decision cases, `test_context_reduction_only_changes_request_view` and compaction reservation tests |
| 6 | Unsupported tool schema | Adapter schema case, both-path no-replay cases, `test_unsupported_schema_refreshes_metadata_once_without_resubmitting_prompt`; bounded authenticated refresh tests |
| 7 | Malformed tool JSON | `test_malformed_arguments_and_repeated_identical_failure_are_bounded`, `test_malformed_arguments_and_permission_denials_remain_classified`; frozen malformed-arguments fixture |
| 8 | Stream interrupted before output | `test_unsent_stream_failure_recovers_but_submitted_and_partial_do_not`; actual adapter connection/interruption tests |
| 9 | Stream interrupted after partial output | Same runtime test, adapter partial text/tool/cleanup cases and frozen partial/incomplete-tool fixtures |
| 10 | Timeout before confirmed execution | `test_timeout_before_dispatch_has_distinct_phase`, `test_timeout_does_not_claim_tool_execution_without_phase_evidence` |
| 11 | Timeout after uncertain external effect | `test_timeout_after_external_dispatch_blocks_replay_and_persists_identity`, `test_uncertain_external_timeout_blocks_mutation_replay`; independent supplemental ledger probe |
| 12 | Repeated identical tool failure | `test_malformed_arguments_and_repeated_identical_failure_are_bounded`; frozen repeated-tool-loop fixture |
| 13 | Cancellation in backoff | `test_cancellation_during_backoff_prevents_second_request`; supplemental cancel-backoff probe |
| 14 | Permission revoked between attempts | Streaming and non-stream permission-change tests; native live-policy/tool permission tests retained |
| 15 | Token/monetary exhaustion | `test_request_token_and_monetary_limits_are_bounded`, `test_compaction_cannot_bypass_token_reservation` |
| 16 | Valid evidence completion | `test_current_host_execution_evidence_is_verified_and_no_prose_can_replace_it`, `test_real_recorded_exit_and_source_digest_control_completion`; guarded-edit fixture |
| 17 | Stale evidence | Parameterized stale/hash/revision/exit/unexecuted cases; source-digest completion test |
| 18 | Failed tests and bounded repair | `test_failed_verification_cannot_repeat_without_progress_and_repairs_are_bounded` |
| 19 | Final prose with incomplete criteria | `test_final_without_tools_cannot_satisfy_incomplete_acceptance`, `test_nonstream_final_also_blocks_incomplete_coding_task`, controller incomplete-criteria tests |
| 20 | Existing session compatibility | `test_lifecycle_uses_existing_native_projection_and_legacy_session_statuses`, legacy missing-result repair and successful-journal preservation tests; original session/cockpit regression suites |

The provider test parameterization covers both FIRIK and OpenAI-compatible
adapters and both stream/non-stream interfaces. The recovery integration cases
exercise both `Agent.run()` and `Agent.run_stream()` for critical shared policy
invariants. The completion tests use the existing guarded coding owner; a model
cannot submit an arbitrary successful evidence ID as a substitute.

## Validation receipts

Historical baseline and final candidate/supplemental runs are retained under
[docs/Evidence/Recovery/20261027](Evidence/Recovery/20261027). Stable-source results are recorded in [benchmark validation](ADAPTIVE_RECOVERY_BENCHMARKS.md):

- Frozen Phase 1 before/after: **60/60 objective checks each**, **30/60 verified
  tasks each**, false native claims **6 → 3**, model requests **105 → 105**,
  **zero safety violations** and no per-task regression. Observed mean elapsed
  delta **+5.23 ms/trial**, with a task-cluster descriptive interval spanning
  zero; this is not an isolated latency estimate.
- Supplemental same-source disabled/adaptive: **30/30 checks per profile**,
  eligible recovery **0/12 → 12/12**, **zero false retries**, **zero repeated
  ledger effects**, **12 extra model requests** and **+1.40 ms/trial** average
  elapsed time with short scripted backoff. Monetary cost remains unknown.
- Benchmark/report integrity tests: **8 passed**, with an
  [actual pytest receipt](Evidence/Recovery/20261027/benchmark-tests.xml).

The candidate Python source fingerprint was stable before/after its run:
`1f33caf817f21d6930d541a46efd04e56448237623214c2839b6ca5d001935b4`.
Baseline and candidate raw reports preserve the original controls and individual
native trace rows. Intermediate preflight reports under `/private/tmp` are
diagnostic only and are not final acceptance evidence. Full regression validation passed **936 tests**, with **one Windows-only quoting
test skipped** on macOS and **zero failures**. The complete non-UI selection
passed **844**, and the complete native GUI selection passed **92**, in separate
processes. [Machine-readable totals](Evidence/Recovery/20261027/tests/test-results.json),
[non-UI JUnit receipt](Evidence/Recovery/20261027/tests/nonui.xml) and
[GUI JUnit receipt](Evidence/Recovery/20261027/tests/ui.xml) retain the commands
and results. `compileall` and `git diff --check` also passed.

The only Phase 1 runner adaptation maps native ERROR events explicitly classified
`blocked` to a blocked terminal result before DONE. Other errors remain failed.
Task manifests, expected source bytes, acceptance oracles and starting Git
revisions are unchanged. A stronger native completion gate must not be reported
as a fixture regression merely because enforcement now occurs before DONE.

## Practical limits

- External exactly-once execution is not claimed. Unknown shell/Git/MCP outcomes
  require explicit reconciliation or operator inspection; the host cannot invent
  enforced idempotency keys for arbitrary tools. An idempotency annotation alone
  does not establish trusted semantics.
- Timeouts and cancellation are cooperative. No new OS sandbox, forced process
  termination, cross-process workspace lease or remote transaction service is
  introduced by recovery.
- Token estimates, delayed provider usage and estimated prices limit monetary
  guarantees. Missing usage/prices fail closed under active caps, but already
  billed work cannot be reversed. No live cost or production reliability claim
  follows from offline probes.
- Old `completed` sessions remain readable and are not retroactively marked
  verified. Session restoration is passive; historical tool calls are never
  replayed simply by opening a record.
- Read-only/ask mode has no access to an independent hidden acceptance oracle.
  It can finish unverified while the frozen evaluator correctly rejects false
  prose about work it did not observe. Coding acceptance criteria must be
  explicit and checked through the authoritative coding evidence owner.
- Local validation runs on the available macOS/Python environment; it does not
  establish every supported OS/Python version or live provider's behavior.

## Regression expectation changes

Two old Phase 1 expectations intentionally follow the new runtime contract:
explicit HTTP429 rejections now have bounded retry coverage rather than belonging
to the never-retry parameterization, and interrupted partial coding responses
are recorded as blocked instead of a generic failure. Denial, unknown transport,
authentication and policy no-replay assertions remain. The frozen benchmark
oracles and manifests are unchanged; its native adapter maps an explicit blocked
completion event to the existing blocked status.

## GUI regression resolved during validation

The original long-conversation regression could trap Aqua Tk in native text
layout when a hidden Markdown card mapped at a placeholder width. Long content
now waits for a usable mapped width before constructing wrapped text widgets;
short code cards keep their existing highlighting contract. The full message
stays in persistence/export. The existing resize test is unchanged, and a new
regression test confirms that the latest hidden update renders when opened.
All 92 native GUI tests pass with macOS WindowServer access; sandbox-only Tk
startup aborts were environmental diagnostics, not final test results.

MCP server `isError` responses now remain typed failures and require
reconciliation rather than being recorded as successful strings. Their raw error
bodies are excluded. Host attempt IDs, run identities, operation fingerprints
and side-effect classifications cannot be overwritten by tool result metadata.
Known successful calls can run again under an explicit new run, while uncertain
operations remain blocked across session restoration.
