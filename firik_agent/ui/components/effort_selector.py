"""Shared model-aware reasoning selector for chat and coding."""
import customtkinter as ctk

LABELS = {"none": "Kapalı", "minimal": "En az", "low": "Düşük", "medium": "Orta",
          "high": "Yüksek", "xhigh": "Çok yüksek"}
DEFAULT = "Varsayılan"


class EffortSelector(ctk.CTkFrame):
    def __init__(self, master, service, on_change):
        super().__init__(master, fg_color="transparent")
        self.service = service
        self.on_change = on_change
        self.levels = []
        self.labels = {}
        ctk.CTkLabel(self, text="Düşünme düzeyi", font=ctk.CTkFont(size=11)).pack(anchor="w")
        self.combo = ctk.CTkComboBox(self, values=[DEFAULT], width=145,
                                   state="disabled", command=self._selected)
        self.combo.set(DEFAULT)
        self.combo.pack(fill="x")

    def refresh(self, model, selected=None, busy=False):
        get_levels = getattr(self.service, "model_effort_levels", lambda model: [])
        self.levels = get_levels(model)
        self.labels = {f"{LABELS.get(level, level)} ({level})": level for level in self.levels}
        self.combo.configure(values=[DEFAULT, *self.labels],
                             state="readonly" if self.levels and not busy else "disabled")
        label = next((label for label, level in self.labels.items() if level == selected), DEFAULT)
        self.combo.set(label if self.levels else "Destek yok / bilinmiyor")
        # Keep a restored choice while the asynchronous catalogue is loading.
        return selected if not self.levels or selected in self.levels else None

    def _selected(self, label):
        self.on_change(self.labels.get(label))
