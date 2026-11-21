"""Small shared widgets."""

from __future__ import annotations

import ipaddress

from PySide6.QtCore import QPoint, QRect, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QToolButton, QToolTip, QWidget

IDLE_HELP = (
    "<b>sleep</b>: after the idle time, llama-server unloads the model and frees the VRAM but keeps running. "
    "The next request reloads it automatically (a few seconds).<br><br>"
    "<b>stop</b>: after the idle time, the server process is ended. Start it again from the tray.<br><br>"
    "<b>off</b>: the model stays loaded until you stop it."
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def tailscale_ipv4() -> list[str]:
    """IPv4 addresses Tailscale has assigned to this PC (empty when it isn't connected)."""
    out = []
    for iface in QNetworkInterface.allInterfaces():
        if not iface.flags() & QNetworkInterface.IsUp:
            continue
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.protocol() == QAbstractSocket.IPv4Protocol and ipaddress.ip_address(ip.toString()) in _CGNAT:
                out.append(ip.toString())
    return out


def info_button(parent: QWidget, title: str, html: str) -> QToolButton:
    """'?' that shows its help as a tooltip on hover and on click; nothing to dismiss."""
    b = QToolButton(parent)
    b.setText("?")
    tip = f"<div style='width:420px'>{html}</div>"
    b.setToolTip(tip)
    b.clicked.connect(lambda: QToolTip.showText(b.mapToGlobal(QPoint(0, b.height())), tip, b, QRect(), 30000))
    return b


def with_info(widget: QWidget, button: QToolButton) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 1)
    lay.addWidget(button)
    return w


class InheritSpin(QWidget):
    """A spin box with an 'inherit' toggle; value() is 0 while inheriting.
    `scale` converts between the shown unit and the stored one (60: minutes shown, seconds stored)."""

    changed = Signal(int)

    def __init__(self, minimum=1, maximum=10_000_000, step=1, suffix="", scale=1):
        super().__init__()
        self.scale = scale
        self.inherit = QCheckBox("inherit")
        self.spin = QSpinBox(minimum=minimum, maximum=maximum, singleStep=step, suffix=suffix)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.inherit)
        lay.addWidget(self.spin, 1)
        self.note = QLabel()
        self.note.setStyleSheet("color: gray;")
        lay.addWidget(self.note)
        self.inherit.toggled.connect(self._toggled)
        self.spin.valueChanged.connect(lambda _: self.changed.emit(self.value()))

    def _toggled(self, on: bool) -> None:
        self.spin.setEnabled(not on)
        self.note.setVisible(on)
        self.changed.emit(self.value())

    def value(self) -> int:
        return 0 if self.inherit.isChecked() else self.spin.value() * self.scale

    def set_value(self, stored: int, inherited_hint: int = 0, note: str = "") -> None:
        """stored 0 means inherit; the disabled box then shows inherited_hint (stored units) and note."""
        self.note.setText(note)
        self.note.setVisible(stored == 0 and bool(note))
        self.blockSignals(True)
        shown = stored or inherited_hint
        self.spin.setValue(max(self.spin.minimum(), round(shown / self.scale)) if shown else self.spin.minimum())
        self.inherit.setChecked(stored == 0)
        self.spin.setEnabled(stored != 0)
        self.blockSignals(False)
