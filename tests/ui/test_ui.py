import subprocess
import sys
import uuid
import winreg
from pathlib import Path

import gguf_builder as gb
import pytest
from conftest import FIXTURES
from helpers import FAKE, free_port
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu

from llama_launcher import startup
from llama_launcher.__main__ import App
from llama_launcher.config import Config, GlobalOptions, ModelEntry, Profile, RuntimeEntry
from llama_launcher.controller import Controller
from llama_launcher.launch import ProfileKey
from llama_launcher.runtimes import Runtime
from llama_launcher.server import ServerManager, State, Status
from llama_launcher.single_instance import QUIT, SHOW_SETTINGS

TEST_RUN_KEY = r"Software\llama-launcher-test\Run"
ROOT = Path(__file__).parents[2]


@pytest.fixture
def env(tmp_path, qtbot):
    llm = gb.llm_folder(tmp_path / "LLM")
    cfg = Config(tmp_path / "home" / "settings.toml", tmp_path / "home" / "profiles.toml")
    cfg.load()
    cfg.settings.port = free_port()
    cfg.settings.model_folders = [str(llm)]
    cfg.settings.runtimes = [RuntimeEntry("fake", str(FIXTURES))]
    cfg.settings.global_ = GlobalOptions(idle_mode="off", args="--flash-attn on")
    cfg.save_settings()
    swift = str(llm / "Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS.gguf")
    cfg.models = [
        ModelEntry(
            swift,
            profiles=[
                Profile("swift-orca", ctx_size=102400),
                Profile("mtp", args="--spec-type draft-mtp --spec-draft-n-max 2"),
            ],
        )
    ]
    cfg.save_profiles()
    mgr = ServerManager(tmp_path / "logs", tmp_path / "cwd", ready_timeout=10, poll=0.05, idle_poll=0.2)
    c = Controller(cfg, mgr, runtime_factory=lambda e: Runtime(e.name, e.path, command_prefix=FAKE))
    c.startup_key = TEST_RUN_KEY
    app = App(c, instance_name=f"llama-launcher-test-{uuid.uuid4().hex}")
    yield app, c, llm, swift
    if app.window:
        app.window.dirty = False
        app.window.close()
    c.shutdown()
    app.tray.icon.hide()
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY)
    except OSError:
        pass


def actions(menu: QMenu) -> dict:
    out = {}
    for a in menu.actions():
        if a.menu():
            out[a.text()] = actions(a.menu())
        elif a.objectName().startswith("profile:"):
            out[a.objectName()] = a
        elif a.text():
            out[a.text()] = a
    return out


