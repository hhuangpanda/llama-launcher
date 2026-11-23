"""Profile -> llama-server command line, plus pre-launch validation."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from llama_launcher.args import ArgError, Flag, Merger, parse_text, reserved_in, tokenize
from llama_launcher.catalog import FileEntry, Kind, Suggestion, draft_fits, mmproj_fits, suggest
from llama_launcher.config import DEFAULT_PROFILE, IdleMode, ModelEntry, Profile, Settings
from llama_launcher.gguf import GGUFError, GGUFInfo
from llama_launcher.paths import norm
from llama_launcher.runtimes import EXE_NAME, HelpTable, Runtime, VersionInfo
from llama_launcher.server_flags import (
    ALIAS,
    CACHE_FLAGS,
    CTX_SIZE,
    DRAFT_MODEL,
    DRAFT_MTP,
    HOST,
    METRICS,
    METRICS_MIN_BUILD,
    MMPROJ,
    MODEL,
    PORT,
    PROFILE_FLAGS,
    RESERVED,
    SIDECAR_FLAGS,
    SLEEP_IDLE_SECONDS,
    SPEC_TYPE,
)

ENV_STRIP_PREFIXES = ("LLAMA_ARG_",)
ENV_STRIP = ("LLAMA_API_KEY",)


@dataclass(frozen=True)
class ProfileKey:
    file: str  # normalized model path
    name: str

    @staticmethod
    def of(model: ModelEntry, profile: Profile) -> ProfileKey:
        return ProfileKey(norm(model.file), profile.name)


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Diagnostic:
    level: Level
    message: str


class Diagnostics(list[Diagnostic]):
    def error(self, message: str) -> None:
        self.append(Diagnostic(Level.ERROR, message))

    def warn(self, message: str) -> None:
        self.append(Diagnostic(Level.WARNING, message))


@dataclass
class LaunchSpec:
    key: ProfileKey
    title: str
    runtime: Runtime | None
    version: VersionInfo | None
    argv: list[str]
    env: dict[str, str]
    alias: str
    host: str
    port: int
    idle_mode: str
    idle_seconds: int
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    @property
    def ok(self) -> bool:
        return not any(d.level == Level.ERROR for d in self.diagnostics)

    @property
    def connect_host(self) -> str:
        return connect_host(self.host)

    @property
    def base_url(self) -> str:
        return f"http://{self.connect_host}:{self.port}"

    def comparable(self) -> tuple:
        """What decides whether a running server is stale."""
        return (tuple(self.argv), tuple(sorted(self.env.items())))


def listen_hosts(listen: str) -> list[str]:
    """llama-server's --host takes a comma-separated list."""
    return [h.strip() for h in (listen or "127.0.0.1").split(",") if h.strip()] or ["127.0.0.1"]


def connect_host(listen: str) -> str:
    """Where local clients reach a server listening on `listen`."""
    hosts = listen_hosts(listen)
    if "127.0.0.1" in hosts or "0.0.0.0" in hosts:
        return "127.0.0.1"
    return hosts[0]


def global_ctx(args: str) -> int:
    """--ctx-size/-c value in a raw args string, 0 when absent."""
    try:
        toks = tokenize(args)
    except ArgError:
        return 0
    for i, t in enumerate(toks[:-1]):
        if t in (CTX_SIZE, "-c") and toks[i + 1].isdigit():
            return int(toks[i + 1])
    return 0


def ctx_label(n: int) -> str:
    return f"{n // 1024}k" if n % 1024 == 0 else str(n)


def stem(path: str) -> str:
    base = os.path.basename(path)
    return base[:-5] if base.lower().endswith(".gguf") else base


def implicit_model(entry: FileEntry, sug: Suggestion) -> ModelEntry:
    """A scanned model with no profiles.toml entry: one default profile with suggested sidecars."""
    args = f"{SPEC_TYPE} {DRAFT_MTP}" if (sug.draft or sug.builtin_mtp) else ""
    return ModelEntry(
        entry.path, mmproj=sug.mmproj or "", draft=sug.draft or "", profiles=[Profile(DEFAULT_PROFILE, args=args)]
    )


