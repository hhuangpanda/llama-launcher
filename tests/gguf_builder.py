"""Writes tiny header-only GGUF files for tests."""

import struct
from pathlib import Path


def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def _kv(key: str, value) -> bytes:
    out = _s(key)
    if isinstance(value, bool):
        return out + struct.pack("<I?", 7, value)
    if isinstance(value, int):
        return out + struct.pack("<II", 4, value)
    if isinstance(value, float):
        return out + struct.pack("<If", 6, value)
    if isinstance(value, str):
        return out + struct.pack("<I", 8) + _s(value)
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            return out + struct.pack("<IIQ", 9, 8, len(value)) + b"".join(_s(v) for v in value)
        return out + struct.pack("<IIQ", 9, 5, len(value)) + b"".join(struct.pack("<i", v) for v in value)
    raise TypeError(value)


def write_gguf(path: Path, meta: dict, version: int = 3) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"".join(_kv(k, v) for k, v in meta.items())
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", version, 0, len(meta)) + body)
    return path


def model(path, arch, n_embd, ctx=262144, nextn=0, tokens=50):
    meta = {"general.architecture": arch, "general.type": "model", "general.name": Path(path).stem,
            "tokenizer.ggml.tokens": [f"t{i}" for i in range(tokens)],
            f"{arch}.context_length": ctx, f"{arch}.embedding_length": n_embd}
    if nextn:
        meta[f"{arch}.nextn_predict_layers"] = nextn
    return write_gguf(path, meta)


def mmproj(path, proj_dim, audio_dim=None):
    meta = {"general.architecture": "clip", "general.type": "mmproj", "clip.has_vision_encoder": True,
            "clip.vision.projection_dim": proj_dim}
    if audio_dim:
        meta["clip.audio.projection_dim"] = audio_dim
    return write_gguf(path, meta)


def draft(path, arch, n_embd=1024):
    return write_gguf(path, {"general.architecture": arch, "general.type": "model",
                             f"{arch}.embedding_length": n_embd, f"{arch}.nextn_predict_layers": 4})


def llm_folder(root: Path) -> Path:
    """A flat folder mixing several model families, their mmproj files and an MTP draft."""
    d = Path(root)
    model(d / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "qwen35moe", 2048)
    mmproj(d / "Qwen3.6-35B-A3B-mmproj-F16.gguf", 2048)
    model(d / "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf", "qwen35", 5120)
    model(d / "Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS.gguf", "qwen35", 5120, nextn=1)
    model(d / "Qwen3.8-27B-UD-Q2_K_XL.gguf", "qwen35", 5120, nextn=1)
    mmproj(d / "Qwen3.8-27B-mmproj-blackfrost-ABLITERATED-Q8_0.gguf", 5120)
    model(d / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf", "gemma4", 3840)
    mmproj(d / "gemma-4-12B-it-qat-mmproj-F16.gguf", 3840, audio_dim=3840)
    model(d / "gemma-4-12B-it-qat-q4_0-unquantized-heretic-Q4_0.gguf", "gemma4", 3840)
    draft(d / "mtp-gemma-4-12B-it-Q4_0.gguf", "gemma4-assistant")
    (d / "gemma-4-template.jinja").write_text("x")
    return d