def second_launch(name: str, message: bytes) -> subprocess.Popen:
    """Sends message the way a second app launch does: from its own process, while this one keeps serving."""
    code = (
        "import sys\n"
        "from PySide6.QtCore import QCoreApplication\n"
        "from llama_launcher.single_instance import notify_existing\n"
        "app = QCoreApplication([])\n"
        f"sys.exit(0 if notify_existing({name!r}, {message!r}) else 1)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)


# U1 + U10
def test_settings_single_instance(env, qtbot):
    app, *_ = env
    assert app.window is None
    launch = second_launch(app.instance.name, SHOW_SETTINGS)
    qtbot.waitUntil(lambda: app.window is not None and app.window.isVisible(), timeout=10000)
    assert launch.wait(10) == 0  # it found the instance, so a real second launch would exit here
    assert app.open_settings() is app.window
    assert len([w for w in QApplication.topLevelWidgets() if w.__class__.__name__ == "SettingsWindow"]) == 1


# U11
def test_quit_message_quits_like_the_tray(env, qtbot, monkeypatch):
    app, *_ = env
    quits = []
    monkeypatch.setattr(app, "quit", lambda: quits.append(True))  # the real one would end the test's Qt app
    launch = second_launch(app.instance.name, QUIT)
    qtbot.waitUntil(lambda: quits == [True], timeout=10000)
    assert launch.wait(10) == 0
    assert app.window is None  # it didn't open Settings instead


# U2
def test_settings_is_taskbar_window(env):
    app, *_ = env
    w = app.open_settings()
    flags = w.windowFlags()
    assert w.isWindow() and w.parent() is None
    assert (flags & Qt.Tool) != Qt.Tool and (flags & Qt.SplashScreen) != Qt.SplashScreen


# U3
def test_tray_menu_from_config(env):
    app, c, llm, swift = env
    app.tray.rebuild()
    menu = actions(app.tray.menu)
    swift_menu = menu["Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS"]
    assert list(swift_menu)[:3] == ["profile:swift-orca", "profile:mtp", "Context for the next start"]
    assert swift_menu["profile:mtp"].text() == "Start mtp (up to 256k)"
    assert swift_menu["profile:swift-orca"].text() == "Start swift-orca (100k)"
    assert swift_menu["Profile default"].isChecked() and "128k" in swift_menu
    single = menu["Qwen3.6-35B-A3B-UD-Q4_K_M"]
    assert "profile:default_profile" in single and "Profile default (up to 256k)" in single
    assert not menu["Stop"].isEnabled() and not menu["Restart to apply changes"].isVisible()
    for t in ("Open web UI", "Copy model id", "Copy curl example"):
        assert not menu[t].isEnabled()


# U4 + live refresh: the open menu follows the server state without a rebuild
def test_start_from_tray_updates_menu_live(env, qtbot):
    app, c, llm, swift = env
    app.tray.rebuild()
    menu = actions(app.tray.menu)
    sub = menu["Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS"]
    sub["profile:mtp"].trigger()

    def head():
        return app.tray.header_lines()[0]

    qtbot.waitUntil(lambda: c.manager.status.state is State.RUNNING, timeout=10000)
    qtbot.waitUntil(lambda: head().startswith("Running"))
    assert sub["profile:mtp"].isChecked() and sub["profile:mtp"].text() == "Restart mtp (up to 256k)"
    assert menu["Stop"].isEnabled() and menu["Open web UI"].isEnabled()
    assert [mm.running for mm in app.tray._model_menus if mm.name.startswith("Qwen3.8-27B-Swift")] == [True]
    menu["Stop"].trigger()
    qtbot.waitUntil(lambda: head() == "Stopped")
    assert not sub["profile:mtp"].isChecked() and sub["profile:mtp"].text() == "Start mtp (up to 256k)"


# clicks on sticky items keep the menu open
def test_menu_stays_open_on_start(env, qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    app, c, llm, swift = env
    app.tray.rebuild()
    menu = app.tray.menu
    menu.popup(QPoint(200, 200))
    qtbot.waitExposed(menu)
    stop = next(a for a in menu.actions() if a.text() == "Stop")
    stop.setEnabled(True)
    hits = []
    stop.triggered.connect(lambda: hits.append(1))
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(stop).center())
    assert hits and menu.isVisible()
    settings = next(a for a in menu.actions() if a.text() == "Settings...")
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(settings).center())
    qtbot.waitUntil(lambda: not menu.isVisible())


def test_failure_shown_in_tooltip(env, qtbot):
    app, c, llm, swift = env
    gsq = str(llm / "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf")
    c.config.models.append(ModelEntry(gsq, profiles=[Profile("p", args="--spec-type draft-mtp")]))
    c.start(ProfileKey(gsq.lower(), "p"))
    qtbot.waitUntil(lambda: "Failed" in app.tray.icon.toolTip())
    assert "built-in MTP" in c.manager.status.message


# U5 + U6
def test_profile_editor_preview_updates_live(env, qtbot):
    app, c, llm, swift = env
    w = app.open_settings()
    w.tabs.setCurrentIndex(4)
    top = next(
        w.profiles_tab.tree.topLevelItem(i)
        for i in range(w.profiles_tab.tree.topLevelItemCount())
        if w.profiles_tab.tree.topLevelItem(i).text(0).startswith("Qwen3.8-27B-Swift")
    )
    w.profiles_tab.tree.setCurrentItem(top.child(1))
    assert "--spec-type draft-mtp" in w.profiles_tab.editor.preview.toPlainText()
    assert w.profiles_tab.editor.ctx.inherit.isChecked()
    w.profiles_tab.editor.ctx.inherit.setChecked(False)
    w.profiles_tab.editor.ctx.spin.setValue(65536)
    assert "--ctx-size 65536" in w.profiles_tab.editor.preview.toPlainText() and w.dirty
    assert c.config.models[0].profiles[1].ctx_size == 0  # not saved yet
    # mmproj dropdown lists only the 5120-dim projector; picking another file shows an error
    items = [w.profiles_tab.editor.mmproj.combo.itemData(i) for i in range(w.profiles_tab.editor.mmproj.combo.count())]
    assert [p.split("\\")[-1] for p in items if p] == ["Qwen3.8-27B-mmproj-blackfrost-ABLITERATED-Q8_0.gguf"]
    w.profiles_tab.editor.mmproj.combo.addItem("wrong", str(llm / "gemma-4-12B-it-qat-mmproj-F16.gguf"))
    w.profiles_tab.editor.mmproj.combo.setCurrentIndex(w.profiles_tab.editor.mmproj.combo.count() - 1)
    assert "doesn't fit this model" in w.profiles_tab.editor.problems.text()
    w.save()
    c.config.load()
    assert c.config.models[0].profiles[1].ctx_size == 65536


