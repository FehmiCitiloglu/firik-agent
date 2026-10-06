"""Desktop defaults that work inside packaged apps without external Python."""
from __future__ import annotations


def add_desktop_defaults(config: dict) -> None:
    servers = config.setdefault("mcp_servers", {})
    disabled = config.setdefault("disabled_default_mcps", [])
    if "computer-use" not in servers and "computer-use" not in disabled:
        servers["computer-use"] = {
            "transport": "stdio", "command": "builtin:computer-use",
            "enabled": True, "autostart": True, "default_for_chat": True,
        }
