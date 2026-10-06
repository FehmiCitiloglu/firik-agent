from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from rich.console import Console
from rich.table import Table

from evren_agent.config import (
    add_mcp_server_to_config,
    get_mcp_servers_from_config,
    remove_mcp_server_from_config,
)
from evren_agent.mcp.client import MCPClient

console = Console()


async def mcp_add(
    name: str,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    url: Optional[str] = None,
    test: bool = True,
    config_path: Optional[str] = None,
    con: Optional[Console] = None,
) -> int:
    """Add and configure an MCP server, testing connection and persisting to config.yaml."""
    out = con or console
    if not command and not url:
        out.print("[bold red]Error:[/bold red] You must specify an executable command or --url for the MCP server.")
        out.print("[dim]Usage: firik-agent mcp add <name> <command> [args...] or firik-agent mcp add <name> --url <url>[/dim]")
        return 1

    clean_args = list(args) if args else []
    clean_env = dict(env) if env else {}

    # Test connection if requested
    if test:
        with out.status(f"[cyan]Testing MCP connection for '{name}'...[/cyan]"):
            client = MCPClient(
                name=name,
                command=command,
                args=clean_args,
                env=clean_env,
                url=url,
            )
            success = False
            tools_found = []
            try:
                # 15s timeout for handshake
                await asyncio.wait_for(client.connect(), timeout=15.0)
                mcp_tools = await asyncio.wait_for(client.list_tools(), timeout=10.0)
                tools_found = [t.name for t in mcp_tools]
                success = True
            except Exception as e:
                out.print(f"[yellow]⚠ Warning: Could not connect to MCP server '{name}': {e}[/yellow]")
                should_save = False
                if sys.stdin.isatty():
                    from rich.prompt import Confirm
                    should_save = Confirm.ask(f"Do you still want to save '{name}' to configuration?", default=False)
                if not should_save:
                    out.print("[dim]Aborted. Use --no-test to save without testing connection.[/dim]")
                    return 1
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        if success:
            out.print(f"[green]✓ Connected to MCP server '[bold]{name}[/bold]' successfully![/green]")
            if tools_found:
                out.print(f"[dim]Discovered {len(tools_found)} tool(s): {', '.join(tools_found)}[/dim]")
            else:
                out.print("[dim]Server connected (0 tools declared).[/dim]")

    # Persist to config
    server_conf: Dict[str, Any] = {}
    if command:
        server_conf["command"] = command
    if clean_args:
        server_conf["args"] = clean_args
    if clean_env:
        server_conf["env"] = clean_env
    if url:
        server_conf["url"] = url

    try:
        saved_path = add_mcp_server_to_config(name, server_conf, config_path)
        out.print(f"[bold green]✓ Successfully configured MCP server '[cyan]{name}[/cyan]' in {saved_path}[/bold green]")
        return 0
    except Exception as e:
        out.print(f"[bold red]Failed to save MCP server to configuration:[/bold red] {e}")
        return 1


