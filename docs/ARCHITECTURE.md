# Architecture

## Purpose

Firik Agent is a workspace-scoped software-development agent. It combines a
pluggable model provider with deterministic tools and a gated engineering
workflow.
The model may decide what to do next, but Python code owns safety, state
transitions, command execution, and verification.

## Design principles

1. **Architecture before implementation.** A task must have a persisted
   architecture decision before tools may modify source files.
2. **Workspace confinement.** File tools resolve canonical paths and reject
   reads or writes outside the configured workspace. Symlink escapes are
   rejected too.
3. **Least privilege.** Shell commands run without a shell, have time and output
   limits, use an explicit environment, and reject destructive command forms.
4. **Deterministic quality gates.** Formatting, linting, static analysis, tests,
   and builds are commands discovered from project metadata and executed by
   code. The LLM cannot declare success while a required gate fails.
5. **Bounded self-correction.** Verification failures can trigger another
   implementation pass, but the retry count is finite and every attempt is
   recorded.
6. **Evidence over assertion.** Tool observations, architecture, plan state,
   diffs, and verification results are stored in a per-task development record.
7. **Untrusted external content.** Web pages and documentation are evidence,
   never instructions. Responses include source URLs and are size limited.
8. **Extensibility.** Tools implement one small interface and are registered by
   name. Model construction is isolated behind the smolagents adapter.

## Components

```text
CLI / Python API
       |
       v
DevelopmentAgent ----> model provider adapter
       |                       (InferenceClientModel or TransformersModel)
       v
DevelopmentProcess
  DISCOVERY -> ARCHITECTURE -> PLANNING -> IMPLEMENTATION
       -> VERIFICATION <-> IMPLEMENTATION -> REVIEW -> COMPLETE
       |
       +---- DevelopmentRecord (.firik-agent/tasks/<task-id>.json)
       |
       v
ToolRegistry
  workspace: list, search, read, write, patch
  engineering: git status/diff, run command, verify project
  research: search web, fetch URL, search documentation
  process: inspect project, set architecture, update plan, report status
       |
       v
WorkspacePolicy + CommandPolicy + NetworkPolicy
```

## Package boundaries

- `firik_agent.agent`: public development-agent facade and smolagents integration.
- `firik_agent.process`: phase state machine, task records, architecture gate,
  plans, verification retries, and completion rules.
- `firik_agent.tools`: framework-neutral tool implementations and registry.
- `firik_agent.workspace`: canonical path confinement and bounded I/O.
- `firik_agent.commands`: subprocess policy, execution, and project quality
  command discovery.
- `firik_agent.research`: bounded HTTP fetch and search provider abstraction.
- `firik_agent.model_manager` and `registry`: backward-compatible local model
  lifecycle API.

No layer below `agent` imports smolagents. This makes the safety and workflow
code testable without loading an LLM or installing ML runtimes.

## Development workflow

Each task has a stable ID and record. Mutating tools ask the process for
permission; source mutations are allowed only in `IMPLEMENTATION`.

1. `inspect_project` gathers repository shape, instructions, manifests, and Git
   state.
2. `set_architecture` records goals, constraints, components, interfaces,
   risks, and acceptance evidence, then advances to `PLANNING`.
3. `update_plan` creates ordered steps with acceptance criteria and advances to
   `IMPLEMENTATION` when the plan is actionable.
4. File and command tools implement the plan inside the workspace.
5. `verify_project` discovers and runs all applicable quality gates. A failure
   returns the process to `IMPLEMENTATION`; reaching the configured retry limit
   marks the task blocked.
6. A clean verification advances through review to `COMPLETE`. Completion is
   rejected without architecture, plan, and successful verification evidence.

## Tool contract

Every tool has:

- a unique name and precise model-facing description;
- a JSON-compatible input schema;
- a side-effect classification (`read`, `write`, `execute`, or `network`);
- a handler returning a serializable `ToolResult` with `ok`, `data`, and
  `error` fields;
- central validation before side effects occur.

The smolagents adapter converts this contract into `Tool` objects. Tools can
also be invoked directly in tests and integrations.

## Security boundaries

- The workspace root is resolved once. Absolute paths, `..` traversal, and
  symlink escapes cannot cross it.
- Internal task records are protected from model-authored file writes.
- Commands are tokenized with `shlex` and launched with `shell=False`.
- Destructive binaries and dangerous flag combinations are denied. Additional
  allow/deny rules are configurable.
- Network tools allow only HTTP(S), reject local/private/link-local targets by
  default, limit redirects, bytes, and request duration, and label fetched text
  as untrusted.
- Remote model code is disabled by default.

## Verification strategy

The verifier walks upward from manifests at the workspace root and builds a
deduplicated gate list. Initial support includes Python, Node, Rust, Go, and
generic Make projects. A repository-level `.firik-agent.toml` may add or replace
commands. Each gate captures argv, exit code, duration, stdout, stderr, and
truncation. The standard Python development gate for this repository is:

```text
ruff format --check .
ruff check .
mypy firik_agent
pytest
python -m build
```

## Extension points

- Register language-specific inspectors or quality gates.
- Register organization-specific tools through `ToolRegistry.register`.
- Add search providers without changing the documentation/fetch tools.
- Supply an already constructed smolagents `Model` for local, hosted, or
  OpenAI-compatible inference.
- Add approval callbacks for deployments, dependency changes, or other
  high-impact commands.
