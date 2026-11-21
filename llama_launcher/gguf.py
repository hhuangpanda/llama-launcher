"""Header-only GGUF metadata reader (v3). Tensor data is never touched."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"GGUF"

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class GGUFError(Exception):
    pass


@dataclass(frozen=True)
class GGUFInfo:
    path: str
    arch: str
    type: str
    name: str
    embedding_length: int | None
    context_length: int | None
    nextn_layers: int
    projection_dims: tuple[int, ...]


def _read(f, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise GGUFError("truncated file")
    return b


def _string(f) -> str:
    (n,) = struct.unpack("<Q", _read(f, 8))
    if n > 1 << 24:
        raise GGUFError("implausible string length")
    return _read(f, n).decode("utf-8", "replace")


def _value(f, t: int):
    if t in _SCALARS:
        fmt = _SCALARS[t]
        return struct.unpack(fmt, _read(f, struct.calcsize(fmt)))[0]
    if t == _STRING:
        return _string(f)
    if t == _ARRAY:
        et, n = struct.unpack("<IQ", _read(f, 12))
        if et in _SCALARS:
            f.seek(n * struct.calcsize(_SCALARS[et]), io.SEEK_CUR)
        elif et == _STRING:
            for _ in range(n):
                (ln,) = struct.unpack("<Q", _read(f, 8))
                f.seek(ln, io.SEEK_CUR)
        else:
            for _ in range(n):
                _value(f, et)
        return None  # arrays are not needed; only skipped
    raise GGUFError(f"unknown value type {t}")


def read_metadata(path: str | Path) -> dict:
    """Scalar key/values from the header; arrays are skipped (value None)."""
    try:
        with open(path, "rb", buffering=1 << 20) as f:
            if _read(f, 4) != MAGIC:
                raise GGUFError("not a GGUF file")
            (version,) = struct.unpack("<I", _read(f, 4))
            if version != 3:
                raise GGUFError(f"unsupported GGUF version {version}")
            _n_tensors, n_kv = struct.unpack("<QQ", _read(f, 16))
            meta = {}
            for _ in range(n_kv):
                key = _string(f)
                (t,) = struct.unpack("<I", _read(f, 4))
                meta[key] = _value(f, t)
            return meta
    except OSError as e:
        raise GGUFError(str(e)) from e
    except (struct.error, UnicodeDecodeError) as e:
        raise GGUFError(f"corrupt header: {e}") from e


def info_from_metadata(path: str, meta: dict) -> GGUFInfo:
    arch = str(meta.get("general.architecture") or "")
    dims = tuple(
        int(meta[k])
        for k in ("clip.vision.projection_dim", "clip.audio.projection_dim")
        if isinstance(meta.get(k), int)
    )
    return GGUFInfo(
        path=str(path),
        arch=arch,
        type=str(meta.get("general.type") or ("mmproj" if arch == "clip" else "model")),
        name=str(meta.get("general.name") or ""),
        embedding_length=meta.get(f"{arch}.embedding_length"),
        context_length=meta.get(f"{arch}.context_length"),
        nextn_layers=int(meta.get(f"{arch}.nextn_predict_layers") or 0),
        projection_dims=dims,
    )


def read_info(path: str | Path) -> GGUFInfo:
    return info_from_metadata(str(path), read_metadata(path))
