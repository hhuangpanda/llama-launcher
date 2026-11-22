"""A brief "I'm running" card near the tray, since Windows hides new tray icons in the overflow."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from llama_launcher.ui.icons import app_icon

SHOW_MS = 2500
FADE_MS = 400
MARGIN = 12


class Toast(QWidget):
    def __init__(self, title: str, text: str, on_click: Callable[[], None] | None = None):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._on_click = on_click
        card = QFrame(self)
        card.setObjectName("card")
        pal = self.palette()
        card.setStyleSheet(
            f"#card {{ background: {pal.window().color().name()}; border: 1px solid {pal.mid().color().name()};"
            " border-radius: 8px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 18, 12)
        row.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(40, 40))
        row.addWidget(icon)
        lines = QVBoxLayout()
        lines.setSpacing(2)
        head = QLabel(title)
        f = head.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.1)
        head.setFont(f)
        lines.addWidget(head)
        lines.addWidget(QLabel(text))
        row.addLayout(lines)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=FADE_MS, startValue=1.0, endValue=0.0)
        self._fade.finished.connect(self.close)
        self._timer = QTimer(self, singleShot=True, interval=SHOW_MS)
        self._timer.timeout.connect(self._fade.start)

    def popup(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()  # the corner by the taskbar's notification area
            self.move(area.right() - self.width() - MARGIN + 1, area.bottom() - self.height() - MARGIN + 1)
        self.show()
        self._timer.start()

    def mousePressEvent(self, e):
        self.close()
        if self._on_click:
            self._on_click()
