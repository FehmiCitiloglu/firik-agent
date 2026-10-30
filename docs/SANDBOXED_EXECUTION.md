# Sandboxed execution and isolated agent workspaces

Firik can run commands through an optional Docker backend and place editing/testing subagents in separate Git worktrees. These are separate controls: Docker constrains a command process; a worktree separates source changes. Existing coding permissions and concrete action approvals still apply.

## Start with the prerequisite check

```sh
firik-agent sandbox doctor
firik-agent sandbox doctor --backend host
firik-agent sandbox retained
firik-agent sandbox workspaces --repository /absolute/path/to/project
```

These commands do not contact a model or replay an earlier operation. `doctor` returns status 2 when the requested backend is unavailable. Docker must be installed and running locally with Linux containers and seccomp support. Remote Docker endpoints are unsupported. The backend pins the initially validated local socket for its lifetime and persists it with each workspace; crash reconciliation uses that original endpoint. Simultaneous `DOCKER_HOST` and `DOCKER_CONTEXT` selections are rejected during initial selection.

Prepare a trusted image explicitly before using Docker. Firik never pulls an image automatically. The default is `python:3.12-slim`; an immutable digest can be selected instead. The image must provide `/usr/bin/env` and the selected `/bin/sh` or `/bin/bash`. Image-declared writable `VOLUME`s are rejected. A passing daemon check does not establish that a particular image is present or compatible.

```sh
firik-agent --execution-backend docker --sandbox-image python:3.12-slim -p '!python --version'
```

This explicit command needs no model API key. A missing daemon/image or failed container creation is reported as unavailable/error; the requested command is never retried on the host. Selecting Docker changes shell execution, including `!` commands, rather than sandboxing the general CLI/SDK Agent and all its tools.

## Configuration and profiles

For CLI/SDK sessions, add a top-level section to `config.yaml`:

```yaml
execution:
  backend: docker
  image: python:3.12-slim
  profile: restricted
  network: false
  network_approved: false
  cpus: 1.0
  memory_mb: 512
  pids_limit: 128
  max_timeout: 300
  output_limit: 32768
  tmpfs_mb: 64
  environment_allowlist: [LANG, LC_ALL, TZ, PYTHONUNBUFFERED, CI]
```

The default backend is `host`. `--execution-backend` and `--sandbox-image` override those two CLI session settings. Desktop coding uses the project's saved coding preferences instead of the CLI configuration.

| Profile | Network policy | Writable access |
| --- | --- | --- |
| `restricted` | Always disabled | Private filtered project copy and bounded `/tmp` |
| `development` | Disabled unless both `network` and `network_approved` are true | Same scoped copy; package installation must fit the image, user and existing permissions |
| `custom` | Same explicit network requirement | Same hard mount/privilege restrictions; resource and environment settings can be narrowed |

To opt into development networking, set `profile: development`, `network: true`, and `network_approved: true`. This configuration is not a grant to bypass a coding policy's network denial. Coding commands still require the existing shell authorization, and network-enabled Docker commands also check/authorize `network` and `shell.network`, including scripts without a visible URL. Bridge networking is general network access, not a domain allowlist.

Custom profiles cannot add arbitrary mounts, privileged mode, devices, Linux capabilities, host networking, Docker sockets, or loader/proxy/credential environment variables. The environment allowlist can contain only the five listed names; request values are explicit, not inherited from the host. Containers receive a fixed `PATH`, `HOME=/tmp`, and `TMPDIR=/tmp` after their image environment is cleared.

Configuration limits have hard bounds: 0.1–8 CPUs, 64–8192 MiB RAM, 16–512 PIDs, 1–512 MiB temporary filesystem, 1024–262144 output bytes per stream, and at most 3600 seconds per backend command. Coding commands impose a further 300-second maximum. These configuration bounds do not impose CPU/memory/PID isolation on the host backend; host enforcement covers command duration and retained output.

## Desktop controls and inspection

In the coding workspace, open **İzinler / MCP / Beceriler** to choose the command backend, local image, profile, network access, and dirty-repository starting policy. Use **Docker önkoşulunu denetle** to inspect availability. Saved backend selection is used by both agent-driven coding commands and manual coding commands/tests.

**Yürütme / Sandbox Durumu** exposes the configured executor, workspace identity/path, network/resource settings, permission state, process/cleanup information and agent workspace associations. Coding tools also expose `inspect_execution` and `collect_execution_artifacts`. Child results include worktree, proposal, conflict and collected execution-artifact metadata. CLI `retained` lists surviving Docker workspace metadata; `workspaces` lists managed Git workspace records.

Tool definitions/results carry an execution boundary: `host`, `sandbox`, `remote_mcp`, `external_service`, or `unknown`. Treat that label as the location of the actual executor, independently of whether permission was granted.

## What Docker contains

