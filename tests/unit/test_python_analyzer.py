import ast
import hashlib
import os

import diffimpactscout.impact.python_analyzer as pa
from diffimpactscout.impact.cache import SymbolCache

SRC = (
    "import helper\n"
    "from util import widget\n"
    "\n"
    "def process():\n"
    "    return widget()\n"
    "\n"
    "class Book(object):\n"
    "    status = models.CharField(max_length=20)\n"
    "\n"
    "    def save(self):\n"
    "        return self.status\n"
    "\n"
    "value = process()\n"
)


def _analysis(src):
    return pa.analyze_source(src)


def _write(path, content):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w") as fh:
        fh.write(content)


def _count_parse(monkeypatch):
    calls = {"n": 0}
    real = ast.parse

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    return calls


def test_analyze_source_defs():
    analysis = _analysis(SRC)
    assert analysis is not None
    assert analysis["hash"] == hashlib.sha1(SRC.encode("utf-8")).hexdigest()
    assert analysis["defs"] == {
        "process": {"kind": "function", "line": 4, "end_line": 5, "qname": "process"},
        "Book": {"kind": "class", "line": 7, "end_line": 11, "qname": "Book"},
        "status": {
            "kind": "class_field",
            "line": 8,
            "end_line": 8,
            "qname": "Book.status",
        },
        "save": {"kind": "method", "line": 10, "end_line": 11, "qname": "Book.save"},
        "value": {"kind": "module_field", "line": 13, "end_line": 13, "qname": "value"},
    }


def test_analyze_source_usages():
    analysis = _analysis(SRC)
    assert analysis["usages"] == [
        {"line": 1, "name": "helper", "kind": "import", "ctx_qname": "", "ctx_kind": "module"},
        {"line": 2, "name": "widget", "kind": "import", "ctx_qname": "", "ctx_kind": "module"},
        {"line": 5, "name": "widget", "kind": "name", "ctx_qname": "process", "ctx_kind": "function"},
        {"line": 7, "name": "object", "kind": "name", "ctx_qname": "Book", "ctx_kind": "class"},
        {"line": 8, "name": "CharField", "kind": "attr", "ctx_qname": "Book", "ctx_kind": "class"},
        {"line": 11, "name": "status", "kind": "attr", "ctx_qname": "Book.save", "ctx_kind": "method"},
        {"line": 13, "name": "process", "kind": "name", "ctx_qname": "", "ctx_kind": "module"},
    ]


def test_analyze_source_definitions_not_usages():
    analysis = _analysis("def f():\n    return 1\nclass C:\n    x = 1\n")
    assert analysis["usages"] == []
    assert "f" in analysis["defs"]
    assert "C" in analysis["defs"]
    assert "x" in analysis["defs"]


def test_analyze_source_class_bases_and_decorators_are_usages():
    source = "@decorate\nclass Book(Base):\n    pass\n"
    analysis = _analysis(source)
    names = [u["name"] for u in analysis["usages"]]
    assert sorted(names) == ["Base", "decorate"]


def test_analyze_source_attribute_load_skips_base_name():
    analysis = _analysis("value = obj.status\n")
    assert [u["name"] for u in analysis["usages"]] == ["status"]
    assert analysis["usages"][0]["kind"] == "attr"


def test_analyze_source_nested_attributes():
    analysis = _analysis("out = a.b.c\n")
    assert [u["name"] for u in analysis["usages"]] == ["c", "b"]


def test_analyze_source_import_kinds():
    analysis = _analysis("import os.path as p\nimport pkg.mod\nfrom util import a, b\n")
    usages = analysis["usages"]
    assert [u["name"] for u in usages] == ["os", "pkg", "a", "b"]
    assert all(u["kind"] == "import" for u in usages)


def test_analyze_source_strings_and_docstrings_3_6_12_safe():
    source = (
        '"""module docstring"""\n'
        "from django.contrib import admin as a\n"
        "import os.path as p\n"
        "LABEL = 'ready'\n"
        "\n"
        "def f(text='x'):\n"
        "    return text\n"
    )
    analysis = _analysis(source)
    assert analysis is not None
    names = [u["name"] for u in analysis["usages"]]
    assert "admin" in names
    assert "os" in names
    assert "LABEL" in analysis["defs"]
    assert "f" in analysis["defs"]


