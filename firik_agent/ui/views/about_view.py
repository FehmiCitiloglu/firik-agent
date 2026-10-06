"""firik masaüstü uygulaması - Hakkında Görünümü (About View).

Uygulama bilgileri, sürüm ve platform detaylarını gösterir.
Tüm içerik Türkçedir.
"""
from __future__ import annotations

import platform
import sys
import tkinter as tk
from typing import Any
import webbrowser

import customtkinter as ctk

from firik_agent.ui.theme import APP_NAME, APP_SUBTITLE, APP_VERSION


class AboutView(ctk.CTkFrame):
    """Hakkında Görünümü."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # 1. LOGO VE BAŞLIK ALANI
        hero_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=12)
        hero_card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 12))

        title_lbl = ctk.CTkLabel(
            hero_card,
            text=APP_NAME,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color="#38bdf8",
        )
        title_lbl.pack(pady=(20, 2))

        sub_lbl = ctk.CTkLabel(
            hero_card,
            text=APP_SUBTITLE,
            font=ctk.CTkFont(size=14),
            text_color=("gray30", "#94a3b8"),
        )
        sub_lbl.pack(pady=(0, 4))

        ver_lbl = ctk.CTkLabel(
            hero_card,
            text=f"Sürüm: {APP_VERSION}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10b981",
        )
        ver_lbl.pack(pady=(0, 16))

        # 2. ÖZELLİKLER KARTI
        features_card = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=10)
        features_card.grid(row=1, column=0, sticky="ew", padx=16, pady=6)

        ctk.CTkLabel(
            features_card,
            text="Temel Özellikler",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))

        features_text = (
            "• Akışlı (Streaming) Sohbet: Gerçek zamanlı yapay zekâ metin üretimi ve çoklu mod desteği.\n"
            "• Görsel ve Belge OCR: Belgelerden, tablolardan ve fotoğraflardan yüksek doğrulukla metin çıkarma.\n"
            "• Ses Çözümleme (ASR): Ses kayıtlarını metne ve zaman damgalı SRT altyazılara dönüştürme.\n"
            "• Semantik Yeniden Sıralama (Rerank): Arama sonuçlarını alaka düzeyine göre sıralama.\n"
            "• Şifreli Anahtar Kasası: API anahtarlarını işletim sisteminin güvenli kasasında (Keyring) saklama.\n"
            "• Çapraz Platform: macOS, Windows ve Linux işletim sistemleriyle tam uyumlu çalışma."
        )
        ctk.CTkLabel(
            features_card,
            text=features_text,
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        # 3. SİSTEM BİLGİSİ
        sys_card = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=10)
        sys_card.grid(row=2, column=0, sticky="ew", padx=16, pady=6)

        ctk.CTkLabel(
            sys_card,
            text="Sistem ve Çalışma Ortamı",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))

        sys_info = (
            f"İşletim Sistemi: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Python Sürümü: {sys.version.split()[0]}\n"
            f"Grafik Motoru: Tkinter {tk.TkVersion} / CustomTkinter {ctk.__version__}"
        )
        ctk.CTkLabel(
            sys_card,
            text=sys_info,
            font=ctk.CTkFont(family="monospace", size=11),
            text_color=("gray40", "#94a3b8"),
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        # 4. BAĞLANTILAR
        links_frame = ctk.CTkFrame(self, fg_color="transparent")
        links_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=16)

        portal_btn = ctk.CTkButton(
            links_frame,
            text="🌐 FIRIK Portalı",
            width=140,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1,
            command=lambda: webbrowser.open("https://evren.ssyz.org.tr"),
        )
        portal_btn.pack(side="left", padx=4)

        keys_btn = ctk.CTkButton(
            links_frame,
            text="🔑 API Anahtarları",
            width=140,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1,
            command=lambda: webbrowser.open("https://evren.ssyz.org.tr/api-keys"),
        )
        keys_btn.pack(side="left", padx=4)
