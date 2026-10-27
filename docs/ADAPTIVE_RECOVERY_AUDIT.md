# Adaptive recovery runtime audit — Phase 2

Audit date: 2026-10-27. The baseline is the existing Phase 1 working tree on
`cf7af2fa5dd1ed1ab756376c5352b6fd2ec3b7f0`, including uncommitted Phase 1 work.
It was measured before Phase 2 runtime edits. The matching source fingerprint is
`3da2bb25cfcba6f38c1bf4faa9ed1326db178927e8d175424b13d729988e4c12`;
[raw baseline](Evidence/Recovery/20261027/baseline/report.json) records identical
before/after fingerprints. No original changes were reset or overwritten.

## Baseline boundaries

Both entry points use the selected provider and session-scoped tools. Only the
coding path supplies `CodingRunControl` and the guarded `CodingToolFilter`.
The runtime must preserve that authority; a recovery recommendation grants no
permission by itself.

| Boundary | `Agent.run()` baseline | `Agent.run_stream()` baseline |
| --- | --- | --- |
| Request preparation | Plugin-preprocessed user message is appended. System/environment prompt, filtered schemas, capability-based input budget, archive-preserving compaction, and model-request plugin hook precede `provider.chat()`. | Coding checkpoints and queued messages precede each iteration. Coding history is repaired/redacted; coding mode instructions, task state, live schemas, capability checks, compaction and request budget hook precede `provider.chat_stream()`. General chats retain plugin hooks. |
| Submission | `chat()` awaits one request; no native request identity/event or retry budget in this path. Local preparation failures precede submission but are not typed. | `MODEL_REQUEST_STARTED` is emitted before invoking the provider. This is host intent, not proof of provider acceptance. UUID, iteration, provider/model and attempt index support accounting. |
| Streaming lifecycle | No streaming; returned `Message` is appended only after the complete await and response hook. | Deltas/usage become visible immediately. Cumulative usage is merged. Provider adapters assemble indexed name/argument fragments and emit calls only on a tool-call finish frame; truncation raises. Agent collects returned complete calls and only dispatches after the stream finishes. |
| Partial response | Await exceptions reach the error plugin then propagate; absence of a returned message does not prove no submission or no charge. | `received_chunk` includes usage-only frames. Text/reasoning deltas are emitted before assistant history append. A later exception emits failed request metrics then ERROR, without replay. The desktop bridge separately checkpoints partial text. |
| Compatibility fallback | None. | Only `NotImplementedError` before any received chunk and an allowed fallback policy enters the same provider's `chat()` interface, with a fresh request ID and parent identity. This must never be confused with infrastructure, auth or policy recovery. |
| Tool assembly / arguments | Model-supplied complete calls are passed to the selected registry/filter. Callback argument parsing swallowed malformed JSON, though registry/filter execution rejects it. | Agent extends complete tool-call lists from provider chunks. Event argument parsing swallowed malformed JSON, but execution rejects it. Partial argument bytes remain provider-owned; unfinished calls must never dispatch. |
| Execution owner | Session filter, if supplied, otherwise `ToolRegistry.execute()`. Plugins observe calls/results. | Session filter, if supplied, otherwise registry. Coding plugins cannot bypass the coding filter. Agent owns sequential dispatch and message/result event append; filter owns permission enforcement and actual work. |
| Permission checkpoint | `SessionToolFilter` checks enabled tools and MCP server policy at execution. No coding run control parameter. | Run checkpoint before dispatch; coding filter rechecks mode, enabled tools, exclusions, paths, approvals and live policy immediately before registry invocation. Shell/network/Git/MCP actions have separate host policy checks. |
| Cancellation | Await cancellation propagates; no explicit cancellation argument or backoff. | Run checkpoints around preparation, frames and each tool; optional cancellation event checked at loop/frame/dispatch boundaries. `CancelledError` closes the request and propagates. Coding history repair inserts missing-result markers and never reruns tools. |
| Failure counters | Maximum iterations only; generic registry exceptions become error results. | Coding filter counts normalized name/argument fingerprints, blocks after three identical failures, and clears a key on successful result. Agent has a bounded coding iteration loop. There is no centralized per-request/run recovery budget. |
| Completion | No tool calls immediately returns model content. Iteration exhaustion returns text. Neither verifies coding evidence. | No tool calls immediately emits DONE. Coding loop exhaustion emits ERROR and marks blocked. General-chat loop exhaustion emits DONE with an incomplete message. The UI bridge independently runs optional quality gates and the completion gate before publishing a successful final status; direct runtime users can observe premature DONE. |
| Persistence | Session/messages mutate in memory; caller owns durable saves. | Native events feed existing `CodingSessionStore`/SQLite through the bridge or harness observer. Bridge checkpoints event-first snapshots, partial output, transcript, file journal, plan, test evidence and aggregate metrics. Startup recovery marks active records interrupted without dispatching tools. |
| Error propagation | Provider exception calls plugin error hook and is re-raised. Registry error results become tool messages and the model may continue. | Request start/finish, ERROR, tool error/result and state events use `AgentEvent`. Coding sanitization applies at event and history boundaries. Provider bodies previously appeared in generic exception text. Missing tool outcomes and ordinary tool errors were not typed distinctly. |

