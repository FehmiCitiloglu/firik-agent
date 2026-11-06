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
