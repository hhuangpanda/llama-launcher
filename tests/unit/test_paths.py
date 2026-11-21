import threading
import time

import pytest

from llama_launcher import paths


def _pair(tmp_path):
    src, dst = tmp_path / "new.toml", tmp_path / "cur.toml"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    return src, dst


def test_replace_waits_out_a_brief_lock(tmp_path):
    src, dst = _pair(tmp_path)
    opened = threading.Event()

    def hold():
        with open(dst, "rb"):  # opened without delete sharing, so Windows refuses the replace meanwhile
            opened.set()
            time.sleep(0.1)

    t = threading.Thread(target=hold)
    t.start()
    opened.wait()
    paths.replace_file(src, dst)
    t.join()
    assert paths.read_text(dst) == "new"


def test_replace_gives_up_on_a_lasting_lock(tmp_path):
    src, dst = _pair(tmp_path)
    with open(dst, "rb"), pytest.raises(PermissionError):
        paths.replace_file(src, dst)
    assert paths.read_text(dst) == "old"
