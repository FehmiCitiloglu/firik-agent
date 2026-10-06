"""Proje kapsamındaki kodlama geçmişini gösterir ve pasif olarak geri yükler."""
from __future__ import annotations

from copy import deepcopy
import json
from tkinter import messagebox

import customtkinter as ctk

from firik_agent.core.session import ChatSession
from firik_agent.projects.coding_sessions import redact_sensitive
from firik_agent.ui.coding_strings import STATUS_NAMES, WELCOME


def restore_state_history(state, record, events=None):
    """Restore recorded evidence; historical diffs do not assert current bytes."""
    if record is None:
        return state
    record = type(record).model_validate(redact_sensitive(record.model_dump(mode="json")))
    state["record"] = record
    state["task_id"] = record.task_id
    state["last_prompt"] = record.user_request
    state["status"] = STATUS_NAMES.get(record.status, "Kayıtlı çalışma")
    state["usage"] = deepcopy(record.usage)
    context = deepcopy(record.context_report)
    state["context_report"] = context if isinstance(context, list) else list(context.values()) if all(isinstance(v, dict) for v in context.values()) else []
    state["decisions"] = deepcopy(record.decisions)
    state["tests"] = deepcopy(record.tests_executed)
    state["record_journal"] = deepcopy(record.file_journal)
    state["capabilities"] = next((deepcopy(point["capabilities"]) for point in reversed(record.checkpoints)
                                  if isinstance(point.get("capabilities"), dict)), state.get("capabilities", {}))
    try:
        parsed = json.loads(record.plan) if record.plan else []
        state["plan"] = parsed if isinstance(parsed, list) else [{"title": str(parsed), "status": "pending"}]
    except (ValueError, TypeError):
        state["plan"] = [{"title": record.plan, "status": "pending"}]
    state["messages"] = [{"role": m["role"], "content": str(m.get("content") or "")}
                         for m in record.messages if m.get("role") in {"user", "assistant"} and m.get("content")]
    if not state["messages"]:
        if record.user_request:
            state["messages"].append({"role": "user", "content": record.user_request})
        if record.summary:
            state["messages"].append({"role": "assistant", "content": record.summary})
    if not state["messages"]:
        state["messages"] = [{"role": "system", "content": WELCOME}]
    state["messages"] = redact_sensitive(state["messages"])
    state["text"] = "\n\n".join(m["content"] for m in state["messages"])
    changes, paths = {}, {}
    for entry in record.file_journal:
        path = entry.get("path") or entry.get("file_path")
        if not path:
            continue
        paths[path] = deepcopy(entry)
        status = {"created": "added", "deleted": "deleted", "modified": "modified"}.get(entry.get("kind"), entry.get("status", "modified"))
        if status not in {"added", "deleted", "modified"}:
            status = "modified"
        diff = str(entry.get("diff") or "")
        data_lines = [line for line in diff.splitlines() if not line.startswith(("+++ ", "--- "))]
        current = changes.setdefault(path, {"path": path, "status": status, "diff": "", "added": 0, "removed": 0,
            "origin": "Ajan (kalıcı kayıt)", "historical": True, "can_undo": False})
        current["diff"] += ("\n" if current["diff"] else "") + diff
        current["added"] += sum(line.startswith("+") for line in data_lines)
        current["removed"] += sum(line.startswith("-") for line in data_lines)
        current["undone"] = bool(entry.get("undone"))
        if entry.get("undone"):
            current["origin"] = "Geri alınan ajan kaydı"
    state["changes"] = list(changes.values())
    state["agent_paths"] = paths
    state["change_counts"] = {status: sum(c["status"] == status for c in state["changes"])
                               for status in ("added", "modified", "deleted")}
    state["activities"] = []
    state["test_counts"] = {"passed": 0, "failed": 0, "unexecuted": 0}
    for result in record.tests_executed:
        executed = result.get("executed", result.get("exit_code") is not None)
        passed = executed and result.get("exit_code") == 0 and not result.get("timed_out")
        status = "passed" if passed else "failed" if executed else "unexecuted"
        state["test_counts"][status] += 1
        label = {"passed": "Tamamlandı", "failed": "Hata", "unexecuted": "Çalıştırılmadı"}[status]
        output = str(result.get("output") or "")
        if result.get("exit_code") is not None:
            output += f"\nGerçek çıkış kodu: {result['exit_code']}"
        state["activities"].append({"tool": result.get("command", "Kayıtlı test"), "status": label,
            "output": output, "duration_ms": result.get("duration_ms"), "historical": True})
    for decision in record.decisions:
        if decision.get("kind") == "subagent":
            continue
        approved = decision.get("approved", decision.get("allowed"))
        state["activities"].append({"tool": "Kullanıcı kararı · " + str(decision.get("tool_name", decision.get("kind", "Karar"))),
            "status": "Onaylandı" if approved is True else "Reddedildi" if approved is False else "Kaydedildi",
            "output": json.dumps(decision, ensure_ascii=False), "duration_ms": None, "historical": True})
    for checkpoint in record.checkpoints:
        if checkpoint.get("kind") == "interrupted":
            state["activities"].append({"tool": "Kesinti kontrol noktası", "status": "Kesildi",
                "output": "Önceki durum: " + str(checkpoint.get("previous_status", "bilinmiyor")),
                "duration_ms": None, "historical": True})
    from firik_agent.ui.subagent_activity import update_subagent_activity
    for decision in record.decisions:
        if decision.get("kind") == "subagent" and decision.get("agent_id"):
            update_subagent_activity(state["activities"], decision, decision.get("result", ""), historical=True)
    state["activities"] = redact_sensitive(state["activities"])
    state["before"] = state["snapshot"] = None
    state["selected_change"] = state["changes"][0]["path"] if state["changes"] else None
    state["pending_approvals"] = {}
    state["busy"] = state["paused"] = state["stopped"] = False
    state["future"] = None
    state["inspection_warning"] = "Kalıcı oturum kaydı gösteriliyor; dosyaların güncel durumu henüz yeniden karşılaştırılmadı."
    if record.status == "interrupted":
        state["inspection_warning"] += " Önceki çalışma kesildi; devam etmeden önce dosyalar yeniden doğrulanacak."
    from firik_agent.projects.cockpit import CockpitState
    cockpit = state.setdefault("cockpit", CockpitState())
    cockpit.restore(record, events)
    return state


