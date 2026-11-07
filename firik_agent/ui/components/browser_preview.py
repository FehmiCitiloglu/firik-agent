"""Inline controls and interactive view of the same managed Chromium page."""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import io
import json
import sys
import time
import tkinter as tk
from urllib.parse import quote_plus, urlsplit

import customtkinter as ctk
from PIL import Image


class BrowserPreviewImage(ctk.CTkImage):
    """Decode PNG in this Tk interpreter, avoiding Pillow/Tk ABI mismatches."""
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


def browser_address(value):
    """Accept an address without a scheme, or search ordinary words over HTTPS."""
    value = value.strip()
    if not value:
        raise ValueError("Bir web adresi veya arama yazın.")
    if len(value) > 4096:
        raise ValueError("Web adresi çok uzun.")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("Geçerli bir web adresi yazın.")
    if "://" in value or value.startswith(("about:", "file:", "data:", "javascript:")):
        return value
    if " " in value or ("." not in value and not value.startswith(("localhost", "["))):
        return "https://www.google.com/search?q=" + quote_plus(value)
    return "https://" + value


_ERROR_TEXT = {
    "permission_denied": "Tarayıcı erişimi kapalı. Adresi yeniden açarak etkinleştirin.",
    "insecure_http_denied": "Güvenli bağlantı gerekli. Adres https:// ile başlamalı.",
    "origin_denied": "Bu adres güvenlik kuralları nedeniyle açılamıyor.",
    "session_missing": "Tarayıcı kapalı. Bir adres açarak yeniden başlatın.",
    "session_unavailable": "Tarayıcı oturumu kapandı. Bir adres açarak yeniden başlatın.",
    "browser_launch_failed": "Tarayıcı açılamadı. Yeniden dene ile tekrar başlatabilirsiniz.",
    "origin_not_allowed": "Bu adres güvenlik kuralları nedeniyle açılamıyor.",
    "https_required": "Güvenli bağlantı gerekli. Adres https:// ile başlamalı.",
    "private_network_denied": "Yerel ve özel ağ adresleri güvenlik nedeniyle engelli.",
    "browser_not_installed": "Chromium bulunamadı. Tarayıcı bileşeninin kurulması gerekiyor.",
    "browser_dependency_missing": "Tarayıcı bileşeni bulunamadı. Uygulamayı güncelleyin.",
    "manual_control": "Kontrol sizde. İşiniz bitince Ajana geri ver düğmesine basın.",
    "authentication_handoff_required": "Giriş yapmak için Kontrolü al düğmesine basın.",
    "navigation_timeout": "Sayfa yüklenmesi zaman aşımına uğradı. Adresi kontrol edip yeniden deneyin.",
    "session_not_found": "Tarayıcı kapalı. Bir adres açarak yeniden başlatın.",
}


