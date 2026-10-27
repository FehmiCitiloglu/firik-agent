"""Bounded, event-backed views for the coding cockpit.

The runtime owns execution and approval. These views render recorded evidence
and send user actions through the same service boundary as the orchestrator.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, simpledialog
from typing import Any

import customtkinter as ctk

from firik_agent.projects.indexer import sanitize_secrets

VISIBLE_EVENTS = 60
VISIBLE_COMMANDS = 30
OUTPUT_LIMIT = 16000
STATUS_TEXT = {
    "running": "● Çalışıyor", "started": "● Çalışıyor", "completed": "✓ Tamamlandı",
    "success": "✓ Başarılı", "passed": "✓ Geçti", "failed": "! Hata", "error": "! Hata",
    "cancelled": "■ Durduruldu", "interrupted": "■ Kesildi", "waiting": "◷ Bekliyor",
    "pending": "○ Bekliyor", "blocked": "! Engellendi", "paused": "Ⅱ Duraklatıldı",
    "approved": "✓ Onaylandı", "denied": "× Reddedildi", "stale": "! Yeniden doğrulanmalı",
    "unexecuted": "○ Çalıştırılmadı", "idle": "○ Hazır", "created": "○ Oluşturuldu",
    "planning": "● Planlıyor", "queued": "◷ Sırada", "needs_approval": "◷ Onay bekliyor",
    "reviewing": "● İnceliyor", "testing": "● Doğruluyor", "accepted": "✓ Kabul edildi",
    "rejected": "↶ Geri alındı",
}
ROLE_TEXT = {"orchestrator": "Orkestratör", "worker": "Uygulayıcı", "researcher": "Araştırmacı",
             "research": "Araştırmacı", "reviewer": "İnceleyici", "review": "İnceleyici", "tester": "Test uzmanı"}
CATEGORY_TEXT = {
    "system": "Sistem", "project_rules": "Proje kuralları", "agent_instructions": "Ajan talimatları",
    "conversation": "Konuşma", "memory": "Hafıza", "selected_files": "Seçilen dosyalar",
    "retrieved_files": "Getirilen dosyalar", "tool_schemas": "Araç şemaları", "mcp": "MCP",
    "previous_task": "Önceki görev", "project_context": "Proje bağlamı",
}


def cockpit_value(state: dict, name: str, default: Any = None) -> Any:
    cockpit = state.get("cockpit")
    return cockpit.get(name, default) if isinstance(cockpit, dict) else getattr(cockpit, name, default)


def completion_accounting(state: dict) -> tuple[dict, dict]:
    """New aggregates carry coverage; legacy sessions retain their old fields."""
    usage = state.get("usage", {})
    record = state.get("record")
    persisted = getattr(record, "usage", {}).get("harness_metrics", {}) if record else {}
    metrics = persisted or state.get("harness_metrics") or usage.get("harness_metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("usage"), dict):
        return metrics.get("usage", {}), metrics
    return usage, {}


def update_accounting_state(state: dict, event: Any) -> None:
    """Refresh aggregate displays only when accounting evidence can change."""
    kind = str(getattr(event.type, "value", event.type))
    deltas = {"text_delta", "reasoning_delta", "tool_call_arguments"}
    if kind in deltas or kind == "subagent_event" and event.metadata.get("child_event_type") in deltas:
        return
    cockpit = state.get("cockpit")
    if not hasattr(cockpit, "metrics_snapshot"):
        return
    metrics = cockpit.metrics_snapshot()
    if cockpit.has_request_metrics or metrics.get("tool_calls"):
        state["harness_metrics"] = metrics
        state["usage"] = {**metrics["usage"], "harness_metrics": metrics}
    else:
        state["usage"] = {key: value for key, value in metrics["reported_usage"].items() if value is not None}


def reported_agent_usage(agent: dict) -> str:
    """Per-agent projections retain observed subtotals, not coverage proof."""
    total = (agent.get("usage") or {}).get("total_tokens")
    return f"Bildirilen token: {total} · Kapsam eksik olabilir" if total is not None else ""


def display_status(value: Any) -> str:
    return STATUS_TEXT.get(str(value), str(value or "○ Kaydedildi"))


def timeline_records(state: dict) -> list[dict]:
    """Legacy persisted runs stay inspectable without inventing live events."""
    records = cockpit_value(state, "timeline", [])
    if records:
        return list(records)[-VISIBLE_EVENTS:]
    return [{"id": f"history:{index}", "title": item.get("tool", "İşlem"),
             "event_type": "subagent_event" if item.get("agent_id") else "tool",
             "agent_id": item.get("agent_id", "orchestrator"), "status": item.get("status"),
             "tool": item.get("tool"), "arguments": item.get("arguments", {}),
             "result": item.get("output", ""), "duration_ms": item.get("duration_ms"),
             "historical": item.get("historical", False), "expanded": item.get("expanded", False)}
            for index, item in enumerate(state.get("activities", []))][-VISIBLE_EVENTS:]


def terminal_records(state: dict) -> list[dict]:
    records = cockpit_value(state, "terminal", [])
    if records:
        return list(records)[-VISIBLE_COMMANDS:]
    result = []
    for index, item in enumerate(state.get("activities", [])):
        arguments = item.get("arguments", {})
        if item.get("tool") in {"run_command", "run_tests", "run_shell"} and arguments.get("command"):
            result.append({"id": f"legacy-command:{index}", "command": arguments["command"],
                           "cwd": arguments.get("cwd", "."), "status": item.get("status"),
                           "output": item.get("output", ""), "duration_ms": item.get("duration_ms")})
    return result[-VISIBLE_COMMANDS:]


def test_records(state: dict) -> list[dict]:
    records = cockpit_value(state, "tests", []) or state.get("tests", [])
    return list(records)[-VISIBLE_EVENTS:]


def agent_depth(identifier: str, agents: dict) -> int:
    """Malformed historical parent links cannot hang the inspector."""
    seen = {identifier}
    depth = 0
    parent = agents.get(identifier, {}).get("parent_id")
    while parent and parent in agents and parent not in seen and depth < 8:
        seen.add(parent)
        depth += 1
        parent = agents[parent].get("parent_id")
    return depth


def context_preference_update(state: dict, path: str, action: str) -> dict:
    """Return one coherent preference change; exclusions override inclusion."""
    prefs = state.get("preferences", {})
    keys = ("context_paths", "pinned_files", "always_include_files", "excluded_files", "summarized_files")
    values = {key: list(dict.fromkeys(state.get(key, prefs.get(key, [])))) for key in keys}
    def remove(key):
        values[key] = [value for value in values[key] if value != path]
    def add(key):
        if path not in values[key]:
            values[key].append(path)
    if action == "remove":
        for key in ("context_paths", "pinned_files", "always_include_files"):
            remove(key)
    elif action == "never":
        for key in ("context_paths", "pinned_files", "always_include_files", "summarized_files"):
            remove(key)
        add("excluded_files")
    elif action in {"pin", "always", "summarize", "include"}:
        remove("excluded_files")
        add({"pin": "pinned_files", "always": "always_include_files", "summarize": "summarized_files",
             "include": "context_paths"}[action])
    else:
        raise ValueError("Geçersiz bağlam işlemi.")
    return values


class CodingEvidenceMixin:
    def _open_benchmark_summary(self):
        """Import an existing result for inspection; never start a trial."""
        from pathlib import Path
        from firik_agent.benchmarks.storage import load_report
        from firik_agent.projects.cockpit import benchmark_report_summary
        filename = filedialog.askopenfilename(title="Harness benchmark raporu", filetypes=[("JSON", "*.json")])
        if not filename:
            return
        try:
            summary = benchmark_report_summary(load_report(Path(filename)))
            cost = f"${summary['cost']:.6f} ({', '.join(summary['cost_sources']) or 'kaynak bilinmiyor'})" if summary["cost"] is not None else "Bilinmiyor"
            messagebox.showinfo("Harness Benchmark Lab",
                f"{summary['label']} · {summary['profile']}\n"
                f"Kontrol: {summary['fixtures_passed']}/{summary['trials']} · Doğrulanmış görev: {summary['verified_tasks']}\n"
                f"Pass@1: {summary['pass_at_1']:.1%} · P50/P95: {summary['p50_ms']:.1f}/{summary['p95_ms']:.1f} ms\n"
                f"Yanlış tamamlandı iddiası: {summary['false_completions']} · İzin ihlali: {summary['safety_violations']}\n"
                f"Maliyet: {cost}\n\n{summary['method']}")
        except (ValueError, OSError, TypeError, KeyError) as error:
            from firik_agent.projects.coding_sessions import redact_sensitive
            messagebox.showerror("Benchmark raporu okunamadı", redact_sensitive(str(error)))

    def _evidence_text(self, value, limit=OUTPUT_LIMIT):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        redact = getattr(self.service, "_coding_redact", sanitize_secrets)
        text = str(redact(value))
        return text[:limit] + ("\n… Görüntüleme sınırına ulaşıldı." if len(text) > limit else "")

    @staticmethod
    def _evidence_box(parent, text, height=140):
        box = ctk.CTkTextbox(parent, wrap="none", height=height,
                             font=ctk.CTkFont(family="monospace", size=12))
        box.pack(fill="both", expand=True, padx=6, pady=5)
        box.insert("end", text)
        box.configure(state="disabled")
        return box

    def _mount_coding_evidence(self):
        self._coding_evidence_cache = {}
        self._coding_evidence_loading = set()
        # The right pane owns a single notebook, including its existing task tab.
        self.coding_inspector_tabs = getattr(self, "coding_inspector_tabs", None) or ctk.CTkTabview(self.coding_inspector_host)
        if not self.coding_inspector_tabs.winfo_manager():
            self.coding_inspector_tabs.pack(fill="both", expand=True)
        for name in ("Ajanlar", "Bağlam", "Model", "Kayıtlar"):
            if name not in self.coding_inspector_tabs._tab_dict:
                self.coding_inspector_tabs.add(name)
        try:
            self.coding_inspector_tabs.set("Ajanlar", notify=False)
        except TypeError:
            self.coding_inspector_tabs.set("Ajanlar")
        self.coding_agent_list = ctk.CTkScrollableFrame(self.coding_inspector_tabs.tab("Ajanlar"))
        self.coding_agent_list.pack(fill="both", expand=True)
        self.coding_new_agent_btn = ctk.CTkButton(self.coding_inspector_tabs.tab("Ajanlar"), text="+ Yeni ajan",
                                                command=self._new_coding_agent)
        self.coding_new_agent_btn.pack(fill="x", pady=4)
        self.coding_context_list = ctk.CTkScrollableFrame(self.coding_inspector_tabs.tab("Bağlam"))
        self.coding_context_list.pack(fill="both", expand=True)
        self.coding_model_list = ctk.CTkScrollableFrame(self.coding_inspector_tabs.tab("Model"))
        self.coding_model_list.pack(fill="both", expand=True)
        self.coding_checkpoint_list = ctk.CTkScrollableFrame(self.coding_inspector_tabs.tab("Kayıtlar"))
        self.coding_checkpoint_list.pack(fill="both", expand=True)
        checkpoint_actions = ctk.CTkFrame(self.coding_inspector_tabs.tab("Kayıtlar"), fg_color="transparent")
        checkpoint_actions.pack(fill="x")
        self.coding_checkpoint_create_btn = ctk.CTkButton(checkpoint_actions, text="Kontrol noktası al", command=self._create_coding_checkpoint)
        self.coding_checkpoint_create_btn.pack(fill="x", pady=3)
        ctk.CTkButton(checkpoint_actions, text="Kayıtları yenile", command=self._load_coding_checkpoints).pack(fill="x", pady=3)
        # The timeline remains the existing activity pane and can render old runs.
        activity = self.coding_tabs.tab("Etkinlik" if "Etkinlik" in self.coding_tabs._tab_dict else "İşlemler")
        self.coding_activity_list.grid(row=1, column=0, sticky="nsew")
        activity.grid_rowconfigure(0, weight=0)
        activity.grid_rowconfigure(1, weight=1)
        self.coding_evidence_header = ctk.CTkFrame(activity, fg_color="transparent")
        self.coding_evidence_header.grid(row=0, column=0, sticky="ew")
        self.coding_evidence_header.grid_columnconfigure(0, weight=1)
        self.coding_completion_label = ctk.CTkLabel(self.coding_evidence_header, text="", anchor="w", justify="left", wraplength=560)
        self.coding_completion_label.grid(row=0, column=0, sticky="ew", padx=6)
        self.coding_event_filter = ctk.CTkComboBox(self.coding_evidence_header,
            values=["Tüm işlemler", "Araçlar", "Dosyalar", "MCP / Ağ", "Onaylar", "Ajanlar", "Hatalar"],
            state="readonly", width=145, command=lambda value: self._refresh_coding_activity())
        self.coding_event_filter.set("Tüm işlemler")
        self.coding_event_filter.grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ctk.CTkButton(self.coding_evidence_header, text="Benchmark raporu", width=130, height=26,
                     command=self._open_benchmark_summary).grid(row=1, column=1, padx=6, pady=4)
        self.coding_completion_actions = ctk.CTkFrame(self.coding_evidence_header, fg_color="transparent")
        self.coding_completion_actions.grid(row=2, column=0, sticky="w", padx=4, pady=3)
        for title, action in (("Farkı incele", lambda: self.coding_tabs.set("Değişiklikler")),
                              ("Git", lambda: self._switch_workspace_tab("git")),
                              ("Devam et", lambda: self._control_coding("resume"))):
            ctk.CTkButton(self.coding_completion_actions, text=title, width=80, height=25, command=action).pack(side="left", padx=2)
        for name in ("Terminal", "Testler"):
            if name not in self.coding_tabs._tab_dict:
                self.coding_tabs.add(name)
        terminal = self.coding_tabs.tab("Terminal")
        terminal.grid_columnconfigure(0, weight=1)
        terminal.grid_rowconfigure(1, weight=1)
        command_bar = ctk.CTkFrame(terminal, fg_color="transparent")
        command_bar.grid(row=0, column=0, sticky="ew", pady=4)
        command_bar.grid_columnconfigure(0, weight=1)
        self.coding_terminal_entry = ctk.CTkEntry(command_bar, placeholder_text="Proje dizininde komut çalıştır…")
        self.coding_terminal_entry.grid(row=0, column=0, sticky="ew", padx=4)
        self.coding_terminal_run_btn = ctk.CTkButton(command_bar, text="Çalıştır", width=85, command=self._run_coding_terminal)
        self.coding_terminal_run_btn.grid(row=0, column=1, padx=4)
        self.coding_terminal_entry.bind("<Return>", lambda event: self._run_coding_terminal())
        self.coding_terminal_list = ctk.CTkScrollableFrame(terminal)
        self.coding_terminal_list.grid(row=1, column=0, sticky="nsew")
        tests = self.coding_tabs.tab("Testler")
        tests.grid_rowconfigure(1, weight=1)
        tests.grid_columnconfigure(0, weight=1)
        self.coding_quality_gate_list = ctk.CTkFrame(tests, fg_color="transparent")
        self.coding_quality_gate_list.grid(row=0, column=0, sticky="ew")
        self.coding_test_list = ctk.CTkScrollableFrame(tests)
        self.coding_test_list.grid(row=1, column=0, sticky="nsew")
        self.coding_review_checkpoint_btn = ctk.CTkButton(self.coding_change_footer,
            text="Blok İncele", width=80, height=26, command=self._coding_checkpoints)
        self.coding_review_checkpoint_btn.grid(row=1, column=2, sticky="ew", padx=2, pady=2)
        self._load_coding_quality_gates()
        self._load_coding_checkpoints()
        self._refresh_coding_evidence()

    def _sync_evidence_cards(self, key, parent, records, renderer, empty):
        cache = self._coding_evidence_cache.setdefault(key, {})
        keep = set()
        expanded = self._coding_state().setdefault("evidence_expanded", set())
        for index, record in enumerate(records):
            identifier = str(record.get("id", index))
            keep.add(identifier)
            fingerprint = (json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
                           + str((key, identifier) in expanded) + str(self._coding_state()["busy"]))
            current = cache.get(identifier)
            if current is None or current[0] != fingerprint:
                if current:
                    current[1].destroy()
                card = ctk.CTkFrame(parent)
                renderer(card, record, identifier, (key, identifier) in expanded)
                cache[identifier] = (fingerprint, card)
            cache[identifier][1].pack_forget()
            cache[identifier][1].pack(fill="x", pady=3)
        for identifier in list(cache):
            if identifier not in keep:
                cache.pop(identifier)[1].destroy()
        label = getattr(parent, "_evidence_empty_label", None)
        if records:
            if label:
                label.pack_forget()
        else:
            if label is None:
                label = ctk.CTkLabel(parent, text=empty, anchor="w", justify="left", wraplength=420)
                parent._evidence_empty_label = label
            label.pack(fill="x", padx=5, pady=12)

    def _toggle_coding_evidence(self, key, identifier):
        expanded = self._coding_state().setdefault("evidence_expanded", set())
        item = (key, identifier)
        if item in expanded:
            expanded.remove(item)
        else:
            expanded.add(item)
        # Expansion state is bounded independently of the durable audit log.
        if len(expanded) > VISIBLE_EVENTS:
            expanded.pop()
        self._refresh_coding_evidence()

    def _evidence_record_title(self, record):
        timestamp = record.get("timestamp")
        try:
            stamp = datetime.fromtimestamp(float(timestamp), timezone.utc).astimezone().strftime("%H:%M:%S") + " · " if timestamp else ""
        except (ValueError, TypeError, OverflowError):
            stamp = ""
        duration = record.get("duration_ms")
        duration_text = f" · {duration / 1000:.2f} sn" if isinstance(duration, (int, float)) else ""
        return self._evidence_text(f"{stamp}{record.get('title') or record.get('tool') or record.get('event_type', 'İşlem')}\n"
                                   f"{record.get('agent_id', 'orchestrator')} · {display_status(record.get('status'))}{duration_text}", 600)

    def _render_coding_event(self, card, record, identifier, expanded):
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=3)
        header.grid_columnconfigure(0, weight=1)
        failed = record.get("error") or record.get("status") in {"failed", "error", "Hata", "blocked"}
        ctk.CTkLabel(header, text=self._evidence_record_title(record), anchor="w", justify="left", wraplength=460,
                     text_color="#f87171" if failed else ("gray15", "gray90")).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(header, text="Kapat" if expanded else "Aç", width=48, height=26,
                     command=lambda: self._toggle_coding_evidence("activity", identifier)).grid(row=0, column=1, padx=3)
        ctk.CTkButton(header, text="İncele", width=55, height=26,
                     command=lambda: self._show_coding_activity(record)).grid(row=0, column=2)
        details = record.get("command") or record.get("file") or record.get("server")
        if details:
            ctk.CTkLabel(card, text=self._evidence_text(details, 500), anchor="w", justify="left", wraplength=520).pack(fill="x", padx=8)
        if failed:
            ctk.CTkLabel(card, text=self._evidence_text(record.get("error") or record.get("result") or "İşlem başarısız.", 600),
                         anchor="w", justify="left", wraplength=520).pack(fill="x", padx=8, pady=3)
            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=6, pady=3)
            if record.get("command"):
                ctk.CTkButton(actions, text="Komutu yeniden çalıştır", height=25,
                             command=lambda: self._execute_coding_command(record["command"], cwd=record.get("cwd", "."))).pack(side="left", padx=2)
            ctk.CTkButton(actions, text="Ajana sor", width=80, height=25,
                         command=lambda: self._ask_coding_evidence(record)).pack(side="left", padx=2)
            if "approval" in str(record.get("event_type")) or "permission" in str(record.get("error", "")).lower():
                ctk.CTkButton(actions, text="İzinler", width=65, height=25, command=self._coding_permissions).pack(side="left", padx=2)
        if expanded:
            text = {"parametreler": record.get("arguments", {}), "sonuç": record.get("result", ""),
                    "hata": record.get("error"), "dosya": record.get("file"), "tür": record.get("event_type")}
            self._evidence_box(card, self._evidence_text(text))

    def _refresh_coding_activity(self):
        if not hasattr(self, "coding_event_filter"):
            return
        records = timeline_records(self._coding_state())
        selected = self.coding_event_filter.get()
        predicates = {
            "Araçlar": lambda e: "tool" in e.get("event_type", ""),
            "Dosyalar": lambda e: bool(e.get("file")) or "file" in e.get("event_type", ""),
            "MCP / Ağ": lambda e: bool(e.get("server")) or any(s in e.get("event_type", "") for s in ("mcp", "network")),
            "Onaylar": lambda e: "approval" in e.get("event_type", ""),
            "Ajanlar": lambda e: "agent" in e.get("event_type", ""),
            "Hatalar": lambda e: bool(e.get("error")) or e.get("status") in {"failed", "error", "Hata", "blocked"},
        }
        if selected in predicates:
            records = [record for record in records if predicates[selected](record)]
        self._sync_evidence_cards("activity", self.coding_activity_list, records, self._render_coding_event,
                                  "Ajanın gerçek araç, dosya, ağ, MCP ve onay işlemleri burada görünür.")
        quick_actions = getattr(self.coding_activity_list, "_coding_quick_actions", None)
        if not timeline_records(self._coding_state()) and not self._coding_state()["busy"]:
            if quick_actions is None:
                from firik_agent.ui.coding_strings import QUICK_ACTIONS
                quick_actions = ctk.CTkFrame(self.coding_activity_list, fg_color="transparent")
                self.coding_activity_list._coding_quick_actions = quick_actions
                for label, prompt in QUICK_ACTIONS:
                    ctk.CTkButton(quick_actions, text=label, anchor="w", fg_color="transparent", border_width=1,
                                 text_color=("gray15", "gray90"),
                                 command=lambda value=prompt: self._coding_quick_action(value)).pack(fill="x", pady=3)
            quick_actions.pack(fill="x", padx=5, pady=5)
        elif quick_actions:
            quick_actions.pack_forget()
        self._refresh_coding_completion()

    def _show_coding_activity(self, activity):
        dialog = ctk.CTkToplevel(self)
        dialog.title(activity.get("title") or activity.get("tool") or "İşlem kanıtı")
        dialog.geometry("850x580")
        dialog.transient(self.winfo_toplevel())
        self._evidence_box(dialog, self._evidence_text(activity, 64000), height=400)
        return dialog

    def _ask_coding_evidence(self, record):
        self.coding_input.delete(0, "end")
        self.coding_input.insert(0, "Bu işlemi ve hatanın çözümünü incele:\n" + self._evidence_text(record, 8000))
        self.coding_input.focus_set()

    def _render_coding_command(self, card, record, identifier, expanded):
        ctk.CTkLabel(card, text=self._evidence_text("$ " + str(record.get("command", "")), 1000), anchor="w",
                     justify="left", wraplength=520, font=ctk.CTkFont(family="monospace", size=12)).pack(fill="x", padx=8, pady=4)
        detail = f"{display_status(record.get('status'))} · {record.get('agent_id', 'orchestrator')} · Dizin: {record.get('cwd', '.')}"
        if record.get("exit_code") is not None:
            detail += f" · Çıkış: {record['exit_code']}"
        if isinstance(record.get("duration_ms"), (int, float)):
            detail += f" · {record['duration_ms'] / 1000:.2f} sn"
        ctk.CTkLabel(card, text=detail, anchor="w", justify="left", wraplength=520).pack(fill="x", padx=8)
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=6, pady=3)
        ctk.CTkButton(actions, text="Çıktıyı kapat" if expanded else "Çıktıyı aç", height=25, width=100,
                     command=lambda: self._toggle_coding_evidence("terminal", identifier)).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Yeniden çalıştır", width=110, height=25,
                     state="disabled" if self._coding_state()["busy"] else "normal",
                     command=lambda: self._execute_coding_command(record.get("command", ""), cwd=record.get("cwd", "."))).pack(side="left", padx=2)
        if expanded:
            self._evidence_box(card, self._evidence_text(record.get("output") or "Araç çıktısı henüz gelmedi."))

    def _render_coding_test(self, card, record, identifier, expanded):
        status = "stale" if record.get("stale") else record.get("status")
        if not status:
            status = "passed" if record.get("passed") else "failed" if record.get("exit_code") is not None else "unexecuted"
        ctk.CTkLabel(card, text=self._evidence_text(f"{record.get('gate_kind', record.get('kind', 'Test'))} · {display_status(status)}\n{record.get('command') or record.get('criterion', '')}", 1000),
                     anchor="w", justify="left", wraplength=520).pack(fill="x", padx=8, pady=4)
        detail = ""
        if record.get("exit_code") is not None:
            detail += f"Gerçek çıkış kodu: {record['exit_code']}"
        if record.get("evidence_id"):
            detail += f" · Kanıt: {record['evidence_id']}"
        if detail:
            ctk.CTkLabel(card, text=detail, anchor="w", wraplength=520).pack(fill="x", padx=8)
        ctk.CTkButton(card, text="Kanıtı kapat" if expanded else "Kanıtı aç", width=105, height=25,
                     command=lambda: self._toggle_coding_evidence("tests", identifier)).pack(anchor="w", padx=8, pady=3)
        if expanded:
            self._evidence_box(card, self._evidence_text(record.get("output") or record.get("detail") or record))

    def _refresh_coding_control(self):
        super()._refresh_coding_control()
        if hasattr(self, "coding_terminal_list"):
            self._refresh_coding_evidence()

    def _refresh_coding_evidence(self):
        if not hasattr(self, "coding_terminal_list") or not self.coding_terminal_list.winfo_exists():
            return
        state = self._coding_state()
        self._refresh_coding_activity()
        self._sync_evidence_cards("terminal", self.coding_terminal_list, terminal_records(state), self._render_coding_command,
                                 "Ajanın gerçek komutları ve çıktıları burada görünür. Komut çalıştırma runtime izinlerine tabidir.")
        self._sync_evidence_cards("tests", self.coding_test_list, test_records(state), self._render_coding_test,
                                 "Henüz çalıştırılmış test veya kalite kontrolü yok. Çalıştırılmayan kontroller başarılı sayılmaz.")
        self.coding_terminal_run_btn.configure(state="disabled" if state["busy"] else "normal")
        self.coding_new_agent_btn.configure(state="normal")
        self.coding_checkpoint_create_btn.configure(state="disabled" if state["busy"] else "normal")
        self._refresh_coding_agents()
        self._refresh_coding_context_inspector()
        self._refresh_coding_model_inspector()
        busy = state["busy"]
        if getattr(self, "_coding_evidence_was_busy", False) and not busy:
            self._load_coding_checkpoints()
        self._coding_evidence_was_busy = busy

    def _refresh_coding_completion(self):
        state = self._coding_state()
        summary = cockpit_value(state, "summary", {}) or {}
        record = state.get("record")
        has_run = bool(summary or record or state.get("last_prompt"))
        if state["busy"] or not has_run:
            self.coding_completion_label.configure(text="Canlı işlem günlüğü · Son 60 olay gösterilir; kalıcı denetim kaydı oturumda saklanır.")
            self.coding_completion_actions.grid_remove()
            return
        changes = state.get("changes", [])
        tests = test_records(state)
        passed = sum(bool(test.get("passed")) and not test.get("stale") for test in tests)
        failed = sum(test.get("exit_code") is not None and not test.get("passed") for test in tests)
        stale = sum(bool(test.get("stale")) for test in tests)
        agents = cockpit_value(state, "agents", {}) or {}
        usage, metrics = completion_accounting(state)
        tokens = usage.get("total_tokens")
        cost = usage.get("cost") if metrics else summary.get("cost", usage.get("cost"))
        detail = f"{state.get('status', 'Çalışma kaydı')}\n{len(changes)} dosya · +{sum(c.get('added', 0) for c in changes)} / −{sum(c.get('removed', 0) for c in changes)} satır"
        detail += f" · Doğrulama: {passed} geçti, {failed} hata" if tests else " · Doğrulama çalıştırılmadı"
        if stale:
            detail += f", {stale} yeniden doğrulanmalı"
        detail += f"\nAjan: {len(agents) or 1} · Token: {tokens if tokens is not None else 'Bilinmiyor' if metrics else 'Sağlayıcı bildirmedi'}"
        detail += (f" · Tahmini maliyet: ${cost:.4f}" if isinstance(cost, (float, int)) else
                   " · Maliyet bilinmiyor" if metrics else " · Fiyat bilgisi sağlayıcı tarafından sunulmuyor")
        if metrics:
            detail += f"\nModel isteği: {metrics.get('request_count', 0)} · Araç çağrısı: {metrics.get('tool_calls', 0)}"
            detail += f" · İstek hatası: {metrics.get('failed_requests', 0)} · Araç hatası: {metrics.get('tool_errors', 0)}"
            if not metrics.get("usage_complete") or not metrics.get("cost_complete"):
                detail += " · Kullanım/maliyet kaydı eksik"
        duration = summary.get("duration_sec", summary.get("elapsed_sec"))
        if isinstance(duration, (int, float)):
            detail += f" · Süre: {duration:.1f} sn"
        self.coding_completion_label.configure(text=detail)
        self.coding_completion_actions.grid()

    def _refresh_coding_agents(self):
        state = self._coding_state()
        agents = cockpit_value(state, "agents", {}) or {}
        if not agents:
            agents = {"orchestrator": {"id": "orchestrator", "role": "orchestrator", "status": "running" if state["busy"] else "idle",
                                      "task": state.get("last_prompt", ""), "model": state["session"].model,
                                      "provider": state.get("preferences", {}).get("provider", self.service.provider),
                                      "usage": state.get("usage", {})}}
        records = sorted(agents.values(), key=lambda agent: (agent_depth(agent.get("id", ""), agents), agent.get("id", "")))[:40]
        def render(card, agent, identifier, expanded):
            depth = agent_depth(identifier, agents)
            title = "  " * depth + ("↳ " if depth else "") + str(agent.get("name") or ROLE_TEXT.get(agent.get("role"), agent.get("role", identifier)))
            ctk.CTkLabel(card, text=title + "\n" + display_status(agent.get("status")), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=4)
            if agent.get("task"):
                ctk.CTkLabel(card, text=self._evidence_text(agent["task"], 300), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6)
            model = str(agent.get("model") or state["session"].model)
            ctk.CTkLabel(card, text=self._evidence_text(f"{agent.get('provider') or self.service.provider} · {model}", 220),
                         anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6)
            usage_label = reported_agent_usage(agent)
            if usage_label:
                ctk.CTkLabel(card, text=usage_label, anchor="w", wraplength=230).pack(fill="x", padx=6)
            ctk.CTkButton(card, text="Ajanı incele", height=25, command=lambda: self._inspect_coding_agent(agent)).pack(fill="x", padx=6, pady=4)
        self._sync_evidence_cards("agents", self.coding_agent_list, records, render, "Henüz ajan çalışması başlamadı.")

    def _inspect_coding_agent(self, agent):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Ajan · " + str(agent.get("name") or agent.get("id")))
        dialog.geometry("700x580")
        dialog.transient(self.winfo_toplevel())
        self._evidence_box(dialog, self._evidence_text(agent), height=340)
        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(fill="x", padx=8, pady=5)
        if self._coding_state()["busy"]:
            ctk.CTkButton(actions, text="Yönlendirme gönder", command=lambda: self._steer_coding_agent(agent["id"])).pack(side="left", padx=4)
            ctk.CTkButton(actions, text="Ajanı durdur", fg_color="#9b2c2c", hover_color="#7f1d1d",
                         command=lambda: self._control_coding("cancel") if agent.get("id") == "orchestrator" else self._cancel_coding_agent(agent["id"])).pack(side="right", padx=4)
        return dialog

    def _new_coding_agent(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Yeni alt ajan")
        dialog.geometry("550x530")
        roles = {"Uygulayıcı": "worker", "Araştırmacı": "researcher", "İnceleyici": "reviewer", "Test uzmanı": "tester"}
        role = ctk.CTkComboBox(dialog, values=list(roles), state="readonly")
        role.set("Araştırmacı")
        role.pack(fill="x", padx=12, pady=8)
        task = ctk.CTkTextbox(dialog, height=130)
        task.pack(fill="both", expand=True, padx=12, pady=5)
        model = ctk.CTkEntry(dialog, placeholder_text="Model kimliği (boş: ana ajanın modeli)")
        model.pack(fill="x", padx=12, pady=5)
        provider = ctk.CTkComboBox(dialog, values=list(dict.fromkeys([self.service.provider, *self.service.config.get("providers", {})])), state="readonly")
        provider.set(self._coding_state().get("preferences", {}).get("provider", self.service.provider))
        provider.pack(fill="x", padx=12, pady=5)
        write_paths = ctk.CTkEntry(dialog, placeholder_text="Yazma kapsamı (virgülle ayırın): src/, tests/")
        write_paths.pack(fill="x", padx=12, pady=5)
        ctk.CTkLabel(dialog, text="Alt ajan ana çalışmanın izinlerini ve kapsam sınırlarını kullanır.", wraplength=480).pack(pady=5)
        state, project_id, session_id, generation = self._coding_action_identity()
        def spawn():
            value = task.get("1.0", "end").strip()
            if not value or not self._coding_action_current(state, project_id, session_id, generation):
                return
            paths = [path.strip() for path in write_paths.get().split(",") if path.strip()]
            selected_role = roles[role.get()]
            selected_model = model.get().strip() or None
            selected_provider = provider.get()
            if state["busy"]:
                self.service.spawn_coding_agent_async(session_id, selected_role, value,
                    on_success=lambda result: self._coding_action_refresh(state, project_id),
                    on_error=lambda error: self._coding_action_error(error, state, project_id), model=selected_model,
                    provider=selected_provider, write_paths=paths)
            else:
                self._start_coding_manual_action(f"{selected_role}: {value}",
                    lambda success, error, event: self.service.spawn_coding_agent_async(session_id, selected_role, value,
                        on_success=success, on_error=error, on_event=event, model=selected_model,
                        provider=selected_provider, write_paths=paths))
            dialog.destroy()
        ctk.CTkButton(dialog, text="Ajanı başlat", command=spawn).pack(pady=8)
        return dialog

    # Public palette/keyboard entry points share the same real action handlers.
    def _coding_create_agent(self):
        return self._new_coding_agent()

    def _coding_steer(self):
        self._show_coding_inspector("Ajanlar")
        agents = cockpit_value(self._coding_state(), "agents", {}) or {}
        if len(agents) <= 1:
            return self._steer_coding_agent("orchestrator")
        identifier = simpledialog.askstring("Ajan seç", "Yönlendirilecek ajan kimliği:\n" + ", ".join(agents), parent=self)
        if identifier in agents:
            return self._steer_coding_agent(identifier)

    def _coding_run_command(self):
        self.coding_tabs.set("Terminal")
        self.coding_terminal_entry.focus_set()

    def _coding_run_quality_gates(self):
        self.coding_tabs.set("Testler")
        self._load_coding_quality_gates()

    def _coding_checkpoints(self):
        self._show_coding_inspector("Kayıtlar")
        self._load_coding_checkpoints()

    def _coding_context_inspector(self):
        self._show_coding_inspector("Bağlam")
        self._refresh_coding_context_inspector()

    def _coding_action_identity(self):
        state = self._coding_state()
        return state, self.current_project_id, state["session"].session_id, state["run_id"]

    def _coding_action_current(self, state, project_id, session_id, generation):
        return state["session"].session_id == session_id and state["run_id"] == generation and self.current_project_id == project_id

    def _coding_action_refresh(self, state, project_id):
        if self._coding_visible(project_id):
            self._refresh_coding_evidence()

    def _coding_action_error(self, error, state, project_id):
        state["inspection_warning"] = self._evidence_text(error, 1500)
        if self._coding_visible(project_id):
            messagebox.showerror("Ajan işlemi", state["inspection_warning"], parent=self)
            self._refresh_coding_evidence()

    def _steer_coding_agent(self, agent_id):
        state, project_id, session_id, generation = self._coding_action_identity()
        message = simpledialog.askstring("Ajanı yönlendir", "Bir sonraki işlem sınırında uygulanacak talimat:", parent=self)
        if message and self._coding_action_current(state, project_id, session_id, generation):
            self.service.steer_coding_agent_async(session_id, agent_id, message,
                on_success=lambda result: self._coding_action_refresh(state, project_id),
                on_error=lambda error: self._coding_action_error(error, state, project_id))

    def _cancel_coding_agent(self, agent_id):
        state, project_id, session_id, _generation = self._coding_action_identity()
        self.service.cancel_coding_agent_async(session_id, agent_id,
            on_success=lambda result: self._coding_action_refresh(state, project_id),
            on_error=lambda error: self._coding_action_error(error, state, project_id))

    def _run_coding_terminal(self):
        command = self.coding_terminal_entry.get().strip()
        if command:
            self._execute_coding_command(command)

    def _execute_coding_command(self, command, kind="shell", cwd="."):
        session_id = self._coding_state()["session"].session_id
        return self._start_coding_manual_action(command, lambda success, error, event:
            self.service.run_coding_command_async(session_id, command,
                on_success=success, on_error=error, kind=kind, cwd=cwd, on_event=event))

    def _start_coding_manual_action(self, task, invoke):
        state, project_id, session_id, generation = self._coding_action_identity()
        if state["busy"] or not task:
            return
        state["run_id"] += 1
        generation = state["run_id"]
        ended = False
        state.update(busy=True, stopped=False, status="İşlem izinleri değerlendiriliyor…", last_prompt=task, record=None,
                     before=None, snapshot=None, changes=[], agent_paths={}, usage={}, harness_metrics={})
        cockpit = state.get("cockpit")
        if hasattr(cockpit, "start"):
            cockpit.start(state["session"].model, state.get("preferences", {}).get("provider", self.service.provider), task)
        self._refresh_coding_control()
        def event(event):
            if ended or state["run_id"] != generation or state["session"].session_id != session_id:
                return
            cockpit = state.get("cockpit")
            if hasattr(cockpit, "apply"):
                cockpit.apply(event)
                update_accounting_state(state, event)
            value = event.type.value
            if value == "approval_required":
                request = dict(event.metadata, arguments=event.arguments or event.metadata.get("arguments", {}),
                               tool_name=event.tool_name or event.metadata.get("tool_name"))
                if request.get("request_id"):
                    state["pending_approvals"][request["request_id"]] = request
            elif value == "approval_decided":
                state["pending_approvals"].pop(event.metadata.get("request_id"), None)
            elif value == "file_changed" and event.metadata.get("path"):
                state["agent_paths"][event.metadata["path"]] = event.metadata
            elif value == "subagent_event" and event.metadata.get("child_event_type") == "file_changed":
                child = event.metadata.get("child_metadata", {})
                if child.get("path"):
                    state["agent_paths"][child["path"]] = child
            if self._coding_visible(project_id):
                self._refresh_coding_control()
        def done(result=None, error=None):
            nonlocal ended
            if ended or state["run_id"] != generation or state["session"].session_id != session_id:
                return
            ended = True
            state["busy"] = False
            state["pending_approvals"] = {}
            failed = bool(error) or isinstance(result, dict) and (result.get("passed") is False or result.get("status") == "failed"
                                                                 or result.get("exit_code") not in {None, 0})
            cancelled = isinstance(result, dict) and result.get("status") == "cancelled"
            state["status"] = "İşlem durduruldu" if cancelled else "İşlem hata ile sona erdi" if failed else "İşlem tamamlandı"
            if hasattr(cockpit, "finish"):
                cockpit.finish("cancelled" if cancelled else "failed" if failed else "completed", state.get("changes", []))
            if self._coding_visible(project_id):
                self._refresh_coding_control()
                if error:
                    self._coding_action_error(error, state, project_id)
            def inspected(snapshot):
                if state["run_id"] != generation or state["session"].session_id != session_id:
                    return
                self._apply_coding_snapshot(state, snapshot)
                if self._coding_visible(project_id):
                    self._refresh_coding_changes()
                    self._refresh_coding_evidence()
            self.service.inspect_workspace_async(project.local_path, inspected,
                lambda error: None, before=state["before"])
        def start(snapshot=None, inspection_error=None):
            if state["run_id"] != generation or state["session"].session_id != session_id:
                return
            if state.get("stopped"):
                done({"status": "cancelled"})
                return
            state["before"] = snapshot
            if inspection_error:
                state["inspection_warning"] = str(inspection_error)
            try:
                state["future"] = invoke(done, lambda error: done(error=error), event)
            except Exception as error:  # noqa: BLE001 -- service boundary must clear busy state on every failure
                done(error=str(error))
        project = self.project_service.db.get_project(project_id)
        state["preparing_future"] = self.service.inspect_workspace_async(project.local_path, start,
            lambda error: start(inspection_error=error))

    def _load_coding_quality_gates(self):
        from firik_agent.projects.quality_gates import (
            discover_quality_gates,
            validate_gates,
        )
        state, project_id, session_id, generation = self._coding_action_identity()
        project = self.project_service.db.get_project(project_id)
        async def discover():
            configured = state.get("preferences", {}).get("quality_gates")
            gates = await asyncio.to_thread(validate_gates, configured) if configured is not None else await asyncio.to_thread(discover_quality_gates, project.local_path)
            return [gate.to_dict() for gate in gates]
        def ready(values):
            state["quality_gates"] = values
            if not self._coding_action_current(state, project_id, session_id, generation) or not self.coding_quality_gate_list.winfo_exists():
                return
            for child in self.coding_quality_gate_list.winfo_children():
                child.destroy()
            ctk.CTkButton(self.coding_quality_gate_list, text="Kalite kapılarını düzenle", width=180, height=25,
                         command=self._configure_coding_quality_gates).pack(anchor="w", padx=4, pady=3)
            if any(gate.get("enabled", True) for gate in values):
                ctk.CTkButton(self.coding_quality_gate_list, text="Tüm etkin kalite kapılarını çalıştır", height=25,
                             command=self._execute_coding_quality_gates).pack(fill="x", padx=4, pady=3)
            if not values:
                ctk.CTkLabel(self.coding_quality_gate_list, text="Proje manifestinde kalite komutu bulunamadı. Terminalde gerçek komut çalıştırabilirsiniz.", wraplength=500).pack(anchor="w", padx=6)
            for gate in values:
                if gate.get("enabled", True):
                    ctk.CTkButton(self.coding_quality_gate_list, text=f"{gate['kind']} · {gate['command']}", anchor="w",
                        command=lambda value=gate: self._execute_coding_quality_gates([value])).pack(fill="x", padx=4, pady=2)
        self.service.runtime.submit(discover(), on_done=lambda values: self.service._dispatch(ready, values),
                                    on_error=lambda error: self.service._dispatch(ready, []))

    def _execute_coding_quality_gates(self, gates=None):
        state = self._coding_state()
        session_id = state["session"].session_id
        return self._start_coding_manual_action("Kalite kapılarını çalıştır", lambda success, error, event:
            self.service.run_coding_quality_gates_async(session_id, on_success=success, on_error=error,
                on_event=event, gates=gates if gates is not None else state.get("quality_gates")))

    def _configure_coding_quality_gates(self):
        from firik_agent.projects.quality_gates import validate_gates
        state, project_id, session_id, generation = self._coding_action_identity()
        if state["busy"]:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Kalite kapıları")
        dialog.geometry("780x580")
        ctk.CTkLabel(dialog, text="Komutlar runtime izinlerine tabidir. enabled kontrolü açar; required tamamlanmadan önce geçmesini zorunlu kılar.", wraplength=730).pack(padx=8, pady=8)
        box = ctk.CTkTextbox(dialog, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
        box.pack(fill="both", expand=True, padx=8, pady=5)
        box.insert("end", json.dumps(state.get("quality_gates", []), ensure_ascii=False, indent=2))
        def save():
            if not self._coding_action_current(state, project_id, session_id, generation) or state["busy"]:
                return
            try:
                values = json.loads(box.get("1.0", "end"))
                if not isinstance(values, list):
                    raise TypeError("Kalite kapıları JSON listesi olmalıdır.")
                gates = [gate.to_dict() for gate in validate_gates(values)]
            except (ValueError, TypeError) as error:
                messagebox.showerror("Kalite kapıları", str(error), parent=dialog)
                return
            self._save_coding_preferences({"quality_gates": gates})
            state["quality_gates"] = gates
            dialog.destroy()
            self._load_coding_quality_gates()
        ctk.CTkButton(dialog, text="Tercihleri kaydet", command=save).pack(pady=8)
        return dialog

    def _refresh_coding_context_inspector(self):
        state = self._coding_state()
        report = cockpit_value(state, "context", {}) or state.get("context_inspector", {})
        records = [{"id": "context-total", "type": "total", "report": report}]
        categories = report.get("categories", [])
        if not categories:
            categories = [{"key": item.get("key"), "label": item.get("display_name"), "tokens": item.get("tokens_used", 0)}
                          for item in state.get("context_report", [])]
        records += [{"id": "category:" + str(item.get("key", index)), "type": "category", **item} for index, item in enumerate(categories)]
        records += [{"type": "item", **item} for item in state.get("context_items", [])[:80]]
        def render(card, record, identifier, expanded):
            if record["type"] == "total":
                total = report.get("estimated_input_tokens")
                window = report.get("context_window", state.get("capabilities", {}).get("context_window"))
                label = f"Girdi: ~{total} token" if total is not None else "İstek bağlamı henüz hazırlanmadı."
                label += f" / {window}" if window else "\nContext kapasitesi sağlayıcı tarafından bildirilmedi."
                ctk.CTkLabel(card, text=label, anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=4)
                progress = ctk.CTkProgressBar(card)
                progress.set(min(1, max(0, report.get("fill_ratio") or 0)))
                progress.pack(fill="x", padx=6, pady=5)
                ctk.CTkLabel(card, text=report.get("label") or "Token hesabı yerel tahmindir; sağlayıcı token sayacı farklı olabilir.",
                             wraplength=230, justify="left").pack(fill="x", padx=6, pady=4)
            elif record["type"] == "category":
                label = record.get("label") or CATEGORY_TEXT.get(record.get("key"), record.get("key", "Bağlam"))
                ctk.CTkLabel(card, text=f"{label} · ~{record.get('tokens', 0)} token", anchor="w", wraplength=230).pack(fill="x", padx=6, pady=3)
            else:
                ctk.CTkLabel(card, text=self._evidence_text(f"{record.get('label') or record.get('path')}\n~{record.get('tokens_used', 0)} token · {record.get('reason', record.get('source', ''))}", 450),
                             anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=4)
                if record.get("path"):
                    ctk.CTkButton(card, text="Bağlam tercihleri", height=25,
                                 command=lambda: self._inspect_coding_context_item(record)).pack(fill="x", padx=6, pady=3)
        self._sync_evidence_cards("context", self.coding_context_list, records, render, "Bağlam henüz hazırlanmadı.")

    def _inspect_coding_context_item(self, item):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Bağlam · " + str(item.get("label") or item.get("path")))
        dialog.geometry("650x500")
        self._evidence_box(dialog, self._evidence_text(item), height=260)
        ctk.CTkLabel(dialog, text="Tercihler sonraki model isteği hazırlanırken uygulanır. Özetleme ilk/son bölümleri koruyan sınırlı alıntı kullanır.", wraplength=610).pack(padx=8, pady=4)
        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(fill="x", padx=8, pady=5)
        state, project_id, session_id, generation = self._coding_action_identity()
        def change(action):
            if not self._coding_action_current(state, project_id, session_id, generation):
                return
            values = context_preference_update(state, item["path"], action)
            state["context_paths"] = values["context_paths"]
            self._save_coding_preferences(values)
            dialog.destroy()
        for index, (label, action) in enumerate((("Sabitle", "pin"), ("Her zaman ekle", "always"), ("Çıkar", "remove"),
                                                ("Hiç ekleme", "never"), ("Özetle / sıkıştır", "summarize"))):
            ctk.CTkButton(actions, text=label, width=120, command=lambda value=action: change(value)).grid(row=index // 3, column=index % 3, padx=3, pady=3)
        return dialog

    def _refresh_coding_model_inspector(self):
        state = self._coding_state()
        capabilities = state.get("capabilities", {})
        provider = state.get("preferences", {}).get("provider", self.service.provider)
        info = state.get("model_info") or {}
        usage, accounting = completion_accounting(state)
        from firik_agent.projects.model_capabilities import usage_tokens
        usage = {**usage, **{key: value for key, value in usage_tokens(usage).items() if value is not None}}
        metrics = (cockpit_value(state, "agents", {}) or {}).get("orchestrator", {}).get("metrics", {})
        data = [{"id": "model", "model": state["session"].model, "provider": provider,
                 "capabilities": capabilities, "info": info, "usage": usage, "metrics": metrics}]
        def render(card, record, identifier, expanded):
            lines = [f"Model: {record['model']}", f"Sağlayıcı: {provider}"]
            metadata = info.get("capabilities", info.get("metadata", info))
            if isinstance(info.get("local"), bool):
                lines.append("Çalışma: " + ("Yerel" if info["local"] else "Uzak"))
            for key, label in (("context_window", "Context kapasitesi"), ("supports_tools", "Araç kullanımı"),
                               ("supports_vision", "Görsel girdi"), ("supports_reasoning", "Düşünme"),
                               ("supports_structured_output", "Yapılandırılmış çıktı")):
                value = metadata.get(key, capabilities.get(key))
                lines.append(f"{label}: " + ("Sağlayıcı bildirmedi" if value is None else "Destekliyor" if value is True else "Desteklemiyor" if value is False else str(value)))
            for key, label in (("input_tokens", "Girdi"), ("output_tokens", "Çıktı"), ("cached_tokens", "Önbellek"), ("total_tokens", "Toplam")):
                if usage.get(key) is not None:
                    lines.append(f"{label}: {usage[key]}")
            if accounting and not accounting.get("usage_complete"):
                lines.append("Token kapsamı eksik; toplam bilinmiyor.")
            for key, label in (("ttft_ms", "İlk token (ms)"), ("tokens_per_sec", "Token/sn"),
                               ("prompt_tokens_per_sec", "Girdi token/sn"), ("latency_ms", "Gecikme (ms)"),
                               ("retry_count", "Yeniden deneme"), ("ram_bytes", "RAM (bayt)"),
                               ("vram_bytes", "VRAM (bayt)"), ("kv_cache_bytes", "KV cache (bayt)")):
                if metrics.get(key) is not None:
                    lines.append(f"{label}: {metrics[key]}")
            cost = usage.get("cost")
            lines.append(f"Tahmini maliyet: ${cost:.4f}" if isinstance(cost, (int, float)) else
                         "Maliyet bilinmiyor" if accounting else "Fiyat bilgisi sağlayıcı tarafından sunulmuyor")
            for key, label in (("input_cost_per_million", "Girdi fiyatı / 1M"), ("output_cost_per_million", "Çıktı fiyatı / 1M")):
                if metadata.get(key) is not None:
                    lines.append(f"{label}: ${metadata[key]}")
            health = info.get("health", {})
            if health:
                lines.append("Endpoint: " + str(health.get("status", "Sağlık sorgusu sonuçlanmadı")))
                if health.get("latency_ms") is not None:
                    lines.append(f"Endpoint gecikmesi: {health['latency_ms']:.1f} ms")
            ctk.CTkLabel(card, text=self._evidence_text("\n".join(lines), 3000), anchor="w", justify="left", wraplength=235).pack(fill="x", padx=6, pady=5)
            if hasattr(self.service, "coding_model_info_async"):
                ctk.CTkButton(card, text="Model / endpoint durumunu sorgula", height=28, command=self._load_coding_model_info).pack(fill="x", padx=6, pady=4)
        self._sync_evidence_cards("model", self.coding_model_list, data, render, "Model seçilmedi.")

    def _load_coding_model_info(self):
        state, project_id, session_id, generation = self._coding_action_identity()
        def ready(value):
            state["model_info"] = value
            if self._coding_action_current(state, project_id, session_id, generation):
                self._refresh_coding_model_inspector()
        self.service.coding_model_info_async(project_id, state["session"].model,
            state.get("preferences", {}).get("provider", self.service.provider), on_success=ready,
            on_error=lambda error: self._coding_action_error(error, state, project_id), probe=True)

    def _load_coding_checkpoints(self):
        state, project_id, session_id, generation = self._coding_action_identity()
        if not hasattr(self.service, "list_coding_checkpoints_async"):
            return
        def ready(values):
            state["workspace_checkpoints"] = values
            if self._coding_action_current(state, project_id, session_id, generation) and self.coding_checkpoint_list.winfo_exists():
                self._refresh_coding_checkpoint_list()
        self.service.list_coding_checkpoints_async(project_id, on_success=ready,
            on_error=lambda error: self._coding_action_error(error, state, project_id))

    def _refresh_coding_checkpoint_list(self):
        records = self._coding_state().get("workspace_checkpoints", [])[:50]
        def render(card, record, identifier, expanded):
            label = record.get("label") or "Kontrol noktası"
            text = f"{label}\n{str(record.get('created_at', ''))[:19]} · {record.get('file_count', 0)} dosya"
            if record.get("limited"):
                text += "\nKısmi kayıt; tüm çalışma alanını kapsamaz."
            ctk.CTkLabel(card, text=self._evidence_text(text, 500), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=4)
            ctk.CTkButton(card, text="Karşılaştır / geri yükle", command=lambda: self._compare_coding_checkpoint(identifier), height=26).pack(fill="x", padx=6, pady=4)
        self._sync_evidence_cards("checkpoints", self.coding_checkpoint_list, records, render,
                                 "Bu proje için çalışma alanı kontrol noktası yok.")

    def _create_coding_checkpoint(self):
        state, project_id, session_id, generation = self._coding_action_identity()
        if state["busy"]:
            return
        label = simpledialog.askstring("Kontrol noktası", "Kontrol noktası açıklaması:", parent=self)
        if label is None or not self._coding_action_current(state, project_id, session_id, generation):
            return
        self.service.create_coding_checkpoint_async(project_id, on_success=lambda value: self._load_coding_checkpoints(),
            on_error=lambda error: self._coding_action_error(error, state, project_id), label=label)

    def _compare_coding_checkpoint(self, checkpoint_id):
        state, project_id, session_id, generation = self._coding_action_identity()
        def ready(preview):
            if not self._coding_action_current(state, project_id, session_id, generation):
                return
            dialog = ctk.CTkToplevel(self)
            self.coding_checkpoint_dialog = dialog
            dialog.title("Kontrol noktası karşılaştırması")
            dialog.geometry("900x620")
            changes = preview.get("changes", [])
            scroll = ctk.CTkScrollableFrame(dialog)
            scroll.pack(fill="both", expand=True, padx=8, pady=5)
            if not changes:
                ctk.CTkLabel(scroll, text="Kontrol noktasından sonra dosya değişikliği bulunmadı.").pack(pady=12)
            if preview.get("limited"):
                ctk.CTkLabel(scroll, text="Karşılaştırma kısmi; okunamayan veya sınıra takılan dosyalar dahil değildir.", wraplength=800).pack(fill="x", pady=5)
            def review(change, decision, hunk_id=None):
                if state["busy"] or not self._coding_action_current(state, project_id, session_id, generation):
                    return
                def reviewed(result):
                    if not self._coding_action_current(state, project_id, session_id, generation):
                        return
                    dialog.destroy()
                    self._refresh_coding_changes_async()
                    self._load_coding_checkpoints()
                    self._compare_coding_checkpoint(checkpoint_id)
                self.service.review_coding_change_async(project_id, checkpoint_id, change["path"], decision,
                    change.get("revision", preview.get("revision", "")), on_success=reviewed,
                    on_error=lambda error: self._coding_action_error(error, state, project_id), hunk_id=hunk_id)
            def mount_hunks(parent, change):
                frame = ctk.CTkFrame(parent, fg_color="transparent")
                frame.pack(fill="x", padx=8, pady=2)
                for number, hunk in enumerate(change.get("hunks", [])[:40], 1):
                    row = ctk.CTkFrame(frame, fg_color="transparent")
                    row.pack(fill="x", pady=2)
                    ctk.CTkLabel(row, text=f"Blok {number} · Satır {hunk.get('new_start', 1)}" + (" · Kısmi" if hunk.get("truncated") else ""),
                                 width=160, anchor="w").pack(side="left", padx=2)
                    ctk.CTkButton(row, text="İncele", width=65, height=24,
                        command=lambda value=hunk: self._show_coding_activity({"title": "Değişiklik bloğu", **value})).pack(side="left", padx=2)
                    ctk.CTkButton(row, text="Kabul", width=60, height=24, state="disabled" if state["busy"] or hunk.get("truncated") else "normal",
                        command=lambda identifier=hunk["id"]: review(change, "accepted", identifier)).pack(side="left", padx=2)
                    ctk.CTkButton(row, text="Geri al", width=65, height=24, state="disabled" if state["busy"] or hunk.get("truncated") else "normal",
                        command=lambda identifier=hunk["id"]: review(change, "rejected", identifier)).pack(side="left", padx=2)
                if len(change.get("hunks", [])) > 40:
                    ctk.CTkLabel(frame, text="İlk 40 blok gösteriliyor; tüm dosya farkı yukarıdaki metin alanında.").pack(anchor="w")
                return frame
            for change in changes[:60]:
                card = ctk.CTkFrame(scroll)
                card.pack(fill="x", pady=4)
                ctk.CTkLabel(card, text=self._evidence_text(f"{change['path']} · +{change.get('added', 0)} / −{change.get('removed', 0)}"
                    + (f" · {display_status(change.get('decision'))}" if change.get("decision") else ""), 650),
                    anchor="w", justify="left", wraplength=800).pack(fill="x", padx=8, pady=4)
                actions = ctk.CTkFrame(card, fg_color="transparent")
                actions.pack(fill="x", padx=6, pady=4)
                output = {"box": None, "hunks": None}
                def toggle_diff(value=change, holder=output, parent=card):
                    if holder["box"] is None:
                        holder["box"] = self._evidence_box(parent, self._evidence_text(value.get("diff") or "Metin farkı sunulmadı."))
                        holder["hunks"] = mount_hunks(parent, value)
                    elif holder["box"].winfo_manager():
                        holder["box"].pack_forget()
                        holder["hunks"].pack_forget()
                    else:
                        holder["box"].pack(fill="both", expand=True, padx=6, pady=5)
                        holder["hunks"].pack(fill="x", padx=8, pady=2)
                ctk.CTkButton(actions, text="Farkı aç / kapat", width=120, height=26, command=toggle_diff).pack(side="left", padx=2)
                ctk.CTkButton(actions, text="Dosyayı kabul et", width=125, height=26, state="disabled" if state["busy"] else "normal",
                             command=lambda value=change: review(value, "accepted")).pack(side="left", padx=2)
                ctk.CTkButton(actions, text="Dosyayı geri al", width=120, height=26, state="disabled" if state["busy"] else "normal",
                             command=lambda value=change: review(value, "rejected")).pack(side="left", padx=2)
            if len(changes) > 60:
                ctk.CTkLabel(scroll, text="İlk 60 dosya gösteriliyor. Geri yükleme yalnız gösterilen dosyaları kapsar; diğer dosyalar korunur.", wraplength=800).pack(pady=5)
            ctk.CTkLabel(dialog, text="Geri yükleme güncel dosyaları kontrol noktası içeriğine döndürür. Önce geri alınabilir bir yedek oluşturulur.", wraplength=840).pack(padx=8, pady=4)
            def restore():
                if state["busy"] or not self._coding_action_current(state, project_id, session_id, generation):
                    return
                displayed_paths = {change["path"] for change in changes[:60]}
                reviewed_hashes = {path: digest for path, digest in preview.get("expected_hashes", {}).items() if path in displayed_paths}
                self.service.restore_coding_checkpoint_async(project_id, checkpoint_id, reviewed_hashes,
                    on_success=lambda result: (self._load_coding_checkpoints(), self._refresh_coding_changes_async()),
                    on_error=lambda error: self._coding_action_error(error, state, project_id))
                dialog.destroy()
            ctk.CTkButton(dialog, text="Gösterilen dosyaları geri yükle", fg_color="#9b2c2c", hover_color="#7f1d1d",
                         state="disabled" if state["busy"] or not changes else "normal", command=restore).pack(pady=8)
        self.service.compare_coding_checkpoint_async(project_id, checkpoint_id, on_success=ready,
            on_error=lambda error: self._coding_action_error(error, state, project_id))
