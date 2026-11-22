import struct
from functools import cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from llama_launcher.server import State

BRAND = "#e07a2f"
COLORS = {
    State.STOPPED: "#8a8f98",
    State.STARTING: "#e0a020",
    State.RUNNING: "#2fa84f",
    State.SLEEPING: "#3b7ddd",
    State.STOPPING: "#e0a020",
    State.FAILED: "#d23c3c",
    State.EXTERNAL: "#8e5bd0",
}
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def _llama() -> QPainterPath:
    """Llama head and neck facing right, in a 100x100 box."""
    p = QPainterPath()
    p.moveTo(24, 100)
    p.cubicTo(24, 82, 25, 64, 28, 50)
    p.cubicTo(26, 44, 25, 38, 26, 32)
    p.cubicTo(24, 22, 25, 12, 29, 5)
    p.cubicTo(33, 12, 35, 20, 36, 27)
    p.cubicTo(38, 25, 40, 25, 42, 26)
    p.cubicTo(42, 16, 45, 9, 49, 4)
    p.cubicTo(52, 12, 52, 22, 50, 29)
    p.cubicTo(58, 32, 68, 36, 76, 42)
    p.cubicTo(82, 46, 82, 54, 76, 57)
    p.cubicTo(68, 60, 60, 58, 54, 60)
    p.cubicTo(52, 72, 54, 88, 56, 100)
    p.closeSubpath()
    return p


def _rocket() -> tuple[QPainterPath, QPainterPath]:
    """Rocket body with fins pointing up, and its flame, in a 100x100 box."""
    body = QPainterPath()
    body.moveTo(50, 0)
    body.cubicTo(66, 14, 70, 36, 65, 64)
    body.lineTo(35, 64)
    body.cubicTo(30, 36, 34, 14, 50, 0)
    fins = QPainterPath()
    fins.addPolygon([QPointF(36, 40), QPointF(18, 64), QPointF(20, 78), QPointF(38, 64)])
    fins.addPolygon([QPointF(64, 40), QPointF(82, 64), QPointF(80, 78), QPointF(62, 64)])
    flame = QPainterPath()
    flame.moveTo(38, 66)
    flame.cubicTo(40, 84, 46, 92, 50, 100)
    flame.cubicTo(54, 92, 60, 84, 62, 66)
    flame.closeSubpath()
    return body.united(fins), flame


def badge(color: str, size: int) -> QPixmap:
    """The app mark: a white llama and a rocket on a rounded square of the given colour."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    inset = max(size * 0.03, 0.5)
    r = QRectF(0, 0, size, size).adjusted(inset, inset, -inset, -inset)
    c = QColor(color)
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, c.lighter(115))
    g.setColorAt(1, c.darker(115))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, size * 0.22, size * 0.22)
    p.setClipRect(r)
    p.translate(r.topLeft())
    p.scale(r.width() / 100, r.height() / 100)
    detail = size >= 24  # eye and window are noise at tray size
    p.save()
    p.translate(-2, 18)
    p.scale(0.84, 0.84)
    p.setBrush(QColor("white"))
    p.drawPath(_llama())
    if detail:
        p.setBrush(c.darker(150))
        p.drawEllipse(QPointF(57, 40), 4, 4)
    p.restore()
    body, flame = _rocket()
    p.translate(72, 28)
    p.rotate(40)
    p.scale(0.5, 0.5)
    p.translate(-50, -50)
    ring = QBrush(g)  # background-coloured gap between rocket and llama
    ring.setTransform(p.transform().inverted()[0])
    p.setPen(QPen(ring, 16))
    p.setBrush(ring)
    p.drawPath(body.united(flame))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffcc33"))
    p.drawPath(flame)
    p.setBrush(QColor("white"))
    p.drawPath(body)
    if detail:
        p.setBrush(c.darker(140))
        p.drawEllipse(QPointF(50, 30), 8, 8)
    p.end()
    return pm


def _icon(color: str) -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(badge(color, size))
    return icon


@cache
def app_icon() -> QIcon:
    return _icon(BRAND)


@cache
def state_icon(state: State) -> QIcon:
    return _icon(COLORS[state])


@cache
def dot_icon(state: State, size: int = 32) -> QIcon:
    """A plain circle in the state's tray colour."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(COLORS[state]))
    p.drawEllipse(QRectF(size * 0.15, size * 0.15, size * 0.7, size * 0.7))
    p.end()
    return QIcon(pm)


@cache
def emoji_icon(text: str, size: int = 32) -> QIcon:
    """A colour emoji drawn as an icon, so menus get colour without image files."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    f = QFont("Segoe UI Emoji")
    f.setPixelSize(int(size * 0.8))
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
    p.end()
    return QIcon(pm)


def write_ico(path: Path, color: str = BRAND) -> None:
    """Multi-size .ico (PNG entries) for the exe."""
    pngs = []
    for size in SIZES:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QBuffer.WriteOnly)
        badge(color, size).save(buf, "PNG")
        pngs.append((size, bytes(data)))
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    for size, png in pngs:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path.write_bytes(out + b"".join(png for _, png in pngs))
