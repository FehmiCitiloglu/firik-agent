"""Native TkDND integration without replacing the CustomTkinter root."""
from __future__ import annotations
import tkinter as tk


def register_file_drop(widget, on_files):
    """Return native availability; file selection remains usable on load failure."""
    try:
        from tkinterdnd2 import DND_FILES, COPY, TkinterDnD
        TkinterDnD.require(widget.winfo_toplevel())
        widget.drop_target_register(DND_FILES)
        def drop(event):
            files = widget.tk.splitlist(event.data)
            on_files(files[:50])
            return COPY
        widget.dnd_bind("<<Drop>>", drop)
        return True
    except (ImportError, RuntimeError, tk.TclError, OSError):
        return False
