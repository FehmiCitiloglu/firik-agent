"""Explicit chat model choices survive new conversations and app restarts."""
from pathlib import Path
import sys

import customtkinter as ctk
import pytest

from firik_agent.config import load_config
from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.service import FirikService
from firik_agent.ui.views.chat_workspace import ChatWorkspace
from test_ui_chat import pump


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    # Exercise the packaged app's actual user config path without real MCPs.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("firik_agent.ui.service.add_desktop_defaults", lambda config: None)
    return FirikService


def test_model_choice_is_saved_before_any_message_and_survives_restart(service_factory):
    service = service_factory()
    old = service.get_or_create_session()
    service.set_default_model("  mimo  ")
    assert service.get_or_create_session().model == "mimo"
    assert old.model == "glm-5.3"
    assert not service.list_chat_history()
    assert load_config(service.config_path)["providers"]["firik"]["default_model"] == "mimo"
    restarted = service_factory()
    assert restarted.default_model == "mimo"
    assert restarted.get_or_create_session().model == "mimo"
    assert restarted.base_url == service.base_url
    assert restarted.timeout == service.timeout


def test_failed_or_empty_model_choice_keeps_previous_default(service_factory, monkeypatch):
    service = service_factory()
    with pytest.raises(ValueError):
        service.set_default_model(" ")

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr("firik_agent.ui.service.save_config", fail)
    with pytest.raises(OSError):
        service.set_default_model("mimo")
    assert service.default_model == "glm-5.3"
    assert service.config["providers"]["firik"]["default_model"] == "glm-5.3"


def test_workspace_model_selection_persists_without_sending(service_factory, monkeypatch):
    _ensure_tk_environment()
    root = ctk.CTk()
    service = service_factory()
    monkeypatch.setattr(service, "fetch_models_async", lambda on_success: on_success(["glm-5.3", "mimo"]))
    view = ChatWorkspace(root, service)
    view.pack(fill="both", expand=True)
    try:
        pump(root)
        first = view.current
        # Use the combo's actual selection command, before the first message.
        first.model_combo.set("mimo")
        first.model_combo.cget("command")("mimo")
        first.input_textbox.insert("1.0", "Taslak")
        view.new_chat()
        pump(root)
        assert view.current is not first
        assert view.current.model_combo.get() == "mimo"
        assert view.current.session.model == "mimo"
        assert service_factory().default_model == "mimo"

        # Opening an older GLM conversation must not reset the remembered choice.
        old = service.get_or_create_session()
        old.model = "glm-5.3"
        service.save_chat_transcript(old, [{"role": "user", "content": "Eski sohbet"}])
        view.open_chat(old.session_id)
        assert view.current.model_combo.get() == "glm-5.3"
        view.new_chat()
        assert view.current.model_combo.get() == "mimo"
        assert service_factory().default_model == "mimo"
    finally:
        root.destroy()
