"""Reviewable, offline-capable learning management in the existing desktop UI."""
from __future__ import annotations

import json
from tkinter import messagebox, simpledialog

import customtkinter as ctk


SECTIONS = ("Genel Bakış", "Öğrenme Geçmişi", "Bellek", "Beceriler", "Araçlar",
            "Deneyler", "Kod İyileştirmeleri", "Onaylar", "Ayarlar")


def display_json(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


class ImprovementView(ctk.CTkFrame):
    """All values originate in the coordinator; unknown metrics remain unknown."""

    PAGE_SIZE = 25

    def __init__(self, master, service, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.offset = 0
        self.selected = None
        self._request_serial = 0
        self._detail_serial = 0
        self._rows = []
        self._busy = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        ctk.CTkLabel(header, text="Kendi Kendini Geliştirme", font=ctk.CTkFont(size=21, weight="bold")).pack(side="left")
        self.refresh_button = ctk.CTkButton(header, text="Yenile", width=85, command=self.refresh)
        self.refresh_button.pack(side="right")
        navigation = ctk.CTkFrame(self, fg_color="transparent")
        navigation.grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        self.section = ctk.CTkOptionMenu(navigation, values=list(SECTIONS), width=210, command=self._change_section)
        self.section.pack(side="left")
        self.mode_label = ctk.CTkLabel(navigation, text="Öğrenme durumu yenilendiğinde gösterilir.", anchor="w")
        self.mode_label.pack(side="left", padx=12)
        scope = ctk.CTkFrame(self, fg_color="transparent")
        scope.grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        self.project_entry = ctk.CTkEntry(scope, placeholder_text="Proje kimliği (boş: genel kapsam)", width=260)
        self.project_entry.pack(side="left", padx=(0, 6))
        self.session_entry = ctk.CTkEntry(scope, placeholder_text="İncelenecek oturum kimliği", width=220)
        self.session_entry.pack(side="left", padx=6)
        ctk.CTkButton(scope, text="Oturumu değerlendir", width=155, command=self._reflect).pack(side="left", padx=6)
        ctk.CTkButton(scope, text="Öğrenmeyi durdur", width=130,
            command=self._stop_learning).pack(side="left", padx=6)
        self.status_label = ctk.CTkLabel(self, text="Çevrimdışı inceleme kullanılabilir. Yeni bilgiler onay için hazırlanır.", anchor="w", wraplength=780)
        self.status_label.grid(row=3, column=0, sticky="ew", padx=16, pady=4)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=4, column=0, sticky="nsew", padx=16, pady=4)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.list_frame = ctk.CTkScrollableFrame(body, width=225)
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.detail = ctk.CTkTextbox(body, wrap="word", font=ctk.CTkFont(family="monospace", size=12))
        self.detail.grid(row=0, column=1, sticky="nsew")
        self._set_detail("Bir bölüm seçip Yenile'ye basın. Öneri, kanıt, fark ve doğrulama sonuçları burada gösterilir.")
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=16, pady=4)
        ctk.CTkButton(footer, text="Önceki", width=80, command=lambda: self._page(-1)).pack(side="left", padx=(0, 4))
        self.page_label = ctk.CTkLabel(footer, text="1–25")
        self.page_label.pack(side="left", padx=4)
        ctk.CTkButton(footer, text="Sonraki", width=80, command=lambda: self._page(1)).pack(side="left", padx=4)
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 12))
        self.action_buttons = {}
        for label, action in (("Fark", self._diff), ("Doğrula", lambda: self._decision("evaluate")),
                ("Onayla", lambda: self._decision("approve")), ("Reddet", lambda: self._decision("reject")),
                ("Ertele", self._defer), ("Geri al", lambda: self._decision("rollback"))):
            button = ctk.CTkButton(self.actions, text=label, width=83, command=action, state="disabled")
            button.pack(side="left", padx=(0, 4))
            self.action_buttons[label] = button
        ctk.CTkButton(self.actions, text="Diğer işlemler", width=120, command=self._other_operations).pack(side="right")

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _set_detail(self, value):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", display_json(value))
        self.detail.configure(state="disabled")

    def _call(self, action, *args, callback=None, **kwargs):
        if self._busy:
            return
        self._busy = True
        self.refresh_button.configure(state="disabled")
        self.section.configure(state="disabled")
        self.status_label.configure(text="İşlem sürüyor…")

        def done(result):
            if not self._alive():
                return
            self._busy = False
            self.refresh_button.configure(state="normal")
            self.section.configure(state="normal")
            self.status_label.configure(text="İşlem tamamlandı. Bekleyen öneriler henüz uygulanmadı.")
            if callback:
                callback(result)

        def failed(error):
            if self._alive():
                self._busy = False
                self.refresh_button.configure(state="normal")
                self.section.configure(state="normal")
                self.status_label.configure(text=f"İşlem tamamlanamadı: {error}")

        try:
            self.service.improvement_async(action, *args, on_success=done, on_error=failed, **kwargs)
        except (ValueError, RuntimeError, OSError) as error:
            failed(str(error))

    def _project(self):
        return self.project_entry.get().strip() or None

    def _change_section(self, _value):
        self.offset = 0
        self.refresh()

    def _page(self, delta):
        if delta > 0 and len(self._rows) < self.PAGE_SIZE:
            return
        self.offset = max(0, self.offset + delta * self.PAGE_SIZE)
        self.refresh()

    def _reset_selection(self):
        self.selected = None
        self._detail_serial += 1
        for button in self.action_buttons.values():
            button.configure(state="disabled")

    def refresh(self):
        if self._busy:
            return
        self._request_serial += 1
        serial = self._request_serial
        section = self.section.get()
        self._reset_selection()
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._rows = []
        self.page_label.configure(text=f"{self.offset + 1}–{self.offset + self.PAGE_SIZE}")

        def received(result):
            if serial != self._request_serial:
                return
            if section == "Genel Bakış":
                settings = result.get("settings", {}) if isinstance(result, dict) else {}
                effective_reflection = result.get("automatic_reflection", settings.get("automatic_reflection", "bilinmiyor"))
                self.mode_label.configure(text=f"Mod: {settings.get('mode', 'bilinmiyor')} | Otomatik yansıtma: {effective_reflection}")
            if isinstance(result, list):
                rows = result
            elif isinstance(result, dict) and isinstance(result.get("items"), list):
                rows = result["items"]
            else:
                self._set_detail(result)
                return
            self._rows = rows
            self._set_detail({"section": section, "offset": self.offset, "count": len(rows),
                              "message": "Ayrıntı için soldan bir kayıt seçin."})
            for row in rows:
                label = row.get("title") or row.get("content") or row.get("event") or row.get("id") or "Kayıt"
                state = row.get("status") or row.get("kind") or ""
                ctk.CTkButton(self.list_frame, text=f"{str(label)[:65]}\n{state}", anchor="w", height=50,
                    command=lambda item=row: self._select(item)).pack(fill="x", pady=3)

        if section == "Genel Bakış":
            self._call("status", callback=received)
        elif section == "Öğrenme Geçmişi":
            self._call("history", limit=self.PAGE_SIZE, offset=self.offset, callback=received)
        elif section == "Bellek":
            self._call("memories", project_id=self._project(), limit=self.PAGE_SIZE, offset=self.offset, callback=received)
        elif section == "Ayarlar":
            self._call("settings", callback=received)
        else:
            # Coordinator pagination is authoritative. A type filter is applied
            # only to the displayed page, never used to fabricate totals.
            kinds = {"Beceriler": {"skill"}, "Araçlar": {"tool"}, "Deneyler": {"harness", "optimization"},
                     "Kod İyileştirmeleri": {"code"}}.get(section)
            def proposals_received(result):
                if kinds and isinstance(result, list):
                    self._rows = result
                    received([row for row in result if row.get("kind") in kinds])
                    self._rows = result  # Pagination refers to the complete page.
                else:
                    received(result)
            self._call("proposals", limit=self.PAGE_SIZE, offset=self.offset, callback=proposals_received)

    def _select(self, row):
        if self._busy:
            return
        self._reset_selection()
        self.selected = row
        if self.section.get() in {"Bellek", "Öğrenme Geçmişi"}:
            self._set_detail(row)
            return
        proposal_id = row.get("id")
        if not proposal_id:
            self._set_detail(row)
            return
        self._detail_serial += 1
        serial = self._detail_serial

        def show(result):
            if serial != self._detail_serial:
                return
            self.selected = result
            self._set_detail(result)
            for button in self.action_buttons.values():
                button.configure(state="normal")
        self._call("show", proposal_id, callback=show)

    def _diff(self):
        if self.selected and self.selected.get("id"):
            self._call("diff", self.selected["id"], callback=self._set_detail)

    def _decision(self, action):
        if getattr(self, "_busy", False):
            return
        proposal = self.selected
        if not proposal or not proposal.get("id") or proposal.get("revision") is None:
            return
        if action in {"approve", "rollback"} and not messagebox.askyesno("Değişikliği onayla",
                f"{proposal['id']} / revizyon {proposal['revision']}\n"
                f"Tür: {proposal.get('kind', 'bilinmiyor')}\n"
                f"İşlem: {action}\n\nİncelediğiniz bu revizyon için devam edilsin mi?", parent=self):
            return
        options = {"expected_revision": proposal["revision"], "actor": "user"} if action != "evaluate" else {}

        def changed(result):
            self._set_detail(result)
            self._reset_selection()
            # Revision changes invalidate the previously reviewed selection.
            self.status_label.configure(text="Sonuç gösteriliyor. Yeni işlem için kaydı yeniden seçip güncel revizyonu inceleyin.")
        self._call(action, proposal["id"], callback=changed, **options)

    def _defer(self):
        self._reset_selection()
        self.status_label.configure(text="İnceleme ertelendi. Önerinin bekleyen durumu korundu.")

    def _reflect(self):
        session_id = self.session_entry.get().strip()
        if not session_id:
            self.status_label.configure(text="İncelenecek oturum kimliğini girin.")
            return
        self._call("reflect", session_id, project_id=self._project(), callback=self._set_detail)

    def _stop_learning(self):
        # Stop remains available while a generation/experiment request is busy.
        # Delivery still uses the existing worker bridge and Tk callback queue.
        def stopped(result):
            if self._alive():
                self._set_detail(result)
                self.status_label.configure(text="Öğrenme kapatıldı. Çalışan öğrenme işleri için iptal istendi.")
        def failed(error):
            if self._alive():
                self.status_label.configure(text=f"Öğrenme durdurulamadı: {error}")
        self.service.improvement_async("configure", {"mode": "off", "automatic_reflection": False},
                                      on_success=stopped, on_error=failed)

    def _other_operations(self):
        section = self.section.get()
        if section == "Ayarlar":
            self._edit_settings()
        elif section == "Bellek":
            self._memory_actions()
        elif section == "Beceriler":
            self._learn_workflow()
        elif section in {"Deneyler", "Araçlar"}:
            self._experiment_actions()
        elif section == "Kod İyileştirmeleri":
            self._code_actions()
        else:
            self._call("metrics", callback=self._set_detail)

    def _experiment_actions(self):
        proposal = dict(self.selected or {})
        if not proposal.get("id") or proposal.get("kind") not in {"harness", "tool", "code"}:
            self.status_label.configure(text="Denemek veya izlemek için önce ilgili öneriyi seçip ayrıntılarını inceleyin.")
            return
        monitoring = proposal.get("status") == "applied"
        if monitoring and proposal["kind"] != "harness":
            self.status_label.configure(text="Bu değişiklik için otomatik izleme yok. Kanıtları ve geri alma seçeneğini inceleyebilirsiniz.")
            return
        window = ctk.CTkToplevel(self)
        window.title("Gerileme ölçümü" if monitoring else "Bağımsız deney")
        window.geometry("650x470")
        field = {"harness": "cases", "tool": "fixtures", "code": "gates"}[proposal["kind"]]
        instructions = {"cases": "Bağımsız bellek sorguları ve beklenen kayıt kimlikleri (JSON dizi).",
                        "fixtures": "Araç girdileri ve beklenen çıktılar (JSON dizi).",
                        "gates": "İncelediğiniz bağımsız test komutları (JSON dizi)."}[field]
        ctk.CTkLabel(window, text=f"{proposal['id']}\n{instructions}", wraplength=600, anchor="w").pack(fill="x", padx=16, pady=12)
        specification = ctk.CTkTextbox(window)
        specification.pack(fill="both", expand=True, padx=16, pady=4)
        ctk.CTkLabel(window, text="Sonuç yalnızca ölçülür ve kaydedilir. Yeni davranış uygulanmaz; otomatik geri alma yapılmaz.",
                     wraplength=600).pack(padx=16, pady=8)
        def submit():
            text = specification.get("1.0", "end-1c")
            try:
                if len(text.encode("utf-8")) > 256 * 1024:
                    raise ValueError()
                value = json.loads(text)
                if not isinstance(value, list):
                    raise ValueError()
            except (ValueError, TypeError):
                self.status_label.configure(text="Deney tanımı en fazla 256 KiB boyutunda geçerli bir JSON dizi olmalı.")
                return
            window.destroy()
            if monitoring:
                self._call("monitor", proposal["id"], value, callback=self._set_detail)
            else:
                self._call("experiment", proposal["id"], **{field: value}, callback=self._set_detail)
        ctk.CTkButton(window, text="Gerilemeyi ölç" if monitoring else "Deneyi çalıştır", command=submit).pack(padx=16, pady=12)

    def _code_actions(self):
        window = ctk.CTkToplevel(self)
        window.title("Kod iyileştirme")
        window.geometry("430x220")
        ctk.CTkLabel(window, text="Aday kaynak kodu onaylanmadan çalışan uygulamaya uygulanmaz.",
                     wraplength=380).pack(padx=16, pady=16)
        def generate():
            window.destroy()
            self._generate_code()
        def experiment():
            window.destroy()
            self._experiment_actions()
        ctk.CTkButton(window, text="Seçili modelle yeni yama öner", command=generate).pack(padx=16, pady=8)
        ctk.CTkButton(window, text="Seçili adayın testlerini çalıştır", command=experiment).pack(padx=16, pady=8)

    def _generate_code(self):
        project_id = self._project()
        window = ctk.CTkToplevel(self)
        window.title("İncelenebilir kaynak yaması üret")
        window.geometry("650x550")
        repository = ctk.CTkEntry(window, placeholder_text="Yerel Git deposunun tam yolu")
        repository.pack(fill="x", padx=16, pady=(16, 6))
        paths = ctk.CTkTextbox(window, height=100)
        paths.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(window, text="İzin verilen depo içi dosyalar: her satırda tam bir göreli dosya yolu.", anchor="w").pack(fill="x", padx=16)
        task = ctk.CTkTextbox(window, height=170)
        task.pack(fill="both", expand=True, padx=16, pady=8)
        ctk.CTkLabel(window, text="Görev ve seçtiğiniz dosyaların içeriği mevcut ajan modeline gönderilir; model kotası kullanılabilir. "
                     "Üretilen kod çalıştırılmaz veya uygulanmaz.", wraplength=600).pack(padx=16, pady=8)
        def submit():
            root = repository.get().strip()
            allowed = [line.strip() for line in paths.get("1.0", "end-1c").splitlines() if line.strip()]
            request = task.get("1.0", "end-1c").strip()
            if not root or not allowed or len(allowed) > 20 or not request or len(request) > 6000:
                self.status_label.configure(text="Depo yolu, en fazla 20 dosya ve en fazla 6000 karakterlik dar bir görev gerekiyor.")
                return
            if not messagebox.askyesno("Seçili modele kaynak gönder",
                f"Depo: {root}\nDosyalar:\n" + "\n".join(allowed) +
                "\n\nBu dosyaların içeriği ve görev mevcut ajan modeline gönderilsin mi? Model kotası kullanılabilir.", parent=window):
                return
            window.destroy()
            self._call("generate_code", root, allowed, request, project_id=project_id,
                       allow_model_digest=True, callback=self._set_detail)
        ctk.CTkButton(window, text="Kaynak kapsamını incele ve yama öner", command=submit).pack(padx=16, pady=12)

    def _edit_settings(self):
        value = simpledialog.askstring("Öğrenme ayarları", 'JSON güncellemesi (örnek: {"mode":"off"}):', parent=self)
        if value is None:
            return
        try:
            updates = json.loads(value)
            if not isinstance(updates, dict):
                raise ValueError()
        except (ValueError, TypeError):
            self.status_label.configure(text="Ayarlar geçerli bir JSON nesnesi olmalı.")
            return
        self._call("configure", updates, callback=self._set_detail)

    def _memory_actions(self):
        selected = dict(self.selected or {})
        memory_id = selected.get("memory_id") or selected.get("id")
        project_id = selected.get("project_id") if memory_id else self._project()
        window = ctk.CTkToplevel(self)
        window.title("Bellek yönetimi")
        window.geometry("500x470")
        ctk.CTkLabel(window, text="Kapsam: " + (project_id or "genel"), anchor="w").pack(fill="x", padx=16, pady=8)
        query = ctk.CTkEntry(window, placeholder_text="Görev veya arama sorgusu")
        query.pack(fill="x", padx=16, pady=4)
        def inspect_context():
            text = query.get().strip()
            if text:
                window.destroy()
                self._call("retrieve", text, project_id=project_id, callback=self._set_detail)
        ctk.CTkButton(window, text="Seçilen bağlamı ve nedenlerini incele", command=inspect_context).pack(padx=16, pady=4)
        content = ctk.CTkTextbox(window, height=140)
        content.pack(fill="both", expand=True, padx=16, pady=4)
        ctk.CTkLabel(window, text="Yeni bilgi inceleme için hazırlanır. Onaylanmadan bağlama girmez.", wraplength=450).pack(padx=16, pady=4)
        def propose():
            text = content.get("1.0", "end-1c").strip()
            if text:
                window.destroy()
                self._call("propose_memory", text, project_id=project_id, source_reference="user:desktop",
                           supersedes_id=memory_id, callback=self._set_detail)
        ctk.CTkButton(window, text="Bilgi / düzeltme öner", command=propose).pack(padx=16, pady=8)
        if memory_id:
            def pin():
                window.destroy()
                self._call("pin_memory", memory_id, pinned=not selected.get("pinned", False), callback=self._set_detail)
            ctk.CTkButton(window, text="Sabitlemeyi kaldır" if selected.get("pinned") else "Seçili bilgiyi sabitle",
                          command=pin).pack(padx=16, pady=4)
            def remove():
                if messagebox.askyesno("Bilgiyi kaldır", f"{memory_id} kaldırılıp gelecek bağlamlardan çıkarılsın mı?", parent=window):
                    window.destroy()
                    self._call("remove_memory", memory_id, callback=self._set_detail)
            ctk.CTkButton(window, text="Seçili bilgiyi kaldır", command=remove, fg_color="#b91c1c").pack(padx=16, pady=8)

    def _learn_workflow(self):
        name = simpledialog.askstring("İş akışı öğren", "Beceri adı:", parent=self)
        if not name:
            return
        description = simpledialog.askstring("İş akışı öğren", "Kısa açıklama ve tetikleyici:", parent=self)
        if not description:
            return
        window = ctk.CTkToplevel(self)
        window.title("İş akışı adımları")
        window.geometry("600x420")
        procedure = ctk.CTkTextbox(window)
        procedure.pack(fill="both", expand=True, padx=16, pady=12)
        project_id = self._project()
        def submit():
            instructions = procedure.get("1.0", "end-1c").strip()
            if instructions:
                window.destroy()
                self._call("propose_skill", name, description, instructions, project_id=project_id, callback=self._set_detail)
        ctk.CTkButton(window, text="İnceleme için hazırla", command=submit).pack(padx=16, pady=12)