def test_analyze_source_accepts_bytes():
    analysis = _analysis(SRC.encode("utf-8"))
    assert analysis is not None
    assert analysis["defs"]["process"]["kind"] == "function"


def test_analyze_source_unparseable_returns_none():
    assert pa.analyze_source("def broken(:\n") is None
    assert pa.analyze_source(b"def broken(:\n") is None


def test_find_references_across_files():
    analyses = {
        "a.py": _analysis("import widget\n"),
        "b.py": _analysis("from util import widget\n\ndef run():\n    return widget()\n"),
        "c.py": _analysis("value = widget()\n"),
    }
    hits = pa.find_references(analyses, ["widget"])
    assert [(h["path"], h["line"], h["how"]) for h in hits] == [
        ("a.py", 1, "import"),
        ("b.py", 1, "import"),
        ("b.py", 4, "name"),
        ("c.py", 1, "name"),
    ]
    assert hits[2] == {
        "name": "widget",
        "path": "b.py",
        "line": 4,
        "how": "name",
        "ctx_qname": "run",
        "ctx_kind": "function",
    }


def test_find_references_unused_name_empty():
    analyses = {"a.py": _analysis("import widget\n")}
    assert pa.find_references(analyses, ["nope"]) == []
    assert pa.find_references(analyses, []) == []
    assert pa.find_references({}, ["widget"]) == []


def test_find_references_attr_name_matches_field_usage():
    analyses = {
        "models.py": _analysis(
            "class Book(object):\n    status = models.CharField()\n"
        ),
        "views.py": _analysis("def view():\n    return book.status\n"),
    }
    hits = pa.find_references(analyses, ["status"])
    assert [(h["path"], h["line"], h["how"], h["ctx_kind"]) for h in hits] == [
        ("views.py", 2, "attr", "function")
    ]


def test_find_references_skips_attribute_base_names():
    analyses = {
        "models.py": _analysis(
            "class Book(object):\n    status = models.CharField()\n"
        )
    }
    assert pa.find_references(analyses, ["models"]) == []
    hits = pa.find_references(analyses, ["CharField"])
    assert [(h["path"], h["how"]) for h in hits] == [("models.py", "attr")]


def test_find_references_deterministic_order():
    analyses = {"a.py": _analysis("from m import alpha, beta\n")}
    hits = pa.find_references(analyses, ["beta", "alpha"])
    assert [(h["name"], h["line"], h["how"]) for h in hits] == [
        ("alpha", 1, "import"),
        ("beta", 1, "import"),
    ]


def test_find_references_kinds_attr_excludes_bare_name():
    analyses = {
        "models.py": _analysis(
            "class Book(object):\n    status = models.CharField()\n\n"
            "    def save(self):\n        return self.status\n"
        ),
        "views.py": _analysis(
            "def helper():\n    status = 'ready'\n    return status\n\n"
            "def view(book):\n    return book.status\n"
        ),
    }
    hits = pa.find_references(analyses, ["status"], kinds={"attr"})
    assert [(h["path"], h["line"], h["how"], h["ctx_kind"]) for h in hits] == [
        ("models.py", 5, "attr", "method"),
        ("views.py", 6, "attr", "function"),
    ]


def test_find_references_kinds_none_matches_all_kinds():
    analyses = {
        "a.py": _analysis("import widget\n"),
        "b.py": _analysis("def run():\n    return widget()\n"),
        "c.py": _analysis("value = obj.widget\n"),
    }
    hits = pa.find_references(analyses, ["widget"])
    assert [(h["path"], h["how"]) for h in hits] == [
        ("a.py", "import"),
        ("b.py", "name"),
        ("c.py", "attr"),
    ]


def test_find_references_kinds_name_only_name_hits():
    analyses = {
        "a.py": _analysis("import widget\n"),
        "b.py": _analysis("value = obj.widget\n"),
        "c.py": _analysis("def run():\n    return widget()\n"),
    }
    hits = pa.find_references(analyses, ["widget"], kinds={"name"})
    assert [(h["path"], h["how"]) for h in hits] == [("c.py", "name")]


