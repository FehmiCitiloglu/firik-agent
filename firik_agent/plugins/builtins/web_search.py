from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Tuple
import httpx
from firik_agent.core.types import ToolDefinition
from firik_agent.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class WebSearchPlugin(BasePlugin):
    """Provides tools to fetch web content and perform search queries."""

    name = "web_search"
    description = "Provides HTTP request and web content inspection tools"
    version = "1.0.0"

    def get_tools(self) -> List[Tuple[ToolDefinition, Callable[..., Any]]]:
        tool_fetch = ToolDefinition(
            name="fetch_web_page",
            description="Fetch the text or HTML content from a public URL via HTTP GET",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full HTTP/HTTPS URL to fetch",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 4000)",
                    },
                },
                "required": ["url"],
            },
            source="plugin:web_search",
        )

        async def fetch_web_page(url: str, max_length: int = 4000) -> str:
            try:
                headers = {"User-Agent": "FirikAgent/0.1.0"}
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url, headers=headers)
                    text = resp.text
                    if len(text) > max_length:
                        text = text[:max_length] + f"\n... [Truncated {len(text) - max_length} chars]"
                    return f"Status: {resp.status_code}\nURL: {resp.url}\n\n{text}"
            except Exception as e:
                return f"Error fetching URL: {e}"

        return [(tool_fetch, fetch_web_page)]
