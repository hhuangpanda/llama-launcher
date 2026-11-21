import gguf_builder as gb
import pytest

from llama_launcher import args, launch
from llama_launcher import catalog as c
from llama_launcher.config import GlobalOptions, ModelEntry, Profile, Settings
from llama_launcher.runtimes import Runtime


def build(help, glob_args="", prof=None, model=None, settings=None, **kw):
    s = settings or Settings(global_=GlobalOptions(args=glob_args, idle_mode="off"))
    m = model or ModelEntry(r"C:\m\model.gguf")
    p = prof or Profile("p")
    return launch.build(s, m, p, Runtime("rt", r"C:\rt", command_prefix=["srv"]), help, **kw)


def flags(spec):
    return spec.argv[1:]


def count(spec, *names):
    return sum(spec.argv.count(n) for n in names)


def value(spec, *names):
    for n in names:
        if n in spec.argv:
            return spec.argv[spec.argv.index(n) + 1]
    return None


# A1
def test_profile_ctx_replaces_global(release_help):
    s = build(release_help, "--ctx-size 102400", Profile("p", ctx_size=76800))
    assert count(s, "--ctx-size", "-c") == 1 and value(s, "--ctx-size") == "76800"


# A2
def test_alias_spellings_merge(release_help):
    s = build(release_help, "-c 4096", Profile("p", args="--ctx-size 8192"))
    assert count(s, "--ctx-size", "-c") == 1 and value(s, "--ctx-size", "-c") == "8192"


# A3
def test_negation_replaces(release_help):
    s = build(release_help, "--kv-unified", Profile("p", args="--no-kv-unified"))
    assert "--no-kv-unified" in s.argv and "--kv-unified" not in s.argv


# A4
def test_unset_removes_global_flag(release_help):
    s = build(release_help, "--spec-type draft-mtp --spec-draft-n-max 2", Profile("p", unset=["--spec-type"]))
    assert "--spec-type" not in s.argv and value(s, "--spec-draft-n-max") == "2"


def test_unset_can_drop_model_mmproj(release_help):
    s = build(release_help, prof=Profile("p", unset=["-mm"]), model=ModelEntry(r"C:\m\a.gguf", mmproj=r"C:\m\p.gguf"))
    assert "--mmproj" not in s.argv


# A5: llama-server has no repeatable flags (lists are comma-separated), so later always wins
def test_override_tensor_later_wins(release_help):
    s = build(release_help, "-ot a=CPU", Profile("p", args="--override-tensor b=CPU,c=CPU"))
    assert count(s, "-ot", "--override-tensor") == 1 and value(s, "--override-tensor") == "b=CPU,c=CPU"


# A6
@pytest.mark.parametrize("bad", ["--port 1", "--host 0.0.0.0", "--api-key k", "-m x.gguf", "--model x.gguf"])
def test_reserved_flags_rejected(release_help, bad):
    s = build(release_help, prof=Profile("p", args=bad))
    assert not s.ok and "managed by llama-launcher" in s.diagnostics[0].message
    assert s.argv[1:5] == ["--host", "127.0.0.1", "--port", "9931"]


# A7
def test_quoted_json_and_paths(release_help):
    raw = r"""--chat-template-kwargs '{"preserve_thinking":true}' --chat-template-file "C:\My Files\t.jinja" """
    s = build(release_help, prof=Profile("p", args=raw))
    assert value(s, "--chat-template-kwargs") == '{"preserve_thinking":true}'
    assert value(s, "--chat-template-file") == r"C:\My Files\t.jinja"


def test_tokenizer_details():
    assert args.tokenize(r'a "b \"c\"" d\e') == ["a", 'b "c"', r"d\e"]
    assert args.tokenize("--temp -1 --x=y") == ["--temp", "-1", "--x=y"]


# A8
def test_unbalanced_quote(release_help):
    s = build(release_help, prof=Profile("p", args="--alias 'oops"))
    assert not s.ok and "unclosed ' quote starting at position 9" in s.diagnostics[0].message


# A9
def test_unknown_flag(release_help):
    s = build(release_help, prof=Profile("p", args="--bogus-flag 1"))
    assert not s.ok and "--bogus-flag is not a flag of this llama-server build (rt)" in s.diagnostics[0].message


# A10
def test_per_runtime_flag_table(release_help, local_help):
    p = Profile("p", args="--moe-cache-cpu-share 1")

    def flag_errors(s):
        return [d for d in s.diagnostics if "not a flag" in d.message]

    assert flag_errors(build(release_help, prof=p))
    assert not flag_errors(build(local_help, prof=p))


# A11
@pytest.mark.parametrize("mode,expect", [("sleep", "3600"), ("stop", "3600"), ("off", None)])
def test_idle_flag(release_help, mode, expect):
    s = build(release_help, settings=Settings(global_=GlobalOptions(idle_mode=mode)))
    assert value(s, "--sleep-idle-seconds") == expect and s.idle_mode == mode


def test_profile_idle_overrides_global(release_help):
    s = build(release_help, settings=Settings(), prof=Profile("p", idle_mode="stop", idle_seconds=60))
    assert value(s, "--sleep-idle-seconds") == "60" and s.idle_mode == "stop"
    s = build(release_help, settings=Settings(), prof=Profile("p", idle_mode="off"))
    assert "--sleep-idle-seconds" not in s.argv


# A12
def test_one_shot_ctx(release_help):
    p = Profile("p", ctx_size=8192)
    s = build(release_help, prof=p, ctx_override=32768)
    assert value(s, "--ctx-size") == "32768" and p.ctx_size == 8192


