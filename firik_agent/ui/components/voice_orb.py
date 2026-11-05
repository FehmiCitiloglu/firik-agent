"""Bounded vector animation driven by actual voice state and input levels."""
from __future__ import annotations

import math
import tkinter as tk

import customtkinter as ctk

from firik_agent.ui.theme import THEME_COLORS


def blend(background, foreground, amount):
    a, b = (tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) for value in (background, foreground))
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


class VoiceOrb(ctk.CTkFrame):
    """A state indicator, not an output waveform or an audio-device owner."""

    ANIMATED = {"listening", "recording", "speech_detected", "transcribing", "thinking", "executing", "speaking"}

    def __init__(self, master, *, motion=True, **kwargs):
        super().__init__(master, fg_color="transparent", width=280, height=182, **kwargs)
        self.pack_propagate(False)
        self._closed = False
        self._job = None
        self._phase = 0.
        self._motion = motion
        self.state = "ready"
        self.level = 0.
        self._shown_level = 0.
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self._halos = [self.canvas.create_oval(0, 0, 1, 1, width=0) for _ in range(5)]
        self._track = self.canvas.create_oval(0, 0, 1, 1, width=1)
        self._arcs = [self.canvas.create_arc(0, 0, 1, 1, style="arc", width=2) for _ in range(8)]
        self._core = self.canvas.create_oval(0, 0, 1, 1, width=1)
        self._bars = [self.canvas.create_line(0, 0, 1, 1, width=3, capstyle="round") for _ in range(9)]
        self._dots = [self.canvas.create_oval(0, 0, 1, 1, width=0) for _ in range(4)]
        self.canvas.bind("<Configure>", lambda event: self._render())
        self.canvas.bind("<Map>", lambda event: self._schedule())
        self.canvas.bind("<Unmap>", lambda event: self._cancel_animation())
        self._render()

    def set_state(self, state):
        self.state = str(getattr(state, "value", state))
        if self.state not in {"listening", "recording", "speech_detected"}:
            self.level = self._shown_level = 0.
        self._render()
        self._schedule()

    def set_level(self, value):
        self.level = max(0., min(1., float(value))) if self.state in {"listening", "recording", "speech_detected"} else 0.
        if not self._motion:
            self._shown_level = self.level
        self._render()

    def set_motion(self, enabled):
        self._motion = bool(enabled)
        if not self._motion:
            self._shown_level = self.level
        self._render()
        self._schedule()

    def _cancel_animation(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _schedule(self):
        if self._closed:
            return
        animate = self._motion and self.state in self.ANIMATED and self.winfo_ismapped()
        if not animate:
            self._cancel_animation()
        elif self._job is None:
            self._job = self.after(50, self._tick)

    def _tick(self):
        self._job = None
        if self._closed:
            return
        self._phase = (self._phase + .065) % (math.pi * 2)
        self._shown_level += (self.level - self._shown_level) * .3
        self._render()
        self._schedule()

    def _render(self):
        if self._closed:
            return
        palette = THEME_COLORS["dark" if self._get_appearance_mode() == "dark" else "light"]
        background = palette["bg_primary"]
        accent = palette["accent_secondary"]
        if self.state in {"thinking", "transcribing"}:
            accent = "#a78bfa" if self._get_appearance_mode() == "dark" else "#7c3aed"
        elif self.state == "executing":
            accent = palette["warning"]
        elif self.state == "speaking":
            accent = palette["success"]
        elif self.state in {"error", "permission_required"}:
            accent = palette["danger"]
        elif self.state in {"idle", "interrupted", "disabled"}:
            accent = palette["text_muted"]
        self.canvas.configure(background=background)
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if width < 10 or height < 10:
            width, height = 280, 182
        cx, cy = width / 2, height / 2
        radius = min(height / 2 - 12, width / 2 - 18)
        phase = self._phase if self._motion and self.state in self.ANIMATED else 0.
        level = self._shown_level
        pulse = (1 + math.sin(phase * 2)) / 2 if phase else .25
        for index, item in enumerate(self._halos):
            r = radius * (1 - index * .09) + level * (4 - index * .5)
            self.canvas.coords(item, cx - r, cy - r, cx + r, cy + r)
            self.canvas.itemconfigure(item, fill=blend(background, accent, .025 + index * .016 + pulse * .012))
        r = radius * .93
        self.canvas.coords(self._track, cx - r, cy - r, cx + r, cy + r)
        self.canvas.itemconfigure(self._track, outline=blend(background, accent, .23))
        for index, item in enumerate(self._arcs):
            r = radius * (.88 if index < 4 else .71) + level * 3
            self.canvas.coords(item, cx - r, cy - r, cx + r, cy + r)
            direction = 1 if index < 4 else -1
            self.canvas.itemconfigure(item, start=index * 90 + phase * 35 * direction,
                extent=52 if index < 4 else 30, outline=blend(background, accent, .78 if index % 2 else .38))
        r = radius * .53 + level * 4
        self.canvas.coords(self._core, cx - r, cy - r, cx + r, cy + r)
        self.canvas.itemconfigure(self._core, fill=blend(background, accent, .09), outline=blend(background, accent, .35))
        for index, item in enumerate(self._bars):
            x = cx + (index - 4) * radius * .075
            shape = 1 - abs(index - 4) / 5
            # Input height follows the measured microphone level. Other
            # states use orbital motion only; no fabricated output meter.
            h = 3 + shape * (radius * .08 + level * radius * .38)
            self.canvas.coords(item, x, cy - h, x, cy + h)
            self.canvas.itemconfigure(item, fill=blend(background, accent, .6 + shape * .35))
        for index, item in enumerate(self._dots):
            angle = index * math.pi / 2 + phase * .3
            x, y = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
            self.canvas.coords(item, x - 1.6, y - 1.6, x + 1.6, y + 1.6)
            self.canvas.itemconfigure(item, fill=accent)

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "canvas"):
            self._render()

    def destroy(self):
        self._closed = True
        self._cancel_animation()
        super().destroy()
