"""evren masaüstü uygulaması ana pencere ve gezinme bileşeni.

Çapraz platform (macOS, Windows, Linux) destekli, tamamen Türkçe,
modern koyu/açık temalı grafiksel kullanıcı arayüzü (GUI).
Uygulama adı: 'evren'
"""
from __future__ import annotations

import os
import platform
import queue
import sys
from typing import Dict, Optional
import tkinter as tk

import customtkinter as ctk

from evren_agent.ui.icons import ensure_app_icon
from evren_agent.ui.service import EvrenService
from evren_agent.ui.theme import APP_NAME
from evren_agent.ui.views.about_view import AboutView
from evren_agent.ui.views.chat_view import ChatView
from evren_agent.ui.views.chat_workspace import ChatWorkspace
from evren_agent.ui.views.mcp_view import MCPView
from evren_agent.ui.views.ocr_view import OCRView
from evren_agent.ui.views.projects_view import ProjectsView
from evren_agent.ui.views.quota_view import QuotaView
from evren_agent.ui.views.rerank_view import RerankView
from evren_agent.ui.views.settings_view import SettingsView
from evren_agent.ui.views.transcribe_view import TranscribeView

from pathlib import Path


def _ensure_tk_environment() -> None:
    """Tcl/Tk kütüphane yollarını sanal ortamlarda (uv, venv, pyenv) otomatik tespit eder."""
    if sys.platform.startswith("linux"):
        # Process-local font policy; never change the user's system fonts.
        os.environ["FONTCONFIG_FILE"] = str(Path(__file__).parent / "assets/fonts-linux.conf")
    for base in [sys.base_prefix, sys.prefix]:
        for ver in ["tcl8.6", "tcl9.0", "tcl8.5"]:
            tcl_cand = Path(base) / "lib" / ver
            if tcl_cand.exists() and "TCL_LIBRARY" not in os.environ:
                os.environ["TCL_LIBRARY"] = str(tcl_cand)
                break
        for ver in ["tk8.6", "tk9.0", "tk8.5"]:
            tk_cand = Path(base) / "lib" / ver
            if tk_cand.exists() and "TK_LIBRARY" not in os.environ:
                os.environ["TK_LIBRARY"] = str(tk_cand)
                break


