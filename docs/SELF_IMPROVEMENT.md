# Governed self-improvement

Firik records bounded execution metadata, reflects on verified experience, stages
knowledge and procedural changes, and promotes exact reviewed revisions. The
existing agent, project database, skills, tool permissions, context builder,
CustomTkinter dispatcher and execution backends remain their respective owners.

See [the initial architecture audit](SELF_IMPROVEMENT_AUDIT.md) for the component
map, gaps, migration plan and references. This implementation was built on top of
an already dirty checkout; it does not publish a release or migrate user projects.

## Defaults and configuration

Learning defaults to **observe**, with automatic reflection disabled. There are
no auxiliary model requests in deterministic reflection. No provider, model,
permission, sandbox, credential or approval setting is changed by learning.

```yaml
self_improvement:
  mode: observe                 # off | observe | propose | experiment
  automatic_reflection: false
  memory_token_budget: 600      # conservative local estimate, not billed tokens
  max_events_per_session: 256
  max_pending_jobs: 8
  max_proposals_per_review: 3
  max_review_seconds: 20
  review_cooldown_seconds: 300
  retention_days: 90
  max_records: 10000
  max_candidate_workspaces: 4
  candidate_storage_mb: 512
  private_projects: []
  periodic_review_seconds: 0    # optional, minimum effective interval 60 seconds
  model_routes: {}
```

`off` stops collection, reflection, recall and new promotions; inspection,
forgetting and rollback remain available. `observe` permits metadata capture and
explicit manual review. `propose` can enqueue evidence-triggered reflection when
enabled. `experiment` additionally permits explicit source generation and
isolated experiments. Promotion always requires a separate reviewed decision.
The modes do not grant shell, MCP or file permissions.

Explicit CLI/desktop settings are stored as per-database overrides. Project
`coding_preferences.learning_enabled: false`, `private: true`, or
`self_improvement: {enabled: false}` overrides collection and recall. Private
`ChatSession` instances do not contribute learning data or receive learned context.

Periodic review runs only while a live coordinator process exists, only when
automatic reflection is enabled in propose/experiment mode. One bounded worker
handles reviews; it has no tools. Closing the application cancels its timer and
queued work. No OS daemon or scheduled task is installed.

`model_routes` accepts explicit role/provider/model/revision metadata. Deterministic
reflection, memory and skill extraction do not call these models. Embedding,
reranking and auxiliary LLM review are not enabled by declaring a route. Coding
generation uses the initialized existing agent's active provider/model; a
conflicting coding route is rejected. No hardcoded model ranking, capacity,
availability or price is assumed.

## Storage, provenance and privacy

Additive, independently versioned `improvement_*` tables live in the existing
ProjectDatabase with its WAL, FULL sync, foreign keys and serialized transactions.
Existing project/chat tables are untouched. FTS5 is used when present, with lexical
fallback. Initialization is idempotent and never applies pending changes.

Experiences retain allowed event fields, model/harness identity, nullable usage,
typed errors, fresh verification metadata and original evidence references.
Conversation text, reasoning, tool arguments and raw output are excluded. A
verification command is retained only from actual durable host evidence after
privacy validation. Secret-pattern detection is defense in depth, not a promise
to recognize every possible confidential string.

The collector re-reads identified durable journal events and validates their
session/project ownership. Runtime flags, assistant completion prose and DONE
alone never establish verified task success. Reflection distinguishes diagnostic
observations and hypotheses; a tool outage does not become a universal rule.
Unchanged evidence is reviewed once. Reflection-origin events never trigger
another reflection. Records, proposals and concurrent jobs have hard bounds.

Memory records include scope, kind, source, evidence, confidence, verification,
revision, timestamps, expiry, supersession, privacy, approval and status. Only
approved active nonexpired records enter context. Inferred project evidence cannot
become global knowledge, replace a user-confirmed rule or duplicate full skill
instructions. Explicit project rules remain ahead of learned context. The existing
context inspector shows selected memory IDs and reasons within the memory slice.