The Docker backend admits bounded regular project files into a private copy and mounts only that copy at `/workspace`. It excludes sensitive paths, Git administrative data, symlinks, hardlinks, ordinary generated dependency/cache directories and recognized credential content. It does not mount the user's home or credential stores, the original checkout, or a host Docker socket. Host API keys are not forwarded into the container. The trusted Docker CLI still runs on the host and can read its own local Docker configuration; it is not an untrusted command container.

Each container uses a numeric non-root user, read-only root filesystem, no Linux capabilities, `no-new-privileges`, the daemon's seccomp policy, bounded CPU/memory/swap/PIDs, bounded non-executable `/tmp`, limited open files, and disabled core dumps/health checks. Docker log-file storage is disabled; attached output is drained with bounded retention. Networking defaults to `none`. There is no user-facing escape hatch for unconfined seccomp or privileged execution.

Before subsequent commands, admitted host changes refresh the copy. Changes that collide with retained sandbox edits fail with a conflict. Command-generated files stay in the copy. Artifact collection reports paths, sizes and hashes for review; it does not publish them into the user's checkout. Unsafe/sensitive output is withheld rather than followed or copied, and still prevents silent workspace deletion. Docker stage artifacts are separate from a child worktree's merge proposal.

A successful test command is not fresh evidence for the host source when the admitted host source changed during execution or the staged source differs afterward. Child verification remains child-local; integrating its work invalidates parent evidence and requires verification in the resulting parent workspace.

## Execution interface and lifecycle

`firik_agent.core.execution.ExecutionBackend` defines the asynchronous backend contract. `HostExecutionBackend` and `DockerExecutionBackend` implement it; `build_backend(ExecutionConfig(...))` selects one without fallback.

| Method | Purpose |
| --- | --- |
| `create_workspace(root, workspace_id=None)` | Establish a structured `ExecutionWorkspace` |
| `prepare_environment(workspace)` | Check backend/image prerequisites |
| `execute_command(workspace, CommandRequest(...))` | Return a structured `CommandResult` with exit, streams, timeout, boundary and cleanup information |
| `stream_output(command_id)` | Consume bounded stdout/stderr events for a registered command |
| `cancel_command(command_id)` | Cancel a live command and await cleanup |
| `inspect_status(workspace=None)` | Inspect availability, limits, workspaces and commands |
| `collect_artifacts(workspace)` | List isolated output for review |
| `destroy_workspace(workspace, discard=False)` | Stop owned execution and remove only eligible managed storage |

The backend library does not own a Firik session's authorization. SDK callers must authorize the exact action before dispatching it. Coding mode supplies that gate through `CodingRunControl`/`CodingToolFilter`.

Subprocesses use structured argument arrays for orchestration; generated shell text is passed to the selected shell. Output readers drain both streams while retaining bounded data. Commands run in process groups. Timeout/cancellation stops the group; POSIX success also reaps children that closed their output pipes. Docker cleanup separately verifies ownership labels and removes the container, terminating its descendants. Killing only the Docker CLI would be insufficient.

An uncertain exit state, unavailable daemon during cleanup or failed container removal is reported explicitly. It must be reconciled before repeating a potentially effective command. A new command identifier is not evidence that an earlier operation had no effect. No automatic host retry is permitted.

## Agent-local work and result integration

`AgentAssignment` records task, role, allowed paths, permitted tools, required outputs, acceptance criteria, dependencies, budget and execution backend. The parent can narrow its own grants; it cannot grant a child a capability it lacks. Children inherit the selected backend, live policy restrictions and excluded paths. They cannot change to a weaker backend or recursively spawn more agents. Existing child-count, parallelism, depth, iteration, token and cost limits remain in force.

Researcher/reviewer roles are read-only. Worker/tester runs receive a separate detached Git worktree. Host children receive no arbitrary shell/test/quality-gate capability because a working directory cannot constrain a script's filesystem access. Docker children may execute permitted verification commands inside their filtered staging copy. Worker file edits must satisfy the assigned path scope; out-of-scope results are retained and blocked from integration.

Only independent work can run concurrently. Declared dependencies finish before a dependent run begins; their results remain untrusted data. Waiting does not implicitly copy another child's uncommitted files into the next child's worktree. Integrate/review prerequisite changes deliberately before starting work that requires their source.

`IsolatedWorkspaceManager` creates unique detached worktrees from a resolved revision. By default, a dirty source is rejected. Explicit `dirty_start="clean_head"` on `SubagentToolFilter` (manager API: `dirty_policy="clean_head"`) starts from committed content and preserves the main checkout's dirty state. It does **not** copy staged, unstaged or untracked user modifications. No unrelated branches are switched or rewritten.

Default metadata lives under `<git-common-dir>/firik-isolation`; worktrees live under `<repository-parent>/.firik-workspaces/<repository-hash>/<workspace-id>`. Metadata persists independently of a running agent. Git plumbing/materialization avoids repository hooks, clean/smudge filters, external diff tools and configured merge drivers. Worktrees still share Git administrative/object storage and are not an OS security boundary.

