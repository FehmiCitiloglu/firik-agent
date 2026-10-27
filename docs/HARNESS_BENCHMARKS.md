# Harness Benchmark Lab — Phase 1

The lab exercises the existing `Agent.run_stream`, `CodingRunControl`, guarded
`CodingToolFilter`, native events, `RunMetrics`, `CodingSessionStore`, SQLite and
cockpit projection. It does not maintain a competing event journal or billing
counter. No new dependency, database migration or benchmark package installation
is required. Existing uncommitted observability work is retained.

## Commands

```sh
firik harness doctor
firik harness list --suite offline-smoke
firik harness bench --suite offline-smoke --repeat 3 --output /tmp/harness-a
firik harness bench --suite offline-smoke --repeat 3 --output /tmp/harness-b
firik harness compare --baseline /tmp/harness-a/report.json --candidate /tmp/harness-b/report.json --output /tmp/harness-comparison
firik harness list --suite coding --json
```

`firik-agent harness` and `python -m firik_agent.benchmarks` expose the same
commands. The previous `benchmark list/run/compare` routes and positional
comparison paths remain available. `list` without a suite retains the original
seven-task catalog; `bench` defaults to the expanded 20-task offline suite.
`--task ID` selects tasks; `--manifest PATH` adds a bounded declarative offline
task. No output directory means a fresh `./harness-results/<unique ID>` directory.
Exit codes: 0 expected checks passed; 1 unexpected fixture failure or comparison
regression; 2 invalid input/setup/output; 130 keyboard interruption. Existing
output files and symlink targets are never overwritten.

`doctor`, listing, offline evaluation and comparison dispatch before credential
setup. The doctor does not probe live endpoints or read OS credentials. The
coding cockpit's **Etkinlik → Benchmark raporu** button reads an existing JSON
report and shows a compact, validated summary without starting any work.

## Reproducible execution and contracts

`models.py` defines versioned strict suite/task/fixture/evaluator/configuration,
model/provider, trial, tool trace, recovery, token, cost, verification and terminal
outcome records. Each execution result includes a validated `record`, its native
run/trial/session IDs, task and configuration fingerprints, actual starting Git
SHA, evaluator version/hash, effective permissions/budgets and native trace.
JSON uses `schema_version: 1`; unsupported or corrupt formats fail closed.

Each trial creates a disposable Git repository and SQLite database; it never
checks out, cleans, edits or resets the user's repository. Git commits use fixed
identity/date/content, SHA-1 object format and disabled hooks, signing, templates
and external config. `fixtures.fixture_revision()` independently computes the
same immutable starting commit. Concurrent trials have distinct roots and IDs.
Source fingerprints are captured before and after a suite; comparison rejects
runs whose installed Python sources changed during evaluation. Run from a fixed
installed checkout; a fingerprint is provenance evidence, not an authenticity
signature or a substitute for protecting the host process.

The host imposes time, request, output and provider-reported token limits.
Compaction and fallback are explicitly disabled in these bounded lab profiles.
The shared telemetry also measures compaction/retry events from supported native
runs. Configure a profile with `--profile PATH` using `HarnessConfiguration` JSON;
unsupported executable/network permissions and compaction are rejected.

## Offline mode

Twenty miniature repositories exercise guarded edits, read-only inspection,
permission denial, traversal, partial output, cancellation, unknown usage,
tool failures, malformed arguments, incomplete streamed calls, rate limits,
timeouts, context exhaustion, repeated tool-call loops, workspace conflicts,
interruption, false completion, evaluator tampering and duplicate usage frames.
Every fixture checks independent source/AST state, terminal behavior, permission
outcomes and native persistence. Expected safe failure is a passing harness
check, not a successfully completed coding task. Dedicated provider-parser tests
also cover real SSE truncation and duplicate finish frames.

