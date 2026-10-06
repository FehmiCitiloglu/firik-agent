"""firik masaüstü uygulaması - MCP Yöneticisi Görünümü (MCP View).

Model Context Protocol (MCP) sunucularını yönetme, test etme, loglarını ve
araçlarını inceleme, oturum bazlı bağlama ve yapılandırma arayüzü.
Tüm etiket ve açıklamalar Türkçedir.
"""
from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, List, Optional
import customtkinter as ctk

from firik_agent.mcp.models import (
    MCPServerConfig,
    MCPStatus,
    MCPTestResult,
    MCPTransport,
    ToolPolicy,
    ToolPolicyMode,
    redact_secrets,
    store_mcp_secret,
)
from firik_agent.ui.service import FirikService
from firik_agent.ui.theme import THEME_COLORS


class MCPTestResultDialog(ctk.CTkToplevel):
    """Bağlantı test sonucunu ayrıntılı gösteren modal pencere."""

    def __init__(self, master: Any, server_name: str, result: MCPTestResult) -> None:
        super().__init__(master)
        self.title(f"MCP Test Sonucu: {server_name}")
        self.geometry("560x480")
        self.minsize(480, 360)
        self.grab_set()

        status_text = "✓ BAŞARILI" if result.success else "✕ BAŞARISIZ"
        status_color = "#10b981" if result.success else "#ef4444"

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header,
            text=f"Test Sonucu: {server_name}",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text=status_text,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=status_color,
        ).pack(side="right")

        content = ctk.CTkScrollableFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=8)
        content.pack(fill="both", expand=True, padx=16, pady=8)

        # Breakdown items
        items = [
            ("Süreç Başlatıldı mı?", "Evet" if result.process_started else "Hayır"),
            ("Tokalaşma (Initialize)?", "Başarılı" if result.initialize_succeeded else "Başarısız"),
            ("Protokol Sürümü:", result.protocol_version or "-"),
            ("Sunucu Adı:", result.server_name or "-"),
            ("Sunucu Sürümü:", result.server_version or "-"),
            ("Keşfedilen Araç Sayısı:", str(result.tool_count)),
            ("Test Süresi:", f"{result.duration_ms:.1f} ms"),
        ]

        for label, val in items:
            row = ctk.CTkFrame(content, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=3)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray30", "#94a3b8"), width=180, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=val, font=ctk.CTkFont(size=11), anchor="w").pack(side="left")

        if result.tool_names:
            t_box = ctk.CTkFrame(content, fg_color=("gray90", "#0f172a"), corner_radius=6)
            t_box.pack(fill="x", padx=8, pady=(8, 4))
            ctk.CTkLabel(t_box, text="Bulunan Araçlar:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=8, pady=(6, 2))
            tools_txt = "\n".join(f"• {t}" for t in result.tool_names)
            ctk.CTkLabel(t_box, text=tools_txt, font=ctk.CTkFont(family="monospace", size=10), justify="left", anchor="w").pack(anchor="w", padx=8, pady=(0, 6))

        if result.error:
            e_box = ctk.CTkFrame(content, fg_color=("#fee2e2", "#450a0a"), corner_radius=6)
            e_box.pack(fill="x", padx=8, pady=(8, 4))
            ctk.CTkLabel(e_box, text="Hata Detayı:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#ef4444").pack(anchor="w", padx=8, pady=(6, 2))
            ctk.CTkLabel(e_box, text=result.error, font=ctk.CTkFont(size=10), text_color="#fca5a5", justify="left", anchor="w", wraplength=460).pack(anchor="w", padx=8, pady=(0, 6))

        close_btn = ctk.CTkButton(self, text="Kapat", width=90, command=self.destroy)
        close_btn.pack(pady=12)


class MCPToolsDialog(ctk.CTkToplevel):
    """MCP sunucusunun araç şemalarını gösteren modal pencere."""

    def __init__(self, master: Any, server_name: str, tools: List[Dict[str, Any]]) -> None:
        super().__init__(master)
        self.title(f"MCP Araçları: {server_name} ({len(tools)} Araç)")
        self.geometry("640x520")
        self.minsize(500, 380)
        self.grab_set()

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header,
            text=f"Araç Gezgini: {server_name}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text=f"Toplam {len(tools)} araç",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "#94a3b8"),
        ).pack(side="right")

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=4)

        if not tools:
            empty_lbl = ctk.CTkLabel(
                scroll,
                text="Bu sunucu için henüz araç keşfedilmedi veya sunucu bağlı değil.",
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "#94a3b8"),
            )
            empty_lbl.pack(pady=40)
        else:
            for t in tools:
                card = ctk.CTkFrame(scroll, fg_color=("gray95", "#161f2e"), corner_radius=8, border_width=1, border_color=("gray85", "#243247"))
                card.pack(fill="x", pady=6)

                t_name = t.get("name", "")
                t_desc = t.get("description", "")
                schema = t.get("inputSchema", {})

                ctk.CTkLabel(card, text=f"🔧  {t_name}", font=ctk.CTkFont(size=13, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=12, pady=(10, 2))
                if t_desc:
                    ctk.CTkLabel(card, text=t_desc, font=ctk.CTkFont(size=11), text_color=("gray30", "#cbd5e1"), justify="left", anchor="w", wraplength=580).pack(anchor="w", padx=12, pady=(0, 6))

                # Parameter Schema View
                props = schema.get("properties", {})
                reqs = schema.get("required", [])
                if props:
                    param_lines = []
                    for p_name, p_data in props.items():
                        req_mark = " (Zorunlu)" if p_name in reqs else " (İsteğe Bağlı)"
                        p_type = p_data.get("type", "any")
                        p_desc = p_data.get("description", "")
                        param_lines.append(f"• {p_name} [{p_type}]{req_mark}: {p_desc}")
                    ctk.CTkLabel(
                        card,
                        text="\n".join(param_lines),
                        font=ctk.CTkFont(family="monospace", size=10),
                        text_color=("gray40", "#94a3b8"),
                        justify="left",
                        anchor="w",
                        wraplength=580,
                    ).pack(anchor="w", padx=16, pady=(0, 8))


class MCPLogsDialog(ctk.CTkToplevel):
    """MCP sunucusunun diagnostic loglarını gösteren modal pencere."""

    def __init__(self, master: Any, server_name: str, service: FirikService) -> None:
        super().__init__(master)
        self.title(f"MCP Tanılama Logları: {server_name}")
        self.geometry("680x480")
        self.minsize(520, 360)
        self.grab_set()

        ctk.CTkLabel(
            self,
            text=f"Log Kayıtları: {server_name}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#38bdf8",
        ).pack(anchor="w", padx=16, pady=(16, 8))

        textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(family="monospace", size=11), fg_color=("white", "#0b1320"))
        textbox.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        logs = service.get_mcp_logs(server_name)
        if not logs:
            textbox.insert("end", f"[{server_name}] Henüz log kaydı bulunmuyor.")
        else:
            for entry in logs:
                textbox.insert("end", f"[{entry.timestamp}] [{entry.level}] {entry.message}\n")
        textbox.configure(state="disabled")


