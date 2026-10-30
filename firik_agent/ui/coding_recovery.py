"""Explicit, passive-first recovery view for persisted execution state."""
from __future__ import annotations

import json
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk

from firik_agent.projects.coding_sessions import redact_sensitive


def recovery_summary(report):
    """Never describe an unresolved or conflicted run as eligible."""
    uncertain = (report.get("uncertain_operations") or []) + (report.get("unprojected_model_outputs") or [])
    conflicts = report.get("workspace_conflicts") or []
    eligible = report.get("resume_eligible") is True and not uncertain and not conflicts
    last = report.get("last_successful_operation") or {}
    checkpoint = report.get("last_checkpoint") or {}
    return redact_sensitive("\n".join((
        f"Çalışma: {report.get('run_id', 'bilinmiyor')} · {report.get('lifecycle_state', report.get('status', 'bilinmiyor'))}",
        f"Son kalıcı kontrol noktası: {checkpoint.get('id', checkpoint.get('checkpoint_id', checkpoint.get('kind', 'yok'))) if isinstance(checkpoint, dict) else checkpoint}",
        f"Son başarılı işlem: {last.get('operation', last.get('attempt_id', 'yok')) if isinstance(last, dict) else last}",
        f"Sonucu belirsiz işlem: {len(uncertain)} · Çalışma alanı çakışması: {len(conflicts)}",
        f"Bekleyen doğrulama: {len(report.get('pending_verification') or [])} · Bekleyen onay: {len(report.get('pending_approvals') or [])}",
        "Devam hazırlığı uygun; yeni işlemler güncel izinlere tabidir." if eligible else
        "Devam engellendi; rapordaki önkoşulları ve uzlaştırma gereksinimlerini inceleyin.",
        "Bu görünüm açıldığında hiçbir model veya araç çağrısı yinelenmez.",
    )))


