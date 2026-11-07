"""Automatic public web reads must enforce policy before each request."""
import json
import socket

import httpx
import pytest

from firik_agent.mcp.web_server import WebMCPServer


@pytest.fixture
def public_dns(monkeypatch):
    def resolve(host, *args, **kwargs):
        address = '127.0.0.1' if host == 'localhost' else '93.184.216.34'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)


@pytest.fixture
def requests(monkeypatch, public_dns):
    seen = []
    real_client = httpx.Client
    def install(handler):
        def respond(request):
            seen.append(request)
            return handler(request)
        def client(**kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
            assert kwargs.get('verify', True) is True
            return real_client(transport=httpx.MockTransport(respond), **kwargs)
        monkeypatch.setattr(httpx, 'Client', client)
        return seen
    return install


@pytest.mark.parametrize('url', [
    'http://example.com/', 'file:///etc/passwd', 'javascript:alert(1)',
    'https://user:pass@example.com/', 'https://127.0.0.1/',
    'https://localhost/', 'https://10.0.0.1/', 'https://169.254.169.254/',
    'https://[::1]/', 'https://example.com\\@127.0.0.1/',
])
def test_unsafe_destinations_never_dispatch(url, requests):
    seen = requests(lambda request: httpx.Response(200, text='should not load'))
    with pytest.raises(ValueError):
        WebMCPServer()._get(url)
    assert not seen


@pytest.mark.parametrize('destination', ['http://example.org/', 'https://127.0.0.1/', 'https://localhost/'])
def test_redirect_target_is_checked_before_request(destination, requests):
    seen = requests(lambda request: httpx.Response(302, headers={'location': destination}))
    with pytest.raises(ValueError):
        WebMCPServer()._get('https://example.com/')
    assert [str(request.url) for request in seen] == ['https://example.com/']


def test_any_public_https_site_and_safe_redirects_work_without_an_allowlist(requests):
    def respond(request):
        if request.url.host == 'example.com':
            return httpx.Response(302, headers={'location': 'https://other.example/docs'})
        return httpx.Response(200, headers={'content-type': 'text/html'},
                              text='<h1>Public page</h1><p>Readable</p>')
    seen = requests(respond)
    result = json.loads(WebMCPServer().execute_tool('web_open', {'url': 'https://example.com/'}))
    assert result['url'] == 'https://other.example/docs'
    assert result['text'] == 'Public page\nReadable'
    assert len(seen) == 2 and all(request.method == 'GET' for request in seen)
    assert not any('authorization' in request.headers for request in seen)


def test_dns_is_rechecked_after_same_host_redirect(monkeypatch, requests):
    seen = requests(lambda request: httpx.Response(302, headers={'location': '/next'}))
    answers = iter(['93.184.216.34', '10.0.0.1'])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (next(answers), 443))])
    with pytest.raises(ValueError, match='Private network'):
        WebMCPServer()._get('https://example.com/')
    assert len(seen) == 1


def test_redirect_loops_are_bounded(requests):
    seen = requests(lambda request: httpx.Response(302, headers={'location': '/again'}))
    with pytest.raises(ValueError, match='redirect limit'):
        WebMCPServer()._get('https://example.com/')
    assert len(seen) == 6


def test_page_and_search_limits_still_apply(requests, monkeypatch):
    from firik_agent.mcp import web_server
    requests(lambda request: httpx.Response(200, headers={'content-type': 'text/plain'}, text='x' * 11))
    monkeypatch.setattr(web_server, 'MAX_BYTES', 10)
    with pytest.raises(ValueError, match='download limit'):
        WebMCPServer()._get('https://example.com/')
    with pytest.raises(ValueError, match='query'):
        WebMCPServer().execute_tool('web_search', {'query': ''})


def test_search_excludes_unencrypted_result_urls():
    from firik_agent.mcp.web_server import SearchParser
    parser = SearchParser()
    parser.feed('<a class="result__a" href="http://example.com">Insecure</a>'
                '<a class="result__a" href="https://example.org">HTTPS</a>')
    assert parser.results == [{'title': 'HTTPS', 'url': 'https://example.org'}]


@pytest.mark.asyncio
async def test_automatic_web_reads_still_block_unsafe_sites_before_network(requests):
    from firik_agent.core.session import ChatSession, SessionToolFilter
    from firik_agent.core.types import ToolCall
    from firik_agent.mcp.connection import MCPConnectionManager
    from firik_agent.mcp.models import MCPServerConfig
    seen = requests(lambda request: httpx.Response(200, headers={'content-type': 'text/html'},
                                                   text='<p>Public HTTPS content</p>'))
    manager = MCPConnectionManager()
    await manager.add_server(MCPServerConfig(name='web', command='builtin:web'), connect_now=True)
    session = ChatSession(active_mcp_servers={'web'})
    session.browser_enabled = True
    gate = SessionToolFilter(manager.tool_registry, manager,
        external_tool_authorize=lambda *args: pytest.fail('Public Web should use network policy, not a popup'))
    try:
        for url in ('https://example.com/', 'https://another.example/',
                    'http://example.com/', 'https://127.0.0.1/'):
            call = ToolCall(id='site', function={'name': 'mcp_web_web_open',
                                                'arguments': json.dumps({'url': url})})
            result = await gate.execute(call, session)
            assert result.is_error == (url.startswith('http:') or '127.0.0.1' in url)
            assert result.metadata['host_public_web_policy'] is True
        assert [str(request.url) for request in seen] == [
            'https://example.com/', 'https://another.example/']
    finally:
        await manager.shutdown()
