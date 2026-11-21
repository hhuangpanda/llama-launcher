import os
from pathlib import Path

APP_NAME = "llama-launcher"


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
