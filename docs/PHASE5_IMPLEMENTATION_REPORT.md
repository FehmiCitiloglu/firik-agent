# Phase 5 implementation and validation

Implemented on 2026-10-28 in the existing working tree. Pre-existing durability, installer, UI and test changes were preserved. No repository commit, branch change or push was performed.

## Delivered behavior

- Structured async execution lifecycle in `firik_agent/core/execution.py`: host and opt-in Docker backends, stream records, cancellation, inspection, artifact retention and explicit crash reconciliation.
- Required Docker execution never dispatches the requested command on host after an unavailable prerequisite or execution failure. CLI/SDK, coding runs, manual desktop commands/tests and children retain the selected backend.
- Fixed Docker privilege/mount/environment/resource controls, default network denial, locally validated and pinned daemon endpoint, persisted original cleanup endpoint, no implicit image pulls or volumes, and disabled Docker log-file storage.
- Per-agent detached Git worktrees with persistent metadata, explicit clean-head handling for dirty source, raw Git plumbing, patch collection, reviewed three-way proposals, overlap resolution, conditional rollback and conservative cleanup.
- Immutable delegation contracts and parent capability narrowing, live permission/exclusion checks, no recursive delegation, existing concurrency/dependency/token/cost budgets, and retained messaging/cancellation.
- Host children have no arbitrary shell/test execution. Docker child verification runs in staging; host file edits stay in the assigned worktree. Staging-generated files require separate artifact review and are not automatically integrated.
- Human review and separate revision-bound integration approval preserve the existing permission gate. Integration invalidates parent evidence and requires verification of the resulting parent tree.
- Tool boundary metadata distinguishes host, sandbox, remote MCP, external service and unknown execution. CLI doctor/retained/workspace inspection and desktop selection/status controls expose actual boundaries and cleanup state.

[Operational guide](SANDBOXED_EXECUTION.md).

## Validation receipts

| Run | Result | Scope |
| --- | --- | --- |
| Broad non-GUI suite | 1,097 passed, 2 skipped | Existing and new repository tests, excluding `tests/test_ui*` |
| Follow-up dispatch/recovery suite | 177 passed, 1 skipped | Selected shell, runtime, bridge, manual-action, recovery and boundary tests after integration changes |
| Final backend/boundary suite | 41 passed | Final Docker argument/endpoint/recovery/cancellation/artifact changes and coding/SDK boundary regressions |
| Native desktop sandbox test | 1 passed | Real Tk preferences, status display and next-run backend propagation; GUI sandbox escalation required |
| Explicitly enabled real Docker integration | 1 skipped | Daemon unavailable; no container isolation assertion executed |
| Compilation and diff hygiene | Passed | Changed execution/workspace/runtime/desktop modules compile; `git diff --check` clean |

These suites overlap; their counts must not be added. The broad run was followed by targeted validation of the final boundary changes. The complete aggregate GUI suite was not run; the new UI behavior was tested separately in a native process.

Commands used:

```sh
.venv/bin/python -m pytest -q tests --ignore-glob='tests/test_ui*' --tb=short --junitxml=/tmp/firik-phase5-nongui-final.xml
.venv/bin/python -m pytest -q tests/test_execution_backends.py tests/test_sandbox_runtime.py tests/test_recovery_engine.py tests/test_shell.py tests/test_coding_runtime.py tests/test_coding_bridge.py tests/test_cockpit_actions.py --tb=short --junitxml=/tmp/firik-phase5-dispatch-final.xml
.venv/bin/python -m pytest -q tests/test_execution_backends.py tests/test_sandbox_runtime.py --tb=short --junitxml=/tmp/firik-phase5-boundaries-final.xml
.venv/bin/python -m pytest -q tests/test_ui_sandbox.py --tb=short --junitxml=/tmp/firik-phase5-native-ui.xml
FIRIK_DOCKER_INTEGRATION=1 .venv/bin/python -m pytest -q tests/test_docker_integration.py -rs
.venv/bin/python -m firik_agent.cli sandbox doctor
.venv/bin/python -m firik_agent.cli sandbox doctor --backend host
```

Docker CLI 29.9.2 was installed on macOS arm64. Its local Desktop daemon was unavailable even when checked outside the execution sandbox. Doctor returned exit 2 with `host_fallback: false`; host doctor returned available with `isolation: none`, `network: host` and timeout/output-only resource enforcement.

Focused regressions cover unavailable/no-fallback dispatch, fixed mounts/limits/environment, recognized secret and link exclusion, credential-directory ancestors, staged mutation invalidating verification, parent network denial, MCP boundary labels, repeated cancellation, daemon failure, original endpoint retention, orphan identity retention, withheld artifacts, dirty source, worktree collisions, binary/untracked patches, conflicting/overlapping edits, stale proposals, failed integration rollback, malicious Git hooks/filters/drivers, child cancellation, live permission/exclusion narrowing, and native preference propagation.

## Actual guarantees and outstanding runtime evidence

Application permission checks and Git worktrees are not OS confinement. Host execution retains ordinary user filesystem/environment/network authority. Docker isolates only selected command processes; file tools, MCP, provider requests, plugins and native computer use retain separate host/external authority.

The Docker code applies the fixed restrictions and fails closed. Mock tests establish its dispatch, configuration and lifecycle behavior. They do not prove real container enforcement. Running-daemon/image tests on native Linux and Docker Desktop macOS/Windows remain unverified. Windows safe filesystem integration deliberately fails closed without POSIX dirfd support; tracked symlinks/submodules are unsupported.

Recognized sensitive files, credential-directory ancestors, links and high-confidence credential content are excluded, and host API keys are not forwarded. Unknown secrets embedded in ordinary source or a trusted image remain an admission concern. General network access, when approved, can exfiltrate admitted data.

Process groups are best-effort host cleanup and cannot contain deliberate session escapes; Windows Job Object confinement is absent. Container cleanup depends on the original daemon being reachable and ownership labels being verifiable. Uncertain cleanup preserves identities and artifacts and is never a reason to replay a command automatically.

No total disk quota exists on staging/retained artifacts. OS temporary-directory retention is not durable archival. Container/kernel/daemon/image vulnerabilities and hostile same-user races remain outside a perfect isolation guarantee.

## Retained threat model and source binding

The canonical threat model and its source/validation inventories are retained through the Codex Security artifact store:

- [Threat model](/Users/alien/.codex/state/plugins/codex-security/scans/evren/artifacts-724921b460c463e8b287d7702e43cb70be6454e3f773de4a66b7b76f92bd2e6a/threatmodel.md)
- [Source inventory](/Users/alien/.codex/state/plugins/codex-security/scans/evren/artifacts-724921b460c463e8b287d7702e43cb70be6454e3f773de4a66b7b76f92bd2e6a/artifacts/phase5/source-inventory.json)
- [Validation summary](/Users/alien/.codex/state/plugins/codex-security/scans/evren/artifacts-724921b460c463e8b287d7702e43cb70be6454e3f773de4a66b7b76f92bd2e6a/artifacts/phase5/validation-summary.json)

The threat model includes the independent architecture review, effective resource/authority table, attacker stories, residual risks and severity calibration. Its scope is 45 source files and is not an exhaustive vulnerability audit.

Source snapshot: `codex-security-snapshot/v1:sha256:89af72d79d891498a6a57b461983e6a56f44bf2df4b3a88b9df5a8948df4bd35`.

