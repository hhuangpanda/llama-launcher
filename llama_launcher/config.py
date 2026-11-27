"""settings.toml and profiles.toml: typed views plus comment-preserving saves."""

from __future__ import annotations

import re
from dataclasses import MISSING, Field, dataclass, field, fields
from enum import StrEnum
from pathlib import Path

import tomlkit
from tomlkit.items import AoT

from llama_launcher.paths import norm, read_text, replace_file

DEFAULT_PROFILE = "default_profile"


class IdleMode(StrEnum):
    SLEEP = "sleep"
    STOP = "stop"
    OFF = "off"


IDLE_MODES = tuple(m.value for m in IdleMode)
CACHE_TYPES = ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1")


class ConfigError(ValueError):
    pass


def section(toml_key: str, factory):
    """A field stored as a nested TOML table or array of tables, parsed and saved by its owner."""
    return field(default_factory=factory, metadata={"toml": toml_key})


def invalid_values():
    """Key -> problem for values in the file that couldn't be used; in memory only, never saved.
    The entry can't launch while one is left, and saving keeps the value in the file."""
    return field(default_factory=dict, compare=False, repr=False, metadata={"memory": True})


def _scalars(cls) -> dict[str, Field]:
    return {f.name: f for f in fields(cls) if "toml" not in f.metadata and "memory" not in f.metadata}


def _sections(cls) -> set[str]:
    return {f.metadata["toml"] for f in fields(cls) if "toml" in f.metadata}


@dataclass
class RuntimeEntry:
    name: str
    path: str


@dataclass
class GlobalOptions:
    cache_type_k: str = ""
    cache_type_v: str = ""
    idle_mode: str = IdleMode.SLEEP.value
    idle_seconds: int = 3600
    args: str = ""


@dataclass
class Settings:
    port: int = 9931
    listen: str = "127.0.0.1"
    api_key: str = ""
    launch_at_startup: bool = False
    default_runtime: str = ""
    model_folders: list[str] = field(default_factory=list)
    global_: GlobalOptions = section("global", GlobalOptions)
    runtimes: list[RuntimeEntry] = section("runtimes", list)

    def runtime(self, name: str) -> RuntimeEntry | None:
        return next((r for r in self.runtimes if r.name == name), None)


@dataclass
class Profile:
    name: str = DEFAULT_PROFILE
    alias: str = ""
    runtime: str = ""
    ctx_size: int = 0
    cache_type_k: str = ""
    cache_type_v: str = ""
    idle_mode: str = ""
    idle_seconds: int = 0
    draft: str = ""
    lora: str = ""
    spec_type: str = ""  # "" inherits from args, "auto" passes none, else the --spec-type value
    args: str = ""
    unset: list[str] = field(default_factory=list)
    invalid: dict[str, str] = invalid_values()


@dataclass
class ModelEntry:
    file: str
    mmproj: str = ""
    draft: str = ""  # files before 0.3.0: copied to each profile without its own on load, then saved per profile
    profiles: list[Profile] = section("profiles", list)
    invalid: dict[str, str] = invalid_values()

    def profile(self, name: str) -> Profile | None:
        return next((p for p in self.profiles if p.name == name), None)


_DEFAULT_SETTINGS_TEXT = """\
# llama-launcher settings. Edited by the app; hand edits are picked up automatically.
port = 9931
listen = "127.0.0.1"   # "0.0.0.0" to listen on the LAN
api_key = ""           # empty = no key
launch_at_startup = false
default_runtime = ""
model_folders = []

[global]
idle_mode = "sleep"    # sleep | stop | off
idle_seconds = 3600
args = ""
"""
_DEFAULT_PROFILES_TEXT = """\
# llama-launcher profiles: one [[models]] per .gguf, each with one or more [[models.profiles]].
# Profile fields left out inherit from [global] in settings.toml.
"""


def _default(f):
    return f.default_factory() if f.default_factory is not MISSING else f.default


def _fill(cls, table, where: str, problems: list[str]) -> tuple:
    """Typed scalar fields of cls from a TOML table; its sections are left to the caller.
    Unknown keys are reported and skipped. Returns (obj, {key: problem}) for values of the wrong type,
    which are left at their defaults."""
    kwargs, bad = {}, {}
    known, nested = _scalars(cls), _sections(cls)
    for key, value in table.items():
        if key in nested:
            continue
        if key not in known:
            problems.append(f"{where}: unknown key '{key}', ignored")
            continue
        d = _default(known[key])
        typ = str if d is MISSING else type(d)
        if (typ is int and isinstance(value, bool)) or not isinstance(value, typ):
            bad[key] = f"{where}: '{key}' must be {typ.__name__}, got {type(value).__name__}"
            continue
        kwargs[key] = value
    return cls(**kwargs), bad


