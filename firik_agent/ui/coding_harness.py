"""Passive harness settings, execution traces and explicit isolated experiments."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from firik_agent.harness.config import resolve_harness_configuration, select_backend
from firik_agent.projects.coding_sessions import redact_sensitive


def harness_snapshot(config: dict | None = None, preferences: dict | None = None) -> dict:
    from firik_agent.harness.registry import backend_inventory
    settings = resolve_harness_configuration(config, preferences)
    return {"configured": settings.to_dict(),
            "backends": backend_inventory({"harness": settings.to_dict()}),
            "selection_scope": "next task only",
            "external_coding": "disabled until host tools and execution policy can be mapped",
            "external_prompt": "Explicit live tool-free Pi is available through 'harness prompt --backend pi'; no workspace admission. See docs/HARNESS_DESKTOP.md.",
            "model_configuration": "The model provider is independent of the harness; external experiments require explicit provider/model settings.",
            "automatic_backend_switching": False}


def choose_project_backend(view, name: str) -> None:
    state = view._coding_state()
    settings = resolve_harness_configuration(getattr(view.service, "config", {}), state["preferences"])
    select_backend(settings, name, active_backend=settings.default_backend if state["busy"] else None)
    if state["busy"]:
        raise ValueError("Harness settings can only be saved after the active task finishes.")
    # External tool-free generation is not an authorization bridge for coding.
    if name != "native":
        raise ValueError("External coding tools cannot yet enforce this desktop project's permission policy. Use an explicitly authorized tool-free experiment.")
    saved = settings.to_dict()
    saved["default_backend"] = name
    view._save_coding_preferences({"harness": saved})


def execution_trace(state: dict[str, Any]) -> dict:
    """Project existing state; opening the view never resumes or dispatches work."""
    record = state.get("record")
    recorded = getattr(record, "usage", {}) or {}
    configured = state.get("preferences", {}).get("harness", {}).get("default_backend", "native")
    backend = recorded.get("harness_backend", configured)
    return redact_sensitive({
        "backend": backend, "model": getattr(state.get("session"), "model", None),
        "task": state.get("last_prompt", ""), "busy": state.get("busy", False),
        "status": state.get("status"), "steps": state.get("plan", []),
        "events": state.get("activities", [])[-200:],
        "verification": state.get("verifications", getattr(record, "verifications", [])),
        "usage": state.get("usage", {}), "metrics": state.get("harness_metrics", {}),
        "uncertainty": state.get("inspection_warning", ""),
    })


def load_comparison_analysis(path: Path) -> dict:
    from firik_agent.harness.cli import read_document
    from firik_agent.harness.comparison import validate_comparison_report
    from firik_agent.harness.optimizer import recommend
    report = read_document(path)
    validated = validate_comparison_report(report)
    report = validated if isinstance(validated, dict) else report
    return {"report": redact_sensitive(report), "recommendation": recommend(report),
            "interpretation": "Only recorded trials are evidence. Missing usage/cost and small samples remain uncertain; this view does not select a backend."}


def show_harness_dialog(view):
    import json
    from tkinter import filedialog, messagebox
    import customtkinter as ctk

    project_id = view.current_project_id
    dialog = ctk.CTkToplevel(view)
    dialog.title("Harness Ayarları ve Benchmark")
    dialog.geometry("960x700")
    dialog.transient(view.winfo_toplevel())
    view.coding_harness_dialog = dialog
    ctk.CTkLabel(dialog, text="Harness seçimi sonraki görev için geçerlidir. Model ve yürütme izinleri ayrı tutulur.",
                 wraplength=900, justify="left").pack(fill="x", padx=12, pady=8)
    tabs = ctk.CTkTabview(dialog)
    tabs.pack(fill="both", expand=True, padx=10, pady=4)
    boxes = {}
    for label in ("Backend", "Karşılaştırma", "Yürütme İzi", "Deney"):
        panel = tabs.add(label)
        boxes[label] = ctk.CTkTextbox(panel, wrap="word")
        boxes[label].pack(fill="both", expand=True)
    footer = ctk.CTkFrame(dialog, fg_color="transparent")
    footer.pack(fill="x", padx=12, pady=8)
    selector = ctk.CTkComboBox(footer, values=["native"], state="readonly", width=130)
    selector.set("native")
    selector.pack(side="left", padx=3)
    jobs = []
    running = {"experiment": None}

    def alive():
        return bool(dialog.winfo_exists()) and view.current_project_id == project_id

    def present(label, payload):
        if not alive():
            return
        box = boxes[label]
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", json.dumps(redact_sensitive(payload), ensure_ascii=False, indent=2, default=str))
        box.configure(state="disabled")

    def failure(error):
        from concurrent.futures import CancelledError
        if isinstance(error, (CancelledError, asyncio.CancelledError)):
            return
        if alive():
            messagebox.showerror("Harness", str(redact_sensitive(error)), parent=dialog)

    def submit(coro, label):
        # The application queue is the GUI boundary; Tk.after itself must not
        # be called by the background runtime thread.
        jobs[:] = [job for job in jobs if not job.done()]
        future = view.service.runtime.submit(coro,
            on_done=lambda value: view.service._dispatch(present, label, value),
            on_error=lambda error: view.service._dispatch(failure, error))
        jobs.append(future)
        return future

    def refresh():
        if not alive():
            return
        state = view._coding_state()
        present("Yürütme İzi", execution_trace(state))
        config = dict(getattr(view.service, "config", {}))
        preferences = dict(state["preferences"])
        submit(asyncio.to_thread(harness_snapshot, config, preferences), "Backend")

    def save():
        try:
            choose_project_backend(view, selector.get())
            refresh()
        except (ValueError, RuntimeError) as error:
            failure(error)

    def report():
        path = filedialog.askopenfilename(parent=dialog, title="Harness Karşılaştırma Raporu", filetypes=[("JSON", "*.json")])
        if path:
            submit(asyncio.to_thread(load_comparison_analysis, Path(path)), "Karşılaştırma")
            tabs.set("Karşılaştırma")

    def experiment():
        if not alive():
            return
        if view._coding_state()["busy"]:
            failure("Aktif görev sürerken deney başlatılamaz.")
            return
        if running["experiment"] is not None and not running["experiment"].done():
            failure("Bir deney zaten çalışıyor; önce bitmesini bekleyin veya durdurun.")
            return
        path = filedialog.askopenfilename(parent=dialog, title="Açık Deney Planı", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            from firik_agent.harness.cli import read_document, _backend_factory
            from firik_agent.harness.comparison import ComparisonPlan, run_comparison
            plan = ComparisonPlan.model_validate(read_document(Path(path)))
            rows = [f"{item.id}: {item.backend} · {item.provider} · {item.model}" for item in plan.configurations]
            paid = any(item.provider != "replay" for item in plan.configurations)
            text = "\n".join(rows) + ("\n\nBu plan ücretli model çağrıları içerebilir; bu onay listelenen tüm yapılandırmaları kapsar." if paid else "\n\nBu plan çevrimdışı sentetik tekrar sağlayıcısını kullanır.")
            text += "\nHer yapılandırma ayrı geçici çalışma alanında çalışır. Sonuçlar proje dosyalarına uygulanmaz.\nDeney başlatılsın mı?"
            present("Deney", {"plan": plan.model_dump(mode="json"), "requires_explicit_authorization": True})
            tabs.set("Deney")
            if not messagebox.askyesno("Deney Onayı", text, parent=dialog):
                return
            running["experiment"] = submit(run_comparison(plan, allow_paid=paid,
                backend_factory=_backend_factory(getattr(view.service, "config", {}))), "Deney")
        except (ValueError, RuntimeError, TypeError, OSError) as error:
            failure(error)

    def cancel_experiment():
        future = running["experiment"]
        if future is not None and not future.done():
            future.cancel()
            present("Deney", {"state": "cancellation_requested", "cleanup": "Backend cancellation and teardown are in progress."})

    def close():
        for future in jobs:
            if not future.done():
                future.cancel()
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", close)
    for label, command in (("Sonraki Görev İçin Kaydet", save), ("Yenile", refresh),
                           ("Rapor Aç", report), ("Deney Planı Çalıştır", experiment), ("Deneyi Durdur", cancel_experiment)):
        ctk.CTkButton(footer, text=label, width=135, command=command).pack(side="left", padx=3)
    present("Deney", {"state": "idle", "instructions": "Önce sağlayıcı, model, backend, görev, ortak bütçe ve izinleri belirten bir karşılaştırma planı seçin.",
                       "paid_requests": "Her deney için açık onay gerekir.", "automatic_integration": False})
    refresh()
    return dialog
