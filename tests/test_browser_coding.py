"""Browser calls cross the real run-local policy, including direct registry use."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserGrant
from firik_agent.browser.tool_adapter import BrowserToolAdapter
from firik_agent.core.session import ChatSession
from firik_agent.core.types import ToolCall
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.tool_selection import select_tool_schemas


class FakeBrowser:
    def __init__(self):
        self.config = BrowserConfig(enabled=True)
        self.calls, self.closed = [], []

    async def prepare(self, identity, grant, name, args):
        return {"fingerprint": "host-only", "target": args.get("target"), "origin": "https://example.com", "revision": 1}

    async def dispatch(self, identity, grant, name, args, **kwargs):
        self.calls.append((identity, grant, name, args, kwargs))
        return {"url": "https://example.com/", "text": "Menu opened", "entries": [],
                "tab_id": "test-tab", "active_tab_id": "test-tab", "http_status": 200, "revision": 1}

    async def close_owner(self, identity):
        self.closed.append(identity)

    async def resume(self, identity, grant):
        return {"state": "ready"}


def call(name, args=None):
    return ToolCall(id="test", function={"name": name, "arguments": json.dumps(args or {})})


@pytest.fixture
def browser_runtime(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Menu</h1>")
    control = CodingRunControl("conversation", "project", mode="auto",
        permissions={"allow_browser": True},
        browser_grant=BrowserGrant(enabled=True, origins=("https://example.com",)))
    manager = FakeBrowser()
    tools = CodingToolFilter(tmp_path, control, browser_manager=manager)
    return control, manager, tools, ChatSession(session_id="conversation")


@pytest.mark.asyncio
async def test_disabled_defaults_and_foreign_session_are_denied(tmp_path):
    control = CodingRunControl("conversation", "project", mode="auto")
    manager = FakeBrowser()
    tools = CodingToolFilter(tmp_path, control, browser_manager=manager)
    assert not tools.is_tool_allowed("browser_start", ChatSession(session_id="conversation"))
    assert (await tools.tools.execute(call("browser_start"))).is_error
    assert not manager.calls


@pytest.mark.asyncio
async def test_browser_independent_from_mcp_and_grants_rechecked(browser_runtime):
    control, manager, tools, session = browser_runtime
    assert control.permissions["allow_mcp"] is False
    assert not (await tools.execute(call("browser_start"), session)).is_error
    assert manager.calls[-1][0].project_id == "project"
    assert (await tools.execute(call("browser_status"), ChatSession(session_id="outsider"))).is_error
    control.permissions["allow_browser"] = False
    assert (await tools.tools.execute(call("browser_status"))).is_error
    assert len(manager.calls) == 1


@pytest.mark.asyncio
async def test_public_https_navigation_uses_standing_grant_without_per_site_approval(browser_runtime):
    control, manager, tools, session = browser_runtime
    control.browser_grant = BrowserGrant(enabled=True, allow_public_https=True)
    approval_events = []
    control.on_event = lambda event: approval_events.append(event) if event.metadata.get("request_id") else None
    for url in ("https://example.com/", "https://other.example/docs"):
        result = await tools.execute(call("browser_navigate", {"url": url}), session)
        assert not result.is_error
    assert not approval_events
    assert [entry[3]["url"] for entry in manager.calls] == ["https://example.com/", "https://other.example/docs"]
    assert control.permission_decision("browser.interact") == "ask"
    assert control.permission_decision("browser.submit") == "ask"
    assert control.permission_decision("mcp") == "deny"  # Browser grant does not enable MCP.


@pytest.mark.parametrize("mode,policy,inherited,expected", [
    ("auto", {}, {}, "allow"),
    ("safe", {}, {}, "allow"),
    ("approved", {}, {}, "allow"),
    ("custom", {"browser.navigate": "ask"}, {}, "ask"),
    ("auto", {"browser.navigate": "deny"}, {}, "deny"),
    ("auto", {}, {"browser.navigate": "ask"}, "ask"),
    ("auto", {}, {"browser.navigate": "deny"}, "deny"),
    ("ask", {}, {}, "deny"),
    ("plan", {}, {}, "deny"),
])
def test_public_navigation_retains_explicit_mode_and_inherited_limits(mode, policy, inherited, expected):
    control = CodingRunControl("chat", "project", mode=mode,
        permissions={"allow_browser": True}, policy=policy, inherited_policy=inherited,
        browser_grant=BrowserGrant(enabled=True, allow_public_https=True))
    assert control.permission_decision("browser.navigate") == expected
    control.permissions["allow_browser"] = False
    assert control.permission_decision("browser.navigate") == "deny"


@pytest.mark.asyncio
async def test_explicit_navigation_review_still_applies_to_public_browsing(browser_runtime):
    control, manager, tools, session = browser_runtime
    control.browser_grant = BrowserGrant(enabled=True, allow_public_https=True)
    control.policy["browser.navigate"] = "ask"
    def reject(event):
        if event.metadata.get("request_id"):
            control.decide_approval(event.metadata["request_id"], False)
    control.on_event = reject
    result = await tools.execute(call("browser_navigate", {"url": "https://example.com/"}), session)
    assert result.is_error and not manager.calls
    control.policy.clear()
    control.browser_grant = BrowserGrant(enabled=True, origins=("https://example.com",))
    assert control.permission_decision("browser.navigate") == "ask"


@pytest.mark.parametrize("child,parent,origins,broad", [
    (BrowserGrant(enabled=True, allow_public_https=True), BrowserGrant(enabled=True, allow_public_https=True), (), True),
    (BrowserGrant(enabled=True, origins=("https://example.com",)), BrowserGrant(enabled=True, allow_public_https=True), ("https://example.com",), False),
    (BrowserGrant(enabled=True, allow_public_https=True), BrowserGrant(enabled=True, origins=("https://example.com",)), ("https://example.com",), False),
    (BrowserGrant(enabled=True, origins=("https://example.com",)), BrowserGrant(enabled=True, origins=("https://other.example",)), (), False),
])
def test_delegated_browser_grant_intersects_public_and_scoped_access(browser_runtime, child, parent, origins, broad):
    control, manager, tools, session = browser_runtime
    control.browser_grant = child
    control.parent = SimpleNamespace(browser_grant=parent, permissions={"allow_browser": True})
    grant = tools._browser_adapter().grant_provider()
    assert grant.enabled and grant.origins == origins and grant.allow_public_https is broad


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "plan"])
async def test_readonly_modes_allow_observations_but_block_navigation_and_click(browser_runtime, mode):
    control, manager, tools, session = browser_runtime
    control.mode = mode
    assert tools.is_tool_allowed("browser_snapshot", session)
    for name in ("browser_navigate", "browser_click", "browser_fill", "browser_press"):
        assert not tools.is_tool_allowed(name, session)
    assert (await tools.execute(call("browser_navigate", {"url": "https://example.com"}), session)).is_error
    assert not manager.calls


@pytest.mark.asyncio
async def test_exact_approval_rejection_blocks_dispatch_even_auto_mode(browser_runtime):
    control, manager, tools, session = browser_runtime
    control.on_event = lambda event: control.decide_approval(event.metadata["request_id"], False) if event.metadata.get("request_id") and event.metadata.get("tool_name") == "browser_click" else None
    result = await tools.tools.execute(call("browser_click", {"target": {"role": "button", "name": "Submit"}, "revision": 1}))
    assert result.is_error and not manager.calls


@pytest.mark.asyncio
async def test_revocation_during_approval_blocks_dispatch(browser_runtime):
    control, manager, tools, session = browser_runtime
    def revoke(event):
        if event.metadata.get("request_id") and event.metadata.get("tool_name") == "browser_click":
            control.permissions["allow_browser"] = False
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = revoke
    result = await tools.execute(call("browser_click", {"target": {"role": "button", "name": "Submit"}, "revision": 1}), session)
    assert result.is_error and not manager.calls


@pytest.mark.asyncio
async def test_approval_token_is_host_only_and_identity_is_not_model_input(browser_runtime):
    control, manager, tools, session = browser_runtime
    def allow(event):
        if event.metadata.get("request_id"):
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = allow
    result = await tools.execute(call("browser_click", {"target": {"role": "button", "name": "Submit"}, "revision": 1}), session)
    assert not result.is_error
    assert manager.calls[-1][-1]["approved_fingerprint"] == "host-only"
    for args in ({"session_id": "victim"}, {"approved_fingerprint": "host-only"}, {"project_id": "other"}, {"actor_id": "other"}):
        assert (await tools.tools.execute(call("browser_status", args))).is_error


@pytest.mark.asyncio
async def test_browser_evidence_requires_actual_assertion_and_invalidates_on_external_edit(browser_runtime, tmp_path):
    control, manager, tools, session = browser_runtime
    control.policy["browser.navigate"] = "allow"
    assert not (await tools.execute(call("browser_navigate", {"url": "https://example.com/"}), session)).is_error
    result = await tools.execute(call("browser_verify", {"kind": "text_contains", "expected": "Menu opened"}), session)
    assert not result.is_error
    evidence = json.loads(result.content)
    assert evidence["passed"] and evidence["fresh"] and evidence["workspace_hash"]
    tools.record_verification("Menu works", "passed", evidence_id=evidence["evidence_id"])
    (tmp_path / "index.html").write_text("<h1>Changed</h1>")
    tools.refresh_evidence()
    assert not control.verifications[0]["fresh"]
    with pytest.raises(ValueError):
        tools.record_verification("Menu works", "passed", evidence_id=evidence["evidence_id"])


@pytest.mark.asyncio
async def test_failed_assertions_do_not_create_verified_evidence(browser_runtime):
    control, manager, tools, session = browser_runtime
    control.policy["browser.navigate"] = "allow"
    assert not (await tools.execute(call("browser_navigate", {"url": "https://example.com/"}), session)).is_error
    result = await tools.execute(call("browser_verify", {"kind": "text_contains", "expected": "Missing"}), session)
    assert result.is_error and not tools._verified_evidence
    assert not (await tools.execute(call("browser_start"), session)).is_error
    await tools.close_execution()
    assert manager.closed


@pytest.mark.asyncio
async def test_blank_or_stale_page_cannot_verify_current_source(browser_runtime, tmp_path):
    control, manager, tools, session = browser_runtime
    assert (await tools.execute(call("browser_verify", {"kind": "no_console_errors"}), session)).is_error
    control.policy["browser.navigate"] = "allow"
    await tools.execute(call("browser_navigate", {"url": "https://example.com/"}), session)
    (tmp_path / "index.html").write_text("changed source")
    assert (await tools.execute(call("browser_verify", {"kind": "text_contains", "expected": "Menu opened"}), session)).is_error
    await tools.execute(call("browser_reload"), session)
    assert not (await tools.execute(call("browser_verify", {"kind": "text_contains", "expected": "Menu opened"}), session)).is_error


def test_browser_schema_selection_is_compact_and_discovery_has_no_permission_effect(browser_runtime):
    control, manager, tools, session = browser_runtime
    schemas = tools.get_schemas(session)
    selected, report = select_tool_schemas(schemas, task="Fix mobile menu in frontend")
    names = {s["function"]["name"] for s in selected}
    assert {"browser_start", "browser_snapshot", "browser_verify", "browser_click"} <= names
    assert "browser_screenshot" not in names
    assert report["permission_effect"] == "none"
    assert "browser_start" not in {s["function"]["name"] for s in select_tool_schemas(schemas, task="Refactor Python parser")[0]}


@pytest.mark.asyncio
async def test_adapter_cancellation_closes_context_and_stops_pending_action(browser_runtime):
    control, manager, tools, session = browser_runtime
    started = asyncio.Event()
    async def pending(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    manager.dispatch = pending
    task = asyncio.create_task(tools.execute(call("browser_snapshot"), session))
    await started.wait()
    control.cancel_event.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.closed


@pytest.mark.asyncio
async def test_concurrent_read_does_not_erase_cancelled_navigation_uncertainty(browser_runtime):
    control, manager, tools, session = browser_runtime
    control.policy["browser.navigate"] = "allow"
    started = asyncio.Event()
    original = manager.dispatch
    async def pending(identity, grant, name, args, **kwargs):
        if name == "browser_navigate":
            started.set()
            await asyncio.Event().wait()
        return await original(identity, grant, name, args, **kwargs)
    manager.dispatch = pending
    task = asyncio.create_task(tools.execute(call("browser_navigate", {"url": "https://example.com/"}), session))
    await started.wait()
    assert not (await tools.execute(call("browser_status"), session)).is_error
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert control.unresolved_operations and control.unresolved_operations[-1]["requires_reconciliation"]
    assert (await tools.execute(call("browser_fill", {"target": {"label": "Name"}, "revision": 1, "value": "Ada"}), session)).is_error


@pytest.mark.asyncio
async def test_uncertain_browser_result_is_durable_and_blocks_new_effects(tmp_path):
    from firik_agent.browser.models import BrowserError
    from firik_agent.projects.db import ProjectDatabase
    from firik_agent.projects.models import Project
    from firik_agent.projects.coding_sessions import CodingSessionStore
    from firik_agent.projects.execution_journal import ExecutionJournal
    root = tmp_path / "workspace"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "state" / "projects.db")
    db.create_project(Project(id="project", name="Browser", local_path=str(root)))
    run = CodingSessionStore(db).create_run("project", session_id="conversation", mode="auto")
    control = CodingRunControl("conversation", "project", mode="auto", run_id=run.id,
        permissions={"allow_browser": True}, browser_grant=BrowserGrant(enabled=True, origins=("https://example.com",)))
    control.attach_journal(ExecutionJournal(db), workspace_root=root)
    control.policy["browser.navigate"] = "allow"
    manager = FakeBrowser()
    async def unknown(*args, **kwargs):
        raise BrowserError("tool_outcome_unknown", "Dispatch outcome unknown.", dispatched=True, requires_reconciliation=True)
    manager.dispatch = unknown
    tools = CodingToolFilter(root, control, browser_manager=manager)
    result = await tools.execute(call("browser_navigate", {"url": "https://example.com/"}), ChatSession(session_id="conversation"))
    assert result.is_error and result.metadata["requires_reconciliation"]
    steps = control.execution_journal.list_steps(run.id)
    assert steps[0]["state"] == "outcome_unknown"
    assert control.unresolved_operations
    assert not tools.completion_gate()["passed"]


@pytest.mark.asyncio
async def test_delegated_browser_capability_uses_an_independent_actor(browser_runtime):
    from firik_agent.projects.specialists import SpecialistCoordinator, SpecialistRole, WorkspaceBinding
    control, manager, tools, session = browser_runtime
    coordinator = SpecialistCoordinator(enabled=True)
    async def runner(assignment, child):
        return await child.execute(call("browser_start"))
    coordinator.register(SpecialistRole.TESTING, runner)
    await tools.execute(call("browser_start"), session)
    workspace = WorkspaceBinding(control.project_id, str(tools.root))
    denied = await coordinator.run(SpecialistRole.TESTING, workspace=workspace, parent_filter=tools,
        session=session, context={"agent_id": "child-denied"}, requested_permissions={}, requested_tools=["browser_start"])
    assert denied.is_error
    allowed = await coordinator.run(SpecialistRole.TESTING, workspace=workspace, parent_filter=tools,
        session=session, context={"agent_id": "child-testing"}, requested_permissions={"allow_browser": True}, requested_tools=["browser_start"])
    assert not allowed.is_error
    assert manager.calls[0][0].actor_id == "parent"
    assert manager.calls[-1][0].actor_id == "child-testing"
    assert manager.calls[0][0] != manager.calls[-1][0]
    control.permissions["allow_browser"] = False
    denied = await coordinator.run(SpecialistRole.TESTING, workspace=workspace, parent_filter=tools,
        session=session, context={"agent_id": "child-testing"}, requested_permissions={"allow_browser": True}, requested_tools=["browser_start"])
    assert denied.is_error


@pytest.mark.asyncio
async def test_dev_server_start_cannot_bypass_approval_or_live_policy(browser_runtime):
    control, manager, tools, session = browser_runtime
    calls = []
    async def start(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ready": True, "passed": True}
    tools.dev_servers.start = start
    def reject(event):
        if event.metadata.get("request_id"):
            control.decide_approval(event.metadata["request_id"], False)
    control.on_event = reject
    args = {"command": "npm run dev", "origin": "http://127.0.0.1:3000"}
    assert (await tools.tools.execute(call("start_dev_server", args))).is_error
    assert not calls
    control.mode = "plan"
    assert (await tools.execute(call("start_dev_server", args), session)).is_error
    control.mode = "auto"
    control.permissions["allow_browser"] = False
    assert (await tools.tools.execute(call("start_dev_server", args))).is_error
    assert not calls


@pytest.mark.asyncio
async def test_dev_server_cleanup_failure_does_not_skip_browser_cleanup(browser_runtime):
    control, manager, tools, session = browser_runtime
    await tools.execute(call("browser_start"), session)
    async def failed():
        raise OSError("fixture cleanup failure")
    tools.dev_servers.close = failed
    outcome = await tools.close_execution()
    assert outcome["retained"] and manager.closed
