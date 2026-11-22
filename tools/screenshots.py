"""Renders the README logo and screenshots (made-up models, test fake server): docs/images/*.png.

Machine paths are swapped for neutral ones before capture, and the run fails if any remain."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import gguf_builder as gb  # noqa: E402
from helpers import FAKE, FIXTURES, free_port  # noqa: E402
from PySide6.QtCore import QPoint, QRect, QSignalBlocker, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTableWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItemIterator,
    QWidget,
)

OUT = ROOT / "docs" / "images"
MODELS_SHOWN = r"D:\models"
RUNTIME_SHOWN = r"D:\llama.cpp\b11860"
HOME_SHOWN = r"%LOCALAPPDATA%\llama-launcher"


def model_folder(d: Path) -> Path:
    gb.model(d / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "qwen35moe", 2048)
    gb.model(d / "gemma-4-31B-it-Q4_K_M.gguf", "gemma4", 5376)
    gb.mmproj(d / "mmproj-gemma-4-31B-it-F16.gguf", 5376)
    gb.model(d / "Inkling-2-Mini-UD-Q4_K_M.gguf", "inkling2", 3072, ctx=1048576)
    gb.mmproj(d / "mmproj-Inkling-2-Mini-F16.gguf", 3072)
    gb.model(d / "gpt-oss-20b-MXFP4.gguf", "gpt-oss", 2880, ctx=131072)
    return d


def wait(app: QApplication, ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(app: QApplication, pred, timeout_ms: int = 15000) -> None:
    for _ in range(timeout_ms // 50):
        if pred():
            return
        wait(app, 50)
    raise TimeoutError("condition not met")


class Scrubber:
    """Replaces machine paths in every visible text of a widget tree."""

    def __init__(self, pairs: list[tuple[str, str]], forbidden: list[str]):
        self.rules = [(re.compile(re.escape(a).replace(r"\\", r"[\\/]"), re.I), b) for a, b in pairs]
        self.forbidden = [f.lower() for f in forbidden]

    def fix(self, s: str) -> str:
        for rx, b in self.rules:
            s = rx.sub(b.replace("\\", "\\\\"), s)
        return s

    def apply(self, root: QWidget) -> None:
        for w in [root, *root.findChildren(QWidget)]:
            with QSignalBlocker(w):
                if isinstance(w, QLabel | QLineEdit):
                    w.setText(self.fix(w.text()))
                elif isinstance(w, QPlainTextEdit | QTextEdit):
                    w.setPlainText(self.fix(w.toPlainText()))
                elif isinstance(w, QComboBox):
                    for i in range(w.count()):
                        w.setItemText(i, self.fix(w.itemText(i)))
                    if w.isEditable():
                        w.setEditText(self.fix(w.currentText()))
                elif isinstance(w, QTreeWidget):
                    it = QTreeWidgetItemIterator(w)
                    while it.value():
                        item = it.value()
                        for c in range(item.columnCount()):
                            item.setText(c, self.fix(item.text(c)))
                        it += 1
                elif isinstance(w, QListWidget):
                    for i in range(w.count()):
                        w.item(i).setText(self.fix(w.item(i).text()))
                elif isinstance(w, QTableWidget):
                    for r in range(w.rowCount()):
                        for c in range(w.columnCount()):
                            if w.item(r, c):
                                w.item(r, c).setText(self.fix(w.item(r, c).text()))
        self.check(root)

    def check(self, root: QWidget) -> None:
        texts = []
        for w in [root, *root.findChildren(QWidget)]:
            for getter in ("text", "toPlainText", "currentText"):
                if hasattr(w, getter):
                    try:
                        texts.append(str(getattr(w, getter)()))
                    except TypeError:
                        pass
            if isinstance(w, QTreeWidget):
                it = QTreeWidgetItemIterator(w)
                while it.value():
                    texts += [it.value().text(c) for c in range(it.value().columnCount())]
                    it += 1
            if isinstance(w, QComboBox):
                texts += [w.itemText(i) for i in range(w.count())]
            if isinstance(w, QTableWidget):
                texts += [
                    w.item(r, c).text() for r in range(w.rowCount()) for c in range(w.columnCount()) if w.item(r, c)
                ]
        leaks = [t for t in texts if any(f in t.lower() for f in self.forbidden)]
        if leaks:
            raise SystemExit(f"machine-specific text left in {root.__class__.__name__}: {leaks}")


def framed(shots: list[tuple[QPixmap, QPoint]], pad: int = 14) -> QPixmap:
    """Places captures on a transparent canvas with rounded corners and a shadow."""
    dpr = shots[0][0].devicePixelRatio()
    rects = [QRect(pos, pm.deviceIndependentSize().toSize()) for pm, pos in shots]
    box = rects[0]
    for r in rects[1:]:
        box = box.united(r)
    out = QPixmap((box.size().width() + 2 * pad) * dpr, (box.size().height() + 2 * pad) * dpr)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    for (pm, _), r in zip(shots, rects, strict=True):
        r = r.translated(pad - box.left(), pad - box.top())
        for i in range(10, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(r.adjusted(-i, -i + 3, i, i + 3), 8 + i, 8 + i)
            p.fillPath(shadow, QColor(0, 0, 0, 6))
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(r.topLeft(), pm)
        p.restore()
    p.end()
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ll-shots-"))
    os.environ["LLAMA_LAUNCHER_HOME"] = str(tmp / "home")
    for k in [k for k in os.environ if k.startswith("LLAMA_ARG_")]:
        del os.environ[k]

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    from llama_launcher.__main__ import App
    from llama_launcher.config import Config, GlobalOptions, ModelEntry, Profile, RuntimeEntry
    from llama_launcher.controller import Controller
    from llama_launcher.launch import ProfileKey
    from llama_launcher.runtimes import Runtime
    from llama_launcher.server import ServerManager, State

    llm = model_folder(tmp / "models")
    cfg = Config(tmp / "home" / "settings.toml", tmp / "home" / "profiles.toml")
    cfg.load()
    cfg.settings.port = free_port()
    cfg.settings.model_folders = [str(llm)]
    cfg.settings.runtimes = [RuntimeEntry("b11860", str(FIXTURES))]
    cfg.settings.global_ = GlobalOptions(idle_mode="sleep", idle_seconds=900, args="--flash-attn on")
    cfg.save_settings()
    qwen = str(llm / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    gemma = str(llm / "gemma-4-31B-it-Q4_K_M.gguf")
    cfg.models = [
        ModelEntry(
            qwen,
            profiles=[
                Profile(
                    "general",
                    ctx_size=262144,
                    args="--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 1.5",
                ),
                Profile("coding", ctx_size=131072, args="--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0"),
            ],
        ),
        ModelEntry(gemma, mmproj=str(llm / "mmproj-gemma-4-31B-it-F16.gguf"), profiles=[Profile("vision")]),
    ]
    cfg.save_profiles()

    mgr = ServerManager(tmp / "home" / "logs", tmp / "cwd", ready_timeout=15, poll=0.05, idle_poll=0.2)
    c = Controller(cfg, mgr, runtime_factory=lambda e: Runtime(e.name, e.path, command_prefix=FAKE))
    a = App(c, instance_name=f"llama-launcher-shots-{os.getpid()}")
    scrub = Scrubber(
        [
            (" ".join(FAKE), RUNTIME_SHOWN + r"\llama-server.exe"),
            (f"--port {cfg.settings.port}", "--port 8080"),
            (str(llm), MODELS_SHOWN),
            (str(FIXTURES), RUNTIME_SHOWN),
            (str(tmp / "home"), HOME_SHOWN),
            (str(tmp), "D:"),
        ],
        [str(tmp), str(ROOT), str(Path.home()), os.environ.get("USERNAME", "") or str(tmp)],
    )
    OUT.mkdir(parents=True, exist_ok=True)
    from llama_launcher.ui.icons import BRAND, badge

    badge(BRAND, 256).save(str(OUT / "logo.png"))
    try:
        c.start(ProfileKey(qwen.lower(), "coding"))
        wait_until(app, lambda: c.manager.status.state is State.RUNNING)

        # tray menu with the running model's submenu open
        a.tray.rebuild()
        menu = a.tray.menu
        area = QGuiApplication.primaryScreen().availableGeometry()
        menu.popup(area.topLeft() + QPoint(40, 40))
        wait(app, 400)
        act = next(x for x in menu.actions() if x.menu() and x.text().startswith("Qwen3.6-35B"))
        menu.setActiveAction(act)
        sub = act.menu()
        geo = menu.actionGeometry(act)
        sub.popup(menu.mapToGlobal(QPoint(menu.width() - 4, geo.top() - 4)))
        wait(app, 400)
        scrub.check(menu)
        pos_menu, pos_sub = menu.pos(), sub.pos()
        tray = framed([(menu.grab(), pos_menu), (sub.grab(), pos_sub)])
        tray.save(str(OUT / "tray-menu.png"))
        sub.hide()
        menu.hide()

        # settings: profiles tab with a profile selected
        w = a.open_settings()
        w.resize(1100, 720)
        w.tabs.setCurrentWidget(w.profiles_tab)
        tree = w.profiles_tab.tree
        top = next(
            tree.topLevelItem(i)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(0).startswith("Qwen3.6-35B")
        )
        tree.setCurrentItem(top.child(1))
        wait(app, 400)
        scrub.apply(w)
        wait(app, 100)
        framed([(w.grab(), QPoint(0, 0))]).save(str(OUT / "settings-profiles.png"))
        w.dirty = False
        w.close()
    finally:
        c.shutdown()
        a.tray.icon.hide()
    print("\n".join(str(p.relative_to(ROOT)) for p in sorted(OUT.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
