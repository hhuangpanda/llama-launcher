"""Turns a failed llama-server run (exit code + log tail) into one readable line."""

from __future__ import annotations

import re
from dataclasses import dataclass

ACCESS_VIOLATION = 0xC0000005
CRASH_CODES = {ACCESS_VIOLATION: "access violation", 0xC0000409: "fast fail / stack buffer overrun"}
_PREFIX = re.compile(r"^\d+\.\d+\.\d+\.\d+ [IWEDT] (?:\w{2,5} {2,})?")  # time, level, padded module


@dataclass
class Failure:
    kind: str
    message: str


def strip(line: str) -> str:
    return _PREFIX.sub("", line.rstrip())


def _error_lines(lines: list[str]) -> list[str]:
    return [strip(line) for line in lines if re.match(r"^\d+\.\d+\.\d+\.\d+ E ", line) or line.startswith("error")]


_RULES = [
    ("unknown_argument", re.compile(r"(?:invalid|unknown) argument: (\S+)"),
     lambda m: f"llama-server rejected the argument {m.group(1)}"),
    ("file_not_found", re.compile(r"failed to open GGUF file '([^']+)'"),
     lambda m: f"file not found: {m.group(1)}"),
    ("mmproj_mismatch", re.compile(r"mismatch between text model \(n_embd = (\d+)\) and mmproj \(n_embd = (\d+)\)"),
     lambda m: f"the mmproj doesn't fit this model (model {m.group(1)}, mmproj {m.group(2)})"),
    ("mtp_unavailable", re.compile(r"failed to create MTP context"),
     lambda m: "MTP speculative decoding failed: the model has no MTP head and no matching draft was given"),
    ("out_of_memory", re.compile(r"cudaMalloc failed|out of memory|failed to allocate|unable to allocate", re.I),
     lambda m: "ran out of GPU memory: lower the context size or use a smaller quant"),
    ("port_in_use", re.compile(r"couldn't bind|address already in use|failed to bind", re.I),
     lambda m: "the port is already in use"),
    ("model_load_failed", re.compile(r"failed to load (?:multimodal )?model|error loading model", re.I),
     lambda m: "the model failed to load"),
]


def classify(exit_code: int | None, lines: list[str]) -> Failure:
    text = "\n".join(lines)
    for kind, rx, fmt in _RULES:
        m = rx.search(text)
        if m:
            return Failure(kind, fmt(m))
    code = (exit_code or 0) & 0xFFFFFFFF
    errs = _error_lines(lines)
    last = errs[-1] if errs else ""
    if code in CRASH_CODES:
        msg = f"llama-server crashed ({CRASH_CODES[code]}, 0x{code:08X})"
        return Failure("crash", msg + (f": {last}" if last else ""))
    tail = errs[-5:] if errs else [strip(line) for line in lines if line.strip()][-5:]
    hexcode = f" (0x{code:08X})" if code > 255 else ""
    return Failure("other", f"llama-server exited with code {exit_code}{hexcode}" + ("\n" + "\n".join(tail) if tail else ""))


def tail(path, n: int = 200) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []
