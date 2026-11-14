"""Deterministic runtime lifecycle checks with an in-memory Playwright double."""
from unittest.mock import AsyncMock, Mock
import pytest
from firik_agent.browser import BrowserConfig, BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.manager import BrowserManager
from firik_agent.browser.models import BrowserSession, BrowserTab


def fixture_session(manager, identity):
    context = Mock(); context.close = AsyncMock()
    page = Mock(); page.is_closed.return_value = False; page.url = 'about:blank'
    session = BrowserSession(identity, context, BrowserGrant(enabled=True), state='ready')
    tab = BrowserTab(page); session.tabs[tab.tab_id] = tab; session.active_tab_id=tab.tab_id
    manager._sessions[identity] = session
    return session, tab


@pytest.mark.asyncio
async def test_manual_takeover_does_not_dismiss_user_dialogs():
    manager = BrowserManager(BrowserConfig(enabled=True))
    context, page = Mock(), Mock()
    session = BrowserSession(BrowserIdentity('manual-dialog'), context, BrowserGrant(True), state='manual_control')
    manager._track_page(session, page)
    handlers = {args.args[0]: args.args[1] for args in page.on.call_args_list}
    dialog = Mock(); dialog.dismiss = AsyncMock()
    assert handlers['dialog'](dialog) is None
    dialog.dismiss.assert_not_awaited()
    session.state = 'ready'
    await handlers['dialog'](dialog)
    dialog.dismiss.assert_awaited_once()
    await manager.close_all()


@pytest.mark.asyncio
async def test_ownership_exact_identity_project_actor_and_run():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat','project','run','parent')
    session,tab=fixture_session(manager,owner)
    for foreign in [BrowserIdentity('other','project','run'), BrowserIdentity('chat','other','run'),
                    BrowserIdentity('chat','project','other'), BrowserIdentity('chat','project','run','child')]:
        with pytest.raises(BrowserError) as error: await manager.dispatch(foreign,BrowserGrant(True),'snapshot',{'tab_id':tab.tab_id})
        assert error.value.code=='session_missing'
        assert manager.sessions(foreign)==[]
    assert len(manager.sessions(owner))==1
    await manager.close_all()
    context=session.context
    context.close.assert_awaited_once()
    assert manager.sessions(owner)==[]
    await manager.close_all()


@pytest.mark.asyncio
async def test_mutation_direct_dispatch_cannot_bypass_approval():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    for fingerprint in [None,'guessed-token']:
        with pytest.raises(BrowserError) as error:
            await manager.dispatch(owner,BrowserGrant(True),'click',{'target':{'role':'button','name':'Send'},'revision':1},approved_fingerprint=fingerprint)
        assert error.value.code=='approval_required'
    await manager.close_all()


@pytest.mark.asyncio
async def test_live_permission_revocation_and_manual_observations():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    session.state='manual_control'
    for operation in ['snapshot','get_console','screenshot','new_tab']:
        with pytest.raises(BrowserError) as error: await manager.dispatch(owner,BrowserGrant(True),'browser_'+operation)
        assert error.value.code=='manual_control'
    with pytest.raises(BrowserError) as error: await manager.dispatch(owner,BrowserGrant(False),'status')
    assert error.value.code=='permission_denied'
    await manager.close_owner(owner)
    assert manager.sessions(owner)==[]


@pytest.mark.asyncio
async def test_artifact_foreign_owner_and_cleanup():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('one'); other=BrowserIdentity('two')
    artifact=manager.artifacts.save(owner,b'png')
    assert manager.artifact_path(owner,artifact.artifact_id).read_bytes()==b'png'
    with pytest.raises(BrowserError): manager.artifact_path(other,artifact.artifact_id)
    manager.artifacts.remove_owner(owner)
    assert not artifact.path.exists()
    await manager.close_all()


@pytest.mark.asyncio
async def test_cancelled_launch_stops_partial_driver():
    import asyncio
    manager=BrowserManager(BrowserConfig(enabled=True)); started=asyncio.Event()
    async def launch(**kwargs):
        started.set()
        await asyncio.Event().wait()
    driver=Mock(); driver.chromium.launch=launch; driver.stop=AsyncMock()
    manager._playwright=driver
    task=asyncio.create_task(manager._browser(True))
    await started.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    driver.stop.assert_awaited_once()
    assert manager._playwright is None and not manager._browsers


