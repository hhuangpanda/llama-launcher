import logging

import pytest
from conftest import FIXTURES

from llama_launcher import errors, logs

LOGS = FIXTURES / "logs"


# E1
@pytest.mark.parametrize("name,code,kind,text", [
    ("unknown-argument", 1, "unknown_argument", "rejected the argument --bogus-flag"),
    ("file-not-found", 1, "file_not_found", r"does-not-exist.gguf"),
    ("mmproj-mismatch", 1, "mmproj_mismatch", "model 3840, mmproj 2048"),
    ("mtp-without-head", 1, "mtp_unavailable", "no MTP head"),
    ("draft-mismatch", 3221225477, "crash", "access violation"),
    ("synthetic-oom", 1, "out_of_memory", "ran out of GPU memory"),
])
def test_real_failure_logs(name, code, kind, text):
    f = errors.classify(code, errors.tail(LOGS / f"{name}.log"))
    assert f.kind == kind and text in f.message


# E2
def test_unknown_output_shows_code_and_tail():
    lines = [f"0.00.00{i}.000 I something {i}" for i in range(10)]
    f = errors.classify(7, lines)
    assert f.kind == "other" and f.message.splitlines()[0] == "llama-server exited with code 7"
    assert f.message.splitlines()[-1] == "something 9" and len(f.message.splitlines()) == 6


def test_crash_codes():
    f = errors.classify(3221226505, [])
    assert f.kind == "crash" and "0xC0000409" in f.message
    assert "(0xC0000135)" in errors.classify(0xC0000135, []).message


# L5
def test_app_log_rotates(tmp_path):
    d = tmp_path / "logs"
    logs.setup_app_log(d)
    logging.getLogger("llama_launcher").info("first session")
    logs.setup_app_log(d)
    logging.getLogger("llama_launcher").info("second session")
    logging.getLogger("llama_launcher").handlers[0].flush()
    assert "first session" in (d / "app.prev.log").read_text() and "second session" in (d / "app.log").read_text()
    logs.setup_app_log(d)
    assert "first session" not in (d / "app.prev.log").read_text()
    for h in logging.getLogger("llama_launcher").handlers:
        h.close()
