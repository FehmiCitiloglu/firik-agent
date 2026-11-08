"""Compact, horizontally scrolling tabs for the owned browser panel."""
from __future__ import annotations

import tkinter as tk
from urllib.parse import urlsplit

import customtkinter as ctk


class BrowserTabStrip(ctk.CTkFrame):
    """Keep tab identity separate from its title and from the visible viewport."""

    _BACKGROUND = ("#e3e9f1", "#202b3b")
    _ACTIVE = ("#ffffff", "#3a4c65")
    _INACTIVE = ("#d4dde9", "#29374b")
    _HOVER = ("#c2d5ec", "#415975")
    _BORDER = ("#3b8bc9", "#6cb5ee")
    _TEXT = ("#27364b", "#eef4fc")
    _MIN_TAB_WIDTH = 112
    _MAX_TAB_WIDTH = 190
    _HEIGHT = 34

    def __init__(self, master, *, on_select, on_close, on_new, **kwargs):
        kwargs.setdefault("fg_color", self._BACKGROUND)
        kwargs.setdefault("corner_radius", 6)
        kwargs.setdefault("height", self._HEIGHT + 4)
        super().__init__(master, **kwargs)
        self._on_select, self._on_close, self._on_new = on_select, on_close, on_new
        self._tab_widgets = {}
        self._tabs = {}
        self._active_tab = ""
        self._layout_job = None
        self._last_geometry = None
        self._reveal_pending = False
        self._closed = False
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left_button = ctk.CTkButton(self, text="‹", width=23, height=30,
            fg_color="transparent", text_color=self._TEXT, hover_color=self._HOVER, command=lambda: self._scroll(-1))
        self.left_button.grid(row=0, column=0, padx=(2, 0), pady=2)
        self._viewport = tk.Canvas(self, width=1, height=self._HEIGHT, borderwidth=0,
            highlightthickness=0, background=self._viewport_color())
        self._viewport.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        self.right_button = ctk.CTkButton(self, text="›", width=23, height=30,
            fg_color="transparent", text_color=self._TEXT, hover_color=self._HOVER, command=lambda: self._scroll(1))
        self.right_button.grid(row=0, column=2, padx=(0, 2), pady=2)
        self.new_button = ctk.CTkButton(self, text="+", width=30, height=30,
            fg_color="transparent", text_color=self._TEXT, hover_color=self._HOVER, command=self._on_new)
        self.new_button.grid(row=0, column=3, padx=(2, 3), pady=2)
        self.left_button.grid_remove()
        self.right_button.grid_remove()
        self._inner = ctk.CTkFrame(self._viewport, fg_color=self.cget("fg_color"),
            corner_radius=0, width=1, height=self._HEIGHT)
        self._inner.grid_propagate(False)
        self._window = self._viewport.create_window(0, 0, anchor="nw", window=self._inner)
        self._empty = ctk.CTkLabel(self._inner, text="Yeni sekme", anchor="w", height=30)
        self._empty.grid(row=0, column=0, sticky="w", padx=12)
        self._viewport.configure(xscrollcommand=self._scroll_changed)
        self.bind("<Configure>", self._schedule_layout, add="+")
        self._viewport.bind("<Configure>", self._schedule_layout, add="+")
        self._bind_wheel(self._viewport)
        self._bind_wheel(self._inner)
        self._bind_wheel(self._empty)
        self._schedule_layout()

    def _viewport_color(self):
        color = self.cget("fg_color")
        return self._apply_appearance_mode(self.cget("bg_color") if color == "transparent" else color)

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "_viewport"):
            self._viewport.configure(background=self._viewport_color())

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        if hasattr(self, "_viewport"):
            self._schedule_layout()

    def _bind_wheel(self, widget):
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, self._wheel, add="+")

    def _wheel(self, event):
        if getattr(event, "num", None) in {4, 5}:
            direction = -1 if event.num == 4 else 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return "break"
            direction = -1 if delta > 0 else 1
        self._scroll(direction)
        # Stop the conversation's global scroll binding at this local surface.
        return "break"

    @staticmethod
    def _title(tab):
        value = tab.get("title")
        if not value:
            try:
                value = urlsplit(tab.get("url") or "").hostname
            except ValueError:
                value = None
        return " ".join(str(value or "Yeni sekme").split())

    def set_tabs(self, tabs, active_tab_id):
        """Apply a status observation without recreating unchanged tab controls."""
        if self._closed:
            return
        observed = {}
        for tab in tabs:
            tab_id = tab.get("tab_id", tab.get("id"))
            if isinstance(tab_id, str) and tab_id and tab_id not in observed:
                observed[tab_id] = tab
        active = active_tab_id if active_tab_id in observed else next(
            (tab_id for tab_id, tab in observed.items() if tab.get("active")), next(iter(observed), ""))
        changed = tuple(observed) != tuple(self._tabs)
        self._reveal_pending |= active != self._active_tab or changed
        self._tabs, self._active_tab = observed, active
        for tab_id in self._tab_widgets.keys() - observed.keys():
            self._tab_widgets.pop(tab_id)["frame"].destroy()
        for index, (tab_id, tab) in enumerate(observed.items()):
            widgets = self._tab_widgets.get(tab_id)
            if widgets is None:
                frame = ctk.CTkFrame(self._inner, height=32, corner_radius=7, border_width=1)
                frame.grid_propagate(False)
                frame.grid_columnconfigure(0, weight=1)
                title = ctk.CTkButton(frame, text="", anchor="w", height=28, width=1,
                    corner_radius=5, fg_color="transparent", text_color=self._TEXT, hover_color=self._HOVER,
                    font=ctk.CTkFont(size=12), command=lambda selected=tab_id: self._on_select(selected))
                title.grid(row=0, column=0, sticky="ew", padx=(5, 0), pady=2)
                close = ctk.CTkButton(frame, text="×", width=21, height=24,
                    fg_color="transparent", text_color=self._TEXT, hover_color=self._HOVER,
                    command=lambda closed=tab_id: self._on_close(closed))
                close.grid(row=0, column=1, padx=(1, 4), pady=4)
                widgets = self._tab_widgets[tab_id] = {"frame": frame, "title": title, "close": close}
                for widget in widgets.values():
                    self._bind_wheel(widget)
            widgets["frame"].grid(row=0, column=index, padx=(0, 3))
            color = self._ACTIVE if tab_id == active else self._INACTIVE
            widgets["frame"].configure(fg_color=color,
                border_color=self._BORDER if tab_id == active else color)
            widgets["full_title"] = self._title(tab)
        if observed:
            self._empty.grid_remove()
        else:
            self._empty.grid()
        self._schedule_layout()

    def _schedule_layout(self, event=None):
        if event is not None and event.widget is self._viewport:
            # Showing overflow controls changes the canvas width after the
            # first layout. Reveal again once Tk has applied that geometry.
            self._reveal_pending = True
        if not self._closed and self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self):
        self._layout_job = None
        if self._closed:
            return
        scale = self._get_widget_scaling()
        available = max(1, self.winfo_width() / scale - 41)
        count = len(self._tabs)
        overflow = count * (self._MIN_TAB_WIDTH + 3) > available
        for button in (self.left_button, self.right_button):
            button.grid() if overflow else button.grid_remove()
        if overflow:
            available = max(1, available - 50)
        tab_width = max(self._MIN_TAB_WIDTH, min(self._MAX_TAB_WIDTH,
            int(available / max(1, count)) - 3))
        for widgets in self._tab_widgets.values():
            widgets["frame"].configure(width=tab_width)
            title = widgets["full_title"]
            limit = max(6, int((tab_width - 38) / 7))
            widgets["title"].configure(text=title if len(title) <= limit else title[:limit - 1] + "…")
        width = max(120 if not count else 1, count * (tab_width + 3))
        self._inner.configure(width=width, height=self._HEIGHT)
        height = round(self._HEIGHT * scale)
        self._viewport.configure(height=height, scrollregion=(0, 0, round(width * scale), height))
        self._viewport.itemconfigure(self._window, width=round(width * scale), height=height)
        geometry = (round(available), tab_width, count, scale)
        if geometry != self._last_geometry:
            self._reveal_pending = True
        self._last_geometry = geometry
        if self._reveal_pending:
            self._reveal_pending = False
            self._reveal_active(tab_width, width, self._viewport.winfo_width() / scale)
        self._scroll_changed(*self._viewport.xview())

    def _reveal_active(self, tab_width, content_width, available):
        if self._active_tab not in self._tabs or not content_width:
            return
        index = tuple(self._tabs).index(self._active_tab)
        left, right = index * (tab_width + 3), (index + 1) * (tab_width + 3)
        start = self._viewport.xview()[0] * content_width
        if left < start:
            self._viewport.xview_moveto(left / content_width)
        elif right > start + available:
            self._viewport.xview_moveto((right - available) / content_width)

    def _scroll(self, direction):
        region = self._viewport.cget("scrollregion").split()
        width = float(region[2]) if len(region) == 4 else 0
        if width:
            step = min(180 * self._get_widget_scaling(), self._viewport.winfo_width() * .8)
            self._viewport.xview_moveto(self._viewport.xview()[0] + direction * step / width)

    def _scroll_changed(self, first, last):
        self.left_button.configure(state="disabled" if float(first) <= .001 else "normal")
        self.right_button.configure(state="disabled" if float(last) >= .999 else "normal")

    def destroy(self):
        self._closed = True
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        super().destroy()
