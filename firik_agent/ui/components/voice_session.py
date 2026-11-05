"""A session-bound assistant console; audio and execution remain with its owner."""
from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk

from firik_agent.ui.components.voice_orb import VoiceOrb
from firik_agent.ui.theme import THEME_COLORS
from firik_agent.voice.commands import command_examples


def color(name):
    return THEME_COLORS["light"][name], THEME_COLORS["dark"][name]


STATE_LABELS = {
    "disabled": "Ses kapalı", "idle": "Hazırım", "ready": "Hazırım",
    "listening": "Dinliyorum", "recording": "Sizi dinliyorum",
    "speech_detected": "Sizi dinliyorum", "transcribing": "Sözlerinizi çözümlüyorum",
    "awaiting_confirmation": "Göndermeye hazır", "thinking": "Düşünüyorum",
    "executing": "Görevinizle ilgileniyorum", "speaking": "Yanıtlıyorum",
    "interrupted": "Dinleme duraklatıldı", "permission_required": "Mikrofon izni gerekiyor",
    "error": "Bir sorun oluştu",
}
STATE_HINTS = {
    "listening": "Bir soru sorun veya ne yapmamı istediğinizi söyleyin.",
    "recording": "Bitirmek için mikrofon düğmesine basın veya kısayolu bırakın.",
    "speech_detected": "Konuşmanız tamamlandığında metin burada görünür.",
    "transcribing": "Konuşmanız metne dönüştürülüyor; mikrofon kapalı.",
    "awaiting_confirmation": "Metni düzenleyip Gönder ile iletebilirsiniz.",
    "thinking": "İsteğiniz mevcut sohbetinizde değerlendiriliyor.",
    "executing": "İlerlemeyi buradan izleyebilir, görevi durdurabilirsiniz.",
    "speaking": "Dinlemeye dönmek için sesi durdurabilirsiniz.",
    "interrupted": "Devam etmek için mikrofonu açın.",
    "permission_required": "Mikrofon iznini verin veya metinle devam edin.",
    "error": "Aşağıdaki açıklamayı inceleyin; metinle de devam edebilirsiniz.",
}