@pytest.mark.asyncio
async def test_context_cleanup_failure_retains_ownership_for_retry():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    session.context.close.side_effect=OSError('fixture failure')
    with pytest.raises(BrowserError) as error: await manager.close_owner(owner)
    assert error.value.code=='cleanup_unresolved'
    assert manager.sessions(owner)[0]['state']=='failed'
    session.context.close.side_effect=None
    await manager.close_all()
    assert manager.sessions(owner)==[]


@pytest.mark.asyncio
async def test_preview_masks_inputs_in_every_frame():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    session.grant=BrowserGrant(enabled=True,allow_screenshots=True)
    main=Mock(); child=Mock();tab.page.frames=[main,child]; tab.page.screenshot=AsyncMock(return_value=b'png')
    assert await manager._capture(session,tab)==b'png'
    assert tab.page.screenshot.await_args.kwargs['mask']==[main.locator.return_value,child.locator.return_value]
    await manager.close_all()


@pytest.mark.asyncio
async def test_snapshot_capture_race_never_mints_valid_revision():
    manager=BrowserManager(BrowserConfig(enabled=True)); owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    manager._digest=AsyncMock(side_effect=['before','after'])
    tab.page.aria_snapshot=AsyncMock(return_value='- button "Go" [ref=e2]')
    with pytest.raises(BrowserError) as error: await manager.dispatch(owner,BrowserGrant(True),'snapshot')
    assert error.value.code=='stale_observation'
    assert tab.observed_digest==''
    await manager.close_all()


def test_observation_payloads_are_bounded_with_explicit_truncation():
    manager=BrowserManager(BrowserConfig(max_snapshot_chars=100))
    result=manager._bounded_items([{'text':'x'*60},{'text':'y'*60}], 'entries')
    assert len(result['entries'])==1 and result['truncated'] and result['omitted_entries']==1


@pytest.mark.asyncio
async def test_revocation_during_dns_validation_blocks_dispatch():
    import asyncio
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    active=[BrowserGrant(True,('https://example.com',))];manager.bind_grant_provider(owner,lambda:active[0])
    started=asyncio.Event();release=asyncio.Event()
    async def validate(url,grant):started.set();await release.wait();return 'https://example.com'
    manager.policy.validate=validate
    route=Mock();route.request.url='https://example.com';route.request.redirected_from=None;route.request.method='GET';route.request.resource_type='document'
    route.fetch=AsyncMock();route.abort=AsyncMock()
    task=asyncio.create_task(manager._route(session,route))
    await started.wait();active[0]=BrowserGrant();release.set();await task
    route.fetch.assert_not_awaited();route.abort.assert_awaited_once()
    await manager.close_all()


@pytest.mark.asyncio
async def test_preview_privacy_revoked_while_waiting_for_page_lock():
    import asyncio
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('chat');session,tab=fixture_session(manager,owner)
    active=[BrowserGrant(enabled=True,allow_screenshots=True)];manager.bind_grant_provider(owner,lambda:active[0])
    tab.page.screenshot=AsyncMock(return_value=b'png')
    await tab.lock.acquire()
    task=asyncio.create_task(manager.preview(owner,active[0]))
    await asyncio.sleep(0)
    active[0]=BrowserGrant(enabled=True,allow_screenshots=False);tab.lock.release()
    with pytest.raises(BrowserError) as error:await task
    assert error.value.code=='permission_denied';tab.page.screenshot.assert_not_awaited()
    await manager.close_all()


@pytest.mark.asyncio
async def test_inline_host_input_requires_takeover_and_cannot_be_called_as_agent_tool():
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('inline');session,tab=fixture_session(manager,owner)
    tab.page.mouse.click=AsyncMock();tab.page.keyboard.insert_text=AsyncMock();tab.page.viewport_size={'width':1280,'height':800}
    with pytest.raises(BrowserError) as error:await manager.host_input(owner,BrowserGrant(True),'click',{'x':12,'y':25})
    assert error.value.code=='host_action_required'
    handoff=await manager.host_command(owner,BrowserGrant(True),'request_handoff')
    assert handoff['state']=='manual_control' and handoff['tabs']
    await manager.host_input(owner,BrowserGrant(True),'click',{'x':12,'y':25})
    await manager.host_input(owner,BrowserGrant(True),'text',{'text':'hello'})
    tab.page.mouse.click.assert_awaited_once_with(12,25)
    tab.page.keyboard.insert_text.assert_awaited_once_with('hello')
    for args in ({'x':True,'y':1},{'x':1280,'y':5},{'x':-1,'y':1}):
        with pytest.raises(BrowserError):await manager.host_input(owner,BrowserGrant(True),'click',args)
    with pytest.raises(BrowserError) as error:await manager.dispatch(owner,BrowserGrant(True),'host_input',{'kind':'click'})
    assert error.value.code=='unsupported_capability'
    with pytest.raises(BrowserError) as error:await manager.host_command(owner,BrowserGrant(True),'fill',{})
    assert error.value.code=='unsupported_capability'
    assert 'tabs' not in await manager.dispatch(owner,BrowserGrant(True),'status')
    assert 'tabs' not in manager.sessions(owner)[0]
    assert 'tabs' in await manager.host_command(owner,BrowserGrant(True),'status')
    await manager.close_all()


