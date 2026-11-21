import json

import pytest

from llama_launcher import cmdimport as ci
from llama_launcher.config import Config, GlobalOptions, ModelEntry, Profile, RuntimeEntry, Settings

REL = r"C:\llama\llama-server.exe"
LOCAL = r"C:\src\llama.cpp\build-cuda\bin\llama-server.exe"
M = r"C:\models"
BASE = ["-t", "12", "--load-mode", "none", "--fit", "on", "--flash-attn", "on",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--host", "127.0.0.1", "--port", "9931"]

ENTRIES = [
    {"name": "gemma-qat", "command": [REL, "-m", rf"{M}\gemma-qat.gguf", "--ctx-size", "131072", *BASE,
                                      "--mmproj", rf"{M}\gemma-mmproj.gguf", "-md", rf"{M}\mtp-gemma.gguf",
                                      "--spec-type", "draft-mtp", "--temp", "1.0", "--alias", "gemma-qat"]},
    {"name": "qwen-general", "command": [REL, "-m", rf"{M}\qwen.gguf", "--ctx-size", "196608", *BASE,
                                         "--chat-template-kwargs", '{"preserve_thinking":true}', "--temp", "1.0"]},
    {"name": "qwen-kvq4", "command": [LOCAL, "-m", rf"{M}\qwen.gguf", "--ctx-size", "102400", *BASE[:-6],
                                      "--cache-type-v", "q4_0", "--host", "127.0.0.1", "--port", "9931",
                                      "--alias", "qwen-kvq4", "--temp", "0.6"]},
]


@pytest.fixture
def run(release_help):
    def _run(entries=ENTRIES, settings=None, models=None, replace=False):
        return ci.plan(ci.load_entries(json.dumps(entries)), settings or Settings(), models or [],
                       lambda exe: release_help, replace)
    return _run


def by_name(p):
    return {r.name: r for r in p.results}


def test_new_import(run):
    p = run()
    r = by_name(p)
    assert [x.status for x in p.results] == ["new"] * 3
    assert all(x.exact for x in p.results), [(x.name, x.exact) for x in p.results]
    s = p.settings
    assert [(x.name, x.path) for x in s.runtimes] == [("llama", r"C:\llama"), ("build-cuda", r"C:\src\llama.cpp\build-cuda\bin")]
    assert s.default_runtime == "llama" and s.model_folders == [M]
    assert s.glob.args == "-t 12 --load-mode none --fit on --flash-attn on" and s.glob.cache_type_k == "q8_0"
    assert s.glob.cache_type_v == ""  # differs between commands -> stays per profile
    gemma = next(m for m in p.models if m.file.endswith("gemma-qat.gguf"))
    assert gemma.mmproj.endswith("gemma-mmproj.gguf") and gemma.draft.endswith("mtp-gemma.gguf")
    assert gemma.profiles[0].alias == "" and gemma.profiles[0].ctx_size == 131072
    q4 = next(m for m in p.models if m.file.endswith("qwen.gguf")).profile("qwen-kvq4")
    assert (q4.runtime, q4.cache_type_v, q4.alias) == ("build-cuda", "q4_0", "qwen-kvq4")
    assert "--host" not in q4.args and p.notes == ["ignored --host, --port in the commands: llama-launcher sets these itself"]
    assert "'{\"preserve_thinking\":true}'" in r["qwen-general"].profile.args


def test_rerun_is_unchanged_and_adds_nothing(run):
    first = run()
    again = run(settings=first.settings, models=first.models)
    assert {x.status for x in again.results} == {"unchanged"}
    assert not again.has_changes and again.setting_changes == []
    assert len(again.settings.runtimes) == 2 and sum(len(m.profiles) for m in again.models) == 3


def test_conflict_skipped_unless_replace(run):
    first = run()
    edited = json.loads(json.dumps(ENTRIES))
    edited[1]["command"][edited[1]["command"].index("196608")] = "65536"
    p = run(edited, first.settings, first.models)
    assert by_name(p)["qwen-general"].status == "conflict"
    assert next(m for m in p.models if m.file.endswith("qwen.gguf")).profile("qwen-general").ctx_size == 196608
    p = run(edited, first.settings, first.models, replace=True)
    assert by_name(p)["qwen-general"].status == "replaced"
    assert next(m for m in p.models if m.file.endswith("qwen.gguf")).profile("qwen-general").ctx_size == 65536


def test_existing_runtime_and_folder_matched_by_path(run):
    s = Settings(runtimes=[RuntimeEntry("release", r"C:\LLAMA")], default_runtime="release", model_folders=["C:\\"])
    p = run(settings=s)
    assert [x.name for x in p.settings.runtimes] == ["release", "build-cuda"] and p.settings.model_folders == ["C:\\"]


def test_mismatched_mmproj_kept_in_args(run):
    existing = [ModelEntry(rf"{M}\gemma-qat.gguf", mmproj=rf"{M}\other-mmproj.gguf", profiles=[Profile("x")])]
    r = by_name(run(models=existing))["gemma-qat"]
    assert "--mmproj" in r.profile.args and "kept in profile args" in r.detail and r.exact


@pytest.mark.parametrize("entries,msg", [
    ([{"name": "a", "command": [REL, "--temp", "1"]}], "no -m/--model"),
    ([{"name": "a", "command": [REL, "-m", "x.gguf"]}, {"name": "a", "command": [REL, "-m", "X.gguf"]}], "twice"),
])
def test_entry_errors(run, entries, msg):
    p = run(entries)
    assert p.results[-1].status == "error" and msg in p.results[-1].detail


def test_string_command_and_bad_json():
    e = ci.load_entries(json.dumps([{"name": "a", "command": r'C:\llama\llama-server.exe -m "C:\my models\a.gguf"'}]))
    assert e[0].argv == [REL, "-m", r"C:\my models\a.gguf"]
    with pytest.raises(ci.ImportError_, match="needs"):
        ci.load_entries('[{"name": "a"}]')
    with pytest.raises(ci.ImportError_, match="not valid JSON"):
        ci.load_entries("{")


def test_cli_dry_run_writes_nothing_then_write(tmp_path, monkeypatch, capsys, release_help):
    monkeypatch.setattr(ci.RuntimeProbe, "info", lambda self, rt: type("I", (), {"help": release_help})())
    f = tmp_path / "in.json"
    f.write_text(json.dumps(ENTRIES))
    home = tmp_path / "home"
    assert ci.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "+ new" in out and "dry run: nothing written" in out
    assert "gemma-qat" not in (home / "profiles.toml").read_text()
    assert ci.main([str(f), "--write"]) == 0
    cfg = Config(home / "settings.toml", home / "profiles.toml")
    cfg.load()
    assert sum(len(m.profiles) for m in cfg.models) == 3
    before = (home / "profiles.toml").read_text()
    assert ci.main([str(f), "--write"]) == 0
    assert "nothing to write" in capsys.readouterr().out and (home / "profiles.toml").read_text() == before


def test_global_flag_missing_from_command_is_unset(run):
    p = run(settings=Settings(glob=GlobalOptions(args="--fit on --mlock")))
    r = by_name(p)["qwen-general"]
    assert r.profile.unset == ["--mlock"] and "--fit" not in r.profile.args and r.exact


def test_bom_from_powershell_pipe_accepted():
    e = ci.load_entries("﻿" + json.dumps([{"name": "a", "command": [REL, "-m", "x.gguf"]}]))
    assert e[0].name == "a"
