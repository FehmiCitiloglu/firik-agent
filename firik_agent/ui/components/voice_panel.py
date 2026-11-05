"""Session-bound desktop voice controls; all widget work stays on the Tk thread."""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox  # Retained for existing embedded-panel error adapters.

import customtkinter as ctk

from firik_agent.voice.commands import parse_command
from firik_agent.voice.config import VoiceSettings


class VoicePanel(ctk.CTkFrame):
    def __init__(self, master, service, *, on_dictation, actions, on_submit=None,
                 get_session_id=None, is_busy=None, on_submit_request=None,
                 get_project_id=None, get_workspace_id=None, target="conversation",
                 compact=False, on_text_fallback=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service, self.on_dictation, self.actions = service, on_dictation, actions
        self.on_submit = on_submit
        self.on_submit_request = on_submit_request
        self.get_project_id = get_project_id or (lambda: None)
        self.get_workspace_id = get_workspace_id or (lambda: None)
        self.target = target
        self.compact = compact
        self.on_text_fallback = on_text_fallback or (lambda: None)
        self.composer_mic_button = None
        self._voice_surface = None
        self._response_preview_text = ""
        self._last_transcript = ""
        self._last_operation = "idle"
        self._last_state = "idle"
        self._capture_scope = None
        self._voice_request = None
        self._request_router = None
        self.get_session_id = get_session_id or (lambda: None)
        self.is_busy = is_busy or (lambda: False)
        self.controller = None
        self._active, self._closed = True, False
        self._recording_mode = "Bas-konuş"
        self._dialog = None
        self._narrow = None
        self._status_row = 2
        self._review_visible = False
        self._pending = False
        self._submitting = False
        self._holding = False
        self._hold_source = None
        self._conversation = False
        self._paused = False
        self._capture_session = None
        self._review_generation = None
        self._duration_start = None
        self._duration_job = None
        self._speech_paused = False
        self._automatic_speech = False
        self._audio_guard_job = None
        self._state_label = "Ses kapalı" if not self._settings().enabled else "Hazır"
        self._shortcut_bindings = []
        self._bound_shortcut = None
        self._shortcut_release_keys = set()
        self.grid_columnconfigure(3, weight=1)
        self.mic_button = ctk.CTkButton(self, text="Basılı tut", width=104, height=28,
                                       command=self._mic_invoked)
        self.mic_button.bind("<ButtonPress-1>", self._press_mic, add="+")
        self.mic_button.bind("<ButtonRelease-1>", self._release_mic, add="+")
        self.toggle_button = ctk.CTkButton(self, text="Aç / bitir", width=85, height=28,
                                          command=self.toggle_preferred_interaction)
        self.mode = ctk.CTkOptionMenu(self, values=["Bas-konuş", "Sesli komut"], width=106, height=28)
        from firik_agent.ui.components.voice_settings import make_voice_controls_accessible
        self._mode_focus_controls = make_voice_controls_accessible(self.mode, include_self=True)
        self.settings_button = ctk.CTkButton(self, text="Ses ayarları", width=85, height=28,
                                            command=self.open_settings)
        self.session_button = ctk.CTkButton(self, text="Sesli sohbet", width=104, height=28,
                                           command=self.start_conversation)
        self.pause_button = ctk.CTkButton(self, text="Duraklat", width=85, height=28,
                                         command=self.toggle_pause)
        self.exit_button = ctk.CTkButton(self, text="Sohbetten çık", width=106, height=28,
                                        command=self.exit_conversation)
        self.stop_button = ctk.CTkButton(self, text="Dinlemeyi durdur", width=126, height=28,
                                        command=self.stop_listening)
        self.speech_button = ctk.CTkButton(self, text="Konuşmayı durdur", width=126, height=28,
                                          command=self.stop_speaking)
        self.speech_pause_button = ctk.CTkButton(self, text="Sesi duraklat", width=104, height=28,
                                               command=self.toggle_speech_pause)
        self.replay_button = ctk.CTkButton(self, text="Yeniden dinle", width=104, height=28,
                                         command=self.replay_speech)
        self.agent_button = ctk.CTkButton(self, text="Ajanı durdur", width=106, height=28,
                                         command=self.stop_agent)
        self.status = ctk.CTkLabel(self, text=self._state_label, anchor="w",
                                   font=ctk.CTkFont(size=11), wraplength=360)
        self.input_level = ctk.CTkProgressBar(self, height=5)
        self.input_level.set(0)
        self.review = ctk.CTkFrame(self, fg_color="transparent")
        self.review.grid_columnconfigure(0, weight=1)
        self.command_text = ctk.CTkEntry(self.review, placeholder_text="Transkripti inceleyip düzenleyin")
        self.command_text.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        self.command_text.bind("<KeyRelease>", lambda event: self._sync_surface_draft(), add="+")
        self.review_actions = ctk.CTkFrame(self.review, fg_color="transparent")
        self.review_actions.grid(row=1, column=0, columnspan=4, sticky="w")
        self.apply_button = ctk.CTkButton(self.review_actions, text="Gönder", width=64, command=self.apply_transcript)
        self.copy_button = ctk.CTkButton(self.review_actions, text="Kopyala", width=64, command=self.copy_transcript)
        self.retry_button = ctk.CTkButton(self.review_actions, text="Yeniden kaydet", width=100, command=self.retry_transcript)
        self.discard_button = ctk.CTkButton(self.review_actions, text="Vazgeç", width=64, command=self.dismiss_command)
        for column, button in enumerate((self.apply_button, self.copy_button, self.retry_button, self.discard_button)):
            button.grid(row=0, column=column, padx=(0, 3), pady=(0, 3))
        self.error_actions = ctk.CTkFrame(self, fg_color="transparent")
        self.error_retry = ctk.CTkButton(self.error_actions, text="Yeniden dene", width=100, height=28, command=self.retry_voice_error)
        self.text_fallback_button = ctk.CTkButton(self.error_actions, text="Metinle devam et", width=120, height=28, command=self.use_text_input)
        self.error_retry.grid(row=0, column=0, padx=(0, 4))
        self.text_fallback_button.grid(row=0, column=1)
        for button in (self.mic_button, self.toggle_button, self.settings_button, self.session_button,
                       self.pause_button, self.exit_button, self.stop_button, self.speech_button,
                       self.agent_button, self.speech_pause_button, self.replay_button,
                       self.apply_button, self.copy_button, self.retry_button,
                       self.discard_button, self.error_retry, self.text_fallback_button):
            self._make_accessible(button)
        if not hasattr(service, "create_voice_controller"):
            for button in (self.mic_button, self.toggle_button, self.session_button):
                button.configure(state="disabled")
        self.bind("<Configure>", self._layout, add="+")
        self._layout()
        self._bind_shortcut()
        if hasattr(service, "add_voice_settings_listener"):
            service.add_voice_settings_listener(self.refresh_settings)

    def mount_composer_microphone(self, master, *, button=None, **grid_options):
        """Tk cannot reparent a widget: both controls delegate to one controller."""
        if button is None:
            button = ctk.CTkButton(master, width=64, height=40)
        button.configure(text="🎙 Ses", fg_color="transparent", border_width=1,
                         text_color=("gray20", "gray90"), command=self._mic_invoked)
        button.bind("<ButtonPress-1>", self._press_mic, add="+")
        button.bind("<ButtonRelease-1>", self._release_mic, add="+")
        self._make_accessible(button)
        button.grid(**grid_options)
        self.composer_mic_button = button
        return button

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        from firik_agent.ui.components.voice_settings import refresh_voice_focus_colors
        refresh_voice_focus_colors(getattr(self, "_mode_focus_controls", ()), self._apply_appearance_mode)

    def _layout(self, event=None):
        width = event.width if event else self.winfo_width()
        narrow = width < 500
        if narrow != self._narrow:
            self._narrow = narrow
            if self.compact:
                rows = ((self.session_button, self.mode, self.settings_button),)
            elif narrow:
                rows = ((self.mic_button, self.mode), (self.toggle_button, self.settings_button),
                        (self.session_button, self.pause_button), (self.exit_button, self.stop_button),
                        (self.speech_button, self.agent_button), (self.speech_pause_button, self.replay_button))
            else:
                rows = ((self.mic_button, self.mode, self.toggle_button, self.settings_button),
                        (self.session_button, self.pause_button, self.exit_button),
                        (self.stop_button, self.speech_button, self.agent_button),
                        (self.speech_pause_button, self.replay_button))
            for row, buttons in enumerate(rows):
                for column, button in enumerate(buttons):
                    button.grid(row=row, column=column, padx=(0, 4), pady=2, sticky="w")
            self._status_row = len(rows)
            self.status.grid(row=self._status_row, column=0, columnspan=4, sticky="ew")
            self.input_level.grid(row=self._status_row + 1, column=0, columnspan=4, sticky="ew", pady=3)
            if self._review_visible:
                self._show_review()
        self.status.configure(wraplength=max(180, width - 10))

    @staticmethod
    def _make_accessible(button):
        # CTkButton draws on a Canvas; explicitly include that native widget in
        # keyboard traversal and support ordinary Space/Return activation.
        button._canvas.configure(takefocus=1)
        resting_border = button.cget("border_width")
        def invoke(event):
            button.invoke()
            return "break"
        button.bind("<Return>", invoke, add="+")
        button.bind("<space>", invoke, add="+")
        def focused(event):
            from firik_agent.ui.components.keyboard_focus import reveal_in_scroll_frame
            button.configure(border_width=2)
            reveal_in_scroll_frame(button)
        button.bind("<FocusIn>", focused, add="+")
        button.bind("<FocusOut>", lambda event: button.configure(border_width=resting_border), add="+")

    def _settings(self):
        return getattr(self.service, "voice_settings", VoiceSettings())

    def _controller(self):
        if self.controller is None:
            self.controller = self.service.create_voice_controller(
                on_state=self._on_state, on_transcript=self._on_transcript, on_error=self._on_error,
                on_level=self._on_level)
        self.controller.settings = self._settings()
        if hasattr(self.controller, "bind_session"):
            self.controller.bind_session(self.get_session_id())
        return self.controller

    def _allowed(self):
        if self._closed or not self._active:
            return False
        if not self._settings().enabled:
            self.open_settings()
            return False
        return True

    def _prepare_capture(self):
        if not self._allowed() or self._pending:
            return False
        self._recording_mode = self.mode.get()
        self._capture_session = self.get_session_id()
        self._capture_scope = self._voice_scope() if self._capture_session else None
        return True

    def _voice_scope(self):
        from firik_agent.voice.requests import VoiceScope
        return VoiceScope(self.get_session_id(), project_id=self.get_project_id(),
                          workspace_id=self.get_workspace_id(), target=self.target,
                          revision=getattr(self.service, "_voice_revision", 0))

    def _router(self):
        if self._request_router is None:
            from firik_agent.voice.intent_router import VoiceRequestRouter
            from firik_agent.voice.commands import VoiceCommand
            def handler(request):
                action = self.actions.get(request.command.value)
                if action is not None:
                    return action() is not False
                local = {"mute_speech": self.mute_speech, "unmute_speech": self.unmute_speech,
                         "replay_speech": self.replay_speech, "stop_speech": self.stop_speaking}
                if request.command.value in local:
                    return local[request.command.value]() is not False
                top = self.winfo_toplevel()
                return bool(getattr(top, "handle_voice_command", lambda request: False)(request))
            options = dict(on_submit=self._submit_scoped_request,
                command_handlers={command: handler for command in VoiceCommand},
                scope_provider=self._voice_scope,
                inventory_provider=lambda: getattr(self.winfo_toplevel(), "voice_destinations", lambda: ())())
            factory = getattr(self.service, "create_voice_request_router", VoiceRequestRouter)
            self._request_router = factory(**options)
        return self._request_router

    def _submit_scoped_request(self, request):
        if self.is_busy():
            return False
        if hasattr(self.service, "validate_voice_request"):
            self.service.validate_voice_request(request)
        if self.on_submit_request:
            return self.on_submit_request(request)
        return bool(self.on_submit and self.on_submit(request.request_text))

    def _mic_invoked(self):
        # CTk invokes its command before our release binding. A pointer hold is
        # finalized by _release_mic; keyboard/assistive invoke remains a toggle.
        if not self._holding:
            self.toggle_preferred_interaction()

    def _press_mic(self, event=None):
        self._begin_hold("pointer")

    def _begin_hold(self, source):
        if self._holding or not self._prepare_capture():
            return
        controller = self._controller()
        if controller.state in ("recording", "listening", "speech_detected", "transcribing"):
            return
        self._holding, self._hold_source = True, source
        started = controller.start_recording()
        if started is False:
            self._holding, self._hold_source = False, None

    def _release_mic(self, event=None):
        if self._holding and self._hold_source == "pointer":
            self._finish_hold()

    def _finish_hold(self):
        if not self._holding:
            return
        self._holding, self._hold_source = False, None
        if self.controller:
            self.controller.stop_recording()

    def toggle_recording(self):
        if self._closed or not self._active:
            return
        if self.controller and self.controller.state in ("recording", "listening", "speech_detected"):
            self.controller.stop_recording()
            return
        if self.controller and self.controller.state == "transcribing":
            self.stop_listening()
            return
        if self._prepare_capture():
            self._controller().start_recording()

    def toggle_preferred_interaction(self):
        """The accessible toggle follows preference without starting on load."""
        settings = self._settings()
        if settings.interaction_mode == "conversation" and settings.vad_enabled:
            if self._conversation:
                self.exit_conversation()
            else:
                self.start_conversation()
            return
        self.toggle_recording()

    def _bind_shortcut(self, *, force=False):
        shortcut = getattr(self._settings(), "shortcut", "<Control-space>")
        if shortcut == self._bound_shortcut and not force:
            return
        self._unbind_shortcut()
        try:
            # Tk matches the *shifted* keysym (V), even when a user writes the
            # more familiar <Control-Shift-v> preference.
            sequence = shortcut
            key = shortcut[1:-1].split("-")[-1]
            if "Shift-" in shortcut and len(key) == 1 and key.isalpha():
                sequence = shortcut[:-len(key) - 1] + key.upper() + ">"
            surfaces = [self.winfo_toplevel()]
            if self._voice_surface is not None:
                surfaces.append(self._voice_surface)
            for top in surfaces:
                for pattern, callback in ((sequence, self._shortcut_press),
                        ("<KeyRelease>", self._shortcut_release), ("<FocusOut>", self._focus_out),
                        ("<Unmap>", self._window_unmapped), ("<ButtonRelease-1>", self._release_mic)):
                    self._shortcut_bindings.append((top, pattern, top.bind(pattern, callback, add="+")))
            self._bound_shortcut = shortcut
            self._shortcut_release_keys = {key.casefold()}
            modifiers = {"Control": ("control_l", "control_r"), "Shift": ("shift_l", "shift_r"),
                         "Alt": ("alt_l", "alt_r", "option_l", "option_r"),
                         "Option": ("option_l", "option_r", "alt_l", "alt_r"),
                         "Command": ("command_l", "command_r", "meta_l", "meta_r", "super_l", "super_r")}
            for modifier, keysyms in modifiers.items():
                if modifier + "-" in shortcut:
                    self._shortcut_release_keys.update(keysyms)
        except tk.TclError:
            self._unbind_shortcut()
            self.status.configure(text="Kısayol geçersiz; Ses ayarları bölümünden değiştirin.")

    def _unbind_shortcut(self):
        for top, sequence, identifier in self._shortcut_bindings:
            try:
                if identifier:
                    top.unbind(sequence, identifier)
            except tk.TclError:
                pass
        self._shortcut_bindings = []
        self._bound_shortcut = None

    def _shortcut_press(self, event):
        if self._active and not self._closed and self.winfo_ismapped() and self._dialog is None:
            self._begin_hold("keyboard")
            return "break"

    def _shortcut_release(self, event):
        if (self._holding and self._hold_source == "keyboard"
                and event.keysym.casefold() in self._shortcut_release_keys):
            # Releasing either the shortcut key or a modifier ends capture; no
            # microphone can remain open because modifiers were released first.
            self._finish_hold()

    def _focus_out(self, event):
        if self._holding and self.focus_displayof() is None:
            self._finish_hold()
        if self._automatic_speech:
            self._schedule_audio_guard(0)

    def _window_unmapped(self, event):
        self._focus_out(event)
        if event.widget in (self.winfo_toplevel(), self._voice_surface):
            # CTk briefly withdraws Windows top-levels to repaint the titlebar.
            # Recheck after that native remap instead of cancelling live input.
            self.after(50, self._stop_hidden_capture)

    def _stop_hidden_capture(self):
        if not self._active or self._closed or not self.controller:
            return
        surface = self._voice_surface
        if self.winfo_viewable() or (surface is not None and surface.winfo_viewable()):
            return
        state = getattr(self.controller, "state_id", self.controller.state)
        if state in ("recording", "listening", "speech_detected"):
            self.stop_listening()

    def start_conversation(self):
        if not self._prepare_capture():
            return
        controller = self._controller()
        if not hasattr(controller, "start_conversation"):
            return
        if controller.start_conversation() is not False:
            self._conversation, self._paused = True, False
            self.session_button.configure(text="Sesli sohbet açık")
            self.pause_button.configure(text="Duraklat")
            self.show_voice_surface()

    def toggle_pause(self):
        if not self._conversation or not self.controller:
            return
        paused = not self._paused
        if self._paused:
            if self._pending:
                self.status.configure(text="Önce transkripti gönderin veya vazgeçin.")
                return
            self._capture_session = self.get_session_id()
            if self.controller.resume_conversation() is False:
                self.status.configure(text="Dinleme sürdürülemedi. Yeniden deneyin veya metinle devam edin.")
                return
        else:
            self.controller.pause_conversation()
        self._paused = paused
        self.pause_button.configure(text="Devam et" if self._paused else "Duraklat")
        if self._paused:
            self.status.configure(text="Sesli sohbet duraklatıldı; mikrofon kapalı.")
        self._sync_voice_surface()

    def exit_conversation(self):
        if self.controller:
            if hasattr(self.controller, "exit_conversation"):
                self.controller.exit_conversation()
            else:
                self.controller.cancel()
        self._conversation, self._paused = False, False
        self._holding, self._hold_source = False, None
        self.session_button.configure(text="Sesli sohbet")
        self.pause_button.configure(text="Duraklat")
        self.dismiss_command(resolve=False)
        self._stop_duration()
        self._close_voice_surface()

    def show_voice_surface(self):
        if self._voice_surface is not None and self._voice_surface.winfo_exists():
            self._voice_surface.lift()
            return self._voice_surface
        from firik_agent.ui.components.voice_session import VoiceSessionPanel
        self._voice_surface = VoiceSessionPanel(self)
        self._bind_shortcut(force=True)
        self._voice_surface.set_transcript(self._last_transcript, pending=self._pending)
        self._voice_surface.set_preview(self._response_preview_text)
        self._sync_voice_surface()
        # Creating a native Windows top-level can pump Tk callbacks before the
        # constructor returns. Replay an error delivered during that interval.
        if self._last_state in {"error", "permission_required"}:
            self._voice_surface.show_error(self.status.cget("text"))
        return self._voice_surface

    def _close_voice_surface(self):
        surface, self._voice_surface = self._voice_surface, None
        if surface is not None and surface.winfo_exists():
            surface.destroy()
            if not self._closed:
                self._bind_shortcut(force=True)

    def _sync_voice_surface(self):
        if self._voice_surface is not None and self._voice_surface.winfo_exists():
            canonical = getattr(self.controller, "state_id", self._last_state)
            canonical = getattr(canonical, "value", canonical)
            self._voice_surface.update_state(canonical,
                recording=self._last_state in {"recording", "listening", "speech_detected"},
                paused=self._paused, conversation=self._conversation)

    def _sync_surface_draft(self):
        if self._voice_surface is not None and self._pending:
            self._voice_surface.set_transcript(self.command_text.get(), pending=True)

    def _on_state(self, state):
        if self._closed or not self._active:
            return
        self._last_state = state
        if state in {"recording", "listening", "speech_detected", "transcribing", "speaking"}:
            self._last_operation = state
            self.error_actions.grid_remove()
        if self.controller:
            generation = getattr(self.controller, "generation", None)
            if self._pending and generation != self._review_generation:
                self.dismiss_command(resolve=False)
            if hasattr(self.controller, "conversation_active"):
                self._conversation = self.controller.conversation_active
                self._paused = self.controller.paused
                self.session_button.configure(text="Sesli sohbet açık" if self._conversation else "Sesli sohbet")
                self.pause_button.configure(text="Devam et" if self._paused else "Duraklat")
        recording = state in ("recording", "listening", "speech_detected")
        if self.composer_mic_button is not None:
            self.composer_mic_button.configure(text="■ Bitir" if recording else "🎙 Ses",
                fg_color=("#dc2626", "#b91c1c") if recording else "transparent",
                text_color="white" if recording else ("gray20", "gray90"))
        self.toggle_button.configure(text="Kaydı bitir" if recording else "Aç / bitir")
        self._state_label = {
            "disabled": "Ses kapalı", "idle": "Hazır", "ready": "Hazır",
            "permission_required": "Mikrofon izni gerekiyor.",
            "recording": "Kayıt — bitirmek için bırakın veya Aç / bitir düğmesine basın.",
            "listening": "Dinleniyor — konuşmaya başlayın.", "speech_detected": "Konuşma algılandı…",
            "transcribing": "Konuşma metne dönüştürülüyor…",
            "awaiting_confirmation": "Transkripti inceleyin; düzenleyip gönderebilirsiniz.",
            "thinking": "Yanıt hazırlanıyor…", "executing": "İşlem yürütülüyor…",
            "speaking": "Yanıt okunuyor…", "interrupted": "Ses kesildi.", "error": "Ses işlemi başarısız.",
        }.get(state, state)
        if recording and self._duration_start is None:
            self._duration_start = time.monotonic()
            self._tick_duration()
        elif not recording:
            self._stop_duration()
        if not recording:
            self.status.configure(text=self._state_label)
            self.input_level.set(0)
        if state != "speaking":
            self._speech_paused = False
            self.speech_pause_button.configure(text="Sesi duraklat")
        self._sync_voice_surface()

    def _on_level(self, value):
        if self._active and not self._closed:
            self.input_level.set(max(0, min(1, float(value))))
            if self._voice_surface is not None:
                self._voice_surface.update_level(value)

    def _tick_duration(self):
        self._duration_job = None
        if self._closed or not self._active or self._duration_start is None:
            return
        elapsed = max(0, int(time.monotonic() - self._duration_start))
        self.status.configure(text=f"{self._state_label}  {elapsed // 60:02}:{elapsed % 60:02}")
        self._duration_job = self.after(100, self._tick_duration)

    def _stop_duration(self):
        if self._duration_job is not None:
            try:
                self.after_cancel(self._duration_job)
            except tk.TclError:
                pass
        self._duration_job, self._duration_start = None, None

    def _show_review(self):
        self._review_visible = True
        self.review.grid(row=self._status_row + 2, column=0, columnspan=4, sticky="ew", pady=(3, 0))
        self.apply_button.configure(text="Uygula" if self._recording_mode == "Sesli komut" else "Gönder")

    def _on_transcript(self, text):
        if (self._closed or not self._active or self._pending
                or self._capture_session != self.get_session_id()):
            return
        self._pending = True
        self._last_transcript = str(text)
        self._stop_duration()
        self.toggle_button.configure(text="Aç / bitir")
        self._review_generation = getattr(self.controller, "generation", None)
        self.command_text.delete(0, "end")
        self.command_text.insert(0, text)
        self._show_review()
        if self._voice_surface is not None:
            self._voice_surface.set_transcript(text, pending=True)
            self._voice_surface.show_error("")
        if self.on_submit is not None or self.on_submit_request is not None:
            try:
                self._voice_request = self._router().create_request(str(text), self._capture_scope or self._voice_scope(),
                    recognized_language=getattr(text, "recognized_language", None))
            except ValueError as error:
                self.status.configure(text=str(error))
                if self._voice_surface is not None:
                    self._voice_surface.show_error(error)
                return
        # Preserve the legacy dictation-only embedding API. Actual chat/coding
        # panels supply on_submit and keep unsent speech solely in this review.
        if self.on_submit is None and self.on_submit_request is None and self._recording_mode != "Sesli komut":
            self.on_dictation(text)
            self.dismiss_command()
            self.status.configure(text="Transkript eklendi. Düzenleyip Gönder / Çalıştır ile iletin.")
            return
        settings = self._settings()
        from firik_agent.voice.commands import requires_review
        if (getattr(settings, "auto_send", False) and not getattr(settings, "review_before_send", True)
                and not requires_review(text)):
            # Keep the recognized utterance visible for a Tk draw before the
            # normal submission service starts. No invented partial transcript.
            token = text
            self.after_idle(lambda: self._auto_submit(token))
        else:
            self.status.configure(text="Transkripti inceleyin; düzenleyip Gönder / Uygula ile iletin.")

    def _auto_submit(self, text):
        if (self._pending and self._active and not self._closed
                and self.command_text.get() == text):
            self.apply_transcript(_from_auto=True)

    def _on_error(self, message):
        if not self._closed and self._active:
            self._stop_duration()
            self.status.configure(text=message)
            self.error_actions.grid(row=self._status_row + 3, column=0, columnspan=4, sticky="w", pady=4)
            if self._voice_surface is not None:
                self._voice_surface.show_error(message)

    def retry_voice_error(self):
        if self._last_operation == "speaking":
            self.replay_speech()
        else:
            self.retry_transcript()

    def use_text_input(self):
        self.exit_conversation()
        self.error_actions.grid_remove()
        self.status.configure(text="Metin girişi kullanılabilir.")
        self.on_text_fallback()

    def apply_transcript(self, *, _from_auto=False, _command_only=False):
        if (not self._active or self._closed or not self._pending or self._submitting
                or self._capture_session != self.get_session_id()):
            return
        if not self._review_current():
            return
        text = self.command_text.get().strip()
        if not text:
            return
        self._submitting = True
        try:
            router = self._router()
            request = self._voice_request
            if request is None:
                request = router.create_request(text, self._capture_scope or self._voice_scope())
            elif request.request_text != text:
                request = router.revise(request, text)
            if (self._recording_mode == "Sesli komut" or _command_only) and request.command is None:
                self.status.configure(text="Komut tanınmadı. Desteklenen komutlar için Ses ayarları bölümünü açın.")
                return
            if not _from_auto:
                request = router.confirm(request, source="ui")
            self._voice_request = request
            accepted = router.route(request)
        except ValueError as error:
            self.status.configure(text=str(error))
            if self._voice_surface is not None:
                self._voice_surface.show_error(error)
            return
        finally:
            self._submitting = False
        if self._closed or not self._active:
            return
        if accepted:
            self._last_transcript = request.request_text
            self.dismiss_command(resolve=False, sent=True)
            if self._voice_surface is not None:
                self._voice_surface.mark_transcript_sent()
            if self.controller and hasattr(self.controller, "transcript_resolved"):
                self.controller.transcript_resolved(submitted=request.command is None)
        else:
            self.status.configure(text=("Etkin işlem sürüyor. Transkript bekliyor; işlem bitince gönderebilirsiniz."
                                       if self.is_busy() else "Transkript gönderilmedi. Mevcut taslağı veya etkin işlemi kontrol edin."))
            if self._voice_surface is not None:
                self._voice_surface.show_error(self.status.cget("text"))

    def apply_command(self):
        self.apply_transcript(_command_only=True)

    def _review_current(self):
        if self.controller and getattr(self.controller, "generation", None) != self._review_generation:
            self.dismiss_command(resolve=False)
            self.status.configure(text="Ses oturumu değişti. Yeni bir kayıt başlatın.")
            return False
        return True

    def copy_transcript(self):
        if self._pending:
            self.clipboard_clear()
            self.clipboard_append(self.command_text.get())
            self.status.configure(text="Transkript kopyalandı.")

    def retry_transcript(self):
        if not self._active or self._closed:
            return
        self.dismiss_command(resolve=False)
        self.toggle_recording()

    def dismiss_command(self, resolve=True, *, sent=False):
        if self._voice_request is not None and self._voice_request.pending:
            self._voice_request.cancellation_token.set()
        self._voice_request = None
        pending = self._pending
        self._pending = self._review_visible = False
        self._review_generation = None
        self.review.grid_remove()
        self.command_text.delete(0, "end")
        if not sent:
            self._last_transcript = ""
        if self._voice_surface is not None:
            self._voice_surface.set_transcript(self._last_transcript, pending=False)
        if pending and resolve and self.controller and hasattr(self.controller, "transcript_resolved"):
            self.controller.transcript_resolved(submitted=False)

    def response_started(self, state="thinking", *, from_voice=False):
        self._stop_audio_guard()
        self._response_preview_text = ""
        if self._voice_surface is not None:
            self._voice_surface.set_preview("")
        settings = self._settings()
        if self._active and not self._closed and (self.controller or
                settings.enabled and settings.read_replies and settings.stream_replies):
            controller = self._controller()
            if hasattr(controller, "response_started"):
                if from_voice:
                    controller.response_started(state, from_voice=True)
                else:
                    controller.response_started(state)
            elif hasattr(controller, "mark_agent_state"):
                controller.mark_agent_state(state)

    def agent_activity(self, state):
        # An event from already-running work must not discard a new utterance
        # (for example a spoken stop-agent command). Only a newly accepted
        # submission suspends input via response_started.
        if not self.controller:
            return
        current = getattr(self.controller, "state_id", self.controller.state)
        current = getattr(current, "value", current)
        if current in {"recording", "listening", "speech_detected", "transcribing", "awaiting_confirmation"}:
            return
        if hasattr(self.controller, "mark_agent_state"):
            self.controller.mark_agent_state(state)

    def response_delta(self, text):
        settings = self._settings()
        self._update_response_preview(text, append=True)
        if (self._active and not self._closed and self.winfo_ismapped() and settings.enabled
                and settings.read_replies and settings.stream_replies and self._automatic_audio_allowed()):
            controller = self._controller()
            if hasattr(controller, "response_delta"):
                controller.response_delta(text)
                self._watch_automatic_speech()

    def response_completed(self, text):
        settings = self._settings()
        self._update_response_preview(text or "")
        if not (self._active and not self._closed and self.winfo_ismapped() and settings.enabled) or self.is_busy():
            return
        controller = self._controller()
        automatic = bool(text and settings.read_replies and self._automatic_audio_allowed())
        if hasattr(controller, "response_completed"):
            controller.response_completed(text if automatic else None)
        elif automatic:
            controller.speak(text)
        if automatic:
            self._watch_automatic_speech()

    def _watch_automatic_speech(self):
        self._automatic_speech = True
        if self._audio_guard_job is None:
            self._schedule_audio_guard()

    def _schedule_audio_guard(self, delay=80):
        if self._audio_guard_job is not None:
            self.after_cancel(self._audio_guard_job)
        self._audio_guard_job = self.after(delay, self._guard_automatic_speech)

    def _stop_audio_guard(self):
        self._automatic_speech = False
        if self._audio_guard_job is not None:
            self.after_cancel(self._audio_guard_job)
            self._audio_guard_job = None

    def _guard_automatic_speech(self):
        self._audio_guard_job = None
        if not self._automatic_speech or self._closed:
            return
        if not self._settings().read_replies or not self._automatic_audio_allowed():
            self.stop_speaking()
            return
        state = getattr(self.controller, "state_id", getattr(self.controller, "state", None))
        if getattr(state, "value", state) == "speaking" or self.is_busy():
            self._schedule_audio_guard()
        else:
            self._automatic_speech = False

    def _update_response_preview(self, text, *, append=False):
        if not self._settings().enabled or not self._active or self._closed:
            return
        self._response_preview_text = ((self._response_preview_text + str(text)) if append else str(text))[:2048]
        if self._voice_surface is not None:
            from firik_agent.projects.indexer import sanitize_secrets
            from firik_agent.voice.speech import speech_text
            self._voice_surface.set_preview(speech_text(sanitize_secrets(self._response_preview_text)))

    def _automatic_audio_allowed(self):
        if (not self._settings().enabled or not self._active or self._closed
                or not self.winfo_ismapped() or self._pending or self._holding):
            return False
        if self._dialog is not None:
            return False
        if getattr(self.controller, "_muted", False):
            return False
        top = self.winfo_toplevel()
        try:
            if top.state() in {"iconic", "withdrawn"}:
                return False
            focus = top.focus_displayof()
            if focus is None or focus.winfo_toplevel() not in (top, self._voice_surface):
                return False
        except tk.TclError:
            return False
        return self._last_state not in {"recording", "listening", "speech_detected", "transcribing", "awaiting_confirmation"}

    def coding_completion(self, text, *, outcome="completed"):
        settings = self._settings()
        if (outcome == "completed" and getattr(settings, "completion_chime", False) and self._automatic_audio_allowed()
                and not (self._conversation and not self._paused)):
            self.bell()
        self.response_completed(text)

    def read_text(self, text):
        if not self._settings().enabled:
            self.open_settings()
        elif text and self._active and not self._closed:
            self.stop_speaking()
            self._controller().speak(text)

    def stop_listening(self):
        self._holding, self._hold_source = False, None
        if self.controller:
            if hasattr(self.controller, "stop_listening"):
                self.controller.stop_listening()
            else:
                self.controller.cancel()
        self._paused = self._conversation
        self.pause_button.configure(text="Devam et" if self._paused else "Duraklat")
        self._stop_duration()
        self.status.configure(text="Dinleme durduruldu; etkin ajan işlemi sürer.")
        self._sync_voice_surface()

    def stop_speaking(self):
        self._stop_audio_guard()
        if self.controller and hasattr(self.controller, "stop_speaking"):
            self.controller.stop_speaking()

    def toggle_speech_pause(self):
        if self.controller:
            action = "resume_speaking" if self._speech_paused else "pause_speaking"
            if getattr(self.controller, action, lambda: False)():
                self._speech_paused = not self._speech_paused
                self.speech_pause_button.configure(text="Sese devam et" if self._speech_paused else "Sesi duraklat")
                self._sync_voice_surface()

    def replay_speech(self):
        if self.controller:
            self.stop_speaking()
            return getattr(self.controller, "replay_speech", lambda: False)()
        return False

    def mute_speech(self):
        return bool(self.controller and getattr(self.controller, "mute", lambda: False)())

    def unmute_speech(self):
        return bool(self.controller and getattr(self.controller, "unmute", lambda: False)())

    def stop_agent(self):
        if self._active and not self._closed:
            action = self.actions.get("stop_response")
            if action:
                action()

    def cancel(self):
        self._stop_audio_guard()
        if self._voice_request is not None and self._voice_request.pending:
            self._voice_request.cancellation_token.set()
        self._voice_request = None
        self._capture_scope = None
        self._response_preview_text = self._last_transcript = ""
        if self.controller:
            self.controller.cancel()
        self._holding, self._hold_source = False, None
        self._conversation, self._paused = False, False
        self._stop_duration()
        self._close_voice_surface()
        self.error_actions.grid_remove()
        if not self._closed:
            self.session_button.configure(text="Sesli sohbet")
            self.pause_button.configure(text="Duraklat")
            self.status.configure(text="Hazır")

    def deactivate(self):
        self._active = False
        self.cancel()
        self.dismiss_command(resolve=False)
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.destroy()
        self._dialog = None

    def activate(self):
        self._active = True
        if self.controller:
            if hasattr(self.controller, "sync_agent_state"):
                self.controller.sync_agent_state(self.is_busy())
            elif not self.is_busy() and hasattr(self.controller, "response_completed"):
                self.controller.response_completed(None)
            elif self.is_busy() and hasattr(self.controller, "mark_agent_state"):
                self.controller.mark_agent_state("thinking")
        self._bind_shortcut()

    def destroy(self):
        self._closed = True
        self._stop_audio_guard()
        if hasattr(self.service, "remove_voice_settings_listener"):
            self.service.remove_voice_settings_listener(self.refresh_settings)
        self._close_voice_surface()
        if self._request_router is not None:
            self._request_router.close()
        self._stop_duration()
        self._unbind_shortcut()
        if self.controller:
            self.controller.close()
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.destroy()
        super().destroy()

    def refresh_settings(self):
        if self._closed:
            return
        self.cancel()
        self.dismiss_command(resolve=False)
        if self.controller is not None:
            self.controller.settings = self._settings()
        self._bind_shortcut()
        self._state_label = "Hazır" if self._settings().enabled else "Ses kapalı"
        self.status.configure(text=self._state_label)

    def open_settings(self):
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.lift()
            return
        self.stop_listening()
        from firik_agent.ui.components.voice_settings import VoiceSettingsFrame
        dialog = self._dialog = ctk.CTkToplevel(self)
        dialog.title("Ses ayarları")
        width = min(680, max(380, dialog.winfo_screenwidth() - 80))
        height = min(740, max(500, dialog.winfo_screenheight() - 100))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(380, 480)
        dialog.transient(self.winfo_toplevel())
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)

        def saved(settings):
            if self._dialog is dialog:
                self._dialog = None
                dialog.destroy()
            self.refresh_settings()

        form = VoiceSettingsFrame(dialog, self.service, on_saved=saved, show_save=False)
        form.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self._settings_form = form
        def closed(event):
            if event.widget is dialog and self._dialog is dialog:
                self._dialog = None
                self._settings_form = None
        dialog.bind("<Destroy>", closed, add="+")
        ctk.CTkButton(dialog, text="Kaydet", command=form.save).grid(row=1, column=0, pady=(0, 10))
        dialog.bind("<Escape>", lambda event: dialog.destroy())
