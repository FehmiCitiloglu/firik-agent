"""Compact, inspectable activity for the terminal REPL.

Only the presentation changes: full tool results stay in the agent's context.
prompt_toolkit is imported lazily so the plain-terminal fallback still works.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

from rich.cells import chop_cells


@dataclass(eq=False)
class ActivityEntry:
    title: str
    detail: str
    status: str = ""
    expanded: bool = False
    tool_name: str | None = None


class ActivityLog:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []
        self.running = False
        self.notify = lambda: None

    def thought(self, text: str) -> None:
        self.entries.append(ActivityEntry("Thinking", text))
        self.notify()

    def tool_start(self, name: str, args: dict[str, Any]) -> None:
        self.entries.append(ActivityEntry(
            name, "Arguments:\n" + json.dumps(args, ensure_ascii=False, indent=2),
            status="running", tool_name=name,
        ))
        self.notify()

    def tool_end(self, name: str, result: str) -> None:
        for entry in reversed(self.entries):
            if entry.tool_name == name and entry.status == "running":
                entry.detail += "\n\nResult:\n" + result
                # A returned result is not necessarily a successful operation.
                entry.status = "returned"
                break
        self.notify()

    def finish(self) -> None:
        self.running = False
        for entry in self.entries:
            if entry.status == "running":
                entry.status = "interrupted"
        self.notify()

    @property
    def summary(self) -> str:
        tools = sum(entry.tool_name is not None for entry in self.entries)
        thoughts = sum(entry.tool_name is None for entry in self.entries)
        active = next((e.tool_name for e in reversed(self.entries) if e.status == "running"), None)
        state = f"Running {active}" if active else "Firik düşünüyor" if self.running else "Activity"
        return f"{state} · {tools} tool calls · {thoughts} thinking blocks"

    def plain_details(self) -> str:
        return "\n\n".join(f"{e.title} ({e.status or 'thinking'})\n{e.detail}" for e in self.entries)


class ActivityView:
    """A bounded live panel, or a full-screen inspector after a turn."""

    def __init__(self, log: ActivityLog, *, live: bool = False, **app_kwargs: Any) -> None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.data_structures import Point
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.styles import Style

        self.log = log
        self.live = live
        self.expanded = not live
        self.cursor = 0
        self._owners: list[ActivityEntry | None] = []
        self._mouse_events = MouseEventType
        bindings = KeyBindings()

        @bindings.add("c-o")
        def toggle_panel(event):
            if not live:
                event.app.exit()
            else:
                self.expanded = not self.expanded
                self.cursor = 0

        @bindings.add("enter")
        @bindings.add(" ")
        @bindings.add("o")
        def toggle_entry(event):
            self.toggle_selected()

        @bindings.add("g", "g")
        def move_top(event):
            self.fragments()
            self.cursor = 0

        @bindings.add("G")
        def move_bottom(event):
            self.fragments()
            self.cursor = max(0, len(self._owners) - 1)

        @bindings.add("up")
        @bindings.add("down")
        @bindings.add("k")
        @bindings.add("j")
        @bindings.add("pageup")
        @bindings.add("pagedown")
        @bindings.add("c-u")
        @bindings.add("u")
        @bindings.add("c-d")
        @bindings.add("d")
        @bindings.add("home")
        @bindings.add("end")
        def move(event):
            key = event.key_sequence[0].key
            self.fragments()
            last = max(0, len(self._owners) - 1)
            delta = {
                "up": -1, "k": -1,
                "down": 1, "j": 1,
                "pageup": -10, "u": -10, "c-u": -10,
                "pagedown": 10, "d": 10, "c-d": 10,
            }.get(key, 0)
            self.cursor = 0 if key == "home" else last if key == "end" else max(0, min(last, self.cursor + delta))

        @bindings.add("q")
        @bindings.add("escape")
        def close(event):
            if live:
                self.expanded = False
                self.cursor = 0
            else:
                event.app.exit()

        @bindings.add("c-c")
        def interrupt(event):
            event.app.exit(exception=KeyboardInterrupt() if live else None)

        control = FormattedTextControl(
            self.fragments, focusable=True,
            get_cursor_position=lambda: Point(x=0, y=self.cursor),
        )
        body = Window(
            control, wrap_lines=True, always_hide_cursor=True, cursorline=True,
            height=(lambda: 12 if self.expanded else 1) if live else None,
        )
        footer = Window(FormattedTextControl(
            "Ctrl+O: details · j/k/↑↓: scroll · gg/G: top/bottom · Enter/space/o: fold · "
            + ("Esc/q: collapse · Ctrl+C: exit" if live else "q/Esc: back")
        ), height=1, style="reverse")
        self.app = Application(
            layout=Layout(HSplit([body, footer])), key_bindings=bindings,
            full_screen=not live, mouse_support=True, erase_when_done=True,
            style=Style.from_dict({"cursor-line": "reverse"}),
            **app_kwargs,
        )

    def toggle_selected(self) -> None:
        self.fragments()
        entry = self._owners[self.cursor] if self._owners else None
        if entry is None:
            self.expanded = not self.expanded
            self.cursor = 0
        else:
            entry.expanded = not entry.expanded
            # Closing from a detail line must leave the cursor on its header.
            self.cursor = self._owners.index(entry)

    def fragments(self):
        from prompt_toolkit.formatted_text import FormattedText

        rows = []
        self._owners = []

        def add(text, owner=None, style="", clickable=False):
            index = len(self._owners)
            self._owners.append(owner)

            def interact(event):
                if event.event_type in (self._mouse_events.SCROLL_UP, self._mouse_events.SCROLL_DOWN):
                    delta = -3 if event.event_type == self._mouse_events.SCROLL_UP else 3
                    self.cursor = max(0, min(len(self._owners) - 1, self.cursor + delta))
                    self.app.invalidate()
                    return None
                if event.event_type != self._mouse_events.MOUSE_UP or not clickable:
                    return NotImplemented
                self.cursor = index
                self.toggle_selected()
                self.app.invalidate()

            rows.append((style, text + "\n", interact))

        add(("▼ " if self.expanded else "▶ ") + self.log.summary, style="bold ansicyan", clickable=True)
        if self.expanded:
            for entry in self.log.entries:
                # One summary line, even for unexpected multiline tool names.
                title = " ".join(entry.title.split())
                add(f"  {'▼' if entry.expanded else '▶'} {title}"
                    + (f" · {entry.status}" if entry.status else ""), entry, "ansicyan", True)
                if entry.expanded:
                    width = max(1, self.app.output.get_size().columns - 4)
                    for line in entry.detail.splitlines() or [""]:
                        # Physical rows make even a single huge JSON line reachable
                        # with arrow keys and the mouse wheel, including on resize.
                        for part in chop_cells(line.expandtabs(4), width) or [""]:
                            add("    " + part, entry)
        self.cursor = min(self.cursor, len(self._owners) - 1)
        # Avoid an extra blank line at the bottom.
        style, text, handler = rows[-1]
        rows[-1] = (style, text.rstrip("\n"), handler)
        return FormattedText(rows)

    async def run(self, operation=None):
        """Run the inspector, keeping its worker and terminal lifecycle together."""
        worker = None
        self.log.notify = self.app.invalidate

        async def execute():
            try:
                result = await operation()
            except Exception as exc:
                self.app.exit(exception=exc)
            else:
                self.app.exit(result=result)

        def start():
            nonlocal worker
            if operation is not None:
                worker = asyncio.create_task(execute())

        try:
            return await self.app.run_async(pre_run=start)
        finally:
            if worker is not None:
                if not worker.done():
                    worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            self.log.notify = lambda: None
