"""Two-generation log files (x.log + x.prev.log) and the app logger."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from llama_launcher.paths import replace_file


def rotate(path: Path) -> str | None:
    """Move path to its .prev.log sibling. Returns a note when that wasn't possible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return None
    prev = prev_path(path)
    try:
        replace_file(path, prev)
        return None
    except OSError as e:
        try:
            path.unlink()
        except OSError:
            pass
        return f"couldn't rotate {path.name} to {prev.name}: {e}"


def prev_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.stem + ".prev" + path.suffix)


def setup_app_log(logs_dir: Path) -> Path:
    path = Path(logs_dir) / "app.log"
    root = logging.getLogger("llama_launcher")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    note = rotate(path)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if note:
        root.warning(note)
    return path


class LogTail:
    """Reads what was appended to a log since the last call; starts over when the file is replaced or shrinks."""

    def __init__(self, path: Path, initial_bytes: int = 256 * 1024):
        self.path = Path(path)
        self.initial_bytes = initial_bytes
        self._ino: int | None = None
        self._pos = 0

    def read(self) -> tuple[bool, str]:
        """(reset, text): reset means the caller should clear what it shows before adding text.
        Only whole lines are returned, and at most about initial_bytes of them."""
        try:
            with open(self.path, "rb") as f:
                st = os.fstat(f.fileno())
                reset = st.st_ino != self._ino or st.st_size < self._pos or st.st_size - self._pos > self.initial_bytes
                if reset:
                    self._ino = st.st_ino
                    self._pos = max(0, st.st_size - self.initial_bytes)
                f.seek(self._pos)
                data = f.read(st.st_size - self._pos)
        except OSError:
            return False, ""
        start = data.find(b"\n") + 1 if reset and self._pos else 0  # began mid-line
        end = data.rfind(b"\n") + 1
        if end <= start:
            return reset, ""
        self._pos += end
        return reset, data[start:end].decode("utf-8", "replace").replace("\r\n", "\n")
