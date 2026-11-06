"""Real Chromium verifies a guarded source edit; no provider requests or quota."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket

import pytest

from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.manager import BrowserManager
from firik_agent.browser.models import BrowserGrant
from firik_agent.core.session import ChatSession
from firik_agent.core.types import ToolCall
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter


pytestmark = [pytest.mark.asyncio, pytest.mark.browser_integration,
    pytest.mark.skipif(os.environ.get("FIRIK_BROWSER_INTEGRATION") != "1", reason="explicit Chromium integration opt-in required")]


def call(name, **args):
    return ToolCall(id="real_" + name, function={"name": name, "arguments": json.dumps(args)})


async def test_guarded_mobile_menu_fix_and_browser_evidence(tmp_path, python_command):
    pytest.importorskip("playwright")
    source = tmp_path / "index.html"
    source.write_text('''<!doctype html><html><head><title>Firik browser acceptance</title></head>
<body><h1>Local mobile menu fixture</h1><button aria-label="Menu" onclick="void 0">Menu</button>
<nav hidden id="menu">Menu opened</nav><label>Name<input id="name"></label>
<script>console.warn("fixture warning");</script></body></html>''')
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = "http://127.0.0.1:" + str(port)
    command = python_command("-m", "http.server", str(port), "--bind", "127.0.0.1")
    manager = BrowserManager(BrowserConfig(enabled=True))
    control = CodingRunControl("acceptance", "fixture", mode="auto", permissions={"allow_browser": True},
        browser_grant=BrowserGrant(enabled=True, origins=(origin,), allow_private_origins=(origin,), allow_screenshots=True))
    approvals = []
    def approve(event):
        from firik_agent.core.events import AgentEventType
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            approvals.append(event.metadata)
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = approve
    tools, session = CodingToolFilter(tmp_path, control, browser_manager=manager), ChatSession(session_id="acceptance")
    async def execute(name, **args):
        result = await tools.execute(call(name, **args), session)
        assert not result.is_error, result.content
        return json.loads(result.content)
    try:
        server = await execute("start_dev_server", command=command, origin=origin, ready_timeout=10, lifetime=60)
        assert server["ready"] and server["execution_boundary"] == "host"
        started = await execute("browser_start", width=390, height=844)
        await execute("browser_navigate", url=origin + "/index.html")
        before = await execute("browser_snapshot")
        await execute("browser_click", target={"role": "button", "name": "Menu"}, revision=before["revision"])
        failed = await tools.execute(call("browser_verify", kind="text_contains", expected="Menu opened"), session)
        assert failed.is_error, "The broken menu must be reproduced before fixing it."
        content = source.read_bytes()
        await execute("edit_file", path="index.html", expected_hash=hashlib.sha256(content).hexdigest(),
            old_text='onclick="void 0"', new_text='onclick="document.getElementById(\'menu\').hidden = false"')
        await execute("browser_reload")
        refreshed = await execute("browser_snapshot")
        await execute("browser_click", target={"role": "button", "name": "Menu"}, revision=refreshed["revision"])
        verified = await execute("browser_verify", kind="text_contains", expected="Menu opened")
        assert verified["passed"] and verified["fresh"] and verified["workspace_hash"]
        await execute("record_verification", criterion="Mobile menu opens at 390px", status="passed", evidence_id=verified["evidence_id"])
        fields = await execute("browser_snapshot")
        await execute("browser_fill", target={"label": "Name"}, revision=fields["revision"], value="Firik fixture")
        screenshot = await execute("browser_screenshot")
        path = manager.artifact_path(tools.browser_adapter.identity, screenshot["artifact_id"])
        assert path.is_file() and path.read_bytes().startswith(b"\x89PNG")
        diagnostics = await execute("browser_get_console")
        assert any(entry["type"] == "warning" for entry in diagnostics["entries"])
        assert approvals and any(a["tool_name"] == "browser_click" for a in approvals)
        # The existing native loop drives this same real browser. The provider
        # here is scripted so the acceptance test never consumes LLM quota.
        from firik_agent.core.agent import Agent
        from firik_agent.core.events import AgentEventType
        from firik_agent.core.types import Message, StreamChunk
        class OfflineProvider:
            name, current_model = "fixture", "offline-browser"
            model_metadata = {"offline-browser": {"source": "verified_config", "supports_tools": True}}
            calls = 0
            async def chat(self, **kwargs):
                self.calls += 1
                sequence = [("start_dev_server", {"command": command, "origin": origin, "ready_timeout": 10, "lifetime": 60}),
                            ("browser_status", {}), ("browser_snapshot", {}),
                            ("browser_verify", {"kind": "text_contains", "expected": "Menu opened"})]
                if self.calls <= len(sequence):
                    name, args = sequence[self.calls - 1]
                    return Message(role="assistant", tool_calls=[call(name, **args)])
                return Message(role="assistant", content="Verified the local menu through browser tools.")
            async def chat_stream(self, **kwargs):
                response = await self.chat(**kwargs)
                yield StreamChunk(delta_content=response.content, tool_calls=response.tool_calls)
        agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
            "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"}})
        agent._is_initialized = True
        provider = OfflineProvider()
        agent.providers.register("fixture", provider, set_active=True)
        try:
            events = [event async for event in agent.run_stream("Verify the local browser menu", session=session,
                session_tool_filter=tools, run_control=control)]
            assert not [event for event in events if event.type == AgentEventType.ERROR]
            assert provider.calls == 5
            assert any(message.role == "tool" and message.name == "browser_verify" for message in session.messages)
        finally:
            await agent.close()
        evidence_dir = os.environ.get("FIRIK_BROWSER_EVIDENCE_DIR")
        if evidence_dir:
            destination = Path(evidence_dir)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination / "mobile-menu.png")
            (destination / "coding-demo.json").write_text(json.dumps({
                "scenario": "broken mobile menu reproduced, guarded edit, reload, approved click, real DOM verification",
                "browser_version": started["browser_version"], "viewport": {"width": 390, "height": 844},
                "evidence": verified, "form_filling": "observed", "screenshot": "mobile-menu.png",
                "console": diagnostics, "approved_actions": [a["tool_name"] for a in approvals],
                "native_harness": "Agent.run_stream with scripted provider, supervised dev-server reuse and real Chromium, five model turns",
                "development_server": {key: server[key] for key in ("ready", "origin", "backend", "execution_boundary", "command_id", "http_status")},
            }, indent=2))
        await tools.close_execution()
        assert not manager.sessions(tools.browser_adapter.identity)
        assert not path.exists(), "Temporary screenshots must be deleted on close."
    finally:
        await tools.close_execution()
        await manager.close_all()