# A13
def test_model_fields_rendered_once_raw_wins(release_help):
    m = ModelEntry(r"C:\m\a.gguf", mmproj=r"C:\m\p1.gguf", draft=r"C:\m\d.gguf")
    s = build(release_help, model=m, prof=Profile("p", args=r"-mm C:\m\p2.gguf"))
    assert count(s, "--mmproj", "-mm") == 1 and value(s, "-mm") == r"C:\m\p2.gguf"
    assert value(s, "--spec-draft-model") == r"C:\m\d.gguf"
    assert any("overrides the model's mmproj" in d.message for d in s.diagnostics)


def test_alias_default_and_override(release_help):
    assert build(release_help).alias == "model"
    s = build(release_help, prof=Profile("p", args="--alias custom"))
    assert s.alias == "custom" and count(s, "--alias") == 1


def test_cache_types_layering(release_help):
    st = Settings(global_=GlobalOptions(cache_type_k="q8_0", cache_type_v="q8_0", idle_mode="off"))
    s = build(release_help, settings=st, prof=Profile("p", cache_type_v="q4_0"))
    assert value(s, "--cache-type-k") == "q8_0" and value(s, "--cache-type-v") == "q4_0"


def test_env_api_key_and_strip(release_help):
    st = Settings(api_key="dummy", global_=GlobalOptions(idle_mode="off"))
    s = build(release_help, settings=st, base_env={"PATH": "x", "LLAMA_ARG_CTX_SIZE": "1", "LLAMA_API_KEY": "old"})
    assert s.env == {"PATH": "x", "LLAMA_API_KEY": "dummy"} and "dummy" not in s.argv


def test_listen_all_connects_locally(release_help):
    s = build(release_help, settings=Settings(listen="0.0.0.0", port=9000))
    assert value(s, "--host") == "0.0.0.0" and s.base_url == "http://127.0.0.1:9000"


def test_no_runtime(release_help):
    s = launch.build(Settings(), ModelEntry(r"C:\m\a.gguf"), Profile("p"), None, release_help)
    assert not s.ok and "no llama.cpp runtime" in s.diagnostics[0].message


# Validation against GGUF metadata (F5 + pre-launch checks)
@pytest.fixture
def llm(tmp_path):
    d = gb.llm_folder(tmp_path / "LLM")
    return d, c.InfoCache()


def test_missing_model_file(release_help, tmp_path):
    s = build(release_help, model=ModelEntry(str(tmp_path / "gone.gguf")), get_info=c.InfoCache().get)
    assert not s.ok and "model file not found" in s.diagnostics[0].message


def test_mmproj_mismatch_blocked(release_help, llm):
    d, cache = llm
    m = ModelEntry(str(d / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"), mmproj=str(d / "Qwen3.6-35B-A3B-mmproj-F16.gguf"))
    s = build(release_help, model=m, get_info=cache.get)
    assert not s.ok and "doesn't fit this model (projection 2048 vs model embedding 3840)" in s.diagnostics[0].message


def test_draft_mismatch_blocked(release_help, llm):
    d, cache = llm
    m = ModelEntry(str(d / "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf"), draft=str(d / "mtp-gemma-4-12B-it-Q4_0.gguf"))
    s = build(release_help, model=m, get_info=cache.get)
    assert not s.ok and "doesn't fit a qwen35 model" in s.diagnostics[0].message


@pytest.mark.parametrize("file,ok", [("Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf", False),
                                     ("Qwen3.8-27B-UD-Q2_K_XL.gguf", True)])
def test_mtp_needs_head(release_help, llm, file, ok):
    d, cache = llm
    s = build(release_help, "--spec-type draft-mtp", model=ModelEntry(str(d / file)), get_info=cache.get)
    assert s.ok is ok


def test_mtp_draft_needs_spec_type(release_help, llm):
    d, cache = llm
    m = ModelEntry(str(d / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"), draft=str(d / "mtp-gemma-4-12B-it-Q4_0.gguf"))
    s = build(release_help, model=m, get_info=cache.get)
    assert not s.ok and "is an MTP head: add --spec-type draft-mtp" in s.diagnostics[0].message
    assert build(release_help, "--spec-type draft-mtp", model=m, get_info=cache.get).ok


def test_ctx_over_trained_warns(release_help, llm):
    d, cache = llm
    s = build(release_help, model=ModelEntry(str(d / "Qwen3.8-27B-UD-Q2_K_XL.gguf")),
              prof=Profile("p", ctx_size=300000), get_info=cache.get)
    assert s.ok and s.diagnostics[0].level == "warning"


def test_model_rows_merge_configured_and_scanned(llm):
    d, _ = llm
    entries = c.scan([str(d)])
    configured = [ModelEntry(str(d / "Qwen3.8-27B-UD-Q2_K_XL.gguf"), profiles=[Profile("a"), Profile("b")]),
                  ModelEntry(str(d / "missing.gguf"), profiles=[Profile("x")])]
    rows = {launch.stem(r.model.file): r for r in launch.model_rows(configured, entries)}
    assert len(rows) == 7
    assert [p.name for p in rows["Qwen3.8-27B-UD-Q2_K_XL"].model.profiles] == ["a", "b"]
    assert rows["missing"].exists is False
    gemma = rows["gemma-4-12B-it-qat-UD-Q4_K_XL"]
    assert gemma.implicit and gemma.model.draft and gemma.model.profiles[0].args == "--spec-type draft-mtp"
    assert rows["Qwen3.6-35B-A3B-UD-Q4_K_M"].model.profiles[0].args == ""
