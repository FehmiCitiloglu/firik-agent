"""firik-agent masaüstü uygulaması ana pencere ve gezinme bileşeni.

Çapraz platform (macOS, Windows, Linux) destekli, tamamen Türkçe,
modern koyu/açık temalı grafiksel kullanıcı arayüzü (GUI).
Uygulama adı: 'firik-agent'
"""
from __future__ import annotations

import os
import platform
import queue
import sys
import threading
from tkinter import messagebox
try:
    import tkinter as tk
    import customtkinter as ctk
except (ImportError, ModuleNotFoundError) as err:
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    hint = ""
    if sys.platform == "darwin":
        hint = f"\nHomebrew ile kurmak için:\n  brew install python-tk@{py_ver}"
    elif sys.platform.startswith("linux"):
        hint = "\nLinux için:\n  sudo apt install python3-tk"
    elif sys.platform == "win32":
        hint = "\nWindows için:\n  Python kurulumunda 'tcl/tk and IDLE' seçeneğini işaretleyerek Python'ı yeniden yükleyin."

    raise ImportError(
        f"firik-agent masaüstü arayüzü için 'tkinter' modülü bulunamadı ({err}).{hint}"
    ) from err

from firik_agent.ui.icons import ensure_app_icon
from firik_agent.ui.service import FirikService
from firik_agent.ui.theme import APP_NAME
from firik_agent.ui.views.about_view import AboutView
from firik_agent.ui.views.chat_view import ChatView
from firik_agent.ui.views.chat_workspace import ChatWorkspace
from firik_agent.ui.views.mcp_view import MCPView
from firik_agent.ui.views.improvement_view import ImprovementView
from firik_agent.ui.views.ocr_view import OCRView
from firik_agent.ui.views.projects_view import ProjectsView
from firik_agent.ui.views.quota_view import QuotaView
from firik_agent.ui.views.rerank_view import RerankView
from firik_agent.ui.views.settings_view import SettingsView
from firik_agent.ui.views.transcribe_view import TranscribeView

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


