"""The single Settings window: general, runtimes, model folders, global options, profiles."""

from __future__ import annotations

import copy
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from llama_launcher import catalog, paths
from llama_launcher.config import CACHE_TYPES, IDLE_MODES, ModelEntry, Profile, RuntimeEntry
from llama_launcher.controller import Controller
from llama_launcher.launch import ProfileKey, listen_hosts, model_rows, stem
from llama_launcher.runtimes import Runtime
from llama_launcher.ui.icons import state_icon
from llama_launcher.ui.profile_editor import ProfileEditor
from llama_launcher.server import State
from llama_launcher.ui.tray import open_path
from llama_launcher.ui.widgets import IDLE_HELP, info_button, tailscale_ipv4, with_info

ROLE = Qt.UserRole


class SettingsWindow(QWidget):
    def __init__(self, controller: Controller):
        super().__init__(None, Qt.Window)
        self.c = controller
        self.setWindowTitle("llama-launcher settings")
        self.setWindowIcon(state_icon(State.RUNNING))
        self.resize(1100, 760)
        self.dirty = False
        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._runtimes_tab(), "Runtimes")
        self.tabs.addTab(self._folders_tab(), "Model folders")
        self.tabs.addTab(self._global_tab(), "Global")
        self.tabs.addTab(self._profiles_tab(), "Profiles")
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.revert)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        self.status = QLabel()
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        for b in (self.revert_btn, self.save_btn, self.close_btn):
            bottom.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addLayout(bottom)
        self.c.config_changed.connect(self._external_change)
        self.revert()

    # ---- state ----
    def revert(self) -> None:
        self.settings = copy.deepcopy(self.c.settings)
        self.models = copy.deepcopy(self.c.config.models)
        self._loading = True
        self._load_general()
        self._load_runtimes()
        self._load_folders()
        self._load_global()
        self._load_tree()
        self._loading = False
        self._set_dirty(False)

    def _external_change(self) -> None:
        if not self.dirty:
            self.revert()

    def _set_dirty(self, dirty: bool = True) -> None:
        if getattr(self, "_loading", False):
            return
        self.dirty = dirty
        self.setWindowTitle("llama-launcher settings" + (" *" if dirty else ""))
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)

    def save(self) -> None:
        problems = self._profile_name_problems()
        if problems:
            QMessageBox.warning(self, "Can't save", "\n".join(problems))
            return
        self.c.save(copy.deepcopy(self.settings), copy.deepcopy(self.models))
        self._set_dirty(False)
        self.status.setText("Saved." + ("  The running model has changes: use Restart to apply in the tray."
                                        if self.c.is_stale() else ""))

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.dirty:
            r = QMessageBox.question(self, "Unsaved changes", "Save changes before closing?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                e.ignore()
                return
            if r == QMessageBox.Save:
                self.save()
            else:
                self.revert()
        e.accept()

    def bring_to_front(self) -> None:
        if not self.dirty:
            self.revert()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---- General ----
    def _general_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.port = QSpinBox(minimum=1, maximum=65535)
        self.listen = QComboBox(editable=True)
        self.listen_hint = QLabel()
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText("empty = no key")
        self.startup = QCheckBox("Launch at Windows sign-in")
        f.addRow("Port", self.port)
        f.addRow("Listen address", self.listen)
        f.addRow("", self.listen_hint)
        f.addRow("API key", self.api_key)
        f.addRow("", self.startup)
        row = QHBoxLayout()
        b1 = QPushButton("Open config folder")
        b1.clicked.connect(lambda: open_path(paths.home()))
        b2 = QPushButton("Open logs folder")
        b2.clicked.connect(lambda: open_path(paths.logs_dir()))
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch()
        f.addRow("", row)
        self.port.valueChanged.connect(lambda v: self._gset("port", v))
        self.listen.currentTextChanged.connect(self._listen_changed)
        self.api_key.textChanged.connect(lambda v: self._gset("api_key", v.strip()))
        self.startup.toggled.connect(lambda v: self._gset("launch_at_startup", v))
        return w

    def _gset(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.settings, attr, value)
        self._set_dirty()
        self._refresh_preview()

    def _listen_changed(self, text: str) -> None:
        value = text.replace(" ", "")
        ts = set(tailscale_ipv4())
        hosts = set(listen_hosts(value))
        if "0.0.0.0" in hosts:
            hint = "All networks: this PC, LAN" + (" and Tailscale" if ts else "")
        elif hosts == {"127.0.0.1"}:
            hint = "This PC only"
        else:
            names = ["this PC" if h == "127.0.0.1" else f"Tailscale ({h})" if h in ts else h for h in sorted(hosts)]
            hint = "Only: " + ", ".join(names)
        if not ts:
            hint += "   (Tailscale not connected: its address isn't offered)"
        self.listen_hint.setText(hint)
        self._gset("listen", value)

    def _load_general(self) -> None:
        s = self.settings
        self.port.setValue(s.port)
        self.listen.blockSignals(True)
        self.listen.clear()
        self.listen.addItems(["127.0.0.1", "0.0.0.0"])
        for ip in tailscale_ipv4():  # re-read on every open: Tailscale may have connected since
            self.listen.addItems([ip, f"127.0.0.1,{ip}"])
        self.listen.blockSignals(False)
        self.listen.setCurrentText(s.listen)
        self._listen_changed(self.listen.currentText())
        self.api_key.setText(s.api_key)
        self.startup.setChecked(s.launch_at_startup)

    # ---- Runtimes ----
    def _runtimes_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Folders that contain llama-server.exe and its DLLs (a release zip or your own build)."))
        self.rt_table = QTableWidget(0, 3)
        self.rt_table.setHorizontalHeaderLabels(["Name", "Folder", "Version"])
        self.rt_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.rt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rt_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.rt_table)
        row = QHBoxLayout()
        add = QPushButton("Add folder...")
        add.clicked.connect(self._add_runtime)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_runtime)
        self.default_rt = QComboBox()
        self.default_rt.currentIndexChanged.connect(lambda _: self._gset("default_runtime", self.default_rt.currentData() or ""))
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        row.addWidget(QLabel("Default runtime"))
        row.addWidget(self.default_rt)
        lay.addLayout(row)
        return w

    def _load_runtimes(self) -> None:
        was = self._loading
        self._loading = True
        self.rt_table.setRowCount(0)
        for r in self.settings.runtimes:
            i = self.rt_table.rowCount()
            self.rt_table.insertRow(i)
            rt = self.c.runtime_factory(r)
            try:
                version = self.c.probe.info(rt).version.label()
            except RuntimeError as e:
                version = f"problem: {e}"
            for col, text in enumerate((r.name, r.path, version)):
                self.rt_table.setItem(i, col, QTableWidgetItem(text))
        self.default_rt.clear()
        self.default_rt.addItem("(first in list)", "")
        for r in self.settings.runtimes:
            self.default_rt.addItem(r.name, r.name)
        i = self.default_rt.findData(self.settings.default_runtime)
        self.default_rt.setCurrentIndex(max(i, 0))
        self._loading = was

    def _add_runtime(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder with llama-server.exe")
        if not folder:
            return
        folder = os.path.normpath(folder)
        problem = Runtime("new", folder).check()
        if problem:
            QMessageBox.warning(self, "Not a llama.cpp folder", problem)
            return
        name, ok = QInputDialog.getText(self, "Runtime name", "Name for this llama.cpp build:",
                                        text=os.path.basename(folder.rstrip("\\")) or "llama")
        if not ok or not name.strip():
            return
        if self.settings.runtime(name.strip()):
            QMessageBox.warning(self, "Name taken", f"A runtime called '{name}' already exists.")
            return
        self.settings.runtimes.append(RuntimeEntry(name.strip(), folder))
        self._load_runtimes()
        self._set_dirty()
        self._refresh_preview()

    def _remove_runtime(self) -> None:
        row = self.rt_table.currentRow()
        if row < 0:
            return
        removed = self.settings.runtimes.pop(row)
        if self.settings.default_runtime == removed.name:
            self.settings.default_runtime = ""
        self._load_runtimes()
        self._set_dirty()
        self._refresh_preview()

    # ---- Model folders ----
    def _folders_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Folders scanned (recursively) for .gguf files. Nothing is copied or moved."))
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(110)
        lay.addWidget(self.folder_list)
        row = QHBoxLayout()
        add = QPushButton("Add folder...")
        add.clicked.connect(self._add_folder)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_folder)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self._rescan)
        for b in (add, rm, rescan):
            row.addWidget(b)
        self.scan_status = QLabel()
        self.scan_status.setObjectName("scan_status")
        row.addWidget(self.scan_status, 1)
        lay.addLayout(row)
        self.file_table = QTableWidget(0, 5)
        self.file_table.setHorizontalHeaderLabels(["File", "Kind", "Arch", "Size match", "Folder"])
        self.file_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.file_table, 1)
        return w

    def _load_folders(self) -> None:
        self.folder_list.clear()
        self.folder_list.addItems(self.settings.model_folders)
        self._load_files()

    def _rescan(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._load_files()
            if self.settings.model_folders == self.c.settings.model_folders:
                self.c.rescan()  # the tray picks up new files without a save
        finally:
            QApplication.restoreOverrideCursor()

    def _load_files(self) -> None:
        self.entries = catalog.scan(self.settings.model_folders, self.c.info_cache)
        counts = {k: sum(e.kind is k for e in self.entries) for k in catalog.Kind}
        parts = [f"{counts[catalog.Kind.MODEL]} models", f"{counts[catalog.Kind.MMPROJ]} mmproj",
                 f"{counts[catalog.Kind.DRAFT]} draft"]
        if counts[catalog.Kind.UNREADABLE]:
            parts.append(f"{counts[catalog.Kind.UNREADABLE]} unreadable")
        self.scan_status.setText(f"Scanned {datetime.now():%H:%M:%S}: " + ", ".join(parts))
        self.file_table.setRowCount(0)
        for e in self.entries:
            i = self.file_table.rowCount()
            self.file_table.insertRow(i)
            info = e.info
            match = ""
            if info and e.kind is catalog.Kind.MMPROJ:
                match = "projection " + ", ".join(map(str, info.projection_dims))
            elif info:
                match = f"embedding {info.embedding_length}" + (" + MTP" if info.nextn_layers else "")
            vals = (os.path.basename(e.path), e.kind.value + (f" ({e.error})" if e.error else ""),
                    info.arch if info else "", match, os.path.dirname(e.path))
            for col, text in enumerate(vals):
                self.file_table.setItem(i, col, QTableWidgetItem(text))
        self.file_table.resizeColumnsToContents()
        if hasattr(self, "tree"):
            self._load_tree()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Model folder")
        if folder:
            folder = os.path.normpath(folder)
            if catalog.norm(folder) not in {catalog.norm(f) for f in self.settings.model_folders}:
                self.settings.model_folders.append(folder)
                self._load_folders()
                self._set_dirty()

    def _remove_folder(self) -> None:
        row = self.folder_list.currentRow()
        if row >= 0:
            self.settings.model_folders.pop(row)
            self._load_folders()
            self._set_dirty()

    # ---- Global ----
    def _global_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.g_ctk = QComboBox()
        self.g_ctv = QComboBox()
        for c in (self.g_ctk, self.g_ctv):
            c.addItem("(llama-server default)", "")
            for t in CACHE_TYPES:
                c.addItem(t, t)
        self.g_idle = QComboBox()
        for m in IDLE_MODES:
            self.g_idle.addItem(m, m)
        self.g_idle_s = QSpinBox(minimum=1, maximum=7 * 1440, suffix=" min")
        self.g_args = QPlainTextEdit()
        self.g_args.setPlaceholderText("Flags applied to every profile, e.g. --flash-attn on --fit on -t 12")
        f.addRow("K cache", self.g_ctk)
        f.addRow("V cache", self.g_ctv)
        f.addRow("When idle", with_info(self.g_idle, info_button(self, "When idle", IDLE_HELP)))
        f.addRow("Idle after", self.g_idle_s)
        f.addRow("Global args", self.g_args)
        self.g_ctk.currentIndexChanged.connect(lambda _: self._globset("cache_type_k", self.g_ctk.currentData()))
        self.g_ctv.currentIndexChanged.connect(lambda _: self._globset("cache_type_v", self.g_ctv.currentData()))
        self.g_idle.currentIndexChanged.connect(lambda _: self._globset("idle_mode", self.g_idle.currentData()))
        self.g_idle_s.valueChanged.connect(lambda v: self._globset("idle_seconds", v * 60))
        self.g_args.textChanged.connect(lambda: self._globset("args", self.g_args.toPlainText().strip()))
        return w

    def _globset(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.settings.glob, attr, value)
        self._set_dirty()
        self._refresh_preview()

    def _load_global(self) -> None:
        g = self.settings.glob
        self.g_ctk.setCurrentIndex(max(0, self.g_ctk.findData(g.cache_type_k)))
        self.g_ctv.setCurrentIndex(max(0, self.g_ctv.findData(g.cache_type_v)))
        self.g_idle.setCurrentIndex(max(0, self.g_idle.findData(g.idle_mode)))
        self.g_idle_s.setValue(max(1, round(g.idle_seconds / 60)))
        self.g_args.setPlainText(g.args)

    # ---- Profiles ----
    def _profiles_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._tree_selected)
        ll.addWidget(self.tree)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Add profile")
        self.add_btn.clicked.connect(lambda: self._new_profile(copy_current=False))
        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(lambda: self._new_profile(copy_current=True))
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self._delete_profile)
        self.start_btn = QPushButton("Start")
        self.start_btn.setToolTip("Save, then start this profile")
        self.start_btn.clicked.connect(self._start_profile)
        for b in (self.add_btn, self.dup_btn, self.del_btn, self.start_btn):
            row.addWidget(b)
        ll.addLayout(row)
        self.editor = ProfileEditor()
        self.editor.edited.connect(self._profile_edited)
        split.addWidget(left)
        split.addWidget(self.editor)
        split.setSizes([330, 770])
        QVBoxLayout(w).addWidget(split)
        return w

    def _rows(self):
        return model_rows(self.models, getattr(self, "entries", self.c.entries))

    def _load_tree(self, select: tuple[str, str] | None = None) -> None:
        if select is None:
            cur = self.tree.currentItem()
            select = cur.data(0, ROLE) if cur else None
        self.tree.blockSignals(True)
        self.tree.clear()
        self.row_models = {}
        target = None
        for row in self._rows():
            key = catalog.norm(row.model.file)
            self.row_models[key] = row
            label = stem(row.model.file) + ("" if row.exists else "  (file missing)")
            top = QTreeWidgetItem([label])
            top.setData(0, ROLE, (key, None))
            self.tree.addTopLevelItem(top)
            for p in row.model.profiles:
                child = QTreeWidgetItem([p.name + ("  (not saved yet)" if row.implicit else "")])
                child.setData(0, ROLE, (key, p.name))
                top.addChild(child)
                if select and select == (key, p.name):
                    target = child
            if select and select == (key, None):
                target = top
            top.setExpanded(len(row.model.profiles) > 1)
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
        self._tree_selected(self.tree.currentItem())

    def _selection(self):
        item = self.tree.currentItem()
        if not item:
            return None, None, None
        key, pname = item.data(0, ROLE)
        row = self.row_models.get(key)
        if row is None:
            return None, None, None
        if pname is None:
            pname = row.model.profiles[0].name if row.model.profiles else None
        prof = row.model.profile(pname) if pname else None
        return row, row.model, prof

    def _tree_selected(self, *_):
        row, model, prof = self._selection()
        has = prof is not None
        for b in (self.dup_btn, self.del_btn, self.start_btn):
            b.setEnabled(has)
        self.add_btn.setEnabled(model is not None)
        if not has:
            self.editor.setEnabled(False)
            self.editor.show_spec(None)
            return
        self.editor.load(model, prof, self.settings, getattr(self, "entries", self.c.entries))
        self._refresh_preview()

    def _materialize(self, model: ModelEntry) -> None:
        """An implicit (scanned, unsaved) model becomes a real profiles.toml entry on first edit."""
        if not any(m is model for m in self.models):
            self.models.append(model)

    def _profile_edited(self) -> None:
        self._materialize(self.editor.model)
        self._set_dirty()
        item = self.tree.currentItem()
        if item and item.data(0, ROLE)[1] is not None and item.text(0) != self.editor.profile.name:
            item.setText(0, self.editor.profile.name)
            item.setData(0, ROLE, (item.data(0, ROLE)[0], self.editor.profile.name))
        self._refresh_preview()

    def _profile_name_problems(self) -> list[str]:
        out = []
        for m in self.models:
            names = [p.name for p in m.profiles]
            if any(not n for n in names) or len(set(names)) != len(names):
                out.append(f"{stem(m.file)}: profile names must be unique and not empty")
        return out

    def _refresh_preview(self) -> None:
        if not hasattr(self, "editor") or self.editor.profile is None or self._loading:
            return
        spec = self.c.build(self.editor.model, self.editor.profile, settings=self.settings)
        self.editor.show_spec(spec, self._profile_name_problems())

    def _new_profile(self, copy_current: bool) -> None:
        row, model, prof = self._selection()
        if model is None:
            return
        names = {p.name for p in model.profiles}
        base = (prof.name if copy_current and prof else "profile")
        name, n = base + ("-copy" if copy_current else ""), 2
        while name in names:
            name, n = f"{base}-{n}", n + 1
        new = copy.deepcopy(prof) if copy_current and prof else Profile()
        new.name = name
        model.profiles.append(new)
        self._materialize(model)
        self._set_dirty()
        self._load_tree(select=(catalog.norm(model.file), name))

    def _delete_profile(self) -> None:
        row, model, prof = self._selection()
        if prof is None:
            return
        model.profiles.remove(prof)
        if not model.profiles:
            self.models = [m for m in self.models if m is not model]
        self._set_dirty()
        self._load_tree(select=(catalog.norm(model.file), None))

    def _start_profile(self) -> None:
        row, model, prof = self._selection()
        if prof is None:
            return
        if self.dirty:
            self.save()
            if self.dirty:
                return
        self.c.start(ProfileKey.of(model, prof))
        self.status.setText(f"Starting {stem(model.file)} / {prof.name}; see the tray icon for progress.")
