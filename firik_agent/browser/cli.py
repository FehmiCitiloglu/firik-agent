"""Explicit browser diagnostics and tasks through the existing native harness."""
from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


async def _terminal_prompt(message: str) -> str:
    # prompt_toolkit is already a native CLI dependency. Its async prompt can be
    # cancelled; a blocking input() in an executor can keep shutdown waiting.
    from prompt_toolkit import PromptSession
    return await PromptSession().prompt_async(message)


def doctor() -> dict[str, Any]:
    """Inspect installation only: no network, browser launch or download."""
    result: dict[str, Any] = {
        "platform": platform.system(), "architecture": platform.machine(),
        "python": platform.python_version(), "playwright_installed": False,
        "chromium_available": False, "launch_verified": False,
        "headful_display_configured": (bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")) if sys.platform == "linux" else None),
        "headless_support": "not probed", "headful_support": "not probed",
        "security_boundary": "host browser with request policy; not OS/network isolation",
        "session_permission": "disabled until an explicit conversation/project grant",
        "strict_network_isolation": "unsupported; rejected",
    }
    try:
        result["playwright_version"] = version("playwright")
        result["playwright_installed"] = True
    except PackageNotFoundError:
        result["setup"] = 'Install firik-agent[browser], then run firik-agent browser install.'
        return result
    # Playwright's supported executable discovery reads its distribution's
    # matching browser registry; it does not search arbitrary PATH executables.
    try:
        from firik_agent.browser.config import configure_frozen_browser_cache
        configure_frozen_browser_cache()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            executable = Path(pw.chromium.executable_path)
            result["chromium_available"] = executable.is_file()
            result["chromium_executable"] = str(executable)
    except Exception as error:
        result["driver_error"] = type(error).__name__
    if not result["chromium_available"]:
        result["setup"] = "Run firik-agent browser install to explicitly install matching Chromium."
    return result


async def _launch_probe(headful: bool = False) -> dict[str, Any]:
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.manager import BrowserManager
    from firik_agent.browser.models import BrowserGrant, BrowserIdentity
    manager = BrowserManager(BrowserConfig(enabled=True, headless=not headful))
    identity, grant = BrowserIdentity("browser-doctor"), BrowserGrant(enabled=True)
    try:
        return await manager.dispatch(identity, grant, "browser_start", {})
    finally:
        await manager.close_all()


async def _task(args) -> int:
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.manager import BrowserManager
    from firik_agent.browser.models import BrowserGrant, BrowserIdentity
    from firik_agent.browser.tool_adapter import BrowserToolAdapter
    from firik_agent.core.agent import Agent
    from firik_agent.core.session import ChatSession, SessionToolFilter
    from firik_agent.cli import ensure_provider_key

    agent = Agent(config_path=args.config)
    if args.provider:
        agent.providers.set_active(args.provider)
    if args.model:
        agent.providers.get_active().set_model(args.model)
    await agent.initialize()
    session = ChatSession(model=agent.providers.get_active().current_model)
    session.browser_enabled = True
    manager = BrowserManager(BrowserConfig(enabled=True, headless=not args.headful))
    identity = BrowserIdentity(session.session_id)
    grant = BrowserGrant(enabled=True, origins=tuple(args.origin),
                         allow_private_origins=tuple(args.allow_private_origin), allow_screenshots=args.screenshots)

    async def authorize(action, name, preview):
        if action in {"browser.observe", "browser.navigate", "browser.debug"}:
            return
        if not sys.stdin.isatty():
            raise PermissionError("This browser action requires an interactive terminal approval.")
        print(json.dumps({"tool": name, "action": action, "preview": preview}, ensure_ascii=False, indent=2))
        answer = await _terminal_prompt("Approve this exact action? [y/N] ")
        if answer.strip().lower() != "y":
            raise PermissionError("Browser action rejected by user.")

    adapter = BrowserToolAdapter(manager, identity, lambda: grant, authorize=authorize)
    async def external_authorize(name, arguments):
        if not sys.stdin.isatty():
            return False
        definition = agent.tools.get_tool(name)
        print(json.dumps({"tool": name, "arguments": arguments,
            "execution_boundary": definition.execution_boundary if definition else "unknown",
            "reason": "This conversation contains untrusted browser content. Review this separate tool operation."},
            ensure_ascii=False, indent=2))
        return (await _terminal_prompt("Approve this exact tool operation? [y/N] ")).strip().lower() == "y"
    gate = SessionToolFilter(agent.tools, browser_adapter=adapter,
        external_tool_authorize=external_authorize if sys.stdin.isatty() else None)
    try:
        # Credential resolution follows the native CLI's existing conventions.
        provider = agent.providers.get_active()
        ensure_provider_key(agent, provider)
        from firik_agent.core.events import AgentEventType
        text = ""
        async for event in agent.run_stream(args.prompt, session=session, session_tool_filter=gate):
            if event.type == AgentEventType.TEXT_DELTA:
                print(event.content or "", end="", flush=True)
            elif event.type == AgentEventType.DONE:
                text = event.content or ""
            elif event.type == AgentEventType.ERROR:
                print(event.content or "Agent failed", file=sys.stderr)
                return 1
            elif event.type == AgentEventType.TOOL_CALL_RESULT and event.tool_name == "browser_request_handoff":
                if not sys.stdin.isatty():
                    raise PermissionError("Manual handoff requires a terminal host resume signal.")
                answer = await _terminal_prompt("Complete manual browser control, then type resume: ")
                if answer.strip().lower() != "resume":
                    raise PermissionError("Manual browser session was not resumed by the user.")
                await adapter.host_resume()
        print()
        return 0
    finally:
        await manager.close_all()
        await agent.close()


async def _session(args) -> int:
    """Host operator shell, with no independent model/reasoning loop."""
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.manager import BrowserManager
    from firik_agent.browser.models import BrowserGrant, BrowserIdentity
    manager = BrowserManager(BrowserConfig(enabled=True, headless=not args.headful))
    identity = BrowserIdentity("browser-cli")
    grant = BrowserGrant(enabled=True, origins=tuple(args.origin),
                         allow_private_origins=tuple(args.allow_private_origin), allow_screenshots=args.screenshots)
    try:
        print(json.dumps(await manager.dispatch(identity, grant, "browser_start", {})))
        if args.url:
            print(json.dumps(await manager.dispatch(identity, grant, "browser_navigate", {"url": args.url})))
        if not sys.stdin.isatty():
            print(json.dumps(await manager.dispatch(identity, grant, "browser_snapshot", {})))
            return 0
        print("Commands: status, sessions, snapshot, navigate URL, handoff, resume, close")
        while True:
            command = (await _terminal_prompt("browser> ")).strip()
            if command in {"close", "quit", "exit"}:
                return 0
            if command == "resume":
                result = await manager.resume(identity, grant)
            elif command == "sessions":
                result = manager.sessions(identity)
            elif command.startswith("navigate "):
                result = await manager.dispatch(identity, grant, "browser_navigate", {"url": command[9:].strip()})
            elif command in {"status", "snapshot", "handoff"}:
                operation = "request_handoff" if command == "handoff" else command
                result = await manager.dispatch(identity, grant, "browser_" + operation, {})
            else:
                print("Unknown command")
                continue
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await manager.close_all()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="firik-agent browser")
    commands = parser.add_subparsers(dest="command", required=True)
    health = commands.add_parser("doctor", help="Read installation state; never installs software")
    health.add_argument("--launch", action="store_true", help="Explicitly probe a real Chromium launch")
    health.add_argument("--headful", action="store_true")
    commands.add_parser("install", help="Explicitly install the matching Playwright Chromium binary")
    for name in ("start", "run"):
        child = commands.add_parser(name, help="Start an owned browser session" if name == "start" else "Run a native-agent browser task")
        child.add_argument("--origin", action="append", default=[], help="Exact allowed origin including port")
        child.add_argument("--allow-private-origin", action="append", default=[], help="Explicit private origin, also required in --origin")
        child.add_argument("--headful", action="store_true")
        child.add_argument("--screenshots", action="store_true")
        if name == "run":
            child.add_argument("--prompt", required=True)
            child.add_argument("--model")
            child.add_argument("--provider")
            child.add_argument("--config")
        else:
            child.add_argument("--url")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
            if args.launch:
                result["launch"] = asyncio.run(_launch_probe(args.headful))
                result["launch_verified"] = not result["launch"].get("is_error", False)
                result["headful_support" if args.headful else "headless_support"] = "launch verified"
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["playwright_installed"] and result["chromium_available"] else 1
        if args.command == "install":
            if getattr(sys, "frozen", False):
                print("Use a Python source install with the browser extra to install Chromium; the frozen app cannot execute python -m playwright.", file=sys.stderr)
                return 2
            return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])
        return asyncio.run(_task(args) if args.command == "run" else _session(args))
    except (KeyboardInterrupt, EOFError):
        return 130
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
