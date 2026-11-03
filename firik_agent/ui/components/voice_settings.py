"""Reusable desktop voice preferences; microphone access is always explicit."""
from __future__ import annotations

from dataclasses import replace
import json
import re
import sys
import tkinter as tk

import customtkinter as ctk

from firik_agent.projects.indexer import sanitize_secrets
from firik_agent.voice.config import VoiceSettings


_MUTED = ("#475569", "#94a3b8")
_CARD = ("#ffffff", "#161f2e")
_BORDER = ("#cbd5e1", "#334155")
_LANGUAGES = {"Otomatik": "auto", "Türkçe": "tr", "İngilizce": "en"}
_MODES = {"Bas-konuş": "push_to_talk", "Sesli sohbet": "conversation"}


def _safe_message(value, limit=350):
    text = sanitize_secrets(str(value or ""))
    text = re.sub(r"(?i)\bhttps?://\S+", "", text)
    return text[:limit] or "Ses işlemi tamamlanamadı. Metinle devam edebilirsiniz."


def make_voice_controls_accessible(parent, *, include_self=False):
    """Give canvas controls tab focus; include_self scopes this to one control."""
    controls = []
    for child in ([parent] if include_self else parent.winfo_children()):
        focus_target = None
        if isinstance(child, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkOptionMenu)):
            controls.append(child)
            canvas = child._canvas
            focus_target = canvas
            canvas.configure(takefocus=0 if child.cget("state") == "disabled" else 1,
                             highlightthickness=2, highlightcolor="#2563eb",
                             highlightbackground=canvas.cget("bg"))
            def activate(event, control=child):
                if control.cget("state") != "disabled":
                    if isinstance(control, ctk.CTkOptionMenu):
                        control._clicked()
                    elif isinstance(control, ctk.CTkCheckBox):
                        control.toggle()
                    else:
                        control.invoke()
                return "break"
            def step(event, control=child):
                if control.cget("state") != "disabled":
                    values = control.cget("values")
                    if values:
                        position = values.index(control.get()) if control.get() in values else 0
                        value = values[(position + (1 if event.keysym == "Down" else -1)) % len(values)]
                        control._dropdown_callback(value)
                return "break"
            if not getattr(child, "_voice_keyboard_bound", False):
                canvas.bind("<space>", activate, add="+")
                canvas.bind("<Return>", activate, add="+")
                if isinstance(child, ctk.CTkOptionMenu):
                    canvas.bind("<Down>", step, add="+")
                    canvas.bind("<Up>", step, add="+")
                child._voice_keyboard_bound = True
        elif isinstance(child, ctk.CTkEntry):
            focus_target = child._entry
        elif isinstance(child, ctk.CTkTextbox):
            focus_target = child._textbox
        if focus_target is not None and not getattr(child, "_voice_scroll_focus_bound", False):
            from firik_agent.ui.components.keyboard_focus import reveal_in_scroll_frame
            focus_target.bind("<FocusIn>", lambda event, control=child: reveal_in_scroll_frame(control), add="+")
            child._voice_scroll_focus_bound = True
        controls.extend(make_voice_controls_accessible(child))
    return controls


def refresh_voice_focus_colors(controls, apply_mode):
    """Update native focus canvases alongside CustomTkinter theme colors."""
    for control in controls:
        try:
            control._canvas.configure(highlightbackground=apply_mode(control.cget("bg_color")),
                                       highlightcolor="#2563eb")
        except tk.TclError:
            pass


class SettingsScrollFrame(ctk.CTkScrollableFrame):
    """Restack existing embedded content when a settings tab is remapped.

    On macOS Tk, a remapped canvas can obscure its still-mapped child frame.
    Geometry and yview remain correct, so restore the native child ordering
    after mapping settles instead of recreating controls or losing edits.
    """
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._restore_job = None
        self._closing = False
        self._parent_canvas.bind("<Map>", self._schedule_restore, add="+")

    def _schedule_restore(self, event=None):
        if not self._closing and self._restore_job is None:
            self._restore_job = self.after_idle(self._restore_content)

    def _restore_content(self):
        self._restore_job = None
        if not self._closing and self.winfo_ismapped() and self._parent_canvas.winfo_ismapped():
            self.lift()

    def destroy(self):
        self._closing = True
        if self._restore_job is not None:
            try:
                self.after_cancel(self._restore_job)
            except tk.TclError:
                pass
            self._restore_job = None
        super().destroy()


