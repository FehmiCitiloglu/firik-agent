"""Selectable Markdown replies, without HTML execution or remote image fetching."""
from __future__ import annotations

import tkinter.font as tkfont
import math
import re
import webbrowser
from urllib.parse import urlsplit
from typing import Callable

import customtkinter as ctk
from markdown_it import MarkdownIt

from evren_agent.ui.theme import THEME_COLORS


class _TextBlock(ctk.CTkTextbox):
    """Grow to the text's display height; the conversation owns vertical scrolling."""

    def __init__(self, master, *, fixed_width=False, on_layout=None):
        self._after_jobs = set()
        self._fit_job = None
        self._last_width = None
        self._last_height = None
        self.fixed_width = fixed_width
        self.on_layout = on_layout
        self.styles = {}
        super().__init__(master, width=1, height=24, corner_radius=0,
                         border_spacing=0, fg_color="transparent",
                         text_color=(THEME_COLORS["light"]["text_primary"],
                                     THEME_COLORS["dark"]["text_primary"]),
                         font=ctk.CTkFont(size=14), wrap="none" if fixed_width else "word",
                         activate_scrollbars=fixed_width, padx=0, pady=2)
        self._textbox.bind("<Configure>", self._resize, add="+")
        families = {name.casefold() for name in tkfont.families(self)}
        requested = self._font.cget("family")
        self._prose_family = requested if requested.casefold() in families else tkfont.nametofont("TkDefaultFont").cget("family")
        # Text's own wheel binding must not scroll independently of the chat.
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._textbox.bind(event, self._wheel)

    def _wheel(self, event):
        parent = self.master
        while parent is not None:
            if isinstance(parent, ctk.CTkScrollableFrame):
                if getattr(event, "num", None) in (4, 5):
                    steps = -3 if event.num == 4 else 3
                else:
                    steps = -int(event.delta / 120) if abs(event.delta) >= 120 else -int(event.delta)
                parent._parent_canvas.yview_scroll(steps, "units")
                return "break"
            parent = getattr(parent, "master", None)

    def _resize(self, event):
        if event.width != self._last_width:
            self._last_width = event.width
            self.schedule_fit()
        elif event.height != self._last_height and self.on_layout:
            # Native platforms can apply the measured height after the first
            # scroll callback. Notify when that size is actually displayed.
            self.on_layout()
        self._last_height = event.height

    def schedule_fit(self):
        if self._fit_job is None:
            # Run after native Text layout, outside scrollbar idle callbacks.
            self._fit_job = self.after(1, self._fit)

    def _fit(self):
        self._fit_job = None
        # Tk's -update flag reenters idle callbacks while measuring. A scrollbar
        # callback can replace/destroy this block in that nested loop (Aqua Tk
        # 8.6 / Python 3.10 can crash). Measure settled geometry without reentry.
        pixels = self._textbox.count("1.0", "end", "ypixels")
        if pixels:
            pixels = pixels[0] if isinstance(pixels, tuple) else pixels
            # Include Text's padding/border and round up at fractional DPI.
            height = max(24, math.ceil((pixels + 10) / self._get_widget_scaling()))
            if self.fixed_width:
                height += 18  # Leave space for a horizontal scrollbar.
            if height != self.cget("height"):
                self.configure(height=height)
            if self.on_layout:
                self.on_layout()

    def style(self, names):
        key = "+".join(sorted(set(names))) or "body"
        if key not in self.styles:
            self.styles[key] = tuple(names)
            self._configure_style(key, names)
        return key

    def _configure_style(self, key, names):
        colors = THEME_COLORS[ctk.get_appearance_mode().lower()]
        heading = next((int(n[1:]) for n in names if n.startswith("h") and n[1:].isdigit()), 0)
        mono = "code" in names or self.fixed_width
        family = tkfont.nametofont("TkFixedFont").cget("family") if mono else self._prose_family
        size = {1: 24, 2: 20, 3: 17}.get(heading, 15 if heading else 14)
        size = size * getattr(self, "coding_font_size", 14) / 14
        font = (family, -round(size * self._get_widget_scaling()),
                "bold" if "strong" in names or heading else "normal",
                "italic" if "em" in names else "roman")
        options = {"font": font, "foreground": colors["text_primary"]}
        if "code" in names:
            options["background"] = colors["bg_input"]
        if "quote" in names:
            options.update(foreground=colors["text_secondary"], lmargin1=14, lmargin2=14)
        if "s" in names:
            options["overstrike"] = True
        if "link" in names:
            options.update(foreground=colors["accent_secondary"], underline=True)
        self._textbox.tag_configure(key, **options)

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        for key, names in self.styles.items():
            self._configure_style(key, names)
        self.schedule_fit()

    def _set_appearance_mode(self, mode):
        super()._set_appearance_mode(mode)
        for key, names in self.styles.items():
            self._configure_style(key, names)

    def destroy(self):
        for job in tuple(self._after_jobs):
            self.after_cancel(job)
        self._fit_job = None
        super().destroy()

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        def run():
            self._after_jobs.discard(job)
            func(*args)
        job = super().after(ms, run)
        self._after_jobs.add(job)
        return job

    def after_cancel(self, job):
        self._after_jobs.discard(job)
        super().after_cancel(job)


