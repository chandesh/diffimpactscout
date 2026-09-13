import builtins
import glob
import os

import diffimpactscout.impact.cache as cache
from diffimpactscout.impact.cache import SymbolCache


def _sample_data():
    return {
        "a.py": {
            "hash": "abc123",
            "analysis": {"defs": {"helper": {"line": 1}}, "usages": []},
        },
        "b.py": {
            "hash": "def456",
            "analysis": {"defs": {"Book": {"line": 5}}, "usages": []},
        },
    }


def test_roundtrip_save_load(tmp_path):
    path = str(tmp_path / "cache.json")
    data = _sample_data()
    SymbolCache(path).save(data)
    loaded = SymbolCache(path).load()
    assert loaded == data


def test_load_missing_returns_empty(tmp_path):
    path = str(tmp_path / "nope" / "cache.json")
    assert SymbolCache(path).load() == {}


def test_load_corrupt_returns_empty(tmp_path):
    path = str(tmp_path / "cache.json")
    with open(path, "wb") as fh:
        fh.write(b"\x00\x01not-json{")
    sc = SymbolCache(path)
    assert sc.load() == {}
    assert sc.load() == {}


def test_load_non_object_returns_empty(tmp_path):
    path = str(tmp_path / "cache.json")
    with open(path, "w") as fh:
        fh.write("[1, 2, 3]")
    assert SymbolCache(path).load() == {}


def test_save_creates_nested_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "deep" / "cache.json")
    data = _sample_data()
    SymbolCache(path).save(data)
    assert SymbolCache(path).load() == data
    assert os.path.isfile(path)


def test_save_parent_is_file_no_raise(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    with open(str(blocker), "w") as fh:
        fh.write("occupied")
    path = str(blocker / "cache.json")
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err


def test_save_replace_failure_no_raise(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "cache.json")

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(cache.os, "replace", _boom)
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err
    assert not os.path.exists(path)


def test_save_open_failure_no_raise(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "cache.json")

    def _boom(*args, **kwargs):
        raise OSError("simulated open failure")

    monkeypatch.setattr(builtins, "open", _boom)
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err


def test_save_leaves_no_temp_file(tmp_path):
    path = str(tmp_path / "cache.json")
    SymbolCache(path).save(_sample_data())
    leftovers = glob.glob(str(tmp_path / "cache.json.tmp"))
    assert leftovers == []


def test_prune_removes_stale_keys(tmp_path):
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    pruned = sc.prune(["a.py"])
    assert pruned == {"a.py": _sample_data()["a.py"]}
    assert sc.load() == {"a.py": _sample_data()["a.py"]}


def test_prune_keeps_all_when_no_existing(tmp_path):
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    assert sc.prune(["a.py", "b.py"]) == _sample_data()


def test_prune_accepts_list_and_empty(tmp_path):
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    assert sc.prune([]) == {}
    assert sc.prune(["a.py", "b.py"]) == {}