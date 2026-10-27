# Adaptive Recovery and Verified Completion — Phase 2

Firik routes `Agent.run()` and `Agent.run_stream()` through the same bounded
model-request recovery controller. Coding runs also use the existing guarded
tool executor, persisted session records, workspace evidence and quality gates
to determine completion. Recovery does not grant permissions, select another
provider or replace checkpoints.

Implementation entry points are `core/failures.py`, `core/recovery.py`,
`providers/_chat.py`, `core/agent.py`, `core/completion.py` and
`projects/coding_runtime.py`. The [runtime audit](ADAPTIVE_RECOVERY_AUDIT.md)
describes the execution boundaries. Benchmark receipts and their limitations
are documented in [Phase 2 validation](ADAPTIVE_RECOVERY_BENCHMARKS.md).

## Failure classification

`RuntimeFailure` carries a `FailureKind`, a `RequestPhase` and bounded diagnostic
metadata. Firik and OpenAI-compatible chat adapters share normalization for HTTP,
transport, SSE parsing, partial tool assembly and explicit refusals. Classification
uses structured provider errors; the body is discarded before constructing the
public exception. Safe metadata can include HTTP status, provider code, provider
request ID, source exception type and parsed `Retry-After`. Arbitrary messages,
response bodies, credentials and complete HTTP headers are not retained.

| Failure kind | Default recovery treatment |
| --- | --- |
| `transport_error` | Retry only with proof that a model request was not submitted. A timeout after submission does not establish that proof. |
| `rate_limited` | Bounded backoff at a request boundary without partial output; honor valid `Retry-After`. |
| `provider_overloaded` | Bounded backoff for a classified overload response without partial output. |
| `authentication_failed` | Stop and request credential resolution. |
| `permission_denied` | Stop the recovery operation; existing host permission checks remain authoritative. |
| `context_overflow` | Reduce oversized tool output in the request view, or use the existing context compactor when enabled; otherwise request intervention. |
| `unsupported_capability` | Perform at most one bounded read-only capability refresh, then stop the original request. A remote schema rejection does not cause automatic schema removal or a changed prompt. |
| `invalid_tool_arguments` | Return a typed tool error without dispatching malformed JSON. |
| `stream_interrupted` | Reconcile partial output; no silent replay. An interruption without output still needs a proven safe submission state for automatic retry. |
| `tool_timeout` | Distinguish a pre-dispatch timeout from an uncertain dispatched operation. |
| `tool_outcome_unknown` | Require reconciliation; do not repeat the operation automatically. |
| `workspace_conflict` | Inspect the current workspace revision before another write. |
| `budget_exceeded` | Stop when the relevant resource limit is reached. |
| `policy_refusal` | Stop; no provider switch, prompt rewriting, classification downgrade or hidden retry. |
| `cancelled` | Stop, including while waiting in backoff. |
| `unknown` | Fail closed with the safe source exception type. |

HTTP 401 maps to authentication failure, 403 to permission denial unless a
structured policy refusal is identified, and 402/insufficient-quota errors to
budget exhaustion. A generic 500 or arbitrary `RuntimeError` is not enough to
prove that retrying is safe. Explicit refusal fields and content-filter finish
reasons also stop HTTP-200 responses and SSE streams. A late refusal or permission
denial keeps its classification even after earlier output.

## Submission and streaming boundaries

| Persisted/observed phase | What the host knows |
| --- | --- |
| `not_submitted` | A local unsupported interface or a classified connection/pool failure established that submission did not occur. |
| `submitted_no_output` | Submission may have happened, but no text, reasoning or tool output was observed. This is not permission to replay an ambiguous request. |
| `partial_response` | Text, reasoning or tool-call fragments were observed. Adapters track incomplete tool fragments before emitting an assembled call. |
| `response_complete` | The provider response finished; subsequent tool execution has its own ownership and state. |
| `tool_not_dispatched` | Validation failed before calling the tool owner. |
| `tool_dispatched` | Execution was handed to the owner, or conservatively journaled as possibly dispatched before a crash. A result is not yet established. |
| `tool_completed` | A successful result was recorded. |

