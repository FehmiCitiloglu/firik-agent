"""Native behavior of the browser's horizontal tab strip."""
from __future__ import annotations

import customtkinter as ctk
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.browser_tabs import BrowserTabStrip


def settle(root):
    for _ in range(3):
        root.update_idletasks()
        root.update()


@pytest.fixture
def strip():
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("620x140")
    events = []
    view = BrowserTabStrip(root, on_select=lambda tab: events.append(("select", tab)),
        on_close=lambda tab: events.append(("close", tab)), on_new=lambda: events.append(("new",)))
    view.pack(fill="x", padx=8, pady=8)
    settle(root)
    try:
        yield root, view, events
    finally:
        root.destroy()


def tabs(count):
    return [{"tab_id": f"tab-{index}", "title": "A repeated page title", "url": f"https://example.org/{index}"}
            for index in range(count)]


def test_tab_callbacks_use_ids_even_for_duplicate_titles_and_inactive_close(strip):
    root, view, events = strip
    view.set_tabs(tabs(3), "tab-0")
    settle(root)
    view._tab_widgets["tab-1"]["title"].invoke()
    view._tab_widgets["tab-2"]["close"].invoke()
    view.new_button.invoke()
    assert events == [("select", "tab-1"), ("close", "tab-2"), ("new",)]
    assert view._active_tab == "tab-0"  # Selection waits for the browser's observation.
    frames = [entry["frame"] for entry in view._tab_widgets.values()]
    assert len({frame.winfo_y() for frame in frames}) == 1
    assert [frame.winfo_x() for frame in frames] == sorted(frame.winfo_x() for frame in frames)


def test_repeated_observations_reuse_controls_and_update_active_style(strip):
    root, view, events = strip
    data = tabs(2)
    view.set_tabs(data, "tab-0")
    settle(root)
    first, second = [view._tab_widgets[key]["frame"] for key in ("tab-0", "tab-1")]
    active_color, inactive_color = first.cget("fg_color"), second.cget("fg_color")
    assert active_color != inactive_color
    view.set_tabs(data, "tab-1")
    settle(root)
    assert view._tab_widgets["tab-0"]["frame"] is first
    assert view._tab_widgets["tab-1"]["frame"] is second
    assert first.cget("fg_color") == inactive_color
    assert second.cget("fg_color") == active_color
    assert not events
    view.set_tabs(data, "tab-1")
    settle(root)
    assert view._tab_widgets["tab-1"]["frame"] is second
    assert not events


def test_narrow_strip_scrolls_and_reveals_newly_active_tab_without_hiding_plus(strip):
    root, view, _ = strip
    root.geometry("320x140")
    data = tabs(8)
    view.set_tabs(data, "tab-0")
    settle(root)
    assert view.left_button.winfo_ismapped() and view.right_button.winfo_ismapped()
    assert view._viewport.xview()[0] == 0
    assert view._viewport.xview()[1] < 1
    assert view.new_button.winfo_ismapped()
    assert view.new_button.winfo_rootx() + view.new_button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    view.set_tabs(data, "tab-7")
    settle(root)
    first, last = view._viewport.xview()
    assert first > .5 and last > .99
    view.left_button.invoke()
    settle(root)
    scrolled = view._viewport.xview()[0]
    assert scrolled < first
    view.set_tabs(data, "tab-7")
    settle(root)
    assert abs(view._viewport.xview()[0] - scrolled) < .001
    view.set_tabs(data, "tab-0")
    settle(root)
    assert view._viewport.xview()[0] == 0


def test_removed_tabs_are_destroyed_and_empty_state_is_passive(strip):
    root, view, events = strip
    view.set_tabs(tabs(2), "tab-0")
    settle(root)
    removed = view._tab_widgets["tab-1"]["frame"]
    view.set_tabs([{"id": "tab-0", "url": "https://example.org"}], "tab-0")
    settle(root)
    assert not removed.winfo_exists()
    assert view._tab_widgets["tab-0"]["title"].cget("text") == "example.org"
    view.set_tabs([], "")
    settle(root)
    assert view._empty.winfo_ismapped()
    assert not view._tab_widgets
    assert not view.left_button.winfo_ismapped() and not view.right_button.winfo_ismapped()
    assert not events
    view.new_button.invoke()
    assert events == [("new",)]


def test_resize_and_active_switch_in_same_event_loop_reveal_final_tab(strip):
    root, view, _ = strip
    root.geometry("880x140")
    data = tabs(4)
    view.set_tabs(data, "tab-1")
    settle(root)
    assert not view.right_button.winfo_ismapped()

    # Neither the root nor canvas has applied the smaller width when the
    # browser's next observation selects the last tab.
    root.geometry("320x140")
    view.set_tabs(data, "tab-3")
    settle(root)

    active = view._tab_widgets["tab-3"]["frame"]
    viewport_left = view._viewport.winfo_rootx()
    viewport_right = viewport_left + view._viewport.winfo_width()
    assert active.winfo_rootx() >= viewport_left
    assert active.winfo_rootx() + active.winfo_width() <= viewport_right + 1
    assert view._viewport.xview()[1] >= .999


@pytest.mark.parametrize("mode", ["Light", "Dark"])
def test_theme_and_long_titles_fit_bounded_tabs(strip, mode):
    root, view, _ = strip
    previous = ctk.get_appearance_mode()
    try:
        ctk.set_appearance_mode(mode)
        view.set_tabs([{"tab_id": "long", "title": "Very long browser page title " * 12}], "long")
        settle(root)
        title = view._tab_widgets["long"]["title"].cget("text")
        assert title.endswith("…") and len(title) < 40
        assert view._viewport.cget("background") == view._viewport_color()
        assert view._tab_widgets["long"]["close"].winfo_ismapped()
    finally:
        ctk.set_appearance_mode(previous)