def test_editing_implicit_model_materializes_it(env):
    app, c, llm, swift = env
    w = app.open_settings()
    top = next(
        w.profiles_tab.tree.topLevelItem(i)
        for i in range(w.profiles_tab.tree.topLevelItemCount())
        if w.profiles_tab.tree.topLevelItem(i).text(0) == "gemma-4-12B-it-qat-UD-Q4_K_XL"
    )
    w.profiles_tab.tree.setCurrentItem(top.child(0))
    assert "--spec-draft-model" in w.profiles_tab.editor.preview.toPlainText()
    w.profiles_tab.editor.args.setPlainText("--spec-type draft-mtp --spec-draft-n-max 3")
    w.save()
    c.config.load()
    gemma = c.config.model(str(llm / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"))
    assert gemma and gemma.draft.endswith("mtp-gemma-4-12B-it-Q4_0.gguf") and gemma.profiles[0].args.endswith("3")


# U7: context is picked per model without starting; it applies on the next start
def test_context_picker_and_save_default(env, qtbot):
    app, c, llm, swift = env
    app.tray.rebuild()
    sub = actions(app.tray.menu)["Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS"]
    sub["32k"].trigger()
    assert c.manager.status.state is State.STOPPED and sub["32k"].isChecked()
    assert sub["profile:swift-orca"].text() == "Start swift-orca (32k)"
    assert sub["profile:mtp"].text() == "Start mtp (32k)"
    assert not sub["Save picked context as default (start a profile first)"].isEnabled()
    sub["profile:swift-orca"].trigger()
    qtbot.waitUntil(lambda: c.manager.status.state is State.RUNNING, timeout=10000)
    assert "32768" in c.manager.status.spec.argv
    c.config.load()
    assert c.config.models[0].profile("swift-orca").ctx_size == 102400
    sub["64k"].trigger()  # running model: pick a new context -> restart offered
    assert actions(app.tray.menu)["Restart to apply changes"].isVisible()
    sub = actions(app.tray.menu)["Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS"]  # labels updated live
    sub["Save picked context as default for 'swift-orca'"].trigger()
    text = c.config.profiles_path.read_text()
    assert text.count("ctx_size") == 1 and "ctx_size = 65536" in text
    assert c.next_ctx(swift) is None


# U8
def test_copy_model_id_and_curl(env, qtbot):
    app, c, llm, swift = env
    c.config.settings.api_key = "dummy"
    c.start(ProfileKey(swift.lower(), "mtp"))
    qtbot.waitUntil(lambda: c.manager.status.state is State.RUNNING, timeout=10000)
    app.tray.copy_model_id()
    assert QGuiApplication.clipboard().text() == "Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS"
    app.tray.copy_curl()
    curl = QGuiApplication.clipboard().text()
    assert f"http://127.0.0.1:{c.settings.port}/v1/chat/completions" in curl
    assert "Authorization: Bearer dummy" in curl and "Qwen3.8-27B-Swift" in curl


# U9
def test_launch_at_startup_toggle(env):
    app, c, *_ = env
    w = app.open_settings()
    w.general_tab.startup.setChecked(True)
    w.save()
    assert startup.is_enabled(TEST_RUN_KEY)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY) as k:
        assert "-m llama_launcher" in winreg.QueryValueEx(k, startup.VALUE)[0]
    w.general_tab.startup.setChecked(False)
    w.save()
    assert not startup.is_enabled(TEST_RUN_KEY)


# P7 through the UI: restart badge
def test_restart_badge_after_edit(env, qtbot):
    app, c, llm, swift = env
    c.start(ProfileKey(swift.lower(), "mtp"))
    qtbot.waitUntil(lambda: c.manager.status.state is State.RUNNING, timeout=10000)
    c.config.models[0].profile("mtp").args += " --temp 0.6"
    c.config.save_profiles()
    app.tray.rebuild()
    menu = actions(app.tray.menu)
    assert menu["Restart to apply changes"].isVisible()
    menu["Restart to apply changes"].trigger()
    qtbot.waitUntil(
        lambda: c.manager.status.state is State.RUNNING and "0.6" in c.manager.status.spec.argv, timeout=10000
    )
    app.tray.rebuild()
    assert not actions(app.tray.menu)["Restart to apply changes"].isVisible()


# F4: hand edit picked up by the watcher, broken edit keeps last good config
def test_hand_edit_reloads(env, qtbot):
    app, c, llm, swift = env
    errors = []
    c.config_error.connect(errors.append)
    text = c.config.profiles_path.read_text().replace('name = "mtp"', 'name = "mtp2"')
    c.config.profiles_path.write_text(text)
    qtbot.waitUntil(lambda: c.config.models[0].profiles[1].name == "mtp2", timeout=5000)
    c.config.profiles_path.write_text("[[models]]\n")
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    assert c.config.models[0].profiles[1].name == "mtp2"


def test_inherit_toggle_and_minutes(env):
    app, c, llm, swift = env
    w = app.open_settings()
    top = next(
        w.profiles_tab.tree.topLevelItem(i)
        for i in range(w.profiles_tab.tree.topLevelItemCount())
        if w.profiles_tab.tree.topLevelItem(i).text(0).startswith("Qwen3.8-27B-Swift")
    )
    w.profiles_tab.tree.setCurrentItem(top.child(0))
    assert not w.profiles_tab.editor.ctx.inherit.isChecked() and w.profiles_tab.editor.ctx.spin.value() == 102400
    w.profiles_tab.editor.ctx.inherit.setChecked(True)  # back to inherit
    assert (
        w.profiles_tab.editor.profile.ctx_size == 0 and "--ctx-size" not in w.profiles_tab.editor.preview.toPlainText()
    )
    w.profiles_tab.editor.idle_mode.setCurrentIndex(w.profiles_tab.editor.idle_mode.findData("sleep"))
    w.profiles_tab.editor.idle_seconds.inherit.setChecked(False)
    w.profiles_tab.editor.idle_seconds.spin.setValue(5)
    assert (
        w.profiles_tab.editor.profile.idle_seconds == 300
        and "--sleep-idle-seconds 300" in w.profiles_tab.editor.preview.toPlainText()
    )
    w.global_tab.idle_s.setValue(90)
    assert w.settings.global_.idle_seconds == 5400 and w.global_tab.idle_s.suffix() == " min"


def test_rescan_reports_counts(env):
    app, c, llm, swift = env
    w = app.open_settings()
    gb.model(llm / "new-model.gguf", "qwen35", 5120)
    w.folders_tab.rescan()
    assert "7 models, 3 mmproj, 1 draft" in w.folders_tab.scan_status.text()
    assert any(e.path.endswith("new-model.gguf") for e in c.entries)


def test_listen_hint(env):
    app, c, *_ = env
    w = app.open_settings()
    assert w.general_tab.listen_hint.text().startswith("This PC only")
    w.general_tab.listen.setCurrentText("0.0.0.0")
    assert w.general_tab.listen_hint.text().startswith("All networks") and w.settings.listen == "0.0.0.0"


def test_info_button_is_a_tooltip(env, qtbot):
    from PySide6.QtWidgets import QToolButton, QToolTip

    app, *_ = env
    w = app.open_settings()
    w.tabs.setCurrentIndex(3)
    qtbot.waitExposed(w)
    w.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is w)
    btn = next(b for b in w.findChildren(QToolButton) if b.text() == "?" and b.isVisible())
    assert "sleep" in btn.toolTip()
    btn.click()
    qtbot.waitUntil(QToolTip.isVisible)
    assert "unloads the model" in QToolTip.text()
    assert not [x for x in QApplication.topLevelWidgets() if x.isVisible() and x.inherits("QMessageBox")]