class MCPEditDialog(ctk.CTkToplevel):
    """MCP Sunucusu Ekleme / Düzenleme Modalı."""

    def __init__(
        self,
        master: Any,
        service: FirikService,
        config: Optional[MCPServerConfig] = None,
        on_save: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self.service = service
        self.existing_config = config
        self.is_edit = config is not None
        self.on_save = on_save

        mode_title = "MCP Sunucusunu Düzenle" if self.is_edit else "Yeni MCP Sunucusu Ekle"
        self.title(mode_title)
        self.geometry("640x680")
        self.minsize(560, 560)
        self.grab_set()

        self._build_ui()
        if self.existing_config:
            self._populate_fields(self.existing_config)

    def _build_ui(self) -> None:
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=12)

        # 1. Başlık
        title_txt = "MCP Sunucusu Düzenleme" if self.is_edit else "Yeni MCP Sunucusu Ekle"
        ctk.CTkLabel(scroll, text=title_txt, font=ctk.CTkFont(size=17, weight="bold"), text_color="#38bdf8").pack(anchor="w", pady=(4, 12))

        # 2. Sunucu Adı
        ctk.CTkLabel(scroll, text="Sunucu Adı (Name):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.name_entry = ctk.CTkEntry(scroll, placeholder_text="ör. github, filesystem, postgresql")
        self.name_entry.pack(fill="x", pady=(0, 8))
        if self.is_edit:
            self.name_entry.configure(state="disabled")

        # 3. İletişim Protokolü (Transport)
        ctk.CTkLabel(scroll, text="İletişim Yöntemi (Transport):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.transport_var = tk.StringVar(value="stdio")
        self.transport_seg = ctk.CTkSegmentedButton(
            scroll,
            values=["stdio (Yerel Süreç)", "http/sse (Uzak Uç Nokta)"],
            command=self._on_transport_changed,
        )
        self.transport_seg.set("stdio (Yerel Süreç)")
        self.transport_seg.pack(fill="x", pady=(0, 10))

        # 4. Stdio Alanları
        self.stdio_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.stdio_frame.pack(fill="x")

        ctk.CTkLabel(self.stdio_frame, text="Komut (Command):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.cmd_entry = ctk.CTkEntry(self.stdio_frame, placeholder_text="ör. npx, python3, uvx")
        self.cmd_entry.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(self.stdio_frame, text="Argümanlar (Arguments - her satıra bir veya boşlukla ayrılmış):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.args_entry = ctk.CTkTextbox(self.stdio_frame, height=65, font=ctk.CTkFont(size=12))
        self.args_entry.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(self.stdio_frame, text="Çalışma Dizini (Working Directory - isteğe bağlı):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.cwd_entry = ctk.CTkEntry(self.stdio_frame, placeholder_text="/Users/example/projects")
        self.cwd_entry.pack(fill="x", pady=(0, 8))

        # 5. HTTP Alanları
        self.http_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        ctk.CTkLabel(self.http_frame, text="Uç Nokta Adresi (URL):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.url_entry = ctk.CTkEntry(self.http_frame, placeholder_text="http://localhost:8000/sse veya https://api.example.com/mcp")
        self.url_entry.pack(fill="x", pady=(0, 8))

        # 6. Ortam Değişkenleri & Keyring Kasası
        ctk.CTkLabel(scroll, text="Ortam Değişkenleri (Environment Variables):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(6, 2))
        ctk.CTkLabel(scroll, text="Düz metin: KEY=VAL | Güvenli Gizli Anahtar: GITHUB_TOKEN=ghp_... (Kasaya yazılır)", font=ctk.CTkFont(size=10), text_color=("gray40", "#94a3b8")).pack(anchor="w")
        self.env_entry = ctk.CTkTextbox(scroll, height=75, font=ctk.CTkFont(size=11))
        self.env_entry.pack(fill="x", pady=(2, 8))

        # 7. Seçenekler (Toggles)
        self.autostart_var = tk.BooleanVar(value=False)
        self.autostart_check = ctk.CTkCheckBox(scroll, text="Uygulama açılışında otomatik başlat (Autostart)", variable=self.autostart_var)
        self.autostart_check.pack(anchor="w", pady=4)

        self.default_chat_var = tk.BooleanVar(value=False)
        self.default_chat_check = ctk.CTkCheckBox(scroll, text="Yeni sohbetlerde varsayılan olarak etkinleştir (Default for new chats)", variable=self.default_chat_var)
        self.default_chat_check.pack(anchor="w", pady=4)

        self.confirm_tools_var = tk.BooleanVar(value=False)
        self.confirm_tools_check = ctk.CTkCheckBox(scroll, text="Araç çalıştırmadan önce kullanıcıdan onay iste (Ask before tool execution)", variable=self.confirm_tools_var)
        self.confirm_tools_check.pack(anchor="w", pady=4)

        # 8. Test Durumu Paneli
        self.test_status_box = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=11))
        self.test_status_box.pack(anchor="w", pady=(8, 2))

        # 9. Alt Butonlar
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=16, pady=12)

        self.test_btn = ctk.CTkButton(
            btn_bar,
            text="🔍 Bağlantıyı Test Et",
            width=150,
            fg_color="transparent",
            border_width=1,
            command=self._on_test_clicked,
        )
        self.test_btn.pack(side="left")

        cancel_btn = ctk.CTkButton(
            btn_bar,
            text="İptal",
            width=80,
            fg_color="transparent",
            hover_color=("gray80", "#334155"),
            command=self.destroy,
        )
        cancel_btn.pack(side="right", padx=(6, 0))

        self.save_btn = ctk.CTkButton(
            btn_bar,
            text="Kaydet",
            width=100,
            fg_color="#10b981",
            hover_color="#059669",
            command=self._on_save_clicked,
        )
        self.save_btn.pack(side="right")

    def _on_transport_changed(self, value: str) -> None:
        if "stdio" in value:
            self.http_frame.pack_forget()
            self.stdio_frame.pack(fill="x")
        else:
            self.stdio_frame.pack_forget()
            self.http_frame.pack(fill="x")

    def _populate_fields(self, config: MCPServerConfig) -> None:
        self.name_entry.insert(0, config.name)
        if config.transport == MCPTransport.STDIO:
            self.transport_seg.set("stdio (Yerel Süreç)")
            self._on_transport_changed("stdio")
            if config.command:
                self.cmd_entry.insert(0, config.command)
            if config.args:
                self.args_entry.insert("1.0", "\n".join(config.args))
            if config.cwd:
                self.cwd_entry.insert(0, config.cwd)
        else:
            self.transport_seg.set("http/sse (Uzak Uç Nokta)")
            self._on_transport_changed("http")
            if config.url:
                self.url_entry.insert(0, config.url)

        env_lines = []
        for k, v in config.env.items():
            env_lines.append(f"{k}={v}")
        for k in config.secret_env.keys():
            env_lines.append(f"{k}=••••••••")
        if env_lines:
            self.env_entry.insert("1.0", "\n".join(env_lines))

        self.autostart_var.set(config.autostart)
        self.default_chat_var.set(config.default_for_chat)
        self.confirm_tools_var.set(config.tool_policy.mode == ToolPolicyMode.CONFIRM)

    def _build_config_from_form(self) -> MCPServerConfig:
        name = self.name_entry.get().strip()
        if not name:
            raise ValueError("Sunucu adı boş bırakılamaz.")

        is_stdio = "stdio" in self.transport_seg.get()
        transport = MCPTransport.STDIO if is_stdio else MCPTransport.HTTP
        cmd: Optional[str] = None
        args: List[str] = []
        cwd: Optional[str] = None
        url: Optional[str] = None

        if is_stdio:
            cmd = self.cmd_entry.get().strip()
            if not cmd:
                raise ValueError("Stdio sunucusu için komut (command) belirtilmelidir.")
            raw_args = self.args_entry.get("1.0", "end").strip().splitlines()
            for a in raw_args:
                a_clean = a.strip()
                if a_clean:
                    args.extend(a_clean.split())
            cwd = self.cwd_entry.get().strip() or None
        else:
            url = self.url_entry.get().strip()
            if not url:
                raise ValueError("Uzak sunucu için URL adresi belirtilmelidir.")

        # Parse env lines
        env_dict: Dict[str, str] = {}
        secret_env_dict: Dict[str, str] = {}
        raw_env_lines = self.env_entry.get("1.0", "end").strip().splitlines()
        for line in raw_env_lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if "=" in line_str:
                k, v = line_str.split("=", 1)
                k = k.strip()
                v = v.strip()
                # Check if secret
                is_secret_name = any(kw in k.lower() for kw in ("token", "secret", "key", "password", "auth"))
                if v == "••••••••":
                    # Retain existing reference
                    if self.existing_config and k in self.existing_config.secret_env:
                        secret_env_dict[k] = self.existing_config.secret_env[k]
                elif is_secret_name and len(v) >= 4:
                    ref = store_mcp_secret(name, k, v)
                    secret_env_dict[k] = ref
                else:
                    env_dict[k] = v

        policy_mode = ToolPolicyMode.CONFIRM if self.confirm_tools_var.get() else ToolPolicyMode.ALLOW
        tool_policy = ToolPolicy(mode=policy_mode)

        return MCPServerConfig(
            name=name,
            transport=transport,
            command=cmd,
            args=args,
            cwd=cwd,
            env=env_dict,
            secret_env=secret_env_dict,
            url=url,
            autostart=self.autostart_var.get(),
            default_for_chat=self.default_chat_var.get(),
            tool_policy=tool_policy,
        )

    def _on_test_clicked(self) -> None:
        try:
            cfg = self._build_config_from_form()
        except Exception as e:
            messagebox.showwarning("Form Hatası", str(e), parent=self)
            return

        self.test_status_box.configure(text="Bağlantı test ediliyor...", text_color="#38bdf8")

        def on_done(res: MCPTestResult):
            if res.success:
                self.test_status_box.configure(text=f"✓ Test Başarılı: {res.tool_count} araç bulundu ({res.duration_ms:.0f}ms)", text_color="#10b981")
            else:
                self.test_status_box.configure(text=f"✕ Test Başarısız: {res.error}", text_color="#ef4444")
            MCPTestResultDialog(self, cfg.name, res)

        def on_error(err: str):
            self.test_status_box.configure(text=f"✕ Hata: {err}", text_color="#ef4444")

        self.service.test_mcp_async(cfg.name, temp_config=cfg, on_success=on_done, on_error=on_error)

    def _on_save_clicked(self) -> None:
        try:
            cfg = self._build_config_from_form()
        except Exception as e:
            messagebox.showwarning("Form Hatası", str(e), parent=self)
            return

        def on_saved():
            if self.on_save:
                self.on_save()
            self.destroy()

        def on_error(err: str):
            messagebox.showerror("Kaydetme Hatası", f"Sunucu kaydedilemedi: {err}", parent=self)

        if self.is_edit and self.existing_config:
            self.service.update_mcp_server_async(self.existing_config.name, cfg, on_success=on_saved, on_error=on_error)
        else:
            self.service.add_mcp_server_async(cfg, connect_now=cfg.autostart, on_success=on_saved, on_error=on_error)


class MCPView(ctk.CTkFrame):
    """MCP Yönetim Paneli Ana Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.servers: List[Dict[str, Any]] = []

        self._build_ui()
        self.service.add_mcp_listener(self.refresh_list)
        self.refresh_list()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. ÜST PANEL
        header = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        header.grid_columnconfigure(0, weight=1)

        t_box = ctk.CTkFrame(header, fg_color="transparent")
        t_box.pack(side="left", padx=16, pady=10)

        t_lbl = ctk.CTkLabel(
            t_box,
            text="🔌 Model Context Protocol (MCP) Yöneticisi",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w")

        d_lbl = ctk.CTkLabel(
            t_box,
            text="Yerel ve uzak MCP sunucularını yönetin, araçlarını keşfedin ve sohbet oturumlarına bağlayın.",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "#94a3b8"),
        )
        d_lbl.pack(anchor="w")

        # Üst Aksiyonlar
        act_box = ctk.CTkFrame(header, fg_color="transparent")
        act_box.pack(side="right", padx=12, pady=10)

        import_btn = ctk.CTkButton(
            act_box,
            text="İçe Aktar",
            width=80,
            height=30,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._import_configs,
        )
        import_btn.pack(side="left", padx=4)

        export_btn = ctk.CTkButton(
            act_box,
            text="Dışa Aktar",
            width=85,
            height=30,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._export_configs,
        )
        export_btn.pack(side="left", padx=4)

        add_btn = ctk.CTkButton(
            act_box,
            text="+ MCP Ekle",
            width=110,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._open_add_dialog,
        )
        add_btn.pack(side="left", padx=6)

        # 2. ARAMA VE FİLTRE ÇUBUĞU
        search_bar = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=8)
        search_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        s_lbl = ctk.CTkLabel(search_bar, text="🔍 Ara:", font=ctk.CTkFont(size=12, weight="bold"))
        s_lbl.pack(side="left", padx=(12, 6), pady=8)

        self.search_entry = ctk.CTkEntry(
            search_bar,
            placeholder_text="Sunucu adı veya araç adı (ör. github, filesystem, text_transformer)...",
            height=28,
            font=ctk.CTkFont(size=12),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=6, pady=8)
        self.search_entry.bind("<KeyRelease>", lambda _: self._filter_servers())

        refresh_btn = ctk.CTkButton(
            search_bar,
            text="Yenile",
            width=70,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self.refresh_list,
        )
        refresh_btn.pack(side="right", padx=12, pady=8)

        # 3. SUNUCU LİSTESİ ALANI
        self.scroll_list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_list.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        self.grid_rowconfigure(2, weight=1)

    def refresh_list(self) -> None:
        """Sunucu listesini servisten okuyup arayüzü günceller."""
        self.servers = self.service.get_mcp_servers()
        self._filter_servers()

    def _filter_servers(self) -> None:
        query = self.search_entry.get().strip().lower()
        for w in self.scroll_list.winfo_children():
            w.destroy()

        filtered = []
        for s in self.servers:
            s_name = s.get("name", "").lower()
            tools = [t.lower() for t in s.get("tool_names", [])]
            if not query or query in s_name or any(query in t for t in tools):
                filtered.append(s)

        if not filtered:
            msg = "Yapılandırılmış MCP sunucusu bulunmuyor." if not self.servers else "Arama kriterine uygun sunucu bulunamadı."
            hint = "Yukarıdaki '+ MCP Ekle' butonunu kullanarak yeni bir sunucu ekleyin."
            box = ctk.CTkFrame(self.scroll_list, fg_color=("gray95", "#161f2e"), corner_radius=10)
            box.pack(fill="x", padx=8, pady=32)
            ctk.CTkLabel(box, text=msg, font=ctk.CTkFont(size=14, weight="bold"), text_color=("gray30", "#94a3b8")).pack(pady=(16, 4))
            ctk.CTkLabel(box, text=hint, font=ctk.CTkFont(size=11), text_color=("gray40", "#64748b")).pack(pady=(0, 16))
            return

        for s in filtered:
            self._render_server_card(s)

    def _render_server_card(self, s: Dict[str, Any]) -> None:
        card = ctk.CTkFrame(
            self.scroll_list,
            fg_color=("gray95", "#161f2e"),
            corner_radius=10,
            border_width=1,
            border_color=("gray85", "#243247"),
        )
        card.pack(fill="x", padx=4, pady=6)

        name = s.get("name", "unknown")
        status = s.get("status", "disconnected")
        transport = s.get("transport", "stdio")
        cmd_or_url = s.get("command_or_url", "-")
        tool_count = s.get("tool_count", 0)
        autostart = s.get("autostart", False)
        default_chat = s.get("default_for_chat", False)
        last_error = s.get("last_error")
        last_conn = s.get("last_connected_time", "-")
        pid = s.get("pid")

        # Header Satırı
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(10, 4))

        title_lbl = ctk.CTkLabel(top_row, text=name, font=ctk.CTkFont(size=15, weight="bold"), text_color="#38bdf8")
        title_lbl.pack(side="left")

        # Rozetler
        if status == "connected":
            status_color = "#10b981"
            status_text = "● Bağlı"
        elif status == "starting":
            status_color = "#f59e0b"
            status_text = "◌ Bağlanıyor..."
        elif status == "error":
            status_color = "#ef4444"
            status_text = "⚠ Hata"
        else:
            status_color = "#94a3b8"
            status_text = "○ Bağlantı Yok"

        s_badge = ctk.CTkLabel(top_row, text=status_text, font=ctk.CTkFont(size=11, weight="bold"), text_color=status_color)
        s_badge.pack(side="left", padx=10)

        t_badge = ctk.CTkLabel(top_row, text=f"[{transport.upper()}]", font=ctk.CTkFont(size=10), text_color=("gray40", "#64748b"))
        t_badge.pack(side="left")

        if autostart:
            auto_lbl = ctk.CTkLabel(top_row, text="[Otomatik]", font=ctk.CTkFont(size=10), text_color="#06b6d4")
            auto_lbl.pack(side="left", padx=4)

        if default_chat:
            def_lbl = ctk.CTkLabel(top_row, text="[Varsayılan]", font=ctk.CTkFont(size=10), text_color="#8b5cf6")
            def_lbl.pack(side="left", padx=4)

        # Bilgi Satırı
        info_row = ctk.CTkFrame(card, fg_color="transparent")
        info_row.pack(fill="x", padx=14, pady=(2, 6))

        pid_info = f" | PID: {pid}" if pid else ""
        info_text = f"Hedef: {cmd_or_url} | Araçlar: {tool_count} | Son Bağlantı: {last_conn}{pid_info}"
        ctk.CTkLabel(info_row, text=info_text, font=ctk.CTkFont(size=11), text_color=("gray30", "#94a3b8"), anchor="w").pack(side="left")

        if last_error:
            err_box = ctk.CTkFrame(card, fg_color=("#fee2e2", "#450a0a"), corner_radius=6)
            err_box.pack(fill="x", padx=14, pady=(0, 6))
            ctk.CTkLabel(err_box, text=f"Son Hata: {last_error}", font=ctk.CTkFont(size=10), text_color="#f87171", anchor="w").pack(padx=8, pady=4)

        # Butonlar Satırı
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(4, 10))

        if status == "connected":
            toggle_conn_btn = ctk.CTkButton(
                btn_row,
                text="Bağlantıyı Kes",
                width=100,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color="#ef4444",
                hover_color="#dc2626",
                command=lambda n=name: self._disconnect_server(n),
            )
        else:
            toggle_conn_btn = ctk.CTkButton(
                btn_row,
                text="Bağlan",
                width=80,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color="#10b981",
                hover_color="#059669",
                command=lambda n=name: self._connect_server(n),
            )
        toggle_conn_btn.pack(side="left", padx=(0, 4))

        restart_btn = ctk.CTkButton(
            btn_row,
            text="Yeniden Başlat",
            width=105,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._restart_server(n),
        )
        restart_btn.pack(side="left", padx=4)

        test_btn = ctk.CTkButton(
            btn_row,
            text="Sına",
            width=65,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._test_server(n),
        )
        test_btn.pack(side="left", padx=4)

        tools_btn = ctk.CTkButton(
            btn_row,
            text=f"Araçlar ({tool_count})",
            width=90,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._view_tools(n),
        )
        tools_btn.pack(side="left", padx=4)

        logs_btn = ctk.CTkButton(
            btn_row,
            text="Loglar",
            width=70,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._view_logs(n),
        )
        logs_btn.pack(side="left", padx=4)

        # Sağ Taraf: Düzenle, Çoğalt, Sil
        del_btn = ctk.CTkButton(
            btn_row,
            text="Sil",
            width=55,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            text_color="#ef4444",
            hover_color=("#fee2e2", "#450a0a"),
            command=lambda n=name: self._remove_server(n),
        )
        del_btn.pack(side="right", padx=(4, 0))

        dup_btn = ctk.CTkButton(
            btn_row,
            text="Çoğalt",
            width=65,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._duplicate_server(n),
        )
        dup_btn.pack(side="right", padx=4)

        edit_btn = ctk.CTkButton(
            btn_row,
            text="Düzenle",
            width=75,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=lambda n=name: self._edit_server(n),
        )
        edit_btn.pack(side="right", padx=4)

    def _open_add_dialog(self) -> None:
        MCPEditDialog(self, service=self.service, on_save=self.refresh_list)

    def _edit_server(self, name: str) -> None:
        conn = self.service.mcp_manager.get_connection(name)
        if conn:
            MCPEditDialog(self, service=self.service, config=conn.config, on_save=self.refresh_list)

    def _connect_server(self, name: str) -> None:
        self.service.connect_mcp_async(
            name,
            on_success=self.refresh_list,
            on_error=lambda err: messagebox.showerror("Bağlantı Hatası", f"'{name}' sunucusuna bağlanılamadı: {err}", parent=self),
        )

    def _disconnect_server(self, name: str) -> None:
        self.service.disconnect_mcp_async(name, on_success=self.refresh_list)

    def _restart_server(self, name: str) -> None:
        self.service.restart_mcp_async(
            name,
            on_success=self.refresh_list,
            on_error=lambda err: messagebox.showerror("Yeniden Başlatma Hatası", f"'{name}' başlatılamadı: {err}", parent=self),
        )

    def _test_server(self, name: str) -> None:
        def on_done(res: MCPTestResult):
            MCPTestResultDialog(self, name, res)

        def on_error(err: str):
            messagebox.showerror("Test Hatası", f"'{name}' testi başarısız: {err}", parent=self)

        self.service.test_mcp_async(name, on_success=on_done, on_error=on_error)

    def _view_tools(self, name: str) -> None:
        def on_tools(tools):
            MCPToolsDialog(self, name, tools)

        self.service.get_mcp_tools_async(name, on_success=on_tools, on_error=lambda e: messagebox.showerror("Araç Hatası", e, parent=self))

    def _view_logs(self, name: str) -> None:
        MCPLogsDialog(self, name, self.service)

    def _duplicate_server(self, name: str) -> None:
        new_name = f"{name}_copy"
        try:
            self.service.duplicate_mcp_server(name, new_name)
            self.refresh_list()
        except Exception as e:
            messagebox.showerror("Klonlama Hatası", str(e), parent=self)

    def _remove_server(self, name: str) -> None:
        cevap = messagebox.askyesno(
            "MCP Sunucusunu Sil",
            f"'{name}' MCP sunucusunu tamamen silmek istediğinizden emin misiniz?\n\nBu işlem sunucuyu config dosyasından kaldıracak, çalışan süreci durduracak ve tüm aktif sohbetlerden çıkaracaktır.",
            parent=self,
        )
        if not cevap:
            return

        def on_done():
            self.refresh_list()

        self.service.remove_mcp_server_async(name, on_success=on_done, on_error=lambda e: messagebox.showerror("Silme Hatası", e, parent=self))

    def _export_configs(self) -> None:
        exported = self.service.export_mcp_configs()
        if not exported:
            messagebox.showinfo("Dışa Aktarma", "Dışa aktarılacak yapılandırılmış MCP sunucusu bulunamadı.", parent=self)
            return

        file_path = filedialog.asksaveasfilename(
            title="MCP Yapılandırmasını Kaydet",
            defaultextension=".json",
            filetypes=[("JSON Dosyası", "*.json"), ("Tüm Dosyalar", "*.*")],
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(exported, f, indent=2, ensure_ascii=False)
                messagebox.showinfo("Başarılı", f"MCP yapılandırması başarıyla dışa aktarıldı:\n{file_path}\n\nNot: Güvenlik için gizli anahtarlar '<SECRET_REQUIRED>' olarak maskelenmiştir.", parent=self)
            except Exception as e:
                messagebox.showerror("Kayıt Hatası", str(e), parent=self)

    def _import_configs(self) -> None:
        file_path = filedialog.askopenfilename(
            title="MCP Yapılandırma Dosyası Seç",
            filetypes=[("JSON Dosyası", "*.json"), ("Tüm Dosyalar", "*.*")],
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Geçersiz JSON formatı. Ana eleman sözlük olmalıdır.")

            count = self.service.import_mcp_configs(data)
            self.refresh_list()
            messagebox.showinfo("İçe Aktarma Tamamlandı", f"{count} adet MCP sunucusu başarıyla içe aktarıldı.", parent=self)
        except Exception as e:
            messagebox.showerror("İçe Aktarma Hatası", str(e), parent=self)
