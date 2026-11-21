import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from helpers import FIXTURES, PYTHON, free_port, pid_alive, spec, wait_for

from llama_launcher import server
from llama_launcher.config import GlobalOptions, Profile, Settings
from llama_launcher.paths import read_text
from llama_launcher.server import ServerManager, State, http_json


@pytest.fixture
def mgr(tmp_path):
    states = []
    m = ServerManager(
        tmp_path / "logs",
        tmp_path / "cwd",
        on_change=lambda s: states.append(s.state),
        ready_timeout=10,
        poll=0.05,
        idle_poll=0.1,
        stop_timeout=5,
    )
    m.states = states
    yield m
    m.shutdown()


def running(m):
    return wait_for(lambda: m.status.state is State.RUNNING)


# P1
def test_start_ready_stop(mgr, tmp_path):
    (tmp_path / "cwd").mkdir()
    (tmp_path / "cwd" / "ffcache123").write_text("x")
    s = spec(tmp_path)
    mgr.start(s)
    assert running(mgr)
    pid = mgr.status.pid
    assert pid_alive(pid) and not (tmp_path / "cwd" / "ffcache123").exists()
    mgr.stop()
    assert mgr.status.state is State.STOPPED and not pid_alive(pid)
    assert mgr.states[:3] == [State.STARTING, State.STARTING, State.RUNNING]  # 2nd: pid recorded
    assert mgr.states[-2:] == [State.STOPPING, State.STOPPED]


# P2
def test_start_b_stops_a_first(mgr, tmp_path):
    port = free_port()
    mgr.start(spec(tmp_path, port=port, model_name="a.gguf"))
    assert running(mgr)
    pid_a = mgr.status.pid
    mgr.start(spec(tmp_path, port=port, model_name="b.gguf"))
    assert wait_for(lambda: mgr.status.state is State.RUNNING and mgr.status.pid != pid_a)
    assert not pid_alive(pid_a) and mgr.status.spec.alias == "b"


# P3
def test_exit_during_load_is_classified(mgr, tmp_path):
    mgr.start(spec(tmp_path, env={"FAKE_EXIT": "1:error: invalid argument: --bogus-flag"}))
    assert wait_for(lambda: mgr.status.state is State.FAILED)
    st = mgr.status
    assert st.message == "llama-server rejected the argument --bogus-flag"
    assert st.log_path.endswith("server.log") and "exited with code 1" in Path(st.log_path).read_text()


def test_crash_exit_code(mgr, tmp_path):
    mgr.start(spec(tmp_path, env={"FAKE_EXIT": "3221225477:0.01.000.000 E llama_init_from_model: boom"}))
    assert wait_for(lambda: mgr.status.state is State.FAILED)
    assert "access violation" in mgr.status.message and "boom" in mgr.status.message


def test_unexpected_exit_while_running(mgr, tmp_path):
    mgr.start(spec(tmp_path, env={"FAKE_CRASH_AFTER": "2"}))
    assert running(mgr)
    assert wait_for(lambda: mgr.status.state is State.FAILED)
    assert "exited with code 3" in mgr.status.message


# P4
def test_ready_timeout(mgr, tmp_path):
    mgr.ready_timeout = 1
    mgr.start(spec(tmp_path, env={"FAKE_DELAY": "30"}))
    assert wait_for(lambda: mgr.status.state is State.STARTING and mgr.status.pid)
    pid = mgr.status.pid
    assert wait_for(lambda: mgr.status.state is State.FAILED, 10)
    assert "didn't become ready within 1s" in mgr.status.message and not pid_alive(pid)


def test_invalid_spec_fails_without_spawning(mgr, tmp_path):
    mgr.start(spec(tmp_path, profile=Profile("p", args="--port 1")))
    assert mgr.status.state is State.FAILED and "managed by llama-launcher" in mgr.status.message
    assert not (tmp_path / "logs" / "server.log").exists()


