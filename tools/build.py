"""Builds dist/llama-launcher/llama-launcher.exe (PyInstaller, one folder)."""

import ctypes
import msvcrt
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "llama-launcher"

OPTIONS = {
    "--name": "llama-launcher",
    "--paths": str(ROOT),
    "--distpath": str(ROOT / "dist"),
    "--workpath": str(ROOT / "build"),
    "--specpath": str(ROOT / "build"),
}

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
QUIT_WAIT_SECONDS = 30  # quitting stops the model first, which can take up to llama-server's stop timeout


def running_from(folder: Path) -> list[tuple[int, str]]:
    """(pid, exe path) of every process whose executable is inside folder."""
    psapi = ctypes.WinDLL("psapi")
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, wintypes.PDWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)

    pids = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    prefix = os.path.normcase(str(folder)) + os.sep
    found = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:  # gone already, or a protected system process
            continue
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            named = k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if named and os.path.normcase(buf.value).startswith(prefix):
                found.append((pid, buf.value))
        finally:
            k32.CloseHandle(handle)
    return found


def interactive() -> bool:
    """True when a person can answer: stdin is a real console. isatty() is also true for NUL (`< /dev/null`)."""
    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError):  # no stdin at all (pythonw), or it was closed
        return False
    mode = wintypes.DWORD()
    return bool(ctypes.windll.kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))


def quit_if_confirmed() -> bool:
    """Asks to quit the running app; True once it has exited. Never asks, so never quits, without a console."""
    if not interactive():
        return False
    try:
        answer = input("Quit it now? This also stops any model it is serving. [y/N] ")
    except EOFError:
        return False
    if answer.strip().lower() not in ("y", "yes"):
        return False
    subprocess.run([sys.executable, "-m", "llama_launcher", "--quit"], cwd=ROOT, check=False)
    deadline = time.monotonic() + QUIT_WAIT_SECONDS
    while running_from(DIST):
        if time.monotonic() > deadline:
            print(
                f"It didn't quit within {QUIT_WAIT_SECONDS}s (Settings may be asking about unsaved changes).",
                file=sys.stderr,
            )
            return False
        time.sleep(0.5)
    return True


def main() -> int:
    running = running_from(DIST)
    if running:
        pids = ", ".join(str(pid) for pid, _ in running)
        print(f"llama-launcher is running from {DIST} (pid {pids}), so its files can't be replaced.", file=sys.stderr)
        if not quit_if_confirmed():
            print("Quit it from the tray menu, then build again.", file=sys.stderr)
            return 1
    argv = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir"]
    for flag, value in OPTIONS.items():
        argv += [flag, value]
    argv.append(str(ROOT / "tools" / "entry.py"))
    subprocess.run(argv, check=True)
    print(DIST / "llama-launcher.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
