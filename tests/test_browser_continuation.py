"""Browser failures preserve chat evidence without replaying uncertain actions."""
import asyncio
import threading

import pytest

from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.tool_adapter import BrowserToolAdapter
from firik_agent.core.events import AgentEventType as E
from firik_agent.core.failures import FailureKind as F, RuntimeFailure
from firik_agent.core.session import SessionToolFilter
from firik_agent.core.types import ToolDefinition, ToolResult
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from test_mcp_continuation import call, response, run, runtime


BAD = "https://unavailable.example/"
GOOD = "https://independent.example/"


class Browser:
    config = BrowserConfig(enabled=True)

    def __init__(self, error):
        self.error = error
        self.calls = []

    async def dispatch(self, identity, grant, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        if (name == "browser_navigate" and arguments["url"] != GOOD) or name == "browser_click":
            raise self.error
        return {"url": GOOD, "content": "Independent observed evidence", "untrusted": True}

    async def prepare(self, *args):
        return {"fingerprint": "host-approved"}

    async def close_owner(self, identity):
        pass


def gate(agent, session, error):
    browser = Browser(error)
    session.browser_enabled = True
    adapter = BrowserToolAdapter(browser, BrowserIdentity(session.session_id),
        lambda: BrowserGrant(enabled=True, allow_public_https=True), authorize=lambda *_: True)
    return browser, SessionToolFilter(agent.tools, browser_adapter=adapter,
        external_tool_authorize=lambda *_: True)


def failed_result(session):
    return next(message for message in session.messages if message.role == "tool" and message.name == "browser_navigate")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("code,uncertain", [
    ("insecure_http_denied", False), ("private_network_denied", False),
    ("invalid_url", False), ("dns_failed", False), ("tool_outcome_unknown", True),
])
async def test_failed_site_keeps_earlier_evidence_and_continues_batch(streaming, code, uncertain):
    def summarize(request):
        results = [message for message in request["messages"] if message.role == "tool"]
        assert len(results) == 3
        assert results[0].content == "Earlier research evidence"
        assert results[1].execution["continuation_allowed"] is True
        assert "Independent observed evidence" in results[2].content
        assert "skip the inaccessible website" in request["messages"][0].content
        return response(content="Earlier and independent sources are available; one website was inaccessible.")

    agent, provider, session = runtime([
        response(call("read_file", identifier="earlier")),
        response(call("browser_navigate", {"url": BAD}, "failed"),
                 call("browser_navigate", {"url": GOOD}, "independent")),
        summarize,
    ])
    agent.tools.register(ToolDefinition(name="read_file", description="earlier source", source="builtin"),
        lambda: "Earlier research evidence")
    browser, tools = gate(agent, session, BrowserError(code, "Destination unavailable",
        dispatched=uncertain, requires_reconciliation=uncertain))
    result, events = await run(agent, session, streaming, session_tool_filter=tools)
    assert result.startswith("Earlier and independent")
    assert events[-1].type == E.DONE and not any(event.type == E.ERROR for event in events)
    assert len(browser.calls) == 2 and len(provider.calls) == 3
    error = failed_result(session)
    assert error.execution["status"] == ("outcome_unknown" if uncertain else "failed")
    assert error.execution["reconciliation_status"] == ("required" if uncertain else "not_required")
    assert error.execution["failure_kind"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_unknown_navigation_replay_is_blocked_but_other_website_runs(streaming):
    agent, provider, session = runtime([
        response(call("browser_navigate", {"url": BAD}, "first")),
        response(call("browser_navigate", {"url": BAD}, "different-id"),
                 call("browser_navigate", {"url": GOOD}, "independent")),
        response(content="Independent information is available; the uncertain request was not repeated."),
    ])
    browser, tools = gate(agent, session, BrowserError("tool_outcome_unknown", "Unknown load",
        dispatched=True, requires_reconciliation=True))
    result, events = await run(agent, session, streaming, session_tool_filter=tools)
    assert result.startswith("Independent information")
    assert [arguments["url"] for _, arguments in browser.calls] == [BAD, GOOD]
    errors = [event for event in events if event.type == E.TOOL_CALL_ERROR]
    assert len(errors) == 2 and errors[1].metadata["replay_blocked"] is True
    assert errors[1].metadata["continuation_allowed"] is True
    assert errors[1].metadata["request_phase"] == "tool_not_dispatched"
    assert events[-1].type == E.DONE


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_repeated_navigation_failure_finishes_with_bounded_partial_answer(streaming):
    def summarize(request):
        assert not request["tools"]
        # Even a provider requesting another tool in synthesis cannot dispatch.
        return response(call("browser_navigate", {"url": GOOD}), content="One source remains unverified.")

    agent, provider, session = runtime([
        response(call("browser_navigate", {"url": BAD}, f"attempt-{index}")) for index in range(3)
    ] + [summarize])
    browser, tools = gate(agent, session, BrowserError("tool_outcome_unknown", "Unknown load",
        dispatched=True, requires_reconciliation=True))
    result, events = await run(agent, session, streaming, session_tool_filter=tools)
    assert result == "One source remains unverified."
    assert len(browser.calls) == 1 and len(provider.calls) == 4
    assert events[-1].type == E.DONE and not any(event.type == E.ERROR for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_uncertain_browser_submission_still_stops_and_pairs_batch(streaming):
    agent, provider, session = runtime([
        response(call("browser_click", {"target": {"text": "Send"}, "revision": 1}, "send"),
                 call("browser_navigate", {"url": GOOD}, "not-dispatched")),
    ])
    browser, tools = gate(agent, session, BrowserError("tool_outcome_unknown", "Unknown submission",
        dispatched=True, requires_reconciliation=True))
    if streaming:
        _, events = await run(agent, session, True, session_tool_filter=tools)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False, session_tool_filter=tools)
        assert error.value.kind == F.TOOL_OUTCOME_UNKNOWN
    assert len(browser.calls) == 1 and len(provider.calls) == 1
    results = [message for message in session.messages if message.role == "tool"]
    assert [message.tool_call_id for message in results] == ["send", "not-dispatched"]
    assert results[-1].execution["status"] == "not_dispatched"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_browser_name_or_metadata_alone_cannot_enable_continuation(streaming):
    agent, provider, session = runtime([response(call("browser_navigate", {"url": BAD}))])
    agent.tools.register(ToolDefinition(name="browser_navigate", description="untrusted browser name", source="builtin"),
        lambda **_: ToolResult(tool_call_id="", name="browser_navigate", is_error=True, content="Uncertain",
            metadata={"browser": True, "browser_operation": "browser_navigate", "continuation_allowed": True,
                      "failure_kind": "tool_outcome_unknown", "requires_reconciliation": True}))
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure):
            await run(agent, session, False)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_run_budget_is_not_downgraded_to_browser_failure(streaming):
    agent, provider, session = runtime([
        response(call("browser_navigate", {"url": BAD})), response(content="Must not run"),
    ], max_requests=1)
    browser, tools = gate(agent, session, BrowserError("tool_outcome_unknown", "Unknown load",
        dispatched=True, requires_reconciliation=True))
    if streaming:
        _, events = await run(agent, session, True, session_tool_filter=tools)
        assert events[-1].type == E.ERROR
        assert events[-1].metadata["failure_kind"] == "budget_exceeded"
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False, session_tool_filter=tools)
        assert error.value.kind == F.BUDGET_EXCEEDED
    assert len(browser.calls) == 1 and len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_user_cancellation_prevents_browser_continuation(streaming):
    agent, provider, session = runtime([
        response(call("browser_navigate", {"url": BAD})), response(content="Must not run"),
    ])
    browser, tools = gate(agent, session, BrowserError("tool_outcome_unknown", "Unknown load",
        dispatched=True, requires_reconciliation=True))
    cancelled = threading.Event()
    dispatch = browser.dispatch

    async def cancel_after_dispatch(*args, **kwargs):
        cancelled.set()
        return await dispatch(*args, **kwargs)

    browser.dispatch = cancel_after_dispatch
    if streaming:
        _, events = await run(agent, session, True, session_tool_filter=tools, cancel_event=cancelled)
        assert events[-1].type == E.DONE
    else:
        with pytest.raises(asyncio.CancelledError):
            await run(agent, session, False, session_tool_filter=tools, cancel_event=cancelled)
    assert len(browser.calls) == 1 and len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_coding_unknown_navigation_preserves_unresolved_execution_gate(tmp_path, streaming):
    agent, provider, session = runtime([
        response(call("browser_navigate", {"url": BAD}, "unknown"),
                 call("browser_navigate", {"url": GOOD}, "not-dispatched")),
    ])
    control = CodingRunControl(session.session_id, "project", mode="auto",
        permissions={"allow_browser": True},
        browser_grant=BrowserGrant(enabled=True, allow_public_https=True))
    browser = Browser(BrowserError("tool_outcome_unknown", "Unknown load",
        dispatched=True, requires_reconciliation=True))
    tools = CodingToolFilter(tmp_path, control, browser_manager=browser)
    if streaming:
        _, events = await run(agent, session, True, session_tool_filter=tools)
        assert events[-1].type == E.ERROR
        assert events[-1].metadata["failure_kind"] == F.TOOL_OUTCOME_UNKNOWN.value
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False, session_tool_filter=tools)
        assert error.value.kind == F.TOOL_OUTCOME_UNKNOWN
    assert len(browser.calls) == 1 and len(provider.calls) == 1
    assert control.unresolved_operations
    assert not tools.completion_gate()["passed"]