## Outcome distinctions that must remain explicit

1. **Known not submitted:** local serialization/validation/capability failure or
   documented connection failure before sending. A request-start event alone
   does not change this into confirmed submission.
2. **Submitted with no visible response:** HTTP rejection, read timeout or
   connection loss after dispatch. It can incur cost even with no frame; a lack
   of text is not proof of safe replay.
3. **Partial response:** text, reasoning or tool-call fragments observed by the
   adapter/host. Do not silently discard and repeat. Complete calls assembled
   before a later stream failure still must not dispatch from an incomplete
   response.
4. **Tool dispatched, no recorded result:** timeout, cancellation, process crash
   or persistence failure after invocation. Shell and external MCP calls can
   have completed or continued remotely; ordinary retry is unsafe. Restore
   should surface reconciliation-required state, not replay the transcript.
5. **Tool confirmed completed:** a recorded successful result with attempt
   identity. Hash-checked file writes and revision-bound verification evidence
   have stronger confirmation than generic external responses. Do not repeat
   a confirmed mutation solely because a later model request failed.

## Existing systems to extend

- `CodingToolFilter` is authoritative for policy, approval, hash checks and tool
  execution; unknown MCP metadata cannot grant a read-only or idempotent status.
- `run_tests()` records real exit codes, command, workspace revision/digest and
  evidence ID. Source refresh invalidates stale evidence; plan completion and
  required quality gates consume the evidence. Retain those checks.
- `CodingSessionStore` accepts additive native event metadata and JSON snapshots.
  Preserve existing status values and map richer completion classifications
  into additive fields rather than rewriting old records.
- `CodingBridge`, cockpit projection and `RunMetrics` consume the same events.
  Retry decisions, delays, safe-repeat status and final classification belong
  in that stream, not a separate recovery log with different semantics.
- Existing checkpoints protect file intent and historical state. They do not
  prove remote operation outcomes, OS isolation, distributed exactly-once
  behavior or a safe shell replay.

## Baseline validation

Executed before Phase 2 edits:

```sh
.venv/bin/python -m firik_agent.benchmarks bench --suite offline-smoke --repeat 3 --output docs/Evidence/Recovery/20261027/baseline
```

All 60 objective fixture checks passed. Those checks intentionally include safe
failures: only 30/60 tasks completed with verified objective state; 6 of 36 native
completion claims were false. There were 105 model requests, 51 native tool-start events,
zero infrastructure retries and zero safety violations. Mean wall time was
101.61 ms/trial. Usage is synthetic or unknown; monetary cost is unmeasured.

The frozen suite contains error-stop fixtures, not fault-then-success replay
scripts. Its rate-limit exception explicitly disallows retry and lacks normalized
HTTP metadata. Consequently it is useful for regression/completion comparisons,
but cannot by itself estimate recovery success on eligible transient failures.
Separate scripted provider/tool tests must establish recovery behavior and
no-bypass invariants. More retries are not a performance improvement by
construction.

## Phase 2 integration seams

The implemented paths extend the baseline at these ownership boundaries:

- `core/failures.py` normalizes typed failure kind, request phase, bounded
  provider code/request identity, HTTP status and Retry-After. Provider bodies
  are inspected locally for classification and discarded. Phase and policy
  classification survive normalization.
- `core/recovery.py` takes explicit context and returns a bounded decision.
  Security denial, authentication resolution, partial-output reconciliation and
  unknown tool outcomes remain separate from transient recovery. The engine
  never grants host permissions or switches providers.
- `Agent._request_with_recovery()` is shared by `run()` and `run_stream()`.
  Each actual provider request has a distinct identity and native usage
  record; the caller retains responsibility for durable event storage. Checkpoints, live policy checks, external budget hooks and
  interruptible backoff apply at attempt boundaries. A failed request does not
  discard partial output and silently restart it.
- Tool intents/results carry additive host-only `Message.execution` metadata.
  Attempt IDs, canonical operation fingerprints and side-effect risk are not
  sent in `Message.to_dict()` to the model. The coding event observer persists
  intent before dispatch resumes. General desktop chat also saves session context
  at tool-intent/result boundaries before advancing the generator; callers using
  the bare API must supply their own durable event/session sink. Session recovery
  adds an unknown outcome
  marker for a missing result and never dispatches historical tool calls.
- `core/completion.py` adds lifecycle and bounded repair state while the existing
  coding filter continues to own revision/digest evidence and quality gates.
  No-tool final responses are checked before successful DONE; model prose
  cannot manufacture an executed command, evidence ID or acceptance result.
- Existing persisted status values remain compatible. Rich lifecycle and final
  `completed_verified`/`completed_unverified`/blocked/failed/cancelled
  classification is additive metadata, projected through the existing native
  event stream and cockpit. Read-only inspection does not require tests.

These seams do not provide a remote exactly-once guarantee. Unknown external
outcomes require explicit state inspection or user resolution; an idempotency
key cannot be invented for an API that does not enforce it. Shell/Git/payment
operations and third-party MCP annotations never establish safe repetition by
themselves. Retry authorization is evidence about a specific boundary, not a
blanket permission to repeat tools or change the user's security policy.
