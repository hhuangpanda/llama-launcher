import time

import pytest

from llama_launcher.config import Config, ConfigError, ModelEntry, Profile, RuntimeEntry


@pytest.fixture
def cfg(tmp_path):
    c = Config(tmp_path / "settings.toml", tmp_path / "profiles.toml")
    c.load()
    return c


def test_defaults_created(cfg):
    assert cfg.settings.port == 9931 and cfg.settings.global_.idle_mode == "sleep"
    assert cfg.settings_path.exists() and cfg.profiles_path.exists()


# F1
def test_edit_preserves_comments_and_other_content(cfg):
    cfg.models.append(ModelEntry(r"C:\m\a.gguf", profiles=[Profile("x", ctx_size=4096), Profile("y", args="--temp 1")]))
    cfg.save_profiles()
    text = cfg.profiles_path.read_text().replace("ctx_size = 4096", "ctx_size = 4096  # measured")
    text = "# my header\n" + text
    cfg.profiles_path.write_text(text)
    cfg.load()
    cfg.models[0].profile("y").args = "--temp 0.5"
    cfg.save_profiles()
    out = cfg.profiles_path.read_text()
    assert "# my header" in out and "ctx_size = 4096  # measured" in out and "--temp 0.5" in out
    assert "file = 'C:\\m\\a.gguf'" in out  # literal string, no escaping


# F2
def test_ctx_written_once_per_profile(cfg):
    cfg.models.append(ModelEntry(r"C:\m\a.gguf", profiles=[Profile("a"), Profile("b")]))
    cfg.save_profiles()
    for value in (8192, 16384):  # editor save, then quick picker "save as default"
        cfg.load()
        cfg.models[0].profile("a").ctx_size = value
        cfg.save_profiles()
    out = cfg.profiles_path.read_text()
    assert out.count("ctx_size") == 1 and "ctx_size = 16384" in out
    assert out.count("\n[[models]]") == 1
    cfg.load()
    assert [p.name for p in cfg.models[0].profiles] == ["a", "b"]


def test_repeated_saves_are_stable(cfg):
    cfg.models.append(ModelEntry(r"C:\m\a.gguf", profiles=[Profile("a"), Profile("b")]))
    cfg.save_profiles()
    sizes = set()
    for i in range(4):
        cfg.load()
        cfg.models[0].profiles[0].ctx_size = 1000 + i
        cfg.save_profiles()
        sizes.add(len(cfg.profiles_path.read_text()))
    assert len(sizes) == 1


def test_default_values_are_dropped(cfg):
    cfg.models.append(ModelEntry(r"C:\m\a.gguf", profiles=[Profile("a", ctx_size=100)]))
    cfg.save_profiles()
    cfg.models[0].profiles[0].ctx_size = 0
    cfg.save_profiles()
    assert "ctx_size" not in cfg.profiles_path.read_text()


# F3: invalid TOML, or a bad value in settings.toml (it affects every profile), keeps the last good config
@pytest.mark.parametrize(
    "path,text,msg",
    [
        ("settings_path", "port = ", "settings.toml"),
        ("profiles_path", "port = ", "profiles.toml"),
        ("settings_path", "port = 'x'", "must be int"),
        ("settings_path", "port = 70000", "1-65535"),
        ("settings_path", "[global]\nidle_mode = 'nap'", "idle_mode"),
        ("settings_path", "[global]\ncache_type_k = 'q3'", "cache_type_k"),
    ],
)
def test_bad_file_or_setting_keeps_last_good(cfg, path, text, msg):
    cfg.settings.port = 1234
    cfg.save_settings()
    cfg.load()
    getattr(cfg, path).write_text(text)
    with pytest.raises(ConfigError, match=msg):
        cfg.load()
    assert cfg.settings.port == 1234


# F7: an unknown key or broken entry is reported and skipped; the rest loads
@pytest.mark.parametrize(
    "text,msg,check",
    [
        ("bogus = 1\nport = 1234", "unknown key 'bogus'", lambda s: s.port == 1234),
        (
            "[[runtimes]]\nname = 'a'\n[[runtimes]]\nname = 'b'\npath = 'p'",
            "needs 'name' and 'path'",
            lambda s: [r.name for r in s.runtimes] == ["b"],
        ),
    ],
)
def test_bad_settings_entries_skipped(cfg, text, msg, check):
    cfg.settings_path.write_text(text)
    cfg.load()
    assert any(msg in p for p in cfg.problems) and check(cfg.settings)


# F8: a bad value in a model or profile blocks only that entry, and saving keeps the value
def test_bad_profile_value_blocks_that_profile_only(cfg):
    cfg.profiles_path.write_text(
        "[[models]]\nfile = 'a'\n"
        "[[models.profiles]]\nname = 'bad'\nctx_size = 'big'\nidle_mode = 'nap'\n"
        "[[models.profiles]]\nname = 'ok'\nctx_size = 4096\n"
        "[[models]]\nfile = 'b'\ndraft = 5\n[[models.profiles]]\nname = 'p'\n"
    )
    cfg.load()
    a, b = cfg.models
    bad, ok = a.profiles
    assert set(bad.invalid) == {"ctx_size", "idle_mode"} and bad.ctx_size == 0 and bad.idle_mode == ""
    assert not ok.invalid and ok.ctx_size == 4096
    assert set(b.invalid) == {"draft"} and len(cfg.problems) == 3
    assert all("can't launch until it's fixed" in p for p in cfg.problems)
    ok.ctx_size = 8192
    b.mmproj = "m"
    cfg.models[0].profiles.append(Profile("new"))  # changes the shape: tables are rebuilt
    cfg.save_profiles()
    text = cfg.profiles_path.read_text()
    assert "ctx_size = 'big'" in text and "idle_mode = 'nap'" in text and "draft = 5" in text
    assert "ctx_size = 8192" in text and "mmproj = " in text


