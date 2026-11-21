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


# F3
@pytest.mark.parametrize(
    "text,msg",
    [
        ("port = ", "settings.toml"),
        ("port = 'x'", "must be int"),
        ("port = 70000", "1-65535"),
        ("bogus = 1", "unknown key"),
        ("[global]\nidle_mode = 'nap'", "idle_mode"),
        ("[global]\ncache_type_k = 'q3'", "cache_type_k"),
        ("[[runtimes]]\nname = 'a'", "needs 'name' and 'path'"),
    ],
)
def test_invalid_settings_keep_last_good(cfg, text, msg):
    cfg.settings.port = 1234
    cfg.save_settings()
    cfg.load()
    cfg.settings_path.write_text(text)
    with pytest.raises(ConfigError, match=msg):
        cfg.load()
    assert cfg.settings.port == 1234


@pytest.mark.parametrize(
    "text,msg",
    [
        ("[[models]]\nmmproj = 'x'", "'file' is required"),
        ("[[models]]\nfile='a'\n[[models.profiles]]\nname='p'\n[[models.profiles]]\nname='p'", "duplicated"),
        ("[[models]]\nfile='a'\n[[models]]\nfile='A'", "listed twice"),
        ("[[models]]\nfile='a'\n[[models.profiles]]\nname='p'\nctx_size='big'", "must be int"),
    ],
)
def test_invalid_profiles(cfg, text, msg):
    cfg.profiles_path.write_text(text)
    with pytest.raises(ConfigError, match=msg):
        cfg.load()


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
