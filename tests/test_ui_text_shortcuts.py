"""Native key events must retain the platform's text editing behavior."""
import tkinter as tk

import customtkinter as ctk
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.text_shortcuts import install_text_shortcuts


@pytest.fixture
def root():
    _ensure_tk_environment()
    window = ctk.CTk()
    window.geometry("460x260")
    yield window
    window.destroy()


def control(parent, kind):
    if kind == "text":
        outer = ctk.CTkTextbox(parent, width=420, height=150, undo=True)
        native = outer._textbox
    elif kind == "combo":
        outer = ctk.CTkComboBox(parent, values=["alpha beta"], width=300)
        native = outer._entry
    else:
        outer = ctk.CTkEntry(parent, width=300)
        native = outer._entry
    outer.pack(fill="x", padx=10, pady=10)
    parent.update()
    return outer, native


def reset(widget, value, cursor="end"):
    if isinstance(widget, tk.Text):
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.tag_remove("sel", "1.0", "end")
        widget.mark_set("insert", "end-1c" if cursor == "end" else cursor)
    else:
        widget.delete(0, "end")
        widget.insert(0, value)
        widget.selection_clear()
        widget.icursor(cursor)


def value(widget):
    return widget.get("1.0", "end-1c") if isinstance(widget, tk.Text) else widget.get()


def select(widget, start, end):
    if isinstance(widget, tk.Text):
        widget.tag_add("sel", start, end)
        widget.mark_set("insert", end)
    else:
        widget.selection_range(start, end)
        widget.icursor(end)


def key(root, widget, sequence):
    widget.focus_force()
    root.update()
    widget.event_generate(sequence)
    root.update()


@pytest.mark.parametrize("system", ["aqua", "win32", "x11"])
@pytest.mark.parametrize("kind", ["entry", "text"])
def test_word_delete_selection_boundaries_and_disabled(root, system, kind):
    install_text_shortcuts(root, windowing_system=system)
    _, widget = control(root, kind)
    modifier = "Option" if system == "aqua" else "Control"
    start, beta, beta_end = ("1.0", "1.6", "1.10") if kind == "text" else (0, 6, 10)

    reset(widget, "alpha beta")
    key(root, widget, f"<{modifier}-BackSpace>")
    assert value(widget) == "alpha "
    reset(widget, "alpha beta", beta)
    key(root, widget, f"<{modifier}-Delete>")
    assert value(widget) == "alpha "
    reset(widget, "İstanbul dünya")
    key(root, widget, f"<{modifier}-BackSpace>")
    assert value(widget) == "İstanbul "

    for sequence in (f"<{modifier}-BackSpace>", f"<{modifier}-Delete>"):
        reset(widget, "alpha beta gamma")
        select(widget, beta, beta_end)
        key(root, widget, sequence)
        assert value(widget) == "alpha  gamma"

    reset(widget, "alpha beta", start)
    key(root, widget, f"<{modifier}-BackSpace>")
    assert value(widget) == "alpha beta"
    reset(widget, "alpha beta")
    key(root, widget, f"<{modifier}-Delete>")
    assert value(widget) == "alpha beta"
    widget.configure(state="disabled")
    key(root, widget, f"<{modifier}-BackSpace>")
    assert value(widget) == "alpha beta"


@pytest.mark.parametrize("kind", ["entry", "text"])
def test_command_deletes_to_line_edges_and_honors_selection(root, kind):
    install_text_shortcuts(root, windowing_system="aqua")
    _, widget = control(root, kind)
    content = "first line\nalpha beta\nlast line" if kind == "text" else "alpha beta"
    cursor = "2.6" if kind == "text" else 6
    reset(widget, content, cursor)
    key(root, widget, "<Command-BackSpace>")
    assert value(widget) == ("first line\nbeta\nlast line" if kind == "text" else "beta")
    reset(widget, content, cursor)
    key(root, widget, "<Command-Delete>")
    assert value(widget) == ("first line\nalpha \nlast line" if kind == "text" else "alpha ")

    for sequence in ("<Command-BackSpace>", "<Command-Delete>"):
        reset(widget, "alpha beta gamma")
        select(widget, "1.6" if kind == "text" else 6, "1.10" if kind == "text" else 10)
        key(root, widget, sequence)
        assert value(widget) == "alpha  gamma"


