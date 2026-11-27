"""Model folders tab: scanned folders and the files found in them."""

from __future__ import annotations

import os
import re
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from llama_launcher import catalog, paths
from llama_launcher.catalog import Kind
from llama_launcher.ui.tabs.base import SettingsTab


class _Item(QTableWidgetItem):
    """Sorts case-insensitively, with numbers in natural order ("embedding 1024" < "embedding 5120")."""

    def __init__(self, text: str):
        super().__init__(text)
        self._key = [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", text.lower())]

    def __lt__(self, other):
        return self._key < other._key if isinstance(other, _Item) else super().__lt__(other)


class FoldersTab(SettingsTab):
    title = "Model folders"

    def __init__(self, owner):
        super().__init__(owner)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Folders scanned (recursively) for .gguf files. Nothing is copied or moved."))
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(110)
        lay.addWidget(self.folder_list)
        row = QHBoxLayout()
        add = QPushButton("Add folder...")
        add.clicked.connect(self._add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        for b in (add, rm, rescan):
            row.addWidget(b)
        self.scan_status = QLabel()
        self.scan_status.setObjectName("scan_status")
        row.addWidget(self.scan_status, 1)
        lay.addLayout(row)
        self.file_table = QTableWidget(0, 5)
        self.file_table.setHorizontalHeaderLabels(["File", "Kind", "Arch", "Size match", "Folder"])
        self.file_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.file_table.setSortingEnabled(True)
        lay.addWidget(self.file_table, 1)

    def load(self) -> None:
        self.folder_list.clear()
        self.folder_list.addItems(self.owner.settings.model_folders)
        self._scan()

    def rescan(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._scan()
            if self.owner.settings.model_folders == self.c.settings.model_folders:
                self.c.rescan()  # the tray picks up new files without a save
        finally:
            QApplication.restoreOverrideCursor()

    def _scan(self) -> None:
        entries = catalog.scan(self.owner.settings.model_folders, self.c.info_cache)
        counts = {k: sum(e.kind is k for e in entries) for k in Kind}
        parts = [
            f"{counts[Kind.MODEL]} models",
            f"{counts[Kind.MMPROJ]} mmproj",
            f"{counts[Kind.DRAFT]} draft",
            f"{counts[Kind.LORA]} lora",
        ]
        if counts[Kind.UNREADABLE]:
            parts.append(f"{counts[Kind.UNREADABLE]} unreadable")
        self.scan_status.setText(f"Scanned {datetime.now():%H:%M:%S}: " + ", ".join(parts))
        self.file_table.setSortingEnabled(False)  # rows would move while being filled
        self.file_table.setRowCount(0)
        for e in entries:
            i = self.file_table.rowCount()
            self.file_table.insertRow(i)
            info = e.info
            match = ""
            if info and e.kind is Kind.MMPROJ:
                match = "projection " + ", ".join(map(str, info.projection_dims))
            elif info and e.kind is Kind.LORA:
                match = f"arch {info.arch}"
            elif info:
                match = f"embedding {info.embedding_length}" + (" + MTP" if info.nextn_layers else "")
            vals = (
                os.path.basename(e.path),
                e.kind.value + (f" ({e.error})" if e.error else ""),
                info.arch if info else "",
                match,
                os.path.dirname(e.path),
            )
            for col, text in enumerate(vals):
                self.file_table.setItem(i, col, _Item(text))
        self.file_table.setSortingEnabled(True)
        self.file_table.resizeColumnsToContents()
        self.owner.set_entries(entries)

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Model folder")
        if not folder:
            return
        folder = os.path.normpath(folder)
        folders = self.owner.settings.model_folders
        if paths.norm(folder) not in {paths.norm(f) for f in folders}:
            folders.append(folder)
            self.load()
            self.owner.changed()

    def _remove(self) -> None:
        row = self.folder_list.currentRow()
        if row >= 0:
            self.owner.settings.model_folders.pop(row)
            self.load()
            self.owner.changed()
