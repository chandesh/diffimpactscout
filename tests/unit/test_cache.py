import builtins
import glob
import json
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
    """Verifies that saved cache data loads back unchanged."""
    path = str(tmp_path / "cache.json")
    data = _sample_data()
    SymbolCache(path).save(data)
    loaded = SymbolCache(path).load()
    assert loaded == data


def test_load_missing_returns_empty(tmp_path):
    """Checks that loading a missing cache file returns an empty dict."""
    path = str(tmp_path / "nope" / "cache.json")
    assert SymbolCache(path).load() == {}


def test_load_corrupt_returns_empty(tmp_path):
    """Verifies that a corrupt cache file loads as an empty dict."""
    path = str(tmp_path / "cache.json")
    with open(path, "wb") as fh:
        fh.write(b"\x00\x01not-json{")
    sc = SymbolCache(path)
    assert sc.load() == {}
    assert sc.load() == {}


def test_load_non_object_returns_empty(tmp_path):
    """Checks that a non-object cache file loads as an empty dict."""
    path = str(tmp_path / "cache.json")
    with open(path, "w") as fh:
        fh.write("[1, 2, 3]")
    assert SymbolCache(path).load() == {}


def test_save_creates_nested_parent_dirs(tmp_path):
    """Verifies that save creates nested parent directories as needed."""
    path = str(tmp_path / "nested" / "deep" / "cache.json")
    data = _sample_data()
    SymbolCache(path).save(data)
    assert SymbolCache(path).load() == data
    assert os.path.isfile(path)


def test_save_parent_is_file_no_raise(tmp_path, capsys):
    """Checks that save warns instead of raising when the parent is a file."""
    blocker = tmp_path / "blocker"
    with open(str(blocker), "w") as fh:
        fh.write("occupied")
    path = str(blocker / "cache.json")
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err


def test_save_replace_failure_no_raise(tmp_path, monkeypatch, capsys):
    """Verifies that a replace failure warns without raising."""
    path = str(tmp_path / "cache.json")

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(cache.os, "replace", _boom)
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err
    assert not os.path.exists(path)


def test_save_open_failure_no_raise(tmp_path, monkeypatch, capsys):
    """Checks that an open failure warns without raising."""
    path = str(tmp_path / "cache.json")

    def _boom(*args, **kwargs):
        raise OSError("simulated open failure")

    monkeypatch.setattr(builtins, "open", _boom)
    SymbolCache(path).save(_sample_data())
    assert "warning:" in capsys.readouterr().err


def test_save_leaves_no_temp_file(tmp_path):
    """Verifies that save leaves no temporary file behind."""
    path = str(tmp_path / "cache.json")
    SymbolCache(path).save(_sample_data())
    leftovers = glob.glob(str(tmp_path / "cache.json.tmp*"))
    assert leftovers == []


def test_prune_removes_stale_keys(tmp_path):
    """Checks that prune removes cache keys not in the given files."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    pruned = sc.prune(["a.py"])
    assert pruned == {"a.py": _sample_data()["a.py"]}
    assert sc.load() == {"a.py": _sample_data()["a.py"]}


def test_prune_keeps_all_when_no_existing(tmp_path):
    """Verifies prune keeps all entries when none are prunable."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    assert sc.prune(["a.py", "b.py"]) == _sample_data()


def test_prune_accepts_list_and_empty(tmp_path):
    """Checks that prune accepts a file list and handles an empty list."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    assert sc.prune([]) == {}
    assert sc.prune(["a.py", "b.py"]) == {}


def test_sections_migrate_legacy_flat_cache(tmp_path):
    """Verifies that a legacy flat cache file loads as the py section."""
    path = str(tmp_path / "cache.json")
    legacy_data = {
        "some/mod.py": {
            "hash": "abc",
            "analysis": {"defs": {}, "usages": []},
        }
    }
    with open(path, "w") as fh:
        json.dump(legacy_data, fh)
    sc = SymbolCache(path)
    assert sc.load() == legacy_data
    assert sc.load_section("templates") == {}
    assert sc.load_section("frontend") == {}


def test_save_section_preserves_py_section(tmp_path):
    """Verifies that writing a section keeps the py section intact on disk."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    sc.save_section("templates", {"a.html": {"hash": "h1", "graph": {}}})
    fresh = SymbolCache(path)
    assert fresh.load() == _sample_data()
    assert fresh.load_section("templates") == {"a.html": {"hash": "h1", "graph": {}}}


def test_prune_section_only_touches_own_section(tmp_path):
    """Verifies that pruning a section leaves other sections alone."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    sc.save_section("frontend", {"x.ts": {"hash": "h", "graph": {}}, "y.ts": {"hash": "h", "graph": {}}})
    sc.prune_section("frontend", ["x.ts"])
    fresh = SymbolCache(path)
    assert "x.ts" in fresh.load_section("frontend")
    assert "y.ts" not in fresh.load_section("frontend")
    assert fresh.load() == _sample_data()


def test_corrupt_section_file_loads_empty(tmp_path):
    """Verifies that a corrupt multi-section cache file degrades to empty."""
    path = str(tmp_path / "cache.json")
    with open(path, "wb") as fh:
        fh.write(b"\x00not-json{")
    sc = SymbolCache(path)
    assert sc.load() == {}
    assert sc.load_section("templates") == {}


def test_save_preserves_existing_templates_section(tmp_path):
    """Verifies that save preserves an existing templates section."""
    path = str(tmp_path / "cache.json")
    sc = SymbolCache(path)
    sc.save(_sample_data())
    sc.save_section("templates", {"t.html": {"hash": "h", "graph": {}}})
    sc.save(_sample_data())  # save again
    fresh = SymbolCache(path)
    assert fresh.load() == _sample_data()
    assert fresh.load_section("templates") == {"t.html": {"hash": "h", "graph": {}}}


def test_save_after_loading_legacy_flat_makes_sectioned(tmp_path):
    """Verifies that after loading legacy flat cache, save() persists sectioned format."""
    path = str(tmp_path / "cache.json")
    legacy_data = {
        "some/mod.py": {
            "hash": "abc",
            "analysis": {"defs": {}, "usages": []},
        }
    }
    with open(path, "w") as fh:
        json.dump(legacy_data, fh)
    sc = SymbolCache(path)
    sc.save(sc.load())  # re-save under sectioned model
    fresh = SymbolCache(path)
    assert fresh.load() == legacy_data
    assert fresh.load_section("templates") == {}
    assert fresh.load_section("frontend") == {}
