"""Keep keyboard-focused controls visible inside CustomTkinter scroll cards."""
from __future__ import annotations

import tkinter as tk


def reveal_in_scroll_frame(widget):
    try:
        parent = widget.master
        while parent is not None:
            canvas = getattr(parent, "_parent_canvas", None)
            if canvas is not None:
                bounds = canvas.bbox("all")
                if not bounds or bounds[3] <= bounds[1]:
                    return
                top, bottom = canvas.winfo_rooty(), canvas.winfo_rooty() + canvas.winfo_height()
                start, end = widget.winfo_rooty(), widget.winfo_rooty() + widget.winfo_height()
                delta = start - top - 6 if start < top else end - bottom + 6 if end > bottom else 0
                if delta:
                    canvas.yview_moveto(max(0., min(1., canvas.yview()[0] + delta / (bounds[3] - bounds[1]))))
                return
            parent = getattr(parent, "master", None)
    except tk.TclError:
        pass
