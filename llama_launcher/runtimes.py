"""llama.cpp builds: locating llama-server.exe, reading its version and flag table."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

from llama_launcher.server_flags import HELP, VERSION

EXE_NAME = "llama-server.exe"
_FLAG_RE = re.compile(r"-{1,2}[A-Za-z0-9][\w.-]*")


@dataclass
class HelpTable:
    """Maps every flag spelling to one canonical key; aliases and --no-x share it."""

    canon: dict[str, str] = field(default_factory=dict)
    takes_value: dict[str, bool] = field(default_factory=dict)

    def canonical(self, flag: str) -> str:
        return self.canon.get(flag, flag)

    def knows(self, flag: str) -> bool:
        return flag in self.canon

    def needs_value(self, flag: str) -> bool | None:
        key = self.canon.get(flag)
        return None if key is None else self.takes_value[key]


def parse_help(text: str) -> HelpTable:
    table = HelpTable()
    for line in text.splitlines():
        if not line.startswith("-"):
            continue
        names, pos = [], 0
        while True:
            m = _FLAG_RE.match(line, pos)
            if not m:
                break
            names.append(m.group())
            pos = m.end()
            sep = re.match(r",\s*", line[pos:])
            if not sep:
                break
            pos += sep.end()
        if not names:
            continue
        rest = line[pos:]
        takes_value = bool(re.match(r" \S", rest))
        longs = [n for n in names if n.startswith("--") and not n.startswith("--no-")]
        key = longs[0] if longs else names[0]
        table.takes_value[key] = takes_value
        for n in names:
            table.canon.setdefault(n, key)
        # --x and --no-x: the negation never takes a value
        for n in names:
            if n.startswith("--no-") and ("--" + n[5:]) in names:
                table.canon[n] = key
    return table


@dataclass(frozen=True)
class VersionInfo:
    version: str
    build: int | None
    commit: str

    def label(self) -> str:
        b = f"b{self.build}" if self.build is not None else "unknown build"
        return f"{b} ({self.commit})" if self.commit else b


def parse_version(text: str) -> VersionInfo:
    m = re.search(r"version:\s*(\S+)\s*\(build\s*(\d+),\s*commit\s*([0-9a-f]+)\)", text)
    if m:
        return VersionInfo(m.group(1), int(m.group(2)), m.group(3))
    m = re.search(r"version:\s*(\d+)\s*\(([0-9a-f]+)\)", text)  # older builds: "version: 1234 (abc)"
    if m:
        return VersionInfo(m.group(1), int(m.group(1)), m.group(2))
    return VersionInfo("unknown", None, "")


@dataclass
class Runtime:
    name: str
    path: str  # folder holding llama-server.exe
    command_prefix: list[str] | None = None  # tests: [python, fake_server.py]

    @property
    def exe(self) -> str:
        return os.path.join(self.path, EXE_NAME)

    def command(self) -> list[str]:
        return list(self.command_prefix) if self.command_prefix else [self.exe]

    def check(self) -> str | None:
        """Problem description, or None when usable."""
        if self.command_prefix:
            return None
        if not os.path.isdir(self.path):
            return f"runtime '{self.name}': folder not found: {self.path}"
        if not os.path.isfile(self.exe):
            return f"runtime '{self.name}': {EXE_NAME} not found in {self.path}"
        return None


@dataclass
class RuntimeInfo:
    version: VersionInfo
    help_table: HelpTable


class RuntimeProbe:
    """Runs --version/--help once per executable (re-run when its mtime changes)."""

    def __init__(self):
        self._cache: dict[tuple, RuntimeInfo] = {}
        self.runs = 0

    def _sig(self, rt: Runtime) -> tuple:
        cmd = rt.command()
        target = cmd[-1] if rt.command_prefix else rt.exe
        try:
            mtime = os.stat(target).st_mtime_ns
        except OSError:
            mtime = None
        return (tuple(cmd), mtime)

    def info(self, rt: Runtime) -> RuntimeInfo:
        problem = rt.check()
        if problem:
            raise RuntimeError(problem)
        sig = self._sig(rt)
        if sig not in self._cache:
            self.runs += 1
            self._cache[sig] = RuntimeInfo(parse_version(self._run(rt, VERSION)), parse_help(self._run(rt, HELP)))
        return self._cache[sig]

    @staticmethod
    def _run(rt: Runtime, flag: str) -> str:
        try:
            r = subprocess.run(
                rt.command() + [flag],
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"runtime '{rt.name}': {flag} failed: {e}") from e
        return r.stdout + r.stderr
