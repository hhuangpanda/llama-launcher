import os
from pathlib import Path

import pytest

from llama_launcher.runtimes import HelpTable, parse_help

FIXTURES = Path(__file__).parent / "fixtures"


def _load_dotenv(path: Path) -> None:
    """KEY=value lines from the git-ignored .env; variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip().strip("\"'"))


_load_dotenv(Path(__file__).parent.parent / ".env")


@pytest.fixture(scope="session")
def release_help() -> HelpTable:
    return parse_help((FIXTURES / "help" / "release-help.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def local_help() -> HelpTable:
    return parse_help((FIXTURES / "help" / "cuda-local-help.txt").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LLAMA_LAUNCHER_HOME", str(tmp_path / "home"))
    for k in [k for k in os.environ if k.startswith("LLAMA_ARG_")]:
        monkeypatch.delenv(k)