def test_find_references_kinds_multiple():
    analyses = {"a.py": _analysis("import widget\n"), "b.py": _analysis("value = obj.widget\n")}
    hits = pa.find_references(analyses, ["widget"], kinds={"attr", "import"})
    assert [(h["path"], h["how"]) for h in hits] == [
        ("a.py", "import"),
        ("b.py", "attr"),
    ]


def test_find_references_accepts_cache_entries():
    analysis = _analysis("import widget\n")
    analyses = {"a.py": {"hash": analysis["hash"], "analysis": analysis}}
    hits = pa.find_references(analyses, ["widget"])
    assert [(h["path"], h["how"]) for h in hits] == [("a.py", "import")]


def test_analyze_path_analyzes_and_stores(tmp_path):
    _write(str(tmp_path / "mod.py"), "import widget\n")
    cache = SymbolCache(str(tmp_path / "cache.json"))
    analysis = pa.analyze_path("mod.py", str(tmp_path), cache)
    assert analysis is not None
    data = cache.load()
    assert "mod.py" in data
    assert data["mod.py"]["hash"] == analysis["hash"]
    assert data["mod.py"]["analysis"] is analysis


def test_analyze_path_cache_hit_skips_reparse(tmp_path, monkeypatch):
    _write(str(tmp_path / "mod.py"), SRC)
    cache = SymbolCache(str(tmp_path / "cache.json"))
    calls = _count_parse(monkeypatch)
    first = pa.analyze_path("mod.py", str(tmp_path), cache)
    second = pa.analyze_path("mod.py", str(tmp_path), cache)
    assert calls["n"] == 1
    assert first is second
    assert first["defs"] == second["defs"]


def test_analyze_path_reparses_when_content_changes(tmp_path, monkeypatch):
    path = str(tmp_path / "mod.py")
    _write(path, SRC)
    cache = SymbolCache(str(tmp_path / "cache.json"))
    calls = _count_parse(monkeypatch)
    pa.analyze_path("mod.py", str(tmp_path), cache)
    _write(path, SRC + "added = 1\n")
    analysis = pa.analyze_path("mod.py", str(tmp_path), cache)
    assert calls["n"] == 2
    assert "added" in analysis["defs"]


def test_analyze_path_uses_preseeded_cache_entry(tmp_path):
    _write(str(tmp_path / "mod.py"), "import widget\n")
    cache = SymbolCache(str(tmp_path / "cache.json"))
    analysis = _analysis("import widget\n")
    cache.save({"mod.py": {"hash": analysis["hash"], "analysis": analysis}})
    out = pa.analyze_path("mod.py", str(tmp_path), cache)
    assert out == analysis


def test_analyze_path_relative_posix_key(tmp_path):
    _write(str(tmp_path / "pkg" / "mod.py"), "import widget\n")
    cache = SymbolCache(str(tmp_path / "cache.json"))
    analysis = pa.analyze_path("pkg/mod.py", str(tmp_path), cache)
    assert analysis is not None
    assert "pkg/mod.py" in cache.load()


def test_analyze_path_accepts_plain_dict_and_none_cache(tmp_path):
    _write(str(tmp_path / "mod.py"), "import widget\n")
    plain = {}
    analysis = pa.analyze_path("mod.py", str(tmp_path), plain)
    assert analysis is not None
    assert "mod.py" in plain
    assert pa.analyze_path("mod.py", str(tmp_path), None) is not None


def test_analyze_path_unparseable_returns_none(tmp_path):
    _write(str(tmp_path / "bad.py"), "def broken(:\n")
    cache = SymbolCache(str(tmp_path / "cache.json"))
    assert pa.analyze_path("bad.py", str(tmp_path), cache) is None
    assert cache.load() == {}


def test_analyze_path_missing_file_returns_none(tmp_path):
    cache = SymbolCache(str(tmp_path / "cache.json"))
    assert pa.analyze_path("missing.py", str(tmp_path), cache) is None
    assert pa.analyze_path("missing.py", None, cache) is None


def test_analyze_path_directory_returns_none(tmp_path):
    os.makedirs(str(tmp_path / "adir"))
    cache = SymbolCache(str(tmp_path / "cache.json"))
    assert pa.analyze_path("adir", str(tmp_path), cache) is None