Streaming adapters assemble tool fragments by index and emit calls only at a
complete tool-call finish frame. Truncation, malformed SSE and a missing finish
frame are errors; repeated finish frames do not emit the same assembled call
twice. Interrupted text is retained as partial history and is not presented as
completed work. Neither ordinary request retries nor a later successful model
request re-execute already recorded tool results.

The compatibility fallback is limited to a local `NotImplementedError` from an
unimplemented streaming interface before output. When permitted by the caller,
it invokes `chat()` on the same provider, with a separate request identity and
budget charge. Transport errors, authentication failures, refusals, remote schema
errors and partially streamed output do not trigger this fallback. No automatic
provider switching is part of this engine.

## Decisions and budgets

`AdaptiveRecoveryEngine.decide(RecoveryContext)` returns a `RecoveryDecision`
containing the action, delay, retry limit, reason, approval requirement and
whether repetition is safe. Supported actions are `retry`, `backoff`,
`compact_context`, `reduce_tool_output`, `refresh_capabilities`, `reconcile`,
`request_user_intervention`, `fail` and `stop`. A proposed intervention or
reconciliation action emits a native event and stops automatic recovery; it is
not an automatic approval grant or a claim that an uncertain mutation was
reconciled. For a remote unsupported-capability failure, a real provider can
refresh its metadata once with `BaseProvider.refresh_capabilities()`: a read-only
`GET /models` against the same configured endpoint and credentials, with no
redirect following, no retry and a ten-second request bound. It does not use
`list_models()` or a cached catalogue fallback. Only validated capabilities for
the selected model are updated; missing or ambiguous model entries return
`unavailable` and revoke cached claims for that model. Absent capability fields
remain unknown. Scripted/non-HTTP providers without a transport/header contract
return `unavailable` without creating a network client. The original generation
request stops after the refresh and requires a reviewed next request. This is
metadata inspection, not another model-generation call.

Configure the limits under `agent.recovery` in the existing configuration:

```yaml
agent:
  recovery:
    request_retries: 2
    run_retries: 6
    max_requests: 100
    max_elapsed: 900
    base_delay: 0.25
    max_delay: 30
    max_tokens: null
    max_cost: null
    repeated_tool_failures: 3
```

These are the defaults. `request_retries` counts additional attempts for one
logical model request, so 2 permits at most 3 attempts. `run_retries` bounds
additional attempts across the run, including context adjustment and the local
interface fallback. `max_requests` counts actual model attempts and summary
requests. The existing iteration limit still applies independently.
`max_elapsed`, `base_delay` and `max_delay` use seconds. Optional `max_tokens`
and `max_cost` bound aggregate reported usage in USD. Non-USD cost reports do not
establish a USD spending cap. Verified USD price metadata can supply estimates.
A new run receives a fresh resource budget; restoring a session does not promise
to reconstruct a cross-run billing cap.

Eligible transient failures use capped exponential backoff with jitter:
`min(max_delay, base_delay * 2**attempt) * uniform(0.5, 1.0)`. A valid numeric or
HTTP-date `Retry-After` is a lower bound. If honoring it would exceed the allowed
wait or elapsed budget, recovery requests intervention instead of retrying early.
Backoff checks cancellation frequently. Coding retries compare the live policy
with the original policy before submitting another request; tightened permissions
stop the attempt. Tool owners recheck permissions before execution.

For model and context-summary requests, the host reserves estimated input tokens and narrows
the requested output allowance against the remaining token budget. A monetary
reservation requires available input/output prices; missing prices stop a
cost-capped request. Each completed/failed attempt records its available usage.
Unknown usage prevents a subsequent request under an active token or monetary
cap. Context-compaction requests enter the same request and usage ledger.

These controls are not a provider-side spending guarantee. Input tokens are
estimated locally, providers may report usage only after work is billed, cost
may be estimated from metadata, and cancellation cannot reverse completed work
or charges. Compaction retains its existing summary-output configuration, narrowed
by the remaining resource allowance.
Elapsed-time checks are cooperative: synchronous handlers, cancellation-resistant
owners and OS processes cannot be forcibly preempted by a Python polling loop.
The host provides no new OS sandbox or distributed transaction coordinator.

## Tool identities and uncertain side effects

