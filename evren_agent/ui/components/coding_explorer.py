"""Kademeli yüklenen ve indeksle ilişkili proje gezgini."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import customtkinter as ctk

from evren_agent.projects.explorer import ProjectExplorer
from evren_agent.projects.git_service import GitService
from evren_agent.projects.indexer import is_sensitive_file
from evren_agent.projects.workspace import workspace_path


class CodingExplorer(ctk.CTkFrame):
    def __init__(self, master, service, project_id, on_open, on_context, on_preferences):
        super().__init__(master, fg_color=("gray92", "#161f2e"))
        self.service, self.project_id = service, project_id
        self.project = service.projects.db.get_project(project_id)
        self.on_open, self.on_context, self.on_preferences = on_open, on_context, on_preferences
        self.preferences = service.projects.db.get_settings(project_id).coding_preferences
        self.nodes = {}
        self.generation = 0
        self.git_markers = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text="Proje gezgini", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.query = ctk.CTkEntry(self, placeholder_text="Dosya / içerik ara")
        self.query.grid(row=1, column=0, sticky="ew", padx=8)
        self.query.bind("<Return>", lambda event: self.search())
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        self.content = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(head, text="İçerik", variable=self.content, width=75).pack(side="left")
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
        self.tree.bind("<Return>", lambda event: self.open_selected())
        self.tree.bind("<Button-3>", self._menu)
        self.tree.bind("<Button-2>", self._menu)
        self.hint = ctk.CTkLabel(self, text="Yükleniyor…", wraplength=190, anchor="w")
        self.hint.grid(row=4, column=0, sticky="ew", padx=8)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(footer, text="Yenile", width=65, command=self.refresh).pack(side="left")
        ctk.CTkButton(footer, text="Filtreler", width=65, command=self.filters).pack(side="right")
        self.refresh()

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
                    node = self.tree.insert(parent, "end", text=path)
                    self.nodes[node] = {"path": path, "is_dir": False}
        def load():
            status = GitService.get_status(self.project.local_path)
            self.git_markers = {entry["path"]: (entry["index"] + entry["worktree"]).strip()
                                for entry in status.get("files", [])}
            return self._explorer().children(), status
        def show(result):
            items, status = result
            self._populate("", items)
            self.hint.configure(text=(f"Dal: {status.get('branch') or 'Git deposu yok'}\n" +
                                       f"{len(items)} öğe · {len(self.git_markers)} Git değişikliği"))
        self._background(load, show)

    def _populate(self, parent, items):
        for item in items:
            symbol = "▸ " if item["is_dir"] else "· "
            marker = item.get("git_status", "")
            node = self.tree.insert(parent, "end", text=f"{symbol}{item['name']} {marker}")
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
        content = self.content.get()
        def show(items):
            self.nodes.clear()
            self.tree.delete(*self.tree.get_children())
            for item in items:
                path = item.get("path", item.get("file_path", ""))
                line = item.get("line", 1)
                node = self.tree.insert("", "end", text=f"{path}:{line}")
                self.nodes[node] = dict(item, path=path, is_dir=False)
            self.hint.configure(text=f"{len(items)} sonuç (en fazla 50)")
        self._background(lambda: self._explorer().search(query, content=content, limit=50), show)

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
        self.on_preferences(self.preferences)
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
            menu.add_command(label="Editörde aç", command=lambda: self.on_open(item["path"], True))
            menu.add_command(label="Yolu kopyala", command=lambda: self._copy_path(item["path"]))
            menu.add_command(label="Bağlama ekle", command=lambda: self.on_context(item["path"]))
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
        self.on_preferences(self.preferences)
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
        self.on_preferences(self.preferences)
        self.refresh()
