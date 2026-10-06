"""firik masaüstü uygulaması - Görsel ve Belge OCR Görünümü.

Görsel yükleme, önizleme, FIRIK OCR modeli ile metin çıkarımı ve
sonuçları panoya kopyalama / dosyaya kaydetme özelliklerini sunar.
Tüm arayüz Türkçedir.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from firik_agent.ui.service import FirikService


class OCRView(ctk.CTkFrame):
    """Görsel Metin Çıkarımı (OCR) Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.selected_image_path: Optional[str] = None
        self.is_processing = False

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. BAŞLIK VE AÇIKLAMA KARTI
        header_frame = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 8))

        t_lbl = ctk.CTkLabel(
            header_frame,
            text="Görsel ve Belge Metin Çıkarımı (OCR)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        d_lbl = ctk.CTkLabel(
            header_frame,
            text="Fatura, makbuz, taranmış belge veya fotoğraflardaki yazıları yapay zekâ ile yüksek doğrulukla metne dönüştürün.",
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
        )
        d_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        # 2. KONTROL VE MODEL ÇUBUĞU
        controls_frame = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=8)
        controls_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        # Model Seçimi
        m_lbl = ctk.CTkLabel(controls_frame, text="OCR Modeli:", font=ctk.CTkFont(size=12, weight="bold"))
        m_lbl.pack(side="left", padx=(12, 4), pady=8)

        self.model_combo = ctk.CTkComboBox(
            controls_frame,
            values=["dots-ocr", "glm-5.3"],
            width=160,
        )
        self.model_combo.set("dots-ocr")
        self.model_combo.pack(side="left", padx=4, pady=8)

        # Görsel Seç Butonu
        select_btn = ctk.CTkButton(
            controls_frame,
            text="📁 Görsel Dosyası Seç",
            width=160,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._select_image,
        )
        select_btn.pack(side="left", padx=12, pady=8)

        # OCR Başlat Butonu
        self.start_btn = ctk.CTkButton(
            controls_frame,
            text="⚡ Metni Çıkar (OCR)",
            width=160,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            state="disabled",
            command=self._start_ocr,
        )
        self.start_btn.pack(side="left", padx=4, pady=8)

        # Durum Göstergesi
        self.status_lbl = ctk.CTkLabel(
            controls_frame,
            text="Lütfen bir görsel seçin.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
        )
        self.status_lbl.pack(side="right", padx=16, pady=8)

        # 3. İKİ SÜTUNLU ALAN: SOL ÖNİZLEME, SAĞ ÇIKARILAN METİN
        # Sol Sütun: Görsel Önizleme ve Bilgiler
        left_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        left_card.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=(0, 12))
        left_card.grid_rowconfigure(1, weight=1)
        left_card.grid_columnconfigure(0, weight=1)

        left_title = ctk.CTkLabel(left_card, text="Görsel Önizlemesi", font=ctk.CTkFont(size=13, weight="bold"))
        left_title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.preview_lbl = ctk.CTkLabel(
            left_card,
            text="Henüz bir görsel seçilmedi\n\n(PNG, JPG, JPEG, WEBP)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self.preview_lbl.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)

        self.img_info_lbl = ctk.CTkLabel(
            left_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "#94a3b8"),
        )
        self.img_info_lbl.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 10))

        # Sağ Sütun: Çıkarılan Metin Kutusu ve Eylemler
        right_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        right_card.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=(0, 12))
        right_card.grid_rowconfigure(1, weight=1)
        right_card.grid_columnconfigure(0, weight=1)

        right_top = ctk.CTkFrame(right_card, fg_color="transparent")
        right_top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        right_title = ctk.CTkLabel(right_top, text="Çıkarılan Metin", font=ctk.CTkFont(size=13, weight="bold"))
        right_title.pack(side="left")

        # Kopyala ve Kaydet Butonları
        save_btn = ctk.CTkButton(
            right_top,
            text="Kaydet (.txt)",
            width=90,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._save_text,
        )
        save_btn.pack(side="right", padx=(4, 0))

        copy_btn = ctk.CTkButton(
            right_top,
            text="Panoya Kopyala",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._copy_text,
        )
        copy_btn.pack(side="right", padx=4)

        self.output_textbox = ctk.CTkTextbox(
            right_card,
            font=ctk.CTkFont(family="monospace", size=12),
            corner_radius=8,
            border_width=1,
        )
        self.output_textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 12))

    def _select_image(self) -> None:
        dosya = filedialog.askopenfilename(
            title="OCR için Görsel Seçin",
            filetypes=[
                ("Görsel Dosyaları", "*.png *.jpg *.jpeg *.webp *.bmp *.tiff"),
                ("Tüm Dosyalar", "*.*"),
            ],
        )
        if not dosya:
            return

        self.selected_image_path = dosya
        path = Path(dosya)
        boyut_kb = os.path.getsize(dosya) / 1024

        try:
            pil_img = Image.open(dosya)
            genislik, yukseklik = pil_img.size
            self.img_info_lbl.configure(
                text=f"Dosya: {path.name} | Çözünürlük: {genislik}x{yukseklik} | Boyut: {boyut_kb:.1f} KB"
            )

            # Önizleme için orantılı küçült
            pil_thumb = pil_img.copy()
            pil_thumb.thumbnail((360, 360))
            ctk_thumb = ctk.CTkImage(light_image=pil_thumb, dark_image=pil_thumb, size=pil_thumb.size)
            self.preview_lbl.configure(image=ctk_thumb, text="")

            self.start_btn.configure(state="normal")
            self.status_lbl.configure(text="Görsel hazır. 'Metni Çıkar' butonuna tıklayın.", text_color="#10b981")
        except Exception as e:
            messagebox.showerror("Görsel Hatası", f"Görsel açılamadı: {e}")

    def _start_ocr(self) -> None:
        if not self.selected_image_path or self.is_processing:
            return

        model = self.model_combo.get().strip() or "dots-ocr"
        self.is_processing = True
        self.start_btn.configure(state="disabled", text="İşleniyor...")
        self.status_lbl.configure(text="Görsel sunucuya yükleniyor ve metin çıkarılıyor...", text_color="#38bdf8")
        self.output_textbox.delete("1.0", "end")
        self.output_textbox.insert("1.0", "Lütfen bekleyin, optik karakter tanıma (OCR) işlemi yürütülüyor...")

        def on_success(result_text: str):
            self.output_textbox.delete("1.0", "end")
            self.output_textbox.insert("1.0", result_text)
            self.status_lbl.configure(text="İşlem tamamlandı!", text_color="#10b981")
            self.is_processing = False
            self.start_btn.configure(state="normal", text="⚡ Metni Çıkar (OCR)")

        def on_error(err_msg: str):
            self.output_textbox.delete("1.0", "end")
            self.output_textbox.insert("1.0", f"Hata:\n{err_msg}")
            self.status_lbl.configure(text="Hata oluştu!", text_color="#ef4444")
            self.is_processing = False
            self.start_btn.configure(state="normal", text="⚡ Metni Çıkar (OCR)")

        self.service.ocr_async(
            model=model,
            image_path=self.selected_image_path,
            on_success=lambda res: self.after(0, lambda: on_success(res)),
            on_error=lambda err: self.after(0, lambda: on_error(err)),
        )

    def _copy_text(self) -> None:
        text = self.output_textbox.get("1.0", "end-1c").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_lbl.configure(text="Metin panoya kopyalandı.", text_color="#10b981")

    def _save_text(self) -> None:
        text = self.output_textbox.get("1.0", "end-1c").strip()
        if not text:
            return

        dosya = filedialog.asksaveasfilename(
            title="Metni Kaydet",
            defaultextension=".txt",
            filetypes=[("Metin Belgesi", "*.txt"), ("Markdown Dosyası", "*.md")],
        )
        if dosya:
            try:
                Path(dosya).write_text(text, encoding="utf-8")
                messagebox.showinfo("Başarılı", f"Metin kaydedildi:\n{dosya}")
            except Exception as e:
                messagebox.showerror("Hata", f"Dosya kaydedilemedi: {e}")