def show_recovery_dialog(view, run_id):
    project_id = view.current_project_id
    dialog = ctk.CTkToplevel(view)
    dialog.title("Kalıcı Çalışma ve Kurtarma")
    dialog.geometry("890x650")
    dialog.transient(view.winfo_toplevel())
    view.coding_recovery_dialog = dialog
    summary = ctk.CTkLabel(dialog, text="Kalıcı durum ve güncel dosyalar karşılaştırılıyor…", justify="left", anchor="w", wraplength=840)
    summary.pack(fill="x", padx=12, pady=10)
    box = ctk.CTkTextbox(dialog, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
    box.pack(fill="both", expand=True, padx=12, pady=4)
    footer = ctk.CTkFrame(dialog, fg_color="transparent")
    footer.pack(fill="x", padx=12, pady=10)
    state = {"report": {}}

    def alive():
        return dialog.winfo_exists() and view.current_project_id == project_id

    def show_payload(value):
        if not alive():
            return
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", json.dumps(redact_sensitive(value), ensure_ascii=False, indent=2, default=str))
        box.configure(state="disabled")

    def error(value):
        if alive():
            messagebox.showerror("Kurtarma", str(redact_sensitive(value)), parent=dialog)

    def inspected(report):
        if not alive():
            return
        state["report"] = report
        summary.configure(text=recovery_summary(report))
        show_payload(report)
        resume.configure(state="normal" if report.get("resume_eligible") is True and
                         not report.get("uncertain_operations") and not report.get("workspace_conflicts") else "disabled")

    def refresh():
        view.service.inspect_coding_recovery_async(project_id, run_id, inspected, error)

    def compare():
        view.service.compare_coding_recovery_async(project_id, run_id, show_payload, error)

    def reconcile():
        uncertain = (state["report"].get("uncertain_operations") or []) + (state["report"].get("unprojected_model_outputs") or [])
        if not uncertain:
            messagebox.showinfo("Uzlaştırma", "Sonucu belirsiz işlem bulunmuyor.", parent=dialog)
            return
        ids = [str(item.get("attempt_id")) for item in uncertain if isinstance(item, dict)]
        attempt = simpledialog.askstring("İşlem Kimliği", "Uzlaştırılacak attempt_id:\n" + "\n".join(ids),
            initialvalue=ids[0] if len(ids) == 1 else "", parent=dialog)
        if not attempt or attempt not in ids:
            return
        outcome = simpledialog.askstring("Gözlenen Sonuç", "Kanıtladığınız sonuç: completed, failed veya cancelled", parent=dialog)
        if outcome not in {"completed", "failed", "cancelled"}:
            return
        evidence = simpledialog.askstring("Gözlenen Kanıt", "İşlemin sonucunu doğrulayan dosya, uzak kayıt veya inceleme kanıtı:", parent=dialog)
        if evidence and evidence.strip():
            view.service.reconcile_coding_recovery_async(project_id, run_id, attempt, outcome,
                evidence.strip(), lambda result: refresh(), error)

    def continue_run():
        report = state["report"]
        if not alive() or not report.get("resume_eligible") or report.get("uncertain_operations") or report.get("workspace_conflicts"):
            return
        if not messagebox.askyesno("Devam Onayı", "Kalıcı durumu kullanarak yeni bir model isteği başlatılsın mı?\n"
                "İzinler yeniden denetlenir; bekleyen işlem onayları yeniden istenir.", parent=dialog):
            return
        try:
            if not view._load_coding_history(run_id):
                return
            coding = view._coding_state()
            record = coding["record"]
            coding["resume_source_run_id"] = record.resumed_from if record.status == "queued" and record.resumed_from else record.id
            coding["resume_approved"] = True
            if record.status == "queued" and record.resumed_from:
                coding["resume_prepared_run_id"] = record.id
            dialog.destroy()
            view._run_coding_prompt("Kalıcı çalışma kaydındaki tamamlanmamış adımları güncel dosyaları inceleyerek sürdür. "
                "Tamamlanan adımları yineleme; doğrulama ve işlem onaylarını yeniden değerlendir.")
        except (ValueError, PermissionError) as exc:
            error(exc)

    def cancel():
        if messagebox.askyesno("Çalışmayı İptal Et", "Seçili kalıcı çalışma iptal edildi olarak kaydedilsin mi?", parent=dialog):
            view.service.cancel_coding_recovery_async(project_id, run_id, lambda result: refresh(), error)

    def reconcile_workspace():
        evidence = simpledialog.askstring("Çalışma Alanını Uzlaştır", "Güncel kullanıcı değişikliklerini koruyarak yeni temel olarak kabul etme gerekçesi ve inceleme kanıtı:", parent=dialog)
        if evidence and evidence.strip() and messagebox.askyesno("Güncel Dosyaları Kabul Et", "Güncel dosyalar korunacak ve eski doğrulamalar geçersiz sayılacak. Bu temel kabul edilsin mi?", parent=dialog):
            view.service.reconcile_coding_workspace_async(project_id, run_id, evidence.strip(), lambda result: refresh(), error)

    def export():
        path = filedialog.asksaveasfilename(parent=dialog, title="Arındırılmış Çalışma Raporu", defaultextension=".json",
            initialfile=f"{run_id}-recovery.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        def saved(report):
            try:
                with Path(path).open("x", encoding="utf-8") as stream:
                    json.dump(redact_sensitive(report), stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                if alive():
                    messagebox.showinfo("Rapor", "Arındırılmış rapor kaydedildi.", parent=dialog)
            except OSError as exc:
                error(exc)
        view.service.export_coding_recovery_async(project_id, run_id, saved, error)

    for title, command in (("Yeniden İncele", refresh), ("Değişiklikleri Karşılaştır", compare),
                           ("Uzlaştır", reconcile), ("İptal Et", cancel), ("Raporu Dışa Aktar", export)):
        ctk.CTkButton(footer, text=title, width=125, command=command).pack(side="left", padx=3)
    resume = ctk.CTkButton(dialog, text="Onayla ve Devam Et", state="disabled", command=continue_run)
    resume.pack(pady=(0, 12))
    ctk.CTkButton(dialog, text="Güncel Çalışma Alanını Uzlaştır", command=reconcile_workspace).pack(pady=(0, 8))
    refresh()
    return dialog