def _check_options(obj, where: str, allow_empty: bool) -> dict[str, str]:
    """{key: problem} for idle and cache values out of range."""
    bad = {}
    if obj.idle_mode not in IDLE_MODES and not (allow_empty and obj.idle_mode == ""):
        bad["idle_mode"] = f"{where}: idle_mode '{obj.idle_mode}' is not one of {', '.join(IDLE_MODES)}"
    if obj.idle_seconds < 0:
        bad["idle_seconds"] = f"{where}: idle_seconds must be >= 0"
    for k in ("cache_type_k", "cache_type_v"):
        v = getattr(obj, k)
        if v and v not in CACHE_TYPES:
            bad[k] = f"{where}: {k} '{v}' is not one of {', '.join(CACHE_TYPES)}"
    return bad


def _mark_invalid(obj, bad: dict[str, str], what: str, problems: list[str]) -> None:
    """A model or profile with bad values loads but can't launch; the values stay at defaults in memory."""
    d = type(obj)() if not isinstance(obj, ModelEntry) else ModelEntry(obj.file)
    for key, msg in bad.items():
        setattr(obj, key, getattr(d, key))
        problems.append(f"{msg}; this {what} can't launch until it's fixed")
    obj.invalid = dict(bad)


def _raise_first(bad: dict[str, str]) -> None:
    if bad:
        raise ConfigError(next(iter(bad.values())))


def _runtime_ok(r) -> bool:
    return isinstance(r, dict) and bool(r.get("name")) and bool(r.get("path"))


def parse_settings(doc, where="settings.toml", problems: list[str] | None = None) -> Settings:
    problems = [] if problems is None else problems
    data = doc.unwrap() if hasattr(doc, "unwrap") else dict(doc)
    # A bad value here affects every profile, so it rejects the file instead of running on a default.
    s, bad = _fill(Settings, data, where, problems)
    _raise_first(bad)
    s.global_, bad = _fill(GlobalOptions, data.get("global", {}), f"{where} [global]", problems)
    _raise_first(bad)
    _raise_first(_check_options(s.global_, f"{where} [global]", allow_empty=False))
    if not 1 <= s.port <= 65535:
        raise ConfigError(f"{where}: port must be 1-65535")
    for i, r in enumerate(data.get("runtimes", [])):
        if not _runtime_ok(r):
            problems.append(f"{where}: runtimes[{i}] needs 'name' and 'path'; skipped")
        elif s.runtime(str(r["name"])):
            problems.append(f"{where}: runtimes[{i}] name '{r['name']}' is used twice; skipped")
        else:
            s.runtimes.append(RuntimeEntry(str(r["name"]), str(r["path"])))
    return s


def parse_profiles(doc, where="profiles.toml", problems: list[str] | None = None) -> list[ModelEntry]:
    problems = [] if problems is None else problems
    data = doc.unwrap() if hasattr(doc, "unwrap") else dict(doc)
    for key in sorted(set(data) - {"models"}):
        problems.append(f"{where}: unknown key '{key}', ignored")
    out: list[ModelEntry] = []
    seen: set[str] = set()
    for i, m in enumerate(data.get("models", [])):
        w = f"{where} models[{i}]"
        if not m.get("file") or not isinstance(m.get("file"), str):
            problems.append(f"{w}: 'file' is required; skipped")
            continue
        entry, bad = _fill(ModelEntry, m, w, problems)
        if norm(entry.file) in seen:
            problems.append(f"{w}: {entry.file} is listed twice; skipped")
            continue
        seen.add(norm(entry.file))
        if bad:
            _mark_invalid(entry, bad, "model", problems)
        for j, p in enumerate(m.get("profiles", [])):
            pw = f"{w}.profiles[{j}]"
            prof, bad = _fill(Profile, p, pw, problems)
            if "name" in bad or not prof.name or entry.profile(prof.name):
                problems.append(f"{pw}: profile name invalid or used twice; skipped")
                continue
            bad.update(_check_options(prof, pw, allow_empty=True))
            if bad:
                _mark_invalid(prof, bad, "profile", problems)
            if entry.draft and "draft" not in p:
                prof.draft = entry.draft
            entry.profiles.append(prof)
        if entry.profiles:
            entry.draft = ""
        out.append(entry)
    return out


def _toml_value(value):
    """Paths as literal strings, so hand edits need no backslash escaping."""
    if isinstance(value, str) and "\\" in value and "'" not in value and "\n" not in value:
        return tomlkit.string(value, literal=True)
    if isinstance(value, list):
        arr = tomlkit.array()
        arr.extend(_toml_value(v) for v in value)
        return arr
    return value


def _first_by(tables, key) -> tuple[dict, list]:
    """Tables by key(table), first one wins; the rest (no key, or repeated) were skipped on load
    and are written back as they are, so a save never drops what the app couldn't read."""
    first: dict = {}
    rest: list = []
    for t in tables:
        k = key(t) if isinstance(t, dict) else None
        if k and k not in first:
            first[k] = t
        else:
            rest.append(t)
    return first, rest


def _sync(table, obj, keep_defaults=()):
    """Write non-default dataclass fields into a tomlkit table; drop keys that went back to default.
    A value marked invalid on load is left as it is in the file until it's edited in the app."""
    invalid = getattr(obj, "invalid", {})
    for f in _scalars(type(obj)).values():
        if f.name in invalid:
            continue
        value = getattr(obj, f.name)
        if value == _default(f) and f.name not in keep_defaults:
            if f.name in table:
                del table[f.name]
        elif f.name not in table or table[f.name] != value:
            table[f.name] = _toml_value(value)


