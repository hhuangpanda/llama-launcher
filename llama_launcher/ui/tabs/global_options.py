"""Global tab: options every profile inherits."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QPlainTextEdit, QSpinBox

from llama_launcher.config import CACHE_TYPES, IDLE_MODES
from llama_launcher.ui.tabs.base import SettingsTab
from llama_launcher.ui.widgets import IDLE_HELP, choice_combo, info_button, select_data, with_info


class GlobalTab(SettingsTab):
    title = "Global"

    def __init__(self, owner):
        super().__init__(owner)
        f = QFormLayout(self)
        self.cache_k = choice_combo(CACHE_TYPES, blank="(llama-server default)")
        self.cache_v = choice_combo(CACHE_TYPES, blank="(llama-server default)")
        self.idle = choice_combo(IDLE_MODES)
        self.idle_s = QSpinBox(minimum=1, maximum=7 * 1440, suffix=" min")
        self.args = QPlainTextEdit()
        self.args.setPlaceholderText("Flags applied to every profile, e.g. --flash-attn on --fit on -t 12")
        f.addRow("K cache", self.cache_k)
        f.addRow("V cache", self.cache_v)
        f.addRow("When idle", with_info(self.idle, info_button(self, "When idle", IDLE_HELP)))
        f.addRow("Idle after", self.idle_s)
        f.addRow("Global args", self.args)
        self.cache_k.currentIndexChanged.connect(lambda _: self._set("cache_type_k", self.cache_k.currentData()))
        self.cache_v.currentIndexChanged.connect(lambda _: self._set("cache_type_v", self.cache_v.currentData()))
        self.idle.currentIndexChanged.connect(lambda _: self._set("idle_mode", self.idle.currentData()))
        self.idle_s.valueChanged.connect(lambda v: self._set("idle_seconds", v * 60))
        self.args.textChanged.connect(lambda: self._set("args", self.args.toPlainText().strip()))

    def _set(self, attr: str, value) -> None:
        self.set_field(self.owner.settings.global_, attr, value)

    def load(self) -> None:
        g = self.owner.settings.global_
        select_data(self.cache_k, g.cache_type_k)
        select_data(self.cache_v, g.cache_type_v)
        select_data(self.idle, g.idle_mode)
        self.idle_s.setValue(max(1, round(g.idle_seconds / 60)))
        self.args.setPlainText(g.args)