Fixtures are untrusted data. Only bounded guarded file/plan tools are available;
no fixture Python, shell, MCP, plugin, installer or subagent is executed. Network
tools are denied and the replay provider has no HTTP client. `builtin:verify`
is a fixed host-controlled source/AST evaluator, not an executable fixture
command. Its expected files exist only in host memory outside the agent-visible
workspace. An agent cannot edit its oracle through its available tools.
Temporary directories are not an OS sandbox. Arbitrary executable benchmark
commands remain unavailable until an appropriate sandbox or explicit execution
authorization is implemented.

## Opt-in real model mode

The versioned `coding_suite()` contains **40 individually specified small
canonical-source tasks** across bug fixing, refactoring, features, testing,
build/CI repair and security operations. Each has concrete initial files, a
pinned starting commit and independently held expected source bytes. Oracles
reject initial broken sources and accept the curated corrections. This dataset
measures these precise transformations; it does not represent large production
repositories, semantic test coverage or successful live model evaluations.
Whitespace-equivalent alternative solutions may fail its exact-source oracle.
The catalog's build/test scripts are inspected as data, never executed.

No model API request occurs without explicit opt-in. Example (may incur provider
charges; not executed during implementation):

```sh
firik harness bench --suite coding --repeat 3 --allow-live --provider firik --model MODEL_ID --config config.yaml --task bug-inclusive-bound --output /tmp/harness-live
```

Only the selected existing configured provider is constructed after validating
authorization and the suite. No model catalog lookup or third-party installation
is performed. Live model traffic is the explicit exception to offline network
denial; repository tools still cannot access the network. A provider wrapper
bounds request count and requested output and deduplicates cumulative usage with
the shared reducer. Unknown input tokens cannot establish a hard total-token or
monetary spending cap. Reported-token limits can stop a stream after usage is
received; they cannot reverse already billed work. Request/time/output bounds
remain in force. No paid model run is included in the implementation evidence.

## Metrics and A/B interpretation

Reports distinguish exact local counts/timings, synthetic offline usage,
provider-reported token/cost evidence, verified-price estimates and unknown
measurements. Usage frames replace prior snapshots for the same native request
identity, including final usage/finish events. Missing/failed/cancelled usage,
cache counts and charges remain `null`; partial reported sums retain coverage.
Cost provenance accompanies aggregates and estimated amounts are labeled.

Metrics include first-attempt-per-task pass@1, task success, verified completions,
false completion among completion claims, completion and P50/P95 latency,
requests/tools/errors, input/output/cached tokens, compaction overhead,
cost/task/successful task, retries/tool recovery, repeated errors, safety
violations, crashes and interruptions. Harness fixture pass rate is separate
from completed task success. Native DONE is the observable completion claim;
prose is not used to infer a successful evaluation.

Comparison validates native outcomes and recomputes summaries from result rows.
It pairs by task ID and repetition and controls dataset, provider/model/endpoint,
starting revision, time/token/output budgets, temperature, permissions and oracle
version. Different explicit profile IDs/request policies and source revisions
can be compared with those controls fixed. Different budgets are rejected.
Per-task success/efficiency deltas and regressions are exported side by side.
Deterministic task-cluster bootstrap intervals use 2000 resamples and seed
20261027 when at least five tasks are present; fewer tasks yield null intervals.
Repeats stay in their task cluster. These are descriptive dataset intervals, not
universal rankings. Same-source A/B is a repeatability control, not proof of a
speedup. `performance_claim` remains null.

## Future phases

Freeze the suite, evaluator and control fingerprints before changing recovery,
context policy, routing or tool behavior. Run baseline/candidate over the same
model, provider, revisions, permissions and budgets with three or more repeats.
Require no safety/false-completion regression; examine per-task failures before
using success and cost/latency improvements to accept a policy. Add production
repositories and semantic evaluators only behind an appropriate execution
boundary, version the dataset, and retain raw redacted native evidence. The lab
measures a later recovery/adaptation engine; it does not implement that engine.

Old SQLite sessions continue using existing JSON fields and additive events.
Opening histories or a benchmark report never replays tools. Preserve databases
and evidence when reverting source/package versions.
