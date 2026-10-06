"""Mesaj kartları; yalnız değişen yanıtı seyrek günceller, geçmişi sayfalar."""
from __future__ import annotations

import customtkinter as ctk
from firik_agent.ui.components.markdown_message import MarkdownMessage


class CodingInput(ctk.CTkTextbox):
    """Çok satırlı girişin önceki Entry erişim sözleşmesini korur."""

    def get(self, index1="1.0", index2="end-1c"):
        return super().get("1.0" if index1 == 0 else index1, index2)

    def insert(self, index, text, tags=None):
        return super().insert("1.0" if index == 0 else index, text, tags)

    def delete(self, index1, index2=None):
        return super().delete("1.0" if index1 == 0 else index1, index2)


class CodingConversation(ctk.CTkFrame):
    def __init__(self, master, *, on_edit=None, on_context=None, on_file=None, font_size=13):
        super().__init__(master, fg_color="transparent")
        self.on_edit, self.on_context, self.on_file = on_edit, on_context, on_file
        self.font_size = font_size
        self.messages = []
        self.transcript = ""
        self.cards = {}
        self.page_size = 40
        self.query = ""
        self._job = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.older = ctk.CTkButton(self, text="Önceki mesajları göster", height=24,
                                  command=self._load_more)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew")

    def _load_more(self):
        self.page_size += 40
        self._render(rebuild=True)

    def set_messages(self, messages, transcript="", streaming=False):
        self.messages = messages
        self.transcript = transcript
        if self._job is None:
            self._job = self.after(100 if streaming else 1, self._render)

    def search(self, query):
        self.query = query.casefold().strip()
        self._render(rebuild=True)

    def _render(self, rebuild=False):
        self._job = None
        if not self.winfo_exists():
            return
        follow_tail = self.scroll._parent_canvas.yview()[1] >= 0.98
        if rebuild:
            for card, _ in self.cards.values():
                card.destroy()
            self.cards.clear()
        candidates = [(i, m) for i, m in enumerate(self.messages)
                      if not self.query or self.query in m.get("content", "").casefold()]
        selected = candidates[-self.page_size:]
        if len(candidates) > self.page_size:
            self.older.grid(row=0, column=0, sticky="ew")
        else:
            self.older.grid_remove()
        keys = {i for i, _ in selected}
        for key in list(self.cards):
            if key not in keys:
                self.cards.pop(key)[0].destroy()
        for i, message in selected:
            content = message.get("content", "")
            if i in self.cards:
                body = self.cards[i][1]
                if getattr(body, "coding_original_content", None) != content:
                    self._set_body(body, content, streaming=True)
                continue
            card = ctk.CTkFrame(self.scroll, corner_radius=8,
                              fg_color=("gray95", "#192638"))
            card.pack(fill="x", padx=2, pady=5)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=4)
            role = message.get("role", "assistant")
            ctk.CTkLabel(head, text={"user": "Siz", "assistant": "Firik", "system": "Bilgi"}.get(role, "Bilgi"),
                         font=ctk.CTkFont(size=self.font_size, weight="bold")).pack(side="left")
            if role == "user" and self.on_edit:
                ctk.CTkButton(head, text="Düzenle / Yeniden dene", width=135, height=22,
                              command=lambda m=message: self.on_edit(m)).pack(side="right")
            body = MarkdownMessage(card, text="")
            body.pack(fill="x", padx=10, pady=(0, 6))
            body.coding_on_file = self.on_file
            body.coding_on_context = self.on_context
            body.coding_font_size = self.font_size
            self._set_body(body, content)
            self.cards[i] = card, body
        if follow_tail:
            self.see("end")

    @staticmethod
    def _set_body(body, content, streaming=False):
        body.coding_original_content = content
        # Bound the visible tail. The complete message stays in persistence/export.
        if len(content) > 24000:
            content = "… Uzun yanıtın son bölümü gösteriliyor; tam metin dışa aktarılabilir.\n\n" + content[-24000:]
        body.set_text(content, streaming=streaming)

    def insert(self, index, text, *args):
        self.transcript += text

    def delete(self, *args):
        self.transcript = ""

    def get(self, *args):
        return self.transcript

    def see(self, *args):
        self.scroll._parent_canvas.yview_moveto(1.0)

    def destroy(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        super().destroy()
