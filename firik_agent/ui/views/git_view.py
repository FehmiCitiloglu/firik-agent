"""Repository intelligence and deliberate Git operations for the coding workspace.

Git runs in workers; widgets are touched only by the service's UI dispatcher.
Each request is bound to the current screen and repository generation, so a slow
blame or log result cannot overwrite a newer selection or a closed workspace.
"""
from __future__ import annotations

import csv
import json
import re
import threading
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from firik_agent.projects.git_service import GitService


SURFACE = ("#ffffff", "#101a2b")
INSET = ("#f1f5f9", "#0b1321")
BORDER = ("#d8e2ed", "#26354b")
MUTED = ("#52647a", "#94a7be")
INK = ("#172b43", "#e6eef8")
ACCENT = ("#087c84", "#30d5c8")
VIOLET = ("#7352be", "#ac8afa")


class GitTable(ctk.CTkFrame):
    """A real, sortable table with both scrollbars and stable row identities."""

    def __init__(self, master: Any, columns: list[tuple[str, str, int]], height: int = 8):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._columns = columns
        self._rows: dict[str, dict[str, Any]] = {}
        self._sort_column = ""
        self._reverse = False
        self._style_name = f"Git{id(self)}.Treeview"
        self.tree = ttk.Treeview(
            self, columns=[c[0] for c in columns], show="headings",
            height=height, style=self._style_name, selectmode="extended",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        for key, title, width in columns:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort(k))
            self.tree.column(key, width=width, minwidth=50, stretch=key in ("path", "message", "text", "title"))
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self._context_menu = tk.Menu(self, tearoff=False)
        self._context_menu.add_command(label="Seçili Satırları Kopyala", command=self.copy_selection)
        self.tree.bind("<Button-3>", self._open_context_menu)
        self.tree.bind("<Button-2>", self._open_context_menu)
        self.tree.bind("<Control-c>", lambda event: self.copy_selection())
        self.tree.bind("<Command-c>", lambda event: self.copy_selection())
        self.apply_theme()

    def apply_theme(self) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        background = INSET[1 if dark else 0]
        foreground = INK[1 if dark else 0]
        style = ttk.Style(self)
        style.configure(self._style_name, background=background, fieldbackground=background,
                        foreground=foreground, rowheight=29, borderwidth=0, font=("Arial", 11))
        style.configure(self._style_name + ".Heading", background=SURFACE[1 if dark else 0],
                        foreground=foreground, font=("Arial", 11, "bold"), relief="flat")
        style.map(self._style_name, background=[("selected", "#164955" if dark else "#c8eee9")],
                  foreground=[("selected", "#e6fffc" if dark else "#123b43")])
        self.tree.tag_configure("conflict", foreground="#ee7c7c" if dark else "#b91c1c")
        self.tree.tag_configure("current", foreground="#30d5c8" if dark else "#087c84")

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._rows.clear()
        for index, row in enumerate(rows):
            iid = str(index)
            self._rows[iid] = row
            tags = ("conflict",) if row.get("conflict") else (("current",) if row.get("current") else ())
            self.tree.insert("", "end", iid=iid, values=[self._display(row.get(key, ""))
                                                        for key, _, _ in self._columns], tags=tags)
        if self._sort_column:
            self.sort(self._sort_column, toggle=False)

    @staticmethod
    def _display(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if isinstance(value, bool):
            return "✓" if value else ""
        return str(value) if value is not None else ""

    def sort(self, key: str, toggle: bool = True) -> None:
        self._reverse = not self._reverse if self._sort_column == key and toggle else self._reverse if not toggle else False
        self._sort_column = key

        def sort_key(iid: str) -> tuple[int, Any]:
            value = self._rows[iid].get(key, "")
            try:
                return 0, float(value)
            except (TypeError, ValueError):
                return 1, str(value or "").casefold()

        for position, iid in enumerate(sorted(self._rows, key=sort_key, reverse=self._reverse)):
            self.tree.move(iid, "", position)
        for column, title, _ in self._columns:
            self.tree.heading(column, text=title + (" ↓" if self._reverse else " ↑") if column == key else title)

    def selected(self) -> list[dict[str, Any]]:
        return [self._rows[iid] for iid in self.tree.selection() if iid in self._rows]

    def copy_selection(self) -> None:
        rows = self.selected()
        if rows:
            text = "\n".join("\t".join(self._display(row.get(key, "")) for key, _, _ in self._columns) for row in rows)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _open_context_menu(self, event: Any) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.selected():
            try:
                self._context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._context_menu.grab_release()


class GitView(ctk.CTkFrame):
    """Git Studio: history, authorship, branch comparison, changes and recovery."""

    SECTIONS = [
        ("dashboard", "Genel Bakış"), ("history", "Commit Geçmişi"),
        ("ownership", "Kim Değiştirdi?"), ("branches", "Dallar"),
        ("changes", "Değişiklikler"), ("toolbox", "Araç Kutusu"),
        ("recovery", "Kurtarma"),
    ]

    def __init__(self, master: Any, service: Any, project_path: str,
                 on_ask_agent: Callable[[str], None] | None = None,
                 on_open_file: Callable[[str], None] | None = None, **kwargs: Any):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.service = service
        self.project_path = str(project_path)
        self.on_ask_agent = on_ask_agent
        self.on_open_file = on_open_file
        self._generation = 0
        self._requests: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._callback_channel = ""
        self._busy: set[str] = set()
        self._closed = False
        self._mutation_busy = False
        self._active_section = "dashboard"
        self._dashboard_data: dict[str, Any] | None = None
        self._catalog: list[dict[str, Any]] = []
        self._tool_fields: dict[str, Any] = {}
        self._tool_id = ""
        self._nav_columns = 0
        self._theme_mode = ctk.get_appearance_mode()
        self._tables: list[GitTable] = []
        self._canvases: list[tk.Canvas] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self.nav = ctk.CTkFrame(self, fg_color="transparent")
        self.nav.grid(row=1, column=0, sticky="ew", pady=(8, 5))
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, title in self.SECTIONS:
            button = ctk.CTkButton(self.nav, text=title, height=34, corner_radius=8,
                                   width=100, font=ctk.CTkFont(size=11, weight="bold"),
                                   command=lambda name=key: self.show_section(name))
            self.nav_buttons[key] = button
        self.status_frame = ctk.CTkFrame(self, fg_color=INSET, corner_radius=8)
        self.status_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.status_frame.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(self.status_frame, text="Depo inceleniyor…", anchor="w",
                                       justify="left", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.status_label.grid(row=0, column=0, sticky="ew", padx=12, pady=7)
        self.cancel_button = self._button(self.status_frame, "İptal", self.cancel_requests, width=62)
        self.cancel_button.grid(row=0, column=1, padx=(0, 8), pady=4)
        self.cancel_button.grid_remove()
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.body.grid(row=3, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self._resize, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.show_section("dashboard")

    def _build_header(self) -> None:
        hero = ctk.CTkFrame(self, fg_color=SURFACE, border_width=1, border_color=BORDER, corner_radius=13)
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(hero, fg_color="transparent")
        title.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        ctk.CTkLabel(title, text="GIT GOD", font=ctk.CTkFont(size=21, weight="bold"),
                     text_color=ACCENT, anchor="w").pack(fill="x")
        self.repository_label = ctk.CTkLabel(title, text=Path(self.project_path).name,
                                           anchor="w", font=ctk.CTkFont(size=11), text_color=MUTED)
        self.repository_label.pack(fill="x")
        actions = ctk.CTkFrame(hero, fg_color="transparent")
        actions.grid(row=0, column=1, padx=(0, 12), pady=12)
        self._button(actions, "Yenile", self.refresh, width=70).grid(row=0, column=0, padx=3)
        self._button(actions, "Dışa Aktar", self.export_dashboard, width=86).grid(row=0, column=1, padx=3)
        self._button(actions, "Ajana Sor", self.ask_agent, width=78, accent=True).grid(row=1, column=0, columnspan=2, sticky="ew", padx=3, pady=(5, 0))

    def _button(self, master: Any, text: str, command: Callable[[], None], width: int = 110,
                accent: bool = False) -> ctk.CTkButton:
        return ctk.CTkButton(master, text=text, command=command, width=width, height=30,
                            fg_color=("#087c84", "#174b53") if accent else ("#e8edf4", "#233249"),
                            hover_color=("#06636a", "#246771") if accent else ("#dce4ee", "#31435d"),
                            text_color=("#ffffff", "#e6fffc") if accent else INK,
                            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=7)

    def _resize(self, event: Any = None) -> None:
        if self._closed or event is not None and event.widget is not self:
            return
        width = max(300, self.winfo_width())
        columns = min(7, max(2, width // 137))
        if columns != self._nav_columns:
            for column in range(7):
                self.nav.grid_columnconfigure(column, weight=1 if column < columns else 0)
            for index, (key, _) in enumerate(self.SECTIONS):
                self.nav_buttons[key].grid(row=index // columns, column=index % columns, sticky="ew", padx=3, pady=3)
            self._nav_columns = columns
        self.status_label.configure(wraplength=max(160, width - 125))
        self.repository_label.configure(wraplength=max(130, width - 230))
        mode = ctk.get_appearance_mode()
        if mode != self._theme_mode:
            self._theme_mode = mode
            for table in self._tables:
                table.apply_theme()
            for canvas in self._canvases:
                canvas.configure(background=self._color(INSET))
                if callable(getattr(canvas, "_redraw", None)):
                    canvas._redraw()

    @staticmethod
    def _color(pair: tuple[str, str]) -> str:
        return pair[1 if ctk.get_appearance_mode() == "Dark" else 0]

    def _on_destroy(self, event: Any) -> None:
        if event.widget is self:
            self._closed = True
            self._generation += 1

    def destroy(self) -> None:
        self._closed = True
        self._generation += 1
        super().destroy()

    def set_project_path(self, path: str) -> None:
        self.project_path = str(path)
        self._dashboard_data = None
        self.repository_label.configure(text=Path(path).name)
        self.show_section(self._active_section)

    def cancel_requests(self) -> None:
        if self._mutation_busy:
            self._status("Git değişiklik işlemi çalışıyor; tamamlanmasını bekleyin.")
            return
        self._generation += 1
        self._busy.clear()
        self.cancel_button.grid_remove()
        self._status("Görüntüleme isteği iptal edildi. Çalışan Git işlemi varsa tamamlandığında depoya uygulanır.")

    def _status(self, text: str, error: bool = False) -> None:
        if not self._closed:
            if error:
                self._errors[self._callback_channel or "view"] = text
            elif self._errors:
                text += " · Eksik sonuç: " + " · ".join(dict.fromkeys(self._errors.values()))
                error = True
            self.status_label.configure(text=text, text_color=("#b91c1c", "#fca5a5") if error else MUTED)

    def _request(self, channel: str, work: Callable[[], Any], done: Callable[[Any], None],
                 label: str = "Git verisi yükleniyor…", mutation: bool = False) -> None:
        generation = self._generation
        token = self._requests.get(channel, 0) + 1
        self._requests[channel] = token
        self._errors.pop(channel, None)
        self._errors.pop("view", None)
        self._busy.add(channel)
        if self._mutation_busy:
            self.cancel_button.grid_remove()
        else:
            self.cancel_button.grid()
        self._status(label)

        def finish(result: Any, error: str | None) -> None:
            if mutation:
                self._mutation_busy = False
            if self._closed or generation != self._generation or self._requests.get(channel) != token:
                return
            self._busy.discard(channel)
            if not self._busy:
                self.cancel_button.grid_remove()
            if error:
                self._errors[channel] = error
                self._status(error, error=True)
                return
            try:
                self._callback_channel = channel
                done(result)
            except Exception as exc:
                self._status(f"Görüntüleme hatası: {exc}", error=True)
            finally:
                self._callback_channel = ""

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:
                result, error = None, str(exc)
            else:
                error = None
            if self._closed:
                return
            try:
                self.service._dispatch(finish, result, error)
            except (RuntimeError, tk.TclError):
                pass  # The application may close while a subprocess finishes.

        threading.Thread(target=worker, daemon=True, name=f"git-studio-{channel}").start()

    def refresh(self) -> None:
        self.show_section(self._active_section)

    def _invalidate_channels(self, *channels: str) -> None:
        for channel in channels:
            self._requests[channel] = self._requests.get(channel, 0) + 1
            self._busy.discard(channel)
        if not self._busy:
            self.cancel_button.grid_remove()

    def show_section(self, name: str) -> None:
        if self._closed:
            return
        self._generation += 1
        self._busy.clear()
        self._errors.clear()
        self.cancel_button.grid_remove()
        self._active_section = name
        self._tables.clear()
        self._canvases.clear()
        for child in self.body.winfo_children():
            child.destroy()
        for key, button in self.nav_buttons.items():
            button.configure(fg_color=("#d4f4ef", "#163c43") if key == name else ("#e8edf4", "#1c293c"),
                             text_color=ACCENT if key == name else MUTED,
                             hover_color=("#bcebe4", "#23525a"))
        getattr(self, "_build_" + name)()
        self._resize()

    def _card(self, title: str, description: str = "") -> ctk.CTkFrame:
        row = len(self.body.winfo_children())
        card = ctk.CTkFrame(self.body, fg_color=SURFACE, corner_radius=11,
                            border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=1, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=INK).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        if description:
            label = ctk.CTkLabel(card, text=description, anchor="w", justify="left", wraplength=530,
                                 font=ctk.CTkFont(size=11), text_color=MUTED)
            label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 9))
            card.bind("<Configure>", lambda event: label.configure(wraplength=max(170, event.width - 30)), add="+")
        return card

    def _content(self, card: Any, row: int = 2) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(card, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 12))
        frame.grid_columnconfigure(0, weight=1)
        return frame

    def _table(self, card: Any, columns: list[tuple[str, str, int]], row: int = 2, height: int = 8) -> GitTable:
        table = GitTable(card, columns, height=height)
        table.grid(row=row, column=0, sticky="nsew", padx=14, pady=(0, 12))
        self._tables.append(table)
        return table

    def _text(self, card: Any, height: int = 210, row: int = 2) -> ctk.CTkTextbox:
        container = ctk.CTkFrame(card, fg_color=INSET, corner_radius=8)
        container.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 12))
        container.grid_columnconfigure(0, weight=1)
        text = ctk.CTkTextbox(container, height=height, fg_color=INSET, text_color=INK,
                             font=ctk.CTkFont(family="Menlo", size=11), wrap="none", corner_radius=8)
        text.grid(row=0, column=0, sticky="ew")
        horizontal = ctk.CTkScrollbar(container, orientation="horizontal", command=text._textbox.xview, height=12)
        horizontal.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 3))
        text._textbox.configure(xscrollcommand=horizontal.set)
        text.configure(state="disabled")
        return text

    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        raw = widget._textbox
        raw.tag_configure("plus", foreground="#10a886")
        raw.tag_configure("minus", foreground="#e06c75")
        raw.tag_configure("hunk", foreground="#a78bfa")
        for index, line in enumerate(text.splitlines(), 1):
            tag = "plus" if line.startswith("+") and not line.startswith("+++") else "minus" if line.startswith("-") and not line.startswith("---") else "hunk" if line.startswith("@@") else ""
            if tag:
                raw.tag_add(tag, f"{index}.0", f"{index}.end")
        widget.configure(state="disabled")

    def _entry(self, parent: Any, label: str, row: int, column: int = 0, default: str = "",
               placeholder: str = "") -> ctk.CTkEntry:
        field = ctk.CTkFrame(parent, fg_color="transparent")
        field.grid(row=row, column=column, sticky="ew", padx=(0, 7), pady=(0, 8))
        parent.grid_columnconfigure(column, weight=1)
        ctk.CTkLabel(field, text=label, anchor="w", text_color=MUTED,
                     font=ctk.CTkFont(size=10)).pack(fill="x", pady=(0, 3))
        entry = ctk.CTkEntry(field, placeholder_text=placeholder, height=31, width=100,
                             fg_color=INSET, border_color=BORDER, font=ctk.CTkFont(size=11))
        entry.pack(fill="x")
        if default:
            entry.insert(0, default)
        return entry

    def _chart_canvas(self, card: Any, height: int, redraw: Callable[[tk.Canvas], None], row: int = 2) -> tk.Canvas:
        canvas = tk.Canvas(card, width=10, height=height, background=self._color(INSET),
                           highlightthickness=0, borderwidth=0)
        canvas.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 12))
        canvas._redraw = lambda: redraw(canvas)
        canvas.bind("<Configure>", lambda event: redraw(canvas))
        self._canvases.append(canvas)
        return canvas

    # Repository dashboard -------------------------------------------------

    def _build_dashboard(self) -> None:
        settings = self._card("Deponun Nabzı", "Commit yoğunluğu, katkıcılar ve sık değişen dosyalar; seçtiğiniz geçmişten hesaplanır.")
        controls = self._content(settings)
        self.dashboard_ref = self._entry(controls, "Referans", 0, default="HEAD")
        self.dashboard_days = self._entry(controls, "Son kaç gün? (1–3650)", 0, 1, default="90")
        self._button(controls, "Analiz Et", self._load_dashboard, accent=True).grid(row=1, column=0, sticky="w")
        self.dashboard_scope = ctk.CTkLabel(controls, text="", anchor="w", justify="left", text_color=MUTED,
                                          font=ctk.CTkFont(size=10), wraplength=510)
        self.dashboard_scope.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._button(controls, "Kapsam Ayrıntıları", self._toggle_scope_notes, width=155).grid(row=3, column=0, sticky="w", pady=(7, 0))
        self.dashboard_notes = ctk.CTkLabel(controls, text="", anchor="w", justify="left", text_color=MUTED,
                                          font=ctk.CTkFont(size=10), wraplength=500)
        self.dashboard_notes.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.dashboard_notes.grid_remove()
        metrics = self._card("Genel Durum")
        self.metrics = self._content(metrics)
        self.metric_values: dict[str, Any] = {}
        for index, (key, title, color) in enumerate([
            ("commits", "COMMIT", ACCENT), ("contributors", "KATKICI", VIOLET),
            ("files", "DEĞİŞEN DOSYA", ACCENT), ("added", "EKLENEN SATIR", ("#168b68", "#64d5a7")),
            ("removed", "SİLİNEN SATIR", ("#ba515d", "#f492a4")), ("changes", "BEKLEYEN DOSYA", VIOLET),
        ]):
            box = ctk.CTkFrame(self.metrics, fg_color=INSET, corner_radius=8)
            box.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
            self.metrics.grid_columnconfigure(index % 3, weight=1)
            ctk.CTkLabel(box, text=title, text_color=MUTED, font=ctk.CTkFont(size=9)).pack(pady=(8, 0))
            value = ctk.CTkLabel(box, text="—", text_color=color, font=ctk.CTkFont(size=25, weight="bold"))
            value.pack(pady=(0, 8))
            self.metric_values[key] = value
        activity_card = self._card("Commit Takvimi", "Renk yoğunluğu günlük commit sayısını gösterir. Takvim son 365 güne kadar gösterilir; tam kapsam dışa aktarılır. Hücre üzerine gelerek sayı ve tarihi görün.")
        self.activity_canvas = self._chart_canvas(activity_card, 164, self._draw_activity)
        self.activity_hint = ctk.CTkLabel(activity_card, text="Henüz veri yok", text_color=MUTED, font=ctk.CTkFont(size=10))
        self.activity_hint.grid(row=3, column=0, sticky="w", padx=14, pady=(0, 10))
        self.activity_canvas.bind("<Motion>", self._activity_hover)
        contributors = self._card("Kim Daha Çok Katkıda Bulundu?", "Commit sayısı işin niteliğini ölçmez. Birleştirmeler ve metin satırı değişimleri seçilen kapsamla birlikte değerlendirilir.")
        self.contributor_canvas = self._chart_canvas(contributors, 190, self._draw_contributors)
        self.contributor_table = self._table(contributors, [
            ("author", "Katkıcı", 175), ("commits", "Commit", 74), ("added", "+ Satır", 75),
            ("removed", "− Satır", 75), ("last_date", "Son Katkı", 150), ("email", "E-posta", 190),
        ], row=3, height=6)
        self.contributor_table.tree.bind("<Double-1>", lambda event: self._contributor_history())
        hotspots = self._card("Sık Değişen Dosyalar", "Bir dosyayı seçerek satırların kime ait olduğunu ve dosyanın geçmişini inceleyin.")
        self.hotspot_table = self._table(hotspots, [
            ("path", "Dosya", 300), ("commits", "Commit", 70), ("last_author", "Son Değiştiren", 160),
            ("last_date", "Son Değişiklik", 150), ("added", "+ Satır", 72), ("removed", "− Satır", 72),
        ], height=8)
        self.hotspot_table.tree.bind("<Double-1>", lambda event: self._hotspot_ownership())
        self._button(hotspots, "Seçili Dosyanın Sahipliği", self._hotspot_ownership, width=190).grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))
        self._load_dashboard()

    def _load_dashboard(self) -> None:
        try:
            days = int(self.dashboard_days.get())
            if not 1 <= days <= 3650:
                raise ValueError
        except ValueError:
            self._status("Gün sayısı 1 ile 3650 arasında bir sayı olmalı.", error=True)
            return
        path, ref = self.project_path, self.dashboard_ref.get().strip() or "HEAD"
        self._request("dashboard", lambda: GitService.get_dashboard(path, days=days, ref=ref), self._render_dashboard,
                      "Commit geçmişi, katkıcılar ve dosya hareketleri analiz ediliyor…")

    def _render_dashboard(self, data: dict[str, Any]) -> None:
        if data.get("error") or data.get("status", {}).get("error"):
            self._status(str(data.get("error") or data["status"]["error"]), error=True)
            return
        self._dashboard_data = data
        summary, status, scope = data.get("summary", {}), data.get("status", {}), data.get("scope", {})
        for key, widget in self.metric_values.items():
            value = len(status.get("files", [])) if key == "changes" else summary.get(key, 0)
            widget.configure(text=f"{value:,}".replace(",", "."))
        self.contributor_table.set_rows(data.get("contributors", []))
        self.hotspot_table.set_rows(data.get("hotspots", []))
        self._draw_activity(self.activity_canvas)
        self._draw_contributors(self.contributor_canvas)
        repo = data.get("repository", {})
        branch = status.get("branch") or repo.get("branch", "HEAD")
        self.repository_label.configure(text=f"{Path(self.project_path).name}  /  {branch}")
        scope_text = f"{scope.get('ref', 'HEAD')} · {scope.get('since', '')[:10]} — {scope.get('until', '')[:10]} · {summary.get('commits', 0)} commit"
        if data.get("truncated"):
            scope_text += f" · Örnekleme sınırı: {scope.get('limit', 2000)} commit"
        if scope.get("shallow"):
            scope_text += " · Sığ depo: geçmiş eksik olabilir"
        notes = scope.get("notes", [])
        if notes:
            self.dashboard_notes.configure(text="\n\n".join(str(n) for n in notes))
        self.dashboard_scope.configure(text=scope_text)
        pending = len(status.get("files", []))
        upstream = f" · ↑{status.get('ahead', 0)} ↓{status.get('behind', 0)}" if status.get("upstream_known", bool(status.get("upstream"))) else " · Upstream yok"
        self._status(f"{branch}{upstream} · {'Çalışma ağacı temiz' if not pending else f'{pending} dosyada değişiklik'}")

    def _toggle_scope_notes(self) -> None:
        if self.dashboard_notes.winfo_manager():
            self.dashboard_notes.grid_remove()
        else:
            self.dashboard_notes.grid()

    def _draw_activity(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        data = self._dashboard_data or {}
        activity = data.get("activity", [])
        if not activity:
            canvas.create_text(18, 68, text="Bu kapsamda commit bulunamadı.", anchor="w", fill=self._color(MUTED), font=("Arial", 11))
            return
        counts = {str(item.get("date", ""))[:10]: int(item.get("commits", 0)) for item in activity}
        days = max(1, min(365, int(data.get("summary", {}).get("days", 90))))
        # Large ranges are summarized in a bounded 365-day heatmap; all values remain in the export.
        try:
            ending = date.fromisoformat(str(data.get("scope", {}).get("until", ""))[:10])
        except ValueError:
            ending = date.today()
        beginning = ending - timedelta(days=days - 1)
        beginning -= timedelta(days=beginning.weekday())
        weeks = ((ending - beginning).days // 7) + 1
        width = max(200, canvas.winfo_width())
        cell = max(3, min(18, (width - 63) // weeks - 3))
        spacing = cell + 3
        max_count = max(counts.values(), default=1) or 1
        colors = [self._color(("#e3eaf2", "#202f44")), "#205e62", "#258a87", "#30b5aa", "#5ae0c8"]
        self._activity_cells: list[tuple[int, int, int, int, str, int]] = []
        for weekday, title in ((0, "Pzt"), (2, "Çar"), (4, "Cum")):
            canvas.create_text(4, 27 + weekday * spacing + cell / 2, text=title, anchor="w", fill=self._color(MUTED), font=("Arial", 9))
        for offset in range((ending - beginning).days + 1):
            current = beginning + timedelta(days=offset)
            week, weekday = offset // 7, offset % 7
            x, y = 42 + week * spacing, 27 + weekday * spacing
            count = counts.get(current.isoformat(), 0)
            level = 0 if not count else min(4, max(1, int(count / max_count * 3) + 1))
            canvas.create_rectangle(x, y, x + cell, y + cell, fill=colors[level], outline="")
            self._activity_cells.append((x, y, x + cell, y + cell, current.isoformat(), count))
            if weekday == 0 and (current.day <= 7 or week == 0):
                canvas.create_text(x, 10, text=current.strftime("%m/%Y"), anchor="w", fill=self._color(MUTED), font=("Arial", 9))
        canvas.configure(height=max(115, 35 + 7 * spacing))

    def _activity_hover(self, event: Any) -> None:
        for x1, y1, x2, y2, day, count in getattr(self, "_activity_cells", []):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.activity_hint.configure(text=f"{day} · {count} commit")
                return

    def _draw_contributors(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        contributors = sorted((self._dashboard_data or {}).get("contributors", []), key=lambda item: item.get("commits", 0), reverse=True)[:6]
        if not contributors:
            canvas.create_text(18, 60, text="Katkıcı verisi bulunamadı.", anchor="w", fill=self._color(MUTED))
            return
        maximum = max(int(c.get("commits", 0)) for c in contributors) or 1
        available = max(80, canvas.winfo_width() - 235)
        for index, contributor in enumerate(contributors):
            y = 18 + index * 29
            author = str(contributor.get("author", ""))
            author = author if len(author) <= 22 else author[:21] + "…"
            canvas.create_text(12, y + 8, text=author, anchor="w", fill=self._color(INK), font=("Arial", 10))
            size = max(2, int(int(contributor.get("commits", 0)) / maximum * available))
            canvas.create_rectangle(165, y, 165 + available, y + 16, fill=self._color(BORDER), outline="")
            canvas.create_rectangle(165, y, 165 + size, y + 16, fill=self._color(ACCENT if index % 2 == 0 else VIOLET), outline="")
            canvas.create_text(175 + available, y + 8, text=str(contributor.get("commits", 0)), anchor="w", fill=self._color(INK), font=("Arial", 10, "bold"))

    def _hotspot_ownership(self) -> None:
        rows = self.hotspot_table.selected()
        if rows:
            self.open_ownership(rows[0]["path"])

    def _contributor_history(self) -> None:
        rows = self.contributor_table.selected()
        if rows:
            author = rows[0].get("author", "")
            self.show_section("history")
            self.history_author.insert(0, str(author))
            self._load_history()

    # Commit history and topology -----------------------------------------

    @staticmethod
    def _history_columns() -> list[tuple[str, str, int]]:
        return [("short_hash", "Commit", 86), ("message", "Mesaj", 340), ("author", "Yazar", 145),
                ("date", "Tarih", 170), ("refs", "Dallar / Etiketler", 210)]

    def _build_history(self) -> None:
        filters = self._card("Geçmişi Keşfet", "Mesaj, yazar ve dosya filtreleriyle arayın. İçerik araması, bir metnin eklendiği veya silindiği commitleri bulur (git log -S).")
        form = self._content(filters)
        self.history_ref = self._entry(form, "Referans", 0, default="HEAD")
        self.history_query = self._entry(form, "Mesajda ara", 0, 1)
        self.history_author = self._entry(form, "Yazar", 0, 2)
        self.history_path = self._entry(form, "Dosya yolu", 1)
        self.history_pickaxe = self._entry(form, "İçerikte ara (-S)", 1, 1)
        self.history_since = self._entry(form, "Şu tarihten itibaren", 1, 2, placeholder="2026-01-01")
        self.history_limit = self._entry(form, "Commit limiti (1–1000)", 2, default="100")
        self.history_all_refs = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Tüm dallar", variable=self.history_all_refs, width=100,
                        font=ctk.CTkFont(size=11)).grid(row=2, column=1, sticky="w")
        self._button(form, "Ara", self._load_history, accent=True).grid(row=2, column=2, sticky="e")
        for entry in (self.history_query, self.history_author, self.history_pickaxe, self.history_path):
            entry.bind("<Return>", lambda event: self._load_history())
        graph = self._card("Commit Ağı", "İlk 10 commitin düğümleri ve ebeveyn ilişkileri. Kesikli çizgiler görünüm dışındaki atalara uzanır. Tüm sonuçlar aşağıdaki tabloda.")
        self._history_rows: list[dict[str, Any]] = []
        self.graph_canvas = self._chart_canvas(graph, 270, self._draw_graph)
        self.history_table = self._table(graph, self._history_columns(), row=3, height=10)
        self.history_table.tree.bind("<<TreeviewSelect>>", lambda event: self._history_selected(self.history_table))
        detail = self._card("Commit Detayı")
        buttons = self._content(detail)
        self._button(buttons, "Hash Kopyala", lambda: self._copy_selected_hash(self.history_table)).grid(row=0, column=0, sticky="w")
        self._button(buttons, "Değişikliği Ajana Sor", lambda: self.ask_agent("commit"), width=176).grid(row=0, column=1, sticky="e")
        self.commit_detail = self._text(detail, height=285, row=3)
        self._load_history()

    def _load_history(self) -> None:
        self._invalidate_channels("history", "commit-detail")
        self.history_table.set_rows([])
        self._history_rows = []
        self._draw_graph(self.graph_canvas)
        self._set_text(self.commit_detail, "Geçmiş yükleniyor…")
        try:
            limit = int(self.history_limit.get())
            if not 1 <= limit <= 1000:
                raise ValueError
        except ValueError:
            self._status("Commit limiti 1 ile 1000 arasında olmalı.", error=True)
            return
        path = self.project_path
        params = dict(limit=limit, ref=self.history_ref.get().strip() or "HEAD",
                      author=self.history_author.get().strip(), query=self.history_query.get().strip(),
                      file_path=self.history_path.get(), since=self.history_since.get().strip(),
                      pickaxe=self.history_pickaxe.get(), all_refs=self.history_all_refs.get())

        def ready(rows: list[dict[str, Any]]) -> None:
            self._history_rows = rows
            self.history_table.set_rows(rows)
            self._draw_graph(self.graph_canvas)
            self._status(f"{len(rows)} commit bulundu · {'Limit doldu; daha fazla geçmiş olabilir' if len(rows) == limit else 'Filtrelenmiş geçmiş'}")

        self._request("history", lambda: GitService.get_history(path, **params), ready, "Commit geçmişi taranıyor…")

    def _draw_graph(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        rows = self._history_rows[:10]
        if not rows:
            canvas.create_text(15, 35, text="Bu filtrelerle commit bulunamadı.", anchor="w", fill=self._color(MUTED))
            return
        width = max(220, canvas.winfo_width())
        lanes: list[str] = []
        coordinates: dict[str, tuple[int, int]] = {}
        colors = [self._color(ACCENT), self._color(VIOLET), "#f5b96a", "#e58eb5"]
        positions = []
        for index, commit in enumerate(rows):
            commit_hash = commit["hash"]
            if commit_hash not in lanes:
                lanes.append(commit_hash)
            lane = lanes.index(commit_hash)
            x, y = 22 + lane * 24, 20 + index * 25
            coordinates[commit_hash] = (x, y)
            positions.append((commit, x, y, lane))
            parents = commit.get("parents", [])
            if parents:
                lanes[lane] = parents[0]
                for parent in parents[1:]:
                    if parent not in lanes:
                        lanes.append(parent)
            else:
                lanes[lane] = ""
            while lanes and not lanes[-1]:
                lanes.pop()
        for commit, x, y, lane in positions:
            for parent in commit.get("parents", []):
                target = coordinates.get(parent)
                if target:
                    canvas.create_line(x, y, target[0], target[1], fill=colors[lane % len(colors)], width=2)
                else:
                    canvas.create_line(x, y, x, min(y + 16, 280), fill=self._color(BORDER), width=2, dash=(3, 2))
        graph_width = max(x for _, x, _, _ in positions) + 20
        max_chars = max(12, (width - graph_width - 110) // 7)
        for commit, x, y, lane in positions:
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=colors[lane % len(colors)], outline=self._color(INSET), width=1)
            canvas.create_text(graph_width + 7, y, text=commit.get("short_hash", commit["hash"][:8]), anchor="w", fill=self._color(ACCENT), font=("Menlo", 10))
            message = str(commit.get("message", ""))
            canvas.create_text(graph_width + 89, y, text=message[:max_chars] + ("…" if len(message) > max_chars else ""), anchor="w", fill=self._color(INK), font=("Arial", 10))
        canvas.configure(height=35 + len(rows) * 25)

    def _history_selected(self, table: GitTable) -> None:
        rows = table.selected()
        if not rows:
            return
        selected = rows[0]
        commit_hash, path = selected.get("hash", ""), self.project_path
        target = self.commit_detail if self._active_section == "history" else self.ownership_detail

        def ready(detail: dict[str, Any]) -> None:
            metadata = detail.get("metadata", detail)
            heading = f"{commit_hash}\n{metadata.get('author', selected.get('author', ''))} · {metadata.get('date', selected.get('date', ''))}\n{metadata.get('message', selected.get('message', ''))}\n\n"
            self._set_text(target, heading + str(detail.get("diff", "")))
            self._status(f"Commit {selected.get('short_hash', commit_hash[:8])} · {len(detail.get('files', []))} dosya")

        self._request("commit-detail", lambda: GitService.get_commit_detail(path, commit_hash), ready, "Commit değişiklikleri açılıyor…")

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status("Panoya kopyalandı.")

    def _copy_selected_hash(self, table: GitTable) -> None:
        rows = table.selected()
        if rows:
            self._copy(rows[0].get("hash", ""))

    # File and line authorship --------------------------------------------

    def _build_ownership(self) -> None:
        controls = self._card("Dosyayı ve Satırı Kim Değiştirdi?", "Git blame satırın en son commit yazarını gösterir. Dosya geçmişi yeniden adlandırmaları takip eder; seçilen commitin değişikliğini inceleyebilirsiniz.")
        form = self._content(controls)
        self.ownership_path = self._entry(form, "Depoya göre dosya yolu", 0, placeholder="firik_agent/main.py")
        self.ownership_ref = self._entry(form, "Referans (boş: çalışma ağacı)", 0, 1)
        self.ownership_start = self._entry(form, "İlk satır", 1, default="1")
        self.ownership_end = self._entry(form, "Son satır (isteğe bağlı)", 1, 1)
        self.file_picker = ctk.CTkComboBox(form, values=["Takip edilen dosyalar yükleniyor…"], height=31, width=100,
                                         command=self._pick_ownership_file, font=ctk.CTkFont(size=11))
        self.file_picker.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._button(form, "Dosyayı İncele", self._load_ownership, accent=True, width=135).grid(row=3, column=0, sticky="w")
        self._button(form, "Dosyayı Aç", self._open_ownership_file).grid(row=3, column=1, sticky="e")
        self.ownership_path.bind("<Return>", lambda event: self._load_ownership())
        blame = self._card("Satır Sahipliği", "Satırı seçip çift tıklayarak onu son değiştiren commitin ayrıntısını açın. Commit edilmemiş satırlar ayrıca işaretlenir.")
        self.blame_table = self._table(blame, [
            ("line", "Satır", 60), ("author", "Son Yazar", 150), ("date", "Tarih", 155),
            ("short_hash", "Commit", 85), ("summary", "Commit Mesajı", 240), ("text", "Kod", 550),
        ], height=14)
        self.blame_table.tree.bind("<Double-1>", lambda event: self._blame_selected())
        history = self._card("Dosya Geçmişi")
        self.file_history_table = self._table(history, self._history_columns(), height=7)
        self.file_history_table.tree.bind("<<TreeviewSelect>>", lambda event: self._history_selected(self.file_history_table))
        detail = self._card("Seçilen Commitin Değişikliği")
        self.ownership_detail = self._text(detail, height=260)
        path = self.project_path

        def files_ready(files: list[str]) -> None:
            self.file_picker.configure(values=files or ["Takip edilen dosya yok"])
            self.file_picker.set("Dosya seçin veya yolu yukarıya yazın")
            self._status(f"{len(files)} takip edilen dosya · Dosya seçerek satır sahipliğini inceleyin")

        self._request("tracked-files", lambda: GitService.list_tracked_files(path), files_ready)

    def _pick_ownership_file(self, value: str) -> None:
        self.ownership_path.delete(0, "end")
        self.ownership_path.insert(0, value)
        self._load_ownership()

    def open_ownership(self, file_path: str, line: int | None = None) -> None:
        self.show_section("ownership")
        self.ownership_path.insert(0, file_path)
        if line:
            self.ownership_start.delete(0, "end")
            self.ownership_start.insert(0, str(max(1, line - 10)))
            self.ownership_end.insert(0, str(line + 10))
        self._load_ownership()

    def _load_ownership(self) -> None:
        self._invalidate_channels("blame", "file-history", "commit-detail")
        self.blame_table.set_rows([])
        self.file_history_table.set_rows([])
        self._set_text(self.ownership_detail, "")
        file_path = self.ownership_path.get()
        if not file_path:
            self._status("İncelemek için dosya yolu girin veya listeden bir dosya seçin.", error=True)
            return
        try:
            start = int(self.ownership_start.get() or "1")
            end = int(self.ownership_end.get()) if self.ownership_end.get().strip() else None
            if start < 1 or end is not None and end < start:
                raise ValueError
        except ValueError:
            self._status("Satır aralığı pozitif sayılardan oluşmalı; son satır ilk satırdan küçük olamaz.", error=True)
            return
        path, ref = self.project_path, self.ownership_ref.get().strip()
        self._request("blame", lambda: GitService.get_blame(path, file_path, start=start, end=end, ref=ref),
                      self._render_blame, "Satırların yazarları bulunuyor…")

        def history_ready(rows: list[dict[str, Any]]) -> None:
            self.file_history_table.set_rows(rows)
            if rows:
                self._status(f"{file_path} · Son değiştiren: {rows[0].get('author', '')} · {str(rows[0].get('date', ''))[:19]}")

        self._request("file-history", lambda: GitService.get_file_history(path, file_path, limit=60, ref=ref or "HEAD"), history_ready)

    def _render_blame(self, rows: list[dict[str, Any]]) -> None:
        display = []
        for row in rows:
            item = dict(row)
            if item.get("uncommitted"):
                item["author"] = "Commit edilmemiş"
                item["short_hash"] = "Çalışma ağacı"
                item["date"] = "—"
                item["summary"] = "Çalışma ağacındaki değişiklik"
                item["current"] = True
            display.append(item)
        self.blame_table.set_rows(display)
        self._status(f"{len(rows)} satır incelendi · Satıra çift tıklayarak son değişikliği açın")

    def _blame_selected(self) -> None:
        rows = self.blame_table.selected()
        if not rows or rows[0].get("uncommitted"):
            return
        row = rows[0]
        path, commit_hash = self.project_path, row["hash"]
        self._request("commit-detail", lambda: GitService.get_commit_detail(path, commit_hash),
                      lambda detail: self._set_text(self.ownership_detail, f"Satır {row.get('line')} · {row.get('author')} · {row.get('date')}\n{row.get('summary')}\n\n{detail.get('diff', '')}"))

    def _open_ownership_file(self) -> None:
        file_path = self.ownership_path.get()
        if file_path and self.on_open_file:
            self.on_open_file(file_path)
        elif file_path:
            self._copy(str(Path(self.project_path) / file_path))

    # Branch inventory and merge-base comparison --------------------------

    def _build_branches(self) -> None:
        branches = self._card("Dallar ve Upstream", "↑ gönderilmemiş, ↓ alınmamış commitleri gösterir. Dal üzerinde çift tıklayınca karşılaştırma hedefi seçilir.")
        self.branch_table = self._table(branches, [
            ("name", "Dal", 215), ("current", "Aktif", 50), ("upstream", "Upstream", 210),
            ("ahead", "↑", 55), ("behind", "↓", 55), ("author", "Son Yazar", 145),
            ("date", "Son Commit", 150), ("hash", "Hash", 180),
        ], height=9)
        compare = self._card("Dalları Karşılaştır", "İki referans arasındaki dosya diffini, ortak atayı ve her iki taraftaki commit farkını görün.")
        form = self._content(compare)
        self.compare_base = self._entry(form, "Temel referans", 0, default="HEAD~1")
        self.compare_target = self._entry(form, "Hedef referans", 0, 1, default="HEAD")
        self._button(form, "Karşılaştır", self._compare_branches, accent=True).grid(row=1, column=0, sticky="w")
        self._button(form, "Dal İşlemleri", lambda: self.open_tool("branch_create")).grid(row=1, column=1, sticky="e")
        self.branch_summary = ctk.CTkLabel(form, text="İki referans seçip karşılaştırın.", text_color=MUTED,
                                          font=ctk.CTkFont(size=11), anchor="w", wraplength=500)
        self.branch_summary.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.compare_table = self._table(compare, self._history_columns(), row=3, height=7)
        self.compare_diff = self._text(compare, height=270, row=4)
        self.branch_table.tree.bind("<Double-1>", lambda event: self._branch_selected())
        path = self.project_path
        self._request("branches", lambda: GitService.get_branch_details(path),
                      lambda rows: (self.branch_table.set_rows(rows), self._status(f"{len(rows)} dal bulundu")))

    def _branch_selected(self) -> None:
        rows = self.branch_table.selected()
        if rows:
            self.compare_target.delete(0, "end")
            self.compare_target.insert(0, rows[0]["name"])
            self._compare_branches()

    def _compare_branches(self) -> None:
        self._invalidate_channels("compare")
        self.compare_table.set_rows([])
        self._set_text(self.compare_diff, "")
        self.branch_summary.configure(text="")
        base, target, path = self.compare_base.get().strip(), self.compare_target.get().strip(), self.project_path
        if not base or not target:
            self._status("Karşılaştırmak için iki referans girin.", error=True)
            return

        def ready(data: dict[str, Any]) -> None:
            self.compare_table.set_rows(data.get("commits", []))
            self._set_text(self.compare_diff, str(data.get("stat", "")) + "\n\n" + str(data.get("diff", "")))
            self.branch_summary.configure(text=f"{base} → {target} · ↑{data.get('ahead', 0)} ↓{data.get('behind', 0)} · Ortak ata {str(data.get('merge_base', ''))[:10]} · {len(data.get('files', []))} dosya")
            self._status("Dal karşılaştırması tamamlandı.")

        self._request("compare", lambda: GitService.compare_refs(path, base, target=target), ready, "Referanslar karşılaştırılıyor…")

    # Working tree and commit flow ----------------------------------------

    def _build_changes(self) -> None:
        changes = self._card("Çalışma Ağacı ve Index", "Dosyaları seçin, diffi gözden geçirin ve commit için hazırlayın. Birden fazla dosya seçmek için Ctrl / ⌘ kullanın.")
        self.changes_table = self._table(changes, [
            ("path", "Dosya", 340), ("index_label", "Index", 125), ("worktree_label", "Çalışma Ağacı", 140),
            ("original_path", "Önceki Yol", 260),
        ], height=10)
        actions = self._content(changes, row=3)
        self._button(actions, "Seçileni Stage Et", lambda: self._change_action("stage"), width=140, accent=True).grid(row=0, column=0, sticky="w")
        self._button(actions, "Seçileni Unstage Et", lambda: self._change_action("unstage"), width=150).grid(row=0, column=1, sticky="e")
        self._button(actions, "Tümünü Stage Et", lambda: self._prepare_operation("stage", {"paths": ["."]}), width=140).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self._button(actions, "Çatışma Yardımı", lambda: self.ask_agent("conflicts"), width=150).grid(row=1, column=1, sticky="e", pady=(5, 0))
        commit = self._card("Commit Oluştur", "Commit yalnızca indexteki hazırlanmış değişiklikleri içerir. İşlemden önce komut ve mesajı onaylayın.")
        form = self._content(commit)
        self.commit_message = self._entry(form, "Commit mesajı", 0, placeholder="feat: ...")
        self._button(form, "Commit", self._commit_changes, accent=True).grid(row=1, column=0, sticky="w")
        self._button(form, "Mesajı Ajana Hazırlat", lambda: self.ask_agent("commit-message"), width=175).grid(row=1, column=1, sticky="e")
        diff = self._card("Seçilen Dosyanın Değişikliği")
        controls = self._content(diff)
        self.diff_staged = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(controls, text="Hazırlanmış diff (index)", variable=self.diff_staged,
                        command=self._load_selected_diff, font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
        self._button(controls, "Sözcük Diffi", lambda: self._diff_tool("word_diff")).grid(row=0, column=1, sticky="e")
        self.diff_text = self._text(diff, height=330, row=3)
        self.changes_table.tree.bind("<<TreeviewSelect>>", lambda event: self._load_selected_diff())
        path = self.project_path

        def ready(status: dict[str, Any]) -> None:
            if status.get("error"):
                self._status(str(status["error"]), error=True)
                return
            names = {"M": "Değişti", "A": "Eklendi", "D": "Silindi", "R": "Taşındı", "C": "Kopyalandı", "U": "Çatışma", "?": "Takipsiz", " ": "—", "": "—"}
            rows = []
            conflicts = {item["path"] if isinstance(item, dict) else str(item) for item in status.get("conflicts", [])}
            for item in status.get("files", []):
                row = dict(item)
                row["index_label"] = names.get(str(item.get("index", "")), str(item.get("index", "")))
                row["worktree_label"] = names.get(str(item.get("worktree", "")), str(item.get("worktree", "")))
                row["conflict"] = row["path"] in conflicts
                rows.append(row)
            self.changes_table.set_rows(rows)
            self._status(f"{status.get('branch', 'HEAD')} · {len(status.get('staged', []))} staged · {len(status.get('modified', []))} değiştirilmiş · {len(status.get('untracked', []))} takipsiz · {len(conflicts)} çatışma")
            self._load_selected_diff()

        self._request("status", lambda: GitService.get_status(path), ready, "Çalışma ağacı okunuyor…")

    def _change_action(self, tool_id: str) -> None:
        rows = self.changes_table.selected()
        if not rows:
            self._status("İşlem için en az bir dosya seçin.", error=True)
            return
        paths = []
        for row in rows:
            if row.get("original_path"):
                paths.append(row["original_path"])
            paths.append(row["path"])
        self._prepare_operation(tool_id, {"paths": list(dict.fromkeys(paths))})

    def _commit_changes(self) -> None:
        message = self.commit_message.get().strip()
        if not message:
            self._status("Commit mesajı yazın.", error=True)
            return
        self._prepare_operation("commit", {"message": message})

    def _load_selected_diff(self) -> None:
        rows = self.changes_table.selected()
        file_path = rows[0]["path"] if rows else ""
        path, staged = self.project_path, self.diff_staged.get()
        is_untracked = bool(rows and rows[0].get("index") == "?")

        def query() -> str:
            if is_untracked and not staged:
                return "Takipsiz dosya henüz Git diffinde yer almıyor. Dosyayı stage ettikten sonra index diffini inceleyin."
            return GitService.get_diff(path, staged=staged, max_lines=2500, file_path=file_path, strict=True)

        self._request("diff", query, lambda result: self._set_text(self.diff_text, result or "Bu seçim için fark bulunamadı."),
                      f"{'Index' if staged else 'Çalışma ağacı'} diffi yükleniyor…")

    def _diff_tool(self, tool_id: str) -> None:
        rows = self.changes_table.selected()
        params = {"path": rows[0]["path"] if rows else "", "staged": self.diff_staged.get()}
        path = self.project_path

        def ready(result: dict[str, Any]) -> None:
            self._set_text(self.diff_text, result.get("stdout", "") or result.get("stderr", "") or "Fark bulunamadı.")
            self._status("Sözcük diffi hazır." if result.get("ok") else result.get("stderr", "Git işlemi başarısız."), error=not result.get("ok"))

        self._request("diff", lambda: GitService.execute_tool(path, tool_id, params), ready)

    # Searchable catalog with validated previews --------------------------

    def _build_toolbox(self) -> None:
        catalog = self._card("Git Araç Kutusu", "Pickaxe, range-diff, cherry, bisect, reflog, rerere ve daha fazlası. Her araç açıklaması, alanları ve gerçek Git komutuyla çalışır.")
        search = self._content(catalog)
        self.tool_search = self._entry(search, "Araç adı, açıklama veya komut ara", 0)
        self.tool_search.bind("<KeyRelease>", lambda event: self._filter_tools())
        self.tool_category = ctk.CTkComboBox(search, values=["Tüm Kategoriler"], height=31, width=100,
                                           font=ctk.CTkFont(size=11), command=lambda value: self._filter_tools())
        self.tool_category.grid(row=1, column=0, sticky="ew")
        self.catalog_table = self._table(catalog, [
            ("title", "Araç", 205), ("category", "Kategori", 145), ("risk_label", "Etki", 95),
            ("description", "Ne İşe Yarar?", 540),
        ], row=3, height=10)
        self.catalog_table.tree.bind("<<TreeviewSelect>>", lambda event: self._select_tool())
        configure = self._card("Seçilen Araç")
        self.tool_title = ctk.CTkLabel(configure, text="Listeden bir araç seçin.", text_color=ACCENT,
                                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w")
        self.tool_title.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.tool_description = ctk.CTkLabel(configure, text="", text_color=MUTED, justify="left", anchor="w",
                                           font=ctk.CTkFont(size=11), wraplength=520)
        self.tool_description.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 10))
        configure.bind("<Configure>", lambda event: self.tool_description.configure(wraplength=max(180, event.width - 30)), add="+")
        self.tool_form = self._content(configure, row=4)
        actions = self._content(configure, row=5)
        self._button(actions, "Komutu Önizle", self._preview_selected_tool, width=145).grid(row=0, column=0, sticky="w")
        self.tool_run_button = self._button(actions, "Çalıştır", self._run_selected_tool, accent=True)
        self.tool_run_button.grid(row=0, column=1, sticky="e")
        self._button(actions, "Komutu Kopyala", self._copy_tool_command, width=145).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._button(actions, "Aracı Ajana Sor", lambda: self.ask_agent("tool"), width=145).grid(row=1, column=1, sticky="e", pady=(6, 0))
        self.tool_preview = self._text(configure, height=132, row=6)
        output = self._card("İşlem Çıktısı")
        self.tool_output = self._text(output, height=300)
        self._tool_id = ""
        self._tool_fields.clear()
        self._last_preview: dict[str, Any] = {}

        def ready(items: list[dict[str, Any]]) -> None:
            self._catalog = items
            self.tool_category.configure(values=["Tüm Kategoriler"] + sorted({str(item.get("category", "")) for item in items}))
            self.tool_category.set("Tüm Kategoriler")
            self._filter_tools()
            self._status(f"{len(items)} Git aracı hazır · Araç seçip gerçek komutu inceleyin")
            pending = getattr(self, "_pending_tool_id", "")
            if pending:
                self._pending_tool_id = ""
                self._activate_tool(pending)

        self._request("catalog", GitService.get_tool_catalog, ready)

    def _filter_tools(self) -> None:
        query = self.tool_search.get().strip().casefold()
        category = self.tool_category.get()
        labels = {"read": "Okuma", "write": "Değiştirir", "destructive": "Silme / Geri Alma", "network": "Ağ İşlemi"}
        rows = []
        for item in self._catalog:
            haystack = " ".join(str(item.get(key, "")) for key in ("id", "title", "description", "category", "example")).casefold()
            if query and query not in haystack or category != "Tüm Kategoriler" and str(item.get("category", "")) != category:
                continue
            row = dict(item)
            row["risk_label"] = labels.get(item.get("risk", "read"), str(item.get("risk", "")))
            rows.append(row)
        self.catalog_table.set_rows(rows)

    def _select_tool(self) -> None:
        rows = self.catalog_table.selected()
        if rows:
            self._configure_tool(rows[0])

    def _configure_tool(self, item: dict[str, Any]) -> None:
        # Invalidate previews for the previous tool before replacing the form.
        self._invalidate_channels("preview", "operation-preview")
        self._errors.pop("preview", None)
        self._errors.pop("operation-preview", None)
        self._tool_id = str(item["id"])
        self._last_preview = {}
        self.tool_title.configure(text=str(item.get("title", "")))
        self.tool_description.configure(text=str(item.get("description", "")) + ("\nÖrnek: " + str(item["example"]) if item.get("example") else ""))
        for child in self.tool_form.winfo_children():
            child.destroy()
        self._tool_fields.clear()
        for index, field in enumerate(item.get("fields", [])):
            name, default = field["name"], field.get("default", "")
            if isinstance(default, bool):
                variable = tk.BooleanVar(value=default)
                ctk.CTkCheckBox(self.tool_form, text=field.get("label", name), variable=variable,
                                font=ctk.CTkFont(size=11)).grid(row=index * 2, column=0, sticky="w", pady=(0, 9))
                self._tool_fields[name] = variable
            else:
                value = "\n".join(str(v) for v in default) if isinstance(default, list) else str(default or "")
                # Paths support newline-separated values; keep filenames with spaces intact.
                if name == "paths":
                    ctk.CTkLabel(self.tool_form, text=field.get("label", name) + " (her satıra bir yol)", text_color=MUTED,
                                 anchor="w", font=ctk.CTkFont(size=10)).grid(row=index * 2, column=0, sticky="ew")
                    entry = ctk.CTkTextbox(self.tool_form, height=75, fg_color=INSET, font=ctk.CTkFont(size=11))
                    entry.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(2, 8))
                    entry.insert("1.0", value)
                    self._tool_fields[name] = entry
                else:
                    entry = self._entry(self.tool_form, field.get("label", name) + (" *" if field.get("required") else ""), index * 2,
                                        default=value)
                    self._tool_fields[name] = entry
        self.tool_run_button.configure(text="Önizle ve Onayla" if item.get("mutates") else "Çalıştır", width=155)
        self._set_text(self.tool_preview, "Alanları doldurup komutu önizleyin.")
        self._preview_selected_tool()

    def _tool_params(self) -> dict[str, Any]:
        params = {}
        for name, widget in self._tool_fields.items():
            value = widget.get("1.0", "end-1c") if isinstance(widget, ctk.CTkTextbox) else widget.get()
            params[name] = value
        return params

    def _preview_selected_tool(self, done: Callable[[dict[str, Any]], None] | None = None) -> None:
        if not self._tool_id:
            self._status("Önce bir Git aracı seçin.", error=True)
            return
        path, tool_id, params = self.project_path, self._tool_id, self._tool_params()

        def ready(preview: dict[str, Any]) -> None:
            if preview.get("ok") is False:
                self._last_preview = {}
                self._set_text(self.tool_preview, str(preview.get("error", "Komut doğrulanamadı.")))
                self._status(str(preview.get("error", "Komut doğrulanamadı.")), error=True)
                return
            self._last_preview = preview
            warnings = "\n".join(str(w) for w in preview.get("warnings", []))
            text = str(preview.get("command", "")) + ("\n\n" + warnings if warnings else "")
            self._set_text(self.tool_preview, text)
            self._status("Komut önizlemesi hazır.")
            if done:
                done(preview)

        self._request("preview", lambda: GitService.preview_tool(path, tool_id, params), ready, "Komut doğrulanıyor…")

    def _copy_tool_command(self) -> None:
        self._preview_selected_tool(lambda preview: self._copy(str(preview.get("command", ""))))

    def _run_selected_tool(self) -> None:
        if not self._tool_id:
            self._status("Önce bir Git aracı seçin.", error=True)
            return
        self._prepare_operation(self._tool_id, self._tool_params(), toolbox=True)

    def open_tool(self, tool_id: str, params: dict[str, Any] | None = None) -> None:
        self._pending_tool_id = tool_id
        self._pending_tool_params = params or {}
        self.show_section("toolbox")

    def _activate_tool(self, tool_id: str) -> None:
        item = next((entry for entry in self._catalog if entry["id"] == tool_id), None)
        if not item:
            self._status(f"Araç bulunamadı: {tool_id}", error=True)
            return
        self._configure_tool(item)
        for name, value in getattr(self, "_pending_tool_params", {}).items():
            widget = self._tool_fields.get(name)
            if isinstance(widget, ctk.CTkEntry):
                widget.delete(0, "end")
                widget.insert(0, str(value))
            elif isinstance(widget, ctk.CTkTextbox):
                widget.delete("1.0", "end")
                widget.insert("1.0", "\n".join(value) if isinstance(value, list) else str(value))
            elif isinstance(widget, tk.BooleanVar):
                widget.set(bool(value))
        self._pending_tool_params = {}
        self._preview_selected_tool()

    def _prepare_operation(self, tool_id: str, params: dict[str, Any], toolbox: bool = False) -> None:
        if self._mutation_busy:
            self._status("Bir Git değişiklik işlemi hâlâ çalışıyor. Tamamlanmasını bekleyin.", error=True)
            return
        path = self.project_path

        def ready(preview: dict[str, Any]) -> None:
            if preview.get("ok") is False:
                if toolbox:
                    self._set_text(self.tool_preview, str(preview.get("error", "Komut doğrulanamadı.")))
                self._status(str(preview.get("error", "Komut doğrulanamadı.")), error=True)
                return
            if toolbox:
                self._last_preview = preview
                self._set_text(self.tool_preview, str(preview.get("command", "")) + "\n\n" + "\n".join(preview.get("warnings", [])))
            confirmed = False
            if preview.get("requires_confirmation") or preview.get("mutates"):
                warnings = "\n".join(str(w) for w in preview.get("warnings", []))
                prompt = f"{preview.get('description', '')}\n\nDepo: {path}\n\n{preview.get('command', '')}"
                if warnings:
                    prompt += "\n\n" + warnings
                prompt += "\n\nBu komut çalıştırılsın mı?"
                if not messagebox.askyesno(str(preview.get("title", "Git İşlemi")), prompt, parent=self,
                                          icon="warning" if preview.get("risk") == "destructive" else "question"):
                    self._status("İşlem iptal edildi.")
                    return
                confirmed = True
            if self._mutation_busy:
                self._status("Başka bir Git işlemi başladı; bu işlem tekrar onaylanmalı.", error=True)
                return
            mutates = bool(preview.get("mutates"))
            if mutates:
                self._mutation_busy = True
                self._dashboard_data = None

            def executed(result: dict[str, Any]) -> None:
                self._dashboard_data = None if mutates else self._dashboard_data
                output = str(result.get("stdout", ""))
                if result.get("stderr"):
                    output += "\n\n" + str(result["stderr"])
                if result.get("truncated"):
                    output += "\n\n[Çıktı boyut sınırında kısaltıldı.]"
                if toolbox:
                    self._set_text(self.tool_output, str(result.get("command", preview.get("command", ""))) + "\n\n" + (output.strip() or "İşlem tamamlandı; çıktı yok."))
                if result.get("ok"):
                    self._status(f"{preview.get('title', 'Git işlemi')} tamamlandı.")
                    if mutates and not toolbox:
                        self.show_section(self._active_section)
                else:
                    self._status(str(result.get("stderr") or result.get("stdout") or "Git işlemi başarısız."), error=True)

            self._request("operation", lambda: GitService.execute_tool(path, tool_id, params, confirmed=confirmed,
                                                                       expected_command=preview.get("command"),
                                                                       expected_targets=preview.get("targets")),
                          executed, f"{preview.get('title', 'Git işlemi')} çalışıyor…", mutation=mutates)

        self._request("operation-preview", lambda: GitService.preview_tool(path, tool_id, params), ready,
                      "İşlem komutu ve etkisi hazırlanıyor…")

    # Recovery inventory --------------------------------------------------

    def _build_recovery(self) -> None:
        intro = self._card("Git Hafızası ve Kurtarma", "Reflog dal hareketlerini kaydeder; stash geçici çalışmaları saklar. Bir kaydı yeni bir kurtarma dalında açarak önce içeriği inceleyebilirsiniz.")
        actions = self._content(intro)
        self._button(actions, "Stash Oluştur", lambda: self.open_tool("stash_save"), width=130).grid(row=0, column=0, sticky="w")
        self._button(actions, "Kayıptan Dal Oluştur", self._recover_selected, width=170, accent=True).grid(row=0, column=1, sticky="e")
        reflog = self._card("Reflog · HEAD Hareketleri", "Silinmiş dal veya reset öncesi commit burada bulunabilir. Yerel reflog geçmişi başka depolara gönderilmez.")
        self.reflog_table = self._table(reflog, [
            ("ref", "Hareket", 150), ("short_hash", "Commit", 90), ("message", "İşlem", 370),
            ("author", "Yazar", 140), ("date", "Tarih", 180),
        ], height=8)
        stashes = self._card("Saklanan Çalışmalar · Stash", "Kaydı seçip detayını açın. Uygulama ve silme işlemleri araç kutusunda komut önizlemesiyle yapılır.")
        self.stash_table = self._table(stashes, [
            ("ref", "Stash", 115), ("message", "Açıklama", 330), ("author", "Yazar", 135), ("date", "Tarih", 175),
        ], height=5)
        stash_actions = self._content(stashes, row=3)
        self._button(stash_actions, "Detayı Gör", lambda: self._selected_stash_tool("stash_show")).grid(row=0, column=0, sticky="w")
        self._button(stash_actions, "Stash Uygula", lambda: self._selected_stash_tool("stash_apply")).grid(row=0, column=1, sticky="e")
        worktrees = self._card("Çalışma Kopyaları · Worktree", "Aynı depoda farklı dalların bağımsız çalışma klasörleri.")
        self.worktree_table = self._table(worktrees, [
            ("path", "Klasör", 360), ("branch", "Dal", 170), ("locked", "Kilitli", 75),
            ("prunable", "Temizlenebilir", 105), ("detached", "Detached", 80),
        ], height=4)
        self._button(worktrees, "Worktree Araçları", lambda: self.open_tool("worktree_add"), width=155).grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))
        tags = self._card("Etiketler · Sürüm İşaretleri")
        self.tag_table = self._table(tags, [("name", "Etiket", 155), ("date", "Tarih", 160),
                                          ("message", "Açıklama", 330), ("hash", "Hash", 200)], height=5)
        path = self.project_path
        queries = [
            ("reflog", lambda: GitService.get_reflog(path), self.reflog_table),
            ("stashes", lambda: GitService.get_stashes(path), self.stash_table),
            ("worktrees", lambda: GitService.get_worktrees(path), self.worktree_table),
            ("tags", lambda: GitService.get_tags(path), self.tag_table),
        ]
        for channel, query, table in queries:
            self._request(channel, query, lambda rows, target=table: (target.set_rows(rows), self._status("Kurtarma geçmişi güncellendi.")))

    def _recover_selected(self) -> None:
        rows = self.reflog_table.selected()
        if not rows:
            self._status("Kurtarmak için reflog listesinden bir commit seçin.", error=True)
            return
        self.open_tool("rescue_branch", {"branch": "recovery/" + rows[0].get("short_hash", rows[0].get("hash", "")[:8]),
                                          "ref": rows[0].get("hash", "")})

    def _selected_stash_tool(self, tool_id: str) -> None:
        rows = self.stash_table.selected()
        if not rows:
            self._status("Önce bir stash kaydı seçin.", error=True)
            return
        self.open_tool(tool_id, {"stash": rows[0].get("ref", "stash@{0}")})

    # Agent handoff and actual-data export --------------------------------

    def ask_agent(self, context: str = "overview") -> None:
        path = self.project_path
        prompt = f"Git deposunu analiz et: {path}\n"
        if context == "commit" and hasattr(self, "history_table"):
            rows = self.history_table.selected()
            prompt += f"Commiti incele, amacını ve olası risklerini açıkla: {rows[0].get('hash', '') if rows else 'HEAD'}"
        elif context == "tool":
            prompt += f"Git aracı {self._tool_id} için kullanım amacını, parametrelerini ve risklerini değerlendir. Parametreler: {json.dumps(self._tool_params(), ensure_ascii=False)}"
        elif context == "commit-message":
            prompt += "Indexteki değişiklikleri incele ve kısa, açıklayıcı bir commit mesajı öner."
        elif context == "conflicts":
            prompt += "Çatışmalı dosyaları incele, her iki tarafın amacını koruyarak çözüm öner."
        elif self._active_section == "ownership":
            prompt += f"Dosyanın geçmişini ve satır sahipliğini incele: {self.ownership_path.get()}"
        else:
            prompt += "Commit geçmişini, katkıcıları, sık değişen dosyaları, dalları ve bekleyen değişiklikleri incele; somut bulgularla önerilerini yaz."
        if self.on_ask_agent:
            self.on_ask_agent(prompt)
        else:
            self._copy(prompt)

    def export_dashboard(self) -> None:
        if not self._dashboard_data:
            self._status("Dışa aktarmak için önce Genel Bakış analizini yükleyin.", error=True)
            return
        filename = filedialog.asksaveasfilename(
            parent=self, title="Git Analizini Dışa Aktar", initialfile=f"{Path(self.project_path).name}-git-analiz.json",
            defaultextension=".json", filetypes=[("JSON · Tüm analiz", "*.json"), ("CSV · Katkıcılar", "*.csv")],
        )
        if not filename:
            return
        data = self._dashboard_data

        def write() -> str:
            destination = Path(filename)
            if destination.suffix.lower() == ".csv":
                fields = ["author", "email", "commits", "added", "removed", "last_date"]
                with destination.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(data.get("contributors", []))
            else:
                destination.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(destination)

        self._request("export", write, lambda destination: self._status(f"Analiz kaydedildi: {destination}"), "Analiz dosyaya yazılıyor…")
