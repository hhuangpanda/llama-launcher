import os
import subprocess
from pathlib import Path

import gguf_builder as gb
import pytest

from llama_launcher import catalog as c
from llama_launcher.gguf import GGUFError, read_info, read_metadata


def by_stem(entries):
    return {e.stem: e for e in entries}


# G1
def test_reads_model_mmproj_draft(tmp_path):
    m = read_info(gb.model(tmp_path / "m.gguf", "qwen35", 5120, ctx=1000, nextn=1, tokens=5000))
    assert (m.arch, m.type, m.embedding_length, m.context_length, m.nextn_layers) == ("qwen35", "model", 5120, 1000, 1)
    p = read_info(gb.mmproj(tmp_path / "p.gguf", 3840, audio_dim=1024))
    assert (p.type, p.projection_dims) == ("mmproj", (3840, 1024))
    d = read_info(gb.draft(tmp_path / "d.gguf", "gemma4-assistant"))
    assert d.arch == "gemma4-assistant" and d.nextn_layers == 4


# G2
@pytest.mark.parametrize(
    "content,msg",
    [
        (b"", "truncated"),
        (b"NOPE" + b"\0" * 20, "not a GGUF"),
        (b"GGUF\x03\0\0\0" + b"\0" * 8 + b"\x05" + b"\0" * 7 + b"\x10", "truncated"),
    ],
)
def test_bad_files_raise(tmp_path, content, msg):
    f = tmp_path / "bad.gguf"
    f.write_bytes(content)
    with pytest.raises(GGUFError, match=msg):
        read_metadata(f)


def test_v2_rejected(tmp_path):
    with pytest.raises(GGUFError, match="version 2"):
        read_metadata(gb.write_gguf(tmp_path / "v2.gguf", {"a": 1}, version=2))


# G3
def test_values_after_arrays_are_read(tmp_path):
    f = gb.write_gguf(
        tmp_path / "x.gguf", {"s": ["a", "bb"], "n": [1, 2, 3], "f": 1.5, "b": True, "general.architecture": "late"}
    )
    meta = read_metadata(f)
    assert meta["general.architecture"] == "late" and meta["f"] == 1.5 and meta["b"] is True


# C1
def test_flat_llm_folder_pairs_like_the_real_one(tmp_path):
    entries = c.scan([str(gb.llm_folder(tmp_path))])
    models = by_stem(c.models(entries))
    expect = {
        "Qwen3.6-35B-A3B-UD-Q4_K_M": ("Qwen3.6-35B-A3B-mmproj-F16", None, False),
        "Qwen3.8-27B-GSQ-RCO-IQ3_XXS": ("Qwen3.8-27B-mmproj-blackfrost-ABLITERATED-Q8_0", None, False),
        "Qwen3.8-27B-Swift-1.5-ajgazin-Orca-MTP-RCO-IQ3_XXS": (
            "Qwen3.8-27B-mmproj-blackfrost-ABLITERATED-Q8_0",
            None,
            True,
        ),
        "Qwen3.8-27B-UD-Q2_K_XL": ("Qwen3.8-27B-mmproj-blackfrost-ABLITERATED-Q8_0", None, True),
        "gemma-4-12B-it-qat-UD-Q4_K_XL": ("gemma-4-12B-it-qat-mmproj-F16", "mtp-gemma-4-12B-it-Q4_0", False),
        "gemma-4-12B-it-qat-q4_0-unquantized-heretic-Q4_0": (
            "gemma-4-12B-it-qat-mmproj-F16",
            "mtp-gemma-4-12B-it-Q4_0",
            False,
        ),
    }
    assert set(models) == set(expect)
    for name, (mm, dr, mtp) in expect.items():
        s = c.suggest(models[name], entries)
        assert Path(s.mmproj).stem == mm
        assert (Path(s.draft).stem if s.draft else None) == dr
        assert s.builtin_mtp is mtp


# C2
def test_ambiguous_mmproj_not_attached(tmp_path):
    gb.model(tmp_path / "a.gguf", "qwen35", 5120)
    gb.mmproj(tmp_path / "one-mmproj.gguf", 5120)
    gb.mmproj(tmp_path / "two-mmproj.gguf", 5120)
    entries = c.scan([str(tmp_path)])
    s = c.suggest(c.models(entries)[0], entries)
    assert s.mmproj is None and len(s.mmproj_candidates) == 2


# C3
def test_no_matching_mmproj(tmp_path):
    gb.model(tmp_path / "a.gguf", "qwen35", 5120)
    gb.mmproj(tmp_path / "other-mmproj.gguf", 2048)
    entries = c.scan([str(tmp_path)])
    s = c.suggest(c.models(entries)[0], entries)
    assert s.mmproj is None and s.mmproj_candidates == []


# C4
def test_wrong_arch_draft_not_suggested(tmp_path):
    gb.model(tmp_path / "q.gguf", "qwen35", 5120)
    gb.draft(tmp_path / "mtp-gemma.gguf", "gemma4-assistant")
    entries = c.scan([str(tmp_path)])
    assert c.suggest(c.models(entries)[0], entries).draft_candidates == []


# C5
def test_split_model_listed_once(tmp_path):
    for i in (1, 2, 3):
        gb.model(tmp_path / f"big-0000{i}-of-00003.gguf", "qwen35", 5120)
    entries = c.scan([str(tmp_path)])
    assert len(entries) == 1 and entries[0].shard_count == 3 and entries[0].stem == "big"


# C6
def test_nested_unreadable_and_junction_loop(tmp_path):
    gb.model(tmp_path / "a" / "b" / "deep.gguf", "x", 10)
    (tmp_path / "junk.gguf").write_bytes(b"nope")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "a" / "loop"), str(tmp_path)], capture_output=True, check=True
    )
    entries = c.scan([str(tmp_path)])
    kinds = {e.stem: e.kind for e in entries}
    assert kinds == {"deep": c.Kind.MODEL, "junk": c.Kind.UNREADABLE}


# C7
def test_overlapping_folders_dedupe(tmp_path):
    gb.model(tmp_path / "sub" / "a.gguf", "x", 10)
    assert len(c.scan([str(tmp_path), str(tmp_path / "sub")])) == 1


# C8
def test_hardlink_layout_pairs_within_folder(tmp_path):
    src = gb.llm_folder(tmp_path / "LLM")
    dst = tmp_path / "LlamaApp" / "gemma"
    dst.mkdir(parents=True)
    for n in ("gemma-4-12B-it-qat-UD-Q4_K_XL.gguf", "gemma-4-12B-it-qat-mmproj-F16.gguf"):
        os.link(src / n, dst / n)
    entries = c.scan([str(tmp_path / "LLM"), str(tmp_path / "LlamaApp")])
    linked = [e for e in c.models(entries) if "LlamaApp" in e.path][0]
    s = c.suggest(linked, entries)
    assert "LlamaApp" in s.mmproj and len(s.mmproj_candidates) == 2
    assert s.draft is None and s.draft_candidates  # draft lives only in LLM: offered, not auto-attached


def test_info_cache_persists_and_invalidates(tmp_path):
    f = gb.model(tmp_path / "a.gguf", "x", 10)
    cache_file = tmp_path / "cache.json"
    cache = c.InfoCache(cache_file)
    assert cache.get(str(f)).embedding_length == 10
    cache.save()
    assert c.InfoCache(cache_file).get(str(f)).embedding_length == 10
    gb.model(f, "x", 20)
    os.utime(f, ns=(1, 1))
    assert c.InfoCache(cache_file).get(str(f)).embedding_length == 20
