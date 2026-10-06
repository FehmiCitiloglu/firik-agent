from __future__ import annotations
import argparse
import asyncio
import json
import logging
from pathlib import Path
import signal
import sys
import shlex
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from firik_agent.core.agent import Agent
from firik_agent.credentials import ensure_api_key
from firik_agent.activity import ActivityLog, ActivityView
from firik_agent.paste import (
    create_paste_badge,
    delete_badge_on_backspace,
    delete_badge_on_delete,
    expand_pastes,
    PasteBadgeProcessor,
)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.application import in_terminal
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.mouse_events import MouseEventType
    HAVE_PROMPT_TOOLKIT = True
except ImportError:
    HAVE_PROMPT_TOOLKIT = False

console = Console()

COMMANDS_CATALOG: List[Dict[str, str]] = [
    {
        "command": "/shell",
        "category": "Terminal",
        "syntax": "/shell <command> (or !<command>)",
        "description": "Run a local shell command directly, e.g. !ls -la; retain output in chat context",
    },
    {
        "command": "/help",
        "category": "Reference",
        "syntax": "/help",
        "description": "Show categorized guide with practical examples",
    },
    {
        "command": "/commands",
        "category": "Reference",
        "syntax": "/commands [category]",
        "description": "List all slash commands and usage",
    },
    {
        "command": "/list",
        "category": "Reference",
        "syntax": "/list [category]",
        "description": "Alias for /commands (list all commands)",
    },
    {
        "command": "/status",
        "category": "Diagnostics",
        "syntax": "/status",
        "description": "Agent diagnostics (provider, model, tools, skills, turns)",
    },
    {
        "command": "/provider",
        "category": "Providers",
        "syntax": "/provider [name]",
        "description": "View active provider or switch provider (firik, llmtr, openai)",
    },
    {
        "command": "/model",
        "category": "Models",
        "syntax": "/model [id]",
        "description": "List known models or switch active model",
    },
    {
        "command": "/mcp list",
        "category": "MCP",
        "syntax": "/mcp list",
        "description": "List connected MCP servers and exposed tools",
    },
    {
        "command": "/mcp add",
        "category": "MCP",
        "syntax": "/mcp add <name> <cmd> [args...]",
        "description": "Connect & persist a new stdio/HTTP MCP server to config",
    },
    {
        "command": "/mcp remove",
        "category": "MCP",
        "syntax": "/mcp remove <name> (or /mcp rm <name>)",
        "description": "Disconnect & remove an MCP server from configuration",
    },
    {
        "command": "/skill list",
        "category": "Skills",
        "syntax": "/skill list",
        "description": "List available skills and active status",
    },
    {
        "command": "/skill on",
        "category": "Skills",
        "syntax": "/skill on <name>",
        "description": "Activate skill (e.g. computer-use, code_assistant)",
    },
    {
        "command": "/skill off",
        "category": "Skills",
        "syntax": "/skill off <name>",
        "description": "Deactivate skill prompt augmentation",
    },
    {
        "command": "/skill info",
        "category": "Skills",
        "syntax": "/skill info <name>",
        "description": "Inspect skill instructions and tags",
    },
    {
        "command": "/skill add",
        "category": "Skills",
        "syntax": "/skill add",
        "description": "Interactive wizard to create & persist a new skill",
    },
    {
        "command": "/plugin list",
        "category": "Plugins",
        "syntax": "/plugin list",
        "description": "List loaded plugins, versions, and injected tools",
    },
    {
        "command": "/plugin add",
        "category": "Plugins",
        "syntax": "/plugin add <file.py>",
        "description": "Load Python plugin dynamically from file",
    },
    {
        "command": "/tools",
        "category": "Tools",
        "syntax": "/tools",
        "description": "Inspect all registered tools, parameters & sources",
    },
    {
        "command": "/details",
        "category": "Session",
        "syntax": "/details",
        "description": "Inspect folded tool calls, full results and thinking (Ctrl+O)",
    },
    {
        "command": "/terms",
        "category": "FIRIK API",
        "syntax": "/terms [status|text|accept <ver>]",
        "description": "Check status, read text, or accept terms",
    },
    {
        "command": "/api",
        "category": "FIRIK API",
        "syntax": "/api <command> [args...]",
        "description": "Direct FIRIK API CLI (models, quota, ocr, chat...)",
    },
    {
        "command": "/history",
        "category": "Session",
        "syntax": "/history",
        "description": "View conversation message turns and context stats",
    },
    {
        "command": "/export",
        "category": "Session",
        "syntax": "/export [file.md]",
        "description": "Export transcript with reasoning to Markdown",
    },
    {
        "command": "/clear",
        "category": "Session",
        "syntax": "/clear",
        "description": "Clear conversation history & reset memory",
    },
    {
        "command": "/exit",
        "category": "Session",
        "syntax": "/exit (or /quit)",
        "description": "Disconnect MCP servers and quit session",
    },
    {
        "command": "/computer-use",
        "category": "Diagnostics",
        "syntax": "/computer-use [doctor|enable|stop|reset] (or /cu)",
        "description": "Cross-platform Computer Use diagnostics, doctor, and emergency stop control",
    },
]



