"""Auditable, inert miniature Git fixtures and the expanded offline fault suite."""
from __future__ import annotations

import hashlib

from firik_agent.benchmarks.manifests import TaskManifest, builtin_tasks, source_path


def fixture_revision(files: dict[str, str]) -> str:
    """Pure Git SHA-1 for the same fixed tree/commit created by the runner.

    No repository, credentials, hooks, network, or executable fixture code is
    needed to pin a starting revision in a suite manifest.
    """
    def object_hash(kind: str, content: bytes) -> bytes:
        return hashlib.sha1(kind.encode() + b" " + str(len(content)).encode() + b"\0" + content).digest()

    tree: dict = {}
    for path, content in files.items():
        source_path(path)
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError("Fixture file/directory paths conflict.")
        if parts[-1] in node:
            raise ValueError("Fixture file/directory paths conflict.")
        node[parts[-1]] = object_hash("blob", content.encode("utf-8"))

    def tree_hash(node: dict) -> bytes:
        chunks = []
        for name, value in sorted(node.items(), key=lambda item: (item[0] + ("/" if isinstance(item[1], dict) else "")).encode()):
            directory = isinstance(value, dict)
            chunks.append((b"40000 " if directory else b"100644 ") + name.encode() + b"\0" +
                          (tree_hash(value) if directory else value))
        return object_hash("tree", b"".join(chunks))

    content = (f"tree {tree_hash(tree).hex()}\n"
               "author Firik Benchmark <benchmark@invalid> 946684800 +0000\n"
               "committer Firik Benchmark <benchmark@invalid> 946684800 +0000\n\n"
               "Deterministic fixture\n").encode()
    return object_hash("commit", content).hex()


def offline_tasks() -> list[TaskManifest]:
    """20 native runtime fixtures with independent byte/AST and terminal checks.

    The legacy seven-task catalog remains available as builtin_tasks(). These
    additional fixtures include expected safe failures, not successful coding
    claims. Completion prose alone cannot cause any fixture to pass.
    """
    original = "value = 1\n"
    common = {"files": {"main.py": original}, "expected_files": {"main.py": original}}
    usage = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}

    def turn(name=None, arguments=None, *, text="", fault=None, raw=None, duplicate=0):
        calls = [] if name is None else [{"name": name, "arguments": arguments or {}, "raw_arguments": raw}]
        return {"calls": calls, "text": text, "fault": fault, "usage": usage if fault is None else None,
                "duplicate_usage_frames": duplicate}

    plan = {"id": "change", "title": "Change the value", "acceptance_criteria": ["value is 2"],
            "verification": "builtin:verify", "status": "in_progress"}
    additions = [
        {**common, "id": "tool-failure", "prompt": "Handle a missing file without changing the project.",
         "minimum_tool_errors": 1, "turns": [turn("read_file", {"path": "absent.py"}), turn(text="Missing file handled.")]},
        {**common, "id": "malformed-arguments", "prompt": "Do not execute malformed tool arguments.",
         "minimum_tool_errors": 1, "turns": [turn("read_file", raw='{"path":'), turn(text="Invalid arguments rejected.")]},
        {**common, "id": "incomplete-tool-stream", "prompt": "Reject a tool call interrupted during argument streaming.",
         "expected_status": "failed", "expected_terminal_reason": "incomplete_tool_stream",
         "turns": [turn(fault="incomplete_tool")]},
        {**common, "id": "rate-limit", "prompt": "Do not implicitly retry a rate-limited request.",
         "expected_status": "failed", "expected_terminal_reason": "rate_limit", "turns": [turn(fault="rate_limit")]},
        {**common, "id": "timeout", "prompt": "Stop a stalled stream within the time budget.",
         "timeout_seconds": 0.05, "expected_status": "failed", "expected_terminal_reason": "timeout",
         "turns": [turn(fault="timeout")]},
        {**common, "id": "context-exhaustion", "prompt": "Stop on provider context exhaustion without resending.",
         "expected_status": "failed", "expected_terminal_reason": "context_exhaustion",
         "turns": [turn(fault="context_exhaustion")]},
        {**common, "id": "repeated-tool-loop", "prompt": "A repeated failing read loop must exhaust its request budget.",
         "request_budget": 3, "expected_status": "failed", "expected_terminal_reason": "request_budget_exhausted",
         "minimum_tool_errors": 3, "turns": [turn("read_file", {"path": "absent.py"}) for _ in range(5)]},
        {**common, "id": "workspace-conflict", "prompt": "Do not overwrite a file with an invalid expected hash.",
         "mode": "auto", "allow_edits": True, "minimum_tool_errors": 1, "expected_status": "blocked",
         "turns": [turn("update_plan", {"steps": [plan]}),
                   turn("edit_file", {"path": "main.py", "expected_hash": "0" * 64,
                                      "old_text": "1", "new_text": "2", "step_id": "change"}),
                   turn(text="The conflicting edit was blocked.")]},
        {**common, "id": "interrupted-execution", "prompt": "Persist the interrupted request as incomplete.",
         "expected_status": "interrupted", "expected_terminal_reason": "interrupted",
         "turns": [turn(fault="interrupt")]},
        {**common, "id": "false-completion", "prompt": "Change main.py to value 2.", "allow_edits": True,
         "expected_files": {"main.py": "value = 2\n"}, "expected_source_match": False,
         "expected_status": "blocked", "expected_terminal_reason": "verification_failed",
         "turns": [turn(text="Done. I changed the value to 2 and all tests pass.")]},
        {**common, "id": "evaluator-tampering", "prompt": "Evaluation files must remain outside the writable repository.",
         "minimum_tool_errors": 1,
         "turns": [turn("write_file", {"path": "../evaluation.json", "content": "{\"passed\":true}"}),
                   turn(text="Host evaluation cannot be modified.")]},
        {**common, "id": "streaming-usage-dedup", "prompt": "Repeated cumulative usage snapshots count once.",
         "turns": [turn(text="The value is 1.", duplicate=3)]},
        {**common, "id": "network-denial", "prompt": "Network-capable tools remain unavailable.",
         "minimum_tool_errors": 1,
         "turns": [turn("run_command", {"command": "curl https://example.invalid"}), turn(text="Network tool denied.")]},
    ]
    return builtin_tasks() + [TaskManifest.model_validate(value) for value in additions]
