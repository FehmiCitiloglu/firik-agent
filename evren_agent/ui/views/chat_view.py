"""evren masaüstü uygulaması - Sohbet Görünümü (Chat View).

Akışlı (streaming) yanıt üretimi, çok modlu görsel desteği (multimodal),
parametre kontrolleri ve sohbet geçmişi yönetimi sunar.
Tüm etiket ve açıklamalar Türkçedir.
"""
from __future__ import annotations
from evren_agent.ui.components.effort_selector import EffortSelector

import datetime
import base64
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from evren_agent.core.events import AgentEvent, AgentEventType
from evren_agent.core.session import ChatSession
from evren_agent.ui.components.tool_card import ToolActivityGroup
from evren_agent.ui.components.live_activity import LiveActivity, command_preview
from evren_agent.ui.components.markdown_message import MarkdownMessage
from evren_agent.ui.service import EvrenService, turkce_hata_mesaji
from evren_agent.ui.theme import THEME_COLORS


class MCPChatSelectorDialog(ctk.CTkToplevel):
    """Mevcut sohbet oturumuna MCP sunucularını ekleme / çıkarma seçim penceresi."""

    def __init__(self, master: Any, service: EvrenService, session: ChatSession, on_changed: Callable[[], None]) -> None:
        super().__init__(master)
        self.service = service
        self.session = session
        self.on_changed = on_changed

        self.title("Sohbete MCP Ekle / Çıkar")
        self.geometry("450x460")
        self.minsize(380, 320)
        self.grab_set()

        self._build_ui()

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header,
            text="🔌 Sohbet MCP Seçimi",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Etkinleştirilen sunucuların araçları yalnızca bu sohbette modele sunulur.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
        ).pack(anchor="w", pady=(2, 0))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=8)

        servers = self.service.get_mcp_servers()
        if not servers:
            ctk.CTkLabel(
                scroll,
                text="Yapılandırılmış MCP sunucusu bulunamadı.\nSol menüden '🔌 MCP' sekmesine giderek sunucu ekleyebilirsiniz.",
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "#94a3b8"),
                justify="center",
            ).pack(pady=40)
        else:
            for s in servers:
                name = s.get("name", "")
                tool_count = s.get("tool_count", 0)
                status = s.get("status", "disconnected")
                cmd_or_url = s.get("command_or_url", "-")

                row = ctk.CTkFrame(scroll, fg_color=("gray95", "#161f2e"), corner_radius=8, border_width=1, border_color=("gray85", "#243247"))
                row.pack(fill="x", pady=4)

                var = tk.BooleanVar(value=self.session.is_mcp_enabled(name))

                def _on_toggle(server_name=name, v=var):
                    self.service.set_session_mcp(self.session.session_id, server_name, v.get())
                    self.on_changed()

                chk = ctk.CTkCheckBox(
                    row,
                    text=f"{name} ({tool_count} araç)",
                    variable=var,
                    font=ctk.CTkFont(size=13, weight="bold"),
                    command=_on_toggle,
                )
                chk.pack(anchor="w", padx=12, pady=(8, 2))

                sub_text = f"Durum: {status} | Hedef: {cmd_or_url}"
                ctk.CTkLabel(
                    row,
                    text=sub_text,
                    font=ctk.CTkFont(size=10),
                    text_color=("gray40", "#94a3b8"),
                ).pack(anchor="w", padx=36, pady=(0, 8))

        close_btn = ctk.CTkButton(
            self,
            text="Tamam",
            width=100,
            command=self.destroy,
        )
        close_btn.pack(pady=12)