class MarkdownMessage(ctk.CTkFrame):
    """Render raw Markdown, throttling streaming updates to one per 80 ms."""

    def __init__(self, master, text="", on_render: Callable[[], None] | None = None):
        super().__init__(master, fg_color="transparent", width=1, height=1)
        self.source = text
        self.on_render = on_render
        self.get_scroll_anchor = None
        self.on_layout = None
        self._scroll_anchor = None
        self._render_job = None
        self._parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
        self.blocks = []
        self.code_sources = []
        self._render()

    def set_text(self, text, *, streaming=False):
        # Capture the viewport before changing content. Native layout may have
        # grown the scroll region by the time a throttled render starts.
        self._scroll_anchor = self.get_scroll_anchor() if self.get_scroll_anchor else None
        self.source = text
        if streaming:
            if self._render_job is None:
                self._render_job = self.after(80, self._render)
        else:
            if self._render_job is not None:
                self.after_cancel(self._render_job)
            self._render_job = None
            self._render()

    def _new_text(self, *, fixed_width=False, master=None):
        block = _TextBlock(master or self, fixed_width=fixed_width,
                           on_layout=self._layout_changed)
        block.coding_font_size = getattr(self, "coding_font_size", 14)
        block.pack(fill="x", pady=(0, 6))
        self.blocks.append(block)
        return block

    def _layout_changed(self):
        if self.on_layout and self._scroll_anchor is not None:
            self.on_layout(self._scroll_anchor)

    def _insert(self, block, text, names=(), extra=()):
        block._textbox.insert("end", text, (block.style(names), *extra))

    def _inline(self, block, tokens, inherited=()):
        names = list(inherited)
        links = []
        for token in tokens or []:
            if token.type == "link_open":
                url = token.attrGet("href") or ""
                tag = f"url-{len(block._textbox.tag_names())}"
                # Only an explicit click opens a normal web URL.
                if urlsplit(url).scheme.lower() in {"http", "https"}:
                    block._textbox.tag_bind(tag, "<Button-1>", lambda event, u=url: webbrowser.open(u))
                    block._textbox.tag_bind(tag, "<Enter>", lambda event, b=block: b._textbox.configure(cursor="hand2"))
                    block._textbox.tag_bind(tag, "<Leave>", lambda event, b=block: b._textbox.configure(cursor="xterm"))
                elif getattr(self, "coding_on_file", None) and (
                        not urlsplit(url).scheme or re.fullmatch(r"[^:\r\n]+:\d+(?:#L\d+)?", url)):
                    block._textbox.tag_bind(tag, "<Button-1>", lambda event, u=url: self.coding_on_file(u))
                links.append(tag)
                names.append("link")
            elif token.type == "link_close":
                links.pop()
                names.remove("link")
            elif token.type.endswith("_open"):
                names.append(token.tag)
            elif token.type.endswith("_close"):
                if token.tag in names:
                    names.remove(token.tag)
            elif token.type == "code_inline":
                self._insert(block, token.content, (*names, "code"), links)
            elif token.type in {"softbreak", "hardbreak"}:
                self._insert(block, " " if token.type == "softbreak" else "\n", names, links)
            elif token.type == "image":
                self._insert(block, f"[Görsel: {token.content or 'görsel'}]", names, links)
            else:
                self._insert(block, token.content, names, links)

    @staticmethod
    def _finish(block):
        # Remove paragraph spacing at the end, preserving code content separately.
        text = block._textbox.get("1.0", "end-1c")
        trailing = len(text) - len(text.rstrip("\n"))
        if trailing:
            block._textbox.delete(f"end-{trailing + 1}c", "end-1c")
        block.configure(state="disabled")
        block.schedule_fit()

    def _code(self, token):
        code = token.content
        self.code_sources.append(code)
        frame = ctk.CTkFrame(self, fg_color=("#e2e8f0", "#162031"), corner_radius=6)
        frame.pack(fill="x", pady=(2, 10))
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(header, text=token.info.split()[0][:40] if token.info.strip() else "Kod",
                     height=22, font=ctk.CTkFont(size=11), text_color=("#475569", "#94a3b8")).pack(side="left")
        ctk.CTkButton(header, text="Kodu kopyala", width=100, height=22,
                      fg_color="transparent", text_color=("#475569", "#94a3b8"),
                      command=lambda c=code: self._copy_code(c)).pack(side="right")
        if getattr(self, "coding_on_context", None):
            ctk.CTkButton(header, text="Bağlama ekle", width=95, height=22,
                          command=lambda c=code: self.coding_on_context(c)).pack(side="right")
        block = self._new_text(fixed_width=True, master=frame)
        block.pack_configure(padx=8)
        self._insert(block, code.rstrip("\n"))
        if getattr(self, "coding_on_context", None):
            self._highlight_code(block, code)
            block._textbox.bind("<Control-Shift-C>", lambda event: self._selection_context(block), add="+")
        self._finish(block)

    def _selection_context(self, block):
        try:
            self.coding_on_context(block._textbox.get("sel.first", "sel.last"))
        except Exception:
            pass
        return "break"

    @staticmethod
    def _highlight_code(block, code):
        # Lightweight lexical highlighting; never execute code or import a lexer.
        pattern = re.compile(r"(?P<comment>\#[^\n]*|//[^\n]*)|(?P<string>'[^'\n]*'|\"[^\"\n]*\")|(?P<keyword>\b(?:def|class|return|if|else|elif|for|while|import|from|async|await|try|except|const|let|function|public|private|void|true|false|null|None|True|False)\b)|(?P<number>\b\d+(?:\.\d+)?\b)")
        colors = {"comment": "#7b8797", "string": "#35a875", "keyword": "#b379e2", "number": "#c88431"}
        for name, color in colors.items():
            block._textbox.tag_configure("syntax-" + name, foreground=color)
        for match in pattern.finditer(code[:24000]):
            block._textbox.tag_add("syntax-" + match.lastgroup,
                                   f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def _copy_code(self, code):
        self.clipboard_clear()
        self.clipboard_append(code)

    def _table(self, tokens):
        rows, row, header = [], [], False
        for token in tokens:
            if token.type == "thead_open":
                header = True
            elif token.type == "thead_close":
                header = False
            elif token.type == "tr_open":
                row = []
            elif token.type == "inline":
                row.append(token)
            elif token.type == "tr_close":
                rows.append((row, header))
        widths = []
        for row, _ in rows:
            for i, cell in enumerate(row):
                if len(widths) <= i:
                    widths.append(0)
                visible = "".join(t.content for t in cell.children or [] if not t.nesting)
                widths[i] = max(widths[i], len(visible))
        block = self._new_text(fixed_width=True)
        for row, header in rows:
            for i, cell in enumerate(row):
                start = block._textbox.index("end-1c")
                self._inline(block, cell.children, ("strong",) if header else ())
                count = block._textbox.count(start, "end-1c", "chars")
                self._insert(block, " " * (max(0, widths[i] - (count[0] if count else 0)) + 3))
            self._insert(block, "\n")
        self._finish(block)

    def _render(self):
        self._render_job = None
        for child in self.winfo_children():
            child.destroy()
        self.blocks = []
        self.code_sources = []
        tokens = self._parser.parse(self.source)
        block = None
        names = []
        lists = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type in {"fence", "code_block", "table_open", "hr"}:
                if block is not None:
                    self._finish(block)
                    block = None
                if token.type in {"fence", "code_block"}:
                    self._code(token)
                elif token.type == "hr":
                    ctk.CTkFrame(self, height=1, fg_color=("#cbd5e1", "#334155")).pack(fill="x", pady=8)
                else:
                    end = next(i for i in range(index + 1, len(tokens)) if tokens[i].type == "table_close")
                    self._table(tokens[index:end + 1])
                    index = end
            else:
                if block is None:
                    block = self._new_text()
                if token.type == "inline":
                    self._inline(block, token.children, names)
                elif token.type == "heading_open":
                    names.append(token.tag)
                elif token.type == "heading_close":
                    names.remove(token.tag)
                    self._insert(block, "\n\n")
                elif token.type == "blockquote_open":
                    names.append("quote")
                elif token.type == "blockquote_close":
                    names.remove("quote")
                elif token.type in {"bullet_list_open", "ordered_list_open"}:
                    if lists and not block._textbox.get("end-2c", "end-1c") == "\n":
                        self._insert(block, "\n")
                    lists.append([token.type == "ordered_list_open", int(token.attrGet("start") or 1)])
                elif token.type in {"bullet_list_close", "ordered_list_close"}:
                    lists.pop()
                elif token.type == "list_item_open":
                    ordered, number = lists[-1]
                    self._insert(block, "  " * (len(lists) - 1) + (f"{number}. " if ordered else "• "), names)
                    lists[-1][1] += 1
                elif token.type == "list_item_close":
                    self._insert(block, "\n")
                elif token.type == "paragraph_close" and not token.hidden:
                    self._insert(block, "\n\n")
            index += 1
        if block is not None:
            self._finish(block)
        if self.on_render:
            self.on_render()

    def destroy(self):
        if self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        super().destroy()
