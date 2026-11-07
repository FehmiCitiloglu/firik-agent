"""A failed website must not trap the managed browser on its error page."""
import os
from unittest.mock import AsyncMock, Mock

import pytest

from firik_agent.browser import BrowserConfig, BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.manager import BrowserManager
from firik_agent.browser.models import BrowserSession, BrowserTab


PUBLIC = "https://93.184.216.34"
OTHER = "https://1.1.1.1/independent"


@pytest.fixture
def owned_browser():
    manager = BrowserManager(BrowserConfig(enabled=True))
    identity = BrowserIdentity("navigation-failures")
    grant = BrowserGrant(enabled=True, allow_public_https=True)
    context = Mock(close=AsyncMock())
    page = Mock(url="about:blank", is_closed=Mock(return_value=False),
                title=AsyncMock(return_value="Independent source"))
    page.main_frame = Mock(page=page)
    session = BrowserSession(identity, context, grant, state="ready")
    tab = BrowserTab(page)
    session.tabs[tab.tab_id] = tab
    session.active_tab_id = tab.tab_id
    manager._sessions[identity] = session
    return manager, identity, grant, session, tab


def route_for(tab, *, destination="https://127.0.0.1/private", method="GET", main_frame=True):
    page = tab.page
    request = Mock(url=PUBLIC + "/redirect", method=method, resource_type="document",
                   is_navigation_request=Mock(return_value=main_frame))
    request.frame = page.main_frame if main_frame else Mock(page=page)
    response = Mock(status=302, headers={"location": destination}, dispose=AsyncMock())
    return Mock(request=request, fetch=AsyncMock(return_value=response), abort=AsyncMock(),
                fulfill=AsyncMock())


def observed_response():
    return Mock(status=200, request=Mock(redirected_from=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("url, code", [
    ("http://93.184.216.34/", "insecure_http_denied"),
    ("https://127.0.0.1/private", "private_network_denied"),
    ("file:///etc/passwd", "invalid_url"),
])
async def test_direct_policy_block_never_dispatches_and_next_source_works(owned_browser, url, code):
    manager, identity, grant, session, tab = owned_browser

    async def navigate(destination, **kwargs):
        tab.page.url = destination
        return observed_response()

    tab.page.goto = AsyncMock(side_effect=navigate)
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, grant, "navigate", {"url": url})
    assert error.value.code == code
    assert not error.value.dispatched and not error.value.requires_reconciliation
    tab.page.goto.assert_not_awaited()
    result = await manager.dispatch(identity, grant, "navigate", {"url": OTHER})
    assert result["url"] == OTHER and session.state == "ready"
    await manager.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("destination, code", [
    ("https://127.0.0.1/private", "private_network_denied"),
    ("http://93.184.216.34/insecure", "insecure_http_denied"),
    ("file:///etc/passwd", "invalid_url"),
])
async def test_redirect_policy_reason_survives_playwright_abort_and_other_site_loads(
        owned_browser, destination, code):
    manager, identity, grant, session, tab = owned_browser
    route = route_for(tab, destination=destination)

    async def navigate(url, **kwargs):
        if url == PUBLIC + "/redirect":
            await manager._route(session, route)
            tab.page.url = "chrome-error://chromewebdata/"
            raise RuntimeError("Playwright ERR_FAILED; private diagnostics")
        tab.page.url = url
        return observed_response()

    tab.page.goto = AsyncMock(side_effect=navigate)
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, grant, "navigate", {"url": PUBLIC + "/redirect"})
    assert error.value.code == code
    assert not error.value.requires_reconciliation
    assert "private diagnostics" not in str(error.value)
    route.fetch.assert_awaited_once()  # The blocked redirect destination was never fetched.
    route.abort.assert_awaited_once_with("aborted")
    assert not manager._navigation_errors and session.state == "ready"
    result = await manager.dispatch(identity, grant, "navigate", {"url": OTHER})
    assert result["url"] == OTHER
    assert [call.args[0] for call in tab.page.goto.await_args_list] == [PUBLIC + "/redirect", OTHER]
    await manager.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("current_url", ["chrome-error://chromewebdata/", "about:neterror", "https://127.0.0.1/"])