class ChatMessageBubble(ctk.CTkFrame):
    """Sohbet mesaj balonu bileşeni."""

    def __init__(
        self,
        master: Any,
        role: str,
        content: str,
        image_path: Optional[str] = None,
        timestamp: Optional[str] = None,
        image_data_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        is_user = (role == "user")
        def color(name: str):
            return THEME_COLORS["light"][name], THEME_COLORS["dark"][name]

        bg_color = color("user_bubble" if is_user else "bot_bubble")
        fg_color = bg_color

        super().__init__(
            master,
            fg_color=fg_color,
            corner_radius=12,
            border_width=1,
            border_color=color("border"),
            **kwargs,
        )

        self.role = role
        self.raw_content = content
        self.timestamp = timestamp or datetime.datetime.now().strftime("%H:%M")
        self.grid_columnconfigure(0, weight=1)

        # Üst Bilgi Satırı (Gönderen ve Zaman Damgası)
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))

        sender_title = "Siz" if is_user else "evren"
        sender_color = "#dbeafe" if is_user else color("accent_secondary")
        sender_label = ctk.CTkLabel(
            header_frame,
            text=sender_title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=sender_color,
        )
        sender_label.pack(side="left")

        time_label = ctk.CTkLabel(
            header_frame,
            text=self.timestamp,
            font=ctk.CTkFont(size=11),
            text_color="#dbeafe" if is_user else color("text_secondary"),
        )
        time_label.pack(side="left", padx=8)

        # Kopyala butonu
        copy_btn = ctk.CTkButton(
            header_frame,
            text="Kopyala",
            width=58,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            hover_color=color("bg_hover"),
            text_color="#ffffff" if is_user else color("text_secondary"),
            command=self._copy_to_clipboard,
        )
        copy_btn.pack(side="right")

        # Varsa Görsel Önizlemesi
        if image_path or image_data_url:
            try:
                if image_path and os.path.exists(image_path):
                    pil_img = Image.open(image_path)
                elif image_data_url:
                    pil_img = Image.open(BytesIO(base64.b64decode(image_data_url.split(",", 1)[1])))
                else:
                    raise FileNotFoundError(image_path)
                pil_img.thumbnail((240, 180))
                ctk_thumb = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
                img_lbl = ctk.CTkLabel(self, image=ctk_thumb, text="")
                img_lbl.grid(row=1, column=0, sticky="w", padx=12, pady=4)
            except Exception:
                img_name = Path(image_path).name if image_path else "Görsel"
                err_lbl = ctk.CTkLabel(self, text=f"📷 [Ekli Görsel: {img_name}]", font=ctk.CTkFont(size=11))
                err_lbl.grid(row=1, column=0, sticky="w", padx=12, pady=4)

        # Mesaj Metni
        if is_user:
            self.text_label = ctk.CTkLabel(
                self, text=content, font=ctk.CTkFont(size=14),
                text_color="#ffffff", width=1, wraplength=700,
                justify="left", anchor="w",
            )
        else:
            self.text_label = MarkdownMessage(self, text=content)
        self.text_label.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 10))
        self._wraplength = 700
        self.bind("<Configure>", self._resize_text, add="+")

    def _resize_text(self, event: Any) -> None:
        width = max(40, int(event.width / self._get_widget_scaling()) - 24)
        if width != self._wraplength:
            self._wraplength = width
            if self.role == "user":
                self.text_label.configure(wraplength=width)

    def update_text(self, new_text: str, *, streaming: bool = False) -> None:
        """Akış sırasında metni dinamik günceller."""
        self.raw_content = new_text
        if isinstance(self.text_label, MarkdownMessage):
            self.text_label.set_text(new_text, streaming=streaming)
        else:
            self.text_label.configure(text=new_text)

    def _copy_to_clipboard(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.raw_content)