class FirikApp(ctk.CTk):
    """firik-agent masaüstü uygulaması ana penceresi."""

    def __init__(self, *, config_path=None) -> None:
        _ensure_tk_environment()
        super().__init__()

        # Pencere Başlığı ve Boyutlandırma: Adı SADECE 'firik-agent'
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
        self.service = FirikService(dispatch=self._enqueue_callback, config_path=config_path)
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

    def _windows_set_titlebar_color(self, color_mode):
        self._voice_native_theme_refresh = True
        try:
            return super()._windows_set_titlebar_color(color_mode)
        finally:
            self._voice_native_theme_refresh = False

    def destroy(self) -> None:
        if self._closing:
            return
        self._closing = True
        # Invalidate native and queued voice callbacks before destroying Tk.
        for controller in list(self.service._voice_controllers):
            controller.close()
        self.service.cancel_active_stream()
        def cleanup():
            self.service.shutdown()
            if hasattr(self.service, "projects") and hasattr(self.service.projects, "scheduler"):
                self.service.projects.scheduler.stop()
        # Network subprocess shutdown and worker joins must not freeze the UI.
        self._shutdown_thread = threading.Thread(target=cleanup, name="FirikShutdown", daemon=False)
        self._shutdown_thread.start()
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
            ("improvement", "🧠  Kendini Geliştirme"),
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
        self.views["improvement"] = ImprovementView(self.content_container, service=self.service)
        self.views["settings"] = SettingsView(self.content_container, service=self.service)
        self.views["about"] = AboutView(self.content_container)

    def open_existing_coding_conversation(self, *, continue_voice=False) -> bool:
        """Navigate to an existing coding session without starting agent work."""
        projects = self.views["projects"]
        candidates = []
        for project in projects.project_service.db.list_projects():
            if (project.id in projects._coding_states
                    or self.service.coding_store.latest_run(project.id) is not None):
                candidates.append(project)
        current = next((p for p in candidates if p.id == projects.current_project_id), None)

        def open_project(project_id):
            if projects.current_project_id != project_id or not projects.workspace_container.winfo_ismapped():
                projects.show_workspace_screen(project_id)
            if projects.active_workspace_tab != "coding":
                projects._switch_workspace_tab("coding")
            self.show_view("projects")
            if continue_voice:
                self._continue_voice_navigation()

        if current is not None or len(candidates) == 1:
            open_project((current or candidates[0]).id)
            return True
        if not candidates:
            messagebox.showinfo("Kodlama sohbeti", "Mevcut bir kodlama sohbeti bulunamadı. Önce bir projede kodlama sohbeti açın.", parent=self)
            return False
        dialog = ctk.CTkToplevel(self)
        dialog.title("Kodlama sohbetini seçin")
        dialog.geometry("420x320")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Hangi mevcut kodlama sohbetine geçilsin?").pack(padx=16, pady=12)
        choices = ctk.CTkScrollableFrame(dialog)
        choices.pack(fill="both", expand=True, padx=12, pady=12)
        for project in candidates:
            def choose(project_id=project.id):
                dialog.destroy()
                open_project(project_id)
            ctk.CTkButton(choices, text=project.name, command=choose).pack(fill="x", pady=4)
        return True

    def voice_destinations(self):
        """Snapshot the application's actual tabs, histories and project IDs."""
        from firik_agent.voice.requests import VoiceDestination
        result = [VoiceDestination("tab", name, name) for name in self.views]
        chat = self.views.get("chat")
        conversations = {row["session_id"]: row.get("title") or row["session_id"]
                         for row in self.service.list_chat_history()}
        for sid, page in getattr(chat, "pages", {}).items():
            conversations.setdefault(sid, page.session.title)
        result.extend(VoiceDestination("conversation", sid, title, session_id=sid)
                      for sid, title in conversations.items())
        projects = self.views.get("projects")
        if projects is not None:
            result.extend(VoiceDestination("project", project.id, project.name, project_id=project.id)
                          for project in projects.project_service.db.list_projects())
        return tuple(result)

    def _active_voice_panel(self):
        if self.active_tab == "chat":
            return getattr(self.views["chat"].current, "voice_panel", None)
        projects = self.views.get("projects")
        if self.active_tab == "projects" and getattr(projects, "active_workspace_tab", None) == "coding":
            panel = getattr(projects, "coding_voice_panel", None)
            return panel if (panel is not None and panel.winfo_exists()
                             and projects.workspace_container.winfo_manager() == "grid") else None
        return None

    def _continue_voice_navigation(self):
        """Continue an explicitly navigated voice conversation in its new scope."""
        panel = self._active_voice_panel()
        if panel is None:
            return
        session_id = panel.get_session_id()
        def resume():
            if (self._closing or self._active_voice_panel() is not panel or panel._closed
                    or not panel._active or panel.get_session_id() != session_id):
                return
            if panel.is_busy():
                panel._controller().sync_agent_state(True)
                panel.show_voice_surface()
            else:
                panel.start_conversation()
        self.after_idle(resume)

    def handle_voice_command(self, request) -> bool:
        """Deterministic UI actions only; agent tasks use their normal composer."""
        from firik_agent.voice.commands import VoiceCommand
        from firik_agent.voice.summaries import agent_activity_summary, coding_speech_summary
        projects, chat = self.views.get("projects"), self.views.get("chat")
        command, destination = request.command, request.destination
        state = getattr(projects, "_coding_states", {}).get(request.project_id) if projects else None
        page = getattr(chat, "pages", {}).get(request.session_id) if chat else None
        panel = getattr(projects, "coding_voice_panel", None) if request.intended_target == "coding" else getattr(page, "voice_panel", None)
        follow_voice = bool(panel is not None and panel._conversation)
        if destination is not None:
            if destination not in self.voice_destinations():
                return False
            if command == VoiceCommand.OPEN_TAB and destination.id in self.views:
                self.show_view(destination.id)
                if follow_voice:
                    self._continue_voice_navigation()
                return True
            if command == VoiceCommand.SELECT_CONVERSATION and chat is not None:
                # open_chat reuses the page or existing session restoration.
                chat.open_chat(destination.id)
                self.show_view("chat")
                if follow_voice:
                    self._continue_voice_navigation()
                return True
            if command == VoiceCommand.SELECT_PROJECT and projects is not None:
                projects.show_workspace_screen(destination.id)
                projects._switch_workspace_tab("coding")
                self.show_view("projects")
                if follow_voice:
                    self._continue_voice_navigation()
                return True
        if command == VoiceCommand.NEW_CHAT and chat is not None:
            chat.new_chat()
            self.show_view("chat")
            if follow_voice:
                self._continue_voice_navigation()
            return True
        if command == VoiceCommand.SWITCH_CODING:
            return self.open_existing_coding_conversation(continue_voice=follow_voice)
        if command in {VoiceCommand.PAUSE_AGENT, VoiceCommand.RESUME_AGENT}:
            if not state or request.intended_target != "coding":
                from firik_agent.voice.intent_router import VoiceUnsupportedCommand
                raise VoiceUnsupportedCommand("Görevi duraklatma ve sürdürme kodlama oturumunda kullanılabilir. Kodlamaya geçip yeniden deneyin.")
            action = "pause" if command == VoiceCommand.PAUSE_AGENT else "resume"
            if not self.service.coding_control(request.session_id, action):
                return False
            state["paused"] = action == "pause"
            state["status"] = "İşlem sınırında duraklatılıyor…" if action == "pause" else "Devam ediliyor…"
            if projects.current_project_id == request.project_id:
                projects._refresh_coding_display()
            if panel is not None:
                panel.command_feedback(state["status"])
            return True
        if command == VoiceCommand.VIEW_ACTIVITY:
            if state and projects.current_project_id == request.project_id:
                projects.coding_tabs.set("Etkinlik")
                return True
            activity = getattr(page, "_current_activity", None)
            if activity is not None:
                if not activity.is_expanded:
                    activity._toggle_expand()
                return True
            return False
        if command == VoiceCommand.AGENT_STATUS:
            summary = ("Görev duraklatıldı." if state and state.get("busy") and state.get("paused") else
                       "İşlem onayı bekleniyor; ana penceredeki onay ekranını inceleyin." if state and state.get("busy") and state.get("pending_approvals") else
                       agent_activity_summary(state["cockpit"].agents, state["activities"]) if state and state.get("busy") else
                       "Mevcut sohbet ajanı yanıt hazırlıyor." if page and page.is_streaming else "Bu oturumda çalışan ajan yok.")
        elif command in {VoiceCommand.LIST_PROJECTS, VoiceCommand.LIST_CONVERSATIONS}:
            kind = "project" if command == VoiceCommand.LIST_PROJECTS else "conversation"
            items = [item.label for item in self.voice_destinations() if item.kind == kind]
            title = "Projeleriniz" if kind == "project" else "Sohbetleriniz"
            summary = (title + ": " + "; ".join(items[:10]) + "." if items else title + " henüz boş.")
            if len(items) > 10:
                summary += f" Toplam {len(items)} kayıt var; ilk onu gösteriliyor."
            summary += (' Geçiş için “proje adı projesine geç” diyebilirsiniz.' if kind == "project" else
                        ' Geçiş için “sohbet adı sohbetine geç” diyebilirsiniz.')
        elif command == VoiceCommand.TASK_SUMMARY:
            if state:
                project = projects.project_service.db.get_project(request.project_id)
                last = next((row.get("content", "") for row in reversed(state.get("messages", ()))
                             if row.get("role") == "assistant" and row.get("content")), "")
                summary = coding_speech_summary(last or state.get("status") or "Henüz görev sonucu yok.",
                    project_name=project.name if project else None,
                    activities=state.get("activities", ()))
                if state.get("busy"):
                    summary = "Görev henüz sürüyor. " + summary
            else:
                last = next((row.get("content", "") for row in reversed(getattr(page, "messages", ()))
                             if row.get("role") == "assistant" and not row.get("pending")), "")
                summary = coding_speech_summary(last) if last else "Bu oturumda henüz tamamlanan bir yanıt yok."
        elif command == VoiceCommand.SHOW_PLAN:
            plan = state.get("plan", ()) if state else ()
            if isinstance(plan, dict):
                plan = plan.get("steps", ())
            if not isinstance(plan, (list, tuple)):
                plan = [plan] if isinstance(plan, str) else ()
            steps = [str(item.get("title") or item.get("step") or item.get("description") or "")
                     if isinstance(item, dict) else str(item) for item in list(plan or ())[:8]]
            summary = "Görev planı: " + "; ".join(filter(None, steps)) if any(steps) else "Bu oturumda henüz kayıtlı bir görev planı yok."
        elif command == VoiceCommand.HELP:
            summary = ("Projeleri ve sohbetleri listeleyebilir, adlarıyla aralarında geçebilir, görev durumunu ve son sonucu sorabilirsiniz. "
                       "Kodlama oturumunda görevi duraklatabilir, sürdürebilir veya durdurabilirsiniz. "
                       "Yeni bir görev için ne yapılacağını söyleyin. İşlem onayları ana pencereden verilir.")
        elif command == VoiceCommand.DESCRIBE_PROJECT:
            project_id = request.project_id or getattr(projects, "current_project_id", None)
            project = projects.project_service.db.get_project(project_id) if projects and project_id else None
            if project is None:
                return False
            project_state = projects._coding_states.get(project_id)
            summary = coding_speech_summary(project_state["status"] if project_state else "Proje seçili.", project_name=project.name)
        else:
            return False
        if panel is None:
            return False
        from firik_agent.projects.indexer import sanitize_secrets
        from firik_agent.voice.speech import speech_text
        panel.command_feedback(speech_text(sanitize_secrets(summary)))
        return True


    def show_view(self, tab_id: str) -> None:
        """Belirtilen görünümü ekranda gösterir."""
        if tab_id not in self.views:
            return

        previous = self.views.get(self.active_tab)
        if previous is not self.views[tab_id] and hasattr(previous, "set_voice_active"):
            previous.set_voice_active(False)

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
        if hasattr(self.views[tab_id], "set_voice_active"):
            self.views[tab_id].set_voice_active(True)
        self.active_tab = tab_id
        if tab_id == "improvement":
            self.views[tab_id].refresh()

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
            self.status_text.configure(text="FIRIK Çevrimiçi")

        def on_error(err):
            self.status_dot.configure(text="●", text_color="#f59e0b")
            self.status_text.configure(text="Bağlantı Hatası")

        self.service.test_connection_async(
            on_success=lambda d: self.after(0, lambda: on_success(d)),
            on_error=lambda e: self.after(0, lambda: on_error(e)),
        )


