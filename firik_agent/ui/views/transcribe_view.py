"""firik masaüstü uygulaması - Ses Çözümleme (Transkripsiyon) Görünümü.

Ses dosyalarını (MP3, WAV, M4A, OGG vb.) FIRIK ses modelleriyle metne
veya zaman damgalı SRT altyazıya dönüştürür.
Tüm arayüz Türkçedir.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from tkinter import filedialog, messagebox

import customtkinter as ctk

from firik_agent.ui.service import FirikService


class TranscribeView(ctk.CTkFrame):
    """Ses Çözümleme ve Altyazı Üretimi Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.selected_audio_path: Optional[str] = None
        self.is_processing = False

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. BAŞLIK PANELİ
        header_frame = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))

        t_lbl = ctk.CTkLabel(
            header_frame,
            text="Ses Çözümleme ve Altyazı Çıkarma (ASR)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        d_lbl = ctk.CTkLabel(
            header_frame,
            text="Toplantı kayıtları, sesli notlar veya videolarınızı Türkçe veya yabancı dillerde metne ve SRT altyazılara dönüştürün.",
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
        )
        d_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        # 2. AYARLAR VE DOSYA SEÇİM KARTI
        settings_card = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=10)
        settings_card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        # 1. Satır: Dosya Seçimi
        file_row = ctk.CTkFrame(settings_card, fg_color="transparent")
        file_row.pack(fill="x", padx=14, pady=(10, 6))

        file_btn = ctk.CTkButton(
            file_row,
            text="🎵 Ses Dosyası Seç",
            width=160,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._select_audio,
        )
        file_btn.pack(side="left")

        self.file_info_lbl = ctk.CTkLabel(
            file_row,
            text="Henüz ses dosyası seçilmedi (MP3, WAV, M4A, OGG, FLAC)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "#94a3b8"),
        )
        self.file_info_lbl.pack(side="left", padx=14)

        # 2. Satır: Model, Dil ve Biçim Parametreleri
        params_row = ctk.CTkFrame(settings_card, fg_color="transparent")
        params_row.pack(fill="x", padx=14, pady=(4, 10))

        # Model
        m_lbl = ctk.CTkLabel(params_row, text="Model:", font=ctk.CTkFont(size=11, weight="bold"))
        m_lbl.pack(side="left", padx=(0, 4))
        self.model_combo = ctk.CTkComboBox(params_row, values=["qwen3-asr-1.7b"], width=150)
        self.model_combo.set("qwen3-asr-1.7b")
        self.model_combo.pack(side="left", padx=(0, 14))

        # Dil
        lang_lbl = ctk.CTkLabel(params_row, text="Dil:", font=ctk.CTkFont(size=11, weight="bold"))
        lang_lbl.pack(side="left", padx=(0, 4))
        self.lang_combo = ctk.CTkComboBox(
            params_row,
            values=["tr (Türkçe)", "en (İngilizce)", "auto (Otomatik)"],
            width=140,
        )
        self.lang_combo.set("tr (Türkçe)")
        self.lang_combo.pack(side="left", padx=(0, 14))

        # Biçim
        fmt_lbl = ctk.CTkLabel(params_row, text="Çıktı Biçimi:", font=ctk.CTkFont(size=11, weight="bold"))
        fmt_lbl.pack(side="left", padx=(0, 4))
        self.fmt_combo = ctk.CTkComboBox(
            params_row,
            values=["Düz Metin (json)", "Altyazı Dosyası (srt)", "Ayrıntılı (verbose_json)", "Konuşmacılı (diarized_json)"],
            width=190,
        )
        self.fmt_combo.set("Düz Metin (json)")
        self.fmt_combo.pack(side="left", padx=(0, 14))

        # Başlat Butonu
        self.start_btn = ctk.CTkButton(
            params_row,
            text="▶ Çözümlemeyi Başlat",
            width=160,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            state="disabled",
            command=self._start_transcribe,
        )
        self.start_btn.pack(side="right")

        # 3. ÇIKTI ALANI
        output_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        output_card.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))
        output_card.grid_rowconfigure(1, weight=1)
        output_card.grid_columnconfigure(0, weight=1)

        out_header = ctk.CTkFrame(output_card, fg_color="transparent")
        out_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))

        out_title = ctk.CTkLabel(out_header, text="Çözümlenen Metin / Sonuç", font=ctk.CTkFont(size=13, weight="bold"))
        out_title.pack(side="left")

        # Sağ aksiyonlar: Kaydet & Kopyala
        save_btn = ctk.CTkButton(
            out_header,
            text="Farklı Kaydet",
            width=95,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._save_output,
        )
        save_btn.pack(side="right", padx=(4, 0))

        copy_btn = ctk.CTkButton(
            out_header,
            text="Panoya Kopyala",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._copy_output,
        )
        copy_btn.pack(side="right", padx=4)

        self.status_lbl = ctk.CTkLabel(
            out_header,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#10b981",
        )
        self.status_lbl.pack(side="right", padx=10)

        self.output_textbox = ctk.CTkTextbox(
            output_card,
            font=ctk.CTkFont(family="monospace", size=12),
            corner_radius=8,
            border_width=1,
        )
        self.output_textbox.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))

    def _select_audio(self) -> None:
        dosya = filedialog.askopenfilename(
            title="Ses Dosyası Seçin",
            filetypes=[
                ("Ses Dosyaları", "*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma"),
                ("Tüm Dosyalar", "*.*"),
            ],
        )
        if not dosya:
            return

        self.selected_audio_path = dosya
        path = Path(dosya)
        boyut_mb = os.path.getsize(dosya) / (1024 * 1024)
        self.file_info_lbl.configure(
            text=f"📁 {path.name} ({boyut_mb:.2f} MB)",
            text_color="#38bdf8",
        )
        self.start_btn.configure(state="normal")

    def _start_transcribe(self) -> None:
        if not self.selected_audio_path or self.is_processing:
            return

        model = self.model_combo.get().strip() or "qwen3-asr-1.7b"
        raw_lang = self.lang_combo.get().split()[0].strip()
        lang = "tr" if raw_lang == "tr" else ("en" if raw_lang == "en" else "auto")

        raw_fmt = self.fmt_combo.get()
        if "srt" in raw_fmt:
            fmt = "srt"
        elif "verbose_json" in raw_fmt:
            fmt = "verbose_json"
        elif "diarized_json" in raw_fmt:
            fmt = "diarized_json"
        else:
            fmt = "json"

        self.is_processing = True
        self.start_btn.configure(state="disabled", text="İşleniyor...")
        self.status_lbl.configure(text="Ses yükleniyor ve çözümleniyor...", text_color="#38bdf8")
        self.output_textbox.delete("1.0", "end")
        self.output_textbox.insert("1.0", "Lütfen bekleyin, ses dosyanız çözümleniyor. Dosya boyutuna bağlı olarak bu işlem biraz zaman alabilir...")

        def on_success(result: str):
            self.output_textbox.delete("1.0", "end")
            self.output_textbox.insert("1.0", result)
            self.status_lbl.configure(text="Çözümleme başarıyla tamamlandı!", text_color="#10b981")
            self.is_processing = False
            self.start_btn.configure(state="normal", text="▶ Çözümlemeyi Başlat")

        def on_error(err_msg: str):
            self.output_textbox.delete("1.0", "end")
            self.output_textbox.insert("1.0", f"Hata:\n{err_msg}")
            self.status_lbl.configure(text="Hata oluştu!", text_color="#ef4444")
            self.is_processing = False
            self.start_btn.configure(state="normal", text="▶ Çözümlemeyi Başlat")

        self.service.transcribe_async(
            audio_path=self.selected_audio_path,
            model=model,
            language=lang,
            response_format=fmt,
            prompt="",
            on_success=lambda res: self.after(0, lambda: on_success(res)),
            on_error=lambda err: self.after(0, lambda: on_error(err)),
        )

    def _copy_output(self) -> None:
        text = self.output_textbox.get("1.0", "end-1c").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_lbl.configure(text="Metin panoya kopyalandı.", text_color="#10b981")

    def _save_output(self) -> None:
        text = self.output_textbox.get("1.0", "end-1c").strip()
        if not text:
            return

        def_ext = ".srt" if "-->" in text else ".txt"
        dosya = filedialog.asksaveasfilename(
            title="Sonucu Kaydet",
            defaultextension=def_ext,
            filetypes=[
                ("Metin Belgesi", "*.txt"),
                ("Altyazı Dosyası", "*.srt"),
                ("JSON Dosyası", "*.json"),
            ],
        )
        if dosya:
            try:
                Path(dosya).write_text(text, encoding="utf-8")
                messagebox.showinfo("Başarılı", f"Kayıt başarıyla tamamlandı:\n{dosya}")
            except Exception as e:
                messagebox.showerror("Hata", f"Dosya kaydedilemedi: {e}")