class VoiceSettingsFrame(ctk.CTkFrame):
    """Shared Settings/Ses form and legacy modal contents.

    Async service callbacks are delivered by the existing Tk dispatch bridge.
    This form never joins a native worker, opens a stream itself or performs an
    implicit microphone test. ``on_saved`` receives validated VoiceSettings.
    """

    def __init__(self, master, service, *, on_saved=None, show_save=True, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service, self.on_saved = service, on_saved
        self._closed = False
        self._input_test = None
        self._test_generation = 0
        self._device_generation = 0
        self._devices_requested = False
        self._capabilities = {}
        self._labels = []
        self._settings = getattr(service, "voice_settings", VoiceSettings())
        if not isinstance(self._settings, VoiceSettings):
            self._settings = VoiceSettings()
        self.vars = {name: tk.BooleanVar(master=self, value=getattr(self._settings, name, False))
                     for name in ("enabled", "read_replies", "auto_send", "review_before_send", "stream_replies",
                                  "vad_enabled", "completion_chime")}
        self.entries, self.menus = {}, {}
        self._device_choices = {kind: {"Sistem varsayılanı": None} for kind in ("input", "output")}
        self._device_selection = {"input": self._settings.input_device, "output": self._settings.output_device}
        self._rows = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=_CARD, corner_radius=10)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.grid_columnconfigure(0, weight=1)
        self._label(header, "Ses", bold=True).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        self.enabled_control = ctk.CTkCheckBox(header, text="Ses özelliklerini etkinleştir", variable=self.vars["enabled"])
        self.enabled_control.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 12))
        self.tabs = ctk.CTkTabview(self, fg_color=_CARD, border_width=1, border_color=_BORDER,
                                  command=self._form_tab_changed)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        self.pages = {}
        for title in ("Giriş", "Yanıt", "Gizlilik ve yardım"):
            tab = self.tabs.add(title)
            body = SettingsScrollFrame(tab, fg_color=_CARD)
            body.pack(fill="both", expand=True)
            body.grid_columnconfigure(0, weight=1)
            self.pages[title] = body
            self._rows[title] = 0
        self._build_input()
        self._build_output()
        self._build_privacy()
        self.status = ctk.CTkLabel(self, text="Ayarları kaydetmek kayıt başlatmaz.", anchor="w", justify="left",
                                   wraplength=430, text_color=_MUTED)
        self.status.grid(row=2, column=0, sticky="ew", padx=4, pady=(8, 4))
        self.reload_button = ctk.CTkButton(self, text="Kaydedilen ses ayarlarını yeniden yükle",
                                           command=lambda: self.reload_settings(force=True), height=30)
        self.reload_button.grid(row=3, column=0, sticky="ew", pady=4)
        self.reload_button.grid_remove()
        if show_save:
            self.save_button = ctk.CTkButton(self, text="Ses ayarlarını kaydet", command=self.save, height=34)
            self.save_button.grid(row=4, column=0, sticky="e", pady=(2, 8))
        self._provider_changed()
        self._vad_changed()
        self._keyboard_controls = make_voice_controls_accessible(self)
        self._baseline_values = self._form_values()
        listen = getattr(self.service, "add_voice_settings_listener", None)
        if callable(listen):
            listen(self._service_settings_changed)
        self.bind("<Map>", self._mapped, add="+")
        self.bind("<Unmap>", self._unmapped, add="+")
        self.bind("<Configure>", self._resized, add="+")

    def _label(self, parent, text, *, bold=False):
        label = ctk.CTkLabel(parent, text=text, anchor="w", justify="left", wraplength=410,
                            font=ctk.CTkFont(size=13 if bold else 12, weight="bold" if bold else "normal"),
                            text_color=None if bold else _MUTED)
        self._labels.append(label)
        return label

    def _row(self, page):
        body = self.pages[page]
        row = ctk.CTkFrame(body, fg_color=_CARD, bg_color=_CARD, corner_radius=0)
        row.grid(row=self._rows[page], column=0, sticky="ew", padx=6, pady=5)
        row.grid_columnconfigure(0, weight=1)
        self._rows[page] += 1
        return row

    def _note(self, page, text):
        row = self._row(page)
        label = self._label(row, text)
        label.grid(row=0, column=0, sticky="ew")
        return label

    def _check(self, page, key, text, command=None, *, disabled=False):
        row = self._row(page)
        variable = self.vars.get(key)
        if variable is None:
            variable = self.vars[key] = tk.BooleanVar(master=self, value=False)
        control = ctk.CTkCheckBox(row, text=text, variable=variable, command=command,
                                  state="disabled" if disabled else "normal")
        control.grid(row=0, column=0, sticky="w")
        return control

    def _menu(self, page, key, title, values, current, command=None):
        row = self._row(page)
        self._label(row, title, bold=True).grid(row=0, column=0, sticky="w", pady=(0, 4))
        menu = ctk.CTkOptionMenu(row, values=list(values), command=command, height=32)
        menu.set(current)
        menu.grid(row=1, column=0, sticky="ew")
        self.menus[key] = menu
        return menu

    def _entry(self, page, key, title, value):
        row = self._row(page)
        self._label(row, title, bold=True).grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = ctk.CTkEntry(row, height=32)
        entry.insert(0, str(value))
        entry.grid(row=1, column=0, sticky="ew")
        self.entries[key] = entry
        return entry

    def _build_input(self):
        page, settings = "Giriş", self._settings
        self._note(page, "Uygulama açılınca kayıt başlamaz. Ses düğmesine basılı tutarak veya kısayolla kayıt alın; klavyeyle düğmeyi etkinleştirmek etkileşim tercihini kullanır. Yazılı sohbet kullanılabilir.")
        self._menu(page, "interaction_mode", "Tercih edilen etkileşim", list(_MODES),
                   next(label for label, value in _MODES.items() if value == settings.interaction_mode), self._mode_changed)
        self._check(page, "vad_enabled", "Sesli sohbet için yerel konuşma algılamayı etkinleştir", self._vad_preference_changed)
        self._menu(page, "language", "Konuşma dili", list(_LANGUAGES),
                   next(label for label, value in _LANGUAGES.items() if value == settings.language))
        for kind, title in (("input", "Mikrofon"), ("output", "Hoparlör")):
            current = self._device_selection[kind]
            selected = "Sistem varsayılanı" if current is None else "Kayıtlı aygıt · tarama bekleniyor"
            self._device_choices[kind][selected] = current
            self._menu(page, kind + "_device", title, self._device_choices[kind], selected,
                       lambda label, kind=kind: self._select_device(kind, label))
        self.device_status = self._note(page, "Aygıt seçimi sonraki kayıtta / oynatmada uygulanır.")
        ctk.CTkButton(self._row(page), text="Aygıt listesini yenile", command=self.refresh_devices, height=30).grid(row=0, column=0, sticky="ew")
        self._entry(page, "input_gain", "Mikrofon kazancı · 0.1–4", settings.input_gain)
        row = self._row(page)
        self.level_bar = ctk.CTkProgressBar(row, height=8)
        self.level_bar.set(0)
        self.level_bar.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        self.test_button = ctk.CTkButton(row, text="Mikrofon düzeyini sına", command=self.toggle_input_test, height=32)
        self.test_button.grid(row=1, column=0, sticky="ew")
        if not callable(getattr(self.service, "start_voice_input_test", None)):
            self.test_button.configure(state="disabled")
        self.test_status = self._note(page, "Düzey testi yalnızca açık isteğinizle kısa süreli yerel ölçüm yapar; ses gönderilmez ve saklanmaz.")
        self._menu(page, "asr_provider", "Ses çözümleme sağlayıcısı", ["evren"], settings.asr_provider, self._provider_changed)
        self._menu(page, "asr_model", "Ses çözümleme modeli", ["qwen3-asr-1.7b"], settings.asr_model)
        self.asr_hint = self._note(page, "Yerel ASR adaptörü henüz yok. Evren seçiliyken tamamlanan söz Evren'e gönderilir.")
        self.capability_status = self._note(page, "Model erişimi henüz sorgulanmadı.")
        self.capability_button = ctk.CTkButton(self._row(page), text="Evren model erişimini kontrol et",
                                              command=self.discover_capabilities, height=32)
        self.capability_button.grid(row=0, column=0, sticky="ew")
        if not callable(getattr(self.service, "discover_voice_capabilities_async", None)):
            self.capability_button.configure(state="disabled")
        self._note(page, "Bu denetim yalnızca model listesini okur; mikrofonu açmaz. ASR, sohbet modeli ve TTS bağımsızdır.")
        self._menu(page, "vad_engine", "Konuşma algılama", ["energy", "webrtc"], settings.vad_engine, self._vad_changed)
        self.vad_hint = self._note(page, "WebRTC için isteğe bağlı voice-vad paketi gerekir. Eşik yalnızca energy algılayıcısında kullanılır.")
        for key, title in (("vad_threshold", "Konuşma eşiği · PCM RMS, 40–10000"),
                           ("vad_silence_ms", "Söz sonu sessizlik · 250–3000 ms"),
                           ("vad_min_speech_ms", "En kısa konuşma · 100–1000 ms"),
                           ("vad_pre_roll_ms", "Konuşma öncesi koruma · 0–1000 ms"),
                           ("max_record_seconds", "En uzun söz · 5–120 sn"),
                           ("asr_request_seconds", "İstek başına ses · 1–30 sn"),
                           ("shortcut", "Bas-konuş kısayolu · örnek: <Control-Shift-v>")):
            self._entry(page, key, title, getattr(settings, key))

    def _build_output(self):
        page, settings = "Yanıt", self._settings
        self._check(page, "read_replies", "Tamamlanan yanıtları sesli oku")
        self._check(page, "completion_chime", "Kodlama tamamlandığında kısa bildirim sesi çal")
        self._note(page, "Bildirim sesi işletim sisteminin bildirim ses düzeyini kullanır; yanıtı sesli okuma tercihinden bağımsızdır.")
        self._check(page, "stream_replies", "Yanıt cümleleri tamamlandıkça oku")
        self._note(page, "Kodlama yanıtları kısa sonuç özetiyle okunur. Tam kod ve günlükler ekranda kalır.")
        for key, label, options, current in (
                ("tts_provider", "Seslendirme sağlayıcısı", ["evren", "system"], settings.tts_provider),
                ("tts_model", "Evren seslendirme modeli", ["voxcpm2"], settings.tts_model),
                ("tts_voice", "Ses seçimi", ["default"], settings.tts_voice),
                ("tts_format", "Evren ses biçimi", ["wav", "mp3", "flac", "opus", "pcm"], settings.tts_format)):
            self._menu(page, key, label, options, current, self._provider_changed if key == "tts_provider" else None)
        self.tts_hint = self._note(page, "")
        self._entry(page, "output_volume", "Yanıt ses düzeyi · 0–1", settings.output_volume)
        self._note(page, "Bu uygulamanın ses düzeyidir; sistemin genel sesini değiştirmez.")
        self._entry(page, "playback_speed", "Seslendirme hız çarpanı · 0.25–4", settings.playback_speed)
        self._entry(page, "speech_rate", "Sistem seslendirmesi temel hızı · 80–350 kelime/dakika", settings.speech_rate)

    def _build_privacy(self):
        page = "Gizlilik ve yardım"
        self._check(page, "auto_send", "Sıradan istekleri otomatik gönder")
        self._check(page, "review_before_send", "Göndermeden önce her transkripti incele")
        self._note(page, "Transkript onayı araç veya plan onayı değildir. Hassas işlemler ilgili işlem ekranında ayrıca incelenir; sesli onay kabul edilmez.")
        self._check(page, "barge_in", "Ses çalarken araya gir", disabled=True)
        self._note(page, "Yankı iptali yok: yanıt okunurken mikrofon kapalıdır. Araya girmek için Basılı tut sesi durdurur. Kulaklık önerilir.")
        self._check(page, "wake_word_enabled", "Uyandırma sözcüğü", disabled=True)
        self._note(page, "Yerel uyandırma doğruluğu doğrulanmadığı için kullanılamaz. Arka planda otomatik dinleme başlamaz.")
        self._check(page, "retain_audio", "Ham sesi kalıcı olarak sakla", disabled=True)
        self._note(page, "Ham mikrofon sesi diske veya sohbet geçmişine yazılmaz. Yeniden denemek için inceleme süresince sınırlı bellekte tutulur. Sistem TTS geçici ses dosyalarını işlem bitiminde siler.")
        self._note(page, "Evren ASR tamamlanan sesinizi, Evren TTS yanıt metnini yapılandırılmış Evren hizmetine gönderir. Sistem TTS yerel çalışır. Hizmet hatası başka bir sağlayıcıya otomatik aktarım yapmaz.")
        guidance = ("macOS: Sistem Ayarları → Gizlilik ve Güvenlik → Mikrofon. Kaynak çalıştırmada Python/Terminal, paketli uygulamada Firik için izin verin."
                    if sys.platform == "darwin" else "Windows: Ayarlar → Gizlilik ve güvenlik → Mikrofon; masaüstü uygulamalarının erişimini açın."
                    if sys.platform == "win32" else "Linux: PortAudio ve çalışan ALSA/PulseAudio/PipeWire aygıtını kontrol edin. PortAudio için örneğin libportaudio2, yerel TTS için espeak-ng gerekir.")
        self._note(page, guidance + " Aygıt kesilirse yeniden bağlayın ve seçimi yenileyin; yazılı sohbet kullanılabilir.")
        row = self._row(page)
        self.diagnostics_button = ctk.CTkButton(row, text="Tanı bilgisini göster", command=self.show_diagnostics, height=32)
        self.diagnostics_button.grid(row=0, column=0, sticky="ew")
        self.diagnostics_output = ctk.CTkTextbox(self._row(page), height=140, wrap="word")
        self.diagnostics_output.grid(row=0, column=0, sticky="ew")
        self.diagnostics_output.insert("end", "Tanı denetimi çevrimdışıdır; mikrofonu açmaz ve ses göndermez.")
        self.diagnostics_output.configure(state="disabled")
        ctk.CTkButton(self._row(page), text="Tanı bilgisini kopyala", command=self.copy_diagnostics, height=32).grid(row=0, column=0, sticky="ew")

    def _mapped(self, event=None):
        if event is not None and event.widget not in (self, self._canvas):
            return
        if not self._devices_requested:
            self.refresh_devices()
        self.reload_settings()

    def _resized(self, event):
        if event.widget not in (self, self._canvas):
            return
        width = max(170, event.width - 76)
        for label in self._labels:
            label.configure(wraplength=width)
        self.status.configure(wraplength=max(170, event.width - 12))

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        refresh_voice_focus_colors(getattr(self, "_keyboard_controls", ()), self._apply_appearance_mode)

    def _unmapped(self, event):
        if event.widget in (self, self._canvas) and self._input_test is not None:
            self.stop_input_test()

    def _form_tab_changed(self):
        if self.tabs.get() != "Giriş" and self._input_test is not None:
            self.stop_input_test()

    def _alive(self, generation=None):
        return not self._closed and self.winfo_exists() and (generation is None or generation == self._test_generation)

    def _select_device(self, kind, label):
        if label in self._device_choices[kind]:
            self._device_selection[kind] = self._device_choices[kind][label]

    def refresh_devices(self):
        self._devices_requested = True
        self._device_generation += 1
        generation = self._device_generation
        discover = getattr(self.service, "audio_devices_async", None)
        if not callable(discover):
            return
        self.device_status.configure(text="Ses aygıtları taranıyor…")
        def loaded(devices):
            if not self._alive() or generation != self._device_generation:
                return
            for kind in ("input", "output"):
                current = self._device_selection[kind]
                choices = {"Sistem varsayılanı": None}
                selected = "Sistem varsayılanı" if current is None else None
                for item in devices.get(kind, ()):
                    label = f"{item.name} · {item.host_api} ({item.index})"
                    choices[label] = item.id
                    if current == item.id or type(current) is int and current == item.index:
                        selected = label
                if selected is None:
                    selected = "Kayıtlı aygıt şu anda kullanılamıyor"
                    choices[selected] = current
                self._device_choices[kind] = choices
                self.menus[kind + "_device"].configure(values=list(choices))
                self.menus[kind + "_device"].set(selected)
                self._device_selection[kind] = choices[selected]
                if self._baseline_values.get(("device", kind)) == current:
                    self._baseline_values[("device", kind)] = choices[selected]
            self.device_status.configure(text="Aygıtlar hazır. Seçim sonraki kayıtta / oynatmada uygulanır.")
        def failed(message):
            if self._alive() and generation == self._device_generation:
                self.device_status.configure(text=_safe_message(message))
        try:
            discover(loaded, failed)
        except Exception:
            failed("Aygıtlar okunamadı. Ses paketini ve sistem aygıtlarını kontrol edin.")

    def _mode_changed(self, value):
        self.vars["vad_enabled"].set(_MODES.get(value) == "conversation")

    def _vad_preference_changed(self):
        self.menus["interaction_mode"].set("Sesli sohbet" if self.vars["vad_enabled"].get() else "Bas-konuş")

    def _vad_changed(self, value=None):
        if "vad_threshold" in self.entries:
            self.entries["vad_threshold"].configure(state="normal" if self.menus["vad_engine"].get() == "energy" else "disabled")
        self._sync_keyboard_states()

    def _sync_keyboard_states(self):
        for control in getattr(self, "_keyboard_controls", ()):
            control._canvas.configure(takefocus=0 if control.cget("state") == "disabled" else 1)

    def _provider_changed(self, value=None):
        if "tts_provider" not in self.menus:
            return
        system = self.menus["tts_provider"].get() == "system"
        self.menus["tts_model"].configure(state="disabled" if system else "normal")
        self.menus["tts_format"].configure(state="disabled" if system else "normal")
        self.menus["tts_voice"].configure(state="disabled", values=["default"])
        self.menus["tts_voice"].set("Sistem dili / varsayılan ses" if system else "default")
        self.entries["speech_rate"].configure(state="normal" if system else "disabled")
        self.tts_hint.configure(text=("Sistem sesi yerel çalışır. Kurulu sesleri adlarıyla seçme listesi desteklenmiyor; dil ve işletim sistemi varsayılanı kullanılır."
            if system else "Evren için doğrulanmış ses seçeneği default değeridir; ses klonlama yoktur. WAV yerleşik oynatılır, sıkıştırılmış biçimler isteğe bağlı çözücü gerektirebilir. Temel kelime/dakika hızı yalnızca sistem TTS içindir."))
        local = self.menus["asr_provider"].get() == "local"
        self.menus["asr_model"].configure(state="disabled" if local else "normal")
        self.asr_hint.configure(text=("Seçili yerel ASR adaptörü kullanılamıyor. Başka bir sağlayıcıya otomatik geçilmez; Evren'i açıkça seçebilir veya metinle devam edebilirsiniz."
            if local else "Yerel ASR adaptörü henüz yok. Evren seçiliyken tamamlanan söz Evren'e gönderilir."))
        for kind in ("asr", "tts"):
            capability = self._capabilities.get(kind)
            available = getattr(capability, "available_models", None)
            remote = self.menus[kind + "_provider"].get() == "evren"
            if remote and getattr(capability, "authenticated", False) and available is not None:
                model = self.menus[kind + "_model"].get()
                self.menus[kind + "_model"].configure(state="normal" if model in available else "disabled")
        self._sync_keyboard_states()

    def discover_capabilities(self):
        discover = getattr(self.service, "discover_voice_capabilities_async", None)
        if not callable(discover):
            return
        self.capability_button.configure(state="disabled")
        self.capability_status.configure(text="Evren model erişimi sorgulanıyor; mikrofon açılmaz…")
        def loaded(capabilities):
            if not self._alive():
                return
            self._capabilities = capabilities
            self._provider_changed()
            summaries = []
            for kind in ("asr", "tts"):
                capability = capabilities.get(kind)
                available = getattr(capability, "available_models", None)
                if getattr(capability, "provider", "") == "system":
                    summaries.append(f"{kind.upper()}: sistem seslendirmesi seçili.")
                elif getattr(capability, "authenticated", False) and available is not None:
                    summaries.append(f"{kind.upper()}: " + (", ".join(available) if available else "hesapta uygun ses modeli görünmüyor."))
                else:
                    summaries.append(f"{kind.upper()}: hesap erişimi doğrulanmadı.")
            self.capability_status.configure(text=_safe_message("\n".join(summaries)))
            self.capability_button.configure(state="normal")
        def failed(message):
            if self._alive():
                self.capability_status.configure(text=_safe_message(message))
                self.capability_button.configure(state="normal")
        try:
            discover(loaded, failed)
        except Exception:
            failed("Model erişimi sorgulanamadı. Evren bağlantısını ve anahtarını kontrol edin.")

    def collect_settings(self):
        values = {name: variable.get() for name, variable in self.vars.items()
                  if name in VoiceSettings.__dataclass_fields__}
        for name, entry in self.entries.items():
            if name == "shortcut":
                values[name] = entry.get().strip()
            else:
                try:
                    values[name] = float(entry.get()) if name in {"input_gain", "output_volume", "playback_speed"} else int(entry.get())
                except (ValueError, TypeError):
                    raise ValueError("Ses sınırları ve düzeyleri geçerli sayılar olmalıdır.") from None
        values.update(language=_LANGUAGES[self.menus["language"].get()],
                      model=self.menus["asr_model"].get(),
                      input_device=self._device_selection["input"], output_device=self._device_selection["output"],
                      asr_provider=self.menus["asr_provider"].get(), tts_provider=self.menus["tts_provider"].get(),
                      tts_model=self.menus["tts_model"].get(), tts_format=self.menus["tts_format"].get(),
                      tts_voice="default", vad_engine=self.menus["vad_engine"].get(),
                      interaction_mode=_MODES[self.menus["interaction_mode"].get()],
                      barge_in=False, wake_word_enabled=False, retain_audio=False)
        values["vad_enabled"] = self.vars["vad_enabled"].get() or values["interaction_mode"] == "conversation"
        validated = replace(self._settings, **values)
        binding = self.bind(validated.shortcut, lambda event: None, add="+")
        if binding:
            self.unbind(validated.shortcut, binding)
        return VoiceSettings.from_mapping(validated.to_dict())

    def _form_values(self):
        return {**{("var", key): value.get() for key, value in self.vars.items()},
                **{("entry", key): value.get() for key, value in self.entries.items()},
                **{("menu", key): value.get() for key, value in self.menus.items()
                   if key not in {"input_device", "output_device", "tts_voice"}},
                **{("device", key): value for key, value in self._device_selection.items()}}

    def _service_settings_changed(self):
        if self._alive():
            if self._input_test is not None:
                self.stop_input_test()
            self.reload_settings()

    def reload_settings(self, *, force=False):
        if self._closed:
            return False
        latest = getattr(self.service, "voice_settings", self._settings)
        if not isinstance(latest, VoiceSettings) or latest == self._settings and not force:
            return True
        if not force and self._form_values() != self._baseline_values:
            self.status.configure(text="Ses ayarları başka yerde güncellendi. Görünen taslağınız korundu; yeniden yükleyebilir veya yalnızca kendi değişikliklerinizi kaydedebilirsiniz.", text_color=("#92400e", "#fbbf24"))
            self.reload_button.grid()
            return False
        self.stop_input_test()
        self._apply_settings(latest)
        self.status.configure(text="Güncel ses ayarları yüklendi. Kayıt başlamadı.", text_color=_MUTED)
        return True

    def _apply_settings(self, settings):
        self._settings = settings
        for key, variable in self.vars.items():
            variable.set(False if key in {"barge_in", "wake_word_enabled", "retain_audio"}
                         else getattr(settings, key, False))
        for key, entry in self.entries.items():
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, str(getattr(settings, key)))
        for key in ("asr_provider", "tts_provider", "tts_model", "tts_format", "vad_engine"):
            self.menus[key].set(getattr(settings, key))
        self.menus["asr_model"].set(settings.asr_model)
        self.menus["language"].set(next(label for label, value in _LANGUAGES.items() if value == settings.language))
        self.menus["interaction_mode"].set(next(label for label, value in _MODES.items() if value == settings.interaction_mode))
        for kind in ("input", "output"):
            selected = getattr(settings, kind + "_device")
            choices = self._device_choices[kind]
            label = next((label for label, value in choices.items() if value == selected), None)
            if label is None:
                label = "Kayıtlı aygıt şu anda kullanılamıyor"
                choices[label] = selected
                self.menus[kind + "_device"].configure(values=list(choices))
            self._device_selection[kind] = selected
            self.menus[kind + "_device"].set(label)
        self._provider_changed()
        self._vad_changed()
        self._baseline_values = self._form_values()
        self.reload_button.grid_remove()

    def save(self):
        if self._closed:
            return False
        self.stop_input_test()
        try:
            validated = self.collect_settings()
            latest = getattr(self.service, "voice_settings", self._settings)
            if isinstance(latest, VoiceSettings) and latest != self._settings:
                changes = {key: getattr(validated, key) for key in VoiceSettings.__dataclass_fields__
                           if getattr(validated, key) != getattr(self._settings, key)}
                validated = replace(latest, **changes)
        except (ValueError, KeyError, tk.TclError) as error:
            self.status.configure(text="Ayarlar kaydedilemedi: " + (_safe_message(error) if isinstance(error, ValueError)
                                  else "Dil, kısayol ve sayısal ayarları kontrol edin."), text_color=("#b91c1c", "#f87171"))
            return False
        try:
            self.service.update_voice_settings(validated)
        except Exception:
            self.status.configure(text="Ses ayarları kaydedilemedi. Yapılandırma dosyasının yazma iznini kontrol edin.", text_color=("#b91c1c", "#f87171"))
            return False
        self._apply_settings(validated)
        self.status.configure(text="Ses ayarları kaydedildi. Yeni kayıt veya oynatmada uygulanır.", text_color=("#047857", "#34d399"))
        if self.on_saved is not None:
            self.on_saved(validated)
        return True

    def toggle_input_test(self):
        if self._input_test is not None:
            self.stop_input_test()
            return
        if self._closed:
            return
        try:
            settings = self.collect_settings()
        except (ValueError, KeyError, tk.TclError):
            self.test_status.configure(text="Önce mikrofon ve sayısal ses ayarlarını düzeltin.")
            return
        self._test_generation += 1
        generation = self._test_generation
        self.test_button.configure(text="Düzey testini durdur")
        self.test_status.configure(text="Kısa yerel düzey testi çalışıyor; ses gönderilmiyor…")
        completed = False
        def level(value):
            if self._alive(generation):
                self.level_bar.set(max(0., min(1., float(value))))
        def done():
            nonlocal completed
            if completed:
                return
            completed = True
            if self._alive(generation):
                self._input_test = None
                self.level_bar.set(0)
                self.test_button.configure(text="Mikrofon düzeyini sına")
                self.test_status.configure(text="Yerel düzey testi bitti; ses gönderilmedi ve saklanmadı.")
        def failed(message):
            if self._alive(generation):
                done()
                self.test_status.configure(text=_safe_message(message))
        try:
            handle = self.service.start_voice_input_test(level, done, failed, settings=settings)
            self._input_test = None if completed else handle
        except Exception:
            failed("Mikrofon testi başlatılamadı. Sistem iznini ve aygıtı kontrol edin.")

    def stop_input_test(self):
        self._test_generation += 1
        handle, self._input_test = self._input_test, None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        if not self._closed:
            self.level_bar.set(0)
            self.test_button.configure(text="Mikrofon düzeyini sına")
            self.test_status.configure(text="Düzey testi durduruldu.")

    def show_diagnostics(self):
        diagnose = getattr(self.service, "voice_diagnostics", None)
        try:
            details = diagnose() if callable(diagnose) else {"durum": "Ses tanı denetimi bu sürümde kullanılamıyor."}
            text = _safe_message(json.dumps(details, ensure_ascii=False, indent=2), limit=8000)
        except Exception:
            text = "Ses tanı bilgisi hazırlanamadı. Sistem iznini ve isteğe bağlı ses paketlerini kontrol edin."
        self.diagnostics_output.configure(state="normal")
        self.diagnostics_output.delete("1.0", "end")
        self.diagnostics_output.insert("end", text)
        self.diagnostics_output.configure(state="disabled")

    def copy_diagnostics(self):
        self.clipboard_clear()
        self.clipboard_append(self.diagnostics_output.get("1.0", "end-1c"))

    def destroy(self):
        self._closed = True
        self._device_generation += 1
        self.stop_input_test()
        remove = getattr(self.service, "remove_voice_settings_listener", None)
        if callable(remove):
            remove(self._service_settings_changed)
        super().destroy()