def print_commands_table(category_filter: Optional[str] = None) -> None:
    """Print structured list of all slash commands."""
    title = "📋 Available Slash Commands"
    if category_filter:
        title += f" (Filter: {category_filter})"

    table = Table(title=title, border_style="cyan", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Category", style="bold magenta")
    table.add_column("Syntax", style="yellow")
    table.add_column("Description")

    items = COMMANDS_CATALOG
    if category_filter:
        f = category_filter.lower().strip()
        items = [i for i in items if f in i["category"].lower() or f in i["command"].lower()]

    for item in items:
        table.add_row(item["command"], item["category"], item["syntax"], item["description"])

    console.print(table)
    console.print("[dim]Tip: Press [bold]Tab[/bold] or type [bold]/[/bold] for interactive autocompletion.[/dim]\n")


class AgentCompleter(Completer if HAVE_PROMPT_TOOLKIT else object):  # type: ignore
    """Context-aware autocompleter for FirikAgent REPL."""

    def __init__(self, agent: Agent):
        self.agent = agent

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        is_tab = complete_event and complete_event.completion_requested

        # If empty line and user pressed Tab: show root commands
        if not text and is_tab:
            seen_roots = set()
            for item in COMMANDS_CATALOG:
                r = item["command"].split()[0]
                if r not in seen_roots:
                    seen_roots.add(r)
                    yield Completion(r, start_position=0, display=r, display_meta=item["description"])
            return

        # Do not autocomplete regular prompt text unless it begins with /
        if not text.startswith("/"):
            return

        parts = text.split(" ")

        # 1. Root command completion
        if len(parts) == 1:
            prefix = parts[0].lower()
            seen_roots = set()
            for item in COMMANDS_CATALOG:
                r = item["command"].split()[0]
                if r.lower().startswith(prefix) and r not in seen_roots:
                    seen_roots.add(r)
                    yield Completion(
                        r,
                        start_position=-len(prefix),
                        display=r,
                        display_meta=item["description"],
                    )
            return

        # 2. Subcommands and argument completion
        root = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if root in ("/commands", "/list"):
            if len(parts) == 2:
                prefix = parts[1].lower()
                categories = ["reference", "diagnostics", "providers", "models", "mcp", "skills", "plugins", "tools", "firik api", "session"]
                for cat in categories:
                    if cat.startswith(prefix):
                        yield Completion(cat, start_position=-len(parts[1]), display_meta="Filter category")

        elif root == "/provider":
            if len(parts) == 2:
                prefix = parts[1].lower()
                try:
                    for p in self.agent.providers.list_providers():
                        if p.lower().startswith(prefix):
                            meta = "Active Provider" if p == self.agent.providers.active_name else "Provider"
                            yield Completion(p, start_position=-len(parts[1]), display_meta=meta)
                except Exception:
                    pass

        elif root == "/model":
            if len(parts) == 2:
                prefix = parts[1].lower()
                try:
                    prov = self.agent.providers.get_active()
                    known = getattr(prov, "known_models", [])
                    for m in known:
                        m_id = m.get("id") if isinstance(m, dict) else getattr(m, "id", str(m))
                        m_name = m.get("name") if isinstance(m, dict) else getattr(m, "name", "")
                        if m_id and m_id.lower().startswith(prefix):
                            yield Completion(m_id, start_position=-len(parts[1]), display_meta=m_name or "Model")
                except Exception:
                    pass

        elif root == "/mcp":
            if len(parts) == 2:
                prefix = parts[1].lower()
                for sc, desc in [
                    ("list", "List connected & configured servers"),
                    ("add", "Connect and save server"),
                    ("remove", "Disconnect & remove server"),
                    ("rm", "Alias for remove"),
                ]:
                    if sc.startswith(prefix):
                        yield Completion(sc, start_position=-len(parts[1]), display_meta=desc)
            elif len(parts) == 3 and sub in ("remove", "rm"):
                prefix = parts[2].lower()
                try:
                    for s in self.agent.mcp.list_servers():
                        s_name = s.get("name", "")
                        if s_name.lower().startswith(prefix):
                            yield Completion(s_name, start_position=-len(parts[2]), display_meta="Connected MCP Server")
                except Exception:
                    pass

        elif root == "/skill":
            if len(parts) == 2:
                prefix = parts[1].lower()
                for sc, desc in [
                    ("list", "List all skills"),
                    ("on", "Activate skill"),
                    ("off", "Deactivate skill"),
                    ("info", "Inspect skill instructions"),
                    ("add", "Add new skill"),
                ]:
                    if sc.startswith(prefix):
                        yield Completion(sc, start_position=-len(parts[1]), display_meta=desc)
            elif len(parts) == 3:
                prefix = parts[2].lower()
                try:
                    for s in self.agent.skills.skills.values():
                        name = s.name
                        if sub == "on" and s.is_active:
                            continue
                        if sub == "off" and not s.is_active:
                            continue
                        if name.lower().startswith(prefix) or name.replace("_", "-").startswith(prefix):
                            meta = "Active" if s.is_active else "Inactive"
                            desc = f"{meta}: {s.description[:30]}..." if s.description else meta
                            yield Completion(name, start_position=-len(parts[2]), display_meta=desc)
                except Exception:
                    pass

        elif root == "/plugin":
            if len(parts) == 2:
                prefix = parts[1].lower()
                for sc, desc in [("list", "List plugins"), ("add", "Load plugin file")]:
                    if sc.startswith(prefix):
                        yield Completion(sc, start_position=-len(parts[1]), display_meta=desc)

        elif root == "/terms":
            if len(parts) == 2:
                prefix = parts[1].lower()
                for sc, desc in [("status", "Check status"), ("text", "View terms text"), ("accept", "Accept version")]:
                    if sc.startswith(prefix):
                        yield Completion(sc, start_position=-len(parts[1]), display_meta=desc)

        elif root == "/api":
            if len(parts) == 2:
                prefix = parts[1].lower()
                api_subcommands = [
                    ("models", "List models & prices"),
                    ("quota", "Check quota & credits"),
                    ("chat", "Chat completion"),
                    ("completions", "Text completion"),
                    ("responses", "Responses endpoint"),
                    ("embeddings", "Text embeddings"),
                    ("rerank", "Rerank documents"),
                    ("ocr", "OCR image recognition"),
                    ("transcribe", "Audio transcription"),
                    ("media", "Media upload/status"),
                    ("health", "Service health check"),
                    ("terms", "Terms of service status"),
                    ("api-docs", "API contract documentation"),
                ]
                for sc, desc in api_subcommands:
                    if sc.startswith(prefix):
                        yield Completion(sc, start_position=-len(parts[1]), display_meta=desc)


def setup_readline_completer(agent: Agent):
    """Fallback readline completion for platforms or setups without prompt_toolkit."""
    try:
        import readline

        def rl_completer(text: str, state: int):
            line = readline.get_line_buffer()
            if not line.startswith("/"):
                return None
            parts = line.split(" ")
            candidates = []
            if len(parts) <= 1:
                prefix = line.lower()
                candidates = [i["command"].split()[0] for i in COMMANDS_CATALOG if i["command"].lower().startswith(prefix)]
            elif len(parts) == 2:
                root = parts[0].lower()
                prefix = parts[1].lower()
                if root == "/provider":
                    candidates = [p for p in agent.providers.list_providers() if p.lower().startswith(prefix)]
                elif root == "/skill":
                    candidates = [sc for sc in ["list", "on", "off", "info", "add"] if sc.startswith(prefix)]
                elif root == "/mcp":
                    candidates = [sc for sc in ["list", "add", "remove", "rm"] if sc.startswith(prefix)]
                elif root == "/plugin":
                    candidates = [sc for sc in ["list", "add"] if sc.startswith(prefix)]
                elif root == "/terms":
                    candidates = [sc for sc in ["status", "text", "accept"] if sc.startswith(prefix)]
            elif len(parts) == 3 and parts[0].lower() == "/skill":
                prefix = parts[2].lower()
                candidates = [s.name for s in agent.skills.skills.values() if s.name.lower().startswith(prefix)]
            return candidates[state] if state < len(candidates) else None

        readline.set_completer(rl_completer)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass


def print_banner(agent: Agent) -> None:
    prov = agent.providers.get_active()
    active_skills = [s.name for s in agent.skills.skills.values() if s.is_active]
    mcp_count = len(agent.mcp.clients)
    plugins_count = len(agent.plugins.plugins)

    content = f"""[bold cyan]Firik Agent[/bold cyan] — Extensible AI with MCP, Skills & Plugins
[green]Provider:[/green] [bold]{prov.name}[/bold] ([dim]{prov.base_url}[/dim])
[green]Model:[/green]    [bold yellow]{prov.current_model}[/bold yellow]
[green]Skills:[/green]   {', '.join(active_skills) or '[dim]none[/dim]'}
[green]MCPs:[/green]     {mcp_count} servers connected
[green]Plugins:[/green]  {plugins_count} active
[dim]Type [bold]/commands[/bold] or [bold]/help[/bold] for commands (Tab for autocomplete), or start chatting below.
Run local terminal commands with [bold]!ls -la[/bold] or [bold]/shell <command>[/bold].[/dim]"""

    console.print(Panel(content, title="🚀 FirikAgent", border_style="cyan"))


def print_help() -> None:
    """Displays categorized beneficial commands with practical examples."""
    console.print(
        Panel(
            "[bold cyan]Firik Agent — Command Center[/bold cyan]\n"
            "[dim]Manage providers, models, MCP servers, skills, plugins, and session diagnostics.[/dim]",
            border_style="cyan",
        )
    )

    # 1. Providers & Models
    p_table = Table(title="🌐 Providers & Models", border_style="blue", show_header=True, header_style="bold blue")
    p_table.add_column("Command", style="bold cyan", no_wrap=True)
    p_table.add_column("Description & Beneficial Usage Example")
    p_table.add_row("/provider", "Show current active provider and list of available providers")
    p_table.add_row("/provider firik", "Switch to direct FIRIK endpoint [dim](https://evren-llmapi.ssyz.org.tr/v1)[/dim]")
    p_table.add_row("/provider llmtr", "Switch to LLMTR Gateway [dim](https://llmtr.com/v1 - zero-margin FIRIK routing)[/dim]")
    p_table.add_row("/provider openai", "Switch to OpenAI or OpenAI-compatible endpoint")
    p_table.add_row("/model", "Query and list available models for the current provider")
    p_table.add_row("/model <id>", "Switch active model (e.g. [bold yellow]/model glm-5.3[/bold yellow], [bold yellow]/model deepseek-v4-flash[/bold yellow])")
    console.print(p_table)

    # 2. Model Context Protocol (MCP)
    m_table = Table(title="🔌 Model Context Protocol (MCP)", border_style="green", show_header=True, header_style="bold green")
    m_table.add_column("Command", style="bold cyan", no_wrap=True)
    m_table.add_column("Description & Beneficial Usage Example")
    m_table.add_row("/mcp list", "List all connected MCP servers and their discovered tools")
    m_table.add_row(
        "/mcp add <name> <cmd> [args]",
        "Connect a new stdio MCP server on the fly.\n"
        "[dim]Example:[/dim] [italic]/mcp add telemetry python3 examples/mock_mcp_server.py[/italic]\n"
        "[dim]Example:[/dim] [italic]/mcp add git npx -y @modelcontextprotocol/server-git[/italic]",
    )
    m_table.add_row("/mcp remove <name>", "Disconnect an MCP server and unmount its tools cleanly")
    console.print(m_table)

    # 3. Skills (Instruction Augmentation)
    s_table = Table(title="🧠 Skills & Specialized Knowledge", border_style="magenta", show_header=True, header_style="bold magenta")
    s_table.add_column("Command", style="bold cyan", no_wrap=True)
    s_table.add_column("Description & Beneficial Usage Example")
    s_table.add_row("/skill list", "List all available skills and their active/inactive status")
    s_table.add_row("/skill on <name>", "Activate a skill (e.g. [bold yellow]/skill on code_assistant[/bold yellow], [bold yellow]/skill on computer-use[/bold yellow], or [bold yellow]/skill on research_analyst[/bold yellow])")
    s_table.add_row("/skill off <name>", "Deactivate a skill to remove its prompt augmentation")
    s_table.add_row("/skill info <name>", "Inspect full prompt instructions and tags of a skill")
    s_table.add_row("/skill add", "Interactive wizard to create and persist a new skill with [italic]SKILL.md[/italic]")
    console.print(s_table)

    # 4. Plugins & Tools
    t_table = Table(title="🧩 Plugins & Tool Management", border_style="yellow", show_header=True, header_style="bold yellow")
    t_table.add_column("Command", style="bold cyan", no_wrap=True)
    t_table.add_column("Description & Beneficial Usage Example")
    t_table.add_row("/plugin list", "List active plugins, versions, and injected tools")
    t_table.add_row("/plugin add <file.py>", "Dynamically load a python plugin (e.g. [italic]/plugin add plugins/weather_plugin.py[/italic])")
    t_table.add_row("/tools", "Inspect all registered tools, their sources ([italic]builtin, mcp, skill, plugin[/italic]), and schemas")
    console.print(t_table)

    # 5. Diagnostics & Session Controls
    d_table = Table(title="⚙️ Session, Diagnostics & Utilities", border_style="red", show_header=True, header_style="bold red")
    d_table.add_column("Command", style="bold cyan", no_wrap=True)
    d_table.add_column("Description & Beneficial Usage Example")
    d_table.add_row("/status", "Display complete agent diagnostics (provider, model, tools, skills, memory)")
    d_table.add_row("/terms", "Check FIRIK terms of service status [dim](GET /v1/terms/status)[/dim]")
    d_table.add_row("/api <command>", "All direct API commands: /api --help, /api quota, /api ocr --file scan.png")
    d_table.add_row("/terms text", "Read the current terms before accepting")
    d_table.add_row("/terms accept <version>", "Explicitly accept the terms version you have read")
    d_table.add_row("/history", "View message history turns and context stats")
    d_table.add_row("/details", "Open activity details; Ctrl+O or click opens/closes, Enter folds a row")
    d_table.add_row("/export [file.md]", "Export conversation transcript to a Markdown file [dim](default: session_export.md)[/dim]")
    d_table.add_row("/clear", "Clear conversation history and reset context window")
    d_table.add_row("/exit, /quit", "Safely disconnect all MCP servers and exit the session")
    console.print(d_table)

    console.print(Panel(
        "!ls -la  ·  !git status  ·  /shell pwd\n"
        "Runs locally without a model request; output is added to conversation context.\n"
        "Ctrl+C stops the command. Default timeout: 60 seconds.\n"
        "Each command starts a new shell in the launch directory; use cd path && command.\n"
        "Commands run with your local user permissions.",
        title="Terminal", border_style="yellow",
    ))


async def handle_shell_command(agent: Agent, command: str) -> None:
    """Run explicit shell input and let Ctrl+C return to the REPL."""
    task = asyncio.create_task(agent.run_local_command(command))
    previous_handler = signal.getsignal(signal.SIGINT)
    interrupted = False
    loop = asyncio.get_running_loop()

    def interrupt(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            loop.call_soon_threadsafe(task.cancel)

    signal.signal(signal.SIGINT, interrupt)
    try:
        with console.status("[bold yellow]Running command... (Ctrl+C to stop)[/bold yellow]"):
            result = await task
        console.print(Panel(Text(result), title="Terminal", border_style="yellow"))
    except asyncio.CancelledError:
        if not interrupted:
            raise
        console.print("[yellow]Command interrupted.[/yellow]")
    finally:
        signal.signal(signal.SIGINT, previous_handler)


async def handle_slash_command(agent: Agent, cmd: str) -> bool:
    """Returns True if command was handled, False if user wants to exit."""
    parts = cmd.strip().split()
    root = parts[0].lower()

    if root in ("/exit", "/quit"):
        return False

    if root == "/help":
        print_help()

    elif root == "/shell":
        command_parts = cmd.strip().split(maxsplit=1)
        await handle_shell_command(agent, command_parts[1] if len(command_parts) > 1 else "")

    elif root in ("/commands", "/list"):
        cat_filter = parts[1] if len(parts) > 1 else None
        print_commands_table(cat_filter)

    elif root == "/api":
        from firik_agent.api.cli import run as run_api
        from firik_agent.providers.firik import FirikProvider
        prov = agent.providers.get_active()
        if not isinstance(prov, FirikProvider) or prov.is_gateway:
            console.print("[yellow]Direct API commands require /provider firik.[/yellow]")
        else:
            try:
                await run_api(shlex.split(cmd)[1:], api_key=prov.api_key or "", base_url=prov.base_url,
                              default_model=prov.current_model)
            except SystemExit:
                pass  # argparse help/errors must not terminate the REPL.

    elif root == "/status":
        prov = agent.providers.get_active()
        active_skills = [s.name for s in agent.skills.skills.values() if s.is_active]
        mcp_servers = agent.mcp.list_servers()
        plugins = agent.plugins.list_plugins()
        all_tools = agent.tools.list_tools()

        info = f"""[bold cyan]Agent Status & Diagnostics[/bold cyan]
• [bold]Name:[/bold] {agent.name}
• [bold]Active Provider:[/bold] [green]{prov.name}[/green] ([dim]{prov.base_url}[/dim])
• [bold]Active Model:[/bold] [bold yellow]{prov.current_model}[/bold yellow]
• [bold]Conversation Turns:[/bold] {len(agent.messages)} messages in context
• [bold]Active Skills ({len(active_skills)}):[/bold] {', '.join(active_skills) or '[dim]none[/dim]'}
• [bold]Connected MCP Servers ({len(mcp_servers)}):[/bold] {', '.join([s['name'] for s in mcp_servers]) or '[dim]none[/dim]'}
• [bold]Loaded Plugins ({len(plugins)}):[/bold] {', '.join([p['name'] for p in plugins]) or '[dim]none[/dim]'}
• [bold]Total Registered Tools:[/bold] {len(all_tools)} tools ready
"""
        console.print(Panel(info, title="🔍 System Diagnostics", border_style="cyan"))

    elif root == "/history":
        if not agent.messages:
            console.print("[dim]No messages in current conversation.[/dim]")
        else:
            table = Table(title="Conversation History", border_style="dim")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Role", style="bold")
            table.add_column("Content Preview / Tool Calls")

            for idx, msg in enumerate(agent.messages, 1):
                role_style = "cyan" if msg.role == "user" else "green" if msg.role == "assistant" else "yellow"
                desc = msg.content[:80] + ("..." if msg.content and len(msg.content) > 80 else "")
                if msg.tool_calls:
                    tc_names = [tc.function.name for tc in msg.tool_calls]
                    desc = f"[magenta]Tool Calls: {', '.join(tc_names)}[/magenta]"
                elif msg.role == "tool":
                    desc = f"[dim][{msg.name}][/dim] {desc}"
                table.add_row(str(idx), f"[{role_style}]{msg.role}[/{role_style}]", desc or "[dim]empty[/dim]")
            console.print(table)

    elif root == "/export":
        filename = parts[1] if len(parts) > 1 else "session_export.md"
        try:
            lines = [
                f"# Firik Agent Session Export",
                f"- Provider: {agent.providers.active_name}",
                f"- Model: {agent.providers.get_active().current_model}",
                f"- Total Messages: {len(agent.messages)}\n",
                "---\n",
            ]
            for m in agent.messages:
                lines.append(f"### {m.role.upper()}\n")
                if m.reasoning:
                    lines.append(f"> **Reasoning / Thought:**\n> {m.reasoning}\n")
                if m.content:
                    lines.append(f"{m.content}\n")
                if m.tool_calls:
                    lines.append("**Tool Calls:**")
                    for tc in m.tool_calls:
                        lines.append(f"- `{tc.function.name}({tc.function.arguments})`")
                    lines.append("")
                lines.append("---\n")

            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            console.print(f"[green]Successfully exported session to [bold]{filename}[/bold][/green]")
        except Exception as e:
            console.print(f"[red]Error exporting session: {e}[/red]")

    elif root == "/provider":
        if len(parts) == 1:
            curr = agent.providers.active_name
            all_p = agent.providers.list_providers()
            console.print(f"[cyan]Active provider:[/cyan] [bold]{curr}[/bold]")
            console.print(f"[dim]Available providers: {', '.join(all_p)}[/dim]")
        else:
            target = parts[1]
            try:
                candidate = agent.providers.get(target)
                if candidate is not None:
                    ensure_provider_key(agent, candidate)
                prov = agent.providers.set_active(target)
                console.print(f"[green]Switched provider to [bold]{prov.name}[/bold] (model: {prov.current_model})[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

    elif root == "/model":
        prov = agent.providers.get_active()
        if len(parts) == 1:
            console.print(f"[cyan]Current model for {prov.name}:[/cyan] [bold yellow]{prov.current_model}[/bold yellow]")
            try:
                models = await prov.list_models()
                console.print("[dim]Known models:[/dim]")
                for m in models:
                    console.print(f"  • [bold]{m.id}[/bold] - {m.name}")
            except Exception as e:
                console.print(f"[dim]Could not query remote models: {e}[/dim]")
        else:
            new_model = parts[1]
            prov.set_model(new_model)
            console.print(f"[green]Model set to [bold yellow]{new_model}[/bold yellow] for provider {prov.name}[/green]")

    elif root == "/mcp":
        sub = parts[1] if len(parts) > 1 else "list"
        if sub in ("list", "ls"):
            servers = agent.mcp.list_servers()
            if not servers:
                from firik_agent.config import get_mcp_servers_from_config
                cfg_servers = get_mcp_servers_from_config(getattr(agent, "config_path", None))
                if not cfg_servers:
                    console.print("[yellow]No MCP servers connected or configured. Connect with: /mcp add <name> <cmd> [args...][/yellow]")
                else:
                    table = Table(title="Configured MCP Servers (Not Active in Session)")
                    table.add_column("Name", style="bold cyan")
                    table.add_column("Command / URL")
                    for name, sc in cfg_servers.items():
                        target = sc.get("command") or sc.get("url") or "-"
                        table.add_row(name, str(target))
                    console.print(table)
            else:
                table = Table(title="Connected MCP Servers")
                table.add_column("Name", style="bold cyan")
                table.add_column("Type")
                table.add_column("Command / URL")
                table.add_column("Exposed Tools", style="green")
                for s in servers:
                    table.add_row(
                        s["name"],
                        s["type"],
                        str(s.get("command") or s.get("url")),
                        ", ".join(s.get("tools", [])) or "[dim]none[/dim]",
                    )
                console.print(table)

        elif sub == "add":
            # Keep Windows path separators, while retaining quoted paths with spaces.
            lexer = shlex.shlex(cmd, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            if sys.platform == "win32":
                lexer.escape = ""
            raw_tokens = list(lexer)[2:]
            p = argparse.ArgumentParser(prog="/mcp add", add_help=False)
            p.add_argument("--url", type=str, default=None)
            p.add_argument("-e", "--env", action="append", default=[])
            parsed, extra = p.parse_known_args(raw_tokens)
            extra = [t for t in extra if t != "--"]

            if not extra:
                console.print("[yellow]Usage: /mcp add <name> <command> [args...] or /mcp add <name> --url <url>[/yellow]")
            else:
                s_name = extra[0]
                s_cmd = extra[1] if len(extra) > 1 else None
                s_args = extra[2:] if len(extra) > 2 else []

                if not s_cmd and not parsed.url:
                    console.print("[yellow]Error: Must specify command or --url. Usage: /mcp add <name> <command> [args...][/yellow]")
                else:
                    env_dict: Dict[str, str] = {}
                    for item in parsed.env:
                        if "=" in item:
                            k, v = item.split("=", 1)
                            env_dict[k.strip()] = v.strip()
                        else:
                            k = item.strip()
                            env_dict[k] = os.environ.get(k, "")

                    with console.status(f"[cyan]Connecting MCP server '{s_name}'...[/cyan]"):
                        try:
                            tools = await agent.mcp.add_server(
                                name=s_name,
                                command=s_cmd,
                                args=s_args,
                                env=env_dict,
                                url=parsed.url,
                            )
                            from firik_agent.config import add_mcp_server_to_config
                            server_conf: Dict[str, Any] = {}
                            if s_cmd:
                                server_conf["command"] = s_cmd
                            if s_args:
                                server_conf["args"] = s_args
                            if env_dict:
                                server_conf["env"] = env_dict
                            if parsed.url:
                                server_conf["url"] = parsed.url

                            cfg_p = add_mcp_server_to_config(s_name, server_conf, getattr(agent, "config_path", None))
                            tools_preview = f"Registered {len(tools)} tools: {', '.join(tools)}" if tools else "Connected (no tools)"
                            console.print(f"[bold green]✓ Connected MCP server '[cyan]{s_name}[/cyan]' and saved to {cfg_p}.[/bold green]")
                            console.print(f"[dim]{tools_preview}[/dim]")
                        except Exception as e:
                            console.print(f"[bold red]Failed connecting to MCP server:[/bold red] {e}")

        elif sub in ("remove", "rm", "delete"):
            if len(parts) < 3:
                console.print("[yellow]Usage: /mcp remove <name> (or /mcp rm <name>)[/yellow]")
            else:
                s_name = parts[2]
                ok = await agent.mcp.remove_server(s_name)
                from firik_agent.config import remove_mcp_server_from_config
                cfg_ok = remove_mcp_server_from_config(s_name, getattr(agent, "config_path", None))
                if ok or cfg_ok:
                    console.print(f"[bold green]✓ Removed MCP server '[cyan]{s_name}[/cyan]' and updated configuration.[/bold green]")
                else:
                    console.print(f"[bold red]MCP server '{s_name}' not found.[/bold red]")

    elif root == "/skill":
        sub = parts[1] if len(parts) > 1 else "list"
        if sub == "list":
            skills = agent.skills.list_skills()
            table = Table(title="Skills")
            table.add_column("Status", justify="center")
            table.add_column("Name", style="bold cyan")
            table.add_column("Description")
            table.add_column("Tags", style="dim")

            for s in skills:
                status = "[green]ACTIVE[/green]" if s["is_active"] else "[dim]INACTIVE[/dim]"
                table.add_row(status, s["name"], s["description"], ", ".join(s["tags"]))
            console.print(table)

        elif sub == "on" and len(parts) > 2:
            s_name = parts[2]
            if agent.skills.activate_skill(s_name):
                console.print(f"[green]Activated skill '{s_name}'.[/green]")
            else:
                console.print(f"[red]Skill '{s_name}' not found.[/red]")

        elif sub == "off" and len(parts) > 2:
            s_name = parts[2]
            if agent.skills.deactivate_skill(s_name):
                console.print(f"[yellow]Deactivated skill '{s_name}'.[/yellow]")
            else:
                console.print(f"[red]Skill '{s_name}' not found.[/red]")

        elif sub == "info" and len(parts) > 2:
            s_name = parts[2]
            skill = agent.skills.get_skill(s_name)
            if skill:
                content = f"[bold cyan]Skill:[/bold cyan] {skill.name} (Active: {skill.is_active})\n"
                content += f"[bold]Description:[/bold] {skill.description}\n"
                content += f"[bold]Tags:[/bold] {', '.join(skill.tags) or 'none'}\n\n"
                content += f"[bold]Instructions:[/bold]\n{skill.instructions}"
                console.print(Panel(content, title=f"Skill: {skill.name}", border_style="magenta"))
            else:
                console.print(f"[red]Skill '{s_name}' not found.[/red]")

        elif sub == "add":
            name = Prompt.ask("[cyan]Skill name[/cyan]")
            desc = Prompt.ask("[cyan]Short description[/cyan]")
            console.print("[dim]Enter instructions (end with empty line):[/dim]")
            lines = []
            while True:
                line = input()
                if not line:
                    break
                lines.append(line)
            instructions = "\n".join(lines)
            agent.skills.add_skill(name, desc, instructions, activate=True)
            console.print(f"[green]Created and activated skill '{name}'.[/green]")

    elif root == "/plugin":
        sub = parts[1] if len(parts) > 1 else "list"
        if sub == "list":
            plugins = agent.plugins.list_plugins()
            table = Table(title="Plugins")
            table.add_column("Name", style="bold cyan")
            table.add_column("Version", style="dim")
            table.add_column("Description")
            table.add_column("Tools", style="green")
            for p in plugins:
                table.add_row(
                    p["name"],
                    p["version"],
                    p["description"],
                    ", ".join(p["tools"]) or "[dim]none[/dim]",
                )
            console.print(table)

        elif sub == "add" and len(parts) > 2:
            f_path = parts[2]
            try:
                p = agent.plugins.load_plugin_from_file(f_path)
                if p:
                    console.print(f"[green]Loaded plugin '{p.name}'.[/green]")
                else:
                    console.print(f"[red]No valid plugin class found in {f_path}[/red]")
            except Exception as e:
                console.print(f"[red]Error loading plugin: {e}[/red]")

    elif root == "/tools":
        tools = agent.tools.list_tools()
        table = Table(title="Registered Tools")
        table.add_column("Name", style="bold cyan")
        table.add_column("Source", style="dim")
        table.add_column("Description")

        for t in tools:
            table.add_row(t.name, t.source, t.description)
        console.print(table)

    elif root == "/terms":
        prov = agent.providers.get_active()
        from firik_agent.providers.firik import FirikProvider
        if isinstance(prov, FirikProvider) and not prov.is_gateway:
            sub = parts[1] if len(parts) > 1 else "status"
            if sub == "accept":
                with console.status("Accepting FIRIK terms..."):
                    try:
                        if len(parts) < 3:
                            raise ValueError("Read /terms text, then use /terms accept <version>.")
                        ver = int(parts[2])
                        res = await prov.accept_terms(ver)
                        console.print(Panel(json.dumps(res, indent=2), title=f"Accepted Terms ({ver})"))
                    except Exception as e:
                        console.print(f"[red]Error accepting terms: {e}[/red]")
            else:
                with console.status("Checking FIRIK terms status..."):
                    try:
                        res = await prov.get_terms_text() if sub == "text" else await prov.check_terms_status()
                        console.print(Panel(json.dumps(res, indent=2), title="FIRIK Terms Status"))
                    except Exception as e:
                        console.print(f"[red]Error checking terms: {e}[/red]")
        else:
            console.print("[yellow]Active provider is not an FIRIK provider.[/yellow]")

    elif root == "/clear":
        await agent.clear_history()
        console.print("[yellow]Conversation history cleared.[/yellow]")

    elif root in ("/computer-use", "/cu"):
        sub = parts[1].lower() if len(parts) > 1 else "status"
        if sub in ("doctor", "status"):
            from firik_agent.computer_use.cli import run_doctor
            run_doctor()
        elif sub == "enable":
            agent.enable_computer_use()
            console.print("[green]Computer Use tools enabled and registered.[/green]")
        elif sub == "stop":
            agent.stop_computer_use()
            console.print("[red]Emergency STOP sent to Computer Use service.[/red]")
        elif sub == "reset":
            if agent.computer_use_service:
                agent.computer_use_service.reset_stop()
                console.print("[green]Computer Use emergency stop reset.[/green]")
            else:
                console.print("[yellow]Computer Use service is not active.[/yellow]")
        else:
            console.print("[yellow]Usage: /computer-use [doctor|enable|stop|reset][/yellow]")


    else:
        console.print(f"[yellow]Unknown command: {root}. Type /help for options.[/yellow]")

    return True


def ensure_provider_key(agent: Agent, provider) -> None:
    if not provider.api_key:
        config = agent.config.get("providers", {}).get(provider.name, {})
        provider.api_key = ensure_api_key(provider.name, config, base_url=provider.base_url)


async def run_repl(agent: Agent) -> None:
    await agent.initialize()
    print_banner(agent)

    activity = ActivityLog()
    interactive_activity = HAVE_PROMPT_TOOLKIT and console.is_terminal and sys.stdin.isatty()
    paste_map: dict[str, str] = {}

    async def show_details():
        if not activity.entries:
            console.print("[dim]No activity yet.[/dim]")
        elif interactive_activity:
            await ActivityView(activity).run()
        else:
            console.print(Text(activity.plain_details()))

    session: Optional[Any] = None
    if HAVE_PROMPT_TOOLKIT:
        bindings = KeyBindings()

        async def inspect_from_prompt():
            async with in_terminal():
                await show_details()

        @bindings.add("c-o")
        def open_details(event):
            event.app.create_background_task(inspect_from_prompt())

        def click_details(event):
            if event.event_type == MouseEventType.MOUSE_UP:
                session.app.create_background_task(inspect_from_prompt())
            else:
                return NotImplemented

        @bindings.add(Keys.BracketedPaste)
        def handle_bracketed_paste(event):
            badge, is_badge = create_paste_badge(event.data, paste_map)
            if is_badge:
                paste_map[badge] = event.data.replace("\r\n", "\n").replace("\r", "\n")
                event.current_buffer.insert_text(badge)
            else:
                event.current_buffer.insert_text(badge)

        @bindings.add("backspace")
        @bindings.add("c-h")
        def handle_backspace(event):
            buf = event.current_buffer
            if buf.selection_state:
                buf.cut_selection()
            elif not delete_badge_on_backspace(buf):
                buf.delete_before_cursor(count=1)

        @bindings.add("delete")
        def handle_delete(event):
            buf = event.current_buffer
            if buf.selection_state:
                buf.cut_selection()
            elif not delete_badge_on_delete(buf):
                buf.delete(count=1)

        def activity_toolbar():
            if activity.entries:
                return [("", "▶ " + activity.summary + " · Ctrl+O / click: details", click_details)]
            return "Tool calls and thinking are folded · Ctrl+O / /details: inspect"

        history_path = Path.home() / ".firik_agent_history"
        try:
            session = PromptSession(
                completer=AgentCompleter(agent),
                history=FileHistory(str(history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                complete_while_typing=True,
                key_bindings=bindings,
                bottom_toolbar=activity_toolbar if interactive_activity else None,
                mouse_support=interactive_activity,
                input_processors=[PasteBadgeProcessor()] if HAVE_PROMPT_TOOLKIT else None,
            )
        except Exception:
            session = None

    if session is None:
        interactive_activity = False
        setup_readline_completer(agent)

    while True:
        try:
            if session is not None:
                user_input = (await session.prompt_async(HTML("<ansigreen><b>You</b></ansigreen> > "))).strip()
            else:
                user_input = Prompt.ask("\n[bold green]You[/bold green]").strip()

            if not user_input:
                continue

            user_input = expand_pastes(user_input, paste_map)

            if user_input.startswith("!"):
                await handle_shell_command(agent, user_input[1:].strip())
                continue

            if user_input.startswith("/"):
                if user_input.lower() == "/details":
                    await show_details()
                    continue
                keep_going = await handle_slash_command(agent, user_input)
                if user_input.split()[0].lower() == "/clear":
                    activity.entries.clear()
                    paste_map.clear()
                if not keep_going:
                    console.print("[cyan]Goodbye![/cyan]")
                    break
                continue

            activity.running = True

            async def run_agent():
                return await agent.run(
                    prompt=user_input,
                    on_thought=activity.thought,
                    on_tool_start=activity.tool_start,
                    on_tool_end=activity.tool_end,
                )

            try:
                if interactive_activity:
                    response = await ActivityView(activity, live=True).run(run_agent)
                else:
                    with console.status("Firik düşünüyor...") as status:
                        activity.notify = lambda: status.update(Text(activity.summary))
                        response = await run_agent()
            finally:
                activity.notify = lambda: None
                activity.finish()

            if activity.entries:
                console.print(Text(activity.summary + " · /details to inspect", style="dim"))
            console.print("\n[bold cyan]FirikAgent:[/bold cyan]")
            console.print(Markdown(response))

        except (KeyboardInterrupt, EOFError):
            console.print("\n[cyan]Session terminated. Goodbye![/cyan]")
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")

    await agent.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "api":
        from firik_agent.api.cli import main as api_main
        raise SystemExit(api_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        from firik_agent.mcp.cli import main as mcp_main
        raise SystemExit(mcp_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("computer-use", "cu"):
        from firik_agent.computer_use.cli import main as cu_main
        raise SystemExit(cu_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(
        description="Firik Agent CLI",
        epilog="MCP commands: firik-agent mcp --help | Computer Use: firik-agent computer-use doctor | Direct API: firik-agent api --help",
    )

    parser.add_argument("--prompt", "-p", type=str, help="Single-shot prompt to execute")
    parser.add_argument("--provider", type=str, help="Provider name (firik, llmtr, openai)")
    parser.add_argument("--model", "-m", type=str, help="Model ID")
    parser.add_argument("--config", "-c", type=str, help="Path to config.yaml")
    parser.add_argument("--commands", "--list-commands", action="store_true", help="List all available slash commands and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.commands:
        print_commands_table()
        return

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    agent = Agent(config_path=args.config)

    if args.provider:
        agent.providers.set_active(args.provider)
    if args.model:
        agent.providers.get_active().set_model(args.model)

    local_prompt = args.prompt is not None and args.prompt.lstrip().startswith("!")
    if not local_prompt:
        try:
            ensure_provider_key(agent, agent.providers.get_active())
        except (ValueError, RuntimeError) as exc:
            print(f"Hata: {exc}", file=sys.stderr)
            raise SystemExit(1)

    if args.prompt:
        # Single-shot execution
        async def run_single():
            try:
                if local_prompt:
                    await handle_shell_command(agent, args.prompt.lstrip()[1:].strip())
                else:
                    response = await agent.run(args.prompt)
                    print(response)
            finally:
                await agent.close()

        asyncio.run(run_single())
    else:
        # Interactive REPL
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