@dataclass
class ModelRow:
    model: ModelEntry
    implicit: bool
    exists: bool


def model_rows(configured: list[ModelEntry], entries: list[FileEntry]) -> list[ModelRow]:
    rows: list[ModelRow] = []
    seen = set()
    for m in configured:
        seen.add(norm(m.file))
        rows.append(ModelRow(m, False, os.path.isfile(m.file)))
    for e in entries:
        if e.kind is Kind.MODEL and norm(e.path) not in seen:
            rows.append(ModelRow(implicit_model(e, suggest(e, entries)), True, True))
    rows.sort(key=lambda r: stem(r.model.file).lower())
    return rows


def resolve_runtime_name(settings: Settings, profile: Profile) -> str:
    if profile.runtime:
        return profile.runtime
    if settings.default_runtime:
        return settings.default_runtime
    return settings.runtimes[0].name if settings.runtimes else ""


def effective_idle(settings: Settings, profile: Profile) -> tuple[str, int]:
    mode = profile.idle_mode or settings.global_.idle_mode
    seconds = profile.idle_seconds or settings.global_.idle_seconds
    if mode != IdleMode.OFF and seconds <= 0:
        mode = IdleMode.OFF.value
    return mode, seconds


def _field_flags(obj, mapping: dict[str, str], layer: str) -> list[Flag]:
    """A flag for each field of obj in mapping that is set."""
    return [Flag(flag, str(value), layer) for attr, flag in mapping.items() if (value := getattr(obj, attr))]


def _raw_flags(text: str, layer: str, help_table: HelpTable, runtime: Runtime | None, diags: Diagnostics) -> list[Flag]:
    """Flags typed into an args box, minus the ones the launcher manages; problems go to diags."""
    try:
        flags = parse_text(text, help_table, layer)
    except ArgError as e:
        diags.error(str(e))
        return []
    bad = reserved_in(flags, help_table)
    for b in bad:
        diags.error(f"{layer}: {b} is managed by llama-launcher and can't be set here")
    keep = [f for f in flags if f.flag not in bad]
    if help_table.canon:
        for f in keep:
            if not help_table.knows(f.flag):
                diags.error(
                    f"{layer}: {f.flag} is not a flag of this llama-server build"
                    + (f" ({runtime.name})" if runtime else "")
                )
    return keep


