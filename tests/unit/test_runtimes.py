import os
import sys
import time

import pytest
from conftest import FIXTURES

from llama_launcher.runtimes import Runtime, RuntimeProbe, parse_version


# R1
def test_folder_without_exe(tmp_path):
    assert "llama-server.exe not found" in Runtime("x", str(tmp_path)).check()
    assert "folder not found" in Runtime("x", str(tmp_path / "nope")).check()
    with pytest.raises(RuntimeError, match="not found"):
        RuntimeProbe().info(Runtime("x", str(tmp_path)))


# R2
@pytest.mark.parametrize("name,label", [("release", "b11860 (df9beac71)"), ("cuda-local", "b12198 (f477f795c)")])
def test_version_fixtures(name, label):
    v = parse_version((FIXTURES / "help" / f"{name}-version.txt").read_text())
    assert v.label() == label


def test_version_unknown_and_old_format():
    assert parse_version("garbage").label() == "unknown build"
    assert parse_version("version: 4567 (abc123)").build == 4567


def test_help_aliases_and_values(release_help):
    h = release_help
    assert h.canonical("-md") == h.canonical("--model-draft") == "--spec-draft-model"
    assert h.canonical("--no-mmproj-offload") == "--mmproj-offload"
    assert h.needs_value("--kv-unified") is False and h.needs_value("-fa") is True
    assert h.needs_value("--spec-type") is True and h.needs_value("--nope") is None


# R3
def test_probe_cached_until_exe_changes(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text(
        "import sys; print('version: 1.0 (build 7, commit abc)' if '--version' in sys.argv else '--ctx-size N   x')"
    )
    rt = Runtime("fake", str(tmp_path), command_prefix=[sys.executable, str(script)])
    probe = RuntimeProbe()
    assert probe.info(rt).version.build == 7
    probe.info(rt)
    assert probe.runs == 1
    os.utime(script, ns=(time.time_ns(), time.time_ns() + 10**9))
    probe.info(rt)
    assert probe.runs == 2
