"""Native reusable voice settings, explicit tests and ordinary settings section."""
from dataclasses import replace
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.voice_settings import VoiceSettingsFrame
from firik_agent.ui.views.settings_view import SettingsView
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.providers import evren_capabilities
from test_ui_chat import pump


class InputTest:
    def __init__(self):
        self.cancelled = False
        self.done = threading.Event()

    def cancel(self):
        self.cancelled = True


class Service:
    base_url = "https://example.invalid/v1"
    default_model = "existing-chat-model"
    timeout = 180

    def __init__(self):
        self.voice_settings = VoiceSettings()
        self.saved = []
        self.tests = []
        self.devices = []
        self.discoveries = []
        self.listeners = []
        self.config = {"unrelated": {"kept": True}}

    def get_key(self):
        return None

    def fetch_models_async(self, on_success):
        on_success([self.default_model])

    def update_voice_settings(self, settings):
        self.voice_settings = settings
        self.config["voice"] = settings.to_dict()
        self.saved.append(settings)
        for callback in tuple(self.listeners):
            callback()

    def add_voice_settings_listener(self, callback):
        self.listeners.append(callback)

    def remove_voice_settings_listener(self, callback):
        self.listeners = [item for item in self.listeners if item != callback]

    def audio_devices_async(self, success, error):
        self.devices.append((success, error))

    def start_voice_input_test(self, on_level, on_done, on_error, settings=None):
        handle = InputTest()
        self.tests.append((handle, on_level, on_done, on_error, settings))
        return handle

    def discover_voice_capabilities_async(self, success, error):
        self.discoveries.append((success, error))

    def voice_diagnostics(self):
        return {"microphone_opened": False, "network_requests": 0, "packages": {"sounddevice": "available"}}


@pytest.fixture
def voice_form():
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("490x620")
    service = Service()
    frame = VoiceSettingsFrame(root, service)
    frame.pack(fill="both", expand=True, padx=8, pady=8)
    pump(root, .04)
    yield root, service, frame
    root.destroy()


def test_settings_section_is_lazy_and_reuses_form_without_starting_audio(voice_form):
    root, service, standalone = voice_form
    standalone.destroy()
    service.devices.clear()
    view = SettingsView(root, service)
    view.pack(fill="both", expand=True)
    pump(root)
    assert view.voice_settings_frame is None and not service.tests and not service.devices
    form = view.open_voice_section()
    pump(root, .03)
    assert isinstance(form, VoiceSettingsFrame) and service.devices
    assert view.open_voice_section() is form and not service.tests
    form.vars["enabled"].set(True)
    assert form.save()
    assert service.voice_settings.enabled and service.default_model == "existing-chat-model"
    assert service.config["unrelated"] == {"kept": True}


def test_save_validates_without_persisting_bad_numbers_and_keeps_original_chat_model(voice_form):
    _, service, form = voice_form
    form.entries["output_volume"].delete(0, "end")
    form.entries["output_volume"].insert(0, "nan")
    assert not form.save() and not service.saved
    assert "kaydedilemedi" in form.status.cget("text")
    form.entries["output_volume"].delete(0, "end")
    form.entries["output_volume"].insert(0, ".4")
    form.vars["enabled"].set(True)
    form.vars["completion_chime"].set(True)
    form.vars["vad_enabled"].set(True)
    form._vad_preference_changed()
    assert form.save()
    assert service.voice_settings.output_volume == .4
    assert service.voice_settings.completion_chime and service.voice_settings.vad_enabled
    assert service.voice_settings.interaction_mode == "conversation"
    assert service.default_model == "existing-chat-model" and not service.tests


def test_saved_device_id_is_stable_when_backend_indexes_change(voice_form):
    _, service, form = voice_form
    mic = SimpleNamespace(id="pa:stable-mic", name="Microphone", host_api="CoreAudio", index=0)
    speaker = SimpleNamespace(id="pa:stable-out", name="Speaker", host_api="CoreAudio", index=2)
    service.devices[-1][0]({"input": [mic], "output": [speaker]})
    label = next(label for label in form.menus["input_device"].cget("values") if "Microphone" in label)
    form.menus["input_device"].set(label)
    form._select_device("input", label)
    assert form.save() and service.voice_settings.input_device == "pa:stable-mic"
    mic.index = 5
    form.refresh_devices()
    service.devices[-1][0]({"input": [mic], "output": [speaker]})
    assert "(5)" in form.menus["input_device"].get()
    assert form.save() and service.voice_settings.input_device == "pa:stable-mic"


