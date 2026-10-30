# Phase 6 security analysis

This analysis covers the new harness boundary and its integration with the
existing coding runtime. It is a design and regression review, not a claim of
whole-machine confinement or an exhaustive security audit of prior phases.
The [interface audit](HARNESS_BACKEND_RESEARCH.md) records official sources;
the [architecture guide](PLUGGABLE_HARNESSES.md) describes authority ownership.

## Protected assets and trust decisions

The host owns project identity, workspace scope, permissions, explicit approvals,
secret handling, sandbox requirements, cancellation, verification and artifact
integration. Model output, repository instructions, external events and loaded
benchmark reports are untrusted data. User-controlled host configuration may
select a trusted executable; a backend or model response cannot do so. The
provider registry is a generation dependency and grants no execution authority.

Existing native shell/file/plugin/MCP tools retain their actual boundaries.
Wrapping them does not turn host execution into a sandbox. Native rejects a
whole-runtime sandbox request or `sandbox` tool mode, rather than silently
claiming that selected Docker command isolation covers all tools. An explicit
native permission map must match the bound run control's effective policy.

## Admission controls

| Failure or attack | Enforced behavior |
| --- | --- |
| Missing, disabled or incompatible external runtime | Explicit admission error; no fallback, installation, shell wrapper or synthetic success |
| Disabled external executable points to project code | Passive inventory does not execute it; version/compatibility remain unprobed |
| Passive import tries ambient credential discovery | No import-time dotenv load; native configuration/provider startup retains legacy dotenv support |
| External runtime attempts tool, extension or shell activity | Pi starts with these disabled; observed activity fails execution and closes the process |
| Pi requests a repository, home, MCP or native approval inheritance | Unsupported policy rejected before session execution |
| Undocumented Codex scope/approval/configuration mapping | Adapter unavailable; official protocol existence is insufficient evidence |
| Credential leakage through process environment | Explicit provider/key/live opt-in; allowlisted clean environment, private home/config/cwd; no installation login import |
| Output leaks the supplied Pi key | Exact supplied-key and structured secret redaction in events, results, diagnostics and CLI errors |
| Malformed, partial, oversized or endless external protocol | LF JSONL parsing, bounded frames/bytes/identities/messages/text, startup/task deadlines and failed outcome |
| Duplicate cumulative usage | Shared native reducer; Pi final-message deduplication; unknown values stay null |
| Live host permission revoked after an event | Native delivery acknowledgement prevents speculative continuation past the consumer's decision boundary |
| Repeated cancellation or dialog close during teardown | Shared shielded cleanup, one cancellation request, provider final accounting preserved; no terminal callback before native persistence/cleanup |
| Crash or unknown durable operation outcome | Original exception and durable intents preserved; no automatic execution replay or backend transfer |
| Mislabelled comparison backend/provider/model/revision | Observe backend/session/run identities and provider/model sets; mismatch disqualifies comparison |
| Benchmark tries executable fixture code or oracle tampering | Inert fixture tools; host-held exact-source/AST evaluator; built-in verification only; fresh isolated Git repositories |
| Benchmark cleanup/evaluator fails | Failure diagnostics retained; uncertain workspace retained for cleanup and success disqualified |
| Stored summary or recommendation is tampered/stale | Validate controlled fingerprints/outcome consistency, recompute aggregates, reject stale runtime source and insufficient evidence |
| Optimization chooses cheap but unsafe/untested work | No automatic selection; fixed safety/verification constraints and conservative evidence fallback |

Configuration, session, process and result identity checks do not authenticate
arbitrary Python injected by a host application. Backend factories are trusted
host dependencies; no import path, code or credential provider is accepted from
a benchmark manifest. Project preferences apply only before a task and cannot
transfer an active execution's state. The CLI's native replay factory also obeys
the native backend enablement setting.

## Cross-phase review and fixes

The native adapter preserves the previous phases' single runtime and persistent
event journal. Integration testing caught and corrected completed runs being
marked cancelled during disposal, event prefetch racing live permission
revocation, cancellation swallowing a durable crash, missing final request
accounting during cancellation, and repeated cancellation interrupting awaited
cleanup. Independent review also corrected sandbox-mode admission, requested
versus observed model identity, fallback-provider comparability, passive
execution of disabled backends and the replay enablement bypass.

Targeted regression areas include recovery versus streaming and durable intent
boundaries, context/profile/output budgets, subagent scope narrowing and worktree
review, native tool authorization, unknown-effect reconciliation, freshness of
completion evidence, duplicate usage, cancellation and UI lifecycle. Existing
checkpoint, durable resume and subagent integration controllers remain the
owners of their data; the harness does not reimplement or bypass them.

## Remaining failure modes and unverified boundaries

- Pi is trusted installed host software in an application-level tool-free mode.
  A malicious executable, runtime dependency or same-user process is not confined
  by a private directory/environment. Explicit prompt contents and the selected
  key are intentionally exposed to the authorized provider/runtime.
- POSIX process-group cleanup is best effort. Deliberate session escape, host
  SIGKILL/power loss and persistent cross-launch orphan recovery are unverified.
  Failed disposal retains session/temp identity and reports a cleanup error.
  Windows external dispatch is disabled because Job Object enforcement is absent.
- Native host tools, remote MCP and provider traffic have separate authority.
  Docker isolates selected commands only, and no running-daemon security test
  was added or claimed by this phase. Previous Phase 5 device/platform gaps
  continue to apply.
- External coding with Firik authorization, Codex dispatch, external durable
  resume and state transfer are unavailable. Tool-free Pi cannot run the coding
  fixture comparison. No paid or real provider execution is included in evidence.
- Reported token usage arrives after work may be billed. Native ordinary live
  and Pi modes reject hard pre-billing token caps; unknown costs are not zero.
  Pi price estimates are not treated as invoice evidence.
- Curated source replay knows the expected edit. It establishes runtime and
  evaluator regression behavior, not coding-model quality, general performance,
  legal compatibility of hypothetical redistributed dependencies or a universal
  best backend. No external dependency is bundled or installed.
- Temporary workspaces are not an OS sandbox or durable archival. Total disk
  quota, hostile same-user filesystem races and kernel/daemon vulnerabilities
  remain outside these guarantees.

The final implementation report binds actual test/benchmark receipts and any
desktop-environment failures to this source state; passing fake-process tests
does not establish live provider, power-loss or platform confinement coverage.
