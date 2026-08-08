# Firik Agent

Firik Agent is a workspace-scoped software-development agent. It can inspect
and edit code, search official documentation and the public web, run
development commands, recursively verify multi-language repositories, and keep
a durable development record.

It is deliberately more than a prompt plus shell access. Architecture,
planning, mutations, verification, retries, and completion are enforced by
deterministic Python code.

## Capabilities

- Architecture-before-code gate with persisted components, interfaces,
  constraints, risks, and acceptance evidence.
- Stable implementation plans and audited lifecycle phases.
- Confined file listing, search, reading, atomic writing, and exact replacement.
- Non-shell command execution with executable, timeout, cwd, environment, and
  output policies.
- Recursive formatting and quality checks for Python, Node, Rust, Go, and Make
  projects, with `.firik-agent.toml` overrides.
- Root-cause correction loop with a bounded verification retry budget.
- Read-only Git status and diff tools.
- Official-documentation search, public web search, and bounded URL fetching
  with redirect and private-network protections.
- Hosted Hugging Face Inference Providers or local Transformers models.
- Extensible, framework-neutral tool registry.

The runtime uses Hugging Face
[`ToolCallingAgent`](https://huggingface.co/docs/smolagents/reference/agents),
which emits structured tool calls. The tool contract follows the standard
Hugging Face function-calling shape described in the
[Transformers tool-use guide](https://huggingface.co/docs/transformers/main/chat_extras).

## Architecture

```text
DevelopmentAgent
  -> DevelopmentProcess (architecture, plan, verify, review gates)
  -> ToolRegistry
       -> Workspace (path and file policy)
       -> CommandRunner + ProjectVerifier
       -> ResearchClient (web and docs)
  -> smolagents ToolCallingAgent
  -> hosted or local model
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for boundaries, state
transitions, security controls, and extension points.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[agent]'
```

For local Transformers inference:

```bash
python -m pip install -e '.[local]'
```

Local models can be many gigabytes. Installation does not download a model;
the first local run does.

## Run a development task

Hosted inference uses your Hugging Face credentials (`hf auth login` or
`HF_TOKEN`):

```bash
firik-agent develop \
  --workspace /path/to/repository \
  --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  "Add cursor pagination to the users API without breaking existing clients"
```

Choose an Inference Provider when needed:

```bash
firik-agent develop \
  --workspace . \
  --provider novita \
  --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  "Implement the next planned milestone"
```

Run locally only with a tool-calling instruct model that fits the machine:

```bash
firik-agent develop --workspace . --local --model <model-id> "Fix the failing tests"
```

Task evidence is stored under `.firik-agent/tasks/` and is ignored by Git. Inspect
a previous task with:

```bash
firik-agent status <task-id> --workspace .
```

The command exits `0` only when the deterministic process reaches `complete`;
an incomplete, blocked, or unverified model answer exits `2`.

## Python API

```python
from firik_agent import DevelopmentAgent

agent = DevelopmentAgent(
    workspace="/path/to/repository",
    model_id="Qwen/Qwen3-Coder-30B-A3B-Instruct",
    provider="novita",
)
result = agent.run("Add rate limiting and tests to the public API")

print(result.phase)
print(result.record_path)
```

You can supply an already constructed smolagents model through `model=`. This
is the preferred integration point for custom endpoints and test doubles.

## Enforced workflow

```text
discovery -> architecture -> planning -> implementation
    -> verification --failure--> implementation (bounded retry)
    -> verification --success--> review -> complete
```

Source writes and development commands are rejected until the architecture and
plan gates pass. Completion is rejected unless all plan items are complete and
the latest full verification passed.

## Verification configuration

The default recursive verifier discovers repository manifests. To use exact
project commands, add `.firik-agent.toml`:

```toml
[verification]
commands = [
  { name = "format", command = ["ruff", "format", "--check", "."] },
  { name = "lint", command = ["ruff", "check", "."] },
  { name = "types", command = ["mypy", "src"] },
  { name = "tests", command = ["pytest", "-q"], timeout_seconds = 600 },
]
```

Commands are argument arrays or shell-like strings parsed with `shlex`; no
shell is launched and control operators are rejected. Python inline code and
interactive mode are disabled; approved `python -m` quality tools and workspace
`.py` files are supported.

## Built-in tools

| Area | Tools |
|---|---|
| Process | `inspect_project`, `set_architecture`, `set_plan`, `update_plan_item`, `development_status`, `complete_task` |
| Code | `list_files`, `search_code`, `read_file`, `write_file`, `replace_text` |
| Build | `run_command`, `format_project`, `verify_project` |
| Git | `git_status`, `git_diff` |
| Research | `search_documentation`, `search_web`, `fetch_url` |

Register organization-specific tools directly on `DevelopmentToolbox.registry`
before building the smolagents runtime.

## Development

```bash
python -m pip install -e '.[dev,agent]'
ruff format .
ruff check .
mypy firik_agent
pytest
python -m build
```

Tests do not load an LLM or make network requests. Heavy ML dependencies live
in the optional `local` group.

## Security notes

- Tool access is confined to one canonical workspace; traversal and symlink
  escapes are rejected.
- Internal task records cannot be modified through model-facing file tools.
- Arbitrary shells, destructive Git commands, private network targets, URL
  credentials, unbounded downloads, and remote model code are disabled.
- Package scripts and build tools execute repository code. Run the agent only
  against repositories you trust, or place it in a stronger OS/container
  sandbox for hostile code.
- Internet and documentation content is returned with an explicit untrusted
  content warning and is never an authority for tool policy.

## Legacy model lifecycle API

`LLMAgent` and `ModelManager` remain available for direct local model loading,
generation, search, ejection, and memory cleanup. New development automation
should use `DevelopmentAgent`.

## License

MIT