class BrowserPanel(ctk.CTkFrame):
    def __init__(self, master, service, session_id, *, settings=None, on_configure=None, on_close=None, **kwargs):
        super().__init__(master, **kwargs)
        self.service, self.session_id = service, session_id
        self.on_configure = on_configure
        self._settings = dict(settings if settings is not None else service.session_browser_settings(session_id))
        self._frame_hash, self._image = None, None
        self._preview_busy = self._busy = self._input_busy = False
        self._job = None
        self._dialogs = set()
        self._closed = False
        self._tabs = {}
        self._active_tab = ""
        self._state = "uninitialized"
        self._viewport = self._render_size = None
        self._input_queue = deque()
        self._input_epoch = 0
        self._last_status = 0
        self._settings_visible = False
        self._retry_action = None
        self.enabled = tk.BooleanVar(value=self._settings.get("enabled", False))
        self.screenshots = tk.BooleanVar(value=self._settings.get("screenshots", False))
        self.live = tk.BooleanVar(value=True)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(heading, text="Tarayıcı", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        if on_close:
            ctk.CTkButton(heading, text="✕", width=28, height=26, fg_color="transparent",
                command=on_close).pack(side="right")
        ctk.CTkButton(heading, text="Ayarlar", width=58, height=26, fg_color="transparent",
            command=self._toggle_settings).pack(side="right", padx=4)

        tabs = ctk.CTkFrame(self, fg_color="transparent")
        tabs.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        self.tab_selector = ctk.CTkOptionMenu(tabs, values=["Yeni sekme"], command=self._select_tab)
        self.tab_selector.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(tabs, text="+", width=30, height=28,
            command=lambda: self._command("browser_new_tab")).pack(side="left", padx=2)
        ctk.CTkButton(tabs, text="✕", width=30, height=28, fg_color="transparent",
            command=self._close_tab).pack(side="left", padx=2)

        navigation = ctk.CTkFrame(self, fg_color="transparent")
        navigation.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        navigation.grid_columnconfigure(3, weight=1)
        for index, (label, op) in enumerate((("←", "browser_back"), ("→", "browser_forward"), ("↻", "browser_reload"))):
            ctk.CTkButton(navigation, text=label, width=30, height=30,
                command=lambda operation=op: self._command(operation)).grid(row=0, column=index, padx=(0, 3))
        self.url = ctk.CTkEntry(navigation, placeholder_text="Adres veya arama · HTTPS", height=32)
        self.url.grid(row=0, column=3, sticky="ew", padx=3)
        self.url.bind("<Return>", lambda event: self._navigate())
        self.go_button = ctk.CTkButton(navigation, text="Git", width=42, height=30, command=self._navigate)
        self.go_button.grid(row=0, column=4, padx=(3, 0))

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew", padx=8, pady=2)
        self.control_button = ctk.CTkButton(controls, text="Kontrolü al", width=120, height=28,
            command=self._toggle_control)
        self.control_button.pack(side="left", padx=(0, 4))
        ctk.CTkButton(controls, text="Kapat", width=65, height=28, fg_color="transparent",
            command=self._stop).pack(side="right")
        self.mode = ctk.CTkLabel(controls, text="Sohbetle aynı oturum", font=ctk.CTkFont(size=11))
        self.mode.pack(side="left", padx=4)

        self.status = ctk.CTkLabel(self, text="Adres yazıp Git'e basın. Sayfalar bu sohbetle paylaşılır.",
            anchor="w", justify="left", wraplength=500, font=ctk.CTkFont(size=12))
        self.status.grid(row=4, column=0, sticky="ew", padx=12, pady=(3, 0))
        self.preview = ctk.CTkLabel(self, text="Web'de gezinin\n\nBir adres yazın veya arama yapın.\nGenel HTTPS siteleri için domain eklemeniz gerekmez.",
            fg_color=("#f1f5f9", "#0f172a"), corner_radius=8, wraplength=420)
        self.preview.grid(row=5, column=0, sticky="nsew", padx=8, pady=6)
        self.preview._label.configure(takefocus=True)
        self.preview.bind("<Button-1>", self._page_click)
        self.preview.bind("<MouseWheel>", self._page_scroll)
        self.preview.bind("<Button-4>", lambda event: self._page_scroll(event, -160))
        self.preview.bind("<Button-5>", lambda event: self._page_scroll(event, 160))
        self.preview.bind("<KeyPress>", self._page_key)
        self.bind("<Configure>", self._resize_status, add="+")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.security_label = ctk.CTkLabel(footer, text="HTTPS · HTTP ve özel ağ engelli", font=ctk.CTkFont(size=10))
        self.security_label.pack(side="left")
        self.retry_button = ctk.CTkButton(footer, text="Yeniden dene", width=95, height=25, command=self._retry)
        self.retry_button.pack(side="right")
        self.retry_button.pack_forget()

        self.settings_frame = ctk.CTkFrame(self)
        ctk.CTkCheckBox(self.settings_frame, text="Bu sohbette tarayıcı erişimi", variable=self.enabled,
            command=self._permission_changed).pack(anchor="w", padx=10, pady=6)
        ctk.CTkCheckBox(self.settings_frame, text="Canlı sayfa görünümü ve ekran görüntüsü", variable=self.screenshots,
            command=self._apply_permissions).pack(anchor="w", padx=10, pady=6)
        ctk.CTkLabel(self.settings_frame, text="Genel HTTPS siteleri açıktır. Güvensiz bağlantılar ve özel ağ adresleri engellenir.",
            wraplength=450, justify="left", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=10, pady=(2, 8))
        self._poll()

    def _resize_status(self, event):
        width = max(200, int(event.width / self._get_widget_scaling()) - 24)
        self.status.configure(wraplength=width)
        self.preview.configure(wraplength=max(200, width - 30))
        manual = self._state == "manual_control"
        self.mode.configure(text=("Ajan duraklatıldı" if manual else "Aynı oturum") if width < 400
            else ("Kontrol sizde · ajan duraklatıldı" if manual else "Sohbetle aynı oturum"))

    def _toggle_settings(self):
        self._settings_visible = not self._settings_visible
        if self._settings_visible:
            self.settings_frame.grid(row=7, column=0, sticky="ew", padx=8, pady=(0, 8))
        else:
            self.settings_frame.grid_remove()

    def _apply_permissions(self):
        options = {"enabled": self.enabled.get(), "origins": list(self._settings.get("origins", [])),
                   "private_origins": list(self._settings.get("private_origins", [])), "screenshots": self.screenshots.get()}
        try:
            if self.on_configure:
                self.on_configure(options)
            else:
                self.service.set_session_browser(self.session_id, **options)
            self._settings.update(options)
            if not self.screenshots.get():
                self._input_epoch += 1
                self._input_queue.clear()
                self.preview.configure(image=None, text="Canlı sayfa görünümü kapalı. Ayarlardan açabilirsiniz.")
                self._image = self._frame_hash = self._viewport = self._render_size = None
            return True
        except Exception as error:
            self._error(str(error))
            return False

    def _permission_changed(self):
        if self.enabled.get():
            self.screenshots.set(True)
            if self._apply_permissions():
                self._command("browser_start")
        else:
            self._stop()
            self._apply_permissions()

    def _activate(self):
        # Starting/navigation are explicit host gestures. Apply the displayed
        # grant before submitting a tool so a checked switch cannot lag behind.
        self.enabled.set(True)
        self.screenshots.set(True)
        return self._apply_permissions()

    def _approve(self, payload, decide):
        if self._closed or not self.winfo_exists():
            decide(False)
            return
        from firik_agent.ui.components.operation_approval import OperationApprovalDialog
        dialog = OperationApprovalDialog(self, tool_name=payload["tool_name"], arguments=payload["arguments"],
            reason=payload.get("reason", ""), on_decision=decide)
        self._dialogs.add(dialog)
        dialog.bind("<Destroy>", lambda event: self._dialogs.discard(dialog) if event.widget is dialog else None, add="+")

    @staticmethod
    def _data(result):
        try:
            data = json.loads(result.content) if hasattr(result, "content") else result
        except (ValueError, TypeError):
            data = {"message": getattr(result, "content", str(result))}
        return data if isinstance(data, dict) else {"result": data}

    def _navigate(self):
        if self._busy:
            return
        try:
            address = browser_address(self.url.get())
        except ValueError as error:
            self._error(str(error))
            return
        self._command("browser_navigate", {"url": address})

    def _command(self, operation, arguments=None, *, quiet=False, after=None):
        if self._closed:
            return
        needs_start = operation not in {"browser_status", "browser_list_tabs", "browser_start", "browser_resume_after_handoff"}
        if not quiet and operation not in {"browser_status", "browser_list_tabs", "browser_resume_after_handoff"}:
            if not self._activate():
                return
            if operation != "browser_start" or after is None:
                self._retry_action = (operation, arguments)
            self.retry_button.pack_forget()
        if needs_start and self._state in {"uninitialized", "closed", "failed"}:
            self._command("browser_start", after=lambda: self._command(operation, arguments, after=after))
            return
        if operation == "browser_start":
            self.update_idletasks()
            scale = self._get_widget_scaling()
            arguments = {"headless": True,
                "width": max(320, min(2560, int(self.preview.winfo_width() / scale) - 8)),
                "height": max(240, min(1440, int(self.preview.winfo_height() / scale) - 8))}
        if not quiet:
            self._busy = True
            self.status.configure(text="Tarayıcı açılıyor…" if operation == "browser_start" else "Yükleniyor…")
        def done(result):
            if not quiet:
                self._busy = False
            if self._result(operation, result, quiet=quiet) and after:
                after()
        def failed(error):
            self._busy = False
            self._error(error)
        self.service.browser_command_async(self.session_id, operation, arguments,
            on_done=done, on_error=failed, on_approval=self._approve)

    def _result(self, operation, result, *, quiet=False):
        if self._closed or not self.winfo_exists():
            return False
        data = self._data(result)
        if bool(getattr(result, "is_error", False)) or data.get("error"):
            if data.get("error") in {"browser_launch_failed", "session_missing", "session_unavailable"}:
                self._state = "failed"
                self.preview.configure(image=None, text="Tarayıcı kapandı. Yeniden dene ile tekrar açabilirsiniz.")
                self._image = self._frame_hash = self._viewport = self._render_size = None
            self._error(data)
            return False
        previous = self._state
        reported_state = data.get("state", self._state)
        if reported_state != "busy":
            self._state = reported_state
        if operation == "browser_start" and self._state == "uninitialized":
            self._state = "ready"
        if self._state in {"uninitialized", "closed", "failed"} and previous != self._state:
            self.preview.configure(image=None, text="Tarayıcı kapandı. Bir adres açarak yeniden başlayabilirsiniz.")
            self._image = self._frame_hash = self._viewport = self._render_size = None
        if self._state != previous:
            self._input_epoch += 1
            self._input_queue.clear()
        manual = self._state == "manual_control"
        self.control_button.configure(text="Ajana geri ver" if manual else "Kontrolü al")
        narrow = self.winfo_width() / self._get_widget_scaling() < 424
        self.mode.configure(text=("Ajan duraklatıldı" if manual else "Aynı oturum") if narrow
            else ("Kontrol sizde · ajan duraklatıldı" if manual else "Sohbetle aynı oturum"))
        self._active_tab = data.get("active_tab_id", data.get("tab_id", self._active_tab))
        tabs = data.get("tabs")
        if isinstance(tabs, list):
            self._tabs = {f"{index + 1} · {str(tab.get('title') or urlsplit(tab.get('url', '')).hostname or 'Yeni sekme')[:42]}": tab.get("tab_id", tab.get("id"))
                          for index, tab in enumerate(tabs) if tab.get("tab_id", tab.get("id"))}
            self.tab_selector.configure(values=list(self._tabs) or ["Yeni sekme"])
            selected = next((label for label, tab_id in self._tabs.items() if tab_id == self._active_tab), next(iter(self._tabs), "Yeni sekme"))
            self.tab_selector.set(selected)
            selected_tab = next((tab for tab in tabs if tab.get("tab_id", tab.get("id")) == self._active_tab), None)
            if selected_tab:
                data.setdefault("url", selected_tab.get("url", ""))
        if "url" in data and self.focus_get() is not self.url._entry:
            self.url.delete(0, "end")
            if data["url"] != "about:blank":
                self.url.insert(0, data["url"])
        if not quiet:
            self.status.configure(text="Kontrol sizde. Sayfaya tıklayıp yazabilirsiniz; ajan bekliyor." if manual else "Hazır · Sayfayla etkileşmek için Kontrolü al'a basın.")
        if self._state in {"ready", "active", "manual_control", "paused"}:
            self._preview()
            if not quiet and operation not in {"browser_status", "browser_list_tabs"}:
                self._command("browser_status", quiet=True)
                if not manual:
                    self._command("browser_list_tabs", quiet=True)
        return True

    def _select_tab(self, label):
        if label in self._tabs:
            self._command("browser_select_tab", {"tab_id": self._tabs[label]})

    def _close_tab(self):
        if self._active_tab:
            self._command("browser_close_tab", {"tab_id": self._active_tab})

    def _toggle_control(self):
        operation = "browser_resume_after_handoff" if self._state == "manual_control" else "browser_request_handoff"
        self._command(operation)

    def _error(self, error):
        if self._closed or not self.winfo_exists():
            return
        if isinstance(error, dict):
            if error.get("error") in {"browser_launch_failed", "session_missing", "session_unavailable"}:
                self._state = "failed"
                self.preview.configure(image=None, text="Tarayıcı kapandı. Yeniden dene ile tekrar açabilirsiniz.")
                self._image = self._frame_hash = self._viewport = self._render_size = None
            message = _ERROR_TEXT.get(error.get("error"), error.get("message", "Sayfa açılamadı."))
            if error.get("requires_reconciliation"):
                message += " Sonucu sayfadan kontrol edin; işlem otomatik yinelenmedi."
        else:
            message = str(error)
        self.status.configure(text=str(message)[:500])
        if self._retry_action:
            self.retry_button.pack(side="right")

    def _retry(self):
        if self._retry_action and not self._busy:
            operation, arguments = self._retry_action
            self._command(operation, arguments)

    def _stop(self):
        self.enabled.set(False)
        self._state = "closed"
        self._input_epoch += 1
        self._input_queue.clear()
        self.service.stop_browser_async(self.session_id)
        self.preview.configure(image=None, text="Tarayıcı kapalı\nBir adres açarak yeniden başlayabilirsiniz.")
        self.status.configure(text="Tarayıcı kapatılıyor.")
        self.control_button.configure(text="Kontrolü al")
        self._image = self._frame_hash = self._viewport = self._render_size = None

    def _preview(self):
        if self._closed or self._preview_busy or not self.winfo_ismapped() or not self.screenshots.get():
            return
        if self._state in {"uninitialized", "closed", "failed"}:
            return
        self._preview_busy = True
        epoch = self._input_epoch
        def ready(data):
            self._preview_busy = False
            if self._closed or not self.winfo_exists() or epoch != self._input_epoch or not self.screenshots.get():
                return
            scaling = self._get_widget_scaling()
            width, height = max(1, int(self.preview.winfo_width() / scaling) - 8), max(1, int(self.preview.winfo_height() / scaling) - 8)
            digest = (hashlib.sha256(data).hexdigest(), width, height)
            if digest == self._frame_hash:
                return
            self._frame_hash = digest
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                ratio = min(width / image.width, height / image.height)
                size = max(1, int(image.width * ratio)), max(1, int(image.height * ratio))
                self._viewport, self._render_size = image.size, size
                self._image = BrowserPreviewImage(self, image.copy(), size=size)
            self.preview.configure(image=self._image, text="")
        def failed(error):
            self._preview_busy = False
            if not self._closed and epoch == self._input_epoch:
                self._error(error)
        self.service.browser_preview_async(self.session_id, on_done=ready, on_error=failed)

    def _page_point(self, event):
        if not self._viewport or not self._render_size:
            return None
        scale = self._get_widget_scaling()
        rendered_width, rendered_height = (value * scale for value in self._render_size)
        left = self.preview.winfo_rootx() + (self.preview.winfo_width() - rendered_width) / 2
        top = self.preview.winfo_rooty() + (self.preview.winfo_height() - rendered_height) / 2
        x, y = event.x_root - left, event.y_root - top
        if not 0 <= x < rendered_width or not 0 <= y < rendered_height:
            return None
        return {"x": round(x * self._viewport[0] / rendered_width), "y": round(y * self._viewport[1] / rendered_height)}

    def _page_click(self, event):
        point = self._page_point(event)
        if point and self._state == "manual_control":
            self.preview._label.focus_set()
            self._input("click", point)
        elif self._state not in {"uninitialized", "closed"}:
            self.status.configure(text="Sayfayı kullanmak için önce Kontrolü al'a basın.")
        return "break"

    def _page_scroll(self, event, delta=None):
        if self._state == "manual_control":
            if delta is None:
                delta = -event.delta * 35 if sys.platform == "darwin" else -event.delta / 120 * 160
            self._input("scroll", {"delta_y": max(-2000, min(2000, round(delta)))})
        return "break"

    def _page_key(self, event):
        modifier = bool(event.state & (0x8 if sys.platform == "darwin" else 0x4))
        if modifier and event.keysym.lower() == "l":
            self.url.focus_set()
            self.url.select_range(0, "end")
            return "break"
        if self._state != "manual_control":
            return "break"
        if modifier and event.keysym.lower() == "v":
            try:
                self._input("text", {"text": self.clipboard_get()[:10000]})
            except tk.TclError:
                pass
        elif modifier and event.keysym.lower() in {"a", "c", "x", "z"}:
            self._input("key", {"key": ("Meta+" if sys.platform == "darwin" else "Control+") + event.keysym.lower()})
        elif event.keysym in {"Return", "KP_Enter", "BackSpace", "Delete", "Tab", "Escape", "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}:
            key = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Prior": "PageUp", "Next": "PageDown"}.get(event.keysym, event.keysym)
            if event.state & 1 and key == "Tab":
                key = "Shift+Tab"
            self._input("key", {"key": key})
        elif event.char and event.char.isprintable() and not modifier:
            self._input("text", {"text": event.char})
        return "break"

    def _input(self, kind, arguments):
        if self._closed or self._state != "manual_control":
            return
        if len(self._input_queue) >= 128:
            return
        self._input_queue.append((kind, arguments))
        self._next_input()

    def _next_input(self):
        if self._input_busy or not self._input_queue or self._closed or self._state != "manual_control":
            return
        kind, arguments = self._input_queue.popleft()
        self._input_busy = True
        epoch = self._input_epoch
        def done(result):
            self._input_busy = False
            if not self._closed and epoch == self._input_epoch:
                self._preview()
            self._next_input()
        def failed(error):
            self._input_busy = False
            if not self._closed and epoch == self._input_epoch:
                self._input_queue.clear()
                self._error(error)
            else:
                self._next_input()
        self.service.browser_input_async(self.session_id, kind, arguments, on_done=done, on_error=failed)

    def _poll(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if not self._closed:
            if self.live.get() and self.enabled.get():
                self._preview()
                if time.monotonic() - self._last_status >= 4 and not self._busy:
                    self._last_status = time.monotonic()
                    self._command("browser_status", quiet=True)
            self._job = self.after(650 if self._state == "manual_control" else 1200, self._poll)

    def destroy(self):
        self._closed = True
        self._input_queue.clear()
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        for dialog in list(self._dialogs):
            dialog.destroy()
        super().destroy()
