"""System tray icon and menu."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from llama_launcher import paths
from llama_launcher.config import Profile
from llama_launcher.controller import CONTEXT_CHOICES, Controller, CtxSource, status_lines
from llama_launcher.launch import ModelRow, ProfileKey, ctx_label, stem
from llama_launcher.logs import prev_path
from llama_launcher.server import State, Status
from llama_launcher.ui.icons import dot_icon, emoji_icon, state_icon
from llama_launcher.ui.log_view import show_log
from llama_launcher.ui.marquee import Marquee
from llama_launcher.ui.widgets import open_path

MODEL_ICON = "🦙"
DOT_GAP = 8  # between the header's state dot and its text
HEADER_PX = 500  # minimum text width of the header; the menu is at least this wide
HEADER_MARGIN = 11  # left/right padding of the header text, close to the menu items' text inset


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


def _label(text: str) -> str:
    """Menu text shown as-is: a lone & would otherwise become a keyboard-shortcut underline."""
    return text.replace("&", "&&")


def _sticky(a: QAction) -> QAction:
    a.setProperty("sticky", True)
    return a


class Header(QWidget):
    """A state dot and the two status lines at the top of the menu. The lines take the menu's full width
    but never widen it (their text is cut to fit by the marquees), so the menu keeps one size."""

    resized = Signal()

    def __init__(self, min_text_px: int):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(HEADER_MARGIN, 5, HEADER_MARGIN, 5)
        row.setSpacing(DOT_GAP)
        self.dot_px = self.style().pixelMetric(QStyle.PM_SmallIconSize)
        self.dot = QLabel()
        self.dot.setFixedSize(self.dot_px, self.dot_px)
        row.addWidget(self.dot, 0, Qt.AlignVCenter)
        lines = QVBoxLayout()
        lines.setSpacing(4)
        row.addLayout(lines, 1)
        self.labels = (QLabel(), QLabel())
        for label in self.labels:
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            label.setEnabled(False)  # greyed like a disabled menu item
            lines.addWidget(label)
        self.setMinimumWidth(min_text_px + self._chrome())

    def _chrome(self) -> int:
        return 2 * HEADER_MARGIN + self.dot_px + DOT_GAP

    def set_state(self, state: State) -> None:
        self.dot.setPixmap(dot_icon(state).pixmap(self.dot_px, self.dot_px))

    def text_width(self) -> int:
        # From the header's own width: its labels are resized only after this widget's resize event.
        return max(self.width(), self.minimumWidth()) - self._chrome()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class ModelMenu:
    """One model's submenu: Start/Restart per profile, the context picker and save-as-default."""

    def __init__(self, parent: QMenu, controller: Controller, row: ModelRow, changed: Callable[[], None]):
        self.c = controller
        self.row = row
        self.key = paths.norm(row.model.file)
        self.name = stem(row.model.file)
        self._changed = changed
        self.menu = StickyMenu(_label(self.name), parent)
        self.menu.setObjectName(f"model:{self.name}")

        self._starts: dict[str, QAction] = {}
        for p in row.model.profiles:
            a = _sticky(self.menu.addAction(_label(f"Start {p.name}")))
            a.setObjectName(f"profile:{p.name}")
            a.setCheckable(True)
            a.triggered.connect(lambda checked=False, name=p.name: self._start(name))
            self._starts[p.name] = a
        self.menu.addSeparator()
        self.menu.addAction("Context for the next start").setEnabled(False)
        group = QActionGroup(self.menu)
        self._ctx: dict[int | None, QAction] = {}
        for value in (None, *CONTEXT_CHOICES):
            a = _sticky(self.menu.addAction(self._default_ctx_text() if value is None else ctx_label(value)))
            a.setCheckable(True)
            group.addAction(a)
            a.triggered.connect(lambda checked=False, v=value: self._pick_ctx(v))
            self._ctx[value] = a
        self.menu.addSeparator()
        self._save = _sticky(self.menu.addAction(emoji_icon("💾"), "", self._save_ctx))

    def refresh(self, running: ProfileKey | None, suffix: str) -> None:
        model = self.row.model
        for name, a in self._starts.items():
            on = running == ProfileKey(self.key, name)
            a.setChecked(on)
            ctx = f" ({self._ctx_text(model.profile(name))})"
            a.setText(_label(f"Restart {name}{ctx}{suffix}" if on else f"Start {name}{ctx}"))
            a.setIcon(emoji_icon("🔄" if on else "▶️"))
        self.running = running is not None and running.file == self.key
        self.menu.setIcon(dot_icon(State.RUNNING) if self.running else emoji_icon(MODEL_ICON))
        picked = self.c.next_ctx(self.key)
        for value, a in self._ctx.items():
            a.setChecked(value == picked)
            if value is None:
                a.setText(self._default_ctx_text())
        target = self.c.ctx_save_target(model)
        self._save.setText(
            f"Save picked context as default for '{target.name}'"
            if target
            else "Save picked context as default (start a profile first)"
        )
        self._save.setEnabled(bool(picked) and target is not None)

    def _ctx_text(self, profile: Profile, use_picked: bool = True) -> str:
        n, source = self.c.start_ctx(self.row.model, profile, use_picked)
        if source is CtxSource.MODEL:
            return f"up to {ctx_label(n)}"
        return ctx_label(n) if n else "model default"

    def _default_ctx_text(self) -> str:
        profiles = self.row.model.profiles
        if len(profiles) != 1:
            return "Profile default"
        return f"Profile default ({self._ctx_text(profiles[0], use_picked=False)})"

    def _start(self, profile_name: str) -> None:
        self.c.start(ProfileKey(self.key, profile_name))
        self._changed()

    def _pick_ctx(self, value: int | None) -> None:
        self.c.set_next_ctx(self.row.model.file, value)
        self._changed()

    def _save_ctx(self) -> None:
        if self.c.save_ctx_default(self.row.model.file):
            # the save replaced the config's model objects (and made an implicit model real)
            self.row = next((r for r in self.c.rows() if paths.norm(r.model.file) == self.key), self.row)
        self._changed()


