from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from llama_launcher.server import State

COLORS = {
    State.STOPPED: "#8a8f98",
    State.STARTING: "#e0a020",
    State.RUNNING: "#2fa84f",
    State.SLEEPING: "#3b7ddd",
    State.STOPPING: "#e0a020",
    State.FAILED: "#d23c3c",
    State.EXTERNAL: "#8e5bd0",
}


def state_icon(state: State, size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(COLORS[state]))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.22, size * 0.22)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", int(size * 0.5))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "L")
    p.end()
    return QIcon(pm)