def test_explicit_local_level_test_uses_unsaved_input_settings_and_destroy_cancels_late_callbacks(voice_form):
    root, service, form = voice_form
    assert not service.tests
    form.entries["input_gain"].delete(0, "end")
    form.entries["input_gain"].insert(0, "1.8")
    form.test_button.invoke()
    handle, level, done, error, settings = service.tests[-1]
    assert settings.input_gain == 1.8 and not service.saved
    level(.65)
    assert form.level_bar.get() == .65
    form.test_button.invoke()
    assert handle.cancelled and form.level_bar.get() == 0
    level(.9)
    assert form.level_bar.get() == 0
    form.test_button.invoke()
    handle, level, done, error, _ = service.tests[-1]
    form.destroy()
    assert handle.cancelled
    level(.8)
    done()
    error("late error")
    pump(root)


def test_save_cancels_explicit_test_and_errors_do_not_expose_configuration_paths(voice_form):
    _, service, form = voice_form
    form.test_button.invoke()
    handle = service.tests[-1][0]
    def fail(settings):
        raise OSError("/Users/private-person/secret-file password=secret")
    service.update_voice_settings = fail
    assert not form.save() and handle.cancelled
    assert "yazma iznini" in form.status.cget("text")
    assert "private-person" not in form.status.cget("text") and "password" not in form.status.cget("text")


def test_level_test_completion_preserves_actionable_error_in_service_callback_order(voice_form):
    _, service, form = voice_form
    form.test_button.invoke()
    _, _, done, error, _ = service.tests[-1]
    message = "Mikrofon izni gerekli. Sistem gizlilik ayarlarını kontrol edin."
    error(message)
    done()
    assert form.test_status.cget("text") == message
    assert form.test_button.cget("text") == "Mikrofon düzeyini sına"
    assert form._input_test is None


def test_provider_capability_controls_explain_unsupported_features_without_fake_voices(voice_form):
    _, service, form = voice_form
    form.menus["tts_provider"].set("system")
    form._provider_changed()
    assert form.menus["tts_voice"].cget("state") == "disabled"
    assert "Sistem dili" in form.menus["tts_voice"].get() and "desteklenmiyor" in form.tts_hint.cget("text")
    assert form.entries["speech_rate"].cget("state") == "normal"
    form.menus["asr_provider"].set("local")
    form._provider_changed()
    assert form.menus["asr_model"].cget("state") == "disabled"
    assert "kullanılamıyor" in form.asr_hint.cget("text")
    form.menus["asr_provider"].set("evren")
    form.menus["tts_provider"].set("evren")
    form._provider_changed()
    form.capability_button.invoke()
    assert len(service.discoveries) == 1 and not service.tests
    capabilities = {kind: evren_capabilities(kind, {"data": []}, authenticated=True) for kind in ("asr", "tts")}
    service.discoveries[-1][0](capabilities)
    assert "hesapta uygun ses modeli" in form.capability_status.cget("text")
    assert form.menus["asr_model"].cget("state") == "disabled"
    assert form.menus["tts_model"].cget("state") == "disabled"
    assert form.save() and service.voice_settings.tts_voice == "default"


def test_privacy_controls_remain_disabled_and_diagnostics_are_offline_readonly(voice_form):
    _, service, form = voice_form
    service.update_voice_settings(replace(service.voice_settings, barge_in=True))
    assert not form.vars["barge_in"].get()
    disabled = []
    def visit(widget):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkCheckBox) and child.cget("state") == "disabled":
                disabled.append(child.cget("text"))
            visit(child)
    visit(form)
    assert {"Uyandırma sözcüğü", "Ham sesi kalıcı olarak sakla", "Ses çalarken araya gir"} <= set(disabled)
    form.diagnostics_button.invoke()
    assert form.diagnostics_output.cget("state") == "disabled"
    assert '"microphone_opened": false' in form.diagnostics_output.get("1.0", "end")
    assert not service.tests and not service.discoveries
    form.copy_diagnostics()
    assert "network_requests" in form.clipboard_get()


