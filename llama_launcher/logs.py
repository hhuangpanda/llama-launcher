"""Two-generation log files (x.log + x.prev.log) and the app logger."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def rotate(path: Path) -> str | None:
    """Move path to its .prev.log sibling. Returns a note when that wasn't possible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return None
    prev = path.with_name(path.stem + ".prev" + path.suffix)
    try:
        os.replace(path, prev)
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