def test_model_level_draft_moves_to_its_profiles(cfg):
    cfg.profiles_path.write_text(
        "[[models]]\nfile = 'a'\ndraft = 'd.gguf'\n"
        "[[models.profiles]]\nname = 'p'\n"
        "[[models.profiles]]\nname = 'q'\ndraft = 'other.gguf'\n"
    )
    cfg.load()
    m = cfg.models[0]
    assert [p.draft for p in m.profiles] == ["d.gguf", "other.gguf"] and not m.draft and not cfg.problems
    cfg.save_profiles()
    text = cfg.profiles_path.read_text()
    assert text.count("draft = ") == 2 and "d.gguf" in text.split("[[models.profiles]]")[1]
    cfg.load()
    assert [p.draft for p in cfg.models[0].profiles] == ["d.gguf", "other.gguf"]


def test_editing_an_invalid_value_replaces_it(cfg):
    cfg.profiles_path.write_text("[[models]]\nfile = 'a'\n[[models.profiles]]\nname = 'p'\nctx_size = 'big'\n")
    cfg.load()
    p = cfg.models[0].profiles[0]
    del p.invalid["ctx_size"]  # what the editor does when the field is set
    p.ctx_size = 2048
    cfg.save_profiles()
    cfg.load()
    assert cfg.models[0].profiles[0].ctx_size == 2048 and not cfg.problems


@pytest.mark.parametrize(
    "text,msg,names",
    [
        ("[[models]]\nmmproj = 'x'\n[[models]]\nfile = 'b'", "'file' is required", ["b"]),
        ("[[models]]\nfile = 'a'\n[[models]]\nfile = 'A'", "listed twice", ["a"]),
        ("[[models]]\nfile = 'a'\nfuture_key = 1", "unknown key 'future_key'", ["a"]),
        ("top = 1\n[[models]]\nfile = 'a'", "unknown key 'top'", ["a"]),
    ],
)
def test_bad_profile_entries_skipped(cfg, text, msg, names):
    cfg.profiles_path.write_text(text)
    cfg.load()
    assert any(msg in p for p in cfg.problems) and [m.file for m in cfg.models] == names


def test_bad_profile_skipped_and_kept_on_save(cfg):
    cfg.profiles_path.write_text(
        "[[models]]\nfile = 'a'\nfuture_key = 1\n"
        "[[models.profiles]]\nname = 'p'\n"
        "[[models.profiles]]\nname = 'p'\nargs = '-dup'\n"
        "[[models]]\nfile = 'A'\n"
    )
    cfg.load()
    assert [p.name for p in cfg.models[0].profiles] == ["p"] and len(cfg.problems) == 3
    cfg.models[0].profiles.append(Profile("new"))  # changes the shape: tables are rebuilt
    cfg.save_profiles()
    text = cfg.profiles_path.read_text()
    assert "future_key = 1" in text and "-dup" in text and "file = 'A'" in text and 'name = "new"' in text
    cfg.load()
    assert [p.name for p in cfg.models[0].profiles] == ["p", "new"]


def test_skipped_runtime_kept_on_save(cfg):
    cfg.settings_path.write_text("[[runtimes]]\nname = 'a'\n[[runtimes]]\nname = 'b'\npath = 'p'\n")
    cfg.load()
    cfg.settings.runtimes.append(RuntimeEntry("c", "q"))
    cfg.save_settings()
    cfg.load()
    assert [r.name for r in cfg.settings.runtimes] == ["b", "c"] and len(cfg.problems) == 1


# F4 (watch side; debounce is covered in UI tests)
def test_changed_on_disk(cfg):
    assert not cfg.changed_on_disk()
    cfg.save_settings()
    assert not cfg.changed_on_disk()  # own writes don't count
    time.sleep(0.01)
    cfg.settings_path.write_text(cfg.settings_path.read_text() + "\n# hand edit\n")
    assert cfg.changed_on_disk()


# F5 is in test_launch (missing files)


# F6
def test_api_key_plain_text(cfg):
    cfg.settings.api_key = "dummy"
    cfg.save_settings()
    assert 'api_key = "dummy"' in cfg.settings_path.read_text()
    cfg.settings.api_key = ""
    cfg.save_settings()
    cfg.load()
    assert cfg.settings.api_key == ""


def test_runtimes_round_trip(cfg):
    cfg.settings.runtimes = [RuntimeEntry("release", r"C:\llama"), RuntimeEntry("local", r"C:\b\bin")]
    cfg.settings.default_runtime = "local"
    cfg.save_settings()
    cfg.load()
    assert [r.name for r in cfg.settings.runtimes] == ["release", "local"]
    cfg.settings.runtimes.pop(0)
    cfg.save_settings()
    cfg.load()
    assert cfg.settings.runtime("local").path == r"C:\b\bin" and cfg.settings.runtime("release") is None


def test_profile_rename_and_delete(cfg):
    cfg.models.append(ModelEntry(r"C:\m\a.gguf", profiles=[Profile("a", ctx_size=1), Profile("b")]))
    cfg.save_profiles()
    cfg.models[0].profiles[0].name = "renamed"
    del cfg.models[0].profiles[1]
    cfg.save_profiles()
    cfg.load()
    assert [(p.name, p.ctx_size) for p in cfg.models[0].profiles] == [("renamed", 1)]