def test_embedded_form_reflects_modal_save_without_restarting_and_removes_listener(voice_form):
    root, service, form = voice_form
    dialog = ctk.CTkToplevel(root)
    modal = VoiceSettingsFrame(dialog, service, show_save=False)
    modal.pack(fill="both", expand=True)
    modal.vars["read_replies"].set(True)
    modal.entries["output_volume"].delete(0, "end")
    modal.entries["output_volume"].insert(0, ".3")
    assert modal.save()
    assert form.vars["read_replies"].get() and form.entries["output_volume"].get() == "0.3"
    assert not service.tests
    dialog.destroy()
    assert service.listeners == [form._service_settings_changed]


def test_external_save_preserves_dirty_draft_and_merges_only_user_changes(voice_form):
    _, service, form = voice_form
    form.entries["input_gain"].delete(0, "end")
    form.entries["input_gain"].insert(0, "1.7")
    service.update_voice_settings(replace(service.voice_settings, read_replies=True))
    assert form.entries["input_gain"].get() == "1.7" and not form.vars["read_replies"].get()
    assert "başka yerde güncellendi" in form.status.cget("text")
    assert form.reload_button.winfo_manager() == "grid"
    assert form.save()
    assert service.voice_settings.input_gain == 1.7 and service.voice_settings.read_replies
    assert form.vars["read_replies"].get()


def test_explicit_reload_discards_preserved_draft_only_after_user_action(voice_form):
    _, service, form = voice_form
    form.entries["input_gain"].delete(0, "end")
    form.entries["input_gain"].insert(0, "1.7")
    service.update_voice_settings(replace(service.voice_settings, input_gain=1.2))
    assert form.entries["input_gain"].get() == "1.7"
    form.reload_button.invoke()
    assert form.entries["input_gain"].get() == "1.2"
    assert not form.reload_button.winfo_manager()


def test_leaving_visible_level_test_stops_capture_and_late_delivery_is_ignored(voice_form):
    root, service, form = voice_form
    form.test_button.invoke()
    handle, level, _, _, _ = service.tests[-1]
    form.pack_forget()
    pump(root, .02)
    assert handle.cancelled
    level(.9)
    assert form.level_bar.get() == 0


def test_settings_canvas_controls_support_keyboard_focus_activation_and_menu_selection(voice_form):
    root, service, form = voice_form
    root.focus_force()
    canvas = form.enabled_control._canvas
    canvas.focus_set()
    pump(root, .02)
    assert root.focus_get() == canvas and int(canvas.cget("takefocus")) == 1
    assert int(canvas.cget("highlightthickness")) == 2
    canvas.event_generate("<space>")
    pump(root)
    assert form.vars["enabled"].get()
    canvas.event_generate("<Tab>")
    pump(root)
    assert root.focus_get() is not None and root.focus_get() != canvas
    menu = form.menus["interaction_mode"]
    menu._canvas.focus_set()
    menu._canvas.event_generate("<Down>")
    pump(root)
    assert menu.get() == "Sesli sohbet" and form.vars["vad_enabled"].get()
    form.save_button._canvas.focus_set()
    form.save_button._canvas.event_generate("<Return>")
    pump(root)
    assert service.voice_settings.enabled and service.voice_settings.interaction_mode == "conversation"
    assert int(form.menus["tts_voice"]._canvas.cget("takefocus")) == 0


def test_runtime_theme_switch_updates_scroll_rows_labels_and_native_focus_backgrounds(voice_form):
    root, _, form = voice_form
    for mode, color in (("Light", "#ffffff"), ("Dark", "#161f2e"), ("Light", "#ffffff")):
        ctk.set_appearance_mode(mode)
        pump(root, .04)
        expected = root.winfo_rgb(color)
        for label in form._labels:
            assert root.winfo_rgb(label._label.cget("background")) == expected
        for control in (form.enabled_control, form.menus["language"], form.menus["input_device"]):
            assert root.winfo_rgb(control._canvas.cget("bg")) == expected
            assert root.winfo_rgb(control._canvas.cget("highlightbackground")) == expected
    ctk.set_appearance_mode("Dark")