The parent-only tool sequence is:

1. `inspect_subagent_changes(agent_id)` collects a binary/untracked-aware patch and current merge proposal.
2. If agents overlap, `resolve_subagent_conflicts(agent_id, digest, resolutions)` presents both versions and concrete resolution explanations for human approval. Other work remains retained. Actual merge conflicts require source resolution and a new proposal.
3. `review_subagent_changes(agent_id, digest)` requests human inspection of the exact revision-bound proposal. This does not apply it.
4. `integrate_subagent_changes(agent_id, digest)` separately requests integration approval under the parent's current filesystem/Git policy.
5. Verify the resulting parent workspace before treating it as complete.

Integration requires a clean, unchanged target revision and unchanged source digest/revision; it recomputes the three-way result and overlap bindings. New or changed competing work invalidates prior decisions. It publishes regular files with no-follow directory descriptors and records rollback/recovery state. The result remains unstaged and uncommitted. A conflict or stale proposal does not select one agent's version automatically or discard another agent's work.

## Cleanup and crash reconciliation

Cancelling or closing an agent preserves its worktree. `IsolatedWorkspaceManager.inspect_status(id)`, `collect_patch(id)`, `list_workspaces()` and `reconcile()` expose retained state; `destroy_workspace(id)` refuses dirty, untracked, ignored or unintegrated committed work. Interrupted integration is retained for inspection, not automatically replayed.

Docker workspaces use a private, owner-checked directory under the OS temporary directory: `firik-sandboxes-<user-identity>/<workspace-id>/{workspace,workspace.json}`. This survives an application crash when the OS retains those files. It is not durable storage against OS temporary-directory cleanup or a reboot policy that removes them.

`DockerExecutionBackend.retained_workspaces()` discovers records without executing saved commands. Explicit `await backend.recover_workspace(id)` reattaches a retained workspace and verifies container labels before terminating owned containers; a daemon/ownership failure leaves cleanup unresolved and artifacts intact. It does not replay the command. `destroy_workspace(workspace)` refuses changed or withheld output. After reviewing/exporting necessary work, a trusted caller can explicitly request `discard=True`; this is a deletion request, not a merge operation.

## Actual guarantees and platform limits

| Environment | Command isolation | Git integration |
| --- | --- | --- |
| Native Linux Docker | Linux namespaces/cgroups/seccomp depend on the installed daemon/kernel; only the filtered copy is mounted | Safe publishing requires POSIX no-follow directory-descriptor support |
| macOS Docker Desktop | Linux containers execute in Docker Desktop's VM; host file sharing and UID mapping add platform dependencies | POSIX integration is implemented; physical/live Docker behavior must be verified separately |
| Windows Docker Desktop | Linux-container mode is required; named-pipe/local-daemon and file-sharing behavior must be verified | Safe worktree publishing fails closed without POSIX dirfd support |
| Host backend | Ordinary user filesystem/environment/network authority; timeout/output bounds and process cleanup only | Application-level worktree separation, no OS confinement |

Tracked symlinks and submodules are unsupported by isolated worktree materialization. A hostile same-user process is not excluded by application locks. Host process groups cannot contain deliberate session escapes such as `setsid`, and this implementation does not provide Windows Job Object confinement. Docker/container/kernel/image vulnerabilities remain possible; container isolation is defense in depth.

There is no total disk quota on the writable host-backed staging copy or retained artifacts. Admission/artifact reads, CPU, memory, PIDs, output, temporary filesystem and execution time have bounds, but a command can still consume staging disk. Unknown credentials embedded in ordinary source or a selected image cannot be detected universally; review what is admitted. Enabling network access permits exfiltration of admitted data to reachable endpoints.

Docker policy does not constrain host-side MCP servers, remote MCP services, host file tools, native computer use, model-provider requests or general Agent plugins. Their authorization remains independent. Native computer-use capability flags and OS permissions must not be presented as complete application confinement or as a Docker guarantee.

The implementation includes mocked lifecycle/configuration tests and opt-in real Docker tests. Mocks establish application behavior and command construction, not Linux isolation. Real tests require a running daemon and prepared image:

```sh
FIRIK_DOCKER_INTEGRATION=1 pytest tests/test_docker_integration.py
```

At implementation time on 2026-10-28, Docker CLI was installed but the local daemon was unavailable. Real Docker isolation was therefore not established in this environment. Separate Linux/macOS/Windows integration evidence is required before claiming those runtime properties were tested on each platform.

Docker's own documentation explains the [default seccomp profile](https://docs.docker.com/engine/security/seccomp/), [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/), [daemon/container security assumptions](https://docs.docker.com/engine/security/) and [Docker Desktop VM behavior](https://docs.docker.com/desktop/features/vmm/).
