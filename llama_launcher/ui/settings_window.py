"""The single Settings window: holds working copies of the config and one tab per section."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

from llama_launcher.catalog import FileEntry
from llama_launcher.controller import Controller
from llama_launcher.ui.icons import app_icon
from llama_launcher.ui.tabs.folders import FoldersTab
from llama_launcher.ui.tabs.general import GeneralTab
from llama_launcher.ui.tabs.global_options import GlobalTab
from llama_launcher.ui.tabs.profiles import ProfilesTab
from llama_launcher.ui.tabs.runtimes import RuntimesTab

TITLE = "llama-launcher settings"


class SettingsWindow(QWidget):
    def __init__(self, controller: Controller):
        super().__init__(None, Qt.Window)
        self.c = controller
        self.setWindowTitle(TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1100, 760)
        self.dirty = False
        self.loading = False
        self.settings = copy.deepcopy(controller.settings)
        self.models = copy.deepcopy(controller.config.models)
        self.entries: list[FileEntry] = controller.entries

        self.general_tab = GeneralTab(self)
        self.runtimes_tab = RuntimesTab(self)
        self.folders_tab = FoldersTab(self)
        self.global_tab = GlobalTab(self)
        self.profiles_tab = ProfilesTab(self)
        self.tabs = QTabWidget()
        for tab in self._all_tabs():
            self.tabs.addTab(tab, tab.title)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.revert)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        self.status = QLabel()
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        for b in (self.revert_btn, self.save_btn, self.close_btn):
            bottom.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addLayout(bottom)
        self.c.config_changed.connect(self._external_change)
        self.revert()

    def _all_tabs(self):
        return (self.general_tab, self.runtimes_tab, self.folders_tab, self.global_tab, self.profiles_tab)

    # ---- state shared by the tabs ----
    def revert(self) -> None:
        self.settings = copy.deepcopy(self.c.settings)
        self.models = copy.deepcopy(self.c.config.models)
        self.loading = True
        for tab in self._all_tabs():
            tab.load()
        self.loading = False
        self._set_dirty(False)

    def changed(self) -> None:
        """A tab edited the working copies."""
        self._set_dirty(True)
        self.profiles_tab.refresh_preview()

    def set_entries(self, entries: list[FileEntry]) -> None:
        """A rescan found these files; the profile tree follows them."""
        self.entries = entries
        self.profiles_tab.load()

    def _external_change(self) -> None:
        if not self.dirty:
            self.revert()

    def _set_dirty(self, dirty: bool) -> None:
        if self.loading:
            return
        self.dirty = dirty
        self.setWindowTitle(TITLE + (" *" if dirty else ""))
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)

    def save(self) -> None:
        problems = self.profiles_tab.name_problems()
        if problems:
            QMessageBox.warning(self, "Can't save", "\n".join(problems))
            return
        self.c.save(copy.deepcopy(self.settings), copy.deepcopy(self.models))
        self._set_dirty(False)
        self.status.setText(
            "Saved."
            + ("  The running model has changes: use Restart to apply in the tray." if self.c.is_stale() else "")
        )

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.dirty:
            r = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save changes before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if r == QMessageBox.Cancel:
                e.ignore()
                return
            if r == QMessageBox.Save:
                self.save()
            else:
                self.revert()
        e.accept()

    def bring_to_front(self) -> None:
        if not self.dirty:
            self.revert()
        self.showNormal()
        self.raise_()
        self.activateWindow()