async def mcp_list(
    config_path: Optional[str] = None,
    test: bool = False,
    con: Optional[Console] = None,
) -> int:
    """List configured MCP servers from config.yaml."""
    out = con or console
    servers = get_mcp_servers_from_config(config_path)

    if not servers:
        out.print("[yellow]No MCP servers configured.[/yellow]")
        out.print("[dim]Add one with: firik-agent mcp add <name> <command> [args...][/dim]")
        out.print("[dim]Example: firik-agent mcp add git npx -y @modelcontextprotocol/server-git[/dim]")
        return 0

    table = Table(title="Configured MCP Servers", border_style="cyan", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold cyan")
    table.add_column("Type", style="dim")
    table.add_column("Command / URL")
    table.add_column("Arguments", style="dim")
    table.add_column("Environment", style="dim")

    if test:
        table.add_column("Status", justify="center")
        table.add_column("Tools", style="green")

    for name, s_conf in servers.items():
        cmd = s_conf.get("command")
        url = s_conf.get("url")
        args = s_conf.get("args") or []
        env = s_conf.get("env") or {}

        s_type = "stdio" if cmd else "http/sse"
        target = cmd if cmd else (url or "-")
        args_str = " ".join(args) if args else "[dim]-[/dim]"
        env_str = ", ".join(f"{k}={v}" for k, v in env.items()) if env else "[dim]-[/dim]"

        if test:
            # Test connection
            status_str = "[red]OFFLINE[/red]"
            tools_str = "[dim]-[/dim]"
            client = MCPClient(
                name=name,
                command=cmd,
                args=args,
                env=env,
                url=url,
            )
            try:
                await asyncio.wait_for(client.connect(), timeout=10.0)
                tools = await asyncio.wait_for(client.list_tools(), timeout=5.0)
                status_str = "[green]ONLINE[/green]"
                tools_str = f"{len(tools)} tools"
            except Exception:
                status_str = "[red]ERROR[/red]"
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            table.add_row(name, s_type, str(target), args_str, env_str, status_str, tools_str)
        else:
            table.add_row(name, s_type, str(target), args_str, env_str)

    out.print(table)
    return 0


async def mcp_remove(
    name: str,
    config_path: Optional[str] = None,
    con: Optional[Console] = None,
) -> int:
    """Remove an MCP server from config.yaml."""
    out = con or console
    try:
        ok = remove_mcp_server_from_config(name, config_path)
        if ok:
            out.print(f"[bold green]✓ Removed MCP server '[cyan]{name}[/cyan]' from configuration.[/bold green]")
            return 0
        else:
            out.print(f"[bold red]Error:[/bold red] MCP server '[cyan]{name}[/cyan]' not found in configuration.")
            return 1
    except Exception as e:
        out.print(f"[bold red]Failed removing MCP server:[/bold red] {e}")
        return 1


def print_mcp_help(con: Optional[Console] = None) -> None:
    out = con or console
    help_text = """[bold cyan]firik-agent mcp[/bold cyan] — Model Context Protocol (MCP) server manager

[bold]Commands:[/bold]
  [bold cyan]add[/bold cyan] <name> <command> [args...]    Add and configure an MCP server (persisted to config.yaml)
  [bold cyan]list[/bold cyan] [options]                     List all configured MCP servers (alias: [bold]ls[/bold])
  [bold cyan]remove[/bold cyan] <name>                      Remove an MCP server from configuration (alias: [bold]rm[/bold], [bold]delete[/bold])

[bold]Options for 'add':[/bold]
  [yellow]--url <url>[/yellow]                 Remote HTTP or SSE endpoint URL
  [yellow]-e, --env <KEY=VAL>[/yellow]         Set environment variable for the server (can be repeated)
  [yellow]--no-test[/yellow]                   Save configuration without testing connection
  [yellow]-c, --config <path>[/yellow]         Target config.yaml path (default: config.yaml)

[bold]Options for 'list':[/bold]
  [yellow]--test[/yellow]                      Test live connection to each server and report discovered tools
  [yellow]-c, --config <path>[/yellow]         Target config.yaml path (default: config.yaml)

[bold]Examples:[/bold]
  [dim]# Add standard stdio MCP servers (like Claude and Codex):[/dim]
  firik-agent mcp add git npx -y @modelcontextprotocol/server-git
  firik-agent mcp add fs npx -y @modelcontextprotocol/server-filesystem /Users/me/docs
  firik-agent mcp add sqlite uvx mcp-server-sqlite --db-path ./app.db

  [dim]# Add server with environment variables:[/dim]
  firik-agent mcp add github -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_123 npx -y @modelcontextprotocol/server-github

  [dim]# Add remote HTTP/SSE server:[/dim]
  firik-agent mcp add fetch --url http://localhost:8000/sse

  [dim]# List & Remove:[/dim]
  firik-agent mcp list
  firik-agent mcp remove git
"""
    out.print(help_text)


async def run_mcp(argv: Sequence[str] | None = None, con: Optional[Console] = None) -> int:
    """CLI runner for 'firik-agent mcp' and 'evren mcp'."""
    out = con or console
    args_list = list(sys.argv[1:] if argv is None else argv)

    if not args_list or args_list[0] in ("-h", "--help", "help"):
        print_mcp_help(out)
        return 0

    sub = args_list[0].lower()
    sub_args = args_list[1:]

    if sub in ("list", "ls"):
        parser = argparse.ArgumentParser(prog="firik-agent mcp list", add_help=False)
        parser.add_argument("--test", action="store_true", help="Test live connection to each server")
        parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.yaml")
        parser.add_argument("-h", "--help", action="store_true")
        parsed, _ = parser.parse_known_args(sub_args)
        if parsed.help:
            out.print("[bold cyan]Usage:[/bold cyan] firik-agent mcp list [--test] [-c config.yaml]")
            return 0
        return await mcp_list(config_path=parsed.config, test=parsed.test, con=out)

    elif sub in ("remove", "rm", "delete"):
        parser = argparse.ArgumentParser(prog="firik-agent mcp remove", add_help=False)
        parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.yaml")
        parser.add_argument("-h", "--help", action="store_true")
        parsed, extra = parser.parse_known_args(sub_args)
        if parsed.help or not extra:
            out.print("[bold cyan]Usage:[/bold cyan] firik-agent mcp remove <name> [-c config.yaml]")
            return 0 if parsed.help else 1
        return await mcp_remove(name=extra[0], config_path=parsed.config, con=out)

    elif sub == "add":
        parser = argparse.ArgumentParser(prog="firik-agent mcp add", add_help=False)
        parser.add_argument("--url", type=str, default=None, help="Remote HTTP/SSE URL")
        parser.add_argument("-e", "--env", action="append", default=[], help="Environment variables in KEY=VALUE format")
        parser.add_argument("--no-test", action="store_true", help="Skip connection test before saving")
        parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.yaml")
        parser.add_argument("-h", "--help", action="store_true")
        parsed, extra = parser.parse_known_args(sub_args)

        if parsed.help:
            print_mcp_help(out)
            return 0

        # Remove lone '--' separator if present
        extra = [t for t in extra if t != "--"]

        if not extra:
            out.print("[bold red]Error:[/bold red] Missing server name.")
            out.print("[dim]Usage: firik-agent mcp add <name> <command> [args...][/dim]")
            return 1

        name = extra[0]
        cmd: Optional[str] = None
        cmd_args: List[str] = []

        if parsed.url:
            if len(extra) > 1:
                cmd = extra[1]
                cmd_args = extra[2:]
        else:
            if len(extra) < 2:
                out.print("[bold red]Error:[/bold red] Missing command for stdio server.")
                out.print("[dim]Usage: firik-agent mcp add <name> <command> [args...] or specify --url <url>[/dim]")
                return 1
            cmd = extra[1]
            cmd_args = extra[2:]

        # Parse env
        env_dict: Dict[str, str] = {}
        for item in parsed.env:
            if "=" in item:
                k, v = item.split("=", 1)
                env_dict[k.strip()] = v.strip()
            else:
                k = item.strip()
                env_dict[k] = os.environ.get(k, "")

        return await mcp_add(
            name=name,
            command=cmd,
            args=cmd_args,
            env=env_dict,
            url=parsed.url,
            test=not parsed.no_test,
            config_path=parsed.config,
            con=out,
        )

    else:
        out.print(f"[yellow]Unknown MCP command: '{sub}'. Run 'firik-agent mcp --help' for usage.[/yellow]")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run_mcp(argv))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
