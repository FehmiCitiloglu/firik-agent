"""Native sandbox selection survives persistence and reaches coding dispatch."""
import json

import customtkinter as ctk

from test_ui_coding_workspace import workspace as workspace, settle


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_sandbox_preferences_persist_and_reach_next_coding_run(workspace):
    app, view, project, streams = workspace
    view._coding_permissions()
    settle(app)
    dialog = next(widget for widget in view.winfo_children()
                  if isinstance(widget, ctk.CTkToplevel) and widget.title() == "İzinler, MCP ve Beceriler")
    widgets = list(descendants(dialog))
    combos = [widget for widget in widgets if isinstance(widget, ctk.CTkComboBox)]
    next(widget for widget in combos if widget.cget("values") == ["host", "docker"]).set("docker")
    next(widget for widget in combos if widget.cget("values") == ["restricted", "development", "custom"]).set("development")
    next(widget for widget in combos if widget.cget("values") == ["reject", "clean_head"]).set("clean_head")
    network = next(widget for widget in widgets if isinstance(widget, ctk.CTkCheckBox)
                   and widget.cget("text").startswith("Docker ağına"))
    network.select()
    image = next(widget for widget in widgets if isinstance(widget, ctk.CTkEntry)
                 and widget.get() == "python:3.12-slim")
    image.delete(0, "end")
    image.insert(0, "python:3.12-alpine")
    next(widget for widget in widgets if isinstance(widget, ctk.CTkButton)
         and widget.cget("text") == "Kaydet").invoke()
    settle(app)
    assert not dialog.winfo_exists()
    saved = view.project_service.db.get_settings(project.id).coding_preferences
    execution = saved["execution_config"]
    assert execution["backend"] == "docker"
    assert execution["profile"] == "development"
    assert execution["image"] == "python:3.12-alpine"
    assert execution["network"] is True and execution["network_approved"] is True
    assert saved["dirty_start"] == "clean_head"

    view._coding_execution_status()
    settle(app)
    status = next(widget for widget in view.winfo_children()
                  if isinstance(widget, ctk.CTkToplevel) and widget.title() == "Yürütme / Sandbox Durumu")
    box = next(widget for widget in descendants(status) if isinstance(widget, ctk.CTkTextbox))
    assert json.loads(box.get("1.0", "end"))["configured"]["backend"] == "docker"
    status.destroy()

    view.coding_input.insert(0, "main.py açıkla")
    view.coding_send_btn.invoke()
    assert json.loads(json.dumps(streams[-1]["coding_options"]["execution_config"])) == execution
    assert streams[-1]["coding_options"]["dirty_start"] == "clean_head"
    streams[-1]["on_done"]("Kaynak incelendi")
    settle(app)
