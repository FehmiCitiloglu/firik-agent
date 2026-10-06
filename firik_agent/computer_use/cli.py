"""CLI subcommands and doctor diagnostic for Computer Use."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from firik_agent.computer_use.backends import get_backend
from firik_agent.computer_use.permissions import PermissionChecker
from firik_agent.computer_use.service import ComputerUseService

console = Console()


def run_doctor() -> int:
    """Runs cross-platform diagnostics for Computer Use and prints an informative report."""
    console.print(Panel("[bold cyan]Firik Agent — Computer Use Diagnostics & Doctor[/bold cyan]", expand=False))

    service = ComputerUseService()
    env = service.environment()

    # System & Environment Table
    sys_table = Table(title="Host Environment", show_header=True, header_style="bold magenta")
    sys_table.add_column("Property", style="bold")
    sys_table.add_column("Value")
    sys_table.add_row("Operating System", f"{env.os} ({env.os_version})")
    sys_table.add_row("Architecture", env.architecture)
    sys_table.add_row("Display Server", env.display_server)
    sys_table.add_row("Desktop Environment", env.desktop_environment)
    sys_table.add_row("Primary Scale Factor", f"{env.scale_factor}x")
    console.print(sys_table)

    # Displays Table
    disp_table = Table(title="Connected Displays", show_header=True, header_style="bold blue")
    disp_table.add_column("ID", style="bold")
    disp_table.add_column("Name")
    disp_table.add_column("Logical Bounds (X, Y, W, H)")
    disp_table.add_column("Scale")
    disp_table.add_column("Primary")

    for d in env.displays:
        disp_table.add_row(
            str(d.id),
            d.name,
            f"{d.x}, {d.y}, {d.width}x{d.height}",
            f"{d.scale_factor}x",
            "✓ Yes" if d.is_primary else "No",
        )
    console.print(disp_table)

    # Permissions Table
    perm_table = Table(title="System Permissions", show_header=True, header_style="bold yellow")
    perm_table.add_column("Permission", style="bold")
    perm_table.add_column("Status")

    for perm, status in env.permissions.items():
        if status == "granted":
            status_style = "[bold green]✓ GRANTED[/bold green]"
        elif status == "denied":
            status_style = "[bold red]✗ DENIED[/bold red]"
        else:
            status_style = f"[yellow]{status}[/yellow]"
        perm_table.add_row(perm.replace("_", " ").title(), status_style)
    console.print(perm_table)

    # Capabilities Table
    cap_table = Table(title="Supported Capabilities", show_header=True, header_style="bold green")
    cap_table.add_column("Capability", style="bold")
    cap_table.add_column("Status")

    for cap, supported in env.capabilities.items():
        cap_table.add_row(
            cap.replace("_", " ").title(),
            "[bold green]✓ Supported[/bold green]" if supported else "[bold red]✗ Unavailable[/bold red]",
        )
    console.print(cap_table)

    # Guidance for denied permissions
    if any(s == "denied" for s in env.permissions.values()):
        console.print(
            Panel(
                "[bold red]Action Required:[/bold red]\n"
                "Some permissions are missing. On macOS, grant permissions in:\n"
                "  • System Settings → Privacy & Security → Screen Recording\n"
                "  • System Settings → Privacy & Security → Accessibility\n"
                "Then restart Firik Agent.",
                title="Permission Guidance",
                border_style="red",
            )
        )
        return 1

    console.print("\n[bold green]✓ All computer use checks passed successfully![/bold green]\n")
    return 0


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="firik-agent computer-use",
        description="Firik Agent Computer Use Diagnostics and Services",
    )
    subparsers = parser.add_subparsers(dest="command")

    # doctor
    subparsers.add_parser("doctor", help="Run computer use environment and permission diagnostics")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run standalone stdio MCP server for computer-use")
    mcp_parser.add_argument("--mock", action="store_true", help="Use mock backend (no real hardware actions)")

    # screenshot
    ss_parser = subparsers.add_parser("screenshot", help="Capture a diagnostic screenshot")
    ss_parser.add_argument("--output", "-o", default="screenshot.png", help="Output file path")

    parsed = parser.parse_args(args)

    if parsed.command == "doctor" or not parsed.command:
        return run_doctor()
    elif parsed.command == "mcp":
        from firik_agent.computer_use.mcp_server import ComputerUseMCPServer
        server = ComputerUseMCPServer(use_mock=parsed.mock)
        server.run_stdio()
        return 0
    elif parsed.command == "screenshot":
        service = ComputerUseService()
        res = service.screenshot()
        from pathlib import Path
        Path(parsed.output).write_bytes(res.image_bytes)
        console.print(f"[bold green]Screenshot saved to:[/bold green] {parsed.output}")
        console.print(res.text_summary)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