@pytest.mark.asyncio
async def test_host_preview_during_manual_control_is_ephemeral_and_agent_remains_paused():
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('inline');session,tab=fixture_session(manager,owner)
    session.state='manual_control';tab.page.frames=[Mock()];tab.page.screenshot=AsyncMock(return_value=b'png')
    grant=BrowserGrant(enabled=True,allow_screenshots=True)
    assert await manager.host_preview(owner,grant)==b'png'
    assert not manager.artifacts._artifacts
    with pytest.raises(BrowserError) as error:await manager.preview(owner,grant)
    assert error.value.code=='manual_control'
    with pytest.raises(BrowserError) as error:await manager.host_preview(owner,BrowserGrant(True))
    assert error.value.code=='permission_denied'
    with pytest.raises(BrowserError) as error:await manager.host_preview(BrowserIdentity('foreign'),grant)
    assert error.value.code=='session_missing'
    await manager.close_all()


@pytest.mark.asyncio
async def test_explicit_start_replaces_failed_owned_context_and_disconnected_engine():
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('restart');session,tab=fixture_session(manager,owner)
    session.state='failed';session.cancelled=True
    active=BrowserGrant(True);manager.bind_grant_provider(owner,lambda:active)
    page=Mock();page.is_closed.return_value=False;page.url='about:blank'
    context=Mock();context.route=AsyncMock();context.route_web_socket=AsyncMock();context.new_page=AsyncMock(return_value=page);context.close=AsyncMock()
    engine=Mock();engine.new_context=AsyncMock(return_value=context);engine.version='test';engine.close=AsyncMock()
    disconnected=Mock();disconnected.is_connected.return_value=False
    manager._browsers[True]=disconnected
    driver=Mock();driver.chromium.launch=AsyncMock(return_value=engine);driver.stop=AsyncMock();manager._playwright=driver
    result=await manager.host_command(owner,active,'start',{'headless':True})
    assert result['state']=='ready' and result['context_id']!=session.context_id
    session.context.close.assert_awaited_once();driver.chromium.launch.assert_awaited_once()
    assert manager._live_grant(owner,BrowserGrant()) is active
    await manager.close_all()


@pytest.mark.asyncio
async def test_host_input_rechecks_revocation_after_awaited_destination_validation():
    import asyncio
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('inline');session,tab=fixture_session(manager,owner)
    session.state='manual_control';tab.page.url='https://example.com';tab.page.viewport_size={'width':1280,'height':800}
    tab.page.mouse.click=AsyncMock();started=asyncio.Event();release=asyncio.Event()
    active=[BrowserGrant(enabled=True,allow_public_https=True)]
    manager.bind_grant_provider(owner,lambda:active[0])
    async def validate(url,grant):started.set();await release.wait();return 'https://example.com'
    manager.policy.validate=validate
    pending=asyncio.create_task(manager.host_input(owner,active[0],'click',{'x':10,'y':10}))
    await started.wait();active[0]=BrowserGrant();release.set()
    with pytest.raises(BrowserError) as error:await pending
    assert error.value.code=='permission_denied';tab.page.mouse.click.assert_not_awaited()
    await manager.close_all()


