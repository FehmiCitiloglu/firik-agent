"""Real public HTTPS acceptance; no user profile, login, model or provider call."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from firik_agent.browser import BrowserConfig, BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.manager import BrowserManager


async def main():
    manager = BrowserManager(BrowserConfig(enabled=True))
    owner = BrowserIdentity("public-https-acceptance")
    grant = BrowserGrant(enabled=True, allow_public_https=True, allow_screenshots=True)
    report = {"domain_allowlist": [], "pages": []}
    try:
        report["start"] = await manager.host_command(owner, grant, "start", {"headless": True})
        for url in ("https://example.com", "https://playwright.dev/python", "https://www.linkedin.com"):
            entry = {"requested_url": url}
            try:
                result = await manager.host_command(owner, grant, "navigate", {"url": url})
                assert 200 <= result["http_status"] < 400, result
                entry.update(result=result)
                tabs = await manager.host_command(owner, grant, "list_tabs", {})
                entry["tabs"] = tabs["tabs"]
                entry["preview_bytes"] = len(await manager.host_preview(owner, grant))
            except BrowserError as error:
                entry.update(error=error.code, message=str(error))
            report["pages"].append(entry)
        try:
            await manager.host_command(owner, grant, "navigate", {"url": "http://example.com"})
            raise AssertionError("Public HTTP was allowed")
        except BrowserError as error:
            assert error.code == "insecure_http_denied", error.code
            report["public_http"] = error.code
        await manager.host_command(owner, grant, "request_handoff", {})
        report["manual_preview_bytes"] = len(await manager.host_preview(owner, grant))
        try:
            await manager.dispatch(owner, grant, "snapshot")
            raise AssertionError("Agent observed a manually controlled page")
        except BrowserError as error:
            assert error.code == "manual_control", error.code
            report["manual_agent_observation"] = error.code
        report["resume"] = await manager.resume(owner, grant)
    finally:
        await manager.close_all()
        report["owned_sessions_after_close"] = manager.sessions(owner)
        destination = Path(__file__).with_name("public-https.json")
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    assert all("result" in page for page in report["pages"]), report["pages"]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