class VoiceSessionPanel(ctk.CTkToplevel):
    """State, progress, discoverable commands and review in one native surface."""

    def __init__(self, owner):
        self._theme_restore_job = None
        super().__init__(owner)
        self.owner = owner
        self._closed = False
        self._pending = False
        self._error_reveal_job = None
        self._layout_job = None
        self._state = "ready"
        self._preview_text = ""
        self._commands_open = True
        self._compact = None
        self._recording = self._conversation = self._paused = False
        self.title("Firik · Sesli asistan")
        width = min(620, max(360, self.winfo_screenwidth() - 80))
        height = min(800, max(540, self.winfo_screenheight() - 100))
        self.geometry(f"{width}x{height}")
        self.minsize(360, 500)
        self.transient(owner.winfo_toplevel())
        self.configure(fg_color=color("bg_primary"))
        self.protocol("WM_DELETE_WINDOW", owner.exit_conversation)
        self.bind("<Escape>", lambda event: owner.exit_conversation())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="F I R I K", anchor="w", font=ctk.CTkFont(size=23, weight="bold"),
                     text_color=color("text_primary")).grid(row=0, column=0, sticky="w")
        self.settings_button = ctk.CTkButton(header, text="Ses ayarları", width=88, height=28,
            command=owner.open_settings, fg_color="transparent", text_color=color("text_secondary"),
            hover_color=color("bg_hover"))
        self.settings_button.grid(row=0, column=1)
        self.context_label = ctk.CTkLabel(header, text="", anchor="w", justify="left", font=ctk.CTkFont(size=11), text_color=color("text_secondary"))
        self.context_label.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._update_context()

        stage = ctk.CTkFrame(self, fg_color="transparent")
        stage.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        stage.grid_columnconfigure(0, weight=1)
        self.orb = VoiceOrb(stage, motion=owner._voice_motion_enabled)
        self.orb.grid(row=0, column=0)
        self.state_label = ctk.CTkLabel(stage, text="Hazırım", font=ctk.CTkFont(size=23, weight="bold"),
                                       text_color=color("text_primary"))
        self.state_label.grid(row=1, column=0, sticky="ew")
        self.live_text_note = ctk.CTkLabel(stage, text="Konuşmanız tamamlandığında metin burada görünür.",
            justify="center", wraplength=520, font=ctk.CTkFont(size=12), text_color=color("text_secondary"))
        self.live_text_note.grid(row=2, column=0, sticky="ew", pady=(2, 6))
        meter = ctk.CTkFrame(stage, fg_color="transparent")
        meter.grid(row=3, column=0)
        self.recording_indicator = ctk.CTkLabel(meter, text="●  Mikrofon kapalı", font=ctk.CTkFont(size=11), text_color=color("text_secondary"))
        self.recording_indicator.grid(row=0, column=0, padx=(0, 10))
        self.level = ctk.CTkProgressBar(meter, width=62, height=4, progress_color=color("accent_secondary"))
        self.level.set(0)
        self.level.grid(row=0, column=1)
        self.level_label = ctk.CTkLabel(meter, text="%0", width=34, font=ctk.CTkFont(size=10), text_color=color("text_secondary"))
        self.level_label.grid(row=0, column=2, padx=(5, 0))

        self.body = body = ctk.CTkScrollableFrame(self, fg_color=color("bg_primary"), corner_radius=0)
        body.grid(row=2, column=0, sticky="nsew", padx=18)
        body.grid_columnconfigure(0, weight=1)
        tools = ctk.CTkFrame(body, fg_color="transparent")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        tools.grid_columnconfigure(0, weight=1)
        self.command_toggle = ctk.CTkButton(tools, text="Sesle neler yapabilirim?  −", anchor="w", height=28,
            fg_color="transparent", text_color=color("text_secondary"), hover_color=color("bg_hover"), command=self.toggle_commands)
        self.command_toggle.grid(row=0, column=0, sticky="ew")
        self.motion_button = ctk.CTkButton(tools, text="Hareketi azalt", width=104, height=28,
            fg_color="transparent", text_color=color("text_secondary"), hover_color=color("bg_hover"), command=self.toggle_motion)
        self.motion_button.grid(row=0, column=1)
        self.commands_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.commands_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.commands_frame.grid_columnconfigure((0, 1), weight=1, uniform="commands")
        self.command_buttons = []
        for index, (label, phrase) in enumerate(command_examples(owner.target)):
            button = ctk.CTkButton(self.commands_frame, text=label, width=100, height=32,
                font=ctk.CTkFont(size=12), fg_color=color("bg_secondary"), text_color=color("text_primary"),
                border_width=1, border_color=color("border"), hover_color=color("bg_hover"),
                command=lambda text=phrase: owner.stage_prompt(text))
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 6) if index % 2 == 0 else 0, pady=3)
            self.command_buttons.append(button)

        self.transcript_heading = ctk.CTkLabel(body, text="SİZ", anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=color("accent_secondary"))
        self.transcript_heading.grid(row=2, column=0, sticky="ew", pady=(6, 3))
        self.transcript = ctk.CTkTextbox(body, height=86, wrap="word", font=ctk.CTkFont(size=14),
            fg_color=color("bg_secondary"), border_width=1, corner_radius=12, border_color=color("border"))
        self.transcript.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        self.transcript.configure(state="disabled")
        self.transcript.bind("<Control-Return>", lambda event: (self.send(), "break")[1])
        self.transcript.bind("<Command-Return>", lambda event: (self.send(), "break")[1])
        self.transcript.bind("<KeyRelease>", lambda event: self._sync_draft())
        from firik_agent.ui.components.keyboard_focus import reveal_in_scroll_frame
        self.transcript.bind("<FocusIn>", lambda event: reveal_in_scroll_frame(self.transcript))
        self.review_actions = actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew")
        actions.grid_columnconfigure((0, 1, 2), weight=1, uniform="review")
        self.send_button = ctk.CTkButton(actions, text="Gönder  ↗", width=70, height=34, command=self.send,
                                        fg_color=color("accent"), hover_color=color("accent_hover"))
        self.retry_button = self._secondary(actions, "Yeniden kaydet", owner.retry_transcript)
        self.discard_button = self._secondary(actions, "Vazgeç", owner.dismiss_command)
        for column, button in enumerate((self.send_button, self.retry_button, self.discard_button)):
            button.grid(row=0, column=column, sticky="ew", padx=(0, 5) if column < 2 else 0)
        self.error_label = ctk.CTkLabel(body, text="", wraplength=500, anchor="w", justify="left", text_color=color("danger"))
        self.error_label.grid(row=5, column=0, sticky="ew", pady=5)
        self.error_label.grid_remove()
        self.activity_label = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=500,
            font=ctk.CTkFont(size=11), text_color=color("text_secondary"))
        self.activity_label.grid(row=6, column=0, sticky="ew", pady=(10, 3))
        self.activity_label.grid_remove()
        self.response_heading = ctk.CTkLabel(body, text="FIRIK", anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=color("accent_secondary"))
        self.response_heading.grid(row=7, column=0, sticky="ew", pady=(10, 4))
        self.preview = ctk.CTkLabel(body, text="", anchor="nw", justify="left", wraplength=500,
                                  font=ctk.CTkFont(size=14), text_color=color("text_primary"))
        self.preview.grid(row=8, column=0, sticky="ew", pady=(0, 10))
        self.playback_controls = ctk.CTkFrame(body, fg_color="transparent")
        self.playback_controls.grid(row=9, column=0, sticky="ew", pady=(0, 6))
        self.playback_controls.grid_columnconfigure((0, 1), weight=1)
        self.pause_speech_button = self._secondary(self.playback_controls, "Sesi duraklat", owner.toggle_speech_pause)
        self.replay_button = self._secondary(self.playback_controls, "Yeniden dinle", owner.replay_speech)
        self.pause_speech_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.replay_button.grid(row=0, column=1, sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=22, pady=(8, 12))
        footer.grid_columnconfigure((0, 1, 2), weight=1, uniform="footer")
        self.mic_mute_button = ctk.CTkButton(footer, text="Mikrofonu kapat", width=90, height=34,
            font=ctk.CTkFont(size=12), fg_color=color("accent"), hover_color=color("accent_hover"), command=self.toggle_microphone)
        self.stop_speech_button = self._secondary(footer, "Sesi durdur", owner.stop_speaking)
        self.stop_agent_button = self._secondary(footer, "Görevi durdur", owner.stop_agent)
        self.stop_agent_button.configure(text_color=color("danger"))
        self.listen_button = self._secondary(footer, "Dinlemeyi durdur", owner.stop_listening)
        self.text_button = self._secondary(footer, "Metinle devam et", owner.use_text_input)
        self.exit_button = self._secondary(footer, "Ses modundan çık", owner.exit_conversation)
        for i, button in enumerate((self.mic_mute_button, self.stop_speech_button, self.stop_agent_button,
                                    self.listen_button, self.text_button, self.exit_button)):
            button.grid(row=i // 3, column=i % 3, sticky="ew", padx=(0, 5) if i % 3 < 2 else 0, pady=3)
        for button in (self.send_button, self.retry_button, self.discard_button, self.mic_mute_button,
                       self.listen_button, self.stop_speech_button, self.stop_agent_button, self.pause_speech_button,
                       self.replay_button, self.settings_button, self.text_button, self.exit_button,
                       self.command_toggle, self.motion_button, *self.command_buttons):
            owner._make_accessible(button)
        self.set_review_enabled(False)
        self.set_preview("")
        self._update_motion_label()
        self.bind("<Configure>", self._resize, add="+")
        self._layout_job = self.after_idle(self._resize)

    @staticmethod
    def _secondary(master, text, command):
        return ctk.CTkButton(master, text=text, command=command, width=90, height=32,
            font=ctk.CTkFont(size=11), fg_color="transparent", border_width=1,
            border_color=color("border"), text_color=color("text_primary"), hover_color=color("bg_hover"))

    def _resize(self, event=None):
        if event is None:
            self._layout_job = None
        if self._closed or (event is not None and event.widget is not self):
            return
        width = max(230, self.winfo_width() - 70)
        for label in (self.context_label, self.state_label, self.live_text_note, self.preview, self.error_label, self.activity_label):
            label.configure(wraplength=width)
        compact = self.winfo_height() < 680
        if compact != self._compact:
            self._compact = compact
            self.orb.configure(height=116 if compact else 182)

    def update_state(self, state, *, recording=False, paused=False, conversation=False):
        self._state = state
        self._recording, self._conversation, self._paused = recording, conversation, paused
        self._update_context()
        self.state_label.configure(text=STATE_LABELS.get(state, state))
        self.live_text_note.configure(text=STATE_HINTS.get(state, "Konuşmanız tamamlandığında metin burada görünür."))
        self.orb.set_state("interrupted" if paused and not recording and state not in {"thinking", "executing", "speaking"} else state)
        self.recording_indicator.configure(text="●  Mikrofon açık" if recording else "●  Mikrofon kapalı",
            text_color=color("accent_secondary") if recording else color("text_secondary"))
        self._update_microphone()
        self.listen_button.configure(state="normal" if recording or conversation else "disabled")
        self.stop_agent_button.configure(state="normal" if self.owner.is_busy() else "disabled")
        self.pause_speech_button.configure(text="Sese devam et" if self.owner._speech_paused else "Sesi duraklat",
                                          state="normal" if state == "speaking" else "disabled")
        self.replay_button.configure(state="normal" if getattr(self.owner.controller, "can_replay_speech", False) else "disabled")
        for button in (self.mic_mute_button, self.listen_button, self.stop_agent_button, self.pause_speech_button, self.replay_button):
            button._canvas.configure(takefocus=int(button.cget("state") == "normal"))
        if state in {"thinking", "executing"} and not self._preview_text:
            self.preview.configure(text="Yanıtınız burada belirecek…")
            self.response_heading.grid()
            self.preview.grid()
        if not recording:
            self.update_level(0)

    def update_level(self, value):
        value = max(0., min(1., float(value)))
        self.level.set(value)
        self.level_label.configure(text=f"%{round(value * 100)}")
        self.orb.set_level(value)

    def set_transcript(self, text, *, pending):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", str(text))
        self.set_review_enabled(pending)

    def set_review_enabled(self, pending):
        self._pending = pending
        self._update_microphone()
        self.transcript.configure(state="normal" if pending else "disabled")
        self.transcript_heading.configure(text="SİZ · DÜZENLEYİP GÖNDERİN" if pending else "SİZ")
        if self.transcript.get("1.0", "end-1c") or pending:
            self.transcript.grid()
            self.transcript_heading.grid()
        else:
            self.transcript.grid_remove()
            self.transcript_heading.grid_remove()
        if pending:
            self.review_actions.grid()
            self._commands_open = False
            self._update_commands()
        else:
            self.review_actions.grid_remove()
        for button in (self.send_button, self.retry_button, self.discard_button):
            button.configure(state="normal" if pending else "disabled")
            button._canvas.configure(takefocus=int(pending))

    def mark_transcript_sent(self):
        self.set_review_enabled(False)
        self.transcript_heading.configure(text="SİZ · GÖNDERİLDİ")

    def _update_context(self):
        from firik_agent.projects.indexer import sanitize_secrets
        from firik_agent.voice.speech import speech_text
        label = speech_text(sanitize_secrets(str(self.owner.get_context_label())))[:100]
        target = "Kodlama" if self.owner.target == "coding" else "Sohbet"
        self.context_label.configure(text=f"{target} · {label}")

    def _update_microphone(self):
        text = ("Mikrofonu aç" if self._paused else "Mikrofonu kapat") if self._conversation else ("Kaydı bitir" if self._recording else "Konuş")
        enabled = not self._pending and self.owner._settings().enabled
        self.mic_mute_button.configure(text=text, state="normal" if enabled else "disabled")
        self.mic_mute_button._canvas.configure(takefocus=int(enabled))

    def toggle_microphone(self):
        if self._conversation:
            self.owner.toggle_pause()
        else:
            self.owner.toggle_recording()

    def _sync_draft(self):
        if self._pending and self.owner._pending:
            self.owner.command_text.delete(0, "end")
            self.owner.command_text.insert(0, self.transcript.get("1.0", "end-1c"))

    def send(self):
        self._sync_draft()
        self.owner.apply_transcript()

    def set_preview(self, text):
        self._preview_text = str(text)[:8000]
        self.preview.configure(text=self._preview_text)
        if self._preview_text:
            self.response_heading.grid()
            self.preview.grid()
            self.playback_controls.grid()
        else:
            self.response_heading.grid_remove()
            self.preview.grid_remove()
            self.playback_controls.grid_remove()

    def set_activity(self, text):
        self.activity_label.configure(text=str(text))
        if text:
            self.activity_label.grid()
        else:
            self.activity_label.grid_remove()
        self.stop_agent_button.configure(state="normal" if self.owner.is_busy() else "disabled")
        self.stop_agent_button._canvas.configure(takefocus=int(self.owner.is_busy()))

    def toggle_commands(self):
        self._commands_open = not self._commands_open
        self._update_commands()

    def _update_commands(self):
        self.command_toggle.configure(text="Sesle neler yapabilirim?  " + ("−" if self._commands_open else "+"))
        if self._commands_open:
            self.commands_frame.grid()
        else:
            self.commands_frame.grid_remove()

    def toggle_motion(self):
        self.owner._voice_motion_enabled = not self.owner._voice_motion_enabled
        self.orb.set_motion(self.owner._voice_motion_enabled)
        self._update_motion_label()

    def _update_motion_label(self):
        self.motion_button.configure(text="Hareketi azalt" if self.owner._voice_motion_enabled else "Hareketi aç")

    def show_error(self, message):
        self.error_label.configure(text=str(message))
        if self._error_reveal_job is not None:
            self.after_cancel(self._error_reveal_job)
            self._error_reveal_job = None
        if message:
            self.error_label.grid()
            self._error_reveal_job = self.after_idle(self._reveal_error)
        else:
            self.error_label.grid_remove()

    def _reveal_error(self):
        self._error_reveal_job = None
        if not self._closed:
            from firik_agent.ui.components.keyboard_focus import reveal_in_scroll_frame
            reveal_in_scroll_frame(self.error_label)

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if sys.platform == "win32" and not getattr(self, "_closed", True):
            if self._theme_restore_job is not None:
                self.after_cancel(self._theme_restore_job)
            self._theme_restore_job = self.after(50, self._restore_after_theme)

    def _restore_after_theme(self):
        self._theme_restore_job = None
        if (not self._closed and self.state() == "withdrawn"
                and self.owner.winfo_toplevel().state() in {"normal", "zoomed"}):
            self.deiconify()

    def destroy(self):
        self._closed = True
        for job in (self._error_reveal_job, self._theme_restore_job, self._layout_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._error_reveal_job = self._theme_restore_job = self._layout_job = None
        self.orb.destroy()
        super().destroy()
