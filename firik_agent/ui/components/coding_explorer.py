"""Kademeli yüklenen ve indeksle ilişkili proje gezgini."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import customtkinter as ctk

from firik_agent.projects.explorer import ProjectExplorer
from firik_agent.projects.git_service import GitService
from firik_agent.projects.indexer import is_sensitive_file
from firik_agent.projects.workspace import workspace_path
from firik_agent.projects.indexer import safe_project_path


class CodingExplorer(ctk.CTkFrame):
    def __init__(self, master, service, project_id, on_open, on_context, on_preferences, on_action=None):
        super().__init__(master, fg_color=("gray92", "#161f2e"))
        self.service, self.project_id = service, project_id
        self.project = service.projects.db.get_project(project_id)
        self.on_open, self.on_context, self.on_preferences = on_open, on_context, on_preferences
        self.on_action = on_action
        self.preferences = service.projects.db.get_settings(project_id).coding_preferences
        self.nodes = {}
        self.generation = 0
        self.git_markers = {}
        self.git_snapshot = None
        self.context_paths = set(self.preferences.get("context_paths", []))
        self.touched_paths = set()
        self.shortcut_modifier = "Command" if sys.platform == "darwin" else "Control"
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text="Proje gezgini", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.query = ctk.CTkEntry(self, placeholder_text="Dosya, metin veya sembol ara")
        self.query.grid(row=1, column=0, sticky="ew", padx=8)
        self.query.bind("<Return>", lambda event: self.search())
        self.query.bind("<Escape>", lambda event: self._clear_search())
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        self.content = tk.BooleanVar(value=False)
        self.search_kind = ctk.CTkComboBox(head, values=["Dosya", "Metin", "Sembol"], width=104,
                                         state="readonly", command=lambda value: self.content.set(value == "Metin"))
        self.search_kind.set("Dosya")
        self.search_kind.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(head, text="Ara", width=40, command=self.search).pack(side="right")
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=3, column=0, sticky="nsew", padx=4)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        style = ttk.Style(self)
        dark = ctk.get_appearance_mode() == "Dark"
        style.configure("Coding.Treeview", background="#161f2e" if dark else "#ededed",
                        fieldbackground="#161f2e" if dark else "#ededed",
                        foreground="#e2e8f0" if dark else "#172033", rowheight=26)
        self.tree = ttk.Treeview(frame, show="tree", style="Coding.Treeview", selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewOpen>>", self._expand)
        self.tree.bind("<Double-1>", lambda event: self.open_selected())
        self.tree.bind("<Return>", self._open_key)
        self.tree.bind("<Shift-Return>", lambda event: self._key_action("open_ide"))
        self.tree.bind(f"<{self.shortcut_modifier}-Return>", lambda event: self._key_action("add_context"))
        self.tree.bind("<Delete>", lambda event: self._key_action("remove_context"))
        self.tree.bind("<F4>", lambda event: self._key_action("diff"))
        self.tree.bind("<Shift-F10>", self._keyboard_menu)
        self.tree.bind("<space>", self._open_key)
        self.tree.bind("<Button-3>", self._menu)
        self.tree.bind("<Button-2>", self._menu)
        self.hint = ctk.CTkLabel(self, text="Yükleniyor…", wraplength=190, anchor="w")
        self.hint.grid(row=4, column=0, sticky="ew", padx=8)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(footer, text="Yenile", width=65, command=self.refresh).pack(side="left")
        ctk.CTkButton(footer, text="Filtreler", width=65, command=self.filters).pack(side="right")
        self.refresh()

    def mark_paths(self, context_paths=None, touched_paths=None, git_status=None):
        """Update existing lazy rows from canonical workspace metadata, without reads."""
        if context_paths is not None:
            self.context_paths = set(context_paths)
        if touched_paths is not None:
            self.touched_paths = set(touched_paths)
        if git_status is not None:
            if isinstance(git_status, dict) and "files" in git_status:
                self.git_snapshot = dict(git_status)
                self.git_markers = {item["path"]: (item.get("index", "") + item.get("worktree", "")).strip()
                                    for item in git_status["files"]}
            else:
                self.git_markers = dict(git_status)
        for node, item in self.nodes.items():
            if self.tree.exists(node):
                self.tree.item(node, text=self._row_label(item))

    @staticmethod
    def _in_paths(path, selections):
        return any(path == candidate or path.startswith(candidate.rstrip("/") + "/") for candidate in selections)

    def _row_label(self, item):
        path = item.get("path", "")
        label = item.get("name") or path
        icon = "▸" if item.get("is_dir") else "◆" if Path(path).suffix in {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".swift", ".cs"} else "·"
        labels = []
        git = self.git_markers.get(path, "")
        if git:
            labels.append("Git " + git)
        if self._in_paths(path, self.context_paths):
            labels.append("Bağlam")
        if self._in_paths(path, self.touched_paths):
            labels.append("Ajan")
        if self._in_paths(path, self.preferences.get("excluded_files", [])):
            labels.append("AI hariç")
        if item.get("line"):
            label += ":" + str(item["line"])
        if item.get("matched_symbols"):
            label += " · " + ", ".join(item["matched_symbols"][:3])
        return f"{icon} {label}" + (" [" + " · ".join(labels) + "]" if labels else "")

    def focus_search(self, kind="Dosya"):
        if kind in {"Dosya", "Metin", "Sembol"}:
            self.search_kind.set(kind)
            self.content.set(kind == "Metin")
        self.query.focus_set()
        self.query.select_range(0, "end")

    def _clear_search(self):
        self.query.delete(0, "end")
        self.refresh()
        return "break"

    def _open_key(self, event=None):
        item = self.selected()
        if item.get("is_dir"):
            node = self.tree.selection()[0]
            self.tree.focus(node)
            self.tree.item(node, open=not self.tree.item(node, "open"))
            if self.tree.item(node, "open"):
                self._expand()
        else:
            self.open_selected()
        return "break"

    def _key_action(self, action):
        item = self.selected()
        if item:
            self.perform_action(action, item["path"])
        return "break"

    def _keyboard_menu(self, event=None):
        from types import SimpleNamespace
        selection = self.tree.selection()
        if selection:
            box = self.tree.bbox(selection[0])
            if box:
                x, y, width, height = box
                self._menu(SimpleNamespace(y=y + height // 2, x_root=self.tree.winfo_rootx() + x + 20,
                                           y_root=self.tree.winfo_rooty() + y + height))
        return "break"

    def perform_action(self, action, path):
        """Context preferences are reversible; tool actions use the caller's runtime."""
        if action == "open_ide":
            self.on_open(path, True)
            return
        if action == "add_context":
            self.on_context(path)
            self.context_paths.add(path)
        elif action == "remove_context":
            self.context_paths = {selected for selected in self.context_paths if not self._in_paths(selected, [path])}
            self.preferences["context_paths"] = sorted(self.context_paths)
            self.on_preferences({"context_paths": sorted(self.context_paths)})
        elif action == "exclude":
            values = self.preferences.setdefault("excluded_files", [])
            if path not in values:
                values.append(path)
            self.on_preferences({"excluded_files": list(values)})
        elif action == "include":
            values = self.preferences.get("excluded_files", [])
            self.preferences["excluded_files"] = [value for value in values if value != path]
            self.on_preferences({"excluded_files": self.preferences["excluded_files"]})
        elif action == "copy":
            self._copy_path(path)
            return
        elif action == "pin":
            self._pin(path)
            return
        elif action not in {"diff", "ask"}:
            raise ValueError("Bilinmeyen gezgin işlemi.")
        if self.on_action:
            self.on_action(action, path)
        self.mark_paths()

    def _explorer(self):
        return ProjectExplorer(self.project.local_path,
                               indexed_data=self.service.projects.db.get_all_code_indexes(self.project_id),
                               ignored_dirs=self.preferences.get("ignored_dirs"),
                               show_hidden=self.preferences.get("show_hidden", False),
                               git_status=self.git_markers)

    def _background(self, function, callback):
        generation = self.generation
        async def run():
            return await asyncio.to_thread(function)
        def done(result):
            if self.winfo_exists() and generation == self.generation:
                callback(result)
        def error(exc):
            if self.winfo_exists() and generation == self.generation:
                self.hint.configure(text=str(exc))
        self.service.runtime.submit(run(), on_done=lambda result: self.service._dispatch(done, result),
                                    on_error=lambda exc: self.service._dispatch(error, exc))

    def refresh(self):
        self.generation += 1
        self.nodes.clear()
        self.tree.delete(*self.tree.get_children())
        for label, key in (("Sabit dosyalar", "pinned_files"), ("Son dosyalar", "recent_files")):
            values = self.preferences.get(key, [])
            if values:
                parent = self.tree.insert("", "end", text=label, open=True)
                for path in values[:12]:
                    item = {"path": path, "is_dir": False}
                    node = self.tree.insert(parent, "end", text=self._row_label(item))
                    self.nodes[node] = item
        def load():
            store = getattr(self.service, "git_state", None)
            status = (store.refresh(self.project.local_path) if store is not None else self.git_snapshot
                      if self.git_snapshot is not None else GitService.get_status(self.project.local_path))
            return self._explorer().children(), status
        def show(result):
            items, status = result
            if self.git_snapshot is not None and getattr(self.service, "git_state", None) is None:
                status = self.git_snapshot
            self.mark_paths(git_status=status)
            self._populate("", items)
            self.hint.configure(text=(f"Dal: {status.get('branch') or 'Git deposu yok'}\n" +
                                       f"{len(items)} öğe · {len(self.git_markers)} Git değişikliği"))
        self._background(load, show)

    def _populate(self, parent, items):
        for item in items:
            node = self.tree.insert(parent, "end", text=self._row_label(item))
            self.nodes[node] = item
            if item["is_dir"]:
                self.tree.insert(node, "end", text="Yükleniyor…", tags=("placeholder",))
        self.hint.configure(text=f"{len(items)} öğe · Klasörleri açarak yükleyin")

    def _expand(self, event=None):
        node = self.tree.focus()
        item = self.nodes.get(node)
        if not item or not item["is_dir"]:
            return
        children = self.tree.get_children(node)
        if not children or "placeholder" not in self.tree.item(children[0], "tags"):
            return
        self.tree.delete(*children)
        self._background(lambda: self._explorer().children(item["path"]),
                         lambda items: self._populate(node, items) if self.tree.exists(node) else None)

    def search(self):
        query = self.query.get().strip()
        if not query:
            self.refresh()
            return
        self.generation += 1
        self.hint.configure(text="Aranıyor…")
        kind = "Metin" if self.content.get() else self.search_kind.get()
        def show(items):
            self.nodes.clear()
            self.tree.delete(*self.tree.get_children())
            for item in items:
                path = item.get("path", item.get("file_path", ""))
                metadata = dict(item, path=path, name=path, is_dir=False)
                node = self.tree.insert("", "end", text=self._row_label(metadata))
                self.nodes[node] = metadata
            self.hint.configure(text=f"{kind} araması · {len(items)} sonuç (en fazla 50)")
        self._background(lambda: self._search_items(query, kind), show)

    def _search_items(self, query, kind):
        explorer = self._explorer()
        if kind == "Sembol":
            results = []
            for path, item in explorer.index.items():
                symbols = [symbol for symbol in item.get("symbols", []) if query.casefold() in symbol.casefold()]
                if not symbols or not explorer._visible(path, False):
                    continue
                try:
                    safe_project_path(self.project.local_path, path)
                except (ValueError, OSError):
                    continue
                results.append({"path": path, "matched_symbols": symbols[:5], "indexed": True})
                if len(results) >= 50:
                    break
            return results
        results = explorer.search(query, content=kind == "Metin", limit=50)
        return [item for item in results if kind == "Metin" or query.casefold() in item["path"].casefold()]

    def selected(self):
        selection = self.tree.selection()
        return self.nodes.get(selection[0], {}) if selection else {}

    def open_selected(self, editor=False):
        item = self.selected()
        if not item or item.get("is_dir"):
            return
        path = item["path"]
        recent = self.preferences.setdefault("recent_files", [])
        self.preferences["recent_files"] = [path, *[p for p in recent if p != path]][:20]
        self.on_preferences({"recent_files": self.preferences["recent_files"]})
        reference = f"{path}:{item['line']}" if not editor and item.get("line") else path
        self.on_open(reference, editor)

    def _menu(self, event):
        node = self.tree.identify_row(event.y)
        if node:
            self.tree.selection_set(node)
        item = self.selected()
        menu = tk.Menu(self, tearoff=0)
        if item:
            menu.add_command(label="Önizle", command=self.open_selected)
            menu.add_command(label="IDE'de aç · Shift+Enter", command=lambda: self.perform_action("open_ide", item["path"]))
            menu.add_command(label="Yolu kopyala", command=lambda: self._copy_path(item["path"]))
            modifier = "Cmd" if self.shortcut_modifier == "Command" else "Ctrl"
            menu.add_command(label=f"Bağlama ekle · {modifier}+Enter", command=lambda: self.perform_action("add_context", item["path"]))
            menu.add_command(label="Bağlamdan çıkar · Delete", command=lambda: self.perform_action("remove_context", item["path"]))
            menu.add_command(label="Farkı aç · F4", state="normal" if self.on_action and not item.get("is_dir") else "disabled",
                             command=lambda: self.perform_action("diff", item["path"]))
            menu.add_command(label="Ajana sor", state="normal" if self.on_action else "disabled",
                             command=lambda: self.perform_action("ask", item["path"]))
            is_excluded = item["path"] in self.preferences.get("excluded_files", [])
            menu.add_command(label="AI bağlamına tekrar izin ver" if is_excluded else "AI bağlamından hariç tut",
                             command=lambda: self.perform_action("include" if is_excluded else "exclude", item["path"]))
            menu.add_command(label="Sabitle / Sabitlemeyi kaldır", command=lambda: self._pin(item["path"]))
        menu.add_separator()
        menu.add_command(label="Yeni dosya", command=lambda: self.create(False))
        menu.add_command(label="Yeni klasör", command=lambda: self.create(True))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _copy_path(self, path):
        self.clipboard_clear()
        self.clipboard_append(str(workspace_path(self.project.local_path, path)))

    def _pin(self, path):
        pinned = self.preferences.setdefault("pinned_files", [])
        if path in pinned:
            pinned.remove(path)
        else:
            pinned.append(path)
        self.on_preferences({"pinned_files": list(pinned)})
        self.refresh()

    def create(self, directory):
        relative = simpledialog.askstring("Yeni klasör" if directory else "Yeni dosya", "Proje içindeki göreli yol:", parent=self)
        if not relative:
            return
        try:
            path = workspace_path(self.project.local_path, relative)
            if is_sensitive_file(path, project_root=self.project.local_path):
                raise ValueError("Hassas dosyalar bu gezginden oluşturulamaz.")
            if directory:
                path.mkdir(parents=True, exist_ok=False)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8"):
                    pass
            self.refresh()
        except (OSError, ValueError) as error:
            messagebox.showerror("Dosya işlemi", str(error), parent=self)

    def filters(self):
        self.preferences["show_hidden"] = not self.preferences.get("show_hidden", False)
        ignored = simpledialog.askstring("Gezgin filtreleri", "Atlanacak klasörler (virgülle ayırın):", parent=self,
                                        initialvalue=",".join(self.preferences.get("ignored_dirs", ["node_modules", "build", "dist", ".venv", "__pycache__"])))
        if ignored is not None:
            self.preferences["ignored_dirs"] = [p.strip() for p in ignored.split(",") if p.strip()]
        self.on_preferences({"show_hidden": self.preferences["show_hidden"],
                             "ignored_dirs": self.preferences.get("ignored_dirs", [])})
        self.refresh()
