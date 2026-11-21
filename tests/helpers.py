import ctypes
import os
import socket
import sys
import time
from pathlib import Path

from llama_launcher import launch
from llama_launcher.config import GlobalOptions, ModelEntry, Profile, Settings
from llama_launcher.runtimes import Runtime, RuntimeProbe

FIXTURES = Path(__file__).parent / "fixtures"
PYTHON = getattr(sys, "_base_executable", sys.executable)  # not the venv redirector: one process per spawn
FAKE = [PYTHON, "-I", str(FIXTURES / "fake_server.py")]
_probe = RuntimeProbe()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_runtime() -> Runtime:
    return Runtime("fake", str(FIXTURES), command_prefix=FAKE)


def spec(tmp_path, port=None, profile=None, settings=None, env=None, model_name="m.gguf", **kw):
    model = tmp_path / model_name
    model.write_bytes(b"GGUF")
    rt = fake_runtime()
    info = _probe.info(rt)
    s = settings or Settings(global_=GlobalOptions(idle_mode="off"))
    s.port = port or free_port()
    base_env = {"SYSTEMROOT": os.environ["SYSTEMROOT"], **(env or {})}
    return launch.build(s, ModelEntry(str(model)), profile or Profile("p"), rt, info.help_table, info.version,
                   base_env=base_env, **kw)


def pid_alive(pid: int) -> bool:
    k32 = ctypes.WinDLL("kernel32")
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def wait_for(pred, timeout=15.0, interval=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return False
