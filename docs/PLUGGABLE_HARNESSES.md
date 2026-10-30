# Pluggable harness architecture and migration

Phase 6 adds an orchestration boundary without replacing Firik's coding runtime.
The default remains `native`; existing configuration and persistent sessions need
no migration. [Desktop and CLI guide](HARNESS_DESKTOP.md),
[comparison methodology](HARNESS_COMPARISON.md), and
[official interface audit](HARNESS_BACKEND_RESEARCH.md) describe supported use.

## Three independent authorities

| Component | Owns | Does not grant |
| --- | --- | --- |
| Model provider (`providers/`) | Authentication, inference, streaming, model capabilities, reported usage | Filesystem, shell, tools, approval or task completion authority |
| Harness (`harness/`) | Session/task lifecycle, planning/runtime orchestration, event/result normalization, cancellation | New host permissions or an interchangeable sandbox guarantee |
| Execution environment (`core/execution.py`) | Selected command backend, actual process isolation and resource controls | Whole-agent confinement, permission to expose MCP or integrate artifacts |

`HarnessBackend` is a typed structural protocol with metadata, capabilities,
configuration validation, session initialization, task execution, asynchronous
events, inspection, cancellation and explicit close. Backend-neutral records
represent sessions, tasks, execution policy, tools, usage, completion evidence,
errors and results. Capability values are true, false or unknown with evidence.
An unknown quantity remains null; error codes and relevant diagnostic details
survive normalization. Credentials are explicit runtime dependencies rather
than serializable configuration or discovery output.

## Native vertical slice

`Agent.run_stream` selects and pins the harness before dispatch. The native
adapter calls the original `_run_native_stream` implementation; it does not copy
its loop. Existing provider selection, context/compaction, tool registries, MCP,
run controls, live approvals, subagents, durable intents, checkpoint ownership,
quality gates and completion classification remain in that implementation.
Original `AgentEvent` instances still reach existing consumers and SQLite
journals, with additive `harness_backend` and observed `harness_model` metadata.
The non-streaming `Agent.run` loop is preserved and rejects incompatible harness
selection before any local command or model dispatch.

Event delivery is acknowledged before the native loop continues. This matters:
the consumer may revoke a permission or cancel in response to an event, and
prefetching could otherwise start another request before that decision applies.
The existing `RunMetrics` reducer supplies nullable usage and deduplicates native
cumulative request frames; the adapter does not add another billing counter.
Actual model identities come from the provider and observed request events,
never from the requested display label. Unknown revision remains unknown.

Only one task uses a bound native runtime at a time. Session identity is bound
to the backend instance and original coding session/project/workspace. No active
backend transfer or automatic switching is implemented. A coding run identity
cannot be executed twice through the same adapter. History/resume remains the
existing durable controller's responsibility, including reconciliation of
uncertain effects. Opening history or inspecting a harness never replays work.

## Admission and isolation

| Backend | Admitted scope | Security boundary |
| --- | --- | --- |
| Native | Existing Firik coding and chat flows | Existing host permission owner; host tools retain OS user authority; Docker isolates selected commands only |
| Pi | Exact audited version; explicitly authorized tool-free inference with no repository admission | Fresh private cwd/home/config; no tools/extensions/MCP/skills/context discovery; trusted host process, not OS sandbox |
| Codex | Explicitly unavailable adapter | Official interfaces exist; a complete enforced scope/configuration/approval mapping has not been validated |

Native rejects a whole-runtime sandbox requirement, unsupported hard token cap
and a policy that disagrees with the bound permission owner. Pi rejects all
workspace and tool admission and required sandbox/hard token caps. A disabled,
missing or incompatible external runtime fails explicitly; it never falls back
to host execution or changes harness silently. External output is untrusted and
cannot grant tools, approve actions, prove completion or apply a patch.

Pi's separately enabled CLI prompt path requires the exact credential
environment name and explicit live inference authorization. It imports no
existing Pi/Codex login, home configuration or Firik credential store. The
installed executable itself must be trusted; a private cwd and removed tools do
not confine malicious host software. POSIX process-group cleanup handles trusted
runtime failures, not deliberate session escapes. Windows external execution
remains unavailable until process-tree lifecycle enforcement is verified.

## Lifecycle and failure semantics

Event buffers, protocol frames, results, diagnostics, session counts and retained
run inspection state are bounded. Native backpressure preserves each event;
external overflow produces a failed result. Timeouts and cancellation stop the
owned task/process, and repeated cancellation cannot abort awaited cleanup.
Explicit close is idempotent and shares teardown. Terminal UI callbacks continue
to follow the original durable persistence and cleanup boundary.

External startup, malformed/truncated JSONL, incompatible version, unexpected
tool activity, disconnect, missing completion and process exits preserve a
structured error. No reconnect/replay is advertised for the external adapter.
Uncertain external effects must not be retried automatically. A backend's
completion event is separate from independently verified artifact success.

## Comparative execution and recommendations

The new runner reuses the Lab's pinned inert source fixtures, deterministic Git
revision creation and host-held source/AST evaluator. Each candidate gets an
independent temporary repository. Backend/model/revision/settings, task,
evaluator, permissions and budgets are controlled and observed identities are
validated. Unsupported combinations produce failed rows. No candidate tree is
integrated into a user's project. Teardown uncertainty retains the workspace
for controlled cleanup and disqualifies success.

The shipped default factory is synthetic native replay. It exercises the real
guarded runtime but receives canonical fixture edits, so it cannot establish
coding-model quality or a ranking of Pi/Codex. Live factories require explicit
host binding and per-experiment authorization; a plan alone cannot discover a
credential or load arbitrary Python. Current tool-free Pi cannot run these
coding comparisons because it cannot admit a repository or Firik tools.

Recommendations are advisory, validate stored evidence, recompute aggregates,
and reject insufficient, synthetic, mismatched or unsafe evidence. Unknown cost
cannot win a cost objective, and a manifest's local-only claim is not proof of
model locality. The optimizer never weakens verification, changes permission,
starts an extra model request or switches a running task.

## Migration and extension notes

No database schema or provider API changes are required. Optional configuration
is under `harness`, while old `agent.harness_profile` and project profile fields
retain their precedence. Native provider configuration stays in its existing
registry. External backend enablement is separate from installed/supported
availability. Project preferences apply only to the next task.

New backends must implement the protocol, document official versioned interfaces
and license/authentication requirements, prove their actual execution boundary,
and reject every policy they cannot enforce. Add fake-process lifecycle tests
before opt-in real runtime tests. Supporting an official API is necessary but
does not establish safe filesystem, shell, plugin, approval or credential scope.
