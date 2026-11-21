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


def _scalars(cls) -> dict[str, Field]:
    return {f.name: f for f in fields(cls) if "toml" not in f.metadata}


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
    args: str = ""
    unset: list[str] = field(default_factory=list)


@dataclass
class ModelEntry:
    file: str
    mmproj: str = ""
    draft: str = ""
    profiles: list[Profile] = section("profiles", list)

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


def _fill(cls, table, where: str):
    """Typed scalar fields of cls from a TOML table; its sections are left to the caller."""
    kwargs = {}
    known, nested = _scalars(cls), _sections(cls)
    for key, value in table.items():
        if key in nested:
            continue
        if key not in known:
            raise ConfigError(f"{where}: unknown key '{key}'")
        d = _default(known[key])
        typ = str if d is MISSING else type(d)
        if (typ is int and isinstance(value, bool)) or not isinstance(value, typ):
            raise ConfigError(f"{where}: '{key}' must be {typ.__name__}, got {type(value).__name__}")
        kwargs[key] = value
    return cls(**kwargs)


def _validate_options(where: str, idle_mode: str, idle_seconds: int, ck: str, cv: str, allow_empty: bool):
    if idle_mode not in IDLE_MODES and not (allow_empty and idle_mode == ""):
        raise ConfigError(f"{where}: idle_mode must be one of {', '.join(IDLE_MODES)}")
    if idle_seconds < 0:
        raise ConfigError(f"{where}: idle_seconds must be >= 0")
    for k, v in (("cache_type_k", ck), ("cache_type_v", cv)):
        if v and v not in CACHE_TYPES:
            raise ConfigError(f"{where}: {k} '{v}' is not one of {', '.join(CACHE_TYPES)}")


def parse_settings(doc, where="settings.toml") -> Settings:
    data = doc.unwrap() if hasattr(doc, "unwrap") else dict(doc)
    s = _fill(Settings, data, where)
    s.global_ = _fill(GlobalOptions, data.get("global", {}), f"{where} [global]")
    _validate_options(
        f"{where} [global]",
        s.global_.idle_mode,
        s.global_.idle_seconds,
        s.global_.cache_type_k,
        s.global_.cache_type_v,
        allow_empty=False,
    )
    if not 1 <= s.port <= 65535:
        raise ConfigError(f"{where}: port must be 1-65535")
    for i, r in enumerate(data.get("runtimes", [])):
        if not isinstance(r, dict) or not r.get("name") or not r.get("path"):
            raise ConfigError(f"{where}: runtimes[{i}] needs 'name' and 'path'")
        s.runtimes.append(RuntimeEntry(str(r["name"]), str(r["path"])))
    names = [r.name for r in s.runtimes]
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}: duplicate runtime name")
    return s


def parse_profiles(doc, where="profiles.toml") -> list[ModelEntry]:
    data = doc.unwrap() if hasattr(doc, "unwrap") else dict(doc)
    unknown = set(data) - {"models"}
    if unknown:
        raise ConfigError(f"{where}: unknown key '{sorted(unknown)[0]}'")
    out: list[ModelEntry] = []
    seen: set[str] = set()
    for i, m in enumerate(data.get("models", [])):
        w = f"{where} models[{i}]"
        if not m.get("file"):
            raise ConfigError(f"{w}: 'file' is required")
        entry = _fill(ModelEntry, m, w)
        if norm(entry.file) in seen:
            raise ConfigError(f"{w}: {entry.file} is listed twice")
        seen.add(norm(entry.file))
        names = set()
        for j, p in enumerate(m.get("profiles", [])):
            prof = _fill(Profile, p, f"{w}.profiles[{j}]")
            if not prof.name or prof.name in names:
                raise ConfigError(f"{w}.profiles[{j}]: profile name missing or duplicated")
            names.add(prof.name)
            _validate_options(
                f"{w}.profiles[{j}]",
                prof.idle_mode,
                prof.idle_seconds,
                prof.cache_type_k,
                prof.cache_type_v,
                allow_empty=True,
            )
            entry.profiles.append(prof)
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


def _sync(table, obj, keep_defaults=()):
    """Write non-default dataclass fields into a tomlkit table; drop keys that went back to default."""
    for f in _scalars(type(obj)).values():
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
        self._mtimes: tuple = (None, None)

    # ---- loading ----
    def _mtime(self, p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None

    def load(self) -> None:
        """Load both files (creating defaults). On error the previous config is kept."""
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
        settings = parse_settings(sdoc, self.settings_path.name)
        models = parse_profiles(pdoc, self.profiles_path.name)
        self._sdoc, self._pdoc, self.settings, self.models = sdoc, pdoc, settings, models
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
        existing = {t["name"]: t for t in doc.get("runtimes", [])}
        aot = AoT([])
        for r in s.runtimes:
            t = existing.get(r.name) or tomlkit.table()
            t["name"], t["path"] = r.name, _toml_value(r.path)
            aot.append(t)
        if "runtimes" in doc:
            del doc["runtimes"]
        if s.runtimes:
            doc["runtimes"] = aot
        self._write(self.settings_path, doc)

    def save_profiles(self) -> None:
        doc = self._pdoc
        current = doc.get("models") or []
        same_shape = len(current) == len(self.models) and all(
            norm(t["file"]) == norm(m.file)
            and [p["name"] for p in t.get("profiles", [])] == [p.name for p in m.profiles]
            for t, m in zip(current, self.models, strict=True)
        )
        if same_shape:
            for t, m in zip(current, self.models, strict=True):
                _sync(t, m, keep_defaults=("file",))
                for pt, p in zip(t.get("profiles", []), m.profiles, strict=True):
                    _sync(pt, p, keep_defaults=("name",))
            self._write(self.profiles_path, doc)
            return
        old_models = {norm(t["file"]): t for t in doc.get("models", [])}
        aot = AoT([])
        for m in self.models:
            t = old_models.get(norm(m.file))
            if t is None:
                t = tomlkit.table()
            _sync(t, m, keep_defaults=("file",))
            old_profiles = {p["name"]: p for p in t.get("profiles", [])}
            paot = AoT([])
            for p in m.profiles:
                pt = old_profiles.get(p.name) or tomlkit.table()
                _sync(pt, p, keep_defaults=("name",))
                paot.append(pt)
            if "profiles" in t:
                del t["profiles"]
            if m.profiles:
                t["profiles"] = paot
            aot.append(t)
        if "models" in doc:
            del doc["models"]
        if self.models:
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
