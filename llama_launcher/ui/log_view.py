"""Live view of a log file. Exists only while open: closing it frees the text and stops the timer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from llama_launcher.logs import LogTail
from llama_launcher.ui.widgets import open_path

MAX_LINES = 5000
_open: dict[Path, LogViewer] = {}


def show_log(path: Path) -> LogViewer:
    """Opens the viewer for path, or raises the one already open."""
    path = Path(path)
    v = _open.get(path)
    if v is None:
        v = _open[path] = LogViewer(path)
        v.destroyed.connect(lambda: _open.pop(path, None))
    v.show()
    v.raise_()
    v.activateWindow()
    return v


class LogViewer(QDialog):
    def __init__(self, path: Path, interval_ms: int = 500):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{path.name} (live)")
        self.resize(1000, 600)
        self.tail = LogTail(path)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        note = QLabel(f"Last {MAX_LINES} lines. Scroll up to pause following.")
        note.setStyleSheet("color: gray;")
        open_btn = QPushButton("Open in editor")
        open_btn.clicked.connect(lambda: open_path(path))
        bottom = QHBoxLayout()
        bottom.addWidget(note, 1)
        bottom.addWidget(open_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text)
        lay.addLayout(bottom)
        self.timer = QTimer(self, interval=interval_ms, timeout=self.poll)
        self.timer.start()
        self.poll()

    def poll(self) -> None:
        reset, text = self.tail.read()
        if reset:
            self.text.clear()
        if not text:
            return
        bar = self.text.verticalScrollBar()
        follow = reset or bar.value() >= bar.maximum()
        keep = bar.value()
        self.text.appendPlainText(text.removesuffix("\n"))
        bar.setValue(bar.maximum() if follow else keep)
