"""Base for settings tabs: each edits the window's working copies and reports changes to it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from llama_launcher.ui.settings_window import SettingsWindow


class SettingsTab(QWidget):
    title = ""

    def __init__(self, owner: SettingsWindow):
        super().__init__()
        self.owner = owner
        self.c = owner.c

    def load(self) -> None:
        """Show the owner's working copies (called on open, revert and external config changes)."""

    def set_field(self, target, attr: str, value) -> None:
        if self.owner.loading:
            return
        setattr(target, attr, value)
        self.owner.changed()