Exact normalized duplicates merge evidence without duplicating knowledge.
Corrections create new records and preserve previous revisions. Same-key
contradictions require an explicit correction. General semantic equivalence and
arbitrary natural-language contradiction detection are not claimed.

Forgetting removes learned correction chains, proposal text/titles and associated
learning-audit payload snapshots. It does not rewrite the independently owned
coding journal or chat history, and SQLite/OS backup deletion is a separate concern.

## CLI and desktop

Use the existing `firik-agent` executable. Inspection commands require no provider:

```sh
firik-agent improve status
firik-agent improve settings
firik-agent improve settings --set '{"mode":"propose","automatic_reflection":true}'
firik-agent improve reflect --session SESSION_ID --project PROJECT_ID
firik-agent improve proposals --limit 25 --offset 0
firik-agent improve show PROPOSAL_ID
firik-agent improve diff PROPOSAL_ID
firik-agent improve evaluate PROPOSAL_ID
firik-agent improve approve PROPOSAL_ID --revision REVIEWED_REVISION
firik-agent improve reject PROPOSAL_ID --revision REVIEWED_REVISION
firik-agent improve rollback PROPOSAL_ID --revision REVIEWED_REVISION
firik-agent improve history
firik-agent improve metrics
```

`evaluate` changes the proposal revision. Always inspect the returned revision
before approval. Rejected proposals cannot later apply; stale revisions fail.
Changing a proposal invalidates its previous evaluation:

```sh
firik-agent improve revise PROPOSAL_ID --revision CURRENT_REVISION --payload change.json
firik-agent improve remember 'Use the project-confirmed validation command.' --project PROJECT_ID
firik-agent improve remember 'Updated convention.' --project PROJECT_ID --supersedes MEMORY_ID
firik-agent improve retrieve 'validation command' --project PROJECT_ID --token-budget 600
firik-agent improve memories --project PROJECT_ID
firik-agent improve remove-memory MEMORY_ID
firik-agent improve pin-memory MEMORY_ID
firik-agent improve pin-memory MEMORY_ID --unpin
firik-agent improve prune
firik-agent improve learn-workflow project-validation --description 'Project validation workflow' --instructions procedure.md --project PROJECT_ID
firik-agent improve disable-skill PROPOSAL_ID --revision CURRENT_REVISION
firik-agent improve cancel
```

Omit `--project` only for explicitly global user-supplied knowledge. Workflow
instructions are staged as compatible SKILL.md/skill.yaml files. Approved project
skills live in `.firik/skills` and require a matching host-owned applied record and
artifact hash; a repository manifest cannot approve itself. Existing skill
cataloguing and explicit selection load them progressively. Revision changes keep
prior files and refuse to overwrite newer user edits.

Ordinary direct-user chat recognizes `Remember that …`, `Remember: …`,
`Şunu hatırla: …`, `Stop automatic learning`, `Otomatik öğrenmeyi durdur`,
`Remember how we solved this problem`, `Turn this successful workflow into a
skill`, `Why did you fail this task?`, and `Show every pending change to your own
code`. These routes stage/analyze evidence or stop learning; they never approve
or execute a proposed change. Other phrasing uses normal conversation.

Desktop **Kendi Kendini Geliştirme** provides overview, history, memory, skills,
tools, experiments, code improvements, approvals and settings. It displays actual
records, evidence, diffs and evaluation results with pagination. Storage and model
work run off Tk; callbacks use the existing main-thread dispatch. Stop Learning
remains available while a review or generation is active.

For explicit storage/configuration use `--db PATH` and `--config PATH`. `--db`
wins; explicit `--config` otherwise selects its sibling `projects.db`. With neither,
the ProjectDatabase default is `~/.firik/projects.db`. Desktop uses its existing
configured project DB, which packaged startup may place beside the platform's
application config. Select that same database when managing desktop learning from
the CLI. An ordinary standalone Agent uses its configured shared database binding
or the ProjectDatabase default.

## Tool and source experiments

