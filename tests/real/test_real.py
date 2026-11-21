"""Opt-in: pytest -m real. Loads real models with real llama-server builds.

    LLAMA_LAUNCHER_TEST_MODELS    folder with .gguf models (and their mmproj/draft files)
    LLAMA_LAUNCHER_TEST_RUNTIMES  one or more folders holding llama-server.exe, separated by ;

Skipped when either is unset. Models are picked from the folder: the smallest one for load tests."""

import json
import os
import urllib.request

import pytest
from helpers import free_port, pid_alive, wait_for

from llama_launcher import catalog, launch
from llama_launcher.config import GlobalOptions, ModelEntry, Profile, Settings
from llama_launcher.runtimes import Runtime, RuntimeProbe
from llama_launcher.server import ServerManager, State

pytestmark = pytest.mark.real

MODELS_DIR = os.environ.get("LLAMA_LAUNCHER_TEST_MODELS", "")
RUNTIMES = [p for p in os.environ.get("LLAMA_LAUNCHER_TEST_RUNTIMES", "").split(";") if p]
probe = RuntimeProbe()


@pytest.fixture(scope="module")
def entries():
    if not MODELS_DIR or not RUNTIMES:
        pytest.skip("set LLAMA_LAUNCHER_TEST_MODELS and LLAMA_LAUNCHER_TEST_RUNTIMES")
    found = catalog.scan([MODELS_DIR])
    if not catalog.models(found):
        pytest.skip(f"no models in {MODELS_DIR}")
    return found


def smallest(entries, pred=lambda e: True):
    ms = [e for e in catalog.models(entries) if pred(e)]
    return min(ms, key=lambda e: os.path.getsize(e.path)) if ms else None


def settings(idle="off", seconds=3600):
    return Settings(port=free_port(), global_=GlobalOptions(idle_mode=idle, idle_seconds=seconds,
                                                         args="--flash-attn on --fit on --parallel 1"))


def spec_for(s, model, profile):
    rt = Runtime("test", RUNTIMES[0])
    info = probe.info(rt)
    return launch.build(s, model, profile, rt, info.help_table, info.version, get_info=catalog.InfoCache().get)


def chat(spec):
    req = urllib.request.Request(f"{spec.base_url}/v1/chat/completions", method="POST",
                                 data=json.dumps({"messages": [{"role": "user", "content": "Say hi"}],
                                                  "max_tokens": 8}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


@pytest.fixture
def mgr(tmp_path):
    m = ServerManager(tmp_path / "logs", tmp_path / "cwd", ready_timeout=300, idle_poll=2)
    yield m
    m.shutdown()


def running_or_failed(m):
    return wait_for(lambda: m.status.state in (State.RUNNING, State.FAILED), 300)


# R4
def test_real_runtimes():
    if not RUNTIMES:
        pytest.skip("set LLAMA_LAUNCHER_TEST_RUNTIMES")
    for path in RUNTIMES:
        info = probe.info(Runtime("test", path))
        assert info.version.build and all(info.help_table.knows(f) for f in ("--ctx-size", "--spec-type", "--sleep-idle-seconds"))


# X1: smallest model with its suggested sidecars loads, answers and stops
def test_load_answer_stop(mgr, entries):
    e = smallest(entries)
    model = launch.implicit_model(e, catalog.suggest(e, entries))
    sp = spec_for(settings(), model, Profile("x", ctx_size=4096, args=model.profiles[0].args))
    assert sp.ok, sp.diagnostics
    mgr.start(sp)
    assert running_or_failed(mgr) and mgr.status.state is State.RUNNING, mgr.status.message
    pid = mgr.status.pid
    assert chat(sp)["choices"]
    with urllib.request.urlopen(f"{sp.base_url}/v1/models") as r:
        assert json.loads(r.read())["data"][0]["id"] == e.stem
    mgr.stop()
    assert not pid_alive(pid)


# X2: a model in a folder with several mmproj files loads with the one picked by metadata
def test_flat_folder_pairing_loads(mgr, entries):
    def crowded(e):
        same_dir = [x for x in entries if x.kind is catalog.Kind.MMPROJ and x.directory == e.directory]
        return len(same_dir) > 1 and catalog.suggest(e, entries).mmproj
    e = smallest(entries, crowded)
    if e is None:
        pytest.skip("no model shares its folder with more than one mmproj")
    model = launch.implicit_model(e, catalog.suggest(e, entries))
    mgr.start(spec_for(settings(), model, Profile("x", ctx_size=4096, args=model.profiles[0].args)))
    assert running_or_failed(mgr) and mgr.status.state is State.RUNNING, mgr.status.message


# X3: a mismatched mmproj is refused before anything starts
def test_wrong_mmproj_blocked(mgr, entries):
    e = smallest(entries)
    wrong = next((x for x in entries if x.kind is catalog.Kind.MMPROJ and not catalog.mmproj_fits(e.info, x.info)), None)
    if wrong is None:
        pytest.skip("no mismatching mmproj in the folder")
    mgr.start(spec_for(settings(), ModelEntry(e.path, mmproj=wrong.path), Profile("x", ctx_size=4096)))
    assert mgr.status.state is State.FAILED and "doesn't fit this model" in mgr.status.message
    assert mgr.status.pid is None


# X4
def test_sleep_then_wake(mgr, entries):
    e = smallest(entries)
    sp = spec_for(settings("sleep", 10), ModelEntry(e.path), Profile("x", ctx_size=4096))
    mgr.start(sp)
    assert running_or_failed(mgr) and mgr.status.state is State.RUNNING, mgr.status.message
    assert wait_for(lambda: mgr.status.state is State.SLEEPING, 60)
    assert chat(sp)["choices"]
    assert wait_for(lambda: mgr.status.state is State.RUNNING, 10)