class EvrenApp(ctk.CTk):
    """evren masaüstü uygulaması ana penceresi."""

    def __init__(self) -> None:
        _ensure_tk_environment()
        super().__init__()

        # Pencere Başlığı ve Boyutlandırma: Adı SADECE 'evren'
        self.title(APP_NAME)
        self.geometry("1120x760")
        self.minsize(940, 620)

        # Varsayılan Tema
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # Simge Kurulumu
        self._setup_app_icon()

        # Servis Başlatma
        self._callbacks = queue.SimpleQueue()
        self._closing = False
        self.service = EvrenService(dispatch=self._enqueue_callback)
        self._callback_timer = self.after(25, self._drain_callbacks)

        # Izgara Düzeni (Solda Yan Menü, Sağda İçerik)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.views: Dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: Dict[str, ctk.CTkButton] = {}
        self.active_tab: Optional[str] = None

        self._build_sidebar()
        self._build_content_area()
        self._setup_keybindings()

        # İlk görünümü aç (Sohbet)
        self.show_view("chat")

        # Arka planda ilk bağlantı durumunu kontrol et
        self.after(500, self._check_initial_status)

    def _enqueue_callback(self, callback, *args) -> None:
        # Workers never call Tcl/Tk, including after(), directly.
        if not self._closing:
            self._callbacks.put((callback, args))

    def _drain_callbacks(self) -> None:
        for _ in range(100):
            try:
                callback, args = self._callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                self.report_callback_exception(*sys.exc_info())
        if not self._closing:
            self._callback_timer = self.after(25, self._drain_callbacks)

    def destroy(self) -> None:
        self._closing = True
        self.service.cancel_active_stream()
        self.service.shutdown()
        if hasattr(self.service, "projects") and hasattr(self.service.projects, "scheduler"):
            self.service.projects.scheduler.stop()
        self.after_cancel(self._callback_timer)
        super().destroy()


    def _setup_app_icon(self) -> None:
        """Pencere simgesini platforma uygun ayarlar."""
        try:
            icon_path = ensure_app_icon()
            if platform.system() == "Windows":
                # Windows için Taskbar ID ve ICO
                try:
                    import ctypes
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ssyz.firik-agent.desktop.0.2")
                except Exception:
                    pass
                ico_path = icon_path.with_suffix(".ico")
                if ico_path.exists():
                    self.iconbitmap(str(ico_path))
            else:
                from PIL import Image, ImageTk
                pil_icon = Image.open(icon_path)
                tk_icon = ImageTk.PhotoImage(pil_icon)
                self.iconphoto(True, tk_icon)
        except Exception:
            pass

    def _build_sidebar(self) -> None:
        """Sol gezinme menüsü (Sidebar)."""
        sidebar = ctk.CTkFrame(
            self,
            width=220,
            corner_radius=0,
            fg_color=("gray92", "#0b0f19"),
            border_width=1,
            border_color=("gray85", "#1e293b"),
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(8, weight=1)  # Alt durum alanını aşağı itmek için

        # Uygulama Başlık Alanı
        title_box = ctk.CTkFrame(sidebar, fg_color="transparent")
        title_box.pack(fill="x", padx=16, pady=(20, 16))

        app_title = ctk.CTkLabel(
            title_box,
            text=APP_NAME,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#38bdf8",
        )
        app_title.pack(anchor="w")

        app_desc = ctk.CTkLabel(
            title_box,
            text="Yapay Zekâ İstemcisi",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
        )
        app_desc.pack(anchor="w")

        # Gezinme Butonları Listesi
        tabs = [
            ("projects", "📁  Projeler"),
            ("chat", "💬  Sohbet"),
            ("mcp", "🔌  MCP"),
            ("ocr", "👁️  Görsel & OCR"),
            ("transcribe", "🎙️  Ses Çözümleme"),
            ("rerank", "🔍  Sıralama (Rerank)"),
            ("quota", "📊  Kota ve Durum"),
            ("settings", "⚙️  Ayarlar"),
            ("about", "ℹ️  Hakkında"),
        ]

        btn_container = ctk.CTkFrame(sidebar, fg_color="transparent")
        btn_container.pack(fill="both", expand=True, padx=12, pady=4)

        for tab_id, tab_label in tabs:
            btn = ctk.CTkButton(
                btn_container,
                text=tab_label,
                anchor="w",
                height=38,
                font=ctk.CTkFont(size=13, weight="normal"),
                fg_color="transparent",
                text_color=("gray20", "#cbd5e1"),
                hover_color=("gray80", "#1e293b"),
                corner_radius=8,
                command=lambda tid=tab_id: self.show_view(tid),
            )
            btn.pack(fill="x", pady=3)
            self.nav_buttons[tab_id] = btn

        # Bilgisayar Denetimi & Acil Durdurma Paneli
        cu_box = ctk.CTkFrame(sidebar, fg_color=("gray88", "#111827"), corner_radius=8)
        cu_box.pack(side="bottom", fill="x", padx=12, pady=(0, 8))

        cu_header = ctk.CTkFrame(cu_box, fg_color="transparent")
        cu_header.pack(fill="x", padx=8, pady=(6, 2))

        cu_lbl = ctk.CTkLabel(
            cu_header,
            text="🖥️ Bilgisayar Denetimi",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8",
        )
        cu_lbl.pack(side="left")

        self.cu_stop_btn = ctk.CTkButton(
            cu_box,
            text="⏹ ACİL DURDUR",
            height=28,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#ef4444",
            hover_color="#dc2626",
            command=self._emergency_stop_computer_use,
        )
        self.cu_stop_btn.pack(fill="x", padx=8, pady=(4, 6))

        # Alt Bölüm: Bağlantı Durumu Rozeti
        bottom_box = ctk.CTkFrame(sidebar, fg_color=("gray85", "#161f2e"), corner_radius=8)
        bottom_box.pack(side="bottom", fill="x", padx=12, pady=16)

        self.status_dot = ctk.CTkLabel(
            bottom_box,
            text="●",
            font=ctk.CTkFont(size=16),
            text_color="#94a3b8",
        )
        self.status_dot.pack(side="left", padx=(10, 4), pady=8)

        self.status_text = ctk.CTkLabel(
            bottom_box,
            text="Bağlantı Bekleniyor",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "#94a3b8"),
        )
        self.status_text.pack(side="left", padx=2, pady=8)

    def _emergency_stop_computer_use(self) -> None:
        """Kullanıcının GUI üzerinden bilgisayar kullanımını anında durdurmasını sağlar."""
        try:
            from tkinter import messagebox
            # Signal any active agent service
            self.service.stop_computer_use()
            self.cu_stop_btn.configure(text="DURDURULDU ✓", fg_color="#4b5563")
            messagebox.showinfo("Acil Durdurma", "Bilgisayar kullanımı durduruldu. Yeniden kullanmak için MCP yöneticisinden computer-use sunucusunu yeniden başlatın.")
        except Exception:
            pass


    def _build_content_area(self) -> None:
        """Sağ ana içerik alanı."""
        self.content_container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_container.grid(row=0, column=1, sticky="nsew")
        self.content_container.grid_columnconfigure(0, weight=1)
        self.content_container.grid_rowconfigure(0, weight=1)

        # Görünümleri oluştur ve sakla
        self.views["projects"] = ProjectsView(self.content_container, service=self.service)
        self.views["chat"] = ChatWorkspace(self.content_container, service=self.service)
        self.views["mcp"] = MCPView(self.content_container, service=self.service)
        self.views["ocr"] = OCRView(self.content_container, service=self.service)
        self.views["transcribe"] = TranscribeView(self.content_container, service=self.service)
        self.views["rerank"] = RerankView(self.content_container, service=self.service)
        self.views["quota"] = QuotaView(self.content_container, service=self.service)
        self.views["settings"] = SettingsView(self.content_container, service=self.service)
        self.views["about"] = AboutView(self.content_container)


    def show_view(self, tab_id: str) -> None:
        """Belirtilen görünümü ekranda gösterir."""
        if tab_id not in self.views:
            return

        # Önceki görünümü gizle
        for vid, view in self.views.items():
            view.grid_forget()

        # Buton stillerini güncelle (Aktif sekme vurgusu)
        for bid, btn in self.nav_buttons.items():
            if bid == tab_id:
                btn.configure(
                    fg_color=("#3b82f6", "#1d4ed8"),
                    text_color="#ffffff",
                    font=ctk.CTkFont(size=13, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=("gray20", "#cbd5e1"),
                    font=ctk.CTkFont(size=13, weight="normal"),
                )

        # Yeni görünümü yerleştir
        self.views[tab_id].grid(row=0, column=0, sticky="nsew")
        self.active_tab = tab_id

    def _setup_keybindings(self) -> None:
        """Klavye kısayollarını ayarlar."""
        # Yeni Sohbet: Ctrl+N veya Cmd+N
        self.bind("<Control-n>", lambda _: self._on_new_chat_shortcut())
        self.bind("<Command-n>", lambda _: self._on_new_chat_shortcut())

        # Projeler: Ctrl+P veya Cmd+P
        self.bind("<Control-p>", lambda _: self.show_view("projects"))
        self.bind("<Command-p>", lambda _: self.show_view("projects"))

        # Ayarlar: Ctrl+, veya Cmd+,
        self.bind("<Control-comma>", lambda _: self.show_view("settings"))
        self.bind("<Command-comma>", lambda _: self.show_view("settings"))


    def _on_new_chat_shortcut(self) -> None:
        self.show_view("chat")
        chat_v = self.views.get("chat")
        if isinstance(chat_v, (ChatView, ChatWorkspace)):
            chat_v.new_chat()

    def _check_initial_status(self) -> None:
        """Açılışta API anahtarı ve sunucu durumunu kontrol eder."""
        key = self.service.get_key()
        if not key:
            self.status_dot.configure(text="●", text_color="#ef4444")
            self.status_text.configure(text="API Anahtarı Yok")
            return

        def on_success(data):
            self.status_dot.configure(text="●", text_color="#10b981")
            self.status_text.configure(text="EVREN Çevrimiçi")

        def on_error(err):
            self.status_dot.configure(text="●", text_color="#f59e0b")
            self.status_text.configure(text="Bağlantı Hatası")

        self.service.test_connection_async(
            on_success=lambda d: self.after(0, lambda: on_success(d)),
            on_error=lambda e: self.after(0, lambda: on_error(e)),
        )


def main() -> int:
    """evren masaüstü uygulamasını başlatır."""
    app = EvrenApp()
    if "--smoke-test" in sys.argv:
        app.after(250, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
