"""One app instance per user; a second launch asks the first to open Settings."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    return f"llama-launcher-{getpass.getuser()}"


def notify_existing(name: str | None = None, message: bytes = b"show-settings") -> bool:
    """True when another instance answered (this one should exit)."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(500):
        return False
    sock.write(message)
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    message = Signal(str)

    def __init__(self, name: str | None = None):
        super().__init__()
        self.name = name or server_name()
        self.server = QLocalServer(self)
        QLocalServer.removeServer(self.name)  # stale socket from a crashed instance
        if not self.server.listen(self.name):
            raise RuntimeError(f"can't listen on {self.name}: {self.server.errorString()}")
        self.server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self.message.emit(bytes(s.readAll()).decode(errors="replace")))
            sock.disconnected.connect(sock.deleteLater)
