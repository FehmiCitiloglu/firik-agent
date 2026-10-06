"""Independent conversation pages with a searchable, always available history."""
from __future__ import annotations

import datetime
from typing import Optional

import customtkinter as ctk

from firik_agent.ui.views.chat_view import ChatView


class ChatWorkspace(ctk.CTkFrame):
    def __init__(self, master, service, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.pages: dict[str, ChatView] = {}
        self.current: Optional[ChatView] = None
        self._unread: set[str] = set()
        self._busy: set[str] = set()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=210, corner_radius=10, fg_color=("gray95", "#111827"))
        sidebar.grid(row=0, column=0, sticky="ns", padx=(12, 0), pady=12)
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(sidebar, text="Sohbetler", anchor="w", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        ctk.CTkButton(sidebar, text="+ Yeni sohbet", height=32, command=self.new_chat).grid(
            row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.search = ctk.CTkEntry(sidebar, placeholder_text="Sohbetlerde ara", height=30)
        self.search.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.search.bind("<KeyRelease>", lambda _: self.refresh_history())
        self.history_list = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", width=180)
        self.history_list.grid(row=3, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=1, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        records = self.service.list_chat_history()
        self.open_chat(records[0]["session_id"] if records else None)

    def new_chat(self):
        if (self.current and not self.current.messages and not self.current.attached_image_path
                and not self.current.input_textbox.get("1.0", "end-1c").strip()):
            self.current.input_textbox.focus_set()
            return
        self.open_chat(None)

    def open_chat(self, session_id: Optional[str]):
        if self.current:
            self.current.grid_remove()
        page = self.pages.get(session_id) if session_id else None
        if page is None:
            page = ChatView(self.body, self.service, session_id=session_id,
                            navigate=lambda sid: self.open_chat(sid) if sid else self.new_chat(),
                            on_history_change=self.refresh_history)
            self.pages[page.session.session_id] = page
        self.current = page
        self._unread.discard(page.session.session_id)
        page.grid(row=0, column=0, sticky="nsew")
        self.refresh_history()

    def refresh_history(self):
        if not hasattr(self, "history_list"):
            return
        # Deleting the current chat creates a fresh session in that same page.
        for sid, page in list(self.pages.items()):
            if sid != page.session.session_id:
                self.pages.pop(sid)
                self.pages[page.session.session_id] = page
                self._unread.discard(sid)
                self._busy.discard(sid)
        now_busy = {sid for sid, page in self.pages.items() if page.is_streaming}
        self._unread.update(self._busy - now_busy)
        self._busy = now_busy
        current_id = self.current.session.session_id if self.current else None
        self._unread.discard(current_id)
        records = {row["session_id"]: row for row in self.service.list_chat_history()}
        # Include unsent chats as well; their input and scroll position stay local.
        for sid, page in self.pages.items():
            if sid not in records:
                records[sid] = {"session_id": sid, "title": "Yeni sohbet", "updated_at": page.session.created_at}
        for child in self.history_list.winfo_children():
            child.destroy()
        query = self.search.get().casefold().strip()
        for sid, record in sorted(records.items(), key=lambda pair: pair[1]["updated_at"], reverse=True):
            if query and query not in record["title"].casefold():
                continue
            status = (f"● {self.pages[sid].activity_stage}" if sid in self._busy else
                      "● Yeni yanıt" if sid in self._unread else
                      datetime.datetime.fromtimestamp(record["updated_at"]).strftime("%d.%m · %H:%M"))
            title = record["title"][:23] + ("…" if len(record["title"]) > 23 else "")
            button = ctk.CTkButton(self.history_list, text=f"{title}\n{status}", anchor="w", height=54,
                                   font=ctk.CTkFont(size=12),
                                   fg_color=("#dbeafe", "#1e3a5f") if sid == current_id else "transparent",
                                   hover_color=("#e2e8f0", "#1e293b"), text_color=("#0f172a", "#e2e8f0"),
                                   command=lambda sid=sid: self.open_chat(sid))
            button.pack(fill="x", pady=3)
