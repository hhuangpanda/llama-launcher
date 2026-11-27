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
from llama_launcher.args import drop_flag, flag_value, format_argv
from llama_launcher.config import CACHE_TYPES, IDLE_MODES, ModelEntry, Profile, Settings
from llama_launcher.launch import LaunchSpec, Level, global_ctx, stem
from llama_launcher.runtimes import HelpTable
from llama_launcher.server_flags import DFLASH_FAMILY, DRAFT_DFLASH, NGRAM_TYPES, SPEC_AUTO, SPEC_TYPE
from llama_launcher.ui.widgets import IDLE_HELP, InheritSpin, choice_combo, info_button, with_info

INHERIT = "(inherit)"
NONE = "(none)"
OFF = "off"
SPEC_HELP = (
    "Sets <b>--spec-type</b>. <b>Draft</b> offers only the type the model's draft file needs "
    "(or built-in MTP); <b>Auto</b> passes no --spec-type so llama-server reads it from the draft. "
    "To turn a draft off, set Draft file to (none). "
    "<b>N-gram</b> needs no draft. Choosing either replaces a --spec-type in this profile's args; "
    "Auto also drops one from global args."
)


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
        idx = max(
            0,
            next(
                (
                    i
                    for i in range(self.combo.count())
                    if self.combo.itemData(i) and paths.norm(self.combo.itemData(i)) == paths.norm(current)
                ),
                0,
            ),
        )
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
        self._settings: Settings | None = None
        self._entries: list[catalog.FileEntry] = []
        self._entry: catalog.FileEntry | None = None
        self._dtype: str | None = None
        self._loading = False

        model_box = QGroupBox("Model (shared by all its profiles)")
        mf = self._model_form = QFormLayout(model_box)
        self.file_label = QLabel()
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.mmproj = SidecarPicker("mmproj")
        self.draft = SidecarPicker("draft model")
        self.lora = SidecarPicker("LoRA adapter")
        self.mtp_hint = QLabel()
        mf.addRow("File", self.file_label)
        mf.addRow("mmproj", self.mmproj)

        prof_box = QGroupBox("Profile")
        pf = self._profile_form = QFormLayout(prof_box)
        self.name = QLineEdit()
        self.alias = QLineEdit()
        self.runtime = QComboBox()
        self.ctx = InheritSpin(minimum=256, maximum=4_194_304, step=1024, suffix=" tokens")
        self.ctk = choice_combo(CACHE_TYPES, blank=INHERIT)
        self.ctv = choice_combo(CACHE_TYPES, blank=INHERIT)
        self.idle_mode = choice_combo(IDLE_MODES, blank=INHERIT)
        self.idle_seconds = InheritSpin(minimum=1, maximum=7 * 1440, suffix=" min", scale=60)
        self.spec_draft = QComboBox()
        self.spec_ngram = choice_combo(NGRAM_TYPES, blank="Off")
        self.spec_hint = QLabel(wordWrap=True)
        spec = QWidget()
        sl = QHBoxLayout(spec)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Draft"))
        sl.addWidget(self.spec_draft, 1)
        sl.addWidget(QLabel("N-gram"))
        sl.addWidget(self.spec_ngram, 1)
        self.args = QPlainTextEdit()
        self.args.setPlaceholderText("Extra llama-server flags for this profile; they override global args.")
        self.args.setFixedHeight(80)
        self.unset = QLineEdit()
        self.unset.setPlaceholderText("Global flags to remove, e.g. --spec-type --spec-draft-n-max")
        pf.addRow("Name", self.name)
        pf.addRow("Alias (model id)", self.alias)
        pf.addRow("Runtime", self.runtime)
        pf.addRow("LoRA", self.lora)
        pf.addRow("Context size", self.ctx)
        pf.addRow("K cache", self.ctk)
        pf.addRow("V cache", self.ctv)
        pf.addRow("When idle", with_info(self.idle_mode, info_button(self, "When idle", IDLE_HELP)))
        pf.addRow("Idle after", self.idle_seconds)
        pf.addRow("Draft file", self.draft)
        pf.addRow("", self.mtp_hint)
        pf.addRow("Speculative", with_info(spec, info_button(self, "Speculative decoding", SPEC_HELP)))
        pf.addRow("", self.spec_hint)
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
        self.draft.changed.connect(self._draft_changed)
        self.spec_draft.currentIndexChanged.connect(lambda _: self._spec_edited())
        self.spec_ngram.currentIndexChanged.connect(lambda _: self._spec_edited())
        self.lora.changed.connect(lambda v: self._set("lora", v))
        self.name.textChanged.connect(lambda v: self._set("name", v.strip()))
        self.alias.textChanged.connect(lambda v: self._set("alias", v.strip()))
        self.runtime.currentIndexChanged.connect(lambda _: self._set("runtime", self.runtime.currentData() or ""))
        self.ctx.changed.connect(lambda v: self._set("ctx_size", v))
        self.ctk.currentIndexChanged.connect(lambda _: self._set("cache_type_k", self.ctk.currentData()))
        self.ctv.currentIndexChanged.connect(lambda _: self._set("cache_type_v", self.ctv.currentData()))
        self.idle_mode.currentIndexChanged.connect(lambda _: self._set("idle_mode", self.idle_mode.currentData()))
        self.idle_seconds.changed.connect(lambda v: self._set("idle_seconds", v))
        self.args.textChanged.connect(self._args_changed)
        self.unset.textChanged.connect(self._unset_changed)
        self.setEnabled(False)

    def load(self, model: ModelEntry, profile: Profile, settings: Settings, entries: list[catalog.FileEntry]) -> None:
        self._loading = True
        self.model, self.profile = model, profile
        self._settings, self._entries = settings, entries
        self.file_label.setText(model.file)
        entry = next(
            (e for e in entries if e.kind is catalog.Kind.MODEL and paths.norm(e.path) == paths.norm(model.file)), None
        )
        self._entry = entry
        sug = catalog.suggest(entry, entries) if entry and entry.info else None
        self.mmproj.set_options(model.mmproj, sug.mmproj_candidates if sug else [])
        self.draft.set_options(profile.draft, sug.draft_candidates if sug else [])
        self.lora.set_options(profile.lora, sug.lora_candidates if sug else [])
        self.mtp_hint.setText(
            "Built-in MTP head: draft-mtp works without a draft file." if sug and sug.builtin_mtp else ""
        )
        self._profile_form.setRowVisible(self.mtp_hint, bool(self.mtp_hint.text()))
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
            self.ctx.set_value(
                profile.ctx_size, model_ctx or 0, "model's trained context; --fit (on by default) lowers it to fit VRAM"
            )
        self._select(self.ctk, profile.cache_type_k)
        self._select(self.ctv, profile.cache_type_v)
        self._select(self.idle_mode, profile.idle_mode)
        self.idle_seconds.set_value(profile.idle_seconds, settings.global_.idle_seconds, "from Global tab")
        self.args.setPlainText(profile.args)
        self.unset.setText(" ".join(profile.unset))
        self._refresh_spec()
        self.setEnabled(True)
        self._loading = False

    # ---- speculative decoding ----
    def _info(self, path: str):
        key = paths.norm(path)
        return next((e.info for e in self._entries if e.info and paths.norm(e.path) == key), None)

    def _effective_spec(self) -> tuple[str, str]:
        """The --spec-type value this profile ends up with, and where it comes from."""
        p, ht = self.profile, HelpTable()
        in_args = flag_value(p.args, SPEC_TYPE, ht)
        if in_args is not None:
            return in_args, "From this profile's args" + (", which override this setting." if p.spec_type else ".")
        if p.spec_type:
            return ("" if p.spec_type == SPEC_AUTO else p.spec_type), ""
        if SPEC_TYPE not in p.unset and (g := flag_value(self._settings.global_.args, SPEC_TYPE, ht)) is not None:
            return g, "From global args."
        return "", ""

    def _refresh_spec(self) -> None:
        was, self._loading = self._loading, True
        mi = self._entry.info if self._entry else None
        di = self._info(self.profile.draft) if self.profile.draft else None
        self._dtype = catalog.draft_spec_type(mi, di) if mi else None
        auto_ok = catalog.spec_auto_detected(di)
        value, source = self._effective_spec()
        items = [t for t in value.split(",") if t and t != "none"]
        drafts = [t for t in items if t.startswith("draft-")]
        ngrams = [t for t in items if t in NGRAM_TYPES]
        d = drafts[0] if drafts else (SPEC_AUTO if auto_ok and not items else OFF)

        combo = self.spec_draft
        combo.clear()
        if auto_ok:
            combo.addItem(f"Auto (llama-server picks {self._dtype})", SPEC_AUTO)
        if not self.profile.draft:
            combo.addItem("Off", OFF)
        if self._dtype:
            combo.addItem(self._dtype, self._dtype)
        if combo.findData(d) < 0:
            fits = self._dtype == DRAFT_DFLASH and d in DFLASH_FAMILY
            combo.addItem(d if fits else f"{'Off' if d == OFF else d} (doesn't fit this draft)", d)
        combo.setCurrentIndex(combo.findData(d))
        combo.setEnabled(combo.count() > 1)
        self.spec_ngram.setCurrentIndex(max(0, self.spec_ngram.findData(ngrams[0] if ngrams else "")))

        custom = len(drafts) > 1 or len(ngrams) > 1 or len(items) > len(drafts) + len(ngrams)
        hint = [source] if source else []
        if custom:
            hint.append(f"Custom value '{value}'; choosing here replaces it.")
        self.spec_hint.setText(" ".join(hint))
        self._profile_form.setRowVisible(self.spec_hint, bool(hint))
        self._loading = was

    def _spec_edited(self) -> None:
        if self._loading or self.profile is None:
            return
        d, n = self.spec_draft.currentData(), self.spec_ngram.currentData()
        if d == SPEC_AUTO:
            d = self._dtype if n else ""
        elif d == OFF:
            d = ""
        self._apply_spec(",".join(t for t in (d, n) if t) or SPEC_AUTO)

    def _apply_spec(self, value: str) -> None:
        p = self.profile
        args = drop_flag(p.args, SPEC_TYPE, HelpTable())
        if value == p.spec_type and args == p.args:
            return
        p.spec_type, p.args = value, args
        p.invalid.pop("spec_type", None)
        self._loading = True
        if self.args.toPlainText().strip() != args:
            self.args.setPlainText(args)
        self._loading = False
        self._refresh_spec()
        self.edited.emit()

    def _args_changed(self) -> None:
        if self._loading or self.profile is None:
            return
        self._set("args", self.args.toPlainText().strip())
        self._refresh_spec()

    def _unset_changed(self, text: str) -> None:
        if self._loading or self.profile is None:
            return
        self._set("unset", text.split())
        self._refresh_spec()

    def _draft_changed(self, path: str) -> None:
        if self._loading or self.profile is None:
            return
        old = self._dtype
        self._set("draft", path)
        self._refresh_spec()
        # A draft type this profile set for the previous draft follows the new one.
        p = self.profile
        if p.spec_type and old and old in p.spec_type.split(",") and old != self._dtype:
            rest = [t for t in p.spec_type.split(",") if t != old]
            auto_ok = catalog.spec_auto_detected(self._info(path) if path else None)
            d = "" if auto_ok and not rest else (self._dtype or "")
            self._apply_spec(",".join(t for t in (d, *rest) if t) or SPEC_AUTO)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        i = combo.findData(value)
        if i < 0:
            combo.addItem(value, value)
            i = combo.count() - 1
        combo.setCurrentIndex(i)

    def _set(self, field: str, value) -> None:
        if not self._loading and self.profile is not None:
            self._assign(self.profile, field, value)

    def _set_model(self, field: str, value: str) -> None:
        if not self._loading and self.model is not None:
            self._assign(self.model, field, value)

    def _assign(self, obj, field: str, value) -> None:
        """Setting a field here replaces a value that was invalid in the file."""
        fixed = obj.invalid.pop(field, None) is not None
        if getattr(obj, field) != value or fixed:
            setattr(obj, field, value)
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
