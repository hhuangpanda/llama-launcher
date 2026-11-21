"""Runs one llama-server at a time: spawn, readiness, sleep/idle tracking, stop."""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from llama_launcher import errors
from llama_launcher.args import format_argv
from llama_launcher.config import IdleMode
from llama_launcher.launch import LaunchSpec, Level, connect_host, listen_hosts
from llama_launcher.logs import rotate
from llama_launcher.winjob import job

log = logging.getLogger("llama_launcher.server")


class State(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPING = "stopping"
    FAILED = "failed"
    EXTERNAL = "external"


@dataclass
class Status:
    state: State = State.STOPPED
    spec: LaunchSpec | None = None
    message: str = ""
    pid: int | None = None
    log_path: str = ""
    external_ids: list[str] = field(default_factory=list)
    n_ctx: int | None = None  # context per request, as the running server reports it

    @property
    def active(self) -> bool:
        return self.state in (State.STARTING, State.RUNNING, State.SLEEPING)


def http_json(url: str, key: str = "", timeout: float = 2.0):
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"null")


def port_in_use(listen: str, port: int) -> bool:
    if _connects(connect_host(listen), port):
        return True
    for host in listen_hosts(listen):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((host, port))
        except OSError:
            return True
        finally:
            s.close()
    return False


def _connects(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _clear_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for p in d.iterdir():
        try:
            p.unlink()
        except OSError:
            pass


class ServerManager:
    def __init__(self, logs_dir: Path, cwd_dir: Path, on_change: Callable[[Status], None] | None = None,
                 ready_timeout: float = 300, poll: float = 1.0, idle_poll: float = 15.0, stop_timeout: float = 15):
        self.logs_dir = Path(logs_dir)
        self.cwd_dir = Path(cwd_dir)
        self.on_change = on_change
        self.ready_timeout = ready_timeout
        self.poll = poll
        self.idle_poll = idle_poll
        self.stop_timeout = stop_timeout
        self._lock = threading.RLock()
        self._status = Status()
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._worker: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._watch_target: tuple[str, int, str] | None = None

    @property
    def server_log(self) -> Path:
        return self.logs_dir / "server.log"

    @property
    def status(self) -> Status:
        with self._lock:
            return replace(self._status)

    def _set(self, gen: int | None = None, **kw) -> bool:
        with self._lock:
            if gen is not None and gen != self._gen:
                return False
            self._status = replace(self._status, **kw)
            st = replace(self._status)
        log.info("state %s %s", st.state.value, st.message.splitlines()[0] if st.message else "")
        if self.on_change:
            try:
                self.on_change(st)
            except Exception:
                log.exception("on_change failed")
        return True

    def is_stale(self, spec: LaunchSpec) -> bool:
        st = self.status
        return st.active and st.spec is not None and st.spec.key == spec.key and \
            st.spec.comparable() != spec.comparable()

    # ---- start ----
    def start(self, spec: LaunchSpec) -> None:
        """Stops whatever this app runs, then starts spec in the background."""
        with self._lock:
            self._gen += 1
            gen = self._gen
        if not spec.ok:
            errs = "\n".join(d.message for d in spec.diagnostics if d.level == Level.ERROR)
            self._stop_proc("replaced")
            self._set(state=State.FAILED, spec=spec, message=errs, pid=None)
            return
        t = threading.Thread(target=self._run, args=(gen, spec), daemon=True, name="llama-server")
        self._worker = t
        t.start()

    def _run(self, gen: int, spec: LaunchSpec) -> None:
        self._stop_proc("replaced")
        if gen != self._gen:
            return
        if port_in_use(spec.host, spec.port):
            self._set(gen, state=State.EXTERNAL, spec=None, pid=None,
                      external_ids=self._external_ids(spec.connect_host, spec.port, spec.env.get("LLAMA_API_KEY", "")),
                      message=f"port {spec.port} is already in use by another program")
            return
        note = rotate(self.server_log)
        if note:
            log.warning(note)
        _clear_dir(self.cwd_dir)
        version = spec.version.label() if spec.version else "unknown"
        rt = f"{spec.runtime.name} ({version}) {spec.runtime.exe}" if spec.runtime else "?"
        header = (f"# llama-launcher server run\n# started: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                  f"# profile: {spec.title}\n# runtime: {rt}\n# idle: {spec.idle_mode} {spec.idle_seconds}s\n"
                  f"# argv: {format_argv(spec.argv)}\n# ---\n")
        self.server_log.write_text(header, encoding="utf-8")
        self._set(gen, state=State.STARTING, spec=spec, message="loading model", log_path=str(self.server_log),
                  external_ids=[], n_ctx=None)
        try:
            with open(self.server_log, "ab") as fh:
                proc = subprocess.Popen(spec.argv, cwd=self.cwd_dir, env=spec.env, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            self._set(gen, state=State.FAILED, message=f"couldn't start llama-server: {e}", pid=None)
            return
        if not job().assign(proc._handle):
            log.warning("couldn't assign pid %s to the kill-on-close job", proc.pid)
        with self._lock:
            if gen != self._gen:
                proc.kill()
                proc.wait()
                return
            self._proc = proc
        self._set(gen, pid=proc.pid)
        self._wait_ready(gen, spec, proc)

    def _wait_ready(self, gen: int, spec: LaunchSpec, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if gen != self._gen:
                return
            if proc.poll() is not None:
                self._finish_failed(gen, proc)
                return
            try:
                if http_json(f"{spec.base_url}/health", timeout=1)[0] == 200:
                    self._set(gen, state=State.RUNNING, message="")
                    self._monitor(gen, spec, proc)
                    return
            except (OSError, ValueError):
                pass
            time.sleep(min(0.5, self.poll))
        if gen == self._gen:
            self._stop_proc("timeout")
            self._set(gen, state=State.FAILED, pid=None,
                      message=f"llama-server didn't become ready within {int(self.ready_timeout)}s")

    def _finish_failed(self, gen: int, proc: subprocess.Popen) -> None:
        code = proc.returncode
        self._footer(f"exited with code {code}")
        with self._lock:
            if self._proc is proc:
                self._proc = None
        failure = errors.classify(code, errors.tail(self.server_log))
        _clear_dir(self.cwd_dir)
        self._set(gen, state=State.FAILED, pid=None, message=failure.message)

    def _monitor(self, gen: int, spec: LaunchSpec, proc: subprocess.Popen) -> None:
        key = spec.env.get("LLAMA_API_KEY", "")
        next_props = 0.0
        while gen == self._gen:
            if proc.poll() is not None:
                if gen == self._gen:
                    self._finish_failed(gen, proc)
                return
            now = time.monotonic()
            if now >= next_props and (spec.idle_mode != IdleMode.OFF or self.status.n_ctx is None):
                next_props = now + self.idle_poll
                try:
                    props = http_json(f"{spec.base_url}/props", key)[1]
                    sleeping = bool(props.get("is_sleeping"))
                    n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
                except (OSError, ValueError, AttributeError):
                    sleeping, n_ctx = None, None
                if isinstance(n_ctx, int) and n_ctx > 0 and not sleeping and n_ctx != self.status.n_ctx:
                    self._set(gen, n_ctx=n_ctx)
                if sleeping is not None and spec.idle_mode != IdleMode.OFF:
                    if sleeping and spec.idle_mode == IdleMode.STOP:
                        self._stop_proc("idle")
                        self._set(gen, state=State.STOPPED, pid=None,
                                  message=f"stopped after {spec.idle_seconds}s idle")
                        return
                    want = State.SLEEPING if sleeping else State.RUNNING
                    if self.status.state != want:
                        self._set(gen, state=want)
            time.sleep(self.poll)

    # ---- stop ----
    def _footer(self, text: str) -> None:
        try:
            with open(self.server_log, "a", encoding="utf-8") as f:
                f.write(f"\n# {text} at {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        except OSError:
            pass

    def _stop_proc(self, reason: str) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            log.info("stopping pid %s (%s)", proc.pid, reason)
            proc.terminate()  # TerminateProcess on the pid this app spawned
            try:
                proc.wait(self.stop_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self._footer(f"stopped ({reason}), exit code {proc.returncode}")
        _clear_dir(self.cwd_dir)

    def stop(self, reason: str = "user") -> None:
        with self._lock:
            self._gen += 1
            worker = self._worker
            st = self._status.state
        if st not in (State.STARTING, State.RUNNING, State.SLEEPING, State.FAILED):
            return
        self._set(state=State.STOPPING, message="")
        self._stop_proc(reason)
        if worker and worker is not threading.current_thread():
            worker.join(self.stop_timeout + 5)
        self._set(state=State.STOPPED, pid=None, message="")

    def shutdown(self) -> None:
        self._watch_stop.set()
        self.stop("quit")

    # ---- external server watch ----
    def _external_ids(self, host: str, port: int, key: str) -> list[str]:
        try:
            data = http_json(f"http://{host}:{port}/v1/models", key)[1]
            return [m.get("id", "?") for m in data.get("data", [])]
        except (OSError, ValueError, AttributeError):
            return []

    def check_port(self, host: str, port: int, key: str = "") -> None:
        """STOPPED <-> EXTERNAL, based on whether something else holds the port."""
        if self.status.state not in (State.STOPPED, State.EXTERNAL):
            return
        connect = connect_host(host)
        if port_in_use(host, port):
            ids = self._external_ids(connect, port, key)
            st = self.status
            if st.state is not State.EXTERNAL or st.external_ids != ids:
                self._set(state=State.EXTERNAL, external_ids=ids,
                          message=f"port {port} is in use by another program")
        elif self.status.state is State.EXTERNAL:
            self._set(state=State.STOPPED, external_ids=[], message="")

    def watch_port(self, target: Callable[[], tuple[str, int, str]], interval: float = 10.0) -> None:
        def loop():
            while not self._watch_stop.wait(interval):
                try:
                    self.check_port(*target())
                except Exception:
                    log.exception("port watch failed")
        try:
            self.check_port(*target())
        except Exception:
            log.exception("port check failed")
        threading.Thread(target=loop, daemon=True, name="port-watch").start()