class Config:
    def __init__(self, settings_path: Path, profiles_path: Path):
        self.settings_path = Path(settings_path)
        self.profiles_path = Path(profiles_path)
        self._sdoc = None
        self._pdoc = None
        self.settings = Settings()
        self.models: list[ModelEntry] = []
        self.problems: list[str] = []  # from the last successful load
        self._mtimes: tuple = (None, None)

    # ---- loading ----
    def _mtime(self, p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None

    def load(self) -> None:
        """Load both files (creating defaults). A file that isn't valid TOML raises and the previous
        config is kept; bad keys and entries are skipped and listed in self.problems."""
        if not self.settings_path.exists():
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(_DEFAULT_SETTINGS_TEXT, encoding="utf-8")
        if not self.profiles_path.exists():
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            self.profiles_path.write_text(_DEFAULT_PROFILES_TEXT, encoding="utf-8")
        try:
            sdoc = tomlkit.parse(read_text(self.settings_path))
        except Exception as e:
            raise ConfigError(f"{self.settings_path.name}: {e}") from e
        try:
            pdoc = tomlkit.parse(read_text(self.profiles_path))
        except Exception as e:
            raise ConfigError(f"{self.profiles_path.name}: {e}") from e
        problems: list[str] = []
        settings = parse_settings(sdoc, self.settings_path.name, problems)
        models = parse_profiles(pdoc, self.profiles_path.name, problems)
        self._sdoc, self._pdoc, self.settings, self.models = sdoc, pdoc, settings, models
        self.problems = problems
        self._mtimes = (self._mtime(self.settings_path), self._mtime(self.profiles_path))

    def changed_on_disk(self) -> bool:
        return (self._mtime(self.settings_path), self._mtime(self.profiles_path)) != self._mtimes

    # ---- saving ----
    def save_settings(self) -> None:
        s, doc = self.settings, self._sdoc
        _sync(doc, s, keep_defaults=tuple(_scalars(Settings)))
        if "global" not in doc:
            doc["global"] = tomlkit.table()
        _sync(doc["global"], s.global_, keep_defaults=("idle_mode", "idle_seconds", "args"))
        existing, skipped = _first_by(doc.get("runtimes", []), lambda t: t["name"] if _runtime_ok(t) else None)
        aot = AoT([])
        for r in s.runtimes:
            t = existing.get(r.name) or tomlkit.table()
            t["name"], t["path"] = r.name, _toml_value(r.path)
            aot.append(t)
        for t in skipped:
            aot.append(t)
        if "runtimes" in doc:
            del doc["runtimes"]
        if len(aot):
            doc["runtimes"] = aot
        self._write(self.settings_path, doc)

    def save_profiles(self) -> None:
        doc = self._pdoc
        current = doc.get("models") or []
        same_shape = len(current) == len(self.models) and all(
            norm(t.get("file") or "") == norm(m.file)
            and [p.get("name", DEFAULT_PROFILE) for p in t.get("profiles", [])] == [p.name for p in m.profiles]
            for t, m in zip(current, self.models, strict=True)
        )
        if same_shape:
            for t, m in zip(current, self.models, strict=True):
                _sync(t, m, keep_defaults=("file",))
                for pt, p in zip(t.get("profiles", []), m.profiles, strict=True):
                    _sync(pt, p, keep_defaults=("name",))
            self._write(self.profiles_path, doc)
            return
        old_models, skipped = _first_by(
            doc.get("models", []), lambda t: norm(t["file"]) if isinstance(t.get("file"), str) and t["file"] else None
        )
        aot = AoT([])
        for m in self.models:
            t = old_models.get(norm(m.file))
            if t is None:
                t = tomlkit.table()
            _sync(t, m, keep_defaults=("file",))
            old_profiles, skipped_profiles = _first_by(t.get("profiles", []), lambda p: p.get("name", DEFAULT_PROFILE))
            paot = AoT([])
            for p in m.profiles:
                pt = old_profiles.get(p.name) or tomlkit.table()
                _sync(pt, p, keep_defaults=("name",))
                paot.append(pt)
            for pt in skipped_profiles:
                paot.append(pt)
            if "profiles" in t:
                del t["profiles"]
            if len(paot):
                t["profiles"] = paot
            aot.append(t)
        for t in skipped:
            aot.append(t)
        if "models" in doc:
            del doc["models"]
        if len(aot):
            doc["models"] = aot
        self._write(self.profiles_path, doc)

    def _write(self, path: Path, doc) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(re.sub(r"\n{3,}", "\n\n", tomlkit.dumps(doc)), encoding="utf-8")
        replace_file(tmp, path)
        self._mtimes = (self._mtime(self.settings_path), self._mtime(self.profiles_path))

    # ---- lookups ----
    def model(self, file: str) -> ModelEntry | None:
        key = norm(file)
        return next((m for m in self.models if norm(m.file) == key), None)