def test_status_line_shows_running_context(env, qtbot):
    app, c, llm, swift = env
    app.tray.header_px = 1000  # wide enough that nothing scrolls
    app.tray.rebuild()
    c.start(ProfileKey(swift.lower(), "swift-orca"))
    lines = app.tray.header_lines
    want = (
        "Running (idle) Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS / swift-orca",
        "100k context, b11860 (df9beac71)",
    )
    qtbot.waitUntil(lambda: lines() == want, timeout=10000)


def test_long_header_uses_full_width_and_scrolls_at_constant_size(env, qtbot):
    app, c, llm, swift = env
    app.tray.header_px = 200
    c.start(ProfileKey(swift.lower(), "swift-orca"))
    qtbot.waitUntil(lambda: c.manager.status.busy is False, timeout=10000)
    full = "Running (idle) Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS / swift-orca"
    menu = app.tray.menu
    menu.popup(menu.pos())
    qtbot.waitExposed(menu)
    label = app.tray.header.labels[0]
    fm = label.fontMetrics()

    def head():
        return app.tray.header_lines()[0]

    width = label.contentsRect().width()
    assert width > 200  # the header takes the menu's width, not just its minimum
    assert fm.horizontalAdvance(full) > width, "test text must be wider than the header"
    assert full.startswith(head()) and width - fm.horizontalAdvance("W") < fm.horizontalAdvance(head()) <= width
    qtbot.waitUntil(lambda: full[1:].startswith(head()), timeout=2000)  # dropped one char
    sizes, texts = set(), set()
    for _ in range(8):
        qtbot.wait(260)
        sizes.add((menu.width(), menu.height()))
        texts.add(head())
        assert fm.horizontalAdvance(head()) <= width
    assert len(texts) > 1 and len(sizes) == 1, sizes  # text moves, menu doesn't resize
    menu.hide()


