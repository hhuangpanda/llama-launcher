"""Model folder scanning, file classification and sidecar suggestions."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from llama_launcher.gguf import GGUFError, GGUFInfo, info_from_metadata, read_metadata
from llama_launcher.paths import norm

_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
_DRAFT_PREFIXES = ("mtp-", "dspark-", "dflash-")


class Kind(Enum):
    MODEL = "model"
    MMPROJ = "mmproj"
    DRAFT = "draft"
    UNREADABLE = "unreadable"


@dataclass
class FileEntry:
    path: str
    kind: Kind
    info: GGUFInfo | None = None
    error: str = ""
    shard_count: int = 1

    @property
    def directory(self) -> str:
        return os.path.normcase(os.path.dirname(self.path))

    @property
    def stem(self) -> str:
        name = os.path.basename(self.path)
        m = _SHARD_RE.search(name)
        return name[: m.start()] if m else name[: -len(".gguf")]


@dataclass
class Suggestion:
    mmproj: str | None
    draft: str | None
    builtin_mtp: bool
    mmproj_candidates: list[str] = field(default_factory=list)
    draft_candidates: list[str] = field(default_factory=list)


def classify(info: GGUFInfo, filename: str) -> Kind:
    if info.type == "mmproj" or info.arch == "clip":
        return Kind.MMPROJ
    if info.arch.endswith("-assistant") or filename.lower().startswith(_DRAFT_PREFIXES):
        return Kind.DRAFT
    return Kind.MODEL


class InfoCache:
    """GGUF info keyed by path + size + mtime, optionally persisted as JSON."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._data: dict[str, dict] = {}
        self._dirty = False
        if path and path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, path: str) -> GGUFInfo:
        st = os.stat(path)
        key = norm(path)
        sig = [st.st_size, st.st_mtime_ns]
        hit = self._data.get(key)
        if hit and hit["sig"] == sig:
            return GGUFInfo(**{**hit["info"], "path": path, "projection_dims": tuple(hit["info"]["projection_dims"])})
        info = info_from_metadata(path, read_metadata(path))
        self._data[key] = {"sig": sig, "info": {**info.__dict__, "projection_dims": list(info.projection_dims)}}
        self._dirty = True
        return info

    def save(self) -> None:
        if self.path and self._dirty:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data), encoding="utf-8")
                self._dirty = False
            except OSError:
                pass


def _walk(root: str):
    """Yield .gguf file paths under root; directory reparse points (junctions, links) are not followed."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in sorted(entries, key=lambda e: e.name.lower()):
            try:
                if e.is_dir(follow_symlinks=False):
                    attrs = e.stat(follow_symlinks=False).st_file_attributes
                    if not attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        stack.append(e.path)
                elif e.name.lower().endswith(".gguf") and e.is_file():
                    yield e.path
            except OSError:
                continue


def scan(folders: list[str], cache: InfoCache | None = None) -> list[FileEntry]:
    cache = cache or InfoCache()
    seen: set[str] = set()
    out: list[FileEntry] = []
    for root in folders:
        if not root or not os.path.isdir(root):
            continue
        for path in _walk(root):
            key = norm(path)
            if key in seen:
                continue
            seen.add(key)
            m = _SHARD_RE.search(os.path.basename(path))
            if m and m.group(1) != "00001":
                continue
            try:
                info = cache.get(path)
            except (GGUFError, OSError) as e:
                out.append(FileEntry(path, Kind.UNREADABLE, error=str(e)))
                continue
            entry = FileEntry(path, classify(info, os.path.basename(path)), info)
            if m:
                entry.shard_count = int(m.group(2))
            out.append(entry)
    cache.save()
    out.sort(key=lambda e: e.path.lower())
    return out


def mmproj_fits(model: GGUFInfo, mmproj: GGUFInfo) -> bool:
    return model.embedding_length is not None and model.embedding_length in mmproj.projection_dims


def draft_fits(model: GGUFInfo, draft: GGUFInfo) -> bool:
    return draft.arch in (f"{model.arch}-assistant", model.arch)


def _pick(model: FileEntry, candidates: list[FileEntry]) -> str | None:
    """Auto-attach only when exactly one fitting file sits in the model's own folder."""
    local = [c for c in candidates if c.directory == model.directory]
    return local[0].path if len(local) == 1 else None


def _rank(model: FileEntry, candidates: list[FileEntry]) -> list[str]:
    return [c.path for c in sorted(candidates, key=lambda c: (c.directory != model.directory, c.path.lower()))]


def suggest(model: FileEntry, entries: list[FileEntry]) -> Suggestion:
    assert model.info is not None
    mm = [e for e in entries if e.kind is Kind.MMPROJ and mmproj_fits(model.info, e.info)]
    dr = [e for e in entries if e.kind is Kind.DRAFT and e.path != model.path and draft_fits(model.info, e.info)]
    return Suggestion(
        mmproj=_pick(model, mm),
        draft=_pick(model, dr),
        builtin_mtp=model.info.nextn_layers > 0,
        mmproj_candidates=_rank(model, mm),
        draft_candidates=_rank(model, dr),
    )


def models(entries: list[FileEntry]) -> list[FileEntry]:
    return [e for e in entries if e.kind is Kind.MODEL]
