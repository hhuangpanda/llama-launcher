"""Profile -> llama-server command line, plus pre-launch validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from llama_launcher import args as A
from llama_launcher.catalog import FileEntry, Kind, Suggestion, draft_fits, mmproj_fits, norm, suggest
from llama_launcher.config import DEFAULT_PROFILE, ModelEntry, Profile, Settings
from llama_launcher.gguf import GGUFError, GGUFInfo
from llama_launcher.runtimes import HelpTable, Runtime, VersionInfo

ENV_STRIP_PREFIXES = ("LLAMA_ARG_",)
ENV_STRIP = ("LLAMA_API_KEY",)


@dataclass(frozen=True)
class ProfileKey:
    file: str  # normalized model path
    name: str

    @staticmethod
    def of(model: ModelEntry, profile: Profile) -> "ProfileKey":
        return ProfileKey(norm(model.file), profile.name)


@dataclass
class Diagnostic:
    level: str  # "error" | "warning"
    message: str


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
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.level == "error" for d in self.diagnostics)

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
        toks = A.tokenize(args)
    except A.ArgError:
        return 0
    for i, t in enumerate(toks[:-1]):
        if t in ("-c", "--ctx-size") and toks[i + 1].isdigit():
            return int(toks[i + 1])
    return 0


def ctx_label(n: int) -> str:
    return f"{n // 1024}k" if n % 1024 == 0 else str(n)


def stem(path: str) -> str:
    base = os.path.basename(path)
    return base[:-5] if base.lower().endswith(".gguf") else base


def implicit_model(entry: FileEntry, sug: Suggestion) -> ModelEntry:
    """A scanned model with no profiles.toml entry: one default profile with suggested sidecars."""
    args = "--spec-type draft-mtp" if (sug.draft or sug.builtin_mtp) else ""
    return ModelEntry(entry.path, mmproj=sug.mmproj or "", draft=sug.draft or "",
                      profiles=[Profile(DEFAULT_PROFILE, args=args)])


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
    mode = profile.idle_mode or settings.glob.idle_mode
    seconds = profile.idle_seconds or settings.glob.idle_seconds
    if mode != "off" and seconds <= 0:
        mode = "off"
    return mode, seconds


def build(
    settings: Settings,
    model: ModelEntry,
    profile: Profile,
    runtime: Runtime | None,
    help: HelpTable | None,
    version: VersionInfo | None = None,
    ctx_override: int | None = None,
    get_info: Callable[[str], GGUFInfo] | None = None,
    base_env: dict[str, str] | None = None,
) -> LaunchSpec:
    diags: list[Diagnostic] = []
    err = lambda m: diags.append(Diagnostic("error", m))
    warn = lambda m: diags.append(Diagnostic("warning", m))

    help = help or HelpTable()
    if runtime is None:
        err(f"no llama.cpp runtime configured (profile runtime '{resolve_runtime_name(settings, profile)}')")
    alias = profile.alias or stem(model.file)
    idle_mode, idle_seconds = effective_idle(settings, profile)
    m = A.Merger(help)

    def raw(text: str, layer: str) -> list[A.Flag]:
        try:
            flags = A.parse_text(text, help, layer)
        except A.ArgError as e:
            err(str(e))
            return []
        bad = A.reserved_in(flags, help)
        for b in bad:
            err(f"{layer}: {b} is managed by llama-launcher and can't be set here")
        keep = [f for f in flags if f.flag not in bad]
        if help.canon:
            for f in keep:
                if not help.knows(f.flag):
                    err(f"{layer}: {f.flag} is not a flag of this llama-server build"
                        + (f" ({runtime.name})" if runtime else ""))
        return keep

    # 1 managed (model fields)
    managed = [A.Flag("--model", model.file, "model"), A.Flag("--alias", alias, "profile")]
    if model.mmproj:
        managed.append(A.Flag("--mmproj", model.mmproj, "model"))
    if model.draft:
        managed.append(A.Flag("--spec-draft-model", model.draft, "model"))
    m.add(managed)
    # 2 global structured
    g = settings.glob
    m.add([A.Flag("--cache-type-k", g.cache_type_k, "global")] if g.cache_type_k else [])
    m.add([A.Flag("--cache-type-v", g.cache_type_v, "global")] if g.cache_type_v else [])
    # 3 global raw
    m.add(raw(g.args, "global args"))
    # 4 unset
    for u in profile.unset:
        if help.canonical(u) in {help.canonical(r) for r in A.RESERVED}:
            err(f"unset: {u} can't be removed")
        else:
            m.remove(u)
    # 5 profile structured
    if profile.ctx_size:
        m.add([A.Flag("--ctx-size", str(profile.ctx_size), "profile")])
    if profile.cache_type_k:
        m.add([A.Flag("--cache-type-k", profile.cache_type_k, "profile")])
    if profile.cache_type_v:
        m.add([A.Flag("--cache-type-v", profile.cache_type_v, "profile")])
    if idle_mode == "off":
        m.remove("--sleep-idle-seconds")
    else:
        m.add([A.Flag("--sleep-idle-seconds", str(idle_seconds), "idle")])
    # 6 profile raw
    prof_flags = raw(profile.args, "profile args")
    for f in prof_flags:
        c = help.canonical(f.flag)
        if c == help.canonical("--mmproj") and model.mmproj:
            warn(f"profile args {f.flag} overrides the model's mmproj")
        if c == help.canonical("--spec-draft-model") and model.draft:
            warn(f"profile args {f.flag} overrides the model's draft")
    m.add(prof_flags)
    # 7 one-shot
    if ctx_override:
        m.add([A.Flag("--ctx-size", str(ctx_override), "one-shot")])

    host = settings.listen or "127.0.0.1"
    argv = (runtime.command() if runtime else ["llama-server.exe"]) + ["--host", host, "--port", str(settings.port)] + m.argv()

    env = {k: v for k, v in (base_env if base_env is not None else os.environ).items()
           if k not in ENV_STRIP and not k.startswith(ENV_STRIP_PREFIXES)}
    if settings.api_key:
        env["LLAMA_API_KEY"] = settings.api_key

    _validate_files(m, help, get_info, err, warn)
    if runtime is not None and (p := runtime.check()):
        err(p)

    return LaunchSpec(
        key=ProfileKey.of(model, profile),
        title=f"{stem(model.file)} / {profile.name}",
        runtime=runtime,
        version=version,
        argv=argv,
        env=env,
        alias=(m.get("--alias").value if m.get("--alias") else alias),
        host=host,
        port=settings.port,
        idle_mode=idle_mode,
        idle_seconds=idle_seconds,
        diagnostics=diags,
    )


def _validate_files(m: A.Merger, help: HelpTable, get_info, err, warn) -> None:
    def val(flag):
        f = m.get(flag)
        return f.value if f else None

    model_path, mmproj, draft = val("--model"), val("--mmproj"), val("--spec-draft-model")
    for label, p in (("model", model_path), ("mmproj", mmproj), ("draft", draft)):
        if p and not os.path.isfile(p):
            err(f"{label} file not found: {p}")
    if get_info is None or not model_path or not os.path.isfile(model_path):
        return

    def info(p):
        try:
            return get_info(p)
        except (GGUFError, OSError) as e:
            err(f"can't read {os.path.basename(p)}: {e}")
            return None

    mi = info(model_path)
    if mi is None:
        return
    if mmproj and os.path.isfile(mmproj) and (pi := info(mmproj)):
        if not mmproj_fits(mi, pi):
            err(f"mmproj {os.path.basename(mmproj)} doesn't fit this model "
                f"(projection {', '.join(map(str, pi.projection_dims)) or '?'} vs model embedding {mi.embedding_length})")
    di = None
    if draft and os.path.isfile(draft) and (di := info(draft)):
        if not draft_fits(mi, di):
            err(f"draft {os.path.basename(draft)} (arch {di.arch}) doesn't fit a {mi.arch} model")
    spec = val("--spec-type") or ""
    if "draft-mtp" in spec.split(",") and not draft and mi.nextn_layers == 0:
        err("--spec-type draft-mtp needs a draft file or a model with built-in MTP; this model has neither")
    if di is not None and di.arch.endswith("-assistant") and "draft-mtp" not in spec.split(","):
        err(f"draft {os.path.basename(draft)} is an MTP head: add --spec-type draft-mtp (llama-server crashes without it)")
    ctx = val("--ctx-size")
    if ctx and ctx.isdigit() and mi.context_length and int(ctx) > mi.context_length:
        warn(f"ctx-size {ctx} is larger than the model's trained context ({mi.context_length})")