def test_menu_keeps_size_and_stays_on_screen_when_a_model_starts(env, qtbot):
    app, c, llm, swift = env
    menu = app.tray.menu
    area = QGuiApplication.primaryScreen().availableGeometry()
    menu.popup(area.bottomRight())  # opened at the corner, like a tray icon
    qtbot.waitExposed(menu)
    size = menu.size()
    assert area.contains(menu.geometry())
    assert app.tray.header_lines()[1] == f"port {c.settings.port}"  # second line already present when stopped
    c.start(ProfileKey(swift.lower(), "swift-orca"))
    qtbot.waitUntil(
        lambda: c.manager.status.state is State.RUNNING and "context" in app.tray.header_lines()[1], timeout=10000
    )
    assert menu.size() == size and area.contains(menu.geometry())
    menu.hide()


# U12
def test_started_toast_and_menu_icons(env, qtbot):
    app, *_ = env
    app.tray.rebuild()
    assert all(not a.icon().isNull() for a in app.tray.menu.actions() if a.text() and not a.isSeparator())
    app.show_started()
    toast = app._toast
    qtbot.waitUntil(toast.isVisible)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert area.contains(toast.frameGeometry())
    with qtbot.waitSignal(toast.destroyed, timeout=5000):
        qtbot.mouseClick(toast, Qt.LeftButton)
    qtbot.waitUntil(app.tray.menu.isVisible)
    app.tray.menu.hide()


# U13
def test_live_log_viewer(env, qtbot):
    from llama_launcher.ui import log_view

    app, c, _, _ = env
    log = c.manager.server_log
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("# header\n", encoding="utf-8")
    app.tray.rebuild()
    logs = next(a.menu() for a in app.tray.menu.actions() if a.text() == "Logs")
    assert [a.text() for a in logs.actions()] == [
        "View server log (live)",
        "Open server log",
        "Open previous server log",
        "Open logs folder",
    ]
    assert all(not a.icon().isNull() for a in logs.actions())
    logs.actions()[0].trigger()
    v = log_view._open[log]
    assert log_view.show_log(log) is v
    v.timer.setInterval(20)
    with open(log, "a", encoding="utf-8") as f:
        f.write("loading model\nserver is listening\n")
    qtbot.waitUntil(lambda: "server is listening" in v.text.toPlainText(), timeout=3000)
    v.close()
    qtbot.waitUntil(lambda: log not in log_view._open, timeout=3000)


def test_port_in_use_notifies_once_per_takeover(env, monkeypatch):
    app, *_ = env  # nothing launched from this app: servers started elsewhere still notify
    shown = []
    monkeypatch.setattr(app.tray, "notify", lambda title, text, error=False: shown.append(title))
    watch = "port 1 is in use by another program"
    app.tray.on_status(Status(State.EXTERNAL, message=watch))
    app.tray.on_status(Status(State.EXTERNAL, message=watch, external_ids=["m"]))  # other server finished loading
    assert shown == ["Port in use"]
    click = "port 1 is already in use by another program"
    app.tray.on_status(Status(State.EXTERNAL, message=click, external_ids=["m"]))
    app.tray.on_status(Status(State.EXTERNAL, message=click, external_ids=["m"]))
    assert shown == ["Port in use"] * 3
    app.tray.on_status(Status())
    app.tray.on_status(Status(State.EXTERNAL, message=watch))
    assert len(shown) == 4