@pytest.mark.asyncio
async def test_resume_retries_only_stale_read_and_reports_resumed_if_page_keeps_changing():
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('animated');session,tab=fixture_session(manager,owner)
    session.state='manual_control'
    manager.dispatch=AsyncMock(side_effect=[BrowserError('stale_observation','changed'), {'content':'stable','untrusted':True}])
    result=await manager.resume(owner,BrowserGrant(True))
    assert result['resumed_by_host'] and result['content']=='stable' and manager.dispatch.await_count==2
    session.state='manual_control';manager.dispatch=AsyncMock(side_effect=BrowserError('stale_observation','changed'))
    result=await manager.resume(owner,BrowserGrant(True))
    assert result['resumed_by_host'] and result['observation_pending'] and session.state=='ready'
    assert manager.dispatch.await_count==3
    await manager.close_all()


@pytest.mark.asyncio
async def test_cross_origin_resource_redirect_never_fetches_destination_or_leaks_headers():
    manager=BrowserManager(BrowserConfig(enabled=True));owner=BrowserIdentity('redirect');session,tab=fixture_session(manager,owner)
    manager.policy.validate=AsyncMock(return_value='https://example.com')
    response=Mock();response.status=302;response.headers={'location':'https://other.example/private'};response.dispose=AsyncMock()
    route=Mock();route.request.url='https://example.com/data';route.request.method='GET';route.request.resource_type='fetch'
    route.request.is_navigation_request.return_value=False
    route.fetch=AsyncMock(return_value=response);route.abort=AsyncMock();route.fulfill=AsyncMock()
    await manager._route(session,route)
    route.fetch.assert_awaited_once();route.fulfill.assert_not_awaited();route.abort.assert_awaited_once()
    assert tab.network_errors[-1]['failure']=='cross_origin_redirect_blocked'
    await manager.close_all()


@pytest.mark.asyncio
async def test_host_navigation_remains_available_after_agent_action_budget_is_exhausted():
    manager=BrowserManager(BrowserConfig(enabled=True,max_actions_per_task=1));owner=BrowserIdentity('budget');session,tab=fixture_session(manager,owner)
    session.actions=1
    manager._execute=AsyncMock(return_value={'url':'about:blank'})
    with pytest.raises(BrowserError) as error:await manager.dispatch(owner,BrowserGrant(True),'reload')
    assert error.value.code=='browser_limit'
    manager._execute.assert_not_awaited()
    await manager.host_command(owner,BrowserGrant(True),'reload')
    assert session.actions==1
    manager._execute.assert_awaited_once()
    await manager.close_all()


@pytest.mark.asyncio
async def test_browser_resize_and_auto_locale_timezone(monkeypatch):
    from firik_agent.browser.config import resolve_browser_locale, resolve_browser_timezone
    monkeypatch.setenv("FIRIK_BROWSER_LOCALE", "tr_TR.UTF-8")
    monkeypatch.setenv("TZ", "Europe/Istanbul")
    assert resolve_browser_locale("auto") == "tr-TR"
    assert resolve_browser_timezone("auto") == "Europe/Istanbul"
    assert resolve_browser_locale("fr-FR") == "fr-FR"
    assert resolve_browser_timezone("America/New_York") == "America/New_York"

    cfg = BrowserConfig(enabled=True)
    assert cfg.locale == "auto" and cfg.timezone_id == "auto"

    manager = BrowserManager(cfg)
    owner = BrowserIdentity("resize-owner")
    session, tab = fixture_session(manager, owner)
    tab.page.set_viewport_size = AsyncMock()
    new_page = Mock()
    new_page.is_closed.return_value = False
    new_page.url = "about:blank"
    new_page.set_viewport_size = AsyncMock()
    session.context.new_page = AsyncMock(side_effect=lambda: _attach_new_tab(manager, session, new_page))

    with pytest.raises(BrowserError) as error:
        await manager.dispatch(owner, BrowserGrant(enabled=True), "resize", {"width": 720, "height": 540})
    assert error.value.code == "host_action_required"
    tab.page.set_viewport_size.assert_not_awaited()

    res = await manager.host_command(owner, BrowserGrant(enabled=True), "resize", {"width": 720, "height": 540})
    assert res["state"] == "ready"
    assert tab.revision == 1
    assert session.viewport == (720, 540)
    tab.page.set_viewport_size.assert_awaited_once_with({"width": 720, "height": 540})

    created = await manager.host_command(owner, BrowserGrant(enabled=True), "new_tab", {})
    assert created["tab_id"] in session.tabs
    new_page.set_viewport_size.assert_awaited_once_with({"width": 720, "height": 540})
    await manager.close_all()


def _attach_new_tab(manager, session, page):
    tab = BrowserTab(page)
    session.tabs[tab.tab_id] = tab
    return page