Tools are classified as pure reads, idempotent state inspections, conditional
writes, non-idempotent mutations, external side effects or unknown. The actual
execution registry is used when resolving semantics. Third-party MCP, plugin and
skill tools remain unknown regardless of a familiar tool name or an untrusted
read-only annotation. Shell commands and test commands are treated as potentially
external side effects.

Each tool attempt receives a host UUID, tool-call ID, normalized operation
fingerprint, side-effect classification, phase and outcome. The journal lives in
`Message.execution`; `to_dict()` excludes it from provider context. Coding-session
checkpoints persist intent before dispatch. A recorded call ID is not executed
again. An unresolved operation with the same fingerprint blocks repetition even
if the model supplies another call ID. Identical known failures are bounded by
the configured failure threshold.

Conditional file edits retain existing expected-content hashes and workspace
checks. The host does not invent an idempotency header for shell, Git, payment-like
or MCP operations whose contracts do not support one. There is no generic
exactly-once guarantee and no generic remote reconciliation adapter. Unknown
outcomes require operator/tool-specific state inspection before deciding whether
a new operation is appropriate. They block verified coding completion and further
side-effectful coding tools. Opening an old session never replays tools; a legacy
assistant tool call with no result is conservatively recorded as outcome-unknown.

## Evidence-based completion

Coding lifecycle states are `planning`, `implementing`, `verifying`, `repairing`,
`reviewing`, `completed_verified`, `completed_unverified`, `blocked`, `failed` and
`cancelled`. Existing statuses remain readable: verified/unverified completion
maps to legacy `completed`, and `repairing` maps to `correcting`. Classification
and lifecycle details are additive snapshot/event fields; an old `completed`
record does not retroactively establish verified completion.

A final model answer is a completion claim. The coding completion gate checks
host-issued evidence IDs, real execution, recorded successful exit codes,
current workspace revision and content hash, complete workspace observation,
plan acceptance criteria, required quality gates and unresolved verification.
The fixed in-process AST benchmark evaluator is explicitly identified as an
in-process backend; it does not fabricate a shell exit code. Stale evidence,
incomplete criteria, a different green command after an unresolved failure, or
an uncertain tool outcome prevents verified completion.

Ask/plan modes require no build or test command. An ordinary answer or a coding
run without verified requirements can finish as `completed_unverified`; this is
visible in native completion metadata. File changes need current evidence to
pass the coding completion gate. Repair is bounded to three failed verification
cycles by default, and an identical failed command/cwd/workspace hash is not
executed again with the same workspace hash. A changed workspace permits another
bounded cycle. This bounds repeated repairs without treating repeated model prose
as progress.

## Events and validation

Recovery extends `AgentEventType` with `recovery` and uses existing
`model_request_started`, `model_request_finished`, usage, tool, task-state and
terminal events. Metadata records failure kind, request phase, safe diagnostic
identifiers, attempts, selected action, delay, retry eligibility, repeat safety,
budget snapshot and reconciliation state. Terminal coding events include the
completion classification. Cockpit extension-event projection and session storage
consume the same events; no separate recovery journal/event bus is introduced.
`run()` callers can receive these through `on_event`; `run_stream()` yields them
normally.

Focused checks use scripted providers/tools and `httpx.MockTransport`:

```sh
.venv/bin/python -m pytest -q \
  tests/test_provider_failures.py tests/test_recovery_engine.py tests/test_completion_controller.py \
  tests/test_request_metrics.py tests/test_agent_loop.py \
  tests/test_phase2_scripted_benchmark.py tests/test_phase2_recovery_report.py \
  tests/benchmarks/test_security.py
```

The provider cases cover rate limiting/Retry-After, overload, authentication,
permissions, refusals, context/schema errors, pre-submission connection failures,
partial SSE/tool assembly, cleanup failures and capability-refresh isolation
(including one-read bounds, absent metadata, credential denials, redirects and
cancellation). Completion cases exercise fresh
and stale evidence, failed tests, bounded repair, incomplete acceptance criteria
and old session records. Paired scripted fault probes measure both safe recovery
and forbidden retries/effect replays. The frozen Phase 1 benchmark suite provides
a separate before/after regression control. Offline success demonstrates those
specific contracts; it does not establish live-provider reliability, monetary
savings or universal performance improvement.