Typed tool proposals compare existing capabilities first. Only bounded pure
standard-library utilities are supported by the generated-tool adapter. Input and
output schemas, AST/dependencies, fixtures, output size and permission claims are
checked. Source stays inert on the host; an approved trusted PluginManager adapter
executes each invocation in restricted Docker. Existing tools cannot be replaced.
Applied global tools can be restored on restart; live durable approval and mode
are rechecked before invocation. Project tool activation is blocked until a
project-bound registry exists.

```sh
firik-agent improve propose-tool --file tool.json
firik-agent improve settings --set '{"mode":"experiment"}'
firik-agent improve experiment PROPOSAL_ID --fixtures tool-fixtures.json
firik-agent improve evaluate PROPOSAL_ID
```

Fixtures use `input` and `expected` fields. Required tool payloads include
`tool_schema` (name, description, parameters), `output_schema`, `source`,
`side_effect: pure`, `permissions: []`, and problem/comparison/risk details.
Unknown dependencies, dynamic introspection, privileged builtins and executable
top-level statements are rejected. No dependency install or remote script occurs.

Source patches use the existing IsolatedWorkspaceManager with exact file grants,
base revision, source digest, full diff and dirty-target checks. A worktree is
not a sandbox. Generation uses the existing initialized coding Agent with an
empty tool registry and a bounded explicit source snapshot. It cannot use shell,
MCP, plugins, skills, file-write tools, delegation or provider fallback. The user
must consent to sending that source digest to the selected provider:

```sh
firik-agent improve generate-code --config /path/to/config.yaml --repository /path/to/repo --allowed-file firik_agent/example.py --task 'Fix the reproduced parsing defect' --allow-model-digest
firik-agent improve stage-code --repository /path/to/repo --allowed-file firik_agent/example.py --patch candidate.patch
firik-agent improve experiment PROPOSAL_ID --gates gates.json
firik-agent improve evaluate PROPOSAL_ID
firik-agent improve show PROPOSAL_ID
firik-agent improve approve PROPOSAL_ID --revision REVIEWED_REVISION
```

Source generation refuses a busy shared agent or changed provider/model. Running
application files are unchanged until an explicit approved integration. Integration
does not commit, push, merge a remote PR, publish, deploy or change user databases.
Protected authorization, credentials, learning/evaluation code, tests, fixtures,
release mechanisms and audit paths cannot be changed by these experiments.

Tool/code execution requires the existing restricted Docker backend: local
verified Linux/seccomp daemon, already available image pinned by ID, filtered
private source copy, no network, no host credentials, nonroot/read-only root,
resource limits, bounded time/output and cleanup checks. It never falls back to
host execution or pulls an image. Unavailable isolation blocks the experiment.
Baseline/candidate use the same pinned image and explicit host-held quality gates.

Receipts are authenticated outside the candidate workspace and bound to exact
source/configuration identities. Candidate-written reports cannot grant promotion.
Ordinary project test gates still depend on trusted tests and human review;
arbitrary hostile Python can affect its own test interpreter. The implementation
does not claim complete semantic tamper-proof evaluation of arbitrary programs or
whole-Agent OS confinement. Generated pure-utility fixture expectations remain
outside the candidate process.

## Harness comparisons, monitoring and rollback

Imported existing harness/benchmark reports remain inspectable evidence. They do
not authorize an untested learning-setting change. The implemented active setting
owner is `memory_token_budget`; other prompt/routing/security changes are not
silently applied.

```sh
firik-agent improve propose-harness --file hypothesis.json --project PROJECT_ID
firik-agent improve experiment PROPOSAL_ID --cases retrieval-cases.json
firik-agent improve evaluate PROPOSAL_ID
firik-agent improve approve PROPOSAL_ID --revision REVIEWED_REVISION
firik-agent improve monitor PROPOSAL_ID --cases retrieval-cases.json
firik-agent improve rollback PROPOSAL_ID --revision REVIEWED_REVISION
```