# P5
def test_foreign_port_holder_untouched(mgr, tmp_path):
    port = free_port()
    foreign = subprocess.Popen(
        FAKE_CMD + ["--host", "127.0.0.1", "--port", str(port), "--alias", "theirs"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert wait_for(lambda: _health(port))
        mgr.check_port("127.0.0.1", port)
        assert mgr.status.state is State.EXTERNAL and mgr.status.external_ids == ["theirs"]
        mgr.start(spec(tmp_path, port=port))
        assert wait_for(lambda: mgr.status.state is State.EXTERNAL)
        mgr.stop()
        mgr.shutdown()
        assert foreign.poll() is None
    finally:
        foreign.kill()
        foreign.wait()
    assert wait_for(lambda: (mgr.check_port("127.0.0.1", port), mgr.status.state is State.STOPPED)[1])


# P15
def test_start_during_port_watch_is_not_external(mgr, tmp_path, monkeypatch):
    s = spec(tmp_path)
    real_port_in_use = server.port_in_use
    watcher_checking = []

    def port_in_use(listen, port):
        if not watcher_checking:  # the watcher's check is underway: the user starts a model now
            watcher_checking.append(True)
            mgr.start(s)
            assert running(mgr)  # this app's own llama-server holds the port when the check finishes
        return real_port_in_use(listen, port)

    monkeypatch.setattr(server, "port_in_use", port_in_use)
    mgr.check_port(s.host, s.port)
    assert mgr.status.state is State.RUNNING
    assert State.EXTERNAL not in mgr.states


FAKE_CMD = [PYTHON, "-I", str(FIXTURES / "fake_server.py")]


def _health(port):
    try:
        return http_json(f"http://127.0.0.1:{port}/health")[0] == 200
    except OSError:
        return False


# P6
def test_child_dies_with_app(tmp_path):
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            [
                str(Path(__file__).parents[2]),
                str(Path(__file__).parents[1]),
                *[p for p in sys.path if p.endswith("site-packages")],
            ]
        ),
    )
    app = subprocess.Popen(
        [PYTHON, str(FIXTURES / "manager_child.py"), str(tmp_path)],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        child_pid = int(app.stdout.readline())
        assert pid_alive(child_pid)
        app.kill()  # hard kill of the process this test started
        app.wait()
        assert wait_for(lambda: not pid_alive(child_pid), 2)
    finally:
        if app.poll() is None:
            app.kill()


# P7
def test_profile_change_marks_stale_and_restart_applies(mgr, tmp_path):
    port = free_port()
    first = spec(tmp_path, port=port, profile=Profile("p", args="--temp 1.0"))
    mgr.start(first)
    assert running(mgr)
    edited = spec(tmp_path, port=port, profile=Profile("p", args="--temp 0.6"))
    assert mgr.is_stale(edited) and not mgr.is_stale(first)
    mgr.start(edited)
    assert wait_for(lambda: mgr.status.state is State.RUNNING and mgr.status.spec is edited)
    assert "'--temp', '0.6'" in (tmp_path / "logs" / "server.log").read_text()


# P8
def test_settings_change_while_stopped_used_on_next_start(mgr, tmp_path):
    s1 = Settings(global_=GlobalOptions(idle_mode="off", args="--temp 1.0"))
    mgr.start(spec(tmp_path, settings=s1))
    assert running(mgr)
    mgr.stop()
    s2 = Settings(global_=GlobalOptions(idle_mode="off", args="--temp 0.2"))
    mgr.start(spec(tmp_path, settings=s2))
    assert running(mgr)
    assert "--temp 0.2" in (tmp_path / "logs" / "server.log").read_text()


# P9
def test_stop_mode_idle(mgr, tmp_path):
    s = Settings(global_=GlobalOptions(idle_mode="stop", idle_seconds=1))
    mgr.start(spec(tmp_path, settings=s))
    assert running(mgr)
    pid = mgr.status.pid
    assert wait_for(lambda: mgr.status.state is State.STOPPED, 10)
    assert "idle" in mgr.status.message and not pid_alive(pid)
    assert "stopped (idle)" in (tmp_path / "logs" / "server.log").read_text()


def _chat(s):
    req = urllib.request.Request(f"{s.base_url}/v1/chat/completions", data=b"{}", method="POST")
    if s.env.get("LLAMA_API_KEY"):
        req.add_header("Authorization", "Bearer " + s.env["LLAMA_API_KEY"])
    urllib.request.urlopen(req, timeout=2).read()


# P10
def test_stop_mode_activity_keeps_running(mgr, tmp_path):
    s = spec(tmp_path, settings=Settings(global_=GlobalOptions(idle_mode="stop", idle_seconds=1)))
    mgr.start(s)
    assert running(mgr)
    end = time.monotonic() + 2.5
    while time.monotonic() < end:
        _chat(s)
        time.sleep(0.4)
    assert mgr.status.state is State.RUNNING


# P11
def test_sleep_mode_tracks_sleep_and_wake(mgr, tmp_path):
    s = spec(tmp_path, settings=Settings(global_=GlobalOptions(idle_mode="sleep", idle_seconds=1)))
    mgr.start(s)
    assert running(mgr)
    assert wait_for(lambda: mgr.status.state is State.SLEEPING, 10)
    _chat(s)
    assert wait_for(lambda: mgr.status.state is State.RUNNING, 5)


# P12
def test_quit_while_starting(mgr, tmp_path):
    mgr.start(spec(tmp_path, env={"FAKE_DELAY": "30"}))
    assert wait_for(lambda: mgr.status.pid is not None)
    pid = mgr.status.pid
    mgr.shutdown()
    assert mgr.status.state is State.STOPPED and not pid_alive(pid)


# P13
def test_api_key_in_env_only(mgr, tmp_path):
    s = spec(
        tmp_path, settings=Settings(api_key="sekrit-dummy", global_=GlobalOptions(idle_mode="sleep", idle_seconds=60))
    )
    mgr.start(s)
    assert running(mgr)
    text = (tmp_path / "logs" / "server.log").read_text()
    assert "api key set" in text and "sekrit-dummy" not in text
    assert wait_for(lambda: mgr.status.state is State.RUNNING)  # /props polled with the key, no 401 failure


# P14
def test_listen_all(mgr, tmp_path):
    s = spec(tmp_path, settings=Settings(listen="0.0.0.0", global_=GlobalOptions(idle_mode="off")))
    mgr.start(s)
    assert running(mgr)
    assert _health(s.port)


# L1-L3
def test_server_log_rotation(mgr, tmp_path):
    logs = tmp_path / "logs"
    mgr.start(spec(tmp_path, env={"FAKE_EXIT": "1:error: invalid argument: --x"}))
    assert wait_for(lambda: mgr.status.state is State.FAILED)
    first = (logs / "server.log").read_text()
    assert first.startswith("# llama-launcher server run") and "# profile: m / p" in first and "# argv:" in first
    mgr.start(spec(tmp_path))
    assert running(mgr)
    assert (logs / "server.prev.log").read_text() == first
    mgr.start(spec(tmp_path, port=mgr.status.spec.port))
    # read_text retries: the poll can land while the server thread is renaming server.log onto server.prev.log
    assert wait_for(
        lambda: mgr.status.state is State.RUNNING and "invalid argument" not in read_text(logs / "server.prev.log")
    )
    assert sorted(p.name for p in logs.iterdir()) == ["server.log", "server.prev.log"]


# L6
def test_locked_prev_log_does_not_block_start(mgr, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "server.log").write_text("old")
    (logs / "server.prev.log").write_text("older")
    import msvcrt

    with open(logs / "server.prev.log", "r+b") as f:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        mgr.start(spec(tmp_path))
        assert running(mgr)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
