"""Screenshot preview and host controls for the same managed Chromium context."""
from __future__ import annotations

import hashlib
import io
import json
import tkinter as tk
import base64

import customtkinter as ctk
from PIL import Image


class BrowserPreviewImage(ctk.CTkImage):
    """Use Tk's PNG decoder while preserving CustomTkinter's HiDPI contract.

    Some Python distributions pair Tk 9 with a Pillow ImageTk extension built
    for Tk 8. Native PNG loading avoids that ABI boundary and keeps all image
    creation attached to this window's actual interpreter.
    """
    def __init__(self, master, image, size):
        super().__init__(light_image=image, size=size)
        self.master = master
        self._native_images = {}

    def create_scaled_photo_image(self, widget_scaling, appearance_mode):
        size = tuple(max(1, round(value * widget_scaling)) for value in self.cget("size"))
        if size not in self._native_images:
            buffer = io.BytesIO()
            self.cget("light_image").resize(size).save(buffer, format="PNG")
            self._native_images[size] = tk.PhotoImage(master=self.master,
                data=base64.b64encode(buffer.getvalue()), format="png")
        return self._native_images[size]


class BrowserPanel(ctk.CTkFrame):
    def __init__(self, master, service, session_id, *, settings=None, on_configure=None, **kwargs):
        super().__init__(master, **kwargs)
        self.service, self.session_id = service, session_id
        self.on_configure = on_configure
        settings = settings or service.session_browser_settings(session_id)
        self._frame_hash, self._image = None, None
        self._preview_busy = False
        self._job = None
        self._dialogs = set()
        self._closed = False
        self._tabs = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)
        grants = ctk.CTkFrame(self)
        grants.grid(row=0, column=0, sticky="ew", padx=6, pady=5)
        grants.grid_columnconfigure(1, weight=1)
        self.enabled = tk.BooleanVar(value=settings.get("enabled", False))
        self.screenshots = tk.BooleanVar(value=settings.get("screenshots", False))
        ctk.CTkCheckBox(grants, text="Bu sohbet/proje için Tarayıcı", variable=self.enabled).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=4)
        ctk.CTkLabel(grants, text="İzinli kökenler").grid(row=1, column=0, padx=5)
        self.origins = ctk.CTkEntry(grants, placeholder_text="https://example.org, http://localhost:3000")
        self.origins.insert(0, ", ".join(settings.get("origins", [])))
        self.origins.grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        ctk.CTkLabel(grants, text="Yerel/özel kökenler").grid(row=2, column=0, padx=5)
        self.private_origins = ctk.CTkEntry(grants, placeholder_text="http://localhost:3000 (ayrıca üst listede)")
        self.private_origins.insert(0, ", ".join(settings.get("private_origins", [])))
        self.private_origins.grid(row=2, column=1, sticky="ew", padx=5, pady=3)
        ctk.CTkCheckBox(grants, text="Ekran görüntüsü / önizleme izni", variable=self.screenshots).grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=3)
        ctk.CTkButton(grants, text="İzinleri Uygula", width=130, command=self._apply_permissions).grid(row=4, column=0, columnspan=2, pady=4)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=6)
        for label, operation in (("Başlat", "browser_start"), ("←", "browser_back"), ("→", "browser_forward"), ("Yenile", "browser_reload"), ("Sekmeler", "browser_list_tabs"), ("Durum", "browser_status")):
            ctk.CTkButton(bar, text=label, width=58, height=28, command=lambda op=operation: self._command(op)).pack(side="left", padx=2)
        self.headful = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar, text="Pencereli", variable=self.headful, width=90).pack(side="left", padx=5)
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=6, pady=3)
        self.tab_selector = ctk.CTkOptionMenu(controls, values=["Sekme yok"], width=140, command=self._select_tab)
        self.tab_selector.pack(side="left", padx=2)
        for label, operation in (("Kontrolü Al / Duraklat", "browser_request_handoff"), ("Devam", "browser_resume_after_handoff")):
            ctk.CTkButton(controls, text=label, width=110, height=28, command=lambda op=operation: self._command(op)).pack(side="left", padx=2)
        ctk.CTkButton(controls, text="Durdur / Kapat", width=105, height=28, fg_color="#991b1b", command=self._stop).pack(side="left", padx=2)
        navigation = ctk.CTkFrame(self, fg_color="transparent")
        navigation.grid(row=3, column=0, sticky="ew", padx=6, pady=3)
        navigation.grid_columnconfigure(0, weight=1)
        self.url = ctk.CTkEntry(navigation, placeholder_text="İzin verilen URL")
        self.url.grid(row=0, column=0, sticky="ew", padx=3)
        self.url.bind("<Return>", lambda event: self._command("browser_navigate", {"url": self.url.get().strip()}))
        ctk.CTkButton(navigation, text="Git", width=55,
            command=lambda: self._command("browser_navigate", {"url": self.url.get().strip()})).grid(row=0, column=1, padx=3)
        self.status = ctk.CTkLabel(self, text="Tarayıcı kapalı · Henüz gözlem yok", anchor="w", wraplength=800)
        self.status.grid(row=4, column=0, sticky="ew", padx=10, pady=3)
        self.preview = ctk.CTkLabel(self, text="Önizleme için ekran görüntüsü izni verin ve canlı önizlemeyi açın.", wraplength=600)
        self.preview.grid(row=5, column=0, sticky="nsew", padx=8, pady=6)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=6)
        self.live = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(footer, text="Canlı önizleme (2 sn)", variable=self.live, command=self._poll).pack(side="left", padx=4)
        ctk.CTkButton(footer, text="Önizlemeyi Yenile", width=145, command=self._preview).pack(side="left", padx=4)
        self.history = ctk.CTkTextbox(self, height=72, state="disabled", wrap="word")
        self.history.grid(row=7, column=0, sticky="ew", padx=6, pady=5)

    @staticmethod
    def _values(entry):
        return [value.strip() for value in entry.get().split(",") if value.strip()]

    def _apply_permissions(self):
        options = {"enabled": self.enabled.get(), "origins": self._values(self.origins),
                   "private_origins": self._values(self.private_origins), "screenshots": self.screenshots.get()}
        try:
            if self.on_configure:
                self.on_configure(options)
            else:
                self.service.set_session_browser(self.session_id, **options)
            self.status.configure(text="Tarayıcı izinleri güncellendi. Kimlik doğrulama için pencereli başlatın.")
        except Exception as error:
            self._error(str(error))

    def _approve(self, payload, decide):
        if self._closed or not self.winfo_exists():
            decide(False)
            return
        from firik_agent.ui.components.operation_approval import OperationApprovalDialog
        dialog = OperationApprovalDialog(self, tool_name=payload["tool_name"], arguments=payload["arguments"],
            reason=payload.get("reason", ""), on_decision=decide)
        self._dialogs.add(dialog)
        dialog.bind("<Destroy>", lambda event: self._dialogs.discard(dialog) if event.widget is dialog else None, add="+")

    def _command(self, operation, arguments=None):
        if self._closed:
            return
        if operation == "browser_start":
            arguments = {"headless": not self.headful.get()}
        self.status.configure(text=f"{operation} · bekleniyor")
        self.service.browser_command_async(self.session_id, operation, arguments,
            on_done=lambda result: self._result(operation, result), on_error=self._error, on_approval=self._approve)

    def _result(self, operation, result):
        if self._closed or not self.winfo_exists():
            return
        try:
            data = json.loads(result.content) if hasattr(result, "content") else result
        except (ValueError, TypeError):
            data = {"message": getattr(result, "content", str(result))}
        if not isinstance(data, dict):
            data = {"result": data}
        failed = bool(getattr(result, "is_error", False))
        if data.get("state") == "manual_control":
            self.live.set(False)
            self._poll()
            self.preview.configure(image=None, text="Elle kontrol açık · önizleme duraklatıldı")
            self._image = self._frame_hash = None
        self.status.configure(text=("Hata · " if failed else "") + json.dumps(data, ensure_ascii=False)[:650])
        tabs = data.get("tabs", data.get("result", []))
        if isinstance(tabs, list) and tabs and isinstance(tabs[0], dict):
            self._tabs = {f"{index + 1}: {str(tab.get('title', tab.get('tab_id', 'Sekme')))[:35]}": tab.get("tab_id", tab.get("id"))
                          for index, tab in enumerate(tabs) if tab.get("tab_id", tab.get("id"))}
            if self._tabs:
                self.tab_selector.configure(values=list(self._tabs))
                self.tab_selector.set(next(iter(self._tabs)))
        self.history.configure(state="normal")
        self.history.insert("end", f"{operation}: {'hata' if failed else 'tamamlandı'}\n")
        if int(self.history.index("end-1c").split(".")[0]) > 80:
            self.history.delete("1.0", "2.0")
        self.history.see("end")
        self.history.configure(state="disabled")

    def _select_tab(self, label):
        if label in self._tabs:
            self._command("browser_select_tab", {"tab_id": self._tabs[label]})

    def _error(self, error):
        if not self._closed and self.winfo_exists():
            self.status.configure(text=str(error)[:650])

    def _stop(self):
        self.live.set(False)
        self.enabled.set(False)
        self.service.stop_browser_async(self.session_id)
        self.preview.configure(image=None, text="Tarayıcı kapatılıyor · son önizleme artık güncel değil")
        self.status.configure(text="Kapatma istendi; durum denetimiyle doğrulayın.")
        self._image = self._frame_hash = None

    def _preview(self):
        if self._closed or self._preview_busy or not self.winfo_ismapped():
            return
        self._preview_busy = True
        def ready(data):
            self._preview_busy = False
            if self._closed or not self.winfo_exists():
                return
            width, height = max(240, self.preview.winfo_width()), max(160, self.preview.winfo_height())
            digest = (hashlib.sha256(data).hexdigest(), width, height)
            if digest == self._frame_hash:
                return
            self._frame_hash = digest
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                ratio = min(width / image.width, height / image.height, 1)
                size = max(1, int(image.width * ratio)), max(1, int(image.height * ratio))
                self._image = BrowserPreviewImage(self, image.copy(), size=size)
            self.preview.configure(image=self._image, text="")
        def failed(error):
            self._preview_busy = False
            self._error(error)
            if not self._closed and self.winfo_exists():
                self.preview.configure(image=None, text="Önizleme güncel değil")
                self._image = self._frame_hash = None
        self.service.browser_preview_async(self.session_id, on_done=ready, on_error=failed)

    def _poll(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if not self._closed and self.live.get():
            self._preview()
            self._job = self.after(2000, self._poll)

    def destroy(self):
        self._closed = True
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        for dialog in list(self._dialogs):
            dialog.destroy()
        super().destroy()
