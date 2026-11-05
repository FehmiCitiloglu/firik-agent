"""A lightweight, session-bound voice surface using the desktop's own palette."""
from __future__ import annotations

import tkinter as tk
import sys
import customtkinter as ctk

from firik_agent.ui.theme import THEME_COLORS


def color(name):
    return THEME_COLORS["light"][name], THEME_COLORS["dark"][name]


STATE_LABELS = {
    "disabled": "Ses kapalı", "idle": "Hazır", "ready": "Hazır",
    "listening": "Dinleniyor", "recording": "Kayıt yapılıyor",
    "speech_detected": "Konuşma algılandı", "transcribing": "Metne dönüştürülüyor",
    "awaiting_confirmation": "Transkripti inceleyin", "thinking": "Yanıt hazırlanıyor",
    "executing": "Ajan çalışıyor", "speaking": "Yanıt okunuyor",
    "interrupted": "Dinleme duraklatıldı", "permission_required": "Mikrofon izni gerekiyor",
    "error": "Ses işlemi tamamlanamadı",
}


class VoiceSessionPanel(ctk.CTkToplevel):
    """All actions delegate to the existing panel/controller; no new session."""

    def __init__(self, owner):
        self._theme_restore_job = None
        super().__init__(owner)
        self.owner = owner
        self._closed = False
        self._pending = False
        self.title("Sesli sohbet · Firik")
        width = min(560, max(360, self.winfo_screenwidth() - 80))
        height = min(700, max(540, self.winfo_screenheight() - 100))
        self.geometry(f"{width}x{height}")
        self.minsize(360, 500)
        self.transient(owner.winfo_toplevel())
        self.configure(fg_color=color("bg_primary"))
        self.protocol("WM_DELETE_WINDOW", owner.exit_conversation)
        self.bind("<Escape>", lambda event: owner.exit_conversation())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        # ScrollableFrame wraps a native Tk Frame. Give descendants the full
        # palette so their background also follows runtime theme changes.
        self.body = body = ctk.CTkScrollableFrame(self, fg_color=color("bg_primary"))
        body.grid(row=0, column=0, sticky="nsew", padx=14, pady=(12, 4))
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(body, text="Sesli sohbet", anchor="w", font=ctk.CTkFont(size=21, weight="bold"),
                     text_color=color("text_primary")).grid(row=0, column=0, sticky="ew", pady=(0, 2))
        target = "Mevcut kodlama oturumu" if owner.target == "coding" else "Mevcut sohbet oturumu"
        ctk.CTkLabel(body, text=target, anchor="w", text_color=color("text_secondary")).grid(row=1, column=0, sticky="ew")
        status_card = ctk.CTkFrame(body, fg_color=color("bg_secondary"), border_width=1,
                                  border_color=color("border"), corner_radius=12)
        status_card.grid(row=2, column=0, sticky="ew", pady=12)
        status_card.grid_columnconfigure(0, weight=1)
        self.recording_indicator = ctk.CTkLabel(status_card, text="●  Mikrofon kapalı", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=color("text_secondary"))
        self.recording_indicator.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self.state_label = ctk.CTkLabel(status_card, text="Hazır", anchor="w", font=ctk.CTkFont(size=20, weight="bold"))
        self.state_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 8))
        self.level = ctk.CTkProgressBar(status_card, height=9, progress_color=color("accent"))
        self.level.set(0)
        self.level.grid(row=2, column=0, sticky="ew", padx=14)
        self.level_label = ctk.CTkLabel(status_card, text="Giriş düzeyi · %0", anchor="w", text_color=color("text_secondary"))
        self.level_label.grid(row=3, column=0, sticky="ew", padx=14, pady=(3, 10))
        self.live_text_note = ctk.CTkLabel(body, text="Bu ses çözümleyici canlı metin sunmuyor.\nTranskript, sözünüz tamamlandığında görünür.",
            justify="left", anchor="w", wraplength=470, text_color=color("text_secondary"))
        self.live_text_note.grid(row=3, column=0, sticky="ew", pady=(0, 9))
        self.transcript_heading = ctk.CTkLabel(body, text="Transkript", anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        self.transcript_heading.grid(row=4, column=0, sticky="ew")
        self.transcript = ctk.CTkTextbox(body, height=90, wrap="word", font=ctk.CTkFont(size=13),
            fg_color=color("bg_secondary"), border_width=1, border_color=color("border"))
        self.transcript.grid(row=5, column=0, sticky="ew", pady=(4, 7))
        self.transcript.configure(state="disabled")
        self.transcript.bind("<Control-Return>", lambda event: (self.send(), "break")[1])
        self.transcript.bind("<Command-Return>", lambda event: (self.send(), "break")[1])
        self.transcript.bind("<KeyRelease>", lambda event: self._sync_draft())
        from firik_agent.ui.components.keyboard_focus import reveal_in_scroll_frame
        self.transcript.bind("<FocusIn>", lambda event: reveal_in_scroll_frame(self.transcript))
        self.review_actions = actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=6, column=0, sticky="ew")
        for column in range(3):
            actions.grid_columnconfigure(column, weight=1)
        self.send_button = ctk.CTkButton(actions, text="Gönder", width=80, command=self.send)
        self.retry_button = ctk.CTkButton(actions, text="Yeniden kaydet", width=100, command=owner.retry_transcript)
        self.discard_button = ctk.CTkButton(actions, text="Vazgeç", width=80, command=owner.dismiss_command)
        for column, button in enumerate((self.send_button, self.retry_button, self.discard_button)):
            button.grid(row=0, column=column, sticky="ew", padx=(0, 5) if column < 2 else 0)
        ctk.CTkLabel(body, text="Asistan yanıtı", anchor="w", font=ctk.CTkFont(size=13, weight="bold")).grid(row=7, column=0, sticky="ew", pady=(14, 4))
        self.preview = ctk.CTkLabel(body, text="Yanıt burada kısaca görünür.", anchor="nw", justify="left",
            wraplength=470, text_color=color("text_secondary"))
        self.preview.grid(row=8, column=0, sticky="ew", pady=(0, 10))
        controls = ctk.CTkFrame(body, fg_color="transparent")
        controls.grid(row=9, column=0, sticky="ew")
        controls.grid_columnconfigure((0, 1), weight=1)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=18, pady=(5, 12))
        footer.grid_columnconfigure((0, 1), weight=1)
        self.mic_mute_button = ctk.CTkButton(footer, text="Mikrofonu sustur", command=owner.toggle_pause)
        self.listen_button = ctk.CTkButton(footer, text="Dinlemeyi durdur", command=owner.stop_listening)
        self.stop_speech_button = ctk.CTkButton(footer, text="Konuşmayı durdur", command=owner.stop_speaking)
        for i, button in enumerate((self.mic_mute_button, self.listen_button, self.stop_speech_button)):
            button.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 5) if i % 2 == 0 else 0, pady=3)
        self.pause_speech_button = ctk.CTkButton(controls, text="Sesi duraklat", command=owner.toggle_speech_pause)
        self.replay_button = ctk.CTkButton(controls, text="Yeniden dinle", command=owner.replay_speech)
        self.settings_button = ctk.CTkButton(controls, text="Ses ayarları", command=owner.open_settings)
        for i, button in enumerate((self.pause_speech_button, self.replay_button, self.settings_button)):
            button.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 5) if i % 2 == 0 else 0, pady=3)
        self.error_label = ctk.CTkLabel(body, text="", wraplength=470, anchor="w", justify="left", text_color=color("danger"))
        self.error_label.grid(row=10, column=0, sticky="ew", pady=5)
        self.text_button = ctk.CTkButton(body, text="Metinle devam et", command=owner.use_text_input,
            fg_color="transparent", border_width=1, text_color=color("text_primary"))
        self.text_button.grid(row=11, column=0, sticky="ew", pady=(2, 8))
        self.exit_button = ctk.CTkButton(footer, text="Ses modundan çık", command=owner.exit_conversation,
            fg_color="transparent", border_width=1, text_color=color("text_primary"))
        self.exit_button.grid(row=1, column=1, sticky="ew", pady=3)
        for button in (self.send_button, self.retry_button, self.discard_button, self.mic_mute_button,
                       self.listen_button, self.stop_speech_button, self.pause_speech_button, self.replay_button,
                       self.settings_button, self.text_button, self.exit_button):
            owner._make_accessible(button)
        self.set_review_enabled(False)
        self.bind("<Configure>", self._resize, add="+")
        self._layout_job = self.after_idle(self._resize)

    def _resize(self, event=None):
        if not self._closed and (event is None or event.widget is self):
            width = max(220, self.winfo_width() - 65)
            for label in (self.live_text_note, self.preview, self.error_label):
                label.configure(wraplength=width)

    def update_state(self, state, *, recording=False, paused=False, conversation=False):
        self.state_label.configure(text=STATE_LABELS.get(state, state))
        self.recording_indicator.configure(text="●  Mikrofon açık · Kayıt alınıyor" if recording else "●  Mikrofon kapalı",
            text_color=color("danger") if recording else color("text_secondary"))
        self.mic_mute_button.configure(text="Mikrofonu aç" if paused else "Mikrofonu sustur",
                                       state="normal" if conversation else "disabled")
        self.listen_button.configure(state="normal" if recording or conversation else "disabled")
        self.pause_speech_button.configure(text="Sese devam et" if self.owner._speech_paused else "Sesi duraklat",
                                          state="normal" if state == "speaking" else "disabled")
        self.replay_button.configure(state="normal" if getattr(self.owner.controller, "can_replay_speech", False) else "disabled")
        for button in (self.mic_mute_button, self.listen_button, self.pause_speech_button, self.replay_button):
            button._canvas.configure(takefocus=int(button.cget("state") == "normal"))
        if not recording:
            self.update_level(0)

    def update_level(self, value):
        value = max(0., min(1., float(value)))
        self.level.set(value)
        self.level_label.configure(text=f"Giriş düzeyi · %{round(value * 100)}")

    def set_transcript(self, text, *, pending):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", str(text))
        self.set_review_enabled(pending)

    def set_review_enabled(self, pending):
        self._pending = pending
        self.transcript.configure(state="normal" if pending else "disabled")
        self.transcript_heading.configure(text="Transkripti düzenleyip gönderin" if pending else "Son transkript")
        if self.transcript.get("1.0", "end-1c") or pending:
            self.transcript.grid()
        else:
            self.transcript.grid_remove()
            self.transcript_heading.configure(text="Henüz transkript yok")
        if pending:
            self.review_actions.grid()
        else:
            self.review_actions.grid_remove()
        for button in (self.send_button, self.retry_button, self.discard_button):
            button.configure(state="normal" if pending else "disabled")
            button._canvas.configure(takefocus=int(pending))

    def mark_transcript_sent(self):
        self.set_review_enabled(False)
        self.transcript_heading.configure(text="Gönderilen transkript")

    def _sync_draft(self):
        if self._pending and self.owner._pending:
            self.owner.command_text.delete(0, "end")
            self.owner.command_text.insert(0, self.transcript.get("1.0", "end-1c"))

    def send(self):
        self._sync_draft()
        self.owner.apply_transcript()

    def set_preview(self, text):
        self.preview.configure(text=str(text)[:480] or "Yanıt burada kısaca görünür.")

    def show_error(self, message):
        self.error_label.configure(text=str(message))

    def _apply_appearance_mode(self, mode_string):
        super()._apply_appearance_mode(mode_string)
        if sys.platform == "win32" and not getattr(self, "_closed", True):
            if self._theme_restore_job is not None:
                self.after_cancel(self._theme_restore_job)
            self._theme_restore_job = self.after(50, self._restore_after_theme)

    def _restore_after_theme(self):
        self._theme_restore_job = None
        # CTk withdraws the owner to recolor its Windows titlebar. A transient
        # can inherit that hidden state while its own theme callback runs.
        # Restore only this live surface, after the owner is visible again.
        if (not self._closed and self.state() == "withdrawn"
                and self.owner.winfo_toplevel().state() in {"normal", "zoomed"}):
            self.deiconify()

    def destroy(self):
        self._closed = True
        if self._theme_restore_job is not None:
            self.after_cancel(self._theme_restore_job)
            self._theme_restore_job = None
        if self._layout_job is not None:
            try:
                self.after_cancel(self._layout_job)
            except tk.TclError:
                pass
            self._layout_job = None
        super().destroy()
