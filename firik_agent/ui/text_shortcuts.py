"""Fill gaps in Tk's platform text-editing bindings for every desktop input."""
from __future__ import annotations

import tkinter as tk


def install_text_shortcuts(root: tk.Misc, *, windowing_system: str | None = None) -> None:
    """Install once per interpreter, including inputs created later in dialogs.

    CustomTkinter entries/comboboxes and textboxes use the native Entry/Text
    classes. Keep their existing navigation, selection, clipboard and IME
    bindings; only supply the missing modified deletion keys.
    """
    root = root._root()
    if getattr(root, "_firik_text_shortcuts_installed", False):
        return
    system = windowing_system or root.tk.call("tk", "windowingsystem")
    modifier = "Option" if system == "aqua" else "Control"
    bindings = {
        f"<{modifier}-BackSpace>": "previous_word",
        f"<{modifier}-Delete>": "next_word",
    }
    if system == "aqua":
        bindings.update({
            "<Command-BackSpace>": "line_start",
            "<Command-Delete>": "line_end",
        })
    for widget_class in ("Entry", "Text"):
        for sequence, action in bindings.items():
            root.bind_class(widget_class, sequence,
                            lambda event, operation=action: _delete(root, str(event.widget), operation))
    root._firik_text_shortcuts_installed = True


def _delete(root: tk.Misc, path: str, action: str) -> str:
    """Delete a selection or native word/visible-line range as one undo step."""
    # Native Tk dialogs can create controls without a Python widget object.
    # Use the Tcl path so their class bindings work as well.
    def widget(*args):
        return root.tk.call(path, *args)

    if str(widget("cget", "-state")) != "normal":
        return "break"
    is_text = root.tk.call("winfo", "class", path) == "Text"
    if is_text:
        selection = root.tk.splitlist(widget("tag", "ranges", "sel"))
        if selection:
            first, last = selection[:2]
        elif action == "previous_word":
            # TextPrevWord was added in newer Tk releases.
            if root.tk.call("info", "commands", "::tk::TextPrevWord"):
                first = root.tk.call("::tk::TextPrevWord", path, "insert")
            else:
                first = root.tk.call("::tk::TextPrevPos", path, "insert",
                                     "tcl_startOfPreviousWord")
            last = "insert"
        elif action == "next_word":
            first = "insert"
            last = root.tk.call("::tk::TextNextWord", path, "insert")
        elif action == "line_start":
            first, last = "insert display linestart", "insert"
        else:
            first, last = "insert", "insert display lineend"
        # Never include Text's mandatory trailing newline in an edit.
        if widget("compare", last, ">", "end-1c"):
            last = "end-1c"
        if not widget("compare", first, "<", last):
            return "break"
        separate = root.tk.getboolean(widget("cget", "-autoseparators"))
        if separate:
            widget("edit", "separator")
        widget("delete", first, last)
        if separate:
            widget("edit", "separator")
        widget("see", "insert")
    else:
        if widget("selection", "present"):
            first, last = "sel.first", "sel.last"
        elif action == "previous_word":
            first = root.tk.call("::tk::EntryPreviousWord", path, "insert")
            last = "insert"
        elif action == "next_word":
            first = "insert"
            last = root.tk.call("::tk::EntryNextWord", path, "insert")
        elif action == "line_start":
            first, last = 0, "insert"
        else:
            first, last = "insert", "end"
        widget("delete", first, last)
        root.tk.call("::tk::EntrySeeInsert", path)
    return "break"
