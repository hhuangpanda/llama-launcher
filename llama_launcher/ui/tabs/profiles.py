"""Profiles tab: the model/profile tree and the profile editor."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from llama_launcher import paths
from llama_launcher.config import ModelEntry, Profile
from llama_launcher.launch import ProfileKey, model_rows, stem
from llama_launcher.ui.profile_editor import ProfileEditor
from llama_launcher.ui.tabs.base import SettingsTab

ROLE = Qt.UserRole


class ProfilesTab(SettingsTab):
    title = "Profiles"

    def __init__(self, owner):
        super().__init__(owner)
        self.row_models = {}
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._selected)
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
        self.editor.edited.connect(self._edited)
        split.addWidget(left)
        split.addWidget(self.editor)
        split.setSizes([330, 770])
        QVBoxLayout(self).addWidget(split)

    def load(self, select: tuple[str, str | None] | None = None) -> None:
        if select is None:
            cur = self.tree.currentItem()
            select = cur.data(0, ROLE) if cur else None
        self.tree.blockSignals(True)
        self.tree.clear()
        self.row_models = {}
        target = None
        for row in model_rows(self.owner.models, self.owner.entries):
            key = paths.norm(row.model.file)
            self.row_models[key] = row
            label = stem(row.model.file) + ("" if row.exists else "  (file missing)")
            top = QTreeWidgetItem([label])
            top.setData(0, ROLE, (key, None))
            self.tree.addTopLevelItem(top)
            for p in row.model.profiles:
                child = QTreeWidgetItem([p.name + ("  (not saved yet)" if row.implicit else "")])
                child.setData(0, ROLE, (key, p.name))
                top.addChild(child)
                if select == (key, p.name):
                    target = child
            if select == (key, None):
                target = top
            top.setExpanded(len(row.model.profiles) > 1)
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
        self._selected()

    def name_problems(self) -> list[str]:
        out = []
        for m in self.owner.models:
            names = [p.name for p in m.profiles]
            if any(not n for n in names) or len(set(names)) != len(names):
                out.append(f"{stem(m.file)}: profile names must be unique and not empty")
        return out

    def refresh_preview(self) -> None:
        if self.editor.profile is None or self.owner.loading:
            return
        spec = self.c.build(self.editor.model, self.editor.profile, settings=self.owner.settings)
        self.editor.show_spec(spec, self.name_problems())

    def _selection(self) -> tuple[ModelEntry | None, Profile | None]:
        item = self.tree.currentItem()
        if not item:
            return None, None
        key, pname = item.data(0, ROLE)
        row = self.row_models.get(key)
        if row is None:
            return None, None
        if pname is None:
            pname = row.model.profiles[0].name if row.model.profiles else None
        return row.model, (row.model.profile(pname) if pname else None)

    def _selected(self, *_) -> None:
        model, prof = self._selection()
        has = prof is not None
        for b in (self.dup_btn, self.del_btn, self.start_btn):
            b.setEnabled(has)
        self.add_btn.setEnabled(model is not None)
        if not has:
            self.editor.setEnabled(False)
            self.editor.show_spec(None)
            return
        self.editor.load(model, prof, self.owner.settings, self.owner.entries)
        self.refresh_preview()

    def _materialize(self, model: ModelEntry) -> None:
        """An implicit (scanned, unsaved) model becomes a real profiles.toml entry on first edit."""
        if not any(m is model for m in self.owner.models):
            self.owner.models.append(model)

    def _edited(self) -> None:
        self._materialize(self.editor.model)
        item = self.tree.currentItem()
        if item and item.data(0, ROLE)[1] is not None and item.text(0) != self.editor.profile.name:
            item.setText(0, self.editor.profile.name)
            item.setData(0, ROLE, (item.data(0, ROLE)[0], self.editor.profile.name))
        self.owner.changed()

    def _new_profile(self, copy_current: bool) -> None:
        model, prof = self._selection()
        if model is None:
            return
        names = {p.name for p in model.profiles}
        base = prof.name if copy_current and prof else "profile"
        name, n = base + ("-copy" if copy_current else ""), 2
        while name in names:
            name, n = f"{base}-{n}", n + 1
        new = copy.deepcopy(prof) if copy_current and prof else Profile()
        new.name = name
        model.profiles.append(new)
        self._materialize(model)
        self.owner.changed()
        self.load(select=(paths.norm(model.file), name))

    def _delete_profile(self) -> None:
        model, prof = self._selection()
        if prof is None:
            return
        model.profiles.remove(prof)
        if not model.profiles:
            self.owner.models = [m for m in self.owner.models if m is not model]
        self.owner.changed()
        self.load(select=(paths.norm(model.file), None))

    def _start_profile(self) -> None:
        model, prof = self._selection()
        if prof is None:
            return
        if self.owner.dirty:
            self.owner.save()
            if self.owner.dirty:
                return
        self.c.start(ProfileKey.of(model, prof))
        self.owner.status.setText(f"Starting {stem(model.file)} / {prof.name}; see the tray icon for progress.")
