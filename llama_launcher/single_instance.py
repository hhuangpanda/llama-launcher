"""One app instance per user; a second launch asks the first to open Settings (or to quit)."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SHOW_SETTINGS = b"show-settings"
QUIT = b"quit"


def server_name() -> str:
    return f"llama-launcher-{getpass.getuser()}"


def notify_existing(name: str | None = None, message: bytes = SHOW_SETTINGS, timeout_ms: int = 2000) -> bool:
    """True when another instance is listening (this one should exit). Sends message to it."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(500):
        return False
    sock.write(message)
    sock.flush()
    sock.waitForBytesWritten(500)
    # Hanging up right away can lose the message if the instance hasn't accepted the connection yet;
    # it hangs up itself once it has read it.
    sock.waitForDisconnected(timeout_ms)
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
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():  # arrived before the connection was accepted: no readyRead for it
                self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        data = bytes(sock.readAll()).decode(errors="replace")
        if data:
            sock.disconnectFromServer()  # tells the sender it arrived
            self.message.emit(data)