class Tray(QObject):
    def __init__(self, controller: Controller, open_settings: Callable[[], object], quit_app: Callable[[], None]):
        super().__init__()
        self.c = controller
        self.open_settings = open_settings
        self.quit_app = quit_app
        self.icon = QSystemTrayIcon(state_icon(State.STOPPED))
        self.icon.setToolTip("llama-launcher")
        self.menu = StickyMenu()
        self.menu.aboutToShow.connect(self.rebuild)
        measure = self.menu.fontMetrics().horizontalAdvance
        self.header_px = HEADER_PX
        self._marquees = (Marquee(HEADER_PX, measure), Marquee(HEADER_PX, measure))
        self._model_menus: list[ModelMenu] = []
        self._scroll = QTimer(self, interval=self._marquees[0].step_ms)
        self._scroll.timeout.connect(self._scroll_tick)
        self.menu.aboutToShow.connect(self._scroll.start)
        self.menu.aboutToHide.connect(self._scroll.stop)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(lambda: open_path(self.c.manager.server_log))
        self._prev: Status | None = None
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
        elif st.state is State.EXTERNAL and self.c.last_key is not None and not self._ids_only_change(st):
            self.notify("Port in use", st.message)
        self._prev = st
        self.refresh()

    def _ids_only_change(self, st: Status) -> bool:
        # The port watch re-reports EXTERNAL when the other server's model list changes (e.g. once it finishes loading).
        p = self._prev
        return (
            p is not None
            and p.state is State.EXTERNAL
            and p.message == st.message
            and p.external_ids != st.external_ids
        )

    # ---- building ----
    def rebuild(self) -> None:
        m = self.menu
        m.clear()
        self.header = Header(self.header_px)
        self.header.resized.connect(self._fit_header)
        head = QWidgetAction(m)
        head.setDefaultWidget(self.header)
        m.addAction(head)
        for mq in self._marquees:
            mq.measure = self.header.labels[0].fontMetrics().horizontalAdvance
        m.addSeparator()
        rows = self.c.rows()
        if not rows:
            m.addAction(emoji_icon("➕"), "No models: add a model folder in Settings").setEnabled(False)
        self._model_menus = []
        for row in rows:
            if not row.exists:
                m.addAction(emoji_icon("⚠️"), _label(f"{stem(row.model.file)} (file missing)")).setEnabled(False)
                continue
            mm = ModelMenu(m, self.c, row, self.refresh)
            m.addMenu(mm.menu)
            self._model_menus.append(mm)
        m.addSeparator()
        self._stop = _sticky(m.addAction(emoji_icon("⏹️"), "Stop", self.c.stop))
        self._restart = _sticky(m.addAction(emoji_icon("🔄"), "Restart to apply changes", self.c.restart))
        m.addSeparator()
        self._web = m.addAction(emoji_icon("🌐"), "Open web UI", self.open_web_ui)
        self._copy_id = _sticky(m.addAction(emoji_icon("🏷️"), "Copy model id", self.copy_model_id))
        self._copy_curl = _sticky(m.addAction(emoji_icon("📋"), "Copy curl example", self.copy_curl))
        m.addSeparator()
        log = self.c.manager.server_log
        logs = StickyMenu("Logs", m)
        logs.setIcon(emoji_icon("🗂️"))
        m.addMenu(logs)
        logs.addAction(emoji_icon("👁️"), "View server log (live)", lambda: show_log(log))
        self._log = logs.addAction(emoji_icon("📄"), "Open server log", lambda: open_path(log))
        self._prev_log = logs.addAction(emoji_icon("📜"), "Open previous server log", lambda: open_path(prev_path(log)))
        logs.addAction(emoji_icon("📂"), "Open logs folder", lambda: open_path(paths.logs_dir()))
        m.addSeparator()
        m.addAction(emoji_icon("⚙️"), "Settings...", self.open_settings)
        m.addAction(emoji_icon("🚪"), "Quit", self.quit_app)
        self.refresh()

    # ---- live state ----
    def header_lines(self) -> tuple[str, str]:
        return self.header.labels[0].text(), self.header.labels[1].text()

    def _show_header(self) -> None:
        for label, mq in zip(self.header.labels, self._marquees, strict=True):
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
        st = self.c.manager.status
        first, second = status_lines(st)
        self._marquees[0].set_text(first)
        self._marquees[1].set_text(second or f"port {self.c.settings.port}")  # never empty: constant height
        self.header.set_state(st.state)
        self._fit_header()
        running = self.c.running_key()
        suffix = {State.STARTING: "  (starting...)", State.SLEEPING: "  (sleeping)"}.get(st.state, "")
        for mm in self._model_menus:
            mm.refresh(running, suffix)
        serving = st.state in (State.RUNNING, State.SLEEPING, State.EXTERNAL)
        self._stop.setEnabled(st.active)
        self._restart.setVisible(self.c.is_stale())
        for a in (self._web, self._copy_id, self._copy_curl):
            a.setEnabled(serving)
        log = self.c.manager.server_log
        self._log.setEnabled(log.exists())
        self._prev_log.setEnabled(prev_path(log).exists())
        self._keep_on_screen()

    # ---- actions ----
    def open_web_ui(self) -> None:
        QDesktopServices.openUrl(QUrl(self.c.base_url() + "/"))

    def copy_model_id(self) -> None:
        QGuiApplication.clipboard().setText(self.c.served_alias())

    def copy_curl(self) -> None:
        QGuiApplication.clipboard().setText(self.c.curl_example())
