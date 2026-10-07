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


def test_template_graph_invalidated_on_content_change(tmp_path):
    root = _repo(tmp_path, {"shop/templates/shop/a.html": "{% extends 'old.html' %}\n"})
    cache = SymbolCache(str(tmp_path / "cache.json"))
    cfg = {"ignore_paths": [], "use_gitignore": False}
    first = rev.analyze_template_graph(root, "shop/templates/shop/a.html", cache, cfg)
    assert first["extends"] == ["old.html"]
    with open(os.path.join(root, "shop/templates/shop/a.html"), "w") as fh:
        fh.write("{% extends 'new.html' %}\n{% include 'x.html' %}\n")
    second = rev.analyze_template_graph(root, "shop/templates/shop/a.html", cache, cfg)
    assert second["extends"] == ["new.html"]
    assert second["includes"] == ["x.html"]


def test_template_graph_return_shape_consistent_hit_and_miss(tmp_path):
    root = _repo(tmp_path, {"shop/templates/shop/s.html": "{% extends 'b.html' %}\n"})
    cache = SymbolCache(str(tmp_path / "cache.json"))
    cfg = {"ignore_paths": [], "use_gitignore": False}
    miss = rev.analyze_template_graph(root, "shop/templates/shop/s.html", cache, cfg)
    hit = rev.analyze_template_graph(root, "shop/templates/shop/s.html", cache, cfg)
    assert set(miss) == set(hit) == {"extends", "includes", "assets"}
    assert "graph" not in miss
    assert "hash" not in miss


def test_normalize_asset_ref_dynamic_cdn_query_and_dot_lead():
    assert rev._normalize_asset_ref("{{ X }}") is None
    assert rev._normalize_asset_ref("https://cdn.example.com/a.js") is None
    assert rev._normalize_asset_ref("//cdn.example.com/a.js") is None
    assert rev._normalize_asset_ref("shop/app.js?v=1.2") == "shop/app.js"
    assert rev._normalize_asset_ref("./js/app.js") == "js/app.js"


def test_render_site_rows_for_changed_template():
    """Verifies that a changed template produces a python row per render site."""
    analyses = {
        "shop/views.py": {
            "analysis": {
                "defs": {},
                "usages": [
                    {"line": 9, "name": "shop/product.html", "kind": "str", "ctx_qname": "product_view", "ctx_kind": "function"},
                    {"line": 3, "name": "shop/other.html", "kind": "str", "ctx_qname": "other_view", "ctx_kind": "function"},
                ],
            }
        },
    }
    hits = rev.find_render_sites(analyses, ["shop/product.html"])
    assert len(hits) == 1
    assert hits[0]["path"] == "shop/views.py"
    assert hits[0]["ctx_leaf"] == "product_view"


def test_extends_descendants_closed_transitively():
    """Verifies that descendant templates of a changed base are closed transitively."""
    graphs = {
        "a.html": {"extends": ["b.html"], "includes": []},
        "b.html": {"extends": ["c.html"], "includes": ["d.html"]},
        "c.html": {"extends": [], "includes": []},
        "d.html": {"extends": [], "includes": []},
    }
    out = rev.descendants_of("c.html", graphs)
    assert out == {"b.html": ["c.html"], "a.html": ["b.html"]}


def test_extends_cycle_guard():
    """Verifies that an extends cycle does not loop forever."""
    graphs = {
        "a.html": {"extends": ["b.html"], "includes": []},
        "b.html": {"extends": ["a.html"], "includes": []},
    }
    assert rev.descendants_of("a.html", graphs) == {}


def test_reverse_row_shape():
    """Verifies that row dicts carry the fields the report pipeline consumes."""
    row = rev.make_row("shop/views.py", "shop", "python", "render('shop/product.html') at line 9", "product_view")
    assert row["path"] == "shop/views.py"
    assert row["category"] == "python"
    assert row["_entity"] == "product_view"
    assert row["_deleted"] is False
