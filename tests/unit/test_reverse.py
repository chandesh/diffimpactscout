import json
import os

import diffimpactscout.impact.reverse as rev
from diffimpactscout.impact.cache import SymbolCache


def _repo(tmp_path, files):
    root = str(tmp_path)
    for rel, content in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
    return root


def test_template_name_of_normalizes_last_templates_component():
    assert rev.template_name_of("shop/templates/shop/product.html") == "shop/product.html"
    assert rev.template_name_of("project/templates/common/base.html") == "common/base.html"
    assert rev.template_name_of("views.py") is None


def test_template_graph_extracts_extends_include_assets(tmp_path):
    root = _repo(tmp_path, {
        "shop/templates/shop/base.html": (
            "{% extends 'common/base.html' %}\n"
            "{% include 'shop/nav.html' %}\n"
            "<script src=\"{{ MEDIA_URL }}shop/app.js?v=1.2\"></script>\n"
            "<!-- <script src=\"commented.js\"></script> -->\n"
        ),
    })
    cache = SymbolCache(str(tmp_path / "cache.json"))
    cfg = {"ignore_paths": [], "use_gitignore": False}
    graph = rev.analyze_template_graph(root, "shop/templates/shop/base.html", cache, cfg)
    assert graph["extends"] == ["common/base.html"]
    assert graph["includes"] == ["shop/nav.html"]
    assert graph["assets"] == ["shop/app.js"]


def test_template_graph_cached_by_hash(tmp_path):
    root = _repo(tmp_path, {"shop/templates/shop/base.html": "{% extends 'c/b.html' %}\n"})
    cache = SymbolCache(str(tmp_path / "cache.json"))
    cfg = {"ignore_paths": [], "use_gitignore": False}
    rev.analyze_template_graph(root, "shop/templates/shop/base.html", cache, cfg)
    os.utime(os.path.join(root, "shop/templates/shop/base.html"))  # same content
    graph = rev.analyze_template_graph(root, "shop/templates/shop/base.html", cache, cfg)
    assert graph["extends"] == ["c/b.html"]
    fresh = SymbolCache(str(tmp_path / "cache.json"))
    assert fresh.load_section("templates")["shop/templates/shop/base.html"]["hash"]


def test_dynamic_include_lands_in_dynamic_list(tmp_path):
    root = _repo(tmp_path, {"shop/templates/shop/x.html": "{% include block.template_name %}\n"})
    cache = SymbolCache(str(tmp_path / "cache.json"))
    cfg = {"ignore_paths": [], "use_gitignore": False}
    graph = rev.analyze_template_graph(root, "shop/templates/shop/x.html", cache, cfg)
    assert graph["includes"] == []
