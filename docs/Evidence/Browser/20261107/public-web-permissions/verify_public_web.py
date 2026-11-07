"""Real public Web MCP requests after browser taint, without model/provider use."""
import asyncio
import json
from pathlib import Path

from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.types import ToolCall
from firik_agent.mcp.connection import MCPConnectionManager
from firik_agent.mcp.models import MCPServerConfig


async def main():
    manager = MCPConnectionManager()
    session = ChatSession(active_mcp_servers={'web'})
    session.browser_enabled = True
    session.requires_external_tool_approval()
    approvals = []
    def reject(name, arguments):
        approvals.append(name)
        return False
    report = {'approval_requests': approvals, 'requests': []}
    try:
        await manager.add_server(MCPServerConfig(name='web', command='builtin:web'), connect_now=True)
        gate = SessionToolFilter(manager.tool_registry, manager, external_tool_authorize=reject)
        for operation, arguments, blocked in (
            ('web_search', {'query': 'interesting interactive websites', 'max_results': 3}, False),
            ('web_open', {'url': 'https://example.com/'}, False),
            ('web_open', {'url': 'https://playwright.dev/python'}, False),
            ('web_open', {'url': 'http://example.com/'}, True),
            ('web_open', {'url': 'https://127.0.0.1/'}, True),
        ):
            result = await gate.execute(ToolCall(id='public-web-acceptance', function={
                'name': 'mcp_web_' + operation, 'arguments': json.dumps(arguments)}), session)
            entry = {'tool': operation, 'arguments': arguments, 'is_error': result.is_error,
                     'public_web_policy': result.metadata.get('host_public_web_policy')}
            if result.is_error:
                entry['message'] = result.content[:500]
            else:
                value = json.loads(result.content)
                entry['result'] = ({'provider': value['provider'], 'result_count': len(value['results'])}
                                   if operation == 'web_search' else
                                   {'url': value['url'], 'text_characters': len(value['text'])})
            report['requests'].append(entry)
            assert result.metadata.get('host_public_web_policy') is True
            assert result.is_error == blocked, entry
        assert approvals == []
    finally:
        await manager.shutdown()
        Path(__file__).with_name('live-public-web.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
