"""App data locations, path normalization, and file access that tolerates brief locks."""

import os
import time
from pathlib import Path

APP_NAME = "llama-launcher"

# Windows refuses to read or replace a file while another handle has it open without sharing
# (a config watcher reloading, an editor, antivirus scanning a file just written). Those holds
# last milliseconds, so retry for up to about half a second before giving up.
_LOCK_ATTEMPTS = 20
_LOCK_DELAY = 0.025


def _retry_locked(fn, *args):
    for _ in range(_LOCK_ATTEMPTS - 1):
        try:
            return fn(*args)
        except PermissionError:
            time.sleep(_LOCK_DELAY)
    return fn(*args)


def replace_file(src: Path, dst: Path) -> None:
    """os.replace, retried while dst is briefly held open elsewhere."""
    _retry_locked(os.replace, src, dst)


def read_text(path: Path) -> str:
    """UTF-8 text of path, retried while it is briefly held open elsewhere (or mid-replace)."""
    return _retry_locked(Path(path).read_text, "utf-8")


def norm(path: str) -> str:
    """Comparison key for a path: absolute, case-folded, Windows separators."""
    return os.path.normcase(os.path.abspath(path))


def home() -> Path:
    override = os.environ.get("LLAMA_LAUNCHER_HOME")
    return Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / APP_NAME


def settings_file() -> Path:
    return home() / "settings.toml"


def profiles_file() -> Path:
    return home() / "profiles.toml"


def logs_dir() -> Path:
    return home() / "logs"


def server_cwd() -> Path:
    return home() / "server-cwd"


def gguf_cache_file() -> Path:
    return home() / "gguf-cache.json"
