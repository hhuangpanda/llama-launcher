import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from llama_launcher.runtimes import HelpTable, parse_help  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


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