class CodingHistoryMixin:
    def _new_coding_session(self):
        state = self._coding_state()
        if state["busy"]:
            messagebox.showinfo("Yeni Oturum", "Yeni oturum açmadan önce çalışan ajanı durdurun.", parent=self)
            return
        session = ChatSession(model=state["session"].model, active_mcp_servers=set())
        self.service.sessions[session.session_id] = session
        self.service._coding_session_projects[session.session_id] = self.current_project_id
        state.update(session=session, record=None, busy=False, messages=[{"role": "system", "content": WELCOME}], text=WELCOME,
                     status="Hazır", task_id=None, plan=[], activities=[], changes=[], record_journal=[], agent_paths={},
                     context_report=[], usage={}, decisions=[], tests=[], change_counts={}, test_counts={}, before=None,
                     snapshot=None, selected_change=None, inspection_warning="", pending_approvals={}, last_prompt="",
                     paused=False, stopped=False, future=None, documents=[])
        state["run_id"] += 1
        state["cockpit"].start(model=session.model)
        self._switch_workspace_tab("coding")

    def _load_coding_history(self, run_id):
        if self._coding_state()["busy"]:
            return False
        session, record = self.service.restore_coding(self.current_project_id, run_id=run_id)
        state = self._coding_state()
        state["session"] = session
        state["run_id"] += 1
        restore_state_history(state, record, self.service.coding_store.list_events(
            record.id, project_id=self.current_project_id, limit=1000, include_deltas=False))
        tool_filter = self.service._coding_filters.get(session.session_id)
        if tool_filter is not None and tool_filter.control.run_id == record.run_id:
            for change in state["changes"]:
                journal = state["agent_paths"].get(change["path"], {})
                if not journal.get("undone") and not journal.get("redacted"):
                    change.update(origin="Ajan", can_undo=True)
        self._switch_workspace_tab("coding")
        return True

    def _coding_sessions(self):
        if self._coding_state()["busy"]:
            messagebox.showinfo("Oturumlar", "Oturum değiştirmeden önce çalışan ajanı durdurun.", parent=self)
            return
        project_id = self.current_project_id
        records = self.service.coding_store.list_runs(project_id)
        dialog = ctk.CTkToplevel(self)
        dialog.title("Projenin Kodlama Oturumları")
        dialog.geometry("700x530")
        dialog.transient(self.winfo_toplevel())
        self.coding_history_dialog = dialog
        ctk.CTkLabel(dialog, text="Bir kaydı açmak ajanı başlatmaz. Devam işlemi ayrıca seçilir.", wraplength=650).pack(padx=12, pady=8)
        scroll = ctk.CTkScrollableFrame(dialog)
        scroll.pack(fill="both", expand=True, padx=10, pady=5)
        if not records:
            ctk.CTkLabel(scroll, text="Bu projede kayıtlı oturum yok.").pack(pady=20)
        def choose(identifier):
            if self.current_project_id != project_id:
                dialog.destroy()
                return
            try:
                if self._load_coding_history(identifier):
                    dialog.destroy()
            except ValueError as error:
                messagebox.showerror("Oturum", str(error), parent=dialog)
        for record in records:
            frame = ctk.CTkFrame(scroll)
            frame.pack(fill="x", pady=4)
            status = STATUS_NAMES.get(record.status, record.status)
            text = f"{record.title} · {status}\n{record.created_at[:19]} · {record.model or 'Model kaydı yok'}\n{record.summary or record.user_request}"
            ctk.CTkLabel(frame, text=redact_sensitive(text)[:700], justify="left", anchor="w", wraplength=540).pack(side="left", fill="x", expand=True, padx=8, pady=6)
            ctk.CTkButton(frame, text="Kaydı Aç", width=85, command=lambda identifier=record.id: choose(identifier)).pack(side="right", padx=6)
        ctk.CTkButton(dialog, text="Yeni Oturum", command=lambda: (dialog.destroy(), self._new_coding_session())).pack(pady=8)
        return dialog
