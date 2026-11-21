"""Runtimes tab: llama.cpp build folders and the default runtime."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from llama_launcher.config import RuntimeEntry
from llama_launcher.runtimes import Runtime
from llama_launcher.ui.tabs.base import SettingsTab
from llama_launcher.ui.widgets import select_data


class RuntimesTab(SettingsTab):
    title = "Runtimes"

    def __init__(self, owner):
        super().__init__(owner)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Folders that contain llama-server.exe and its DLLs (a release zip or your own build)."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Folder", "Version"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add folder...")
        add.clicked.connect(self._add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        self.default = QComboBox()
        self.default.currentIndexChanged.connect(
            lambda _: self.set_field(self.owner.settings, "default_runtime", self.default.currentData() or ""))
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        row.addWidget(QLabel("Default runtime"))
        row.addWidget(self.default)
        lay.addLayout(row)

    def load(self) -> None:
        s = self.owner.settings
        was, self.owner.loading = self.owner.loading, True
        self.table.setRowCount(0)
        for r in s.runtimes:
            i = self.table.rowCount()
            self.table.insertRow(i)
            rt = self.c.runtime_factory(r)
            try:
                version = self.c.probe.info(rt).version.label()
            except RuntimeError as e:
                version = f"problem: {e}"
            for col, text in enumerate((r.name, r.path, version)):
                self.table.setItem(i, col, QTableWidgetItem(text))
        self.default.clear()
        self.default.addItem("(first in list)", "")
        for r in s.runtimes:
            self.default.addItem(r.name, r.name)
        select_data(self.default, s.default_runtime)
        self.owner.loading = was

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder with llama-server.exe")
        if not folder:
            return
        folder = os.path.normpath(folder)
        problem = Runtime("new", folder).check()
        if problem:
            QMessageBox.warning(self, "Not a llama.cpp folder", problem)
            return
        name, ok = QInputDialog.getText(self, "Runtime name", "Name for this llama.cpp build:",
                                        text=os.path.basename(folder.rstrip("\\")) or "llama")
        name = name.strip()
        if not ok or not name:
            return
        if self.owner.settings.runtime(name):
            QMessageBox.warning(self, "Name taken", f"A runtime called '{name}' already exists.")
            return
        self.owner.settings.runtimes.append(RuntimeEntry(name, folder))
        self.load()
        self.owner.changed()

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        s = self.owner.settings
        removed = s.runtimes.pop(row)
        if s.default_runtime == removed.name:
            s.default_runtime = ""
        self.load()
        self.owner.changed()
