"""System tray icon and menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QLabel, QMenu, QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget,
                               QWidgetAction)

from llama_launcher import catalog, paths
from llama_launcher.controller import CONTEXT_CHOICES, Controller, status_lines
from llama_launcher.launch import ProfileKey, ctx_label, stem
from llama_launcher.logs import prev_path
from llama_launcher.server import State, Status
from llama_launcher.ui.icons import state_icon
from llama_launcher.ui.marquee import Marquee

RUNNING_MARK = "  ●"
HEADER_PX = 500  # minimum text width of the header; the menu is at least this wide
HEADER_MARGIN = 11  # left/right padding of the header text, close to the menu items' text inset


def open_path(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class StickyMenu(QMenu):
    """Actions marked sticky run without closing the menu, so it can be watched and used again."""

    def _sticky(self, a: QAction | None) -> bool:
        return a is not None and bool(a.property("sticky")) and a.isEnabled() and a.menu() is None

    def mouseReleaseEvent(self, e):
        a = self.actionAt(e.position().toPoint())
        if self._sticky(a):
            a.trigger()
            return
        super().mouseReleaseEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.keep_on_screen()

    def keep_on_screen(self) -> None:
        """Move the menu inside the usable screen area (not under the taskbar, not off an edge)."""
        if not self.isVisible() or self.screen() is None:
            return
        area = self.screen().availableGeometry()
        geo = self.geometry()
        x = min(max(geo.left(), area.left()), area.right() - geo.width() + 1)
        y = min(max(geo.top(), area.top()), area.bottom() - geo.height() + 1)
        if (x, y) != (geo.left(), geo.top()):
            self.move(x, y)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self._sticky(self.activeAction()):
            self.activeAction().trigger()
            return
        super().keyPressEvent(e)


def _sticky(a: QAction) -> QAction:
    a.setProperty("sticky", True)
    return a


class Header(QWidget):
    """The two status lines at the top of the menu. They take the menu's full width but never
    widen it (their text is cut to fit by the marquees), so the menu keeps one size."""

    resized = Signal()

    def __init__(self, min_text_px: int):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(HEADER_MARGIN, 5, HEADER_MARGIN, 5)
        lay.setSpacing(4)
        self.labels = (QLabel(), QLabel())
        for label in self.labels:
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            label.setEnabled(False)  # greyed like a disabled menu item
            lay.addWidget(label)
        self.setMinimumWidth(min_text_px + 2 * HEADER_MARGIN)

    def text_width(self) -> int:
        # From the header's own width: its labels are resized only after this widget's resize event.
        return max(self.width(), self.minimumWidth()) - 2 * HEADER_MARGIN

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class Tray(QObject):
    def __init__(self, controller: Controller, open_settings, quit_app):
        super().__init__()
        self.c = controller
        self.open_settings = open_settings
        self.quit_app = quit_app
        self.icon = QSystemTrayIcon(state_icon(State.STOPPED))
        self.menu = StickyMenu()
        self.menu.aboutToShow.connect(self.rebuild)
        measure = self.menu.fontMetrics().horizontalAdvance
        self.header_px = HEADER_PX
        self._marquees = (Marquee(HEADER_PX, measure), Marquee(HEADER_PX, measure))
        self._scroll = QTimer(self, interval=self._marquees[0].step_ms)
        self._scroll.timeout.connect(self._scroll_tick)
        self.menu.aboutToShow.connect(self._scroll.start)
        self.menu.aboutToHide.connect(self._scroll.stop)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(lambda: open_path(self.c.manager.server_log))
        self.c.status_changed.connect(self.on_status)
        self.c.config_changed.connect(self._config_changed)
        self.c.config_error.connect(lambda e: self.notify("Config error (last good config kept)", e, error=True))
        self.rebuild()
        self.on_status(self.c.manager.status)
        if self.c.load_error:
            self.notify("Config error", self.c.load_error, error=True)

    def show(self) -> None:
        self.icon.show()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.menu.popup(QCursor.pos())

    def _config_changed(self) -> None:
        if not self.menu.isVisible():
            self.rebuild()
        else:
            self.refresh()

    def notify(self, title: str, text: str, error: bool = False) -> None:
        if self.icon.isVisible():
            self.icon.showMessage(title, text, QSystemTrayIcon.Critical if error else QSystemTrayIcon.Information, 8000)

    def on_status(self, st: Status) -> None:
        self.icon.setIcon(state_icon(st.state))
        lines = [x for x in status_lines(st) if x]
        self.icon.setToolTip("\n".join(["llama-launcher", *lines, f"port {self.c.settings.port}"])[:127])
        if st.state is State.FAILED:
            self.notify("Model failed to start", st.message.splitlines()[0] if st.message else "", error=True)
        elif st.state is State.EXTERNAL and self.c.last_key is not None:
            self.notify("Port in use", st.message)
        self.refresh()

    # ---- building ----
    def rebuild(self) -> None:
        m = self.menu
        m.clear()
        self._profiles: dict[ProfileKey, tuple[QAction, str]] = {}
        self._models: dict[str, tuple[QMenu, str]] = {}
        self._ctx: dict[str, dict[int | None, QAction]] = {}
        self._save: dict[str, QAction] = {}
        self._rows = {catalog.norm(r.model.file): r for r in self.c.rows()}

        self.header = Header(self.header_px)
        self.header.resized.connect(self._fit_header)
        head = QWidgetAction(m)
        head.setDefaultWidget(self.header)
        m.addAction(head)
        for mq in self._marquees:
            mq.measure = self.header.labels[0].fontMetrics().horizontalAdvance
        m.addSeparator()
        if not self._rows:
            m.addAction("No models: add a model folder in Settings").setEnabled(False)
        for key, row in self._rows.items():
            name = stem(row.model.file)
            if not row.exists:
                m.addAction(f"{name} (file missing)").setEnabled(False)
                continue
            sub = StickyMenu(name, m)
            sub.setObjectName(f"model:{name}")
            m.addMenu(sub)
            self._models[key] = (sub, name)
            self._model_menu(sub, key, row.model)
        m.addSeparator()
        self._stop = _sticky(m.addAction("Stop", self.c.stop))
        self._restart = _sticky(m.addAction("Restart to apply changes", self.c.restart))
        m.addSeparator()
        self._web = m.addAction("Open web UI", self.open_web_ui)
        self._copy_id = _sticky(m.addAction("Copy model id", self.copy_model_id))
        self._copy_curl = _sticky(m.addAction("Copy curl example", self.copy_curl))
        m.addSeparator()
        log = self.c.manager.server_log
        self._log = m.addAction("Open server log", lambda: open_path(log))
        self._prev_log = m.addAction("Open previous server log", lambda: open_path(prev_path(log)))
        m.addAction("Open logs folder", lambda: open_path(paths.logs_dir()))
        m.addSeparator()
        m.addAction("Settings...", self.open_settings)
        m.addAction("Quit", self.quit_app)
        self.refresh()

    def _model_menu(self, sub: QMenu, key: str, model) -> None:
        for p in model.profiles:
            pk = ProfileKey.of(model, p)
            a = _sticky(sub.addAction(f"▶ Start {p.name}"))
            a.setObjectName(f"profile:{p.name}")
            a.setCheckable(True)
            a.triggered.connect(lambda checked=False, k=pk: self._start(k))
            self._profiles[pk] = (a, p.name)
        sub.addSeparator()
        sub.addAction("Context for the next start").setEnabled(False)
        group = QActionGroup(sub)
        self._ctx[key] = {}
        for value in (None, *CONTEXT_CHOICES):
            text = self._default_ctx_text(model) if value is None else ctx_label(value)
            a = _sticky(sub.addAction(text))
            a.setCheckable(True)
            group.addAction(a)
            a.triggered.connect(lambda checked=False, v=value, f=model.file: self._pick_ctx(f, v))
            self._ctx[key][value] = a
        sub.addSeparator()
        self._save[key] = _sticky(sub.addAction("", lambda f=model.file: self._save_ctx(f)))

    def _ctx_text(self, model, profile, use_picked=True) -> str:
        n, source = self.c.start_ctx(model, profile, use_picked)
        if source == "model":
            return f"up to {ctx_label(n)}"
        return ctx_label(n) if n else "model default"

    def _default_ctx_text(self, model) -> str:
        if len(model.profiles) != 1:
            return "Profile default"
        return f"Profile default ({self._ctx_text(model, model.profiles[0], use_picked=False)})"

    # ---- live state ----
    def header_lines(self) -> tuple[str, str]:
        return self.header.labels[0].text(), self.header.labels[1].text()

    def _show_header(self) -> None:
        for label, mq in zip(self.header.labels, self._marquees):
            label.setText(mq.view())

    def _fit_header(self) -> None:
        """Text may use the header's actual width: wider than header_px when other rows widen the menu."""
        limit = self.header.text_width()
        for mq in self._marquees:
            mq.limit = limit
        self._show_header()

    def _keep_on_screen(self) -> None:
        """If the open menu did change size, put it back inside the screen."""
        if self.menu.isVisible():
            self.menu.adjustSize()
            self.menu.keep_on_screen()

    def _scroll_tick(self) -> None:
        if any(mq.scrolls for mq in self._marquees):
            for mq in self._marquees:
                mq.tick()
            self._show_header()

    def refresh(self) -> None:
        if not hasattr(self, "header"):
            return
        st = self.c.manager.status
        first, second = status_lines(st)
        self._marquees[0].set_text(first)
        self._marquees[1].set_text(second or f"port {self.c.settings.port}")  # never empty: constant height
        self._fit_header()
        running = self.c.running_key()
        suffix = {State.STARTING: "  (starting...)", State.SLEEPING: "  (sleeping)"}.get(st.state, "")
        for pk, (a, name) in self._profiles.items():
            on = pk == running
            a.setChecked(on)
            row = self._rows[pk.file]
            ctx = f" ({self._ctx_text(row.model, row.model.profile(name))})"
            a.setText(f"↻ Restart {name}{ctx}{suffix}" if on else f"▶ Start {name}{ctx}")
        for key, (sub, name) in self._models.items():
            sub.setTitle(name + (RUNNING_MARK if running and running.file == key else ""))
            picked = self.c.next_ctx(key)
            row = self._rows[key]
            for value, a in self._ctx[key].items():
                a.setChecked(value == picked)
                if value is None:
                    a.setText(self._default_ctx_text(row.model))
            target = self.c.ctx_save_target(row.model)
            save = self._save[key]
            save.setText(f"Save picked context as default for '{target.name}'" if target
                         else "Save picked context as default (start a profile first)")
            save.setEnabled(bool(picked) and target is not None)
        active = st.state in (State.STARTING, State.RUNNING, State.SLEEPING)
        serving = st.state in (State.RUNNING, State.SLEEPING, State.EXTERNAL)
        self._stop.setEnabled(active)
        self._restart.setVisible(self.c.is_stale())
        for a in (self._web, self._copy_id, self._copy_curl):
            a.setEnabled(serving)
        log = self.c.manager.server_log
        self._log.setEnabled(log.exists())
        self._prev_log.setEnabled(prev_path(log).exists())
        self._keep_on_screen()

    def _start(self, key: ProfileKey) -> None:
        self.c.start(key)
        self.refresh()

    def _pick_ctx(self, model_file: str, value: int | None) -> None:
        self.c.set_next_ctx(model_file, value)
        self.refresh()

    def _save_ctx(self, model_file: str) -> None:
        self.c.save_ctx_default(model_file)
        self._rows.update({k: r for r in self.c.rows() if (k := catalog.norm(r.model.file)) in self._rows})
        self.refresh()

    # ---- actions ----
    def open_web_ui(self) -> None:
        QDesktopServices.openUrl(QUrl(self.c.base_url() + "/"))

    def copy_model_id(self) -> None:
        QGuiApplication.clipboard().setText(self.c.served_alias())

    def copy_curl(self) -> None:
        QGuiApplication.clipboard().setText(self.c.curl_example())
