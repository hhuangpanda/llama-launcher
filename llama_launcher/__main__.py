"""Entry point: single-instance check, then the tray app. `--quit` asks a running instance to quit."""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from llama_launcher import paths
from llama_launcher.controller import Controller
from llama_launcher.logs import setup_app_log
from llama_launcher.single_instance import QUIT, InstanceServer, notify_existing
from llama_launcher.ui.icons import app_icon

log = logging.getLogger("llama_launcher")


class App:
    def __init__(self, controller: Controller, instance_name: str | None = None):
        from llama_launcher.ui.settings_window import SettingsWindow
        from llama_launcher.ui.tray import Tray

        self._window_cls = SettingsWindow
        self.c = controller
        self.window = None
        self.instance = InstanceServer(instance_name)
        self.instance.message.connect(self._on_message)
        self.tray = Tray(controller, self.open_settings, self.quit)
        controller.manager.watch_port(controller.port_target)

    def _on_message(self, message: str) -> None:
        if message == QUIT.decode():
            self.quit()  # same as the tray's Quit: unsaved Settings changes are asked about first
        else:
            self.open_settings()

    def show_started(self) -> None:
        from llama_launcher.ui.toast import Toast

        self._toast = Toast(
            "llama-launcher is running",
            "Check system tray. Click here to see menu.",
            lambda: self.tray.menu.popup(QCursor.pos()),
        )
        self._toast.popup()

    def open_settings(self):
        if self.window is None:
            self.window = self._window_cls(self.c)
        self.window.bring_to_front()
        return self.window

    def quit(self) -> None:
        if self.window is not None and self.window.isVisible() and not self.window.close():
            return
        self.tray.icon.hide()
        self.c.shutdown()
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("llama-launcher")
    app.setWindowIcon(app_icon())
    if "--quit" in sys.argv[1:]:
        return 0 if notify_existing(message=QUIT) else 1  # 1: none running
    if notify_existing():
        return 0
    setup_app_log(paths.logs_dir())
    log.info("starting")
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.error("no system tray available")
    controller = Controller()
    a = App(controller)
    a.tray.show()
    if not controller.settings.runtimes or not controller.settings.model_folders:
        a.open_settings()
    else:
        a.show_started()
    code = app.exec()
    log.info("exiting")
    return code


if __name__ == "__main__":
    sys.exit(main())