def test_keyboard_focus_reveals_offscreen_settings_entry_and_microphone_test_button(voice_form):
    root, service, form = voice_form
    root.focus_force()
    canvas = form.pages["Giriş"]._parent_canvas
    entry = form.entries["shortcut"]
    canvas.yview_moveto(0.)
    pump(root, .03)
    assert entry.winfo_rooty() > canvas.winfo_rooty() + canvas.winfo_height()
    entry._entry.focus_set()
    pump(root, .03)
    assert root.focus_get() == entry._entry
    assert entry.winfo_rooty() >= canvas.winfo_rooty()
    assert entry.winfo_rooty() + entry.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
    form.enabled_control._canvas.focus_set()
    canvas.yview_moveto(1.)
    pump(root, .03)
    button = form.test_button
    assert button.winfo_rooty() + button.winfo_height() < canvas.winfo_rooty()
    button._canvas.focus_set()
    pump(root, .03)
    assert root.focus_get() == button._canvas
    assert button.winfo_rooty() >= canvas.winfo_rooty()
    assert button.winfo_rooty() + button.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
    assert not service.tests


@pytest.mark.parametrize("theme", ["Light", "Dark"])
def test_voice_form_fits_narrow_native_window_and_theme_without_horizontal_clipping(voice_form, theme):
    root, _, form = voice_form
    ctk.set_appearance_mode(theme)
    root.geometry("490x620+80+80")
    pump(root, .08)
    assert form.winfo_width() < 500
    for name in ("Giriş", "Yanıt", "Gizlilik ve yardım"):
        form.tabs.set(name)
        # CTkTabview retires the previous tab after its 100 ms transition.
        pump(root, .13)
        page = form.pages[name]
        for child in page.winfo_children():
            assert child.winfo_reqwidth() <= form.winfo_width(), (name, child.winfo_reqwidth(), form.winfo_width())
    assert form.status.winfo_width() <= form.winfo_width()
    form.tabs.set("Giriş")
    pump(root, .18)
    page = form.pages["Giriş"]
    canvas = page._parent_canvas
    first = form.menus["interaction_mode"]
    assert page.winfo_ismapped() and first.winfo_ismapped()
    assert canvas.yview()[0] == 0.
    assert canvas.bbox("all")[3] > canvas.winfo_height()
    assert first.winfo_rooty() >= canvas.winfo_rooty()
    assert first.winfo_rooty() + first.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
    if os.environ.get("FIRIK_CAPTURE_VOICE_UI"):
        from voice_ui_capture import capture_window
        destination = Path(os.environ["FIRIK_CAPTURE_VOICE_UI"])
        destination.mkdir(parents=True, exist_ok=True)
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        pump(root, .18)
        capture = destination / f"settings-{theme.lower()}.png"
        capture_window(root, capture)
        if capture.exists():
            from PIL import Image
            with Image.open(capture) as image:
                scale = image.width / root.winfo_width()
                titlebar = image.height - root.winfo_height() * scale
                x = round((first.winfo_rootx() - root.winfo_rootx() + first.winfo_width() * .45) * scale)
                y = round(titlebar + (first.winfo_rooty() - root.winfo_rooty() + first.winfo_height() * .5) * scale)
                actual = image.getpixel((x, y))[:3]
            background = tuple(value // 257 for value in root.winfo_rgb(first._apply_appearance_mode(first.cget("bg_color"))))
            assert sum(abs(value - base) for value, base in zip(actual, background)) > 60 and actual[2] > actual[0] + 20, {
                "menu_pixel": actual, "background": background, "sample": (x, y),
                "yview": canvas.yview(), "bbox": canvas.bbox("all"), "mapped": first.winfo_ismapped()}
        root.attributes("-topmost", False)
    ctk.set_appearance_mode("Dark")