class ChatView(ctk.CTkFrame):
    """Sohbet Arayüzü Ana Görünümü."""

    def __init__(self, master: Any, service: EvrenService, session_id: Optional[str] = None,
                 navigate: Optional[Callable[[Optional[str]], None]] = None,
                 on_history_change: Optional[Callable[[], None]] = None, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self._navigate = navigate
        self._on_history_change = on_history_change
        self.session: ChatSession = self.service.get_or_create_session()
        self.messages: List[Dict[str, Any]] = []
        self.attached_image_path: Optional[str] = None
        self.current_bot_bubble: Optional[ChatMessageBubble] = None
        self._stream_token: Optional[object] = None
        self._current_activity: Optional[ToolActivityGroup] = None
        self._scroll_job: Optional[str] = None
        self._scroll_force = False
        self._last_follow_top: Optional[float] = None
        self._current_bot_record: Optional[Dict[str, Any]] = None
        self._history_ids: Dict[str, str] = {}
        self.is_streaming = False
        self.activity_stage = "Hazır"

        self._build_ui()
        self._load_models()
        self.service.add_mcp_listener(self._update_mcp_chips)
        self._update_mcp_chips()
        self._refresh_history()
        history = self.service.list_chat_history()
        if session_id:
            self._restore_chat(session_id)
        elif history and navigate is None:
            self._restore_chat(history[0]["session_id"])

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)  # Mesaj alanı esner; diğer satırlar sabittir

        # 1. ÜST PANEL: Model Seçimi, MCP Seçimi, Parametreler ve Aksiyon Butonları
        top_bar = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        top_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        model_bar = ctk.CTkFrame(top_bar, fg_color="transparent")
        model_bar.pack(fill="x")
        actions_bar = ctk.CTkFrame(top_bar, fg_color="transparent")
        actions_bar.pack(fill="x")

        # Model Seçici
        model_lbl = ctk.CTkLabel(model_bar, text="Model:", font=ctk.CTkFont(size=12, weight="bold"))
        model_lbl.pack(side="left", padx=(12, 4), pady=10)

        self.model_combo = ctk.CTkComboBox(
            model_bar,
            values=[self.service.default_model],
            width=140,
            command=self._on_model_selected,
        )
        self.model_combo.set(self.service.default_model)
        self.model_combo.pack(side="left", padx=4, pady=10)

        self.effort_selector = EffortSelector(model_bar, self.service, self._on_effort_selected)
        self.effort_selector.pack(side="left", padx=6, pady=4)
        self._refresh_effort()

        # MCP Seçici Butonu (Current Chat MCP Indicator)
        self.mcp_btn = ctk.CTkButton(
            model_bar,
            text="🔌 MCP (0)",
            width=80,
            height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent",
            border_width=1,
            text_color="#38bdf8",
            border_color="#38bdf8",
            command=self._open_mcp_selector,
        )
        self.mcp_btn.pack(side="left", padx=6, pady=10)

        # Ayarları Göster / Gizle Butonu
        self.toggle_params_btn = ctk.CTkButton(
            model_bar,
            text="Parametreler ▼",
            width=100,
            height=28,
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray80"),
            command=self._toggle_params,
        )
        self.toggle_params_btn.pack(side="left", padx=4, pady=10)

        # Sağ Taraf Butonları: Yeni Sohbet & Dışa Aktar & Temizle
        clear_btn = ctk.CTkButton(
            actions_bar,
            text="Sohbeti sil",
            width=70,
            height=28,
            fg_color="transparent",
            text_color=("gray30", "#94a3b8"),
            hover_color=("gray80", "#334155"),
            command=self._delete_chat,
        )
        clear_btn.pack(side="right", padx=(4, 12), pady=10)

        export_btn = ctk.CTkButton(
            actions_bar,
            text="Dışa Aktar",
            width=85,
            height=28,
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray80"),
            command=self._export_chat,
        )
        export_btn.pack(side="right", padx=4, pady=10)

        new_chat_btn = ctk.CTkButton(
            actions_bar,
            text="+ Yeni Sohbet",
            width=100,
            height=28,
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self.new_chat,
        )
        new_chat_btn.pack(side="right", padx=4, pady=10)
        if self._navigate:
            new_chat_btn.pack_forget()

        history_bar = ctk.CTkFrame(self, fg_color="transparent")
        history_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 4))
        history_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(history_bar, text="Sohbet geçmişi", font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=(0, 10))
        self.history_combo = ctk.CTkComboBox(history_bar, values=["Yeni sohbet"], height=28,
                                            state="readonly", command=self._select_history)
        self.history_combo.grid(row=0, column=1, sticky="ew")
        self.history_combo.set("Yeni sohbet")
        self.history_status = ctk.CTkLabel(history_bar, text="", font=ctk.CTkFont(size=11),
                                          text_color=("gray40", "#94a3b8"))
        self.history_status.grid(row=0, column=2, padx=(10, 0))
        if self._navigate:
            self.history_combo.grid_remove()
            for child in history_bar.winfo_children():
                if child is not self.history_status:
                    child.grid_remove()

        # 2. KATLANABİLİR PARAMETRE PANELİ
        self.params_frame = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=8)
        self.params_visible = False

        # Sıcaklık (Temperature)
        t_frame = ctk.CTkFrame(self.params_frame, fg_color="transparent")
        t_frame.pack(side="left", padx=12, pady=8)
        self.temp_label = ctk.CTkLabel(t_frame, text="Sıcaklık: 0.7", font=ctk.CTkFont(size=11))
        self.temp_label.pack(anchor="w")
        self.temp_slider = ctk.CTkSlider(
            t_frame,
            from_=0.0,
            to=1.5,
            number_of_steps=15,
            width=130,
            command=self._on_temp_change,
        )
        self.temp_slider.set(0.7)
        self.temp_slider.pack(anchor="w", pady=(2, 0))

        # Maksimum Belirteç (Max Tokens)
        tok_frame = ctk.CTkFrame(self.params_frame, fg_color="transparent")
        tok_frame.pack(side="left", padx=12, pady=8)
        self.tokens_label = ctk.CTkLabel(tok_frame, text="Maks. Belirteç: 2048", font=ctk.CTkFont(size=11))
        self.tokens_label.pack(anchor="w")
        self.tokens_slider = ctk.CTkSlider(
            tok_frame,
            from_=256,
            to=8192,
            number_of_steps=31,
            width=140,
            command=self._on_tokens_change,
        )
        self.tokens_slider.set(2048)
        self.tokens_slider.pack(anchor="w", pady=(2, 0))

        # Canlı Araçlar (Evren Tools)
        self.tools_var = tk.BooleanVar(value=False)
        self.tools_check = ctk.CTkCheckBox(
            self.params_frame,
            text="Canlı Web/Veri Araçları",
            variable=self.tools_var,
            font=ctk.CTkFont(size=12),
        )
        self.tools_check.pack(side="left", padx=14, pady=8)

        # Sistem Yönergesi (System Prompt) Girişi
        sys_frame = ctk.CTkFrame(self.params_frame, fg_color="transparent")
        sys_frame.pack(side="left", fill="x", expand=True, padx=12, pady=8)
        sys_lbl = ctk.CTkLabel(sys_frame, text="Sistem Talimatı:", font=ctk.CTkFont(size=11))
        sys_lbl.pack(anchor="w")
        self.sys_entry = ctk.CTkEntry(
            sys_frame,
            placeholder_text="Asistanın kimliği ve davranış yönergeleri...",
            height=28,
            font=ctk.CTkFont(size=11),
        )
        self.sys_entry.pack(fill="x", pady=(2, 0))

        # 3. SOHBET AKIŞ ALANI (Scrollable)
        self.chat_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
        )
        self.chat_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=4)
        self.chat_scroll.grid_columnconfigure(0, weight=1)

        # Hoş Geldiniz Mesaj Kartı
        self._add_welcome_card()

        # 3.5. AKTİF MCP ÇİPLERİ (CHIPS) ÇUBUĞU
        self.chips_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.chips_frame.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 2))

        # 4. EKLİ GÖRSEL ÇUBUĞU (Varsa gösterilir)
        self.img_tray = ctk.CTkFrame(self, fg_color=("gray85", "#243044"), corner_radius=8, height=36)
        self.img_tray_lbl = ctk.CTkLabel(
            self.img_tray,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#38bdf8",
        )
        self.img_tray_lbl.pack(side="left", padx=10)
        self.img_remove_btn = ctk.CTkButton(
            self.img_tray,
            text="✕ Kaldır",
            width=65,
            height=22,
            fg_color="#ef4444",
            hover_color="#dc2626",
            font=ctk.CTkFont(size=11),
            command=self._remove_attached_image,
        )
        self.img_remove_btn.pack(side="right", padx=10)

        # 5. ALT GİRDİ PANELİ
        bottom_box = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=12)
        bottom_box.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 12))
        bottom_box.grid_columnconfigure(1, weight=1)
        self.live_activity = LiveActivity(bottom_box)
        self.live_activity.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 0))
        self.live_activity.grid_remove()

        # Görsel Ekle Butonu
        self.add_img_btn = ctk.CTkButton(
            bottom_box,
            text="📷 Görsel",
            width=80,
            height=40,
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray85"),
            command=self._select_image,
        )
        self.add_img_btn.grid(row=1, column=0, padx=(10, 6), pady=10)

        # Çok Satırlı Metin Girdisi
        self.input_textbox = ctk.CTkTextbox(
            bottom_box,
            height=70,
            font=ctk.CTkFont(size=13),
            fg_color=("white", "#0f172a"),
            border_width=1,
            corner_radius=8,
        )
        self.input_textbox.grid(row=1, column=1, sticky="ew", padx=6, pady=10)
        self.input_textbox.bind("<Return>", self._handle_return_key)
        self.input_textbox.bind("<KP_Enter>", self._handle_return_key)

        # Gönder / Durdur Butonu
        self.send_btn = ctk.CTkButton(
            bottom_box,
            text="Gönder",
            width=90,
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._on_send_pressed,
        )
        self.send_btn.grid(row=1, column=2, padx=(6, 10), pady=10)

        # Kısayol İpucu
        hint_lbl = ctk.CTkLabel(
            self,
            text="Göndermek için Enter veya Ctrl+Enter tuşlarına basın. Yeni satır için Shift+Enter kullanın.",
            font=ctk.CTkFont(size=10),
            text_color=("gray50", "gray60"),
        )
        hint_lbl.grid(row=7, column=0, sticky="w", padx=22, pady=(0, 6))

    def _open_mcp_selector(self) -> None:
        MCPChatSelectorDialog(self, service=self.service, session=self.session, on_changed=self._update_mcp_chips)

    def _remove_mcp_from_session(self, server_name: str) -> None:
        self.service.set_session_mcp(self.session.session_id, server_name, False)
        self._update_mcp_chips()

    def _update_mcp_chips(self) -> None:
        active_mcps = self.service.get_session_mcps(self.session.session_id)
        self.mcp_btn.configure(text=f"🔌 MCP ({len(active_mcps)})")

        for w in self.chips_frame.winfo_children():
            w.destroy()

        if not active_mcps:
            self.chips_frame.grid_remove()
            return

        self.chips_frame.grid()
        lbl = ctk.CTkLabel(self.chips_frame, text="Aktif MCP:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#38bdf8")
        lbl.pack(side="left", padx=(4, 6))

        for s_name in sorted(active_mcps):
            chip = ctk.CTkButton(
                self.chips_frame,
                text=f"{s_name}  ✕",
                height=22,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color=("gray85", "#1e293b"),
                hover_color=("#fee2e2", "#450a0a"),
                text_color=("gray20", "#e2e8f0"),
                border_width=1,
                border_color=("gray70", "#38bdf8"),
                corner_radius=11,
                command=lambda sn=s_name: self._remove_mcp_from_session(sn),
            )
            chip.pack(side="left", padx=3)

    def _add_welcome_card(self) -> None:
        """İlk açılış bilgilendirme kartı."""
        welcome = ctk.CTkFrame(
            self.chat_scroll,
            fg_color=("gray95", "#161f2e"),
            corner_radius=12,
            border_width=1,
            border_color=("gray85", "#334155"),
        )
        welcome.pack(fill="x", padx=16, pady=24)
        self._welcome_card = welcome

        t_lbl = ctk.CTkLabel(
            welcome,
            text="evren Sohbet Ortamına Hoş Geldiniz",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=16, pady=(14, 4))

        d_lbl = ctk.CTkLabel(
            welcome,
            text=(
                "EVREN LLM API üzerinden gelişmiş yapay zekâ modelleriyle hızlıca sohbet edin.\n"
                "• Metin soruları sorabilir, kod yazdırabilir ve belgeleri özetleyebilirsiniz.\n"
                "• '📷 Görsel' butonuyla fotoğraf veya ekran görüntüsü ekleyip görsel hakkında analiz isteyebilirsiniz.\n"
                "• 'Canlı Web/Veri Araçları' seçeneği ile güncel bilgilere erişebilirsiniz."
            ),
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
            justify="left",
        )
        d_lbl.pack(anchor="w", padx=16, pady=(0, 14))

    def _toggle_params(self) -> None:
        if self.params_visible:
            self.params_frame.grid_forget()
            self.toggle_params_btn.configure(text="Parametreler ▼")
            self.params_visible = False
        else:
            self.params_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
            self.toggle_params_btn.configure(text="Parametreler ▲")
            self.params_visible = True

    def _on_temp_change(self, value: float) -> None:
        self.temp_label.configure(text=f"Sıcaklık: {value:.1f}")

    def _on_tokens_change(self, value: float) -> None:
        self.tokens_label.configure(text=f"Maks. Belirteç: {int(value)}")

    def _load_models(self) -> None:
        """API'den modelleri listeler."""
        def on_models(models: List[str]):
            if models:
                selected = self.model_combo.get() or self.session.model
                self.model_combo.configure(values=models if selected in models else [selected, *models])
                self.model_combo.set(selected)
                self._refresh_effort()

        self.service.fetch_models_async(on_success=on_models)

    def _refresh_effort(self) -> None:
        self.session.reasoning_effort = self.effort_selector.refresh(
            self.session.model, self.session.reasoning_effort, self.is_streaming)

    def _on_effort_selected(self, effort) -> None:
        if self.is_streaming:
            return
        self.session.reasoning_effort = effort
        self._save_history()

    def _on_model_selected(self, model: str) -> bool:
        try:
            self.service.set_default_model(model)
        except Exception as error:
            self.model_combo.set(self.session.model)
            self._refresh_effort()
            messagebox.showerror("Model kaydedilemedi", turkce_hata_mesaji(error))
            return False
        self.session.model = model.strip()
        self.model_combo.set(self.session.model)
        self._refresh_effort()
        self._save_history()
        return True

    def _refresh_history(self) -> None:
        if self._on_history_change:
            self._on_history_change()
            return
        self._history_ids.clear()
        selected = "Yeni sohbet"
        for record in self.service.list_chat_history():
            date = datetime.datetime.fromtimestamp(record["updated_at"]).strftime("%d.%m.%Y %H:%M")
            base = f"{record['title'][:48]} · {date}"
            label = base
            index = 2
            while label in self._history_ids:
                label = f"{base} ({index})"
                index += 1
            self._history_ids[label] = record["session_id"]
            if record["session_id"] == self.session.session_id:
                selected = label
        self.history_combo.configure(values=list(self._history_ids) or ["Yeni sohbet"])
        self.history_combo.set(selected)
        if not self.messages:
            self.history_status.configure(text="")

    def _select_history(self, label: str) -> None:
        session_id = self._history_ids.get(label)
        if self._navigate and session_id:
            self._navigate(session_id)
            return
        if session_id and session_id != self.session.session_id and not self.is_streaming:
            self._restore_chat(session_id)

    def _restore_chat(self, session_id: str) -> None:
        if not self._save_history():
            return
        records = self.service.load_chat_transcript(session_id)
        if not records:
            return
        session = self.service.restore_chat_session(session_id)
        self.clear_chat(reset_session=False)
        self.session = session
        self._welcome_card.destroy()
        self._welcome_card = None
        self.messages = records
        for record in records:
            content = record.get("content", "")
            image_data_url = None
            if isinstance(content, list):
                image_data_url = next((part.get("image_url", {}).get("url") for part in content
                                       if part.get("type") == "image_url"), None)
                content = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            if record.get("pending"):
                content = (content + "\n\nYanıt kesildi. Bu sohbetten devam edebilirsiniz.").strip()
                record.update(content=content, pending=False)
            bubble = self._add_message(record["role"], content, record.get("image_path"), record.get("timestamp"), image_data_url)
            if record.get("activity"):
                activity = ToolActivityGroup.from_record(bubble, record["activity"])
                activity.on_collapse = lambda bubble=bubble: self._reveal_reply(bubble)
                activity.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
                record["activity"] = activity.to_record()
        self.model_combo.set(session.model)
        self._refresh_effort()
        self.temp_slider.set(session.temperature)
        self._on_temp_change(session.temperature)
        self.tokens_slider.set(session.max_tokens)
        self._on_tokens_change(session.max_tokens)
        self.sys_entry.delete(0, "end")
        self.sys_entry.insert(0, session.system_prompt)
        self._update_mcp_chips()
        self._refresh_history()
        self.history_status.configure(text="")
        self._scroll_to_bottom(force=True)

    def _save_history(self) -> bool:
        if not self.messages:
            return True
        if self._current_bot_record is not None and self._current_activity is not None:
            self._current_bot_record["activity"] = self._current_activity.to_record()
        self.session.model = self.model_combo.get()
        self.session.temperature = float(self.temp_slider.get())
        self.session.max_tokens = int(self.tokens_slider.get())
        self.session.system_prompt = self.sys_entry.get().strip()
        try:
            self.service.save_chat_transcript(self.session, self.messages)
            self._refresh_history()
        except Exception:
            self.history_status.configure(text="Kaydedilemedi", text_color=("#b91c1c", "#f87171"))
            return False
        self.history_status.configure(text="")
        return True

    def _delete_chat(self) -> None:
        if not self.messages:
            return
        if not messagebox.askyesno("Sohbeti sil", "Bu sohbet ve işlem ayrıntıları kalıcı olarak silinsin mi?"):
            return
        if self.is_streaming:
            self._on_send_pressed()
        try:
            self.service.delete_chat_history(self.session.session_id)
        except Exception:
            self.history_status.configure(text="Silinemedi", text_color=("#b91c1c", "#f87171"))
            return
        self.session = self.service.get_or_create_session(None)
        self.model_combo.set(self.session.model)
        self._refresh_effort()
        self.clear_chat()
        self._update_mcp_chips()
        self._refresh_history()
        if self._navigate:
            self._navigate(self.session.session_id)

    def _select_image(self) -> None:
        """Multimodal sohbet için görsel dosyası seçer."""
        dosya = filedialog.askopenfilename(
            title="Görsel Seç",
            filetypes=[
                ("Görsel Dosyaları", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                ("Tüm Dosyalar", "*.*"),
            ],
        )
        if dosya:
            self.attached_image_path = dosya
            boyut_kb = os.path.getsize(dosya) / 1024
            self.img_tray_lbl.configure(text=f"📷 Eklenen Görsel: {Path(dosya).name} ({boyut_kb:.1f} KB)")
            self.img_tray.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 4))

    def _remove_attached_image(self) -> None:
        self.attached_image_path = None
        self.img_tray.grid_forget()

    def _handle_return_key(self, event: Any) -> str:
        # Shift+Enter yeni satır bırakır
        if event.state & 0x0001:  # Shift basılı
            return ""
        # Düz Enter veya Ctrl+Enter gönderir
        self._on_send_pressed()
        return "break"

    def _add_message(self, role: str, content: str, image_path: Optional[str] = None,
                     timestamp: Optional[str] = None, image_data_url: Optional[str] = None) -> ChatMessageBubble:
        """Her mesaj için tam genişlikte satır, yönüne göre sınırlı genişlikte balon."""
        row = ctk.CTkFrame(self.chat_scroll, fg_color="transparent", height=1)
        row.pack(fill="x", padx=12, pady=6)
        bubble = ChatMessageBubble(row, role=role, content=content, image_path=image_path,
                                    timestamp=timestamp, image_data_url=image_data_url)
        is_user = role == "user"
        bubble.pack(fill="x", anchor="e" if is_user else "w")
        last_padding = None

        def resize(event: Any) -> None:
            nonlocal last_padding
            available = int(event.width / row._get_widget_scaling())
            width = min(560 if is_user else 800, int(available * (0.76 if is_user else 0.88)))
            gutter = max(0, available - width)
            padding = (gutter, 0) if is_user else (0, gutter)
            if padding != last_padding:
                last_padding = padding
                bubble.pack_configure(padx=padding)

        row.bind("<Configure>", resize, add="+")
        if isinstance(bubble.text_label, MarkdownMessage):
            bubble.text_label.on_render = self._scroll_to_bottom
            bubble.text_label.get_scroll_anchor = self._capture_scroll_anchor
            bubble.text_label.on_layout = lambda top: self._scroll_to_bottom(layout_top=top)
        return bubble

    def _on_send_pressed(self) -> None:
        if self.is_streaming:
            # Durdur
            self.service.cancel_active_stream(self.session.session_id)
            if self.current_bot_bubble:
                content = self.current_bot_bubble.raw_content
                if content == "Yanıt hazırlanıyor...":
                    content = "Yanıt durduruldu."
                else:
                    content = f"{content}\n\nYanıt durduruldu."
                self.current_bot_bubble.update_text(content)
                self._current_bot_record.update(content=content, pending=False)
            if self._current_activity:
                self._current_activity.finish(cancelled=True)
            self._finish_streaming()
            self._save_history()
            return

        text = self.input_textbox.get("1.0", "end-1c").strip()
        if not text and not self.attached_image_path:
            return

        model = self.model_combo.get().strip()
        if not model:
            messagebox.showwarning("Model Gerekli", "Lütfen bir model seçiniz.")
            return
        # Editable combo entries do not invoke the selection callback.
        if model != self.session.model and not self._on_model_selected(model):
            return

        image_to_send = self.attached_image_path

        # Multimodal içerik hazırlığı
        if image_to_send:
            try:
                from evren_agent.api.client import media_data_url
                data_url = media_data_url(image_to_send)
                user_content = [
                    {"type": "text", "text": text or "Bu görseli inceleyip açıkla."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            except Exception as e:
                messagebox.showerror("Görsel Hatası", f"Görsel hazırlanamadı: {e}")
                return
        else:
            user_content = text

        if self._welcome_card is not None:
            self._welcome_card.destroy()
            self._welcome_card = None
        user_bubble = self._add_message("user", text, image_to_send)
        self._remove_attached_image()
        self.input_textbox.delete("1.0", "end")
        self.messages.append({"role": "user", "content": user_content,
                              "timestamp": user_bubble.timestamp, "image_path": image_to_send})

        # Asistan balonunu hazırla
        bot_bubble = self._add_message("assistant", "Yanıt hazırlanıyor...")
        self.current_bot_bubble = bot_bubble
        bot_record = {"role": "assistant", "content": "", "timestamp": bot_bubble.timestamp, "pending": True}
        self.messages.append(bot_record)
        self._current_bot_record = bot_record
        activity: Optional[ToolActivityGroup] = None
        self._current_activity = None
        stream_token = object()
        self._stream_token = stream_token
        self._scroll_to_bottom(force=True)

        # Akışı Başlat
        self.is_streaming = True
        self._refresh_effort()
        self._show_activity_stage("Hazırlanıyor", "Model ve araç bağlantıları hazırlanıyor…")
        self.send_btn.configure(text="Durdur", fg_color="#ef4444", hover_color="#dc2626")

        # Oturum parametrelerini güncelle
        self.session.model = model
        self.session.temperature = float(self.temp_slider.get())
        self.session.max_tokens = int(self.tokens_slider.get())
        self.session.system_prompt = self.sys_entry.get().strip()
        self._save_history()

        accumulated_chunks: List[str] = []
        text_before_tools = False

        def get_activity() -> ToolActivityGroup:
            nonlocal activity
            if activity is None:
                activity = ToolActivityGroup(bot_bubble, on_collapse=lambda: self._reveal_reply(bot_bubble))
                activity.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
                self._current_activity = activity
            return activity

        def complete(full_text: str = "", error: Optional[str] = None) -> None:
            if self._stream_token is not stream_token:
                return
            full_text = full_text.strip()
            text_content = "".join(accumulated_chunks).strip()
            if error:
                text_content = f"{text_content}\n\n⚠ Hata: {error}".strip()
            elif full_text and not text_content.endswith(full_text):
                text_content = f"{text_content}\n\n{full_text}".strip()
            bot_bubble.update_text(text_content or "Yanıt metni oluşturulmadı. İşlem ayrıntılarını inceleyebilirsiniz.")
            bot_record.update(content=bot_bubble.raw_content, pending=False)
            if activity:
                activity.finish(failed=error is not None)
            self._finish_streaming()
            self._save_history()

        def on_event(evt: AgentEvent):
            nonlocal text_before_tools
            if self._stream_token is not stream_token:
                return
            if evt.type == AgentEventType.MODEL_REQUEST_STARTED:
                self._show_activity_stage("Model yanıtı bekleniyor", "Modelin sonraki adımı bekleniyor…")
            elif evt.type == AgentEventType.MCP_CONNECTING:
                self._show_activity_stage("MCP bağlanıyor", f"{evt.server_name} bağlantısı kuruluyor…")
            elif evt.type == AgentEventType.MCP_CONNECTED:
                self._show_activity_stage("Hazırlanıyor", f"{evt.server_name} bağlandı; model yanıtı bekleniyor…")
            if evt.type == AgentEventType.TEXT_DELTA:
                if evt.content:
                    self._show_activity_stage("Yanıt yazılıyor", "Modelden yanıt metni alınıyor…")
                    if text_before_tools and accumulated_chunks:
                        accumulated_chunks.append("\n\n")
                    text_before_tools = False
                    accumulated_chunks.append(evt.content)
                    bot_bubble.update_text("".join(accumulated_chunks), streaming=True)
                    bot_record["content"] = bot_bubble.raw_content
                    self._scroll_to_bottom()

            elif evt.type == AgentEventType.REASONING_DELTA:
                if evt.content:
                    get_activity().append_reasoning(evt.content)
                    self._show_activity_stage("Model düşünüyor", "Model sonraki adımı değerlendiriyor…")
                    self._scroll_to_bottom()

            elif evt.type == AgentEventType.TOOL_CALL_STARTED:
                get_activity().start_tool(evt)
                self._show_active_tools()
                text_before_tools = bool(accumulated_chunks)
                self._scroll_to_bottom()

            elif evt.type in (AgentEventType.TOOL_CALL_RESULT, AgentEventType.TOOL_CALL_ERROR, AgentEventType.TOOL_CALL_ARGUMENTS):
                get_activity().update_tool(evt)
                self._show_active_tools()
                self._scroll_to_bottom()

            elif evt.type == AgentEventType.MCP_ERROR:
                get_activity().add_connection_error(evt)
                self._show_activity_stage("Hazırlanıyor", "MCP bağlantısı kurulamadı; diğer adımlar bekleniyor…")
                self._scroll_to_bottom()

            elif evt.type == AgentEventType.DONE:
                complete(evt.content or "")

            elif evt.type == AgentEventType.ERROR:
                complete(error=evt.content or "Bilinmeyen hata")

        def on_done(full_text: str):
            complete(full_text)

        def on_error(err_msg: str):
            complete(error=err_msg)

        self.service.chat_agent_stream_async(
            session_id=self.session.session_id,
            prompt=text,
            image_path=image_to_send,
            on_event=lambda evt: self.after(0, lambda: on_event(evt)),
            on_done=lambda t: self.after(0, lambda: on_done(t)),
            on_error=lambda e: self.after(0, lambda: on_error(e)),
        )

    def destroy(self) -> None:
        self._save_history()
        self._stream_token = None
        self.live_activity.stop()
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
            self._scroll_job = None
        super().destroy()

    def _finish_streaming(self) -> None:
        following = self._capture_scroll_anchor() is not None
        self.is_streaming = False
        if hasattr(self, "effort_selector"):
            self._refresh_effort()
        self.activity_stage = "Hazır"
        self.live_activity.stop()
        self.live_activity.grid_remove()
        self._stream_token = None
        self.send_btn.configure(text="Gönder", fg_color="#3b82f6", hover_color="#2563eb")
        self.history_combo.configure(state="readonly")
        # Removing the live banner grows the viewport and changes its offset.
        # Preserve bottom-following across that layout change, but leave a
        # reader of older messages at their chosen position.
        self._scroll_to_bottom(force=following)

    def _show_activity_stage(self, stage: str, detail: str) -> None:
        changed = self.activity_stage != stage
        self.activity_stage = stage
        self.live_activity.grid()
        self.live_activity.show_stage(stage, detail)
        if self._current_activity:
            self._current_activity.phase = stage
            self._current_activity._refresh_summary()
        if changed:
            self._refresh_history()

    def _show_active_tools(self) -> None:
        active = [tool for tool in self._current_activity.tools if tool.status == "running"]
        if active:
            tool = active[-1]
            stage = "Komut çalışıyor" if len(active) == 1 else f"{len(active)} komut çalışıyor"
            self._show_activity_stage(stage, f"{tool.server_name} · {tool.tool_name}\n"
                                      + command_preview(tool.tool_name, tool.arguments))
        else:
            self._show_activity_stage("Model yanıtı bekleniyor", "Komutlar tamamlandı; modelin sonraki adımı bekleniyor…")

    def _reveal_reply(self, bubble: ChatMessageBubble) -> None:
        """Keep the clicked turn's answer visible when its tall drawer closes."""
        if not bubble.winfo_exists():
            return
        self.update_idletasks()
        canvas = self.chat_scroll._parent_canvas
        bounds = canvas.bbox("all")
        if not bounds:
            return
        canvas.configure(scrollregion=bounds)
        # A canvas can retain its former pixel offset after content shrinks.
        # Scroll to this turn, including restored/older turns, rather than to
        # the conversation's newest message.
        top = bubble.winfo_rooty() - self.chat_scroll.winfo_rooty()
        bottom = top + bubble.winfo_height()
        viewport = canvas.winfo_height()
        visible_top = canvas.canvasy(0)
        target = visible_top
        if top < visible_top:
            target = top
        elif bottom > visible_top + viewport:
            target = max(top, bottom - viewport)
        canvas.yview_moveto(max(0, target - bounds[1]) / max(1, bounds[3] - bounds[1]))

    def _capture_scroll_anchor(self) -> Optional[float]:
        canvas = self.chat_scroll._parent_canvas
        # A reply can arrive before the new prompt's forced scroll timer runs.
        # Carry that explicit request into Markdown's later native layout.
        top = canvas.canvasy(0)
        stayed_at_follow_position = self._last_follow_top is not None and abs(top - self._last_follow_top) <= 2
        return top if self._scroll_force or canvas.yview()[1] >= 0.97 or stayed_at_follow_position else None

    def _scroll_to_bottom(self, force: bool = False, *, layout_top: Optional[float] = None) -> None:
        canvas = self.chat_scroll._parent_canvas
        self._scroll_force = self._scroll_force or force
        if self._scroll_job is not None:
            return
        # Geçmişi okuyan kullanıcıyı yeni olay geldiğinde aşağı çekme.
        if not force and layout_top is None and canvas.yview()[1] < 0.97:
            return
        # Markdown blocks finish measuring after rendering. Retain the reading
        # position captured before that layout grew, including native resize
        # events that arrive after the initial scroll timer has already fired.
        previous_top = canvas.canvasy(0) if layout_top is None else layout_top

        def scroll() -> None:
            self._scroll_job = None
            # Flush pending text/grid geometry before measuring the canvas;
            # otherwise a slow native window can scroll the old short reply.
            self.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            if self._scroll_force or canvas.canvasy(0) >= previous_top - 2:
                canvas.yview_moveto(1.0)
                self._last_follow_top = canvas.canvasy(0)
            self._scroll_force = False

        self._scroll_job = self.after(50, scroll)

    def new_chat(self) -> None:
        """Yeni bir sohbet oturumu başlatır."""
        if self._navigate:
            self._navigate(None)
            return
        if self.is_streaming:
            self._on_send_pressed()
        if not self._save_history():
            return
        self.session = self.service.get_or_create_session(None)
        self.model_combo.set(self.session.model)
        self._refresh_effort()
        self.clear_chat()
        self._update_mcp_chips()
        self._refresh_history()

    def clear_chat(self, *, reset_session: bool = True) -> None:
        """Tüm sohbet geçmişini temizler."""
        if self.is_streaming:
            self.service.cancel_active_stream(self.session.session_id)
            self._finish_streaming()
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
            self._scroll_job = None
        self._scroll_force = False
        self._last_follow_top = None
        self.current_bot_bubble = None
        self._current_activity = None
        self._current_bot_record = None
        self.messages.clear()
        if reset_session:
            self.session.clear()
        self._remove_attached_image()
        for widget in self.chat_scroll.winfo_children():
            widget.destroy()
        self._add_welcome_card()

    def _export_chat(self) -> None:
        """Sohbeti Markdown veya JSON olarak dışa aktarır."""
        if not self.messages:
            messagebox.showinfo("Boş Sohbet", "Dışa aktarılacak mesaj bulunmuyor.")
            return

        dosya = filedialog.asksaveasfilename(
            title="Sohbeti Kaydet",
            defaultextension=".md",
            filetypes=[
                ("Markdown Dosyası", "*.md"),
                ("JSON Dosyası", "*.json"),
                ("Düz Metin", "*.txt"),
            ],
        )
        if not dosya:
            return

        try:
            if dosya.endswith(".json"):
                with open(dosya, "w", encoding="utf-8") as f:
                    json.dump(self.messages, f, ensure_ascii=False, indent=2)
            else:
                lines = [f"# evren Sohbet Dökümü - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"]
                for msg in self.messages:
                    sender = "Siz" if msg["role"] == "user" else "evren"
                    content = msg["content"]
                    if isinstance(content, list):
                        # Multimodal parçalar
                        text_part = next((item["text"] for item in content if item.get("type") == "text"), "")
                        content = f"[Görsel Ekli]\n{text_part}"
                    lines.append(f"### {sender}\n{content}\n\n---\n")

                with open(dosya, "w", encoding="utf-8") as f:
                    f.writelines(lines)

            messagebox.showinfo("Başarılı", f"Sohbet başarıyla kaydedildi:\n{dosya}")
        except Exception as e:
            messagebox.showerror("Kayıt Hatası", f"Dosya kaydedilemedi: {e}")
