"""Editor for one model's shared fields and one of its profiles, with a live command preview."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llama_launcher import catalog, paths
from llama_launcher.args import format_argv
from llama_launcher.config import CACHE_TYPES, IDLE_MODES, ModelEntry, Profile, Settings
from llama_launcher.launch import LaunchSpec, Level, global_ctx, stem
from llama_launcher.ui.widgets import IDLE_HELP, InheritSpin, choice_combo, info_button, with_info

INHERIT = "(inherit)"
NONE = "(none)"


class SidecarPicker(QWidget):
    changed = Signal(str)

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.combo = QComboBox()
        self.combo.setEditable(False)
        self.combo.setMinimumContentsLength(40)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1)
        lay.addWidget(browse)
        self.combo.currentIndexChanged.connect(lambda _: self.changed.emit(self.value()))

    def set_options(self, current: str, compatible: list[str]) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(NONE, "")
        for p in compatible:
            self.combo.addItem(f"{os.path.basename(p)}   ({os.path.dirname(p)})", p)
        if current and paths.norm(current) not in {paths.norm(p) for p in compatible}:
            self.combo.addItem(f"{os.path.basename(current)}   (not a match for this model)", current)
        idx = max(0, next((i for i in range(self.combo.count())
                           if self.combo.itemData(i) and paths.norm(self.combo.itemData(i)) == paths.norm(current)), 0))
        self.combo.setCurrentIndex(idx if current else 0)
        self.combo.blockSignals(False)

    def value(self) -> str:
        return self.combo.currentData() or ""

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Pick {self.kind}", "", "GGUF (*.gguf)")
        if path:
            path = os.path.normpath(path)
            self.combo.addItem(os.path.basename(path), path)
            self.combo.setCurrentIndex(self.combo.count() - 1)


class ProfileEditor(QWidget):
    edited = Signal()  # any field changed; the owner refreshes the preview

    def __init__(self):
        super().__init__()
        self.model: ModelEntry | None = None
        self.profile: Profile | None = None
        self._loading = False

        model_box = QGroupBox("Model (shared by all its profiles)")
        mf = QFormLayout(model_box)
        self.file_label = QLabel()
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.mmproj = SidecarPicker("mmproj")
        self.draft = SidecarPicker("draft model")
        self.mtp_hint = QLabel()
        mf.addRow("File", self.file_label)
        mf.addRow("mmproj", self.mmproj)
        mf.addRow("Draft", self.draft)
        mf.addRow("", self.mtp_hint)

        prof_box = QGroupBox("Profile")
        pf = QFormLayout(prof_box)
        self.name = QLineEdit()
        self.alias = QLineEdit()
        self.runtime = QComboBox()
        self.ctx = InheritSpin(minimum=256, maximum=4_194_304, step=1024, suffix=" tokens")
        self.ctk = choice_combo(CACHE_TYPES, blank=INHERIT)
        self.ctv = choice_combo(CACHE_TYPES, blank=INHERIT)
        self.idle_mode = choice_combo(IDLE_MODES, blank=INHERIT)
        self.idle_seconds = InheritSpin(minimum=1, maximum=7 * 1440, suffix=" min", scale=60)
        self.args = QPlainTextEdit()
        self.args.setPlaceholderText("Extra llama-server flags for this profile; they override global args.")
        self.args.setFixedHeight(80)
        self.unset = QLineEdit()
        self.unset.setPlaceholderText("Global flags to remove, e.g. --spec-type --spec-draft-n-max")
        pf.addRow("Name", self.name)
        pf.addRow("Alias (model id)", self.alias)
        pf.addRow("Runtime", self.runtime)
        pf.addRow("Context size", self.ctx)
        pf.addRow("K cache", self.ctk)
        pf.addRow("V cache", self.ctv)
        pf.addRow("When idle", with_info(self.idle_mode, info_button(self, "When idle", IDLE_HELP)))
        pf.addRow("Idle after", self.idle_seconds)
        pf.addRow("Args", self.args)
        pf.addRow("Unset", self.unset)

        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setFont(QFont("Consolas", 9))
        self.preview.setObjectName("preview")
        self.problems = QLabel(wordWrap=True)
        self.problems.setObjectName("problems")

        lay = QVBoxLayout(self)
        lay.addWidget(model_box)
        lay.addWidget(prof_box)
        lay.addWidget(QLabel("Command line"))
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.problems)

        self.mmproj.changed.connect(lambda v: self._set_model("mmproj", v))
        self.draft.changed.connect(lambda v: self._set_model("draft", v))
        self.name.textChanged.connect(lambda v: self._set("name", v.strip()))
        self.alias.textChanged.connect(lambda v: self._set("alias", v.strip()))
        self.runtime.currentIndexChanged.connect(lambda _: self._set("runtime", self.runtime.currentData() or ""))
        self.ctx.changed.connect(lambda v: self._set("ctx_size", v))
        self.ctk.currentIndexChanged.connect(lambda _: self._set("cache_type_k", self.ctk.currentData()))
        self.ctv.currentIndexChanged.connect(lambda _: self._set("cache_type_v", self.ctv.currentData()))
        self.idle_mode.currentIndexChanged.connect(lambda _: self._set("idle_mode", self.idle_mode.currentData()))
        self.idle_seconds.changed.connect(lambda v: self._set("idle_seconds", v))
        self.args.textChanged.connect(lambda: self._set("args", self.args.toPlainText().strip()))
        self.unset.textChanged.connect(lambda v: self._set("unset", v.split()))
        self.setEnabled(False)

    def load(self, model: ModelEntry, profile: Profile, settings: Settings, entries: list[catalog.FileEntry]) -> None:
        self._loading = True
        self.model, self.profile = model, profile
        self.file_label.setText(model.file)
        entry = next((e for e in entries if e.kind is catalog.Kind.MODEL and paths.norm(e.path) == paths.norm(model.file)), None)
        sug = catalog.suggest(entry, entries) if entry and entry.info else None
        self.mmproj.set_options(model.mmproj, sug.mmproj_candidates if sug else [])
        self.draft.set_options(model.draft, sug.draft_candidates if sug else [])
        self.mtp_hint.setText("Built-in MTP head: use --spec-type draft-mtp without a draft file."
                              if sug and sug.builtin_mtp else "")
        self.name.setText(profile.name)
        self.alias.setText(profile.alias)
        self.alias.setPlaceholderText(stem(model.file))
        self.runtime.clear()
        default_rt = settings.default_runtime or (settings.runtimes[0].name if settings.runtimes else "none")
        self.runtime.addItem(f"(default: {default_rt})", "")
        for r in settings.runtimes:
            self.runtime.addItem(r.name, r.name)
        self._select(self.runtime, profile.runtime)
        glob_ctx = global_ctx(settings.global_.args)
        model_ctx = entry.info.context_length if entry and entry.info else 0
        if glob_ctx:
            self.ctx.set_value(profile.ctx_size, glob_ctx, "from global args")
        else:
            self.ctx.set_value(profile.ctx_size, model_ctx or 0,
                               "model's trained context; --fit (on by default) lowers it to fit VRAM")
        self._select(self.ctk, profile.cache_type_k)
        self._select(self.ctv, profile.cache_type_v)
        self._select(self.idle_mode, profile.idle_mode)
        self.idle_seconds.set_value(profile.idle_seconds, settings.global_.idle_seconds, "from Global tab")
        self.args.setPlainText(profile.args)
        self.unset.setText(" ".join(profile.unset))
        self.setEnabled(True)
        self._loading = False

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        i = combo.findData(value)
        if i < 0:
            combo.addItem(value, value)
            i = combo.count() - 1
        combo.setCurrentIndex(i)

    def _set(self, field: str, value) -> None:
        if self._loading or self.profile is None:
            return
        if getattr(self.profile, field) != value:
            setattr(self.profile, field, value)
            self.edited.emit()

    def _set_model(self, field: str, value: str) -> None:
        if self._loading or self.model is None:
            return
        if getattr(self.model, field) != value:
            setattr(self.model, field, value)
            self.edited.emit()

    def show_spec(self, spec: LaunchSpec | None, extra: list[str] = ()) -> None:
        if spec is None:
            self.preview.setPlainText("")
            self.problems.setText("")
            return
        self.preview.setPlainText(format_argv(spec.argv))
        lines = [f"⚠ {m}" for m in extra]
        lines += [("✖ " if d.level == Level.ERROR else "⚠ ") + d.message for d in spec.diagnostics]
        self.problems.setText("\n".join(lines) if lines else "✔ Ready to launch")
        bad = any(d.level == Level.ERROR for d in spec.diagnostics) or extra
        self.problems.setStyleSheet("color: #d23c3c;" if bad else "")
