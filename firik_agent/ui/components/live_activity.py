"""Animated activity indicators driven by actual stream events."""
from __future__ import annotations

import time

import customtkinter as ctk

from firik_agent.mcp.models import redact_secrets


def command_preview(tool_name: str, arguments: dict) -> str:
    """Keep a useful, redacted command visible outside the details drawer."""
    value = next((arguments[key] for key in ("command", "cmd", "code", "path", "url")
                  if arguments.get(key)), tool_name)
    preview = " ".join(redact_secrets(str(value)).split())
    return preview[:240] + ("…" if len(preview) > 240 else "")


class ActivityIndicator(ctk.CTkLabel):
    """A UI heartbeat, not a claim that the remote process is making progress."""
    FRAMES = ("◐", "◓", "◑", "◒")

    def __init__(self, master, **kwargs):
        super().__init__(master, text="", anchor="w", **kwargs)
        self.stage = ""
        self.started_at = time.monotonic()
        self.last_event_at = self.started_at
        self._frame = 0
        self._job = None
        self.running = False

    def start(self, stage: str) -> None:
        self.stop()
        self.running = True
        self.started_at = time.monotonic()
        self.set_stage(stage)
        self._tick()

    def set_stage(self, stage: str) -> None:
        if stage != self.stage:
            self.started_at = time.monotonic()
        self.stage = stage
        self.last_event_at = time.monotonic()
        self._render()

    def _render(self) -> None:
        now = time.monotonic()
        elapsed = int(now - self.started_at)
        quiet = int(now - self.last_event_at)
        waiting = f" · Son etkinlik {quiet} sn önce" if quiet >= 15 else ""
        self.configure(text=f"{self.FRAMES[self._frame]} {self.stage} · {elapsed // 60:02d}:{elapsed % 60:02d}{waiting}")

    def _tick(self) -> None:
        self._job = None
        if self.running:
            self._frame = (self._frame + 1) % len(self.FRAMES)
            self._render()
            self._job = self.after(200, self._tick)

    def stop(self) -> None:
        self.running = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def destroy(self) -> None:
        self.stop()
        super().destroy()


class LiveActivity(ctk.CTkFrame):
    """Always visible above the composer while a conversation is running."""
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=("#e0f2fe", "#14263a"), corner_radius=8, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.indicator = ActivityIndicator(self, width=1, justify="left", wraplength=400,
                                           font=ctk.CTkFont(size=12, weight="bold"),
                                           text_color=("#0369a1", "#7dd3fc"))
        self.indicator.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 0))
        self.detail = ctk.CTkLabel(self, text="", width=1, anchor="w", justify="left", wraplength=400,
                                   font=ctk.CTkFont(size=11), text_color=("#334155", "#cbd5e1"))
        self.detail.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 5))
        self.bind("<Configure>", self._resize, add="+")

    def _resize(self, event) -> None:
        width = max(40, int(event.width / self._get_widget_scaling()) - 20)
        self.indicator.configure(wraplength=width)
        self.detail.configure(wraplength=width)

    def show_stage(self, stage: str, detail: str) -> None:
        if not self.indicator.running:
            self.indicator.start(stage)
        else:
            self.indicator.set_stage(stage)
        self.detail.configure(text=redact_secrets(detail))

    def stop(self) -> None:
        self.indicator.stop()