def test_command_backspace_uses_wrapped_display_line(root):
    install_text_shortcuts(root, windowing_system="aqua")
    _, widget = control(root, "text")
    widget.configure(wrap="word", font=("Courier", 16))
    content = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
    reset(widget, content)
    root.update()
    line_start = widget.index("insert display linestart")
    assert widget.compare(line_start, ">", "1.0"), "The fixture must actually wrap"
    prefix = widget.get("1.0", line_start)
    key(root, widget, "<Command-BackSpace>")
    assert value(widget) == prefix


def test_editable_and_readonly_combo_boxes_share_entry_bindings(root):
    install_text_shortcuts(root, windowing_system="aqua")
    combo, widget = control(root, "combo")
    reset(widget, "alpha beta")
    key(root, widget, "<Option-BackSpace>")
    assert combo.get() == "alpha "
    combo.set("alpha beta")
    combo.configure(state="readonly")
    key(root, widget, "<Command-BackSpace>")
    assert combo.get() == "alpha beta"


def test_later_dialog_inputs_inherit_shortcuts_and_install_is_idempotent(root):
    install_text_shortcuts(root, windowing_system="aqua")
    bindings = {name: root.bind_class(name, "<Option-BackSpace>") for name in ("Entry", "Text")}
    install_text_shortcuts(root, windowing_system="aqua")
    assert bindings == {name: root.bind_class(name, "<Option-BackSpace>") for name in bindings}
    dialog = ctk.CTkToplevel(root)
    try:
        _, widget = control(dialog, "entry")
        reset(widget, "alpha beta gamma")
        key(root, widget, "<Option-BackSpace>")
        assert value(widget) == "alpha beta "
    finally:
        dialog.destroy()


def test_tcl_created_inputs_without_python_wrappers_use_shortcuts(root):
    install_text_shortcuts(root, windowing_system="aqua")
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    for kind in ("entry", "text"):
        path = f".native_{kind}"
        root.tk.call(kind, path)
        root.tk.call("pack", path)
        root.tk.call(path, "insert", "1.0" if kind == "text" else 0, "alpha beta")
        if kind == "text":
            root.tk.call(path, "mark", "set", "insert", "end-1c")
        else:
            root.tk.call(path, "icursor", "end")
        root.tk.call("focus", "-force", path)
        root.update()
        root.tk.call("event", "generate", path, "<Option-BackSpace>")
        root.update()
        arguments = ("1.0", "end-1c") if kind == "text" else ()
        result = root.tk.call(path, "get", *arguments)
        assert result == "alpha "
    assert errors == []


@pytest.mark.parametrize("sequence", ["<Option-BackSpace>", "<Command-BackSpace>"])
def test_text_deletion_is_one_undo_step(root, sequence):
    install_text_shortcuts(root, windowing_system="aqua")
    _, widget = control(root, "text")
    reset(widget, "alpha beta gamma")
    widget.edit_reset()
    key(root, widget, sequence)
    result = value(widget)
    assert result != "alpha beta gamma"
    widget.edit_undo()
    assert value(widget) == "alpha beta gamma"
    widget.edit_redo()
    assert value(widget) == result


@pytest.mark.parametrize("kind", ["entry", "text"])
def test_existing_navigation_selection_clipboard_and_plain_delete_survive(root, kind):
    install_text_shortcuts(root)
    _, widget = control(root, kind)
    system = root.tk.call("tk", "windowingsystem")
    aqua = system == "aqua"
    word_modifier = "Option" if aqua else "Control"
    app_modifier = "Command" if aqua else "Control"
    select_all = "<Control-Key-slash>" if system == "x11" else f"<{app_modifier}-Key-a>"
    root.clipboard_clear()
    root.update()
    reset(widget, "alpha beta")
    key(root, widget, f"<{word_modifier}-Left>")
    assert widget.index("insert") == ("1.6" if kind == "text" else 6)
    key(root, widget, f"<{word_modifier}-Shift-Right>")
    if kind == "text":
        assert widget.get("sel.first", "sel.last") == "beta"
    else:
        assert widget.get()[widget.index("sel.first"):widget.index("sel.last")] == "beta"
    key(root, widget, select_all)
    key(root, widget, f"<{app_modifier}-Key-c>")
    assert root.clipboard_get().rstrip("\n") == "alpha beta"
    key(root, widget, f"<{app_modifier}-Key-x>")
    assert value(widget) == ""
    key(root, widget, f"<{app_modifier}-Key-v>")
    assert value(widget).rstrip("\n") == "alpha beta"
    reset(widget, "alpha beta")
    key(root, widget, "<BackSpace>")
    assert value(widget) == "alpha bet"