def main() -> int:
    """firik-agent masaüstü uygulamasını başlatır."""
    if "--smoke-test" in sys.argv:
        if "--check-voice-backend" in sys.argv:
            from firik_agent.voice.audio_devices import AudioDeviceCatalog
            from firik_agent.voice.errors import VoiceAudioError

            try:
                # Verify frozen PortAudio discovery even on headless audio
                # runners. Enumeration never opens an input/output stream.
                devices = AudioDeviceCatalog()
                devices.list_devices("input")
                devices.list_devices("output")
            except VoiceAudioError:
                print("Packaged PortAudio device discovery failed.", file=sys.stderr)
                return 1

        # Frozen smoke checks must never open the user's config, history,
        # projects or configured MCP processes. Use a disposable local profile.
        import tempfile
        from firik_agent.config import save_config
        with tempfile.TemporaryDirectory(prefix="firik-smoke-") as directory:
            config_path = Path(directory) / "config.yaml"
            save_config({"providers": {"firik": {"base_url": "http://127.0.0.1:9/v1",
                         "api_key_env": "FIRIK_SMOKE_UNUSED_KEY", "default_model": "glm-5.3"}},
                         "disabled_default_mcps": ["computer-use", "web"], "timeout": 1}, config_path)
            app = FirikApp(config_path=config_path)
            app.after(250, app.destroy)
            app.mainloop()
        return 0
    app = FirikApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
