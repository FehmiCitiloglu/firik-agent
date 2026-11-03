"""Native settings coverage for independent speech choices and GET discovery."""
import customtkinter as ctk

from test_ui_voice import voice_chat  # noqa: F401 -- shared isolated Tk fixture


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_provider_settings_preserve_chat_model_and_save_preferred_vad(voice_chat):
    _, workspace, service = voice_chat
    chat_model = service.default_model
    panel = workspace.current.voice_panel
    panel.open_settings()
    dialog = panel._dialog
    menus = [item for item in descendants(dialog) if isinstance(item, ctk.CTkOptionMenu)]
    provider_menu = next(item for item in menus if item.cget("values") == ["evren", "system"])
    provider_menu.set("evren")
    vad = next(item for item in descendants(dialog) if isinstance(item, ctk.CTkCheckBox)
               and "yerel konuşma algılamayı" in item.cget("text"))
    vad.toggle()
    save = next(item for item in dialog.winfo_children() if isinstance(item, ctk.CTkButton))
    save.invoke()
    assert service.voice_settings.tts_provider == "evren"
    assert service.voice_settings.asr_model == "qwen3-asr-1.7b"
    assert service.voice_settings.tts_model == "voxcpm2"
    assert service.voice_settings.vad_enabled
    assert service.voice_settings.interaction_mode == "conversation"
    assert service.default_model == chat_model
    assert not service.controllers


def test_capability_discovery_never_opens_capture(voice_chat):
    from firik_agent.voice.providers import evren_capabilities

    _, workspace, service = voice_chat
    calls = []

    def discover(success, error):
        calls.append(True)
        success({kind: evren_capabilities(kind,
            {"data": [{"id": "qwen3-asr-1.7b", "task": "audio_transcription"},
                      {"id": "voxcpm2", "task": "tts"}]}, authenticated=True)
            for kind in ("asr", "tts")})

    service.discover_voice_capabilities_async = discover
    panel = workspace.current.voice_panel
    panel.open_settings()
    button = next(item for item in descendants(panel._dialog) if isinstance(item, ctk.CTkButton)
                  and item.cget("text") == "Evren model erişimini kontrol et")
    button.invoke()
    assert calls and not service.controllers
    assert any(isinstance(item, ctk.CTkLabel) and "ASR: qwen3-asr-1.7b" in item.cget("text")
               for item in descendants(panel._dialog))
