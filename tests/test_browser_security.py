"""Host security boundary tests; these need neither Playwright nor Chromium."""
import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.manager import BrowserManager
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.security import BrowserSecurityPolicy, origin_of, redact, sanitized_url


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,x', 'chrome://settings',
    'https://user:pass@example.com/', 'https://example.com\\@127.0.0.1/', 'https://example.com\n/', 'http://[::1%25lo0]/', 'https://example.com:0/'])
def test_forbidden_url_schemes_credentials_and_ambiguity(url):
    with pytest.raises(BrowserError): origin_of(url)


def test_origin_canonicalization_and_redaction():
    assert origin_of('https://EXAMPLE.COM:443/path?q=1') == 'https://example.com'
    assert origin_of('http://[::1]:8000/') == 'http://[::1]:8000'
    assert sanitized_url('https://x.example/path?token=private#secret') == 'https://x.example/path'
    assert 'topsecret' not in redact('password=topsecret Bearer abcdefghijklmnop')


@pytest.mark.asyncio
async def test_disabled_exact_origin_and_private_network():
    policy = BrowserSecurityPolicy()
    with pytest.raises(BrowserError, match='disabled'):
        await policy.validate('http://127.0.0.1:8000', BrowserGrant())
    grant = BrowserGrant(enabled=True, origins=('http://127.0.0.1:8000',))
    with pytest.raises(BrowserError) as error:
        await policy.validate('http://127.0.0.1:8000', grant)
    assert error.value.code == 'private_network_denied'
    with pytest.raises(BrowserError) as error:
        await policy.validate('http://127.0.0.1:8001', grant)
    assert error.value.code == 'origin_denied'
    grant = BrowserGrant(enabled=True, origins=grant.origins, allow_private_origins=grant.origins)
    assert await policy.validate('http://127.0.0.1:8000/test', grant) == 'http://127.0.0.1:8000'


@pytest.mark.asyncio
async def test_dns_changes_and_mixed_destinations_fail_closed(monkeypatch):
    policy = BrowserSecurityPolicy()
    loop = asyncio.get_running_loop()
    resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))])
    monkeypatch.setattr(loop, 'getaddrinfo', resolver)
    grant = BrowserGrant(enabled=True, origins=('https://example.com',))
    assert await policy.validate('https://example.com', grant) == 'https://example.com'
    resolver.return_value += [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 0))]
    with pytest.raises(BrowserError) as error:
        await policy.validate('https://example.com', grant)
    assert error.value.code == 'private_network_denied'


@pytest.mark.asyncio
async def test_public_https_grant_opens_unlisted_sites_and_their_resources(monkeypatch):
    policy = BrowserSecurityPolicy()
    resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))])
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolver)
    grant = BrowserGrant(enabled=True, allow_public_https=True)
    for url in ('https://example.com/path', 'https://cdn.example.org/app.js', 'https://other.example/'):
        assert await policy.validate(url, grant) == origin_of(url)
    assert resolver.await_count == 3
    # Existing explicitly scoped grants do not silently acquire broad access.
    with pytest.raises(BrowserError) as error:
        await policy.validate('https://other.example/', BrowserGrant(enabled=True, origins=('https://example.com',)))
    assert error.value.code == 'origin_denied'


@pytest.mark.asyncio
async def test_public_browsing_blocks_http_and_private_addresses_even_when_listed(monkeypatch):
    policy = BrowserSecurityPolicy()
    resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))])
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolver)
    for origins in ((), ('http://example.com',)):
        with pytest.raises(BrowserError) as error:
            await policy.validate('http://example.com/path', BrowserGrant(enabled=True, origins=origins, allow_public_https=True))
        assert error.value.code == 'insecure_http_denied'
    grant = BrowserGrant(enabled=True, allow_public_https=True)
    for url in ('https://127.0.0.1/', 'https://169.254.169.254/', 'https://[::1]/'):
        with pytest.raises(BrowserError) as error:
            await policy.validate(url, grant)
        assert error.value.code == 'private_network_denied'
    resolver.return_value += [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 0))]
    with pytest.raises(BrowserError) as error:
        await policy.validate('https://example.com/', grant)
    assert error.value.code == 'private_network_denied'


@pytest.mark.asyncio
async def test_strict_network_boundary_missing_and_disabled_host():
    manager = BrowserManager(BrowserConfig(enabled=True, strict_network_isolation=True))
    identity = BrowserIdentity('one')
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, BrowserGrant(enabled=True), 'start')
    assert error.value.code == 'network_isolation_unavailable'
    manager = BrowserManager()
    with pytest.raises(BrowserError) as error:
        await manager.dispatch(identity, BrowserGrant(enabled=True), 'start')
    assert error.value.code == 'permission_denied'


@pytest.mark.asyncio
async def test_unknown_or_model_owned_arguments_and_host_resume_rejected():
    manager = BrowserManager(BrowserConfig(enabled=True)); identity = BrowserIdentity('one'); grant = BrowserGrant(True)
    for operation, args in [('start', {'session_id':'two'}), ('start', {'headless':'false'}), ('navigate', {'url':1}),
        ('click', {'target':{'css':'button'},'revision':1}), ('click', {'target':{'role':'button','label':'Go'},'revision':1}),
        ('start', {'executable_path':'/tmp/chromium'})]:
        with pytest.raises(BrowserError) as error: await manager.dispatch(identity, grant, operation, args)
        assert error.value.code == 'invalid_tool_arguments'
    with pytest.raises(BrowserError) as error: await manager.dispatch(identity, grant, 'resume_after_handoff')
    assert error.value.code == 'host_action_required'
    for operation in ['upload','download']:
        with pytest.raises(BrowserError) as error: await manager.dispatch(identity, grant, operation)
        assert error.value.code == 'unsupported_capability'


@pytest.mark.parametrize('kwargs', [dict(enabled='false'),dict(allow_screenshots=1),dict(allow_public_https='true'),dict(origins=['https://example.com']),
    dict(origins=('https://example.com/path',)),dict(origins=('https://example.com?token=x',)),
    dict(origins=('https://example.com',)*33),dict(allow_private_origins=('http://localhost:8000',))])
def test_grant_flags_and_origin_authority_are_strictly_validated(kwargs):
    with pytest.raises(ValueError):BrowserGrant(**kwargs)


@pytest.mark.parametrize('kwargs', [dict(conversation_id=1),dict(conversation_id=''),dict(conversation_id='x',actor_id=''),
    dict(conversation_id='x',project_id='x'*257),dict(conversation_id='x',run_id='bad\nidentity')])
def test_host_identity_fields_are_validated(kwargs):
    with pytest.raises(ValueError):BrowserIdentity(**kwargs)


def test_grant_canonicalizes_origins_and_config_flags_are_strict():
    from pydantic import ValidationError
    assert BrowserGrant(origins=('https://EXAMPLE.COM:443/',)).origins==('https://example.com',)
    with pytest.raises(ValidationError):BrowserConfig(enabled='true')
