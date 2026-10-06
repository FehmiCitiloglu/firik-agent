"""firik masaüstü uygulaması - Kota, Bakiye ve Sistem Durumu Görünümü.

Token kotası, kalan kredi bakiyesi, sunucu canlılık kontrolü ve
resmî kullanım şartları onay durumunu takip eder.
Tüm metinler Türkçedir.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from tkinter import messagebox

import customtkinter as ctk

from firik_agent.ui.service import FirikService


class QuotaView(ctk.CTkFrame):
    """Kota ve Sistem Durumu Kontrol Paneli."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.is_refreshing = False

        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # 1. ÜST BAŞLIK VE YENİLE BUTONU
        header_frame = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 10))

        t_lbl = ctk.CTkLabel(
            header_frame,
            text="Hesap Kotası ve Sistem Durumu",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(side="left", padx=16, pady=12)

        self.refresh_btn = ctk.CTkButton(
            header_frame,
            text="🔄 Bilgileri Yenile",
            width=140,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self.refresh_all,
        )
        self.refresh_btn.pack(side="right", padx=16, pady=12)

        # 2. DÖRT ANA BİLGİ KARTI
        # Kart 1: API Anahtarı ve Güvenlik Durumu
        key_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        key_card.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=6)
        ctk.CTkLabel(key_card, text="🔑 API Anahtarı Durumu", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.key_status_lbl = ctk.CTkLabel(key_card, text="Sorgulanıyor...", font=ctk.CTkFont(size=12))
        self.key_status_lbl.pack(anchor="w", padx=14, pady=2)
        self.key_mask_lbl = ctk.CTkLabel(key_card, text="", font=ctk.CTkFont(family="monospace", size=11), text_color="#94a3b8")
        self.key_mask_lbl.pack(anchor="w", padx=14, pady=(0, 12))

        # Kart 2: Kredi Bakiyesi
        credit_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        credit_card.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=6)
        ctk.CTkLabel(credit_card, text="💳 Kredi Bakiyesi", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.credit_lbl = ctk.CTkLabel(credit_card, text="Sorgulanıyor...", font=ctk.CTkFont(size=14, weight="bold"), text_color="#10b981")
        self.credit_lbl.pack(anchor="w", padx=14, pady=2)
        self.credit_sub_lbl = ctk.CTkLabel(credit_card, text="", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.credit_sub_lbl.pack(anchor="w", padx=14, pady=(0, 12))

        # Kart 3: Token Kotası ve Limitler
        quota_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        quota_card.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=6)
        ctk.CTkLabel(quota_card, text="📊 Token / İstek Kotası", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.quota_lbl = ctk.CTkLabel(quota_card, text="Sorgulanıyor...", font=ctk.CTkFont(size=13, weight="bold"), text_color="#38bdf8")
        self.quota_lbl.pack(anchor="w", padx=14, pady=2)
        self.quota_sub_lbl = ctk.CTkLabel(quota_card, text="", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.quota_sub_lbl.pack(anchor="w", padx=14, pady=(0, 12))

        # Kart 4: Sunucu Sağlık ve Hazırlık Kontrolü
        health_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        health_card.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=6)
        ctk.CTkLabel(health_card, text="🌐 Sunucu Sağlık Durumu", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.health_lbl = ctk.CTkLabel(health_card, text="Sorgulanıyor...", font=ctk.CTkFont(size=12))
        self.health_lbl.pack(anchor="w", padx=14, pady=2)
        self.ready_lbl = ctk.CTkLabel(health_card, text="", font=ctk.CTkFont(size=11), text_color="#94a3b8")
        self.ready_lbl.pack(anchor="w", padx=14, pady=(0, 12))

        # 3. KULLANIM ŞARTLARI (TERMS) KARTI
        terms_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        terms_card.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 16))

        t_row = ctk.CTkFrame(terms_card, fg_color="transparent")
        t_row.pack(fill="x", padx=16, pady=12)

        terms_left = ctk.CTkFrame(t_row, fg_color="transparent")
        terms_left.pack(side="left")

        ctk.CTkLabel(terms_left, text="📋 Resmî Kullanım Şartları (Terms of Service)", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        self.terms_status_lbl = ctk.CTkLabel(terms_left, text="Durum sorgulanıyor...", font=ctk.CTkFont(size=12), text_color="#94a3b8")
        self.terms_status_lbl.pack(anchor="w", pady=(2, 0))

        # Şartlar Butonları
        terms_right = ctk.CTkFrame(t_row, fg_color="transparent")
        terms_right.pack(side="right")

        self.accept_terms_btn = ctk.CTkButton(
            terms_right,
            text="✓ Şartları Onayla",
            width=130,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            state="disabled",
            command=self._accept_terms,
        )
        self.accept_terms_btn.pack(side="right", padx=(6, 0))

        view_terms_btn = ctk.CTkButton(
            terms_right,
            text="Metni Oku",
            width=90,
            height=30,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._view_terms_text,
        )
        view_terms_btn.pack(side="right")

        self.latest_version: int = 1

    def refresh_all(self) -> None:
        """Tüm durumları asenkron yeniler."""
        if self.is_refreshing:
            return
        self.is_refreshing = True
        self.refresh_btn.configure(text="Yenileniyor...", state="disabled")

        # 1. API Anahtarı Kontrolü
        key = self.service.get_key()
        if key:
            self.key_status_lbl.configure(text="● Kayıtlı ve Kullanıma Hazır", text_color="#10b981")
            masked = key[:10] + "..." + key[-4:] if len(key) > 16 else "********"
            self.key_mask_lbl.configure(text=f"Anahtar: {masked}\nDepolama: İşletim Sistemi Güvenli Kasası")
        else:
            self.key_status_lbl.configure(text="● API Anahtarı Eksik", text_color="#ef4444")
            self.key_mask_lbl.configure(text="Lütfen 'Ayarlar' sekmesinden LLM Çıkarım anahtarınızı kaydedin.")

        # 2. Sunucu Sağlık Kontrolü
        def on_health(data: Dict[str, Any]):
            h_stat = "Sağlıklı (Liveness OK)" if data.get("health") else "Yanıt Alındı"
            r_stat = "Trafiğe Hazır (Readiness OK)" if data.get("ready") else "Hazır"
            self.health_lbl.configure(text=f"● {h_stat}", text_color="#10b981")
            self.ready_lbl.configure(text=f"Uç Nokta: {self.service.base_url}\n{r_stat}")

        def on_health_err(err: str):
            self.health_lbl.configure(text="● Sunucuya Erişilemiyor", text_color="#ef4444")
            self.ready_lbl.configure(text=err)

        self.service.test_connection_async(
            on_success=lambda d: self.after(0, lambda: on_health(d)),
            on_error=lambda e: self.after(0, lambda: on_health_err(e)),
        )

        # 3. Kota ve Şartlar Kontrolü
        def on_quota(data: Dict[str, Any]):
            self.is_refreshing = False
            self.refresh_btn.configure(text="🔄 Bilgileri Yenile", state="normal")

            # Kredi Bilgisi
            quota_data = data.get("quota") or {}
            credit = quota_data.get("credit") or quota_data.get("balance") or quota_data.get("remaining_credit")
            if credit is not None:
                self.credit_lbl.configure(text=f"₺{credit}")
            else:
                self.credit_lbl.configure(text="Aktif")

            reserved = quota_data.get("reserved_credit", "0.00")
            self.credit_sub_lbl.configure(text=f"Rezerve Kredi: ₺{reserved}")

            # Token Kotası
            tokens = quota_data.get("tokens") or quota_data.get("remaining_tokens") or "Sınırsız / Tanımlı Değil"
            self.quota_lbl.configure(text=f"Kalan: {tokens}")
            reset_time = quota_data.get("reset_at") or quota_data.get("period") or "Dönemsel"
            self.quota_sub_lbl.configure(text=f"Yenilenme: {reset_time}")

            # Şartlar
            terms_data = data.get("terms") or {}
            accepted = terms_data.get("accepted", False)
            current_ver = terms_data.get("current_version") or terms_data.get("version", 1)
            self.latest_version = int(current_ver)

            if accepted:
                self.terms_status_lbl.configure(text=f"✓ Şartlar kabul edilmiş (Sürüm {current_ver})", text_color="#10b981")
                self.accept_terms_btn.configure(state="disabled", text="Onaylandı")
            else:
                self.terms_status_lbl.configure(text=f"⚠️ Kabul Bekliyor (Güncel Sürüm: {current_ver})", text_color="#f59e0b")
                self.accept_terms_btn.configure(state="normal", text=f"✓ Sürüm {current_ver}'i Onayla")

        def on_quota_err(err: str):
            self.is_refreshing = False
            self.refresh_btn.configure(text="🔄 Bilgileri Yenile", state="normal")
            self.credit_lbl.configure(text="Bilgi Alınamadı", text_color="#ef4444")
            self.credit_sub_lbl.configure(text=err)
            self.quota_lbl.configure(text="Bilgi Alınamadı", text_color="#ef4444")
            self.terms_status_lbl.configure(text=f"Durum sorgulanamadı: {err}")

        self.service.fetch_quota_async(
            on_success=lambda d: self.after(0, lambda: on_quota(d)),
            on_error=lambda e: self.after(0, lambda: on_quota_err(e)),
        )

    def _view_terms_text(self) -> None:
        """Kullanım şartları metnini açılır pencerede gösterir."""
        modal = ctk.CTkToplevel(self)
        modal.title("Kullanım Şartları Metni")
        modal.geometry("640x500")
        modal.transient(self)

        txt = ctk.CTkTextbox(modal, font=ctk.CTkFont(size=12))
        txt.pack(fill="both", expand=True, padx=16, pady=16)
        txt.insert("1.0", "Kullanım şartları metni yükleniyor...")

        def on_text(text: str):
            txt.delete("1.0", "end")
            txt.insert("1.0", text)

        def on_err(err: str):
            txt.delete("1.0", "end")
            txt.insert("1.0", f"Metin yüklenemedi:\n{err}")

        self.service.fetch_terms_text_async(
            on_success=lambda t: modal.after(0, lambda: on_text(t)),
            on_error=lambda e: modal.after(0, lambda: on_err(e)),
        )

    def _accept_terms(self) -> None:
        """Şartları onaylar."""
        cevap = messagebox.askyesno(
            "Kullanım Şartları Onayı",
            f"FIRIK LLM API Kullanım Şartları Sürüm {self.latest_version}'i okuduğunuzu ve kabul ettiğinizi onaylıyor musunuz?",
        )
        if not cevap:
            return

        def on_ok(_):
            messagebox.showinfo("Başarılı", "Kullanım şartları başarıyla onaylandı.")
            self.refresh_all()

        def on_err(err: str):
            messagebox.showerror("Onay Hatası", f"Şartlar onaylanamadı:\n{err}")

        self.service.accept_terms_async(
            version=self.latest_version,
            on_success=lambda r: self.after(0, lambda: on_ok(r)),
            on_error=lambda e: self.after(0, lambda: on_err(e)),
        )
