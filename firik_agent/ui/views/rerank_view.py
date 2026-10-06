"""firik masaüstü uygulaması - Yeniden Sıralama (Rerank) Görünümü.

Arama sorgusu ile belge listesini semantik alaka düzeyine göre
sıralayan FIRIK rerank modelini kullanır.
Tüm arayüz Türkçedir.
"""
from __future__ import annotations

from typing import Any, Dict, List
from tkinter import messagebox

import customtkinter as ctk

from firik_agent.ui.service import FirikService


DEFAULT_RERANK_MODEL = "qwen3-reranker-8b"


class RerankView(ctk.CTkFrame):
    """Doküman Yeniden Sıralama Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.is_processing = False

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. BAŞLIK VE AÇIKLAMA
        header_frame = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 8))

        t_lbl = ctk.CTkLabel(
            header_frame,
            text="Doküman ve Metin Yeniden Sıralama (Rerank)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        d_lbl = ctk.CTkLabel(
            header_frame,
            text="Girdiğiniz arama sorgusuna en uygun metinleri veya arama sonuçlarını semantik benzerlik puanına göre sıralayın.",
            font=ctk.CTkFont(size=12),
            text_color=("gray30", "#94a3b8"),
        )
        d_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        # 2. MODEL VE AKSİYON ÇUBUĞU
        controls_frame = ctk.CTkFrame(self, fg_color=("gray95", "#161f2e"), corner_radius=8)
        controls_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        m_lbl = ctk.CTkLabel(controls_frame, text="Rerank Modeli:", font=ctk.CTkFont(size=12, weight="bold"))
        m_lbl.pack(side="left", padx=(12, 4), pady=8)

        self.model_combo = ctk.CTkComboBox(
            controls_frame,
            values=[DEFAULT_RERANK_MODEL],
            width=180,
        )
        self.model_combo.set(DEFAULT_RERANK_MODEL)
        self.model_combo.pack(side="left", padx=4, pady=8)

        self.start_btn = ctk.CTkButton(
            controls_frame,
            text="⚡ Sıralamayı Hesapla",
            width=170,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._start_rerank,
        )
        self.start_btn.pack(side="left", padx=14, pady=8)

        self.status_lbl = ctk.CTkLabel(
            controls_frame,
            text="Sorgu ve belgeleri girin.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
        )
        self.status_lbl.pack(side="right", padx=16, pady=8)

        # 3. GİRDİ PANELİ (SOL) VE SONUÇ PANELİ (SAĞ)
        # Sol Panel: Arama Sorgusu & Belgeler
        left_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        left_card.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=(0, 12))
        left_card.grid_columnconfigure(0, weight=1)
        left_card.grid_rowconfigure(3, weight=1)

        q_lbl = ctk.CTkLabel(left_card, text="Arama Sorgusu:", font=ctk.CTkFont(size=12, weight="bold"))
        q_lbl.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))

        self.query_entry = ctk.CTkEntry(
            left_card,
            placeholder_text="Örnek: Türkiye'nin başkenti neresidir?",
            height=32,
            font=ctk.CTkFont(size=12),
        )
        self.query_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        docs_lbl = ctk.CTkLabel(
            left_card,
            text="Aday Belgeler (Her satıra bir belge girin):",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        docs_lbl.grid(row=2, column=0, sticky="w", padx=12, pady=(4, 2))

        self.docs_textbox = ctk.CTkTextbox(
            left_card,
            font=ctk.CTkFont(size=12),
            corner_radius=8,
            border_width=1,
        )
        self.docs_textbox.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        # Örnek başlangıç verisi
        self.docs_textbox.insert(
            "1.0",
            "Ankara, Türkiye Cumhuriyeti'nin başkenti ve en kalabalık ikinci ilidir.\n"
            "İstanbul, Türkiye'nin Marmara Bölgesi'nde yer alan en büyük şehridir.\n"
            "İzmir, Türkiye'nin batısında Ege Denizi kıyısında bulunan bir liman kentidir.\n"
            "Paris, Fransa'nın başkenti ve en büyük şehridir.",
        )

        # Sağ Panel: Sıralanmış Sonuçlar
        right_card = ctk.CTkFrame(self, fg_color=("gray90", "#1e293b"), corner_radius=10)
        right_card.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=(0, 12))
        right_card.grid_columnconfigure(0, weight=1)
        right_card.grid_rowconfigure(1, weight=1)

        res_title = ctk.CTkLabel(right_card, text="Sıralama Sonuçları", font=ctk.CTkFont(size=13, weight="bold"))
        res_title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.results_scroll = ctk.CTkScrollableFrame(right_card, fg_color="transparent")
        self.results_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.results_scroll.grid_columnconfigure(0, weight=1)

        self._show_placeholder_results()

    def _show_placeholder_results(self) -> None:
        for w in self.results_scroll.winfo_children():
            w.destroy()
        placeholder = ctk.CTkLabel(
            self.results_scroll,
            text="Sonuçlar burada alaka düzeyine göre sıralanmış olarak görünecektir.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        placeholder.pack(pady=40)

    def _start_rerank(self) -> None:
        if self.is_processing:
            return

        query = self.query_entry.get().strip()
        raw_docs = self.docs_textbox.get("1.0", "end-1c").strip()

        if not query:
            messagebox.showwarning("Eksik Sorgu", "Lütfen bir arama sorgusu giriniz.")
            return

        documents = [line.strip() for line in raw_docs.splitlines() if line.strip()]
        if not documents:
            messagebox.showwarning("Eksik Belge", "Lütfen en az bir belge metni giriniz.")
            return

        model = self.model_combo.get().strip() or DEFAULT_RERANK_MODEL
        self.is_processing = True
        self.start_btn.configure(state="disabled", text="Hesaplanıyor...")
        self.status_lbl.configure(text="Belgeler puanlanıyor...", text_color="#38bdf8")

        def on_success(results: List[Dict[str, Any]]):
            self.is_processing = False
            self.start_btn.configure(state="normal", text="⚡ Sıralamayı Hesapla")
            self.status_lbl.configure(text=f"{len(results)} belge başarıyla sıralandı.", text_color="#10b981")
            self._render_results(results)

        def on_error(err_msg: str):
            self.is_processing = False
            self.start_btn.configure(state="normal", text="⚡ Sıralamayı Hesapla")
            self.status_lbl.configure(text="Hata oluştu!", text_color="#ef4444")
            messagebox.showerror("Sıralama Hatası", err_msg)

        self.service.rerank_async(
            model=model,
            query=query,
            documents=documents,
            on_success=lambda r: self.after(0, lambda: on_success(r)),
            on_error=lambda e: self.after(0, lambda: on_error(e)),
        )

    def _render_results(self, results: List[Dict[str, Any]]) -> None:
        for w in self.results_scroll.winfo_children():
            w.destroy()

        if not results:
            self._show_placeholder_results()
            return

        for rank, item in enumerate(results, start=1):
            score = float(item.get("score", 0.0))
            score_percent = score * 100 if score <= 1.0 else score
            score_color = "#10b981" if score_percent >= 70 else ("#f59e0b" if score_percent >= 40 else "#ef4444")

            card = ctk.CTkFrame(self.results_scroll, fg_color=("gray95", "#161f2e"), corner_radius=8)
            card.pack(fill="x", padx=4, pady=4)

            # Başlık satırı
            top_row = ctk.CTkFrame(card, fg_color="transparent")
            top_row.pack(fill="x", padx=10, pady=(6, 2))

            rank_lbl = ctk.CTkLabel(
                top_row,
                text=f"#{rank} Sıra",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#38bdf8",
            )
            rank_lbl.pack(side="left")

            score_lbl = ctk.CTkLabel(
                top_row,
                text=f"Alaka Puanı: %{score_percent:.1f} ({score:.4f})",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=score_color,
            )
            score_lbl.pack(side="right")

            # Belge Metni
            text_lbl = ctk.CTkLabel(
                card,
                text=item.get("text", ""),
                font=ctk.CTkFont(size=12),
                wraplength=460,
                justify="left",
                anchor="w",
            )
            text_lbl.pack(fill="x", padx=10, pady=(2, 8))
