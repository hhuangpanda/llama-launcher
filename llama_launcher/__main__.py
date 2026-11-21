"""Entry point: single-instance check, then the tray app."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from llama_launcher import paths
from llama_launcher.controller import Controller
from llama_launcher.logs import setup_app_log
from llama_launcher.single_instance import InstanceServer, notify_existing

log = logging.getLogger("llama_launcher")


class App:
    def __init__(self, controller: Controller, instance_name: str | None = None):
        from llama_launcher.ui.settings_window import SettingsWindow
        from llama_launcher.ui.tray import Tray

        self._window_cls = SettingsWindow
        self.c = controller
        self.window = None
        self.instance = InstanceServer(instance_name)
        self.instance.message.connect(lambda _: self.open_settings())
        self.tray = Tray(controller, self.open_settings, self.quit)
        controller.manager.watch_port(controller.port_target)

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
    code = app.exec()
    log.info("exiting")
    return code


if __name__ == "__main__":
    sys.exit(main())
