"""Shared trusted desktop approval for an exact, immutable operation."""
from __future__ import annotations

import json
import customtkinter as ctk


class OperationApprovalDialog(ctk.CTkToplevel):
    def __init__(self, master, *, tool_name, arguments, reason="", on_decision):
        super().__init__(master)
        self._on_decision = on_decision
        self._decided = False
        self.title("İşlem Onayı")
        self.geometry("780x580")
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", lambda: self.decide(False))
        ctk.CTkLabel(self, text=f"{tool_name}\n{reason}", wraplength=730).pack(fill="x", padx=12, pady=10)
        box = self.operation = ctk.CTkTextbox(self, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
        box.pack(fill="both", expand=True, padx=12, pady=6)
        box.insert("end", json.dumps(arguments, ensure_ascii=False, indent=2))
        box.configure(state="disabled")
        ctk.CTkLabel(self, text="Yalnızca bu işlem için onay verilir. Sesli yanıtlar onay sayılmaz.", wraplength=730).pack(padx=12)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=10)
        self.allow_button = ctk.CTkButton(footer, text="Bu İşleme İzin Ver", command=lambda: self.decide(True))
        self.allow_button.pack(side="right", padx=4)
        self.deny_button = ctk.CTkButton(footer, text="Reddet", command=lambda: self.decide(False))
        self.deny_button.pack(side="right", padx=4)

    def decide(self, allowed):
        if not self._decided:
            self._decided = True
            self._on_decision(allowed is True)
        self.destroy()

    def destroy(self):
        if not self._decided:
            self._decided = True
            self._on_decision(False)
        super().destroy()
