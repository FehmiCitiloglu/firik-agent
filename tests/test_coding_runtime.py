"""Executor boundary checks and real subprocess results for the coding workspace."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.projects.coding_runtime import (CodingRunControl, CodingToolFilter,
                                                prepare_coding_messages, repair_tool_history, sanitize_message)


def call(name, **arguments):
    return ToolCall(id=f"call_{name}", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("def merhaba():\n    return 'eski'\n", encoding="utf-8", newline="\n")
    session = ChatSession(session_id="coding-a")
    control = CodingRunControl(session.session_id, "project-a", "task-a", mode="auto")
    tools = CodingToolFilter(root, control)
    return root, session, control, tools


async def execute(runtime, name, **arguments):
    return await runtime[3].execute(call(name, **arguments), runtime[1])


def revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "plan"])
async def test_read_only_modes_block_mutations_even_when_called_directly(runtime, mode):
    root, session, control, tools = runtime
    control.mode = mode
    names = {s["function"]["name"] for s in tools.get_schemas(session)}
    assert "read_file" in names and "edit_file" not in names and "run_tests" not in names
    assert "record_verification" not in names
    assert ("update_plan" in names) == (mode == "plan")
    assert (await execute(runtime, "record_verification", criterion="Soru", status="unverified")).is_error
    if mode == "ask":
        assert (await execute(runtime, "update_plan", steps=[{"title": "Gereksiz plan"}])).is_error
    result = await execute(runtime, "write_file", path="new.py", content="değişiklik", expected_hash="new")
    assert result.is_error and not (root / "new.py").exists()
    result = await execute(runtime, "run_command", command="touch marker")
    assert result.is_error and not (root / "marker").exists()


@pytest.mark.asyncio
async def test_isolated_registry_session_and_paths(runtime, tmp_path):
    root, session, control, tools = runtime
    outsider = tmp_path / "outside.txt"
    outsider.write_text("dışarı", encoding="utf-8", newline="\n")
    for path in (str(outsider), "../outside.txt"):
        assert (await execute(runtime, "read_file", path=path)).is_error
        assert (await execute(runtime, "write_file", path=path, content="ez", expected_hash=revision(outsider))).is_error
    assert outsider.read_text(encoding="utf-8") == "dışarı"
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    (root / "inside-link.py").symlink_to(root / "main.py")
    for path in ("link/outside.txt", "inside-link.py"):
        assert (await execute(runtime, "read_file", path=path)).is_error
    os.link(root / "main.py", root / "hard-link.py")
    assert (await execute(runtime, "read_file", path="hard-link.py")).is_error
    other = ChatSession(session_id="coding-b")
    assert not tools.get_schemas(other)
    assert (await tools.execute(call("read_file", path="main.py"), other)).is_error
    assert tools.tools.get_tool("add_plugin") is None
    assert Path.cwd() != root


@pytest.mark.asyncio
async def test_secret_file_content_ranges_and_logs_are_redacted(runtime):
    root, _, control, _ = runtime
    (root / ".env.local").write_text("SECRET=unsafe", encoding="utf-8", newline="\n")
    assert (await execute(runtime, "read_file", path=".env.local")).is_error
    (root / "config.py").write_text("api_key = 'abcdefghijklmnop'\n-----BEGIN PRIVATE KEY-----\nSENSITIVEBASE64BODY\n-----END PRIVATE KEY-----\n", encoding="utf-8", newline="\n")
    result = await execute(runtime, "read_file", path="config.py", start_line=3, end_line=3)
    assert not result.is_error and "SENSITIVEBASE64BODY" not in result.content
    result = await execute(runtime, "search_project", query="api_key")
    assert "abcdefghijklmnop" not in result.content
    events = []
    control.on_event = events.append
    result = await execute(runtime, "edit_file", path="config.py", expected_hash=revision(root / "config.py"),
                           old_text="abcdefghijklmnop", new_text="newsecretabcdefgh")
    assert not result.is_error
    encoded = json.dumps(control.snapshot(), ensure_ascii=False)
    assert "abcdefghijklmnop" not in encoded and "newsecretabcdefgh" not in encoded


@pytest.mark.asyncio
async def test_targeted_edits_revision_conflicts_and_safe_undo(runtime):
    root, _, control, tools = runtime
    path = root / "main.py"
    original = path.read_bytes()
    old_hash = revision(path)
    result = await execute(runtime, "edit_file", path="main.py", expected_hash=old_hash,
                           old_text="'eski'", new_text="'yeni'")
    assert not result.is_error and "yeni" in path.read_text(encoding="utf-8")
    change_id = json.loads(result.content)["change_id"]
    assert (await execute(runtime, "edit_file", path="main.py", expected_hash=old_hash,
                          old_text="'yeni'", new_text="'yanlış'")).is_error
    path.write_text("# Kullanıcının eşzamanlı düzenlemesi\n", encoding="utf-8", newline="\n")
    assert (await execute(runtime, "undo_change", change_id=change_id)).is_error
    assert "Kullanıcının" in path.read_text(encoding="utf-8")
    path.write_bytes(original.replace(b"eski", b"yeni"))
    assert not (await execute(runtime, "undo_change", change_id=change_id)).is_error
    assert path.read_bytes() == original
    assert control.changes[-1]["undone"]
    assert (await execute(runtime, "undo_change", change_id="another-run")).is_error


@pytest.mark.asyncio
async def test_permission_failure_closes_temporary_file_and_preserves_source(runtime, monkeypatch):
    root, _, _, _ = runtime
    source = root / "main.py"
    original = source.read_bytes()
    operation = "fchmod" if os.name == "posix" else "chmod"
    def denied(*args):
        raise PermissionError("simulated permission failure")
    monkeypatch.setattr(os, operation, denied)
    result = await execute(runtime, "edit_file", path="main.py", expected_hash=revision(source),
                           old_text="'eski'", new_text="'yeni'")
    assert result.is_error and "simulated permission failure" in result.content
    assert source.read_bytes() == original
    assert not list(root.glob(".firik-change-*.tmp"))


@pytest.mark.asyncio
async def test_single_file_patch_exact_context_and_creation(runtime):
    root, _, _, tools = runtime
    path = root / "main.py"
    patch = "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n def merhaba():\n-    return 'eski'\n+    return 'yeni'\n"
    result = await execute(runtime, "apply_patch", path="main.py", expected_hash=revision(path), patch=patch)
    assert not result.is_error and "yeni" in path.read_text(encoding="utf-8")
    assert (await execute(runtime, "apply_patch", path="main.py", expected_hash=revision(path), patch=patch)).is_error
    assert (await execute(runtime, "apply_patch", path="main.py", expected_hash=revision(path),
                          patch=patch.replace("b/main.py", "b/another.py"))).is_error
    created = await execute(runtime, "write_file", path="yeni.py", expected_hash="new", content="print('Türkçe')\n")
    assert not created.is_error
    change_id = json.loads(created.content)["change_id"]
    assert not (await execute(runtime, "undo_change", change_id=change_id)).is_error
    assert not (root / "yeni.py").exists()
    assert not list(root.glob(".firik-change-*"))


@pytest.mark.asyncio
async def test_approved_plan_and_each_mutation_await_ui_decision(runtime):
    root, _, control, tools = runtime
    control.mode = "approved"
    arguments = dict(path="new.py", content="# Yeni\n", expected_hash="new")
    assert (await execute(runtime, "write_file", **arguments)).is_error
    tools.update_plan([{"title": "Dosya ekle", "acceptance_criteria": ["Yeni dosya var"], "verification": "Dosyayı oku"}])
    future = asyncio.create_task(execute(runtime, "write_file", **arguments))
    while not control.pending_approvals:
        await asyncio.sleep(0.005)
    assert not future.done() and not (root / "new.py").exists()
    request_id = next(iter(control.pending_approvals))
    assert not control.decide_approval("stale", True)
    assert control.decide_approval(request_id, False)
    assert (await future).is_error and not (root / "new.py").exists()
    future = asyncio.create_task(execute(runtime, "write_file", **arguments))
    while not control.pending_approvals:
        await asyncio.sleep(0.005)
    control.decide_approval(next(iter(control.pending_approvals)), True)
    assert not (await future).is_error and (root / "new.py").exists()
    assert not control.pending_approvals


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_approval(runtime):
    root, _, control, _ = runtime
    control.pause()
    future = asyncio.create_task(execute(runtime, "read_file", path="main.py"))
    await asyncio.sleep(0.05)
    assert not future.done()
    control.resume()
    assert not (await future).is_error
    future = asyncio.create_task(execute(runtime, "run_command", command="touch shouldn't-exist"))
    while not control.pending_approvals:
        await asyncio.sleep(0.005)
    control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await future
    assert not control.pending_approvals


def auto_approve(control, events=None):
    def receive(event):
        if events is not None:
            events.append(event)
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = receive


@pytest.mark.asyncio
async def test_actual_commands_cwd_tests_exit_failure_and_verification_evidence(runtime, python_command):
    root, _, control, tools = runtime
    events = []
    auto_approve(control, events)
    (root / "test_behavior.py").write_text("import unittest\nclass TestBehavior(unittest.TestCase):\n    def test_example(self):\n        self.assertEqual(2 + 2, 4)\n", encoding="utf-8", newline="\n")
    command = python_command("-m", "unittest", "test_behavior", "-v")
    result = await execute(runtime, "run_tests", command=command)
    assert not result.is_error
    test = json.loads(result.content)
    assert test["executed"] and test["passed"] and test["exit_code"] == 0
    assert "test_example" in test["output"]
    tools.record_verification("Örnek test başarılı", "passed", test["evidence_id"])
    tools.update_plan([{"title": "Test", "status": "completed", "acceptance_criteria": ["Örnek test başarılı"],
                        "verification": command, "evidence_ids": [test["evidence_id"]]}])
    assert control.snapshot()["tests"][0]["passed"]
    with pytest.raises(ValueError, match="kanıt"):
        tools.record_verification("Uydurma", "passed", "fake")
    failure = await execute(runtime, "run_tests", command=python_command("-c", "raise SystemExit(3)"))
    assert failure.is_error and json.loads(failure.content)["exit_code"] == 3
    assert len([e for e in events if e.type == AgentEventType.APPROVAL_REQUIRED]) == 2
    assert all(e.session_id == "coding-a" and e.project_id == "project-a" and e.task_id == "task-a" for e in events)


@pytest.mark.asyncio
async def test_actual_process_cancellation_and_timeout_cleanup(runtime, python_command):
    root, _, control, _ = runtime
    auto_approve(control)
    code = "import os,time,pathlib; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(30); pathlib.Path('finished').touch()"
    command = python_command("-c", code)
    future = asyncio.create_task(execute(runtime, "run_command", command=command))
    for _ in range(200):
        if (root / "pid").exists():
            break
        await asyncio.sleep(0.005)
    assert (root / "pid").exists()
    pid = int((root / "pid").read_text(encoding="utf-8"))
    control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await future
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    else:
        import subprocess
        listing = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True)
        assert f'"{pid}"' not in listing
    assert not (root / "finished").exists()
    control.cancel_event.clear()
    timeout = await execute(runtime, "run_tests", command=command, timeout=0.1)
    assert timeout.is_error and json.loads(timeout.content)["timed_out"]
    assert not (root / "finished").exists()


@pytest.mark.asyncio
async def test_mcp_exact_tool_server_selection_and_each_call_approval(runtime):
    root, session, control, _ = runtime
    registry = ToolRegistry()
    invocations = []
    for name in ("mcp_docs_read", "mcp_docs_destroy"):
        registry.register(ToolDefinition(name=name, description="MCP", parameters={}, source="mcp:docs"),
                          lambda **args: invocations.append(args) or "tamam")
    session.active_mcp_servers.add("docs")
    tools = CodingToolFilter(root, control, registry, {"mcp_docs_read"})
    assert (await tools.execute(call("mcp_docs_read"), session)).is_error
    control.permissions["allow_mcp"] = True
    assert (await tools.execute(call("mcp_docs_destroy"), session)).is_error
    assert (await tools.execute(call("mcp_docs_read", path="../elsewhere"), session)).is_error
    events = []
    auto_approve(control, events)
    assert not (await tools.execute(call("mcp_docs_read"), session)).is_error
    assert not (await tools.execute(call("mcp_docs_read"), session)).is_error
    assert len(invocations) == 2
    assert len([e for e in events if e.type == AgentEventType.APPROVAL_REQUIRED]) == 2
    session.active_mcp_servers.clear()
    assert (await tools.execute(call("mcp_docs_read"), session)).is_error


@pytest.mark.asyncio
async def test_agent_stream_runtime_uses_local_tools_skips_plugins_and_reports_usage(runtime):
    root, session, control, tools = runtime
    observed = []
    class Provider:
        current_model = "fake"
        async def chat_stream(self, **kwargs):
            observed.append(kwargs)
            yield StreamChunk(delta_content="Plan hazır.", usage={"prompt_tokens": 25, "completion_tokens": 3})
    agent = Agent()
    agent._is_initialized = True
    agent.default_cwd = "generic-chat-cwd"
    agent.providers = SimpleNamespace(get_active=lambda: Provider())
    async def forbidden(*args):
        raise AssertionError("Global plugin hook used")
    agent.plugins.dispatch_user_message = forbidden
    agent.plugins.dispatch_model_request = forbidden
    agent.plugins.dispatch_model_response = forbidden
    events = [event async for event in agent.run_stream("Planla", session=session, session_tool_filter=tools,
                                                       run_control=control, cwd=str(root))]
    assert events[-1].type == AgentEventType.DONE
    assert any(event.type == AgentEventType.MODEL_USAGE for event in events)
    assert agent.default_cwd == "generic-chat-cwd"
    assert {item["function"]["name"] for item in observed[0]["tools"]} == {t.name for t in tools.tools.list_tools()}
    assert all(event.metadata["run_id"] == control.run_id for event in events)
    assert not agent.tools.list_tools()


@pytest.mark.asyncio
async def test_partial_model_stream_failure_does_not_repeat_request(runtime):
    _, session, control, tools = runtime
    class Provider:
        chat_calls = 0
        async def chat_stream(self, **kwargs):
            yield StreamChunk(delta_content="Kısmi yanıt")
            raise ConnectionError("Ağ kesildi")
        async def chat(self, **kwargs):
            self.chat_calls += 1
            return Message(role="assistant", content="Tekrar")
    provider = Provider()
    agent = Agent()
    agent._is_initialized = True
    agent.providers = SimpleNamespace(get_active=lambda: provider)
    events = [event async for event in agent.run_stream("Sor", session=session, session_tool_filter=tools, run_control=control)]
    assert provider.chat_calls == 0 and events[-1].type == AgentEventType.ERROR


@pytest.mark.asyncio
async def test_provider_without_tools_receives_no_schemas_and_cannot_execute(runtime):
    root, session, control, tools = runtime
    control.capabilities = {"supports_tools": False}
    requests = []
    class Provider:
        async def chat_stream(self, **kwargs):
            requests.append(kwargs)
            yield StreamChunk(tool_calls=[call("write_file", path="forbidden.py", content="x", expected_hash="new")])
    agent = Agent()
    agent._is_initialized = True
    agent.providers = SimpleNamespace(get_active=lambda: Provider())
    events = [event async for event in agent.run_stream("Düzenle", session=session, session_tool_filter=tools, run_control=control)]
    assert requests[0]["tools"] is None
    assert events[-1].type == AgentEventType.ERROR
    assert not (root / "forbidden.py").exists()


def test_compression_preserves_plan_decisions_evidence_and_complete_active_turn(runtime):
    _, _, control, _ = runtime
    control.plan = [{"id": "step-1", "title": "Tamamlanmamış görev", "status": "in_progress"}]
    control.verifications = [{"kind": "test", "command": "pytest", "passed": False, "evidence_id": "evidence-old", "exit_code": 1}]
    control.decisions = [{"tool_name": "run_tests", "allowed": False, "arguments_hash": "hash-old"}]
    messages = [Message(role="system", content="Proje talimatları")]
    for index in range(20):
        messages += [Message(role="user", content=f"Önceki karar {index}: " + "d" * 1000),
                     Message(role="assistant", content="Yanıt " + "y" * 1000)]
    active = [Message(role="user", content="Şimdiki istek"),
              Message(role="assistant", tool_calls=[call("read_file", path="main.py")]),
              Message(role="tool", content="Gerçek araç çıktısı", tool_call_id="call_read_file", name="read_file")]
    messages += active
    prepared = prepare_coding_messages(messages, control, [])
    assert prepared[-3:] == active
    summary = prepared[1].content
    assert "Tamamlanmamış görev" in summary and "evidence-old" in summary and "hash-old" in summary
    assert len(messages) == 44  # Persisted history was not truncated.
    assert len(prepared) == 5


def test_oversized_active_request_and_too_small_verified_window_are_explicit(runtime):
    _, _, control, _ = runtime
    with pytest.raises(ValueError, match="bağlam bütçesini"):
        prepare_coding_messages([Message(role="user", content="x" * 20_000)], control, [])
    control.capabilities = {"context_window": 4096}
    with pytest.raises(ValueError, match="bağlam bütçesini"):
        prepare_coding_messages([Message(role="system", content="s" * 5000), Message(role="user", content="u" * 3000)],
                                control, [], output_tokens=2048)


def test_cancelled_tool_protocol_repair_records_unknown_outcome_without_rerunning():
    messages = [Message(role="user", content="Eski istek"),
                Message(role="assistant", tool_calls=[call("read_file", path="main.py"), call("run_command", command="touch file")]),
                Message(role="tool", name="read_file", tool_call_id="call_read_file", content="ok"),
                Message(role="user", content="Devam et")]
    repair_tool_history(messages)
    assert len(messages) == 5
    assert messages[3].tool_call_id == "call_run_command"
    assert "sonucu kaydedilemedi" in messages[3].content
    repair_tool_history(messages)
    assert len(messages) == 5


@pytest.mark.asyncio
async def test_secret_prompt_prior_context_and_tool_arguments_are_sanitized_before_provider(runtime):
    _, session, control, tools = runtime
    secret = "ghp_" + "a" * 36
    session.messages = [Message(role="user", content=f"api_key = '{secret}'"),
                        Message(role="assistant", tool_calls=[call("write_file", path="old.py", content=f"api_key='{secret}'", expected_hash="new")])]
    requests = []
    class Provider:
        async def chat_stream(self, **kwargs):
            requests.append(kwargs)
            yield StreamChunk(delta_content="Türkçe yanıt")
    agent = Agent()
    agent._is_initialized = True
    agent.providers = SimpleNamespace(get_active=lambda: Provider())
    [event async for event in agent.run_stream(f"Anahtar: {secret}", session=session, session_tool_filter=tools, run_control=control)]
    assert secret not in json.dumps([m.to_dict() for m in requests[0]["messages"]])
    assert secret not in json.dumps([m.to_dict() for m in session.messages])


@pytest.mark.asyncio
async def test_same_failed_tool_is_bounded_and_searches_find_real_symbols(runtime):
    root, _, control, _ = runtime
    for _ in range(3):
        result = await execute(runtime, "edit_file", path="main.py", expected_hash=revision(root / "main.py"),
                               old_text="missing", new_text="no")
        assert result.is_error
    fourth = await execute(runtime, "edit_file", path="main.py", expected_hash=revision(root / "main.py"),
                           old_text="missing", new_text="no")
    assert "üç kez" in fourth.content
    symbols = await execute(runtime, "search_symbols", query="merhaba")
    assert json.loads(symbols.content)["matches"] == [{"path": "main.py", "name": "merhaba", "line": 1}]


def test_structured_tool_redaction_preserves_json_protocol():
    message = Message(role="tool", name="mcp_docs", tool_call_id="call-secret",
                      content=json.dumps({"api_key": "abcdefgh123456789", "path": "main.py", "result": "tamam"}))
    sanitized = sanitize_message(message)
    payload = json.loads(sanitized.content)
    assert payload["api_key"] == "[GİZLENDİ]" and payload["path"] == "main.py"
    assert "abcdefgh123456789" not in sanitized.content


@pytest.mark.asyncio
async def test_managed_worktree_root_allows_sources_but_protects_project_local_secret_dirs(tmp_path):
    root = tmp_path / ".codex" / "worktrees" / "managed" / "repo"
    root.mkdir(parents=True)
    (root / "main.py").write_text("def merhaba():\n    return 'Türkçe'\n", encoding="utf-8", newline="\n")
    secret_dir = root / ".aws"
    secret_dir.mkdir()
    (secret_dir / "credentials").write_text("token=do-not-read-this-secret", encoding="utf-8", newline="\n")
    session = ChatSession(session_id="managed")
    control = CodingRunControl(session.session_id, "managed-project", mode="ask")
    tools = CodingToolFilter(root, control)
    listing = await tools.execute(call("list_files"), session)
    assert not listing.is_error
    assert [entry["path"] for entry in json.loads(listing.content)["entries"]] == ["main.py"]
    symbols = await tools.execute(call("search_symbols", query="merhaba"), session)
    assert not symbols.is_error
    assert json.loads(symbols.content)["matches"][0]["name"] == "merhaba"
    secret = await tools.execute(call("read_file", path=".aws/credentials"), session)
    assert secret.is_error


@pytest.mark.parametrize("protected_root", [".aws", ".ssh", ".git"])
def test_sensitive_directory_cannot_become_runtime_project_root(tmp_path, protected_root):
    root = tmp_path / protected_root
    root.mkdir()
    (root / "source.py").write_text("password='root-secret-value'", encoding="utf-8", newline="\n")
    control = CodingRunControl("restricted-root", "protected-project", mode="ask")
    with pytest.raises(ValueError, match="Hassas"):
        CodingToolFilter(root, control)
    nested = root / "nested-repo"
    nested.mkdir()
    with pytest.raises(ValueError, match="Hassas"):
        CodingToolFilter(nested, control)
