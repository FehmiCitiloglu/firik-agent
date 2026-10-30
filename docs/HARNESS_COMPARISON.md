# Harness comparisons and conservative recommendations

Phase 6 adds `firik_agent.harness.comparison` alongside the existing Harness
Benchmark Lab. It reuses the versioned `BenchmarkSuite`, `TaskSpecification`,
canonical Git fixture revision, source/AST evaluator and installed-source
fingerprint. Existing `harness bench`, `harness compare` and their report format
remain available. The new report kind is `harness-backend-comparison`.

## Safe offline experiment

The following creates a plan from the existing curated task catalog. The default
factory runs the actual native runtime and guarded Lab tools through the native
adapter. Its scripted provider consumes canonical expected changes as synthetic
replay data: success is regression evidence, never real model ability.

```sh
python - <<'PY'
from pathlib import Path
from firik_agent.benchmarks.coding import coding_suite
from firik_agent.harness.comparison import ComparativeConfiguration, ComparisonPlan

suite = coding_suite()
suite = suite.model_copy(update={"tasks": suite.tasks[:5]})
plan = ComparisonPlan(suite=suite, repetitions=3, configurations=[
    ComparativeConfiguration(id="native-a", provider="replay", model="scripted-v1", model_revision="scripted-v1"),
    ComparativeConfiguration(id="native-b", provider="replay", model="scripted-v1", model_revision="scripted-v1"),
])
with Path("/tmp/firik-comparison-plan.json").open("x") as stream:
    stream.write(plan.model_dump_json(indent=2))
PY
firik harness experiment --plan /tmp/firik-comparison-plan.json --output /tmp/firik-comparison-report.json
firik harness recommend --report /tmp/firik-comparison-report.json --objective balanced
```

The identical configurations above establish repeatability. They do not
establish improvement. Report output paths are created exclusively, and existing
reports are not overwritten. The desktop Harness view can load a validated
report or explicitly start a plan in separate disposable repositories.

Recommendations intentionally refuse synthetic replay as production performance
evidence. The resulting fallback keeps the currently selected backend and
permissions. It does not silently select Native during an active task.

## Controlled execution

Every plan matches provider, requested model, declared model revision and
non-secret provider settings across configurations. Task instructions, exact
starting commit, host-held evaluator, time budget, requested token limit and
permissions come from the shared plan. Each task/configuration/repetition gets
a new repository and session. No candidate patch is applied to the working tree.

The currently supported comparison configuration varies backend and existing
host profile. Context strategy is `profile`, tools are `guarded-source`,
verification is `host-source-ast` and concurrency is one. Unsupported alternative
values fail validation rather than implying an implemented strategy.

`shell.safe: ask` exists for the native fixed `builtin:verify` evaluator. The
trusted replay factory approves only that exact tool and arguments. The Lab's
filter rejects every other test command before dispatch; repository programs
are never executed. Network tools, MCP, secrets, subagents, arbitrary shell and
Git mutations cannot be authorized by a source-evaluator plan. An explicit
`shell.safe: deny` remains denied and may prevent native completion evidence.
This fixed host evaluator requires no user approval dialog or model request.

Native permission ownership, provider setup and tooling are injected host
dependencies. A live embedding may call:

```python
report = await run_comparison(plan, backend_factory=authorized_host_factory, allow_paid=True)
```

The factory receives `(configuration, workspace_root, task_specification, plan)`
and returns a backend bound to an authorized runtime. A plan cannot supply an
import path, executable hook or credentials. It cannot construct a provider.
Every non-replay provider requires explicit authorization covering all selected
configurations, even if a manifest calls it free or local. The shipped CLI does
not reconstruct live native credentials: without a host-bound runtime, a live
native experiment produces explicit unavailable/failed rows. Tool-free Pi and
the unavailable Codex adapter cannot satisfy native coding permissions and are
not advertised as operational editing benchmark configurations.

Backend identity, session/project/workspace identity, every event's
backend/session/run identity and final result identity are checked. A model
change during recovery/fallback makes the run incomparable, even if the last
model name matches. Observed provider identities must also match, so a fallback
provider with the same model ID cannot establish equivalence. An unknown actual
provider makes a trial incomparable. A backend which supplies a conflicting actual model
revision fails matching. An unknown actual revision remains unknown; it is not
verified from the requested string. Availability and capability checks fail
clearly before task submission when the requested operations are unsupported.

