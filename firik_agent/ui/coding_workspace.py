"""Kodlama Ajanı çalışma alanı; görünüm ile yürütücü ayrı sorumluluklardır."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import customtkinter as ctk
from firik_agent.ui.components.effort_selector import EffortSelector

from firik_agent.projects.workspace import workspace_path
from firik_agent.projects.indexer import is_sensitive_file, sanitize_secrets
from firik_agent.ui.components.coding_conversation import CodingConversation, CodingInput
from firik_agent.ui.components.coding_explorer import CodingExplorer
from firik_agent.ui.coding_strings import MODES, MODE_NAMES, LEGACY_MODES, STATUS_NAMES, PERMISSION_NAMES, POLICY_NAMES, POLICY_VALUES, WELCOME, WORKSPACE_VIEWS
from firik_agent.projects.cockpit import CockpitState
from firik_agent.projects.composer import parse_composer
from firik_agent.projects.git_service import GitService
from firik_agent.ui.editor import EDITORS
from firik_agent.ui.file_drop import register_file_drop
from firik_agent.ui.coding_history import CodingHistoryMixin, restore_state_history
from firik_agent.ui.coding_evidence import update_accounting_state
from firik_agent.ui.views.git_view import GitView


class CodingWorkspaceTabs(ctk.CTkFrame):
    """Keyboard-addressable views whose navigation wraps in narrow panes."""

    def __init__(self, master, *, on_change=None, numbered=False, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_change, self.numbered = on_change, numbered
        self._tab_dict, self.buttons = {}, {}
        self._current = ""
        self._columns = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.navigation = ctk.CTkFrame(self, fg_color="transparent")
        self.navigation.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 4))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_propagate(False)
        self.navigation.bind("<Configure>", self._layout, add="+")

    def add(self, name):
        tab = ctk.CTkFrame(self.body, fg_color=("gray97", "#101a2b"))
        self._tab_dict[name] = tab
        index = len(self.buttons) + 1
        label = f"{index} {name}" if self.numbered else name
        self.buttons[name] = ctk.CTkButton(self.navigation, text=label, width=90, height=28,
            corner_radius=5, anchor="w", font=ctk.CTkFont(size=11),
            command=lambda value=name: self.set(value))
        self._columns = 0
        self._layout()
        if not self._current:
            self.set(name, notify=False)
        return tab

    def tab(self, name):
        return self._tab_dict[self._canonical(name)]

    @staticmethod
    def _canonical(name):
        return "Etkinlik" if name == "İşlemler" else name

    def set(self, name, *, notify=True):
        name = self._canonical(name)
        if name not in self._tab_dict:
            return
        self._current = name
        for value, tab in self._tab_dict.items():
            if value == name:
                tab.grid(row=0, column=0, sticky="nsew")
            else:
                tab.grid_remove()
            self.buttons[value].configure(fg_color=("#d5eeed", "#164955") if value == name else "transparent",
                text_color=("#123b43", "#e6fffc") if value == name else ("gray25", "#cbd5e1"),
                border_width=1 if value == name else 0)
        if notify and self.on_change:
            self.on_change(name)

    def get(self):
        return self._current

    def _layout(self, event=None):
        width = event.width if event else max(300, self.navigation.winfo_width())
        columns = max(1, min(len(self.buttons), width // 102))
        if columns == self._columns:
            return
        for column in range(max(self._columns, columns)):
            self.navigation.grid_columnconfigure(column, weight=1 if column < columns else 0)
        self._columns = columns
        for index, button in enumerate(self.buttons.values()):
            button.grid(row=index // columns, column=index % columns, sticky="ew", padx=2, pady=2)


class CodingWorkspaceMixin(CodingHistoryMixin):
    def _coding_state(self):
        project_id = self.current_project_id
        if project_id not in self._coding_states:
            session, record = self.service.restore_coding(project_id)
            prefs = self.project_service.db.get_settings(project_id).coding_preferences
            messages = [dict(role=m.role, content=str(m.content or "")) for m in session.messages
                        if m.role in {"user", "assistant"} and m.content]
            if not messages:
                messages = [{"role": "system", "content": WELCOME}]
            state = {
                "session": session, "busy": False, "messages": messages,
                "text": "\n\n".join(m["content"] for m in messages),
                "status": STATUS_NAMES.get(record.status, "Hazır") if record else "Hazır",
                "activities": [], "changes": [], "before": None, "snapshot": None,
                "inspection_warning": "", "run_error": "", "selected_change": None, "run_id": 0,
                "mode": prefs.get("mode", "approved"), "preferences": prefs,
                "policy": dict(prefs.get("policy", {})),
                "permissions": {"allow_reads": True, "allow_deletes": True, "allow_edits": True,
                                "allow_terminal": True, "allow_tests": True, "allow_mcp": False,
                                "allow_subagents": True,
                                **prefs.get("permissions", {})},
                "context_paths": prefs.get("context_paths", []), "documents": [], "plan": [],
                "context_report": [], "capabilities": {}, "usage": {}, "task_id": None,
                "last_prompt": "", "paused": False, "pending_approvals": {},
                "record": record, "stopped": False, "future": None, "agent_paths": {},
                "cockpit": CockpitState(),
            }
            if record:
                restore_state_history(state, record, self.service.coding_store.list_events(
                    record.id, project_id=project_id, limit=1000, include_deltas=False))
                if record.status == "interrupted":
                    state["inspection_warning"] = "Önceki çalışma kesildi. Devam etmeden önce proje ve dosyalar yeniden incelenecek."
            self._coding_states[project_id] = state
        return self._coding_states[project_id]

    def _save_coding_preferences(self, values):
        state = self._coding_state()
        state["preferences"].update(values)
        settings = self.project_service.db.get_settings(self.current_project_id)
        settings.coding_preferences = dict(state["preferences"])
        self.project_service.db.save_settings(settings)

    def _render_tab_coding(self, container):
        state = self._coding_state()
        project_id = self.current_project_id
        project = self.project_service.db.get_project(project_id)
        prefs = state["preferences"]
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)
        panes = tk.PanedWindow(container, orient="horizontal", sashwidth=7, borderwidth=0,
                               background="#26354b" if ctk.get_appearance_mode() == "Dark" else "#d8e2ed")
        panes.grid(row=0, column=0, sticky="nsew")
        self.coding_panes = panes
        left = ctk.CTkFrame(panes, fg_color=("gray92", "#161f2e"))
        mid = ctk.CTkFrame(panes, fg_color=("gray92", "#161f2e"))
        right = ctk.CTkFrame(panes, fg_color=("gray92", "#161f2e"))
        self.coding_left, self.coding_mid, self.coding_right = left, mid, right
        panes.add(left, minsize=180, width=prefs.get("left_width", 220), stretch="never")
        panes.add(mid, minsize=330, stretch="always")
        panes.add(right, minsize=250, width=prefs.get("right_width", 300), stretch="never")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="PROJE / BAĞLAM", anchor="w", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=0, sticky="ew", padx=10, pady=6)
        self.coding_explorer = CodingExplorer(left, self.service, project_id,
            on_open=lambda path, editor: self._open_coding_editor(path) if editor else self._open_coding_reference(path),
            on_context=self._add_coding_path, on_preferences=self._save_coding_preferences, on_action=self._coding_explorer_action)
        self.coding_explorer.grid(row=1, column=0, sticky="nsew")
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(mid, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=5)
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="ew")
        title.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title, text=f"Ajan Çalışma Alanı · {project.name}", anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="ew")
        self.coding_mode_combo = ctk.CTkComboBox(title, values=list(MODES), width=115,
                                               state="readonly", command=self._coding_mode_selected)
        self.coding_mode_combo.set(MODE_NAMES.get(state["mode"], "Güvenli"))
        self.coding_mode_combo.grid(row=0, column=1, padx=(4, 0))
        models = ctk.CTkFrame(head, fg_color="transparent")
        models.grid(row=1, column=0, sticky="ew", pady=5)
        models.grid_columnconfigure(0, weight=1)
        models.grid_columnconfigure(1, weight=1)
        selected_model = self.project_service.db.get_settings(project_id).coding_model or self.service.default_model
        self.coding_model_combo = ctk.CTkComboBox(models, values=list(dict.fromkeys([selected_model, *self.service._cached_models])),
            width=120, state="readonly", command=self._on_coding_model_selected)
        self.coding_model_combo.set(selected_model)
        self.coding_model_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.coding_editor_combo = ctk.CTkComboBox(models, values=EDITORS, width=100, state="readonly", command=self._on_coding_editor_selected)
        self.coding_editor_combo.set(self.service.config.get("coding_editor", {}).get("name", "VS Code"))
        self.coding_editor_combo.grid(row=0, column=1, sticky="ew", padx=4)
        self.coding_open_project_btn = ctk.CTkButton(models, text="IDE'de Aç", width=75, fg_color="transparent", border_width=1, command=self._open_coding_editor)
        self.coding_open_project_btn.grid(row=0, column=2)
        toolbar = ctk.CTkFrame(head, fg_color="transparent")
        toolbar.grid(row=2, column=0, sticky="ew")
        for label, callback in (("Proje", lambda: self._toggle_coding_pane("left")),
                                ("İnceleyici", lambda: self._toggle_coding_pane("right")),
                                ("Bağlam +", self._attach_coding_files), ("Komutlar", self._coding_palette)):
            ctk.CTkButton(toolbar, text=label, width=62, height=25, fg_color="transparent", border_width=1,
                          command=callback).pack(side="left", padx=2)
        self.coding_status_label = ctk.CTkLabel(head, text=state["status"], height=22, anchor="w",
                                               wraplength=500, font=ctk.CTkFont(size=11))
        self.coding_status_label.grid(row=3, column=0, sticky="ew")
        self.coding_pending_btn = ctk.CTkButton(head, text="⚠ Onay Bekleniyor · İşlemi İncele", height=27,
                                               fg_color=("#b45309", "#92400e"), command=self._inspect_coding_approvals)
        self.coding_tabs = CodingWorkspaceTabs(mid, numbered=True, on_change=self._coding_view_changed)
        self.coding_tabs.grid(row=1, column=0, sticky="nsew", padx=4)
        for name in WORKSPACE_VIEWS:
            tab = self.coding_tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.coding_chat_box = CodingConversation(self.coding_tabs.tab("Sohbet"), on_edit=self._edit_coding_message,
            on_context=self._add_coding_snippet, on_file=self._open_coding_reference,
            font_size=prefs.get("font_size", 13))
        self.coding_chat_box.grid(row=0, column=0, sticky="nsew")
        self.coding_chat_box.set_messages(state["messages"], state["text"])
        changes = self.coding_tabs.tab("Değişiklikler")
        changes.grid_rowconfigure(0, weight=0)
        changes.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(changes, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.coding_changes_summary = ctk.CTkLabel(header, text="", anchor="w", wraplength=340, font=ctk.CTkFont(size=11))
        self.coding_changes_summary.grid(row=0, column=0, sticky="ew")
        self.coding_refresh_changes_btn = ctk.CTkButton(header, text="Yenile", width=60, command=self._refresh_coding_changes_async)
        self.coding_refresh_changes_btn.grid(row=0, column=1)
        self.coding_changes_list = ctk.CTkScrollableFrame(changes, height=28)
        # The native scrollbar otherwise contributes its default 200px height
        # even when the requested viewport is small or has no file rows.
        self.coding_changes_list._scrollbar.configure(height=28)
        self.coding_changes_list.grid(row=1, column=0, sticky="ew")
        self.coding_diff_box = ctk.CTkTextbox(changes, wrap="none", height=100, font=ctk.CTkFont(family="monospace", size=12))
        self.coding_diff_box.grid(row=2, column=0, sticky="nsew")
        for tag, color in (("added", "#10b981"), ("removed", "#f87171"), ("header", "#38bdf8")):
            self.coding_diff_box.tag_config(tag, foreground=color)
        footer = ctk.CTkFrame(changes, fg_color="transparent")
        self.coding_change_footer = footer
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.coding_open_file_btn = ctk.CTkButton(footer, text="IDE'de Aç", width=80, command=self._open_selected_coding_file)
        self.coding_open_file_btn.grid(row=0, column=0, sticky="w", padx=2, pady=2)
        ctk.CTkButton(footer, text="Yan Yana", width=80, command=self._coding_side_diff).grid(row=0, column=1, padx=2, pady=2)
        ctk.CTkButton(footer, text="Ajana Sor", width=80, command=self._ask_coding_change).grid(row=0, column=2, padx=2, pady=2)
        self.coding_undo_btn = ctk.CTkButton(footer, text="Ajan Düzenlemesini Geri Al", width=160, fg_color="transparent", border_width=1,
                                            command=self._undo_coding_change)
        self.coding_undo_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        footer.bind("<Configure>", self._layout_coding_change_actions, add="+")
        self.coding_activity_list = ctk.CTkScrollableFrame(self.coding_tabs.tab("Etkinlik"))
        self.coding_activity_list.grid(row=0, column=0, sticky="nsew")
        self.coding_files_list = ctk.CTkScrollableFrame(self.coding_tabs.tab("Dosyalar"))
        self.coding_files_list.grid(row=0, column=0, sticky="nsew")
        self.coding_plan_box = ctk.CTkTextbox(self.coding_tabs.tab("Plan"), wrap="word", font=ctk.CTkFont(size=13))
        self.coding_plan_box.grid(row=0, column=0, sticky="nsew")
        ctk.CTkButton(self.coding_tabs.tab("Plan"), text="Planı Onayla ve Uygula", command=self._approve_coding_plan).grid(row=1, column=0, pady=4)
        self.coding_embedded_git = None
        self.coding_git_host = self.coding_tabs.tab("Git")
        input_area = ctk.CTkFrame(mid, fg_color="transparent")
        input_area.grid(row=2, column=0, sticky="ew", padx=8, pady=3)
        input_area.grid_columnconfigure(0, weight=1)
        self.coding_input = CodingInput(input_area, height=52, wrap="word", font=ctk.CTkFont(size=prefs.get("font_size", 13)))
        self.coding_input.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.coding_drop_available = register_file_drop(self.coding_input._textbox, self._drop_coding_files)
        for key in ("<Return>", "<KP_Enter>"):
            self.coding_input.bind(key, self._coding_composer_return, add="+")
        def newline(event):
            self.coding_input.insert("insert", "\n")
            return "break"
        self.coding_input.bind("<Shift-Return>", newline, add="+")
        self.coding_send_btn = ctk.CTkButton(input_area, text="Çalıştır", width=75, command=self._send_coding_prompt)
        self.coding_send_btn.grid(row=0, column=1)
        primary = "Cmd" if self.tk.call("tk", "windowingsystem") == "aqua" else "Ctrl"
        self._mount_coding_composer(input_area, project_id)
        self.coding_composer_hint = ctk.CTkLabel(input_area, text=f"@ bağlam · / komut · Enter: çalıştır · Shift+Enter: satır · {primary}+K: palet", height=18,
                     anchor="w", font=ctk.CTkFont(size=10))
        self.coding_composer_hint.grid(row=2, column=0, columnspan=2, sticky="ew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        inspector_head = ctk.CTkFrame(right, fg_color="transparent")
        inspector_head.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        ctk.CTkLabel(inspector_head, text="AJAN / GÖREV İNCELEYİCİSİ", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x")
        lifecycle = ctk.CTkFrame(inspector_head, fg_color="transparent")
        lifecycle.pack(fill="x", pady=(5, 0))
        for index, (text, action) in enumerate((("Ⅱ Duraklat", "pause"), ("▶ Devam", "resume"), ("■ Durdur", "cancel"))):
            lifecycle.grid_columnconfigure(index, weight=1)
            ctk.CTkButton(lifecycle, text=text, width=65, height=27, font=ctk.CTkFont(size=11),
                fg_color=("#b91c1c", "#991b1b") if action == "cancel" else "transparent",
                border_width=0 if action == "cancel" else 1,
                command=lambda value=action: self._control_coding(value)).grid(row=0, column=index, sticky="ew", padx=2)
        self.coding_inspector_host = ctk.CTkFrame(right, fg_color="transparent")
        self.coding_inspector_host.grid(row=1, column=0, sticky="nsew", padx=4)
        self.coding_inspector_host.grid_columnconfigure(0, weight=1)
        self.coding_inspector_host.grid_rowconfigure(0, weight=1)
        self.coding_inspector_tabs = CodingWorkspaceTabs(self.coding_inspector_host, on_change=self._coding_inspector_changed)
        self.coding_inspector_tabs.grid(row=0, column=0, sticky="nsew")
        task_tab = self.coding_inspector_tabs.add("Görev")
        task_tab.grid_columnconfigure(0, weight=1)
        task_tab.grid_rowconfigure(0, weight=1)
        controls = ctk.CTkScrollableFrame(task_tab)
        controls.grid(row=0, column=0, sticky="nsew")
        from firik_agent.projects.harness_profiles import PROFILES, resolve_harness_profile
        ctk.CTkLabel(controls, text="Çalışma profili", anchor="w").pack(fill="x", pady=(4, 0))
        configured_profile = resolve_harness_profile(prefs, user_config=getattr(self.service, "config", {}).get("agent", {}))
        self.coding_profile_var = tk.StringVar(value=configured_profile["name"])
        self.coding_profile_selector = ctk.CTkOptionMenu(controls, values=list(PROFILES),
            variable=self.coding_profile_var,
            command=lambda value: self._save_coding_preferences({"harness_profile": value}))
        self.coding_profile_selector.pack(fill="x", pady=4)
        self.coding_subagents_var = tk.BooleanVar(value=prefs.get("subagents_enabled", False))
        ctk.CTkCheckBox(controls, text="Araştırma / inceleme\nalt ajanlarını kullan", variable=self.coding_subagents_var,
            command=lambda: self._save_coding_preferences({"subagents_enabled": self.coding_subagents_var.get()}), height=36).pack(fill="x", pady=4)
        self.coding_task_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=235)
        self.coding_task_label.pack(fill="x", pady=6)
        self.coding_context_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=235)
        self.coding_context_label.pack(fill="x", pady=6)
        self.coding_usage_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=235)
        self.coding_usage_label.pack(fill="x", pady=6)
        self.coding_effort_selector = EffortSelector(controls, self.service, self._on_coding_effort_selected)
        self.coding_effort_selector.pack(fill="x", pady=5)
        settings = self.project_service.db.get_settings(self.current_project_id)
        state["session"].reasoning_effort = settings.coding_preferences.get("reasoning_effort")
        self._refresh_coding_effort()
        self._load_coding_models()
        for text, callback in (("Planı İncele", lambda: self.coding_tabs.set("Plan")),
                               ("Değişiklikleri İncele", lambda: self.coding_tabs.set("Değişiklikler")),
                               ("Yürütme / Sandbox Durumu", self._coding_execution_status),
                               ("Harness / Benchmark", self._coding_harness_settings),
                               ("İzinler / MCP / Beceriler", self._coding_permissions)):
            ctk.CTkButton(controls, text=text, fg_color="transparent", border_width=1, command=callback).pack(fill="x", pady=3)
        self.coding_approval_frame = ctk.CTkFrame(controls, fg_color="transparent")
        self.coding_approval_frame.pack(fill="x", pady=6)
        self._mount_coding_evidence()
        self.coding_inspector_tabs.set(prefs.get("inspector_view", "Ajanlar"), notify=False)
        self.coding_tabs.set(prefs.get("workspace_view", "Etkinlik"))
        self._refresh_coding_changes()
        self._refresh_coding_activity()
        self._refresh_coding_files_async()
        self._refresh_coding_control()
        self._refresh_coding_display()
        self._refresh_coding_evidence()
        def layout(event=None):
            if (not panes.winfo_exists() or self.coding_panes is not panes or not mid.winfo_exists()
                    or not self.coding_status_label.winfo_exists()):
                return
            compact = container.winfo_width() < 1140
            if container.winfo_height() < 500:
                self.coding_composer_hint.grid_remove()
            else:
                self.coding_composer_hint.grid()
            self.coding_status_label.configure(wraplength=max(250, mid.winfo_width() - 20))
            for side, widget, minimum, default in (("left", left, 180, 220), ("right", right, 250, 300)):
                shown = str(widget) in tuple(map(str, panes.panes()))
                desired = prefs.get(f"show_{side}", True) and (not compact or state.get("manual_side") == side)
                if desired and not shown:
                    options = dict(minsize=minimum, width=prefs.get(f"{side}_width", default), stretch="never")
                    if side == "left":
                        options["before"] = mid
                    panes.add(widget, **options)
                elif shown and not desired:
                    panes.forget(widget)
        container.bind("<Configure>", layout, add="+")
        container.after_idle(layout)
        def save_sizes(event):
            if self.current_project_id != project_id:
                return
            visible = tuple(map(str, panes.panes()))
            sizes = {f"{side}_width": max(minimum, widget.winfo_width())
                     for side, widget, minimum in (("left", left, 180), ("right", right, 250)) if str(widget) in visible}
            if sizes:
                self._save_coding_preferences(sizes)
        panes.bind("<ButtonRelease-1>", save_sizes)
        self._bind_coding_shortcuts(panes, project_id)

    def _layout_coding_change_actions(self, event):
        if event.widget not in (self.coding_change_footer, self.coding_change_footer._canvas):
            return
        wide = event.width >= 550
        self.coding_undo_btn.grid(row=0 if wide else 1, column=3 if wide else 0,
                                  columnspan=1 if wide else 2, sticky="ew", padx=2, pady=2)
        if hasattr(self, "coding_review_checkpoint_btn"):
            self.coding_review_checkpoint_btn.grid(row=0 if wide else 1, column=4 if wide else 2,
                                                   sticky="ew", padx=2, pady=2)

    def _coding_view_changed(self, name):
        self._save_coding_preferences({"workspace_view": name})
        if name == "Git" and self.coding_embedded_git is None:
            project = self.project_service.db.get_project(self.current_project_id)
            self.coding_embedded_git = GitView(self.coding_git_host, self.service, project.local_path,
                on_ask_agent=self._coding_quick_action, on_open_file=self._open_coding_reference)
            self.coding_embedded_git.grid(row=0, column=0, sticky="nsew")
        if hasattr(self, "coding_activity_list"):
            self._refresh_coding_evidence()

    def _coding_inspector_changed(self, name):
        self._save_coding_preferences({"inspector_view": name})

    def _coding_quick_action(self, prompt):
        self.coding_input.delete(0, "end")
        self.coding_input.insert(0, prompt)
        self.coding_input.focus_set()

    def _coding_explorer_action(self, action, path):
        state = self._coding_state()
        if action in {"add_context", "remove_context", "exclude", "include"}:
            state["context_paths"] = list(state["preferences"].get("context_paths", []))
            self._refresh_coding_control()
        elif action == "ask":
            quoted = json.dumps(path, ensure_ascii=False) if any(char.isspace() for char in path) else path
            self._coding_quick_action(f"@file:{quoted} Bu dosyanın görevini ve ilgili bağımlılıklarını açıkla.")
        elif action == "diff":
            self.coding_tabs.set("Değişiklikler")
            if any(change["path"] == path for change in state["changes"]):
                self._show_coding_change(path)
                return
            state["selected_change"] = path
            project_id, generation = self.current_project_id, state["run_id"]
            project = self.project_service.db.get_project(project_id)
            async def load():
                return await asyncio.to_thread(GitService.get_diff, project.local_path, file_path=path)
            def ready(diff):
                if not self._coding_visible(project_id) or state["run_id"] != generation or state["selected_change"] != path:
                    return
                lines = [line for line in diff.splitlines() if not line.startswith(("+++", "---"))]
                change = {"path": path, "status": "modified", "diff": diff or "Bu dosyada kaydedilmiş ajan veya Git farkı yok.",
                          "origin": "Git çalışma ağacı", "added": sum(line.startswith("+") for line in lines),
                          "removed": sum(line.startswith("-") for line in lines), "can_undo": False}
                state["changes"].append(change)
                self._refresh_coding_changes()
            self.service.runtime.submit(load(), on_done=lambda diff: self.service._dispatch(ready, diff),
                on_error=lambda error: self.service._dispatch(ready, str(error)))

    def _mount_coding_composer(self, parent, project_id):
        owner = self.coding_input
        dark = ctk.get_appearance_mode() == "Dark"
        self.coding_suggestion_list = tk.Listbox(parent, height=3, exportselection=False,
            background="#101a2b" if dark else "#f8fafc", foreground="#e6eef8" if dark else "#172b43",
            selectbackground="#164955" if dark else "#c8eee9", selectforeground="#e6fffc" if dark else "#123b43",
            font=("Arial", 11), borderwidth=0, highlightthickness=1, activestyle="dotbox")
        self.coding_suggestion_list.grid(row=1, column=0, columnspan=2, sticky="ew", pady=3)
        self.coding_suggestion_list.grid_remove()
        self._coding_suggestions = []
        owner._suggestion_job = None
        owner._suggestion_generation = 0
        def changed(event=None):
            if owner._suggestion_job is not None:
                owner.after_cancel(owner._suggestion_job)
            owner._suggestion_job = owner.after(160, lambda: self._load_coding_composer_suggestions(owner, project_id))
        owner.bind("<KeyRelease>", changed, add="+")
        owner.bind("<Tab>", self._accept_coding_suggestion, add="+")
        def select(event):
            if not self._coding_suggestions:
                return None
            self.coding_suggestion_list.focus_set()
            self.coding_suggestion_list.selection_set(0)
            self.coding_suggestion_list.activate(0)
            return "break"
        owner.bind("<Down>", select, add="+")
        self.coding_suggestion_list.bind("<Return>", self._accept_coding_suggestion)
        self.coding_suggestion_list.bind("<Tab>", self._accept_coding_suggestion)
        self.coding_suggestion_list.bind("<Double-Button-1>", self._accept_coding_suggestion)
        def hide(event=None):
            if self._coding_suggestions:
                self._coding_suggestions = []
                self.coding_suggestion_list.grid_remove()
                owner.focus_set()
                return "break"
            return None
        owner.bind("<Escape>", hide, add="+")
        self.coding_suggestion_list.bind("<Escape>", hide)
        def destroyed(event):
            if event.widget is owner and owner._suggestion_job is not None:
                try:
                    owner.after_cancel(owner._suggestion_job)
                except tk.TclError:
                    pass
                owner._suggestion_job = None
            if event.widget is owner:
                owner._suggestion_generation += 1
        tk.Misc.bind(owner, "<Destroy>", destroyed, add="+")
        modifier = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        owner.bind(f"<{modifier}-space>", lambda event: (self._load_coding_composer_suggestions(owner, project_id), "break")[1], add="+")

    def _coding_composer_fragment(self, owner):
        prefix = owner.get("1.0", "insert")
        match = re.search(r'(?<!\w)(@(?:file|folder):"[^"\n]*|@[^\s]*|/[a-z]*)$', prefix)
        return match.group(1) if match else ""

    def _load_coding_composer_suggestions(self, owner, project_id):
        owner._suggestion_job = None
        owner._suggestion_generation += 1
        generation = owner._suggestion_generation
        if not owner.winfo_exists() or self.coding_input is not owner:
            return
        fragment = self._coding_composer_fragment(owner)
        if not fragment:
            self._coding_suggestions = []
            self.coding_suggestion_list.grid_remove()
            return
        state = self._coding_state()
        context = {"agents": list(state["cockpit"].agents.values()), "terminals": state["cockpit"].terminal}
        skills = state.get("skill_catalogue", [])
        mcp = [dict(name=tool.name) for tool in self.service.mcp_manager.tool_registry.list_tools()]
        def ready(values):
            if (not self._coding_visible(project_id) or self.coding_input is not owner
                    or owner._suggestion_generation != generation or self._coding_composer_fragment(owner) != fragment):
                return
            self._coding_suggestions = values
            self.coding_suggestion_list.delete(0, "end")
            for item in values:
                self.coding_suggestion_list.insert("end", item["label"])
            if values:
                self.coding_suggestion_list.grid()
                self.coding_suggestion_list.selection_set(0)
                self.coding_suggestion_list.activate(0)
            else:
                self.coding_suggestion_list.grid_remove()
        self.service.suggest_coding_composer_async(project_id, fragment, on_success=ready, on_error=lambda error: ready([]),
            composer_context=context, available_skills=skills, mcp_tools=mcp, limit=12)

    def _accept_coding_suggestion(self, event=None):
        if not self._coding_suggestions:
            return None
        index = (self.coding_suggestion_list.curselection() or (0,))[0]
        fragment = self._coding_composer_fragment(self.coding_input)
        if not fragment:
            return None
        self.coding_input.delete(f"insert-{len(fragment)}c", "insert")
        self.coding_input.insert("insert", self._coding_suggestions[index]["insert"])
        self._coding_suggestions = []
        self.coding_suggestion_list.grid_remove()
        self.coding_input.focus_set()
        return "break"

    def _coding_composer_return(self, event=None):
        return self._accept_coding_suggestion(event) or self._handle_coding_return(event)

    def _ask_coding_change(self):
        path = self._coding_state().get("selected_change")
        if path:
            self._coding_quick_action(f"{path} dosyasındaki değişiklikleri gerekçeleri ve doğrulama sonuçlarıyla açıkla.")

    def _bind_coding_shortcuts(self, owner, project_id):
        """Bind to descendant focus, then remove only this view's bindings."""
        target = self.winfo_toplevel()
        modifier = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        self.coding_shortcut_modifier = modifier
        bindings = []
        actions = [(f"<{modifier}-Key-{index}>", lambda value=name: self.coding_tabs.set(value))
                   for index, name in enumerate(WORKSPACE_VIEWS, 1)]
        actions.extend(((f"<{modifier}-k>", self._coding_palette),
                        (f"<{modifier}-f>", self._search_coding_chat),
                        (f"<{modifier}-Return>", self._send_coding_prompt),
                        (f"<{modifier}-period>", lambda: self._control_coding("cancel")),
                        (f"<{modifier}-Shift-A>", self._coding_create_agent)))
        for sequence, callback in actions:
            def invoke(event, fn=callback):
                if not self._coding_visible(project_id) or self.coding_panes is not owner:
                    return None
                # Dialog bindings belong to their own window, not this workspace.
                if event.widget.winfo_toplevel() is not target:
                    return None
                fn()
                return "break"
            binding = target.bind(sequence, invoke, add="+")
            bindings.append((sequence, binding))
        def release(event):
            if event.widget is not owner:
                return
            for sequence, binding in bindings:
                try:
                    target.unbind(sequence, binding)
                except tk.TclError:
                    pass
        owner.bind("<Destroy>", release, add="+")
        self.coding_input.bind("<Escape>", lambda event: (self._control_coding("cancel"), "break")[1], add="+")

    def _refresh_coding_effort(self):
        state = self._coding_state()
        state["session"].reasoning_effort = self.coding_effort_selector.refresh(
            self.coding_model_combo.get(), state["session"].reasoning_effort, state["busy"])

    def _on_coding_effort_selected(self, effort):
        state = self._coding_state()
        if state["busy"]:
            return
        self._save_coding_preferences({"reasoning_effort": effort})
        state["session"].reasoning_effort = effort

    def _refresh_coding_display(self, streaming=False):
        state = self._coding_state()
        self.coding_chat_box.set_messages(state["messages"], state["text"], streaming=streaming)
        self.coding_send_btn.configure(state="disabled" if state["busy"] else "normal", text="Çalışıyor…" if state["busy"] else "Çalıştır")
        self._refresh_coding_effort()
        self.coding_model_combo.configure(state="disabled" if state["busy"] else "readonly")
        self.coding_mode_combo.configure(state="disabled" if state["busy"] else "readonly")
        self.coding_status_label.configure(text=state["status"])

    def _refresh_coding_control(self):
        state = self._coding_state()
        if hasattr(self, "coding_explorer") and self.coding_explorer.winfo_exists():
            self.coding_explorer.mark_paths(context_paths=state["context_paths"], touched_paths=list(state["agent_paths"]))
        task = self.project_service.db.get_task(state["task_id"]) if state["task_id"] else None
        title = task.title if task else state["last_prompt"][:160] or "Görev seçilmedi"
        details = [f"Aktif görev\n{title}", state["status"]]
        if state.get("run_error"):
            details.append("Hata: " + state["run_error"])
        if state["inspection_warning"]:
            details.append("Dosya karşılaştırması: " + state["inspection_warning"])
        self.coding_task_label.configure(text="\n".join(details))
        active = [item for item in state["context_report"] if item.get("has_content") and item.get("tokens_used", 0) > 0]
        report = "\n".join(f"• {item.get('display_name', item.get('name', item.get('key')))}: ~{item.get('tokens_used', 0)} token" for item in active)
        self.coding_context_label.configure(text="Etkin bağlam\n" + report + "\n" + "\n".join(state["context_paths"][:12]))
        usage = state["usage"]
        total = usage.get("total_tokens")
        window = state["capabilities"].get("context_window")
        used = sum(item.get("tokens_used", 0) for item in active)
        fill = f"~%{used / window * 100:.1f} (tahmini)" if window else "Kapasite bilinmiyor"
        cost = usage.get("cost")
        self.coding_usage_label.configure(text=f"Model: {state['session'].model}\nÇalışma token toplamı: {total if total is not None else 'Sağlayıcı bildirmedi'}\nProje bağlamı: {fill}\nGeçmiş ve araç şemaları bu yerel tahmine dahil değil.\nMaliyet: {cost if cost is not None else 'Sağlayıcı bildirmedi'}")
        plan = state["plan"]
        self.coding_plan_box.configure(state="normal")
        self.coding_plan_box.delete("1.0", "end")
        status_labels = {"pending": "Bekliyor", "in_progress": "Çalışıyor", "completed": "Doğrulandı", "blocked": "Engellendi"}
        lines = []
        for number, step in enumerate(plan, 1):
            if not isinstance(step, dict):
                continue
            lines.append(f"{number}. {step.get('title', '')} · {status_labels.get(step.get('status'), 'Bekliyor')}")
            lines += [f"   Kabul kriteri: {criterion}" for criterion in step.get("acceptance_criteria", [])]
            if step.get("verification"):
                lines.append(f"   Doğrulama: {step['verification']}")
            if step.get("evidence_ids"):
                lines.append("   Kanıt: " + ", ".join(step["evidence_ids"]))
            lines.append("")
        self.coding_plan_box.insert("end", "\n".join(lines) if lines else "Henüz yapılandırılmış plan oluşturulmadı.")
        self.coding_plan_box.configure(state="disabled")
        for widget in self.coding_approval_frame.winfo_children():
            widget.destroy()
        if state["pending_approvals"]:
            self.coding_pending_btn.grid(row=4, column=0, sticky="ew", pady=2)
        else:
            self.coding_pending_btn.grid_remove()
        for request_id, request in state["pending_approvals"].items():
            ctk.CTkLabel(self.coding_approval_frame, text=f"Onay gerekiyor\n{request.get('tool_name', request.get('tool', 'İşlem'))}\n{json.dumps(request.get('arguments', {}), ensure_ascii=False)[:1200]}", wraplength=190, justify="left").pack(fill="x")
            ctk.CTkButton(self.coding_approval_frame, text="Tüm İşlemi İncele",
                          command=lambda rid=request_id: self._inspect_coding_approvals(rid)).pack(fill="x", pady=2)

    def _inspect_coding_approvals(self, request_id=None):
        state = self._coding_state()
        if not state["pending_approvals"]:
            return
        request_id = request_id or next(iter(state["pending_approvals"]))
        request = state["pending_approvals"][request_id]
        session_id = state["session"].session_id
        project_id = self.current_project_id
        dialog = ctk.CTkToplevel(self)
        dialog.title("İşlem Onayı")
        dialog.geometry("780x580")
        ctk.CTkLabel(dialog, text=f"{request.get('tool_name', 'İşlem')}\n{request.get('reason', '')}", wraplength=730).pack(fill="x", padx=12, pady=10)
        box = ctk.CTkTextbox(dialog, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
        box.pack(fill="both", expand=True, padx=12, pady=6)
        box.insert("end", json.dumps(request.get("arguments", {}), ensure_ascii=False, indent=2))
        box.configure(state="disabled")
        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=10)
        def decide(allowed):
            # The reviewed request stays bound to its original project/session.
            self.service.coding_control(session_id, "approve", request_id, allowed)
            state["pending_approvals"].pop(request_id, None)
            dialog.destroy()
            if self._coding_visible(project_id):
                self._refresh_coding_control()
        ctk.CTkButton(footer, text="Bu İşleme İzin Ver", command=lambda: decide(True)).pack(side="right", padx=4)
        ctk.CTkButton(footer, text="Reddet", command=lambda: decide(False)).pack(side="right", padx=4)

    def _dispatch_coding_composer(self, composer):
        """Route explicit commands to their real evidence view before inference."""
        command = composer.get("command")
        view = {"plan": "Plan", "test": "Testler", "review": "Değişiklikler",
                "fix": "Etkinlik", "skill": "Etkinlik", "mcp": "Etkinlik"}.get(command)
        if view:
            self.coding_tabs.set(view)
        if command == "agent":
            # The explicit delegation command opts into the existing guarded
            # spawning tools; it does not change inherited permissions/budgets.
            self._save_coding_preferences({"subagents_enabled": True})
            self.coding_subagents_var.set(True)
            self._show_coding_inspector("Ajanlar")

    def _run_coding_prompt(self, prompt, prepared=None):
        project_id = self.current_project_id
        state = self._coding_state()
        if state["busy"]:
            return
        resume_source = state.pop("resume_source_run_id", None)
        resume_approved = state.pop("resume_approved", False)
        resume_prepared = state.pop("resume_prepared_run_id", None)
        composer = parse_composer(prompt)
        self._dispatch_coding_composer(composer)
        if composer.get("mode"):
            state["mode"] = composer["mode"]
            self.coding_mode_combo.set(MODE_NAMES[state["mode"]])
            self._save_coding_preferences({"mode": state["mode"]})
        state.update(busy=True, stopped=False, paused=False, last_prompt=prompt, before=None,
                     changes=[], activities=[], pending_approvals={}, inspection_warning="", run_error="", agent_paths={}, record=None,
                     usage={}, harness_metrics={}, usage_requests={}, model_iteration=0)
        state["run_id"] += 1
        generation = state["run_id"]
        state["messages"].append({"role": "user", "content": prompt})
        response = {"role": "assistant", "content": ""}
        state["messages"].append(response)
        state["text"] += f"\n[Siz]: {prompt}\n[firik]: "
        state["status"] = "Güncel proje bağlamı hazırlanıyor…"
        session = state["session"]
        session.model = self.project_service.db.get_settings(project_id).coding_model or self.service.default_model
        state.setdefault("cockpit", CockpitState()).start(model=session.model,
            provider=state["preferences"].get("provider", self.service.provider), task=prompt,
            run_id=str(state["run_id"]))
        state["cockpit"].plan = list(state["plan"])
        project = self.project_service.db.get_project(project_id)
        if prepared:
            state["task_id"] = prepared.get("task").id if prepared.get("task") else None
        ended = False

        def visible():
            return self._coding_visible(project_id) and state["run_id"] == generation
        def refresh(details=False):
            if visible():
                self._refresh_coding_display(streaming=state["busy"])
                if details:
                    self._refresh_coding_activity()
                    self._refresh_coding_changes()
                    self._refresh_coding_control()
                    self._refresh_coding_evidence()
        refresh(True)

        def finish(text="", error=None):
            nonlocal ended
            if ended:
                return
            ended = True
            if text and not response["content"]:
                response["content"] = text
                state["text"] += text
            if error:
                state["run_error"] = str(error)[:2000]
                response["content"] += f"\nİşlem hatası: {error}"
                state["text"] += f"\nİşlem hatası: {error}"
            state["text"] += "\n"
            def inspected(snapshot=None, inspection_error=None):
                if state["run_id"] != generation:
                    return
                if snapshot:
                    self._apply_coding_snapshot(state, snapshot)
                if inspection_error:
                    state["inspection_warning"] = str(inspection_error)
                state["busy"] = False
                state["pending_approvals"] = {}
                errors = sum(a["status"] == "Hata" for a in state["activities"])
                outcome = ("cancelled" if state["stopped"] else "failed" if error else "blocked"
                           if state["cockpit"].agents.get("orchestrator", {}).get("status") == "blocked" else "completed")
                state["status"] = ("Durduruldu" if outcome == "cancelled" else "Çalışma hata ile sona erdi" if outcome == "failed"
                    else "Engellendi · Tamamlanmamış işlem veya doğrulama var" if outcome == "blocked"
                    else f"Tamamlandı · {len(state['changes'])} dosya değişti" + (f" · {errors} araç hatası" if errors else ""))
                for activity in state["activities"]:
                    if activity["status"] == "Çalışıyor":
                        activity["status"] = "Kesildi"
                state["cockpit"].finish(status=outcome, changes=state["changes"])
                refresh(True)
            self.service.inspect_workspace_async(project.local_path, inspected,
                lambda e: inspected(inspection_error=e), before=state["before"])

        def on_event(event):
            if ended or event.session_id != session.session_id:
                return
            if event.metadata.get("project_id", project_id) != project_id or event.metadata.get("task_id", state["task_id"]) != state["task_id"]:
                return
            state["cockpit"].apply(event)
            update_accounting_state(state, event)
            kind = event.type.value
            if kind in {"context_compaction_started", "context_compacted"}:
                state["status"] = event.content or "Bağlam hazırlanıyor…"
                refresh(True)
            elif kind == "subagent_event":
                if event.metadata.get("child_event_type") in {"text_delta", "reasoning_delta"}:
                    return
                from firik_agent.ui.subagent_activity import update_subagent_activity
                update_subagent_activity(state["activities"], event.metadata, event.content or "")
                refresh(True)
            elif kind == "text_delta" and event.content:
                content = sanitize_secrets(event.content)
                response["content"] += content
                state["text"] += content
                refresh()
            elif kind == "tool_call_started":
                state["activities"].append({"tool": event.tool_name or "Araç", "arguments": event.arguments or {},
                                             "status": "Çalışıyor", "output": "", "duration_ms": None,
                                             "call_id": event.metadata.get("tool_call_id")})
                state["activities"] = state["activities"][-100:]
                state["status"] = f"Ajan çalışıyor · {event.tool_name}"
                refresh(True)
            elif kind in {"tool_call_result", "tool_call_error"}:
                activity = next((a for a in reversed(state["activities"]) if a["tool"] == event.tool_name and a["status"] == "Çalışıyor"), None)
                output = event.content or ""
                match = re.match(r"Exit code (-?\d+):", output)
                failed = kind == "tool_call_error" or event.metadata.get("is_error", False) or (match and int(match.group(1)) != 0)
                if activity:
                    activity.update(status="Hata" if failed else "Tamamlandı", output=sanitize_secrets(output[:16000]), duration_ms=event.duration_ms)
                refresh(True)
                if state["before"] is not None:
                    def inspected(snapshot):
                        if not ended and state["run_id"] == generation:
                            self._apply_coding_snapshot(state, snapshot)
                            refresh(True)
                    self.service.inspect_workspace_async(project.local_path, inspected, lambda e: None, before=state["before"])
            elif kind == "approval_required":
                request = dict(event.metadata, arguments=event.arguments or event.metadata.get("arguments", {}),
                               tool_name=event.tool_name or event.metadata.get("tool_name"))
                request_id = request.get("request_id")
                if request_id:
                    state["pending_approvals"][request_id] = request
                state["status"] = "İşlem onayı bekleniyor"
                refresh(True)
            elif kind == "approval_decided":
                state["pending_approvals"].pop(event.metadata.get("request_id"), None)
                refresh(True)
            elif kind == "plan_updated":
                state["plan"] = event.metadata.get("plan", event.metadata.get("steps", []))
                refresh(True)
            elif kind == "model_request_started":
                if event.metadata.get("request_kind") != "compaction":
                    state["model_iteration"] = event.metadata.get("iteration", state["model_iteration"] + 1)
                state["status"] = "Model isteği gönderiliyor…"
                refresh(True)
            elif kind in {"model_usage", "model_request_finished"}:
                refresh(True)
            elif kind == "file_changed":
                path = event.metadata.get("path")
                if path:
                    state["agent_paths"][path] = event.metadata
                    if visible():
                        self.coding_explorer.mark_paths(touched_paths=list(state["agent_paths"]))
            elif kind == "task_state":
                state["status"] = STATUS_NAMES.get(event.metadata.get("state", event.metadata.get("status")), event.content or state["status"])
                refresh(True)
            elif kind in {"verification", "tool_call_arguments", "mcp_connecting", "mcp_connected", "mcp_error",
                          "quality_gate", "checkpoint.created", "checkpoint_created", "policy_updated", "agent.message", "skill.activated"}:
                refresh(True)
            elif kind == "error":
                finish(error=event.content or "Model hatası")
            elif kind == "done":
                finish(event.content or "")

        def start(snapshot=None, error=None):
            if state["stopped"]:
                finish("Çalışma durduruldu.")
                return
            state["before"] = snapshot
            if error:
                state["inspection_warning"] = str(error)
            state["status"] = "Ajan yanıt hazırlıyor…"
            refresh(True)
            options = {"project_id": project_id, "task_id": state["task_id"], "mode": state["mode"],
                       "permissions": dict(state["permissions"]), "visibility_report": state["context_report"],
                       "policy": dict(state.get("policy", {})),
                       "mcp_servers": state["preferences"].get("mcp_servers", []),
                       "mcp_tools": state["preferences"].get("mcp_tools", []),
                       "capabilities": state["capabilities"]}
            if resume_source:
                options.update(resume_run_id=resume_source, resume_approved=resume_approved)
                if resume_prepared:
                    options["resume_prepared_run_id"] = resume_prepared
            options["subagents_enabled"] = state["preferences"].get("subagents_enabled", False)
            options["provider"] = state["preferences"].get("provider", self.service.provider)
            options["previous_plan"] = state["plan"]
            options["context_items"] = state.get("context_items", [])
            options["harness_profile"] = state.get("harness_profile")
            options["project_context"] = state.get("project_context", "")
            options["excluded_sources"] = state.get("excluded_sources", [])
            options["context_pipeline"] = state.get("context_pipeline", {})
            options["selected_skills"] = state.get("selected_skills", [])
            options["excluded_paths"] = state["preferences"].get("excluded_files", state["preferences"].get("never_include_files", []))
            options["quality_gates"] = state["preferences"].get("quality_gates")
            options["auto_quality_gates"] = bool(state["preferences"].get("auto_quality_gates", False))
            options["subagent_limits"] = dict(state["preferences"].get("subagent_limits", {}))
            options["execution_config"] = dict(state["preferences"].get("execution_config", {}))
            options["dirty_start"] = state["preferences"].get("dirty_start", "reject")
            try:
                state["future"] = self.service.chat_agent_stream_async(session_id=session.session_id, prompt=state.get("prepared_prompt", prompt),
                    cwd=project.local_path, on_event=on_event, on_done=finish, on_error=lambda e: finish(error=e), coding_options=options)
            except Exception as error:
                finish(error=str(error))

        def context_ready(data):
            if ended or state["run_id"] != generation:
                return
            if state["stopped"]:
                finish("Çalışma durduruldu.")
                return
            session.system_prompt = data.get("trusted_system_prompt", data["system_prompt"])
            state["project_context"] = data.get("project_context", "")
            state["harness_profile"] = data.get("harness_profile", {})
            state["excluded_sources"] = data.get("unavailable_report", [])
            state["context_pipeline"] = data.get("context_pipeline", {})
            state["context_report"] = data.get("visibility_report", [])
            state["prepared_prompt"] = data.get("user_prompt", prompt)
            state["context_items"] = data.get("context_items", [])
            state["context_inspector"] = data.get("context_inspector", {})
            state["index_status"] = data.get("index_status", {})
            state["composer"] = data.get("composer", composer)
            state["cockpit"].context = data.get("context_inspector", {"visibility_report": state["context_report"]})
            state["capabilities"] = data.get("capabilities", {})
            state["skill_catalogue"] = data.get("skill_catalogue", [])
            state["selected_skills"] = data.get("selected_skills", [])
            self.service.inspect_workspace_async(project.local_path, start, lambda e: start(error=e))

        # Preparing context is bounded but can involve index/search/Git; keep it off Tk.
        state["preparing_future"] = self.service.prepare_coding_async(project_id, prompt, context_ready, lambda e: finish(error=e),
            task_id=state["task_id"], session_id=session.session_id, relevant_paths=state["context_paths"], documents=state["documents"],
            permissions=state["permissions"],
            session_carry={"project_id": project_id, "task_id": state["task_id"], "plan": state["plan"], "status": state["status"]},
            selected_skills=state["preferences"].get("selected_skills", []))

    def _start_task_with_agent(self, task_id):
        if self._coding_state()["busy"]:
            return
        task = self.project_service.db.get_task(task_id)
        if task is None or task.project_id != self.current_project_id:
            messagebox.showerror("Görev", "Görev bu projeye ait değil.")
            return
        state = self._coding_state()
        if state["task_id"] != task_id:
            from firik_agent.core.session import ChatSession
            state["session"] = ChatSession(model=state["session"].model, active_mcp_servers=set())
            self.service.sessions[state["session"].session_id] = state["session"]
            state["messages"] = []
            state["text"] = ""
            state["plan"] = []
        state["task_id"] = task_id
        state["messages"].append({"role": "system", "content": f"GÖREV BAŞLATILDI: {task.title}"})
        state["text"] += f"\nGÖREV BAŞLATILDI: {task.title}\n"
        self._switch_workspace_tab("coding")
        criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria)
        self._run_coding_prompt(f"'{task.title}' görevini incele.\n{task.description}\nKabul kriterleri:\n{criteria}\nSeçili çalışma moduna göre planla, uygula ve gerçek doğrulama sonuçlarını bildir.")

    def _coding_mode_selected(self, label):
        state = self._coding_state()
        if state["busy"]:
            self.coding_mode_combo.set(MODE_NAMES[state["mode"]])
            return
        state["mode"] = MODES.get(label, LEGACY_MODES.get(label, state["mode"]))
        self._save_coding_preferences({"mode": state["mode"]})

    def _control_coding(self, action):
        state = self._coding_state()
        if action == "resume" and not state["busy"]:
            if state["record"]:
                self._coding_recovery(state["record"].id)
            elif state["last_prompt"]:
                self._run_coding_prompt("Önceki çalışmayı güncel dosyaları yeniden inceleyerek sürdür. Tamamlanmamış adımları ve önceki doğrulama sonuçlarını koru.")
            return
        if action == "cancel":
            state["stopped"] = True
            preparing = state.get("preparing_future")
            if preparing and not preparing.done():
                preparing.cancel()
        if state["busy"]:
            self.service.coding_control(state["session"].session_id, action)
            state["status"] = {"pause": "İşlem sınırında duraklatılıyor…", "resume": "Devam ediliyor…", "cancel": "Çalışma durduruluyor…"}[action]
            self._refresh_coding_display()

    def _decide_coding(self, request_id, allowed):
        state = self._coding_state()
        if self.service.coding_control(state["session"].session_id, "approve", request_id, allowed):
            state["pending_approvals"].pop(request_id, None)
            self._refresh_coding_control()

    def _approve_coding_plan(self):
        state = self._coding_state()
        if state["busy"]:
            self.coding_tabs.set("İşlemler")
            return
        if not state["plan"]:
            messagebox.showinfo("Plan", "Önce Planla modunda uygulanabilir bir plan oluşturun.")
            return
        self._coding_mode_selected("Yardımlı")
        self.coding_mode_combo.set("Yardımlı")
        self._run_coding_prompt("Gösterilen planı onaylıyorum. Planı izinlerim çerçevesinde uygula; gereken işlemler için ayrı onay iste.\n" + json.dumps(state["plan"], ensure_ascii=False))

    def _edit_coding_message(self, message):
        if self._coding_state()["busy"]:
            return
        self.coding_input.delete(0, "end")
        self.coding_input.insert(0, message["content"])
        self.coding_input.focus_set()

    def _search_coding_chat(self):
        query = simpledialog.askstring("Konuşmada Ara", "Aranacak metin (boş: tüm mesajlar):", parent=self)
        if query is not None:
            self.coding_chat_box.search(query)
            self.coding_tabs.set("Sohbet")

    def _export_coding(self):
        target = filedialog.asksaveasfilename(title="Konuşmayı Dışa Aktar", defaultextension=".md", filetypes=[("Markdown", "*.md"), ("Oturum JSON", "*.json")])
        if not target:
            return
        state = self._coding_state()
        try:
            if target.endswith(".json"):
                record = state.get("record") or self.service.coding_store.latest_run(
                    self.current_project_id, session_id=state["session"].session_id)
                content = self.service.coding_store.export_json(record.id, project_id=self.current_project_id) if record else json.dumps(self.service._coding_redact(state["messages"]), ensure_ascii=False, indent=2)
            else:
                content = "\n\n".join(f"### {'Siz' if m['role'] == 'user' else 'Firik' if m['role'] == 'assistant' else 'Bilgi'}\n\n{m['content']}" for m in state["messages"])
                content = sanitize_secrets(content)
            Path(target).write_text(content, encoding="utf-8")
        except (OSError, ValueError) as error:
            messagebox.showerror("Dışa Aktarma", str(error))

    def _add_coding_path(self, relative):
        project = self.project_service.db.get_project(self.current_project_id)
        try:
            path = workspace_path(project.local_path, relative)
            if is_sensitive_file(path, project_root=project.local_path):
                raise ValueError("Hassas dosyalar bağlama eklenemez.")
            value = path.relative_to(Path(project.local_path).resolve()).as_posix()
            state = self._coding_state()
            if value not in state["context_paths"]:
                state["context_paths"].append(value)
            self._save_coding_preferences({"context_paths": state["context_paths"]})
            self._refresh_coding_control()
        except (OSError, ValueError) as error:
            messagebox.showerror("Bağlam", str(error))

    def _add_coding_snippet(self, content):
        self._coding_state()["documents"].append({"title": "Sohbetten seçilen kod", "content": sanitize_secrets(content[:16000])})
        self._refresh_coding_control()

    def _attach_coding_files(self):
        for path in filedialog.askopenfilenames(title="Projedeki Dosyaları Bağlama Ekle"):
            self._add_coding_path(path)

    def _drop_coding_files(self, paths):
        for path in paths:
            self._add_coding_path(path)

    def _open_coding_reference(self, reference):
        fragment = re.search(r"#L(\d+)$", reference)
        reference = reference.split("#", 1)[0]
        match = re.match(r"^(.*?)(?::(\d+))?$", reference)
        if match:
            self._preview_coding_file(match.group(1), int(match.group(2) or (fragment.group(1) if fragment else 1)))

    def _toggle_coding_pane(self, side):
        panes = self.coding_panes
        widget = self.coding_left if side == "left" else self.coding_right
        shown = str(widget) in tuple(map(str, panes.panes()))
        if shown:
            panes.forget(widget)
            if self._coding_state().get("manual_side") == side:
                self._coding_state()["manual_side"] = None
        else:
            self._coding_state()["manual_side"] = side
            # Compact windows show one auxiliary panel at a time.
            if panes.winfo_width() < 1140:
                other = self.coding_right if side == "left" else self.coding_left
                if str(other) in tuple(map(str, panes.panes())):
                    panes.forget(other)
            minimum = 180 if side == "left" else 250
            kwargs = {"minsize": minimum, "width": self._coding_state()["preferences"].get(f"{side}_width", 220 if side == "left" else 300), "stretch": "never"}
            if side == "left":
                kwargs["before"] = self.coding_mid
            panes.add(widget, **kwargs)
        self._save_coding_preferences({f"show_{side}": not shown})

    def _coding_palette(self):
        existing = getattr(self, "coding_palette_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.search.focus_set()
            return existing
        dialog = ctk.CTkToplevel(self)
        dialog.title("Komut Paleti")
        dialog.geometry("520x490")
        dialog.transient(self.winfo_toplevel())
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        self.coding_palette_dialog = dialog
        modifier = "Cmd" if self.tk.call("tk", "windowingsystem") == "aqua" else "Ctrl"
        commands = [(f"{name} Görünümünü Aç · {modifier}+{index}", lambda value=name: self.coding_tabs.set(value))
                    for index, name in enumerate(WORKSPACE_VIEWS, 1)]
        commands += [
            (f"Yeni Ajan Oluştur · {modifier}+Shift+A", self._coding_create_agent),
            ("Ajanı Seç / İncele", lambda: (self._show_coding_inspector("Ajanlar"), self._refresh_coding_agents())),
            ("Ajanı Yönlendir", self._coding_steer),
            ("Modeli Değiştir", lambda: self.coding_model_combo.focus_set()),
            ("Terminal Komutu Çalıştır", self._coding_run_command),
            ("Test / Kalite Kapılarını Çalıştır", self._coding_run_quality_gates),
            ("Bağlama Dosya Ekle", self._attach_coding_files),
            ("Bağlamı İncele", self._coding_context_inspector),
            ("Kontrol Noktası Oluştur / Geri Yükle", self._coding_checkpoints),
            ("İzinler / MCP / Beceriler", self._coding_permissions),
            ("Ajanı Duraklat", lambda: self._control_coding("pause")),
            ("Ajanı Devam Ettir", lambda: self._control_coding("resume")),
            (f"Ajanı Durdur · {modifier}+.", lambda: self._control_coding("cancel")),
            ("Yeni Oturum", self._new_coding_session),
            ("Oturum Geçmişi", self._coding_sessions),
            ("Konuşmayı Dışa Aktar", self._export_coding),
            ("Proje Dosyalarında Ara", self._focus_coding_file_search),
            ("Konuşmada Ara", self._search_coding_chat),
            ("Önceki İsteği Düzenle", lambda: self._edit_coding_message({"content": self._coding_state()["last_prompt"]})),
            ("Görevleri Aç", lambda: self._switch_workspace_tab("tasks")),
            ("Projeyi IDE'de Aç", self._open_coding_editor),
            ("Görünüm / Yazı Boyutu", self._coding_font_size),
            ("Proje Panelini Aç / Kapat", lambda: self._toggle_coding_pane("left")),
            ("İnceleyiciyi Aç / Kapat", lambda: self._toggle_coding_pane("right")),
        ]
        query = tk.StringVar(master=dialog)
        dialog.search = ctk.CTkEntry(dialog, placeholder_text="Komut ara…", textvariable=query, height=34)
        dialog.search.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        dark = ctk.get_appearance_mode() == "Dark"
        dialog.results = tk.Listbox(dialog, borderwidth=0, highlightthickness=1, activestyle="dotbox",
            background="#101a2b" if dark else "#f8fafc", foreground="#e6eef8" if dark else "#172b43",
            selectbackground="#164955" if dark else "#c8eee9", selectforeground="#e6fffc" if dark else "#123b43",
            font=("Arial", 12), exportselection=False)
        dialog.results.grid(row=1, column=0, sticky="nsew", padx=12)
        ctk.CTkLabel(dialog, text="↑↓ seç · Enter çalıştır · Esc kapat", anchor="w").grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        matches = []
        def filter_commands(*args):
            matches[:] = [(label, callback) for label, callback in commands if query.get().casefold().strip() in label.casefold()]
            dialog.results.delete(0, "end")
            for label, _ in matches:
                dialog.results.insert("end", label)
            if matches:
                dialog.results.selection_set(0)
                dialog.results.activate(0)
        def execute(event=None):
            selection = dialog.results.curselection()
            if selection and matches:
                callback = matches[selection[0]][1]
                dialog.destroy()
                callback()
            return "break"
        def move(event):
            if matches:
                index = (dialog.results.curselection() or (0,))[0]
                index = max(0, min(len(matches) - 1, index + (1 if event.keysym == "Down" else -1)))
                dialog.results.selection_clear(0, "end")
                dialog.results.selection_set(index)
                dialog.results.activate(index)
                dialog.results.see(index)
            return "break"
        query.trace_add("write", filter_commands)
        for widget in (dialog.search, dialog.results):
            widget.bind("<Return>", execute)
            widget.bind("<Up>", move)
            widget.bind("<Down>", move)
        dialog.results.bind("<Double-Button-1>", execute)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        filter_commands()
        dialog.after(80, dialog.search.focus_set)
        return dialog

    def _show_coding_inspector(self, view):
        if str(self.coding_right) not in tuple(map(str, self.coding_panes.panes())):
            self._toggle_coding_pane("right")
        self.coding_inspector_tabs.set(view)

    def _focus_coding_file_search(self):
        if str(self.coding_left) not in tuple(map(str, self.coding_panes.panes())):
            self._toggle_coding_pane("left")
        self.coding_explorer.query.focus_set()

    def _coding_font_size(self):
        value = simpledialog.askinteger("Yazı Boyutu", "Kodlama alanı yazı boyutu (11–22):", minvalue=11, maxvalue=22, initialvalue=self._coding_state()["preferences"].get("font_size", 13), parent=self)
        if value:
            self._save_coding_preferences({"font_size": value})
            self.coding_input.configure(font=ctk.CTkFont(size=value))
            self.coding_chat_box.font_size = value
            self.coding_chat_box._render(rebuild=True)

    def _coding_permissions(self):
        state = self._coding_state()
        if state["busy"]:
            messagebox.showinfo("İzinler", "Etkin çalışmanın izinleri sabittir. Önce çalışmayı durdurun.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("İzinler, MCP ve Beceriler")
        dialog.geometry("480x600")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        scroll = ctk.CTkScrollableFrame(dialog)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(scroll, text="Model sağlayıcısı").pack(anchor="w")
        configured = list(dict.fromkeys(["firik", "llmtr", "openai", *self.service.config.get("providers", {})]))
        provider_combo = ctk.CTkComboBox(scroll, values=configured, state="readonly")
        provider_combo.set(state["preferences"].get("provider", self.service.provider))
        provider_combo.pack(fill="x", pady=5)
        ctk.CTkLabel(scroll, text="Model kimliği (sağlayıcıdaki tam adı)").pack(anchor="w")
        model_entry = ctk.CTkEntry(scroll)
        model_entry.insert(0, self.coding_model_combo.get())
        model_entry.pack(fill="x", pady=5)
        permission_vars = {}
        for key, label in PERMISSION_NAMES.items():
            var = tk.BooleanVar(value=state["permissions"].get(key, False))
            permission_vars[key] = var
            ctk.CTkCheckBox(scroll, text=label, variable=var).pack(anchor="w", pady=4)
        ctk.CTkLabel(scroll, text="Özel izin politikası", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(14, 4))
        custom_policy = tk.BooleanVar(value=bool(state.get("policy")))
        ctk.CTkCheckBox(scroll, text="Özel çalışma modunda bu politikayı kullan", variable=custom_policy).pack(anchor="w", pady=4)
        from firik_agent.projects.coding_runtime import CodingRunControl
        defaults = CodingRunControl(state["session"].session_id, self.current_project_id, mode="custom",
            permissions={key: True for key in PERMISSION_NAMES}, policy=state.get("policy", {})).policy_snapshot()
        policy_combos = {}
        policy_labels = {value: label for label, value in POLICY_VALUES.items()}
        for key, label in POLICY_NAMES.items():
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left", fill="x", expand=True)
            immutable = key in {"secrets", "shell.sudo", "git.forcePush"}
            combo = ctk.CTkComboBox(row, values=list(POLICY_VALUES), width=115,
                                  state="disabled" if immutable else "readonly", command=lambda value: custom_policy.set(True))
            combo.set(policy_labels[defaults[key]])
            combo.pack(side="right")
            policy_combos[key] = combo
        ctk.CTkLabel(scroll, text="Terminal/test, Git yazma ve MCP ayrı onay ister. Docker yalnız komutları izole eder; dosya araçları ve MCP host/dış servis sınırında kalır.", wraplength=420, justify="left").pack(anchor="w", pady=8)
        execution = state["preferences"].get("execution_config", {})
        ctk.CTkLabel(scroll, text="Komut yürütme arka ucu").pack(anchor="w")
        backend_combo = ctk.CTkComboBox(scroll, values=["host", "docker"], state="readonly")
        backend_combo.set(execution.get("backend", "host"))
        backend_combo.pack(fill="x", pady=4)
        ctk.CTkLabel(scroll, text="Docker imajı (yerelde hazır olmalı)").pack(anchor="w")
        image_entry = ctk.CTkEntry(scroll)
        image_entry.insert(0, execution.get("image", "python:3.12-slim"))
        image_entry.pack(fill="x", pady=4)
        profile_combo = ctk.CTkComboBox(scroll, values=["restricted", "development", "custom"], state="readonly")
        profile_combo.set(execution.get("profile", "restricted"))
        profile_combo.pack(fill="x", pady=4)
        network_var = tk.BooleanVar(value=execution.get("network", False))
        ctk.CTkCheckBox(scroll, text="Docker ağına izin ver (development/custom)", variable=network_var).pack(anchor="w", pady=4)
        ctk.CTkLabel(scroll, text="Varsayılan sınırlar: 1 CPU, 512 MiB RAM, 128 PID, 300 sn, 64 MiB /tmp. Docker kullanılamazsa host'a geçilmez.", wraplength=420, justify="left").pack(anchor="w", pady=4)
        ctk.CTkButton(scroll, text="Docker önkoşulunu denetle", command=self._coding_docker_doctor).pack(fill="x", pady=4)
        dirty_combo = ctk.CTkComboBox(scroll, values=["reject", "clean_head"], state="readonly")
        dirty_combo.set(state["preferences"].get("dirty_start", "reject"))
        ctk.CTkLabel(scroll, text="Alt ajan başlangıcı: reject kirli depoyu reddeder; clean_head yalnız commit içeriğini alır, kaydedilmemiş düzenlemeler taşınmaz.", wraplength=420, justify="left").pack(anchor="w", pady=4)
        dirty_combo.pack(fill="x", pady=4)
        selected_servers, selected_tools = {}, {}
        prefs = state["preferences"]
        for server in self.service.get_mcp_servers():
            name = server["name"]
            var = tk.BooleanVar(value=name in prefs.get("mcp_servers", []))
            selected_servers[name] = var
            ctk.CTkCheckBox(scroll, text=f"MCP: {name}", variable=var).pack(anchor="w", pady=3)
        for tool in self.service.mcp_manager.tool_registry.list_tools():
            var = tk.BooleanVar(value=tool.name in prefs.get("mcp_tools", []))
            selected_tools[tool.name] = var
            ctk.CTkCheckBox(scroll, text=tool.name, variable=var).pack(anchor="w", padx=15, pady=2)
        ctk.CTkLabel(scroll, text="Araçlar yalnız seçili sunucu ve açık işlem onayıyla çalışır.", wraplength=420).pack(anchor="w", pady=8)
        skills = self.service.get_agent(state["session"].session_id).skills
        skills.discover_skills(additional_dirs=[Path(__file__).parent.parent / "builtin_skills"])
        skill_vars = {}
        for skill in skills.list_skills():
            name = skill.get("name") if isinstance(skill, dict) else skill.name
            if not name:
                continue
            var = tk.BooleanVar(value=name in prefs.get("selected_skills", []))
            skill_vars[name] = var
            ctk.CTkCheckBox(scroll, text=f"Beceri: {name}", variable=var).pack(anchor="w", pady=3)
        def save():
            from dataclasses import asdict
            from firik_agent.core.execution import ExecutionConfig
            try:
                selected_execution = ExecutionConfig(**{**execution, "backend": backend_combo.get(),
                    "image": image_entry.get().strip(), "profile": profile_combo.get(),
                    "network": network_var.get(), "network_approved": network_var.get()})
            except (TypeError, ValueError) as error:
                messagebox.showerror("Sandbox", str(error), parent=dialog)
                return
            state["permissions"] = {key: var.get() for key, var in permission_vars.items()}
            state["policy"] = {key: POLICY_VALUES[combo.get()] for key, combo in policy_combos.items()} if custom_policy.get() else {}
            if custom_policy.get():
                self._coding_mode_selected("Özel")
                self.coding_mode_combo.set("Özel")
            self._save_coding_preferences({"permissions": state["permissions"],
                "policy": state["policy"],
                "execution_config": asdict(selected_execution), "dirty_start": dirty_combo.get(),
                "provider": provider_combo.get(),
                "mcp_servers": [key for key, var in selected_servers.items() if var.get()],
                "mcp_tools": [key for key, var in selected_tools.items() if var.get()],
                "selected_skills": [key for key, var in skill_vars.items() if var.get()]})
            model = model_entry.get().strip()
            if model:
                self._on_coding_model_selected(model)
                self.coding_model_combo.configure(values=list(dict.fromkeys([model, *self.coding_model_combo.cget("values")])))
                self.coding_model_combo.set(model)
            dialog.destroy()
        ctk.CTkButton(dialog, text="Kaydet", command=save).pack(pady=8)

    def _coding_docker_doctor(self):
        from dataclasses import asdict
        from firik_agent.core.execution import DockerExecutionBackend
        async def inspect():
            return asdict(await DockerExecutionBackend().availability())
        self.service.runtime.submit(inspect(),
            on_done=lambda result: self.after(0, lambda: messagebox.showinfo("Docker önkoşulu", json.dumps(result, ensure_ascii=False, indent=2))),
            on_error=lambda error: self.after(0, lambda: messagebox.showerror("Docker önkoşulu", str(error))))

    def _coding_execution_status(self):
        state = self._coding_state()
        session_id = state["session"].session_id
        control = getattr(self.service, "_coding_controls", {}).get(session_id)
        details = control.snapshot() if control is not None else {
            "execution": getattr(state.get("record"), "usage", {}).get("execution", {}),
            "configured": state["preferences"].get("execution_config", {"backend": "host"})}
        if control is None:
            details["agent_workspaces"] = [item for item in getattr(state.get("record"), "decisions", [])
                                            if item.get("kind") == "subagent"]
        manager = getattr(self.service, "_coding_managers", {}).get(session_id)
        if manager is not None:
            details["agent_workspaces"] = manager.snapshot()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Yürütme / Sandbox Durumu")
        dialog.geometry("680x550")
        ctk.CTkButton(dialog, text="Docker önkoşulunu denetle", command=self._coding_docker_doctor).pack(fill="x", padx=10, pady=5)
        box = ctk.CTkTextbox(dialog, wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", json.dumps(details, ensure_ascii=False, indent=2, default=str))
        box.configure(state="disabled")
        tools = getattr(self.service, "_coding_filters", {}).get(session_id)
        if tools is not None:
            def display(execution):
                if not box.winfo_exists():
                    return
                details["execution"] = execution
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("1.0", json.dumps(details, ensure_ascii=False, indent=2, default=str))
                box.configure(state="disabled")
            self.service.runtime.submit(tools.inspect_execution(),
                on_done=lambda result: self.after(0, lambda: display(result)),
                on_error=lambda error: self.after(0, lambda: display({"diagnostic": str(error)})))

    def _coding_harness_settings(self):
        from firik_agent.ui.coding_harness import show_harness_dialog
        return show_harness_dialog(self)

    def _coding_side_diff(self):
        state = self._coding_state()
        path = state["selected_change"]
        if not path or not state["before"] or not state["snapshot"]:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Yan yana fark: {path}")
        dialog.geometry("1050x650")
        dialog.grid_rowconfigure(1, weight=1)
        for column, (title, snapshot) in enumerate((("Önce", state["before"]), ("Sonra", state["snapshot"]))):
            dialog.grid_columnconfigure(column, weight=1)
            ctk.CTkLabel(dialog, text=title).grid(row=0, column=column)
            box = ctk.CTkTextbox(dialog, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
            box.grid(row=1, column=column, sticky="nsew", padx=3, pady=5)
            text = snapshot.files.get(path, "")
            box.insert("end", "\n".join(f"{i:4} {line}" for i, line in enumerate(text.splitlines(), 1)))
            box.configure(state="disabled")

    def _undo_coding_change(self):
        state = self._coding_state()
        if state["busy"] or not state["selected_change"]:
            return
        self.service.undo_coding_async(state["session"].session_id, state["selected_change"],
            on_success=lambda result: self._refresh_coding_changes_async(),
            on_error=lambda error: messagebox.showerror("Güvenli Geri Alma", error))
