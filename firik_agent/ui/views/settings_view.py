"""firik masaüstü uygulaması - Ayarlar Görünümü (Settings View).

API taban adresi, işletim sistemi anahtar kasası (Keyring) yönetimi,
varsayılan model, zaman aşımı ve görsel tema yapılandırmasını sağlar.
Tüm arayüz Türkçedir.
"""
from __future__ import annotations

from typing import Any, List
from tkinter import messagebox

import customtkinter as ctk

from firik_agent.ui.service import FirikService


class SettingsView(ctk.CTkFrame):
    """Uygulama ve Bağlantı Ayarları Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.show_password = False

        self._build_ui()
        self._load_current_values()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # 1. BAŞLIK
        header_frame = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 10))

        t_lbl = ctk.CTkLabel(
            header_frame,
            text="Uygulama ve Bağlantı Ayarları",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        d_lbl = ctk.CTkLabel(
            header_frame,
            text="API uç noktalarını, güvenli anahtar kasasını ve masaüstü arayüz tercihlerinizi özelleştirin.",
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
        )
        d_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        # 2. ANA AYARLAR KARTI
        card = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=10)
        card.grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        card.grid_columnconfigure(1, weight=1)

        # API Taban URL'si
        url_lbl = ctk.CTkLabel(card, text="API Taban Adresi:", font=ctk.CTkFont(size=12, weight="bold"))
        url_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        self.url_entry = ctk.CTkEntry(
            card,
            placeholder_text="https://evren-llmapi.ssyz.org.tr/v1",
            height=32,
            font=ctk.CTkFont(size=12),
        )
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=16, pady=(14, 6))

        # API Anahtarı
        key_lbl = ctk.CTkLabel(card, text="LLM Çıkarım Anahtarı:", font=ctk.CTkFont(size=12, weight="bold"))
        key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=6)

        key_box = ctk.CTkFrame(card, fg_color="transparent")
        key_box.grid(row=1, column=1, sticky="ew", padx=16, pady=6)
        key_box.grid_columnconfigure(0, weight=1)

        self.key_entry = ctk.CTkEntry(
            key_box,
            placeholder_text="firik_llm_...",
            height=32,
            show="•",
            font=ctk.CTkFont(size=12),
        )
        self.key_entry.grid(row=0, column=0, sticky="ew")

        self.show_key_btn = ctk.CTkButton(
            key_box,
            text="Göster",
            width=65,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._toggle_key_visibility,
        )
        self.show_key_btn.grid(row=0, column=1, padx=(6, 0))

        save_key_btn = ctk.CTkButton(
            key_box,
            text="Kasaya Kaydet",
            width=110,
            height=32,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._save_api_key,
        )
        save_key_btn.grid(row=0, column=2, padx=(6, 0))

        key_hint = ctk.CTkLabel(
            card,
            text="ℹ Anahtarınız dosyalara düz metin olarak yazılmaz. İşletim sistemi kasasında güvenle saklanır.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        )
        key_hint.grid(row=2, column=1, sticky="w", padx=16, pady=(0, 8))

        # Varsayılan Model
        m_lbl = ctk.CTkLabel(card, text="Varsayılan Model:", font=ctk.CTkFont(size=12, weight="bold"))
        m_lbl.grid(row=3, column=0, sticky="w", padx=16, pady=6)

        self.model_combo = ctk.CTkComboBox(
            card,
            values=["glm-5.3", "qwen-2.5-72b-instruct", "deepseek-r1"],
            height=32,
            width=260,
        )
        self.model_combo.grid(row=3, column=1, sticky="w", padx=16, pady=6)

        # Zaman Aşımı (Timeout)
        t_lbl = ctk.CTkLabel(card, text="Zaman Aşımı (saniye):", font=ctk.CTkFont(size=12, weight="bold"))
        t_lbl.grid(row=4, column=0, sticky="w", padx=16, pady=6)

        self.timeout_entry = ctk.CTkEntry(
            card,
            placeholder_text="180",
            height=32,
            width=120,
            font=ctk.CTkFont(size=12),
        )
        self.timeout_entry.grid(row=4, column=1, sticky="w", padx=16, pady=6)

        # Görünüm Teması
        theme_lbl = ctk.CTkLabel(card, text="Görünüm Teması:", font=ctk.CTkFont(size=12, weight="bold"))
        theme_lbl.grid(row=5, column=0, sticky="w", padx=16, pady=6)

        self.theme_combo = ctk.CTkComboBox(
            card,
            values=["Koyu Tema (Dark)", "Açık Tema (Light)", "Sistem Teması (System)"],
            height=32,
            width=200,
            command=self._change_theme,
        )
        self.theme_combo.set("Koyu Tema (Dark)")
        self.theme_combo.grid(row=5, column=1, sticky="w", padx=16, pady=6)

        # Yakınlaştırma / Ölçek
        scale_lbl = ctk.CTkLabel(card, text="Arayüz Ölçeği:", font=ctk.CTkFont(size=12, weight="bold"))
        scale_lbl.grid(row=6, column=0, sticky="w", padx=16, pady=(6, 16))

        self.scale_combo = ctk.CTkComboBox(
            card,
            values=["%90", "%100 (Varsayılan)", "%110", "%120", "%130"],
            height=32,
            width=200,
            command=self._change_scaling,
        )
        self.scale_combo.set("%100 (Varsayılan)")
        self.scale_combo.grid(row=6, column=1, sticky="w", padx=16, pady=(6, 16))

        # 2.5. BİLGİSAYAR KULLANIMI VE İZİNLER KARTI
        cu_card = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=10)
        cu_card.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        cu_card.grid_columnconfigure(1, weight=1)

        cu_title = ctk.CTkLabel(
            cu_card,
            text="🖥️ Bilgisayar Denetimi ve İzinler (Computer Use)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
        )
        cu_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4))

        cu_desc = ctk.CTkLabel(
            cu_card,
            text="Ekran yakalama, fare ve klavye otomasyon yetenekleri ile sistem izinleri durumu.",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "#94a3b8"),
        )
        cu_desc.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 8))

        self.cu_doctor_btn = ctk.CTkButton(
            cu_card,
            text="🔍 Sistem İzinlerini Denetle (Doctor)",
            width=220,
            height=32,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray90"),
            command=self._check_computer_use_permissions,
        )
        self.cu_doctor_btn.grid(row=2, column=0, sticky="w", padx=16, pady=(4, 12))

        self.cu_status_lbl = ctk.CTkLabel(
            cu_card,
            text="İzin durumu denetlenmedi.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        )
        self.cu_status_lbl.grid(row=2, column=1, sticky="w", padx=8, pady=(4, 12))

        # 3. ALT AKSİYON PANELİ: Bağlantıyı Sına & Kaydet
        actions_card = ctk.CTkFrame(self, fg_color="transparent")
        actions_card.grid(row=3, column=0, sticky="ew", padx=16, pady=16)


        test_btn = ctk.CTkButton(
            actions_card,
            text="🌐 Bağlantıyı Sına",
            width=160,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray90"),
            command=self._test_connection,
        )
        test_btn.pack(side="left")

        self.test_status_lbl = ctk.CTkLabel(
            actions_card,
            text="",
            font=ctk.CTkFont(size=12),
        )
        test_status_lbl = self.test_status_lbl
        test_status_lbl.pack(side="left", padx=16)

        save_btn = ctk.CTkButton(
            actions_card,
            text="✓ Ayarları Kaydet",
            width=160,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._save_settings,
        )
        save_btn.pack(side="right")

    def _load_current_values(self) -> None:
        self.url_entry.insert(0, self.service.base_url)
        self.model_combo.set(self.service.default_model)
        self.timeout_entry.insert(0, str(int(self.service.timeout)))

        existing_key = self.service.get_key()
        if existing_key:
            self.key_entry.insert(0, existing_key)

        def on_models(models: List[str]):
            if models:
                self.model_combo.configure(values=models)
                if self.service.default_model in models:
                    self.model_combo.set(self.service.default_model)

        self.service.fetch_models_async(on_success=lambda m: self.after(0, lambda: on_models(m)))

    def _toggle_key_visibility(self) -> None:
        if self.show_password:
            self.key_entry.configure(show="•")
            self.show_key_btn.configure(text="Göster")
            self.show_password = False
        else:
            self.key_entry.configure(show="")
            self.show_key_btn.configure(text="Gizle")
            self.show_password = True

    def _save_api_key(self) -> None:
        raw_key = self.key_entry.get().strip()
        if not raw_key:
            messagebox.showwarning("Boş Anahtar", "Lütfen bir API anahtarı giriniz.")
            return

        try:
            self.service.set_key(raw_key)
            messagebox.showinfo("Başarılı", "API anahtarınız işletim sisteminin güvenli kimlik bilgisi kasasına kaydedildi.")
        except Exception as e:
            messagebox.showerror("Kayıt Hatası", f"Anahtar kasaya kaydedilemedi:\n{e}")

    def _save_settings(self) -> None:
        url = self.url_entry.get().strip()
        model = self.model_combo.get().strip()
        raw_timeout = self.timeout_entry.get().strip()

        if not url:
            messagebox.showwarning("Eksik Bilgi", "Lütfen geçerli bir API taban adresi giriniz.")
            return

        try:
            timeout = float(raw_timeout)
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Geçersiz Değer", "Zaman aşımı pozitif bir sayı olmalıdır.")
            return

        self.service.update_settings(base_url=url, default_model=model, timeout=timeout)
        messagebox.showinfo("Ayarlar Kaydedildi", "Tüm yapılandırma ayarları başarıyla güncellendi.")

    def _test_connection(self) -> None:
        self.test_status_lbl.configure(text="Sunucuya bağlanılıyor...", text_color="#38bdf8")

        def on_success(data):
            self.test_status_lbl.configure(text="✓ Bağlantı Başarılı! Sunucu hazır.", text_color="#10b981")

        def on_error(err):
            self.test_status_lbl.configure(text="✗ Bağlantı Başarısız!", text_color="#ef4444")
            messagebox.showerror("Bağlantı Hatası", err)

        self.service.test_connection_async(
            on_success=lambda d: self.after(0, lambda: on_success(d)),
            on_error=lambda e: self.after(0, lambda: on_error(e)),
        )

    def _change_theme(self, choice: str) -> None:
        if "Dark" in choice or "Koyu" in choice:
            ctk.set_appearance_mode("Dark")
        elif "Light" in choice or "Açık" in choice:
            ctk.set_appearance_mode("Light")
        else:
            ctk.set_appearance_mode("System")

    def _change_scaling(self, choice: str) -> None:
        scale_map = {
            "%90": 0.9,
            "%100 (Varsayılan)": 1.0,
            "%110": 1.1,
            "%120": 1.2,
            "%130": 1.3,
        }
        val = scale_map.get(choice, 1.0)
        ctk.set_widget_scaling(val)
        ctk.set_window_scaling(val)

    def _check_computer_use_permissions(self) -> None:
        """Sistem ekran ve erişilebilirlik izinlerini sorgulayıp durum rozetini günceller."""
        try:
            from firik_agent.computer_use.permissions import PermissionChecker
            perms = PermissionChecker.check_all()
            details = []
            all_granted = True
            for k, v in perms.items():
                name = k.replace("_", " ").title()
                if v == "granted":
                    details.append(f"{name}: ✓ İzin Verildi")
                else:
                    details.append(f"{name}: ✗ Reddedildi")
                    all_granted = False

            summary = " · ".join(details)
            if all_granted:
                self.cu_status_lbl.configure(text=f"✓ Tüm İzinler Tamam ({summary})", text_color="#10b981")
            else:
                self.cu_status_lbl.configure(text=f"⚠ İzin Eksik: {summary}", text_color="#f59e0b")
                messagebox.showwarning(
                    "İzin Gerekli",
                    "Bilgisayar denetimi için sistem izinleri eksik:\n"
                    f"{summary}\n\n"
                    "Lütfen macOS Sistem Ayarları → Gizlilik ve Güvenlik altından\n"
                    "Ekran Kaydı ve Erişilebilirlik izinlerini veriniz.",
                )
        except Exception as e:
            self.cu_status_lbl.configure(text=f"Hata: {e}", text_color="#ef4444")