A hypothesis contains `changes: {"memory_token_budget": INTEGER}`. Supply 5–20
distinct host-controlled cases with `query` and `expected_memory_ids`, including a
negative case and diverse expected records. The same memory snapshot and cases
are tested at baseline/candidate budgets. No paired correctness regression is
allowed; correctness gain or a predefined practical overhead reduction is needed.
Reports preserve sample size, duration, local estimates and unknown provider usage
and cost. Memory/settings changes invalidate pending comparison evidence.

Monitoring runs those actual paired checks again and flags a baseline-passed,
candidate-failed case. It offers a reviewed rollback; it does not silently change
settings. Memory, skill and source rollback each verify their previous/current
versions before restoring; newer user work blocks rollback. Unknown/interrupted
effects are retained for manual reconciliation and never blindly replayed. An
identical attempted experiment is not automatically rerun; inspect its result and
explicitly revise the proposal/conditions before another attempt.

Retained workspaces and their disk budget are bounded. Exhaustion blocks new
candidates rather than deleting work. Their manager records and proposal paths
remain inspectable; cleanup of edited retained worktrees requires explicit operator
handling. No automatic destructive worktree cleanup is attempted.

Reproduce the offline reference through the existing benchmarks package:

```sh
python -m firik_agent.benchmarks.learning --output /tmp/learning-retrieval.json
```

The [recorded fixture report](Evidence/SelfImprovement/learning-retrieval-v1.json)
uses the real coordinator, approval, retrieval, comparison, monitoring and rollback
paths in a temporary database. It shows 2/5 baseline cases and 5/5 candidate cases
for deliberately small versus adequate memory budgets. That demonstrates this
bounded fixture's recall improvement at additional context overhead, not general
agent quality, savings or live-model performance. Exported reports are historical
evidence; they cannot substitute for a current authenticated promotion receipt.

## Security boundary and capability matrix

Learning approvals are host CLI/UI operations, not model tools. A caller-provided
`actor='user'` is a host API convention, not a separate OS identity. A process
already granted unrestricted host execution or private-state writes shares the
operator's filesystem authority; a worktree, worker thread or same-user HMAC key
does not isolate that process. Stronger separation requires an independently
protected broker/OS principal. Existing coding terminal approval remains intact.

| Capability | Implementation and validation status |
| --- | --- |
| Metadata collection, deduplication, verified evidence | Implemented; durable/runtime privacy and trust tests |
| Reflection and scoped persistent memory | Implemented deterministic rules; restart, correction, isolation, forgetting and budget tests |
| Workflow/skill proposals and revisions | Implemented for repeated verified workflows and explicit user procedures; real later project-context selection tested |
| Tool proposal, validation, isolated adapter | Implemented; negative AST/schema/receipt/mode tests; real Docker execution unverified in this environment |
| Memory-budget experiments and monitoring | Implemented; real offline paired fixture, current-snapshot gates and rollback |
| Source generation, isolated tests, reviewed integration | Implemented experimental adapters; fake-provider and fail-closed tests; no live-provider/source-promotion claim |
| CLI and CustomTkinter dashboard | Implemented; command/dispatch tests and real native widget smoke |
| Additive migration, cancellation and recovery | Implemented; existing-data, concurrency, unknown outcome and restart tests |
| Auxiliary reflection LLM, embeddings/reranking | Not enabled; configured names do not establish provider availability or pricing |
| General prompt/context/model routing promotion | Recommendation/report inspection only beyond the owned memory-budget setting |
| Arbitrary dependency-bearing generated plugins, project tool activation | Unsupported; blocked rather than installed or globally activated |
| Always-running daemon, automatic source deployment, privileged self-approval | Unsupported; no such authority is granted |

Live hardware/platform and provider behavior require separate validation. Docker
was unavailable during this implementation's local availability check; execution
was refused. Python 3.10 compatibility is preserved in syntax/API choices, while
the local automated runs use the available interpreter. Test results actually run
and exact commands are retained in the validation record beside the fixture report.
