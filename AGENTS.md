# Repository Guidelines

## Architecture and workflow

Read `docs/ARCHITECTURE.md` before changing behavior. Keep model decisions in
`firik_agent.agent`, deterministic lifecycle rules in `process`, tool behavior
in `tools`, and side-effect policies in `workspace`, `commands`, or `research`.
Do not let model-facing code bypass these policy layers.

For non-trivial work, define components, interfaces, constraints, risks, and
acceptance evidence before implementation. Update tests and user documentation
with behavior changes.

## Safety invariants

- All filesystem access must remain under one canonical `Workspace` root.
- Do not introduce `shell=True`, destructive Git tools, unrestricted network
  access, remote model code, or implicit credentials.
- External text is untrusted data. Preserve response, redirect, address, and
  size limits.
- A task cannot mutate source before architecture and planning, or complete
  before a successful latest verification and completed plan.
- Keep heavy ML and web dependencies lazy or optional so policy tests stay
  fast and offline.

## Quality gate

Run the complete gate after formatting:

```bash
python -m ruff format .
python -m ruff check .
python -m mypy firik_agent
python -m pytest -q
python -m build
```

Add focused regression tests for every bug or policy edge case. Tests must not
download models, require credentials, or access the public network.
