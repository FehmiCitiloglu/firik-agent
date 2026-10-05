"""Kodlama Ajanı çalışma alanı; görünüm ile yürütücü ayrı sorumluluklardır."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import customtkinter as ctk
from evren_agent.ui.components.effort_selector import EffortSelector

from evren_agent.core.events import AgentEventType
from evren_agent.projects.workspace import workspace_path
from evren_agent.projects.indexer import is_sensitive_file, sanitize_secrets
from evren_agent.ui.components.coding_conversation import CodingConversation, CodingInput
from evren_agent.ui.components.coding_explorer import CodingExplorer
from evren_agent.ui.coding_strings import MODES, MODE_NAMES, STATUS_NAMES, PERMISSION_NAMES, WELCOME
from evren_agent.ui.editor import EDITORS
from evren_agent.ui.file_drop import register_file_drop
from evren_agent.ui.coding_history import CodingHistoryMixin, restore_state_history
from evren_agent.ui.views.git_view import GitView


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
                "inspection_warning": "", "selected_change": None, "run_id": 0,
                "mode": prefs.get("mode", "approved"), "preferences": prefs,
                "permissions": {"allow_reads": True, "allow_deletes": True, "allow_edits": True,
                                "allow_terminal": True, "allow_tests": True, "allow_mcp": False,
                                **prefs.get("permissions", {})},
                "context_paths": prefs.get("context_paths", []), "documents": [], "plan": [],
                "context_report": [], "capabilities": {}, "usage": {}, "task_id": None,
                "last_prompt": "", "paused": False, "pending_approvals": {},
                "record": record, "stopped": False, "future": None, "agent_paths": {},
            }
            if record:
                restore_state_history(state, record)
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
        panes = tk.PanedWindow(container, orient="horizontal", sashwidth=6, borderwidth=0,
                               background="#243247" if ctk.get_appearance_mode() == "Dark" else "#d4d4d4")
        panes.grid(row=0, column=0, sticky="nsew")
        self.coding_panes = panes
        self.coding_explorer = CodingExplorer(panes, self.service, project_id,
            on_open=lambda path, editor: self._open_coding_editor(path) if editor else self._open_coding_reference(path),
            on_context=self._add_coding_path, on_preferences=self._save_coding_preferences)
        left = self.coding_explorer
        mid = ctk.CTkFrame(panes, fg_color=("gray92", "#161f2e"))
        right = ctk.CTkFrame(panes, fg_color=("gray92", "#161f2e"))
        self.coding_left, self.coding_mid, self.coding_right = left, mid, right
        panes.add(left, minsize=170, width=prefs.get("left_width", 220), stretch="never")
        panes.add(mid, minsize=340, stretch="always")
        panes.add(right, minsize=180, width=prefs.get("right_width", 230), stretch="never")
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(mid, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=5)
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="ew")
        title.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title, text="Evren Kodlama Ajanı", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        self.coding_mode_combo = ctk.CTkComboBox(title, values=list(MODES), width=155,
                                               state="readonly", command=self._coding_mode_selected)
        self.coding_mode_combo.set(MODE_NAMES.get(state["mode"], "Onaylı Uygulama"))
        self.coding_mode_combo.grid(row=0, column=1)
        models = ctk.CTkFrame(head, fg_color="transparent")
        models.grid(row=1, column=0, sticky="ew", pady=5)
        models.grid_columnconfigure(0, weight=1)
        models.grid_columnconfigure(1, weight=1)
        selected_model = self.project_service.db.get_settings(project_id).coding_model or self.service.default_model
        self.coding_model_combo = ctk.CTkComboBox(models, values=list(dict.fromkeys([selected_model, *self.service._cached_models])),
            width=120, state="readonly", command=self._on_coding_model_selected)
        self.coding_model_combo.set(selected_model)
        self.coding_model_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.coding_editor_combo = ctk.CTkComboBox(models, values=EDITORS, width=110, state="readonly", command=self._on_coding_editor_selected)
        self.coding_editor_combo.set(self.service.config.get("coding_editor", {}).get("name", "VS Code"))
        self.coding_editor_combo.grid(row=0, column=1, sticky="ew", padx=4)
        self.coding_open_project_btn = ctk.CTkButton(models, text="Projeyi Aç", width=90, command=self._open_coding_editor)
        self.coding_open_project_btn.grid(row=0, column=2)
        self.coding_effort_selector = EffortSelector(right, self.service, self._on_coding_effort_selected)
        self.coding_effort_selector.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        settings = self.project_service.db.get_settings(self.current_project_id)
        state["session"].reasoning_effort = settings.coding_preferences.get("reasoning_effort")
        self._refresh_coding_effort()
        self._load_coding_models()
        toolbar = ctk.CTkFrame(head, fg_color="transparent")
        toolbar.grid(row=2, column=0, sticky="ew")
        for label, callback in (("Gezgin", lambda: self._toggle_coding_pane("left")),
                                ("Kontrol", lambda: self._toggle_coding_pane("right")),
                                ("Ara", self._search_coding_chat), ("Dışa Aktar", self._export_coding),
                                ("Dosya Ekle", self._attach_coding_files), ("⋯", self._coding_palette)):
            ctk.CTkButton(toolbar, text=label, width=55 if label != "Dışa Aktar" else 70, height=24, command=callback).pack(side="left", padx=2)
        self.coding_status_label = ctk.CTkLabel(head, text=state["status"], height=20, anchor="w",
                                               wraplength=500, font=ctk.CTkFont(size=11))
        self.coding_status_label.grid(row=3, column=0, sticky="ew")
        self.coding_pending_btn = ctk.CTkButton(head, text="Onay Bekleniyor · İşlemi İncele", height=25,
                                               command=self._inspect_coding_approvals)
        self.coding_tabs = ctk.CTkTabview(mid, height=180)
        self.coding_tabs.grid(row=1, column=0, sticky="nsew", padx=4)
        for name in ("Sohbet", "Değişiklikler", "İşlemler", "Dosyalar", "Git", "Plan"):
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
        list_frame = ctk.CTkFrame(changes, height=35)
        list_frame.grid(row=1, column=0, sticky="ew")
        list_frame.grid_propagate(False)
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)
        self.coding_changes_list = ctk.CTkScrollableFrame(list_frame, height=35)
        self.coding_changes_list.grid(row=0, column=0, sticky="nsew")
        self.coding_diff_box = ctk.CTkTextbox(changes, wrap="none", height=100, font=ctk.CTkFont(family="monospace", size=12))
        self.coding_diff_box.grid(row=2, column=0, sticky="nsew")
        for tag, color in (("added", "#10b981"), ("removed", "#f87171"), ("header", "#38bdf8")):
            self.coding_diff_box.tag_config(tag, foreground=color)
        footer = ctk.CTkFrame(changes, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew")
        self.coding_open_file_btn = ctk.CTkButton(footer, text="Editörde Aç", width=90, command=self._open_selected_coding_file)
        self.coding_open_file_btn.pack(side="left", padx=2)
        ctk.CTkButton(footer, text="Yan Yana", width=85, command=self._coding_side_diff).pack(side="left", padx=2)
        self.coding_undo_btn = ctk.CTkButton(footer, text="Ajan Değişikliğini Geri Al", width=160, command=self._undo_coding_change)
        self.coding_undo_btn.pack(side="right", padx=2)
        self.coding_activity_list = ctk.CTkScrollableFrame(self.coding_tabs.tab("İşlemler"))
        self.coding_activity_list.grid(row=0, column=0, sticky="nsew")
        self.coding_files_list = ctk.CTkScrollableFrame(self.coding_tabs.tab("Dosyalar"))
        self.coding_files_list.grid(row=0, column=0, sticky="nsew")
        self.coding_plan_box = ctk.CTkTextbox(self.coding_tabs.tab("Plan"), wrap="word")
        self.coding_plan_box.grid(row=0, column=0, sticky="nsew")
        ctk.CTkButton(self.coding_tabs.tab("Plan"), text="Planı Onayla ve Uygula", command=self._approve_coding_plan).grid(row=1, column=0, pady=4)
        ctk.CTkButton(self.coding_tabs.tab("Git"), text="Git Çalışma Alanını Aç", command=lambda: self._switch_workspace_tab("git")).grid(row=0, column=0)
        input_area = ctk.CTkFrame(mid, fg_color="transparent")
        input_area.grid(row=2, column=0, sticky="ew", padx=8, pady=3)
        input_area.grid_columnconfigure(0, weight=1)
        self.coding_input = CodingInput(input_area, height=48, wrap="word", font=ctk.CTkFont(size=prefs.get("font_size", 13)))
        self.coding_input.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.coding_drop_available = register_file_drop(self.coding_input._textbox, self._drop_coding_files)
        for key in ("<Return>", "<KP_Enter>"):
            self.coding_input.bind(key, self._handle_coding_return, add="+")
        def newline(event):
            self.coding_input.insert("insert", "\n")
            return "break"
        self.coding_input.bind("<Shift-Return>", newline, add="+")
        self.coding_send_btn = ctk.CTkButton(input_area, text="Gönder", width=75, command=self._send_coding_prompt)
        self.coding_send_btn.grid(row=0, column=1)
        ctk.CTkLabel(input_area, text="Enter: gönder · Shift+Enter: yeni satır · Ctrl+K: komutlar", height=18, font=ctk.CTkFont(size=10)).grid(row=1, column=0, columnspan=2, sticky="w")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        controls = ctk.CTkScrollableFrame(right, width=200)
        controls.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(controls, text="Ajan kontrol merkezi", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        self.coding_subagents_var = tk.BooleanVar(value=prefs.get("subagents_enabled", False))
        ctk.CTkCheckBox(controls, text="Araştırma / inceleme\nalt ajanları", variable=self.coding_subagents_var,
            command=lambda: self._save_coding_preferences({"subagents_enabled": self.coding_subagents_var.get()}),
            height=36).pack(fill="x", pady=4)
        self.coding_task_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=200)
        self.coding_task_label.pack(fill="x", pady=4)
        self.coding_context_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=200)
        self.coding_context_label.pack(fill="x", pady=4)
        self.coding_usage_label = ctk.CTkLabel(controls, text="", anchor="w", justify="left", wraplength=200)
        self.coding_usage_label.pack(fill="x", pady=4)
        for text, action in (("Duraklat", "pause"), ("Devam Et", "resume"), ("Durdur", "cancel")):
            ctk.CTkButton(controls, text=text, command=lambda a=action: self._control_coding(a)).pack(fill="x", pady=3)
        ctk.CTkButton(controls, text="Planı İncele", command=lambda: self.coding_tabs.set("Plan")).pack(fill="x", pady=3)
        ctk.CTkButton(controls, text="Değişiklikleri İncele", command=lambda: self.coding_tabs.set("Değişiklikler")).pack(fill="x", pady=3)
        ctk.CTkButton(controls, text="İzinler / MCP / Beceriler", command=self._coding_permissions).pack(fill="x", pady=3)
        ctk.CTkButton(controls, text="Yazı Boyutu", command=self._coding_font_size).pack(fill="x", pady=3)
        self.coding_approval_frame = ctk.CTkFrame(controls, fg_color="transparent")
        self.coding_approval_frame.pack(fill="x", pady=6)
        self._refresh_coding_changes()
        self._refresh_coding_activity()
        self._refresh_coding_files_async()
        self._refresh_coding_control()
        self._refresh_coding_display()
        def layout(event=None):
            if not panes.winfo_exists() or self.coding_panes is not panes or not self.coding_status_label.winfo_exists():
                return
            compact = container.winfo_width() < 1040
            if container.winfo_height() < 460:
                self.coding_status_label.grid_remove()
            else:
                self.coding_status_label.grid()
            for side, widget in (("left", left), ("right", right)):
                shown = str(widget) in tuple(map(str, panes.panes()))
                desired = prefs.get(f"show_{side}", True) and (not compact or state.get("manual_side") == side)
                if desired and not shown:
                    panes.add(widget, minsize=170, width=prefs.get(f"{side}_width", 220),
                              before=mid if side == "left" else None, stretch="never")
                elif shown and not desired:
                    panes.forget(widget)
        container.bind("<Configure>", layout, add="+")
        container.after_idle(layout)
        def save_sizes(event):
            if self.current_project_id != project_id:
                return
            self._save_coding_preferences({"left_width": max(170, left.winfo_width()),
                                           "right_width": max(180, right.winfo_width())})
        panes.bind("<ButtonRelease-1>", save_sizes)
        # Widget-owned bindings disappear when this view is destroyed.
        for key, callback in (("<Control-k>", self._coding_palette), ("<Control-f>", self._search_coding_chat),
                              ("<Escape>", lambda: self._control_coding("cancel"))):
            self.coding_input.bind(key, lambda event, fn=callback: (fn(), "break")[1], add="+")

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
        self.coding_send_btn.configure(state="disabled" if state["busy"] else "normal", text="Bekleyin…" if state["busy"] else "Gönder")
        self._refresh_coding_effort()
        self.coding_model_combo.configure(state="disabled" if state["busy"] else "readonly")
        self.coding_mode_combo.configure(state="disabled" if state["busy"] else "readonly")
        self.coding_status_label.configure(text=state["status"])

    def _refresh_coding_control(self):
        state = self._coding_state()
        task = self.project_service.db.get_task(state["task_id"]) if state["task_id"] else None
        title = task.title if task else state["last_prompt"][:160] or "Görev seçilmedi"
        self.coding_task_label.configure(text=f"Aktif görev\n{title}\n{state['status']}\n{state['inspection_warning']}")
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

    def _run_coding_prompt(self, prompt, prepared=None):
        project_id = self.current_project_id
        state = self._coding_state()
        if state["busy"]:
            return
        state.update(busy=True, stopped=False, paused=False, last_prompt=prompt, before=None,
                     changes=[], activities=[], pending_approvals={}, inspection_warning="", agent_paths={}, record=None,
                     usage={}, usage_requests={}, model_iteration=0)
        state["run_id"] += 1
        generation = state["run_id"]
        state["messages"].append({"role": "user", "content": prompt})
        response = {"role": "assistant", "content": ""}
        state["messages"].append(response)
        state["text"] += f"\n[Siz]: {prompt}\n[evren]: "
        state["status"] = "Güncel proje bağlamı hazırlanıyor…"
        session = state["session"]
        session.model = self.project_service.db.get_settings(project_id).coding_model or self.service.default_model
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
                state["status"] = "Durduruldu" if state["stopped"] else "Çalışma hata ile sona erdi" if error else f"Tamamlandı · {len(state['changes'])} dosya değişti" + (f" · {errors} araç hatası" if errors else "")
                for activity in state["activities"]:
                    if activity["status"] == "Çalışıyor":
                        activity["status"] = "Kesildi"
                refresh(True)
            self.service.inspect_workspace_async(project.local_path, inspected,
                lambda e: inspected(inspection_error=e), before=state["before"])

        def on_event(event):
            if ended or event.session_id != session.session_id:
                return
            if event.metadata.get("project_id", project_id) != project_id or event.metadata.get("task_id", state["task_id"]) != state["task_id"]:
                return
            kind = event.type.value
            if kind == "subagent_event":
                if event.metadata.get("child_event_type") in {"text_delta", "reasoning_delta"}:
                    return
                from evren_agent.ui.subagent_activity import update_subagent_activity
                update_subagent_activity(state["activities"], event.metadata, event.content or "")
                if event.metadata.get("child_event_type") == "model_usage":
                    key = (event.metadata["agent_id"], event.metadata.get("request_iteration", 1))
                    state["usage_requests"][key] = event.metadata.get("child_metadata", {}).get("usage", {})
                    totals = {}
                    for usage in state["usage_requests"].values():
                        for name, value in usage.items():
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                totals[name] = totals.get(name, 0) + value
                    state["usage"] = totals
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
                state["model_iteration"] = event.metadata.get("iteration", state["model_iteration"] + 1)
                state["status"] = "Model isteği gönderiliyor…"
                refresh(True)
            elif kind == "model_usage":
                state["usage_requests"][state["model_iteration"]] = event.metadata.get("usage", {})
                totals = {}
                for usage in state["usage_requests"].values():
                    for key, value in usage.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            totals[key] = totals.get(key, 0) + value
                        else:
                            totals[key] = value
                state["usage"] = totals
                refresh(True)
            elif kind == "file_changed":
                path = event.metadata.get("path")
                if path:
                    state["agent_paths"][path] = event.metadata
            elif kind == "task_state":
                state["status"] = STATUS_NAMES.get(event.metadata.get("state", event.metadata.get("status")), event.content or state["status"])
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
                       "mcp_servers": state["preferences"].get("mcp_servers", []),
                       "mcp_tools": state["preferences"].get("mcp_tools", []),
                       "capabilities": state["capabilities"]}
            options["subagents_enabled"] = state["preferences"].get("subagents_enabled", False)
            options["provider"] = state["preferences"].get("provider", self.service.provider)
            options["previous_plan"] = state["plan"]
            try:
                state["future"] = self.service.chat_agent_stream_async(session_id=session.session_id, prompt=prompt,
                    cwd=project.local_path, on_event=on_event, on_done=finish, on_error=lambda e: finish(error=e), coding_options=options)
            except Exception as error:
                finish(error=str(error))

        def context_ready(data):
            if ended or state["run_id"] != generation:
                return
            if state["stopped"]:
                finish("Çalışma durduruldu.")
                return
            session.system_prompt = data["system_prompt"]
            state["context_report"] = data.get("visibility_report", [])
            state["capabilities"] = data.get("capabilities", {})
            state["skill_catalogue"] = data.get("skill_catalogue", [])
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
            from evren_agent.core.session import ChatSession
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
        state["mode"] = MODES[label]
        self._save_coding_preferences({"mode": state["mode"]})

    def _control_coding(self, action):
        state = self._coding_state()
        if action == "resume" and not state["busy"]:
            if state["record"] or state["last_prompt"]:
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
        self._coding_mode_selected("Onaylı Uygulama")
        self.coding_mode_combo.set("Onaylı Uygulama")
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
                content = "\n\n".join(f"### {'Siz' if m['role'] == 'user' else 'Evren' if m['role'] == 'assistant' else 'Bilgi'}\n\n{m['content']}" for m in state["messages"])
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
            if panes.winfo_width() < 1040:
                other = self.coding_right if side == "left" else self.coding_left
                if str(other) in tuple(map(str, panes.panes())):
                    panes.forget(other)
            kwargs = {"minsize": 170, "width": 200, "stretch": "never"}
            if side == "left":
                kwargs["before"] = self.coding_mid
            panes.add(widget, **kwargs)
        self._save_coding_preferences({f"show_{side}": not shown})

    def _coding_palette(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Komut Paleti")
        dialog.geometry("380x490")
        dialog.transient(self.winfo_toplevel())
        for title, callback in (("Sohbete Geç", lambda: self.coding_input.focus_set()),
                                ("Yeni Oturum", self._new_coding_session),
                                ("Oturum Geçmişi", self._coding_sessions),
                                ("Proje Dosyalarında Ara", lambda: self.coding_explorer.query.focus_set()),
                                ("Önceki İsteği Düzenle", lambda: self._edit_coding_message({"content": self._coding_state()["last_prompt"]})),
                                ("Yanıtı Durdur", lambda: self._control_coding("cancel")),
                                ("İzinleri Düzenle", self._coding_permissions),
                                ("Görevleri Aç", lambda: self._switch_workspace_tab("tasks")),
                                ("Projeyi Editörde Aç", self._open_coding_editor)):
            ctk.CTkButton(dialog, text=title, command=lambda fn=callback: (dialog.destroy(), fn())).pack(fill="x", padx=12, pady=6)

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
        configured = list(dict.fromkeys(["evren", "llmtr", "openai", *self.service.config.get("providers", {})]))
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
        ctk.CTkLabel(scroll, text="Terminal/test ve MCP çağrıları işlem başına onay ister.", wraplength=420).pack(anchor="w", pady=8)
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
            state["permissions"] = {key: var.get() for key, var in permission_vars.items()}
            self._save_coding_preferences({"permissions": state["permissions"],
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