Unknown token counts and cost remain null. Event usage is not summed again;
the backend's normalized request-aware usage reducer owns those snapshots.
Duplicate event identities are ignored only when their normalized payloads
match. Conflicting duplicates stop the experiment. Event history and duplicate
tracking share the configured bound; overflow stops the trial and disqualifies
comparison instead of silently discarding execution evidence.

Reported token totals cannot establish a pre-billing cap. A hard requested cap
requires an affirmative, verified backend capability; the replay factory can
enforce it before emitting a synthetic turn. A real provider's reported totals
cannot undo billing. Time limits and cancellation remain active independently.
Effective output/context/request limits can additionally differ by host profile;
inspect the native request trace's context inspector when comparing profiles.
Those profile differences are not evidence of equivalent internal algorithms.

## Failure and cleanup evidence

Startup/configuration errors, crashes, interrupted streams, timeouts, failed
verification and backend errors produce failed trial rows with redacted messages
and backend error details. Partial run inspection preserves available usage and
errors. Cleanup requests cancellation when necessary and closes each initialized
session. Evaluator and workspace-removal failures do not skip session cleanup.
Repeated caller cancellation cannot interrupt the protected cleanup sequence.

If process shutdown or workspace removal cannot be confirmed, the report
retains the workspace path and diagnostic state, and the trial cannot succeed.
No uncertain operation is automatically replayed. The owning backend remains
responsible for startup-failure cleanup before returning a session. Process
groups and disposable directories are not OS confinement; backend-specific
security limitations continue to apply.

Reports retain actual backend version metadata, source fingerprint, plan/task/
configuration/evaluator hashes, pinned revisions, per-trial model identity,
independent verification, usage coverage and execution trace. Validation
recomputes summaries and rejects contradictory trial outcomes. Hashes provide
internal provenance, not authenticity against a malicious report author.

Tool failures and terminal diagnostics have separate metrics. `tool_errors`
counts distinct failed tool invocation IDs; `tool_error_rate` divides that count
by observed tool calls and remains unknown when no tool call was observed.
`terminal_error_rate` (also exposed as the backward-compatible `error_rate`)
counts trials with normalized runtime/startup/cleanup diagnostics divided by
all trials. Recoverable tool failures alone do not become terminal errors.
Exact duplicate normalized diagnostic records count once, while distinct
backend details remain available. `trials_with_recovery_rate` counts trials
with at least one recovery event divided by all trials; it does not assert that
recovery succeeded. Every report carries these metric definitions.

## Recommendations

Objectives are `maximize_success`, `minimize_cost`, `minimize_latency`,
`balanced` and `local_only`. All are advisory. Automatic selection and applying
changes remain disabled.

Default minimum evidence is five distinct tasks and three repeats. Candidates
must have comparable identities, unchanged sources, clean teardown, verified
actual model revision, no false completions and at least 80% verified success.
Installed runtime sources must still match the measured source fingerprint;
otherwise the recommendation requests new evidence instead of ranking stale code.
Only measured configurations are eligible. A cost objective additionally
requires complete cost coverage with supported provenance. Missing cost cannot
be treated as zero. A manifest's locality label does not establish local model
execution. The backend must supply operational locality evidence.

Success objectives prioritize measured completion and descriptive task-level
uncertainty; latency breaks ties. Cost and latency objectives operate only after
the same safety, verification and success gates. Verification depth, tools and
permission controls are never weakened to improve a score. The task interval
uses tasks which passed every repetition, treating correlated repeats
conservatively. It is not a production reliability guarantee.

## Reproducible performance evidence

```sh
python scripts/benchmark_harness_backends.py \
  --output docs/Evidence/Harness/20261029/phase6 \
  --tasks 40 --repeat 3 --overhead-tasks 5 --overhead-repeat 5
```

This executes 120 current native-adapter trials and 25 matched samples each of
the current original native loop and its lifecycle wrapper. Pair order
alternates. Fixture setup and the final independent evaluator are excluded
equally from overhead timing; session initialization, event streaming and close
are included. Complete comparison trial duration also includes fixture setup
and cleanup. All source and syntax outcomes are independently checked.

The script records raw timings and source changes. Earlier recovery-only or
context-only snapshots are not recreated by disabling safety mechanisms.
Historical phase ablations and unsupported/uninstalled external runtimes are
explicitly recorded as unexecuted. No live Pi/Codex comparison, external model
quality result, provider billing result or production speedup is implied.
