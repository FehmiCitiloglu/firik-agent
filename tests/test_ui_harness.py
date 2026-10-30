"""Native desktop rendering; selected explicitly in a GUI-capable environment."""
import json

import customtkinter as ctk

from test_ui_coding_workspace import workspace as workspace, settle


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_harness_view_is_passive_and_saves_next_task(workspace, monkeypatch):
    app, view, project, streams = workspace
    from firik_agent.ui import coding_harness
    monkeypatch.setattr(coding_harness, "harness_snapshot", lambda *args: {"backends": [{"name": "native", "available": True}]})
    dialog = view._coding_harness_settings()
    settle(app, .25)
    assert dialog.title() == "Harness Ayarları ve Benchmark" and streams == []
    widgets = list(descendants(dialog))
    boxes = [widget for widget in widgets if isinstance(widget, ctk.CTkTextbox)]
    assert any('"backends"' in box.get("1.0", "end") for box in boxes)
    save = next(widget for widget in widgets if isinstance(widget, ctk.CTkButton) and widget.cget("text") == "Sonraki Görev İçin Kaydet")
    save.invoke()
    saved = view.project_service.db.get_settings(project.id).coding_preferences["harness"]
    assert saved["default_backend"] == "native" and not saved["optimization"]["automatic_backend_switching"]
    assert streams == []
    dialog.destroy()
