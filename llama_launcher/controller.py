"""Glue between config, catalog, runtimes and the server manager, for the Qt UI."""

from __future__ import annotations

import copy
import logging
import os
from enum import StrEnum

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from llama_launcher import catalog, launch, paths, startup
from llama_launcher.config import Config, ConfigError, ModelEntry, Profile, Settings
from llama_launcher.launch import LaunchSpec, ProfileKey
from llama_launcher.runtimes import Runtime, RuntimeInfo, RuntimeProbe
from llama_launcher.server import ServerManager, State, Status

log = logging.getLogger("llama_launcher.controller")

CONTEXT_CHOICES = (8192, 16384, 32768, 65536, 131072, 196608, 262144, 393216, 524288)


class CtxSource(StrEnum):
    """Where the context of a start comes from."""

    PICKED = "picked"  # chosen in the tray for the next start
    PROFILE = "profile"
    GLOBAL = "global"  # -c/--ctx-size in global args
    MODEL = "model"  # trained maximum; --fit may lower it
    NONE = "none"


class Controller(QObject):
    status_changed = Signal(object)  # Status
    config_changed = Signal()
    config_error = Signal(str)
    config_problems = Signal(list)  # keys and entries skipped by a successful load

    def __init__(
        self,
        config: Config | None = None,
        manager: ServerManager | None = None,
        runtime_factory=None,
        watch: bool = True,
    ):
        super().__init__()
        self.config = config or Config(paths.settings_file(), paths.profiles_file())
        self.probe = RuntimeProbe()
        self.info_cache = catalog.InfoCache(paths.gguf_cache_file())
        self.entries: list[catalog.FileEntry] = []
        self.runtime_factory = runtime_factory or (lambda e: Runtime(e.name, e.path))
        self.manager = manager or ServerManager(paths.logs_dir(), paths.server_cwd())
        self.manager.on_change = self.status_changed.emit
        self.startup_key = startup.RUN_KEY
        self.last_key: ProfileKey | None = None
        self._next_ctx: dict[str, int | None] = {}
        self.load_error = ""
        try:
            self.config.load()
        except ConfigError as e:
            self.load_error = str(e)
            log.error("config: %s", e)
        self._log_problems()
        self.rescan()
        self._watcher = None
        if watch:
            self._watcher = QFileSystemWatcher(self)
            self._debounce = QTimer(self, singleShot=True, interval=300)
            self._debounce.timeout.connect(self.reload_if_changed)
            self._watcher.fileChanged.connect(lambda _: self._debounce.start())
            self._watcher.directoryChanged.connect(lambda _: self._debounce.start())
            self._rewatch()

    # ---- config ----
    def _rewatch(self) -> None:
        if not self._watcher:
            return
        for group in (self._watcher.files(), self._watcher.directories()):
            if group:
                self._watcher.removePaths(group)
        files = [str(p) for p in (self.config.settings_path, self.config.profiles_path) if p.exists()]
        self._watcher.addPaths(files + [str(self.config.settings_path.parent)])

    def reload_if_changed(self) -> bool:
        if not self.config.changed_on_disk():
            return False
        return self.reload()

    def reload(self) -> bool:
        try:
            self.config.load()
        except ConfigError as e:
            self.load_error = str(e)
            log.error("config reload: %s", e)
            self.config_error.emit(str(e))
            return False
        self.load_error = ""
        log.info("config reloaded")
        self._log_problems()
        self.rescan()
        self._rewatch()
        self.config_changed.emit()
        if self.config.problems:
            self.config_problems.emit(list(self.config.problems))
        return True

    def _log_problems(self) -> None:
        for p in self.config.problems:
            log.warning("config: %s", p)

    def save(self, settings: Settings, models: list[ModelEntry]) -> None:
        self.config.settings = settings
        self.config.models = models
        self.config.save_settings()
        self.config.save_profiles()
        if startup.is_enabled(self.startup_key) != settings.launch_at_startup:
            startup.set_enabled(settings.launch_at_startup, self.startup_key)
        self.rescan()
        self._rewatch()
        self.config_changed.emit()

    def rescan(self) -> None:
        self.entries = catalog.scan(self.config.settings.model_folders, self.info_cache)

    @property
    def settings(self) -> Settings:
        return self.config.settings

    def rows(self, models: list[ModelEntry] | None = None) -> list[launch.ModelRow]:
        return launch.model_rows(self.config.models if models is None else models, self.entries)

    def find(self, key: ProfileKey, models: list[ModelEntry] | None = None) -> tuple[ModelEntry, Profile] | None:
        for row in self.rows(models):
            if paths.norm(row.model.file) == key.file:
                p = row.model.profile(key.name)
                if p:
                    return row.model, p
        return None

    # ---- runtimes ----
    def runtime(self, settings: Settings, profile: Profile) -> tuple[Runtime | None, RuntimeInfo | None, str]:
        name = launch.resolve_runtime_name(settings, profile)
        entry = settings.runtime(name)
        if entry is None:
            return None, None, f"runtime '{name}' is not defined" if name else "no runtime configured"
        rt = self.runtime_factory(entry)
        try:
            return rt, self.probe.info(rt), ""
        except RuntimeError as e:
            return rt, None, str(e)

    def build(
        self, model: ModelEntry, profile: Profile, settings: Settings | None = None, ctx_override: int | None = None
    ) -> LaunchSpec:
        settings = settings or self.settings
        rt, info, problem = self.runtime(settings, profile)
        spec = launch.build(
            settings,
            model,
            profile,
            rt if info else None,
            info.help_table if info else None,
            info.version if info else None,
            ctx_override=ctx_override,
            get_info=self.info_cache.get,
        )
        if problem:
            spec.diagnostics = launch.Diagnostics(
                d for d in spec.diagnostics if "no llama.cpp runtime" not in d.message
            )
            spec.diagnostics.insert(0, launch.Diagnostic(launch.Level.ERROR, problem))
        return spec

    # ---- server actions ----
    def next_ctx(self, model_file: str) -> int | None:
        """Context picked in the tray for this model's next start (None = profile default)."""
        return self._next_ctx.get(paths.norm(model_file))

    def set_next_ctx(self, model_file: str, ctx: int | None) -> None:
        self._next_ctx[paths.norm(model_file)] = ctx

    def start_ctx(self, model: ModelEntry, profile: Profile, use_picked: bool = True) -> tuple[int, CtxSource]:
        """Context a start of this profile asks for, and where it comes from."""
        picked = self.next_ctx(model.file) if use_picked else None
        if picked:
            return picked, CtxSource.PICKED
        if profile.ctx_size:
            return profile.ctx_size, CtxSource.PROFILE
        g = launch.global_ctx(self.settings.global_.args)
        if g:
            return g, CtxSource.GLOBAL
        n = self.model_ctx(model)
        return n, CtxSource.MODEL if n else CtxSource.NONE

    def model_ctx(self, model: ModelEntry) -> int:
        """The model's trained context, 0 if unknown."""
        try:
            return self.info_cache.get(model.file).context_length or 0
        except Exception:
            return 0

    def ctx_choices(self, model: ModelEntry) -> tuple[int, ...]:
        """Tray context choices up to the model's trained context (all of them if unknown)."""
        n = self.model_ctx(model)
        return tuple(c for c in CONTEXT_CHOICES if not n or c <= n)

    def start(self, key: ProfileKey) -> LaunchSpec | None:
        found = self.find(key)
        if not found:
            return None
        self.last_key = key
        spec = self.build(*found, ctx_override=self._next_ctx.get(key.file))
        self.manager.start(spec)
        return spec

    def stop(self) -> None:
        self.manager.stop("user")

    def running_key(self) -> ProfileKey | None:
        st = self.manager.status
        return st.spec.key if st.spec and st.active else None

    def is_stale(self) -> bool:
        st = self.manager.status
        if not st.active or not st.spec:
            return False
        found = self.find(st.spec.key)
        if not found:
            return False
        return self.manager.is_stale(self.build(*found, ctx_override=self._next_ctx.get(st.spec.key.file)))

    def restart(self) -> None:
        key = self.running_key() or self.last_key
        if key:
            self.start(key)

    def ctx_save_target(self, model: ModelEntry) -> Profile | None:
        """The profile "save as default" writes to: the running one of this model, else its only profile."""
        rk = self.running_key()
        if rk and rk.file == paths.norm(model.file):
            return model.profile(rk.name)
        return model.profiles[0] if len(model.profiles) == 1 else None

    def save_ctx_default(self, model_file: str) -> bool:
        """Writes the picked context into the target profile (materializing an implicit model)."""
        ctx = self.next_ctx(model_file)
        row = next((r for r in self.rows() if paths.norm(r.model.file) == paths.norm(model_file)), None)
        target = self.ctx_save_target(row.model) if row else None
        if not ctx or target is None:
            return False
        models = copy.deepcopy(self.config.models)
        found = self.find(ProfileKey(paths.norm(model_file), target.name), models)
        if not found:
            return False
        model, profile = found
        profile.ctx_size = ctx
        if not any(m is model for m in models):
            models.append(model)
        self.set_next_ctx(model_file, None)
        self.save(copy.deepcopy(self.settings), models)
        return True

    def port_target(self) -> tuple[str, int, str]:
        s = self.settings
        return s.listen, s.port, s.api_key

    def base_url(self) -> str:
        return f"http://{launch.connect_host(self.settings.listen)}:{self.settings.port}"

    def served_alias(self) -> str:
        st = self.manager.status
        if st.spec:
            return st.spec.alias
        if st.external_ids:
            return os.path.basename(st.external_ids[0])
        return ""

    def curl_example(self) -> str:
        alias = self.served_alias() or "MODEL"
        auth = f' -H "Authorization: Bearer {self.settings.api_key}"' if self.settings.api_key else ""
        body = '{\\"model\\":\\"' + alias + '\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"Hello\\"}]}'
        return f'curl {self.base_url()}/v1/chat/completions -H "Content-Type: application/json"{auth} -d "{body}"'

    def shutdown(self) -> None:
        self.info_cache.save()
        self.manager.shutdown()


def status_lines(st: Status) -> tuple[str, str]:
    """(what is running, details): the tray shows them as two lines; the second may be empty."""
    title = st.spec.title if st.spec else ""
    version = st.spec.version.label() if st.spec and st.spec.version else ""
    ctx = f"{launch.ctx_label(st.n_ctx)} context" if st.n_ctx else ""
    details = ", ".join(x for x in (ctx, version) if x)
    match st.state:
        case State.STOPPED:
            return "Stopped", st.message
        case State.STARTING:
            return f"Starting {title}", version
        case State.RUNNING:
            return (f"Running (idle) {title}" if st.busy is False else f"Running {title}"), details
        case State.SLEEPING:
            return f"Sleeping {title}", details
        case State.STOPPING:
            return "Stopping", ""
        case State.FAILED:
            first = st.message.splitlines()[0] if st.message else "unknown error"
            return (f"Failed {title}" if title else "Failed"), first
        case State.EXTERNAL:
            ids = ", ".join(os.path.basename(i) for i in st.external_ids)
            return "Another server is using the port", ids
    return st.state.value, ""