def build(
    settings: Settings,
    model: ModelEntry,
    profile: Profile,
    runtime: Runtime | None,
    help_table: HelpTable | None,
    version: VersionInfo | None = None,
    ctx_override: int | None = None,
    get_info: Callable[[str], GGUFInfo] | None = None,
    base_env: dict[str, str] | None = None,
) -> LaunchSpec:
    diags = Diagnostics()
    help_table = help_table or HelpTable()
    if runtime is None:
        diags.error(f"no llama.cpp runtime configured (profile runtime '{resolve_runtime_name(settings, profile)}')")
    alias = profile.alias or stem(model.file)
    idle_mode, idle_seconds = effective_idle(settings, profile)
    m = Merger(help_table)
    # 1 managed (model fields)
    m.add([Flag(MODEL, model.file, "model"), Flag(ALIAS, alias, "profile")])
    m.add(_field_flags(model, SIDECAR_FLAGS, "model"))
    # 2 global structured
    g = settings.global_
    m.add(_field_flags(g, CACHE_FLAGS, "global"))
    # 3 global raw
    m.add(_raw_flags(g.args, "global args", help_table, runtime, diags))
    # 4 unset
    for u in profile.unset:
        if help_table.canonical(u) in {help_table.canonical(r) for r in RESERVED}:
            diags.error(f"unset: {u} can't be removed")
        else:
            m.remove(u)
    # 5 profile structured
    m.add(_field_flags(profile, PROFILE_FLAGS, "profile"))
    if idle_mode == IdleMode.OFF:
        m.remove(SLEEP_IDLE_SECONDS)
    else:
        m.add([Flag(SLEEP_IDLE_SECONDS, str(idle_seconds), "idle")])
    if version and (version.build or 0) >= METRICS_MIN_BUILD:
        m.add([Flag(METRICS, None, "status")])  # the tray reads /metrics to tell running from idle
    # 6 profile raw
    prof_flags = _raw_flags(profile.args, "profile args", help_table, runtime, diags)
    sidecars = {help_table.canonical(flag): attr for attr, flag in SIDECAR_FLAGS.items()}
    for f in prof_flags:
        attr = sidecars.get(help_table.canonical(f.flag))
        if attr and getattr(model, attr):
            diags.warn(f"profile args {f.flag} overrides the model's {attr}")
    m.add(prof_flags)
    # 7 one-shot
    if ctx_override:
        m.add([Flag(CTX_SIZE, str(ctx_override), "one-shot")])

    host = settings.listen or "127.0.0.1"
    argv = (runtime.command() if runtime else [EXE_NAME]) + [HOST, host, PORT, str(settings.port)] + m.argv()

    env = {
        k: v
        for k, v in (base_env if base_env is not None else os.environ).items()
        if k not in ENV_STRIP and not k.startswith(ENV_STRIP_PREFIXES)
    }
    if settings.api_key:
        env["LLAMA_API_KEY"] = settings.api_key

    _validate_files(m, get_info, diags)
    if runtime is not None and (p := runtime.check()):
        diags.error(p)

    return LaunchSpec(
        key=ProfileKey.of(model, profile),
        title=f"{stem(model.file)} / {profile.name}",
        runtime=runtime,
        version=version,
        argv=argv,
        env=env,
        alias=(m.get(ALIAS).value if m.get(ALIAS) else alias),
        host=host,
        port=settings.port,
        idle_mode=idle_mode,
        idle_seconds=idle_seconds,
        diagnostics=diags,
    )


def _validate_files(m: Merger, get_info: Callable[[str], GGUFInfo] | None, diags: Diagnostics) -> None:
    def val(flag):
        f = m.get(flag)
        return f.value if f else None

    model_path, mmproj, draft = val(MODEL), val(MMPROJ), val(DRAFT_MODEL)
    for label, p in (("model", model_path), ("mmproj", mmproj), ("draft", draft)):
        if p and not os.path.isfile(p):
            diags.error(f"{label} file not found: {p}")
    if get_info is None or not model_path or not os.path.isfile(model_path):
        return

    def info(p):
        try:
            return get_info(p)
        except (GGUFError, OSError) as e:
            diags.error(f"can't read {os.path.basename(p)}: {e}")
            return None

    mi = info(model_path)
    if mi is None:
        return
    if mmproj and os.path.isfile(mmproj) and (pi := info(mmproj)) and not mmproj_fits(mi, pi):
        diags.error(
            f"mmproj {os.path.basename(mmproj)} doesn't fit this model "
            f"(projection {', '.join(map(str, pi.projection_dims)) or '?'} vs model embedding {mi.embedding_length})"
        )
    di = None
    if draft and os.path.isfile(draft) and (di := info(draft)) and not draft_fits(mi, di):
        diags.error(f"draft {os.path.basename(draft)} (arch {di.arch}) doesn't fit a {mi.arch} model")
    spec_types = (val(SPEC_TYPE) or "").split(",")
    if DRAFT_MTP in spec_types and not draft and mi.nextn_layers == 0:
        diags.error("--spec-type draft-mtp needs a draft file or a model with built-in MTP; this model has neither")
    if di is not None and di.arch.endswith("-assistant") and DRAFT_MTP not in spec_types:
        diags.error(
            f"draft {os.path.basename(draft)} is an MTP head: add --spec-type draft-mtp (llama-server crashes without it)"
        )
    ctx = val(CTX_SIZE)
    if ctx and ctx.isdigit() and mi.context_length and int(ctx) > mi.context_length:
        diags.warn(f"ctx-size {ctx} is larger than the model's trained context ({mi.context_length})")