async def test_new_destination_can_escape_internal_or_denied_current_url(owned_browser, current_url):
    manager, identity, grant, session, tab = owned_browser
    tab.page.url = current_url
    tab.page.goto = AsyncMock(return_value=observed_response())

    async def navigate(url, **kwargs):
        tab.page.url = url
        return observed_response()

    tab.page.goto.side_effect = navigate
    result = await manager.dispatch(identity, grant, "navigate", {"url": OTHER})
    assert result["url"] == OTHER
    await manager.close_all()


@pytest.mark.asyncio
async def test_failed_subresource_cannot_hide_uncertain_main_navigation(owned_browser):
    manager, identity, grant, session, tab = owned_browser
    route = route_for(tab, main_frame=False)

    async def navigate(url, **kwargs):
        await manager._route(session, route)
        raise RuntimeError("unknown navigation failure")

    tab.page.goto = AsyncMock(side_effect=navigate)
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, grant, "navigate", {"url": PUBLIC + "/page"})
    assert error.value.code == "tool_outcome_unknown"
    assert error.value.dispatched and error.value.requires_reconciliation
    assert not manager._navigation_errors
    await manager.close_all()


@pytest.mark.asyncio
async def test_policy_refusal_after_dispatched_post_retains_reconciliation(owned_browser):
    manager, identity, grant, session, tab = owned_browser
    route = route_for(tab, method="POST")

    async def reload(**kwargs):
        await manager._route(session, route)
        raise RuntimeError("POST reload aborted after redirect")

    tab.page.url = PUBLIC + "/form"
    tab.page.reload = AsyncMock(side_effect=reload)
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, grant, "reload")
    assert error.value.code == "private_network_denied"
    assert error.value.dispatched and error.value.requires_reconciliation
    route.fetch.assert_awaited_once()
    await manager.close_all()


@pytest.mark.asyncio
async def test_previous_policy_error_does_not_mask_later_unknown_navigation(owned_browser):
    manager, identity, grant, session, tab = owned_browser
    manager._navigation_errors[tab.tab_id] = BrowserError("private_network_denied", "Previous page")
    tab.page.goto = AsyncMock(side_effect=RuntimeError("new unrelated transport failure"))
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, grant, "navigate", {"url": OTHER})
    assert error.value.code == "tool_outcome_unknown" and error.value.requires_reconciliation
    await manager.close_all()


@pytest.mark.browser_integration
@pytest.mark.skipif(os.environ.get("FIRIK_BROWSER_INTEGRATION") != "1",
                    reason="explicit Chromium integration opt-in required")
@pytest.mark.asyncio
async def test_real_chromium_blocked_redirect_then_independent_site():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    pytest.importorskip("playwright.async_api")
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.server.server_port, self.path))
            if self.path == "/blocked":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/private")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Backup source</title><h1>Independent source loaded</h1>")

    source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    backup = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (source, backup)]
    for thread in threads:
        thread.start()
    origins = tuple(f"http://127.0.0.1:{server.server_port}" for server in (source, backup))
    manager = BrowserManager(BrowserConfig(enabled=True))
    identity = BrowserIdentity("real-navigation-recovery")
    grant = BrowserGrant(enabled=True, origins=origins, allow_private_origins=origins)
    try:
        await manager.dispatch(identity, grant, "start")
        with pytest.raises(BrowserError) as error:
            await manager.dispatch(identity, grant, "navigate", {"url": origins[0] + "/blocked"})
        assert error.value.code == "origin_denied"
        assert not error.value.requires_reconciliation
        result = await manager.dispatch(identity, grant, "navigate", {"url": origins[1] + "/backup"})
        assert result["url"] == origins[1] + "/backup"
        assert result["title"] == "Backup source"
        text = await manager.dispatch(identity, grant, "extract_text")
        assert "Independent source loaded" in text["content"]
        assert hits == [(source.server_port, "/blocked"), (backup.server_port, "/backup")]
    finally:
        await manager.close_all()
        for server, thread in zip((source, backup), threads):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
