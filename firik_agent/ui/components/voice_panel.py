"""Session-bound desktop voice controls; all widget work stays on the Tk thread."""
from __future__ import annotations

from dataclasses import replace
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from firik_agent.voice.commands import parse_command
from firik_agent.voice.config import VoiceSettings


class VoicePanel(ctk.CTkFrame):
    def __init__(self, master, service, *, on_dictation, actions, on_submit=None,
                 get_session_id=None, is_busy=None, on_submit_request=None,
                 get_project_id=None, get_workspace_id=None, target="conversation", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service, self.on_dictation, self.actions = service, on_dictation, actions
        self.on_submit = on_submit
        self.on_submit_request = on_submit_request
        self.get_project_id = get_project_id or (lambda: None)
        self.get_workspace_id = get_workspace_id or (lambda: None)
        self.target = target
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
        self.review_actions = ctk.CTkFrame(self.review, fg_color="transparent")
        self.review_actions.grid(row=1, column=0, columnspan=4, sticky="w")
        self.apply_button = ctk.CTkButton(self.review_actions, text="Gönder", width=64, command=self.apply_transcript)
        self.copy_button = ctk.CTkButton(self.review_actions, text="Kopyala", width=64, command=self.copy_transcript)
        self.retry_button = ctk.CTkButton(self.review_actions, text="Tekrar", width=64, command=self.retry_transcript)
        self.discard_button = ctk.CTkButton(self.review_actions, text="Vazgeç", width=64, command=self.dismiss_command)
        for column, button in enumerate((self.apply_button, self.copy_button, self.retry_button, self.discard_button)):
            button.grid(row=0, column=column, padx=(0, 3), pady=(0, 3))
        for button in (self.mic_button, self.toggle_button, self.settings_button, self.session_button,
                       self.pause_button, self.exit_button, self.stop_button, self.speech_button,
                       self.agent_button, self.speech_pause_button, self.replay_button,
                       self.apply_button, self.copy_button, self.retry_button,
                       self.discard_button):
            self._make_accessible(button)
        if not hasattr(service, "create_voice_controller"):
            for button in (self.mic_button, self.toggle_button, self.session_button):
                button.configure(state="disabled")
        self.bind("<Configure>", self._layout, add="+")
        self._layout()
        self._bind_shortcut()

    def _layout(self, event=None):
        width = event.width if event else self.winfo_width()
        narrow = width < 500
        if narrow != self._narrow:
            self._narrow = narrow
            if narrow:
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
        def invoke(event):
            button.invoke()
            return "break"
        button.bind("<Return>", invoke, add="+")
        button.bind("<space>", invoke, add="+")
        button.bind("<FocusIn>", lambda event: button.configure(border_width=2), add="+")
        button.bind("<FocusOut>", lambda event: button.configure(border_width=0), add="+")

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
            self.toggle_recording()

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

    def _bind_shortcut(self):
        shortcut = getattr(self._settings(), "shortcut", "<Control-space>")
        if shortcut == self._bound_shortcut:
            return
        self._unbind_shortcut()
        try:
            top = self.winfo_toplevel()
            # Tk matches the *shifted* keysym (V), even when a user writes the
            # more familiar <Control-Shift-v> preference.
            sequence = shortcut
            key = shortcut[1:-1].split("-")[-1]
            if "Shift-" in shortcut and len(key) == 1 and key.isalpha():
                sequence = shortcut[:-len(key) - 1] + key.upper() + ">"
            self._shortcut_bindings = [
                (sequence, top.bind(sequence, self._shortcut_press, add="+")),
                ("<KeyRelease>", top.bind("<KeyRelease>", self._shortcut_release, add="+")),
                ("<FocusOut>", top.bind("<FocusOut>", self._focus_out, add="+")),
                ("<ButtonRelease-1>", top.bind("<ButtonRelease-1>", self._release_mic, add="+")),
            ]
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
        try:
            top = self.winfo_toplevel()
            for sequence, identifier in self._shortcut_bindings:
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

    def toggle_pause(self):
        if not self._conversation or not self.controller:
            return
        paused = not self._paused
        if self._paused:
            if self._pending:
                self.status.configure(text="Önce transkripti gönderin veya vazgeçin.")
                return
            self._capture_session = self.get_session_id()
            self.controller.resume_conversation()
        else:
            self.controller.pause_conversation()
        self._paused = paused
        self.pause_button.configure(text="Devam et" if self._paused else "Duraklat")
        if self._paused:
            self.status.configure(text="Sesli sohbet duraklatıldı; mikrofon kapalı.")

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

    def _on_state(self, state):
        if self._closed or not self._active:
            return
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

    def _on_level(self, value):
        if self._active and not self._closed:
            self.input_level.set(max(0, min(1, float(value))))

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
        self._stop_duration()
        self.toggle_button.configure(text="Aç / bitir")
        self._review_generation = getattr(self.controller, "generation", None)
        self.command_text.delete(0, "end")
        self.command_text.insert(0, text)
        self._show_review()
        if self.on_submit is not None or self.on_submit_request is not None:
            try:
                self._voice_request = self._router().create_request(str(text), self._capture_scope or self._voice_scope(),
                    recognized_language=getattr(text, "recognized_language", None))
            except ValueError as error:
                self.status.configure(text=str(error))
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
            return
        finally:
            self._submitting = False
        if self._closed or not self._active:
            return
        if accepted:
            self.dismiss_command(resolve=False)
            if self.controller and hasattr(self.controller, "transcript_resolved"):
                self.controller.transcript_resolved(submitted=request.command is None)
        else:
            self.status.configure(text=("Etkin işlem sürüyor. Transkript bekliyor; işlem bitince gönderebilirsiniz."
                                       if self.is_busy() else "Transkript gönderilmedi. Mevcut taslağı veya etkin işlemi kontrol edin."))

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
        if self.controller and hasattr(self.controller, "retry_transcription"):
            if self.controller.retry_transcription() is not False:
                return
        if self.controller and hasattr(self.controller, "transcript_resolved"):
            self.controller.transcript_resolved(False)
        self.toggle_recording()

    def dismiss_command(self, resolve=True):
        if self._voice_request is not None and self._voice_request.pending:
            self._voice_request.cancellation_token.set()
        self._voice_request = None
        pending = self._pending
        self._pending = self._review_visible = False
        self._review_generation = None
        self.review.grid_remove()
        self.command_text.delete(0, "end")
        if pending and resolve and self.controller and hasattr(self.controller, "transcript_resolved"):
            self.controller.transcript_resolved(submitted=False)

    def response_started(self, state="thinking"):
        settings = self._settings()
        if self._active and not self._closed and (self.controller or
                settings.enabled and settings.read_replies and settings.stream_replies):
            controller = self._controller()
            if hasattr(controller, "response_started"):
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
        if (self._active and not self._closed and self.winfo_ismapped() and settings.enabled
                and settings.read_replies and settings.stream_replies):
            controller = self._controller()
            if hasattr(controller, "response_delta"):
                controller.response_delta(text)

    def response_completed(self, text):
        settings = self._settings()
        if not (self._active and not self._closed and self.winfo_ismapped() and settings.enabled) or self.is_busy():
            return
        controller = self._controller()
        if hasattr(controller, "response_completed"):
            controller.response_completed(text if settings.read_replies else None)
        elif text and settings.read_replies:
            controller.speak(text)

    def read_text(self, text):
        if not self._settings().enabled:
            self.open_settings()
        elif text and self._active and not self._closed:
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

    def stop_speaking(self):
        if self.controller and hasattr(self.controller, "stop_speaking"):
            self.controller.stop_speaking()

    def toggle_speech_pause(self):
        if self.controller:
            action = "resume_speaking" if self._speech_paused else "pause_speaking"
            if getattr(self.controller, action, lambda: False)():
                self._speech_paused = not self._speech_paused
                self.speech_pause_button.configure(text="Sese devam et" if self._speech_paused else "Sesi duraklat")

    def replay_speech(self):
        if self.controller:
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
        if self._voice_request is not None and self._voice_request.pending:
            self._voice_request.cancellation_token.set()
        self._voice_request = None
        self._capture_scope = None
        if self.controller:
            self.controller.cancel()
        self._holding, self._hold_source = False, None
        self._conversation, self._paused = False, False
        self._stop_duration()
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
        if self._request_router is not None:
            self._request_router.close()
        self._stop_duration()
        self._unbind_shortcut()
        if self.controller:
            self.controller.close()
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.destroy()
        super().destroy()

    def open_settings(self):
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.lift()
            return
        self.stop_listening()
        settings = self._settings()
        dialog = self._dialog = ctk.CTkToplevel(self)
        dialog.title("Ses ayarları")
        dialog.geometry("620x720")
        dialog.transient(self.winfo_toplevel())
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(1, weight=1)
        enabled = tk.BooleanVar(value=settings.enabled)
        read = tk.BooleanVar(value=settings.read_replies)
        auto = tk.BooleanVar(value=getattr(settings, "auto_send", False))
        review = tk.BooleanVar(value=getattr(settings, "review_before_send", True))
        stream = tk.BooleanVar(value=settings.stream_replies)
        barge = tk.BooleanVar(value=getattr(settings, "barge_in", False))
        vad_enabled = tk.BooleanVar(value=settings.vad_enabled)
        ctk.CTkCheckBox(body, text="Ses özelliklerini etkinleştir", variable=enabled).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=10)
        ctk.CTkLabel(body, text="Bas-konuş: yalnızca basılı tutarken kayıt. Aç / bitir erişilebilir alternatiftir.\nSesli sohbeti açıkça başlatın; yerel VAD tamamlanan sözleri ayırır.\nEvren ASR seçiliyse tamamlanan ses Evren'e gönderilir; ham ses diske yazılmaz.\nEvren TTS yanıt metnini Evren'e gönderir; sistem TTS yerel çalışır.\nDuraklat / çıkış mikrofonu kapatır. Yazılı sohbet her zaman kullanılabilir.", justify="left", anchor="w", wraplength=550).grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        for row, (label, variable) in enumerate((("Tamamlanan yanıtları sesli oku", read),
                ("Sıradan istekleri otomatik gönder", auto), ("Göndermeden önce her transkripti incele", review)), 2):
            ctk.CTkCheckBox(body, text=label, variable=variable).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        barge.set(False)
        ctk.CTkLabel(body, text="Yankı iptali yok: yanıt okunurken mikrofon kapalıdır.\nAraya girmek için Basılı tut düğmesi sesi durdurur. Kulaklık önerilir.",
                       justify="left", wraplength=550).grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(body, text="Hassas işlemler ve ajanı durdurma komutları her zaman inceleme gerektirir.", wraplength=550, justify="left").grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(body, text="Konuşma dili").grid(row=7, column=0, sticky="w", padx=8, pady=6)
        language = ctk.CTkOptionMenu(body, values=["Otomatik", "Türkçe", "İngilizce"])
        language.set({"auto": "Otomatik", "tr": "Türkçe", "en": "İngilizce"}[settings.language])
        language.grid(row=7, column=1, sticky="ew", padx=8)
        entries = {}
        fields = [("max_record_seconds", "En uzun söz (5–120 sn)", str(settings.max_record_seconds)),
                  ("speech_rate", "Okuma hızı (kelime/dakika)", str(settings.speech_rate)),
                  ("playback_speed", "Seslendirme hız çarpanı (0.25–4)", str(settings.playback_speed)),
                  ("shortcut", "Bas-konuş kısayolu (Tk biçimi)", getattr(settings, "shortcut", "<Control-space>")),
                  ("vad_threshold", "Konuşma eşiği (PCM RMS)", str(getattr(settings, "vad_threshold", 350))),
                  ("vad_silence_ms", "Söz sonu sessizlik (ms)", str(getattr(settings, "vad_silence_ms", 700))),
                  ("input_gain", "Mikrofon kazancı (0.1–4)", str(settings.input_gain)),
                  ("output_volume", "Yanıt ses düzeyi (0–1)", str(settings.output_volume)),
                  ("vad_min_speech_ms", "En kısa konuşma (ms)", str(settings.vad_min_speech_ms)),
                  ("vad_pre_roll_ms", "Konuşma öncesi koruma (ms)", str(settings.vad_pre_roll_ms)),
                  ("asr_request_seconds", "İstek başına ses (1–30 sn)", str(settings.asr_request_seconds))]
        selections, menus, choices = {}, {}, {}
        for row, kind, title, current in ((8, "input", "Mikrofon", settings.input_device),
                                           (9, "output", "Hoparlör", settings.output_device)):
            selections[kind] = current
            initial = "Sistem varsayılanı" if current is None else f"Seçili: {current}"
            choices[kind] = {"Sistem varsayılanı": None, initial: current}
            def selected(label, kind=kind):
                selections[kind] = choices[kind][label]
            ctk.CTkLabel(body, text=title).grid(row=row, column=0, sticky="w", padx=8, pady=6)
            menus[kind] = ctk.CTkOptionMenu(body, values=list(choices[kind]), command=selected)
            menus[kind].set(initial)
            menus[kind].grid(row=row, column=1, sticky="ew", padx=8)

        def devices_loaded(devices):
            if self._closed or self._dialog is not dialog or not dialog.winfo_exists():
                return
            for kind, items in devices.items():
                # Duplicate labels remain distinguishable and IDs resolve
                # ambiguously rather than silently choosing another device.
                for item in items:
                    choices[kind][f"{item.name} · {item.host_api} ({item.index})"] = item.id
                menus[kind].configure(values=list(choices[kind]))
            device_status.configure(text="Aygıt seçimi sonraki kayıtta / oynatmada uygulanır.")
        def devices_error(message):
            if not self._closed and self._dialog is dialog and dialog.winfo_exists():
                device_status.configure(text=message)
        device_status = ctk.CTkLabel(body, text="Ses aygıtları taranıyor…", wraplength=550, justify="left")
        device_status.grid(row=10, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        if hasattr(self.service, "audio_devices_async"):
            self.service.audio_devices_async(devices_loaded, devices_error)
        else:
            device_status.configure(text="Sistem varsayılanını veya kayıtlı aygıtı seçin.")
        for row, (key, label, value) in enumerate(fields, 11):
            ctk.CTkLabel(body, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
            entry = ctk.CTkEntry(body)
            entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            entries[key] = entry
        next_row = 11 + len(fields)
        provider_menus = {}
        for key, label, options, current in (
                ("asr_provider", "Ses çözümleme sağlayıcısı", ["evren"], settings.asr_provider),
                ("asr_model", "Ses çözümleme modeli", ["qwen3-asr-1.7b"], settings.asr_model),
                ("tts_provider", "Seslendirme sağlayıcısı", ["evren", "system"], settings.tts_provider),
                ("tts_model", "Evren seslendirme modeli", ["voxcpm2"], settings.tts_model),
                ("tts_voice", "Evren sesi", ["default"], settings.tts_voice),
                ("tts_format", "Evren ses biçimi", ["wav", "mp3", "flac", "opus", "pcm"], settings.tts_format),
                ("interaction_mode", "Tercih edilen etkileşim", ["push_to_talk", "conversation"], settings.interaction_mode)):
            ctk.CTkLabel(body, text=label).grid(row=next_row, column=0, sticky="w", padx=8, pady=6)
            menu = ctk.CTkOptionMenu(body, values=options)
            if key == "interaction_mode":
                menu.configure(command=lambda value: vad_enabled.set(value == "conversation"))
            # Future/local configurations remain visible instead of silently
            # switching a provider. Validation/runtime reports availability.
            menu.set(current)
            menu.grid(row=next_row, column=1, sticky="ew", padx=8)
            provider_menus[key] = menu
            next_row += 1
        ctk.CTkLabel(body, text="ASR, sohbet modeli ve TTS birbirinden bağımsızdır. Kodlama modeli değişmez.\nEvren ses API sözleşmesi belgelenmiştir; hesabınızdaki model erişimi ve\nTürkçe / İngilizce ses kalitesi canlı istekle henüz doğrulanmamıştır.\nYalnızca default sesi sunulur. Ses klonlama kapalıdır.\nWAV yerleşik oynatılır; diğer biçimler isteğe bağlı çözücü gerektirebilir.",
                       justify="left", wraplength=550).grid(row=next_row, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        next_row += 1
        capability_status = ctk.CTkLabel(body, text="Model erişimi henüz sorgulanmadı.", justify="left", wraplength=550)
        capability_status.grid(row=next_row, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        next_row += 1

        def capabilities_loaded(capabilities):
            if self._closed or self._dialog is not dialog or not dialog.winfo_exists():
                return
            summaries = []
            for kind in ("asr", "tts"):
                capability = capabilities.get(kind)
                if capability is None:
                    continue
                if getattr(capability, "provider", "") == "system":
                    summaries.append(f"{kind.upper()}: sistem seslendirmesi seçili.")
                    continue
                available = getattr(capability, "available_models", None)
                if getattr(capability, "authenticated", False) and available is not None:
                    summaries.append(f"{kind.upper()}: " + (", ".join(available) if available else "hesapta uygun ses modeli görünmüyor."))
                else:
                    summaries.append(f"{kind.upper()}: hesap erişimi doğrulanmadı.")
            capability_status.configure(text="\n".join(summaries) or "Ses yetenekleri henüz doğrulanmadı.")
            capability_button.configure(state="normal")

        def capabilities_error(message):
            if self._closed or self._dialog is not dialog or not dialog.winfo_exists():
                return
            capability_status.configure(text=str(message))
            capability_button.configure(state="normal")

        def discover_capabilities():
            capability_status.configure(text="Evren model erişimi sorgulanıyor; mikrofon açılmaz…")
            capability_button.configure(state="disabled")
            self.service.discover_voice_capabilities_async(capabilities_loaded, capabilities_error)

        capability_button = ctk.CTkButton(body, text="Evren model erişimini kontrol et", command=discover_capabilities)
        capability_button.grid(row=next_row, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        if not hasattr(self.service, "discover_voice_capabilities_async"):
            capability_button.configure(state="disabled")
        next_row += 1
        ctk.CTkCheckBox(body, text="Aç / bitir düğmesinde yerel VAD sohbetini tercih et", variable=vad_enabled,
                       command=lambda: provider_menus["interaction_mode"].set("conversation" if vad_enabled.get() else "push_to_talk")).grid(row=next_row, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        next_row += 1
        ctk.CTkLabel(body, text="Konuşma algılama").grid(row=next_row, column=0, sticky="w", padx=8)
        vad_engine = ctk.CTkOptionMenu(body, values=["energy", "webrtc"])
        vad_engine.set(settings.vad_engine)
        vad_engine.grid(row=next_row, column=1, sticky="ew", padx=8)
        ctk.CTkCheckBox(body, text="Yanıt cümleleri tamamlandıkça oku", variable=stream).grid(row=next_row + 1, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(body, text="WebRTC için isteğe bağlı voice-vad paketini kurun.\nAraç ve plan onayları mevcut onay ekranından verilir.\nUyandırma sözcüğü: yerel doğruluk doğrulanmadığı için devre dışı.", justify="left", wraplength=550).grid(row=next_row + 2, column=0, columnspan=2, sticky="w", padx=8, pady=10)

        def save():
            try:
                shortcut = entries["shortcut"].get().strip()
                # Ask Tk to parse the actual binding before persisting it.
                binding_id = dialog.bind(shortcut, lambda event: None, add="+")
                if binding_id:
                    dialog.unbind(shortcut, binding_id)
                values = dict(enabled=enabled.get(), read_replies=read.get(), stream_replies=stream.get(),
                    language={"Otomatik": "auto", "Türkçe": "tr", "İngilizce": "en"}[language.get()],
                    model=provider_menus["asr_model"].get(), max_record_seconds=int(entries["max_record_seconds"].get()),
                    asr_provider=provider_menus["asr_provider"].get(),
                    tts_provider=provider_menus["tts_provider"].get(), tts_model=provider_menus["tts_model"].get(),
                    tts_voice=provider_menus["tts_voice"].get(), tts_format=provider_menus["tts_format"].get(),
                    interaction_mode=provider_menus["interaction_mode"].get(),
                    vad_enabled=vad_enabled.get() or provider_menus["interaction_mode"].get() == "conversation",
                    playback_speed=float(entries["playback_speed"].get()),
                    input_device=selections["input"], output_device=selections["output"],
                    input_gain=float(entries["input_gain"].get()), output_volume=float(entries["output_volume"].get()),
                    vad_min_speech_ms=int(entries["vad_min_speech_ms"].get()),
                    vad_pre_roll_ms=int(entries["vad_pre_roll_ms"].get()), vad_engine=vad_engine.get(),
                    asr_request_seconds=int(entries["asr_request_seconds"].get()),
                    speech_rate=int(entries["speech_rate"].get()))
                additions = dict(auto_send=auto.get(), review_before_send=review.get(), barge_in=barge.get(),
                    shortcut=shortcut, vad_threshold=int(entries["vad_threshold"].get()),
                    vad_silence_ms=int(entries["vad_silence_ms"].get()))
                values.update({key: value for key, value in additions.items() if key in VoiceSettings.__dataclass_fields__})
                validated = replace(settings, **values)
                self.service.update_voice_settings(VoiceSettings.from_mapping(validated.to_dict()))
            except (ValueError, OSError, tk.TclError) as exc:
                messagebox.showerror("Ses ayarları", f"Ayarlar kaydedilemedi: {exc}", parent=dialog)
                return
            dialog.destroy()
            self._dialog = None
            self.cancel()
            self.dismiss_command(resolve=False)
            self._bind_shortcut()

        ctk.CTkButton(dialog, text="Kaydet", command=save).grid(row=1, column=0, pady=(0, 10))
