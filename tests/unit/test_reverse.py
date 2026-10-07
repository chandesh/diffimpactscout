import os
import subprocess

import diffimpactscout.impact.reverse as rev
from diffimpactscout.impact.cache import SymbolCache
from diffimpactscout.impact.diff_parser import get_file_changes


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


def test_render_site_skips_non_str_usage():
    """Verifies that a matching name with kind != "str" is not a render site."""
    analyses = {
        "shop/views.py": {
            "analysis": {
                "usages": [
                    {"line": 9, "name": "shop/product.html", "kind": "name", "ctx_qname": "v", "ctx_kind": "function"},
                    {"line": 12, "name": "shop/product.html", "kind": "attr", "ctx_qname": "v", "ctx_kind": "function"},
                    {"line": 15, "name": "shop/product.html", "kind": "str", "ctx_qname": "v", "ctx_kind": "function"},
                ],
            }
        },
    }
    hits = rev.find_render_sites(analyses, ["shop/product.html"])
    assert [h["line"] for h in hits] == [15]


def test_render_site_skips_module_scope_usage():
    """Verifies that module-scope string constants are not render sites."""
    analyses = {
        "shop/constants.py": {
            "analysis": {
                "usages": [
                    {"line": 4, "name": "shop/product.html", "kind": "str", "ctx_qname": "", "ctx_kind": "module"},
                    {"line": 8, "name": "shop/product.html", "kind": "str"},
                ],
            }
        },
    }
    assert rev.find_render_sites(analyses, ["shop/product.html"]) == []


def test_render_site_handles_flat_raw_analysis():
    """Verifies that a raw analysis dict (not wrapped) is accepted."""
    analyses = {
        "shop/views.py": {
            "usages": [
                {"line": 9, "name": "shop/product.html", "kind": "str", "ctx_qname": "product_view", "ctx_kind": "function"},
            ],
        },
    }
    hits = rev.find_render_sites(analyses, ["shop/product.html"])
    assert len(hits) == 1
    assert hits[0]["ctx_leaf"] == "product_view"


def test_render_site_skips_non_list_usages():
    """Verifies a non-list "usages" value is ignored instead of crashing."""
    analyses = {
        "a.py": {"analysis": {"usages": {"line": 1}}},
        "b.py": {"analysis": {"usages": None}},
    }
    assert rev.find_render_sites(analyses, ["shop/product.html"]) == []


def test_extends_cycle_fail_closed_with_genuine_child():
    """Verifies a seed on a cycle yields no descendants even with real children."""
    graphs = {
        "a.html": {"extends": ["x.html"], "includes": []},
        "x.html": {"extends": ["a.html"], "includes": []},
        "b.html": {"extends": ["a.html"], "includes": []},
    }
    assert rev.descendants_of("a.html", graphs) == {}


def test_normalize_asset_ref_strips_query_and_prefix():
    assert rev._normalize_asset_ref("{{ MEDIA_URL }}shop/app.js?v=1.2") == "shop/app.js"
    assert rev._normalize_asset_ref("./shop/app.js") == "shop/app.js"
    assert rev._normalize_asset_ref("https://cdn.example/x.js") is None
    assert rev._normalize_asset_ref("{{ MEDIA_URL }}shop/app.js") == "shop/app.js"


def test_asset_chain_rows(tmp_path):
    """Verifies that a changed tracked js file produces template + view rows."""
    root = _repo(tmp_path, {
        "media/shop/app.js": "console.log('x');\n",
        "media/shop/orphan.js": "console.log('o');\n",
        "shop/templates/shop/base.html": "<script src=\"{{ MEDIA_URL }}shop/app.js?v=1\"></script>\n",
        "shop/templates/shop/page.html": "<script src=\"{{ MEDIA_URL }}shop/other.js\"></script>\n",
        "shop/views.py": (
            "from django.shortcuts import render\n\n"
            "def base_view(request):\n"
            "    return render(request, 'shop/base.html')\n"
        ),
    })
    subprocess.check_call(["git", "init", "-q"], cwd=root)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/master"], cwd=root)
    subprocess.check_call(["git", "-C", root, "config", "user.name", "T"])
    subprocess.check_call(["git", "-C", root, "config", "user.email", "t@e.co"])
    subprocess.check_call(["git", "-C", root, "add", "-A"])
    subprocess.check_call(["git", "-C", root, "commit", "-q", "-m", "m0"])
    subprocess.check_call(["git", "-C", root, "remote", "add", "upstream", "."])
    subprocess.check_call(["git", "-C", root, "push", "-q", "upstream", "master"])
    with open(os.path.join(root, "media", "shop", "app.js"), "a") as fh:
        fh.write("console.log('y');\n")
    with open(os.path.join(root, "media", "shop", "orphan.js"), "a") as fh:
        fh.write("console.log('z');\n")
    subprocess.check_call(["git", "-C", root, "add", "-A"])
    subprocess.check_call(["git", "-C", root, "commit", "-q", "-m", "m1"])

    cache = SymbolCache(str(tmp_path / "cache.json"))
    analyses = {
        "shop/views.py": {
            "analysis": {
                "usages": [
                    {"line": 4, "name": "shop/base.html", "kind": "str",
                     "ctx_qname": "base_view", "ctx_kind": "function"},
                ],
            }
        },
    }
    changes = get_file_changes(root, "refs/remotes/upstream/master", False, None, None)
    cfg = {"ignore_paths": [], "use_gitignore": False,
           "impact": {"reverse": True, "template_globs": ["**/*.html"],
                      "asset_url_prefixes": ["{{ MEDIA_URL }}"]}}
    entities = {}
    extra_entities, rows, layers, unresolved = rev.frontend_chain(
        root, cfg, cfg["impact"], changes, entities, analyses, cache,
        "refs/remotes/upstream/master", False, None, None, experimental=False,
    )
    cats = {(r["path"], r["category"]) for r in rows}
    assert ("shop/templates/shop/base.html", "template") in cats
    assert not any("page.html" in p for p, _c in cats)
    py_rows = [r for r in rows if r["category"] == "python"]
    assert any(r["path"] == "shop/views.py" for r in py_rows)
    assert unresolved == [
        {"file": "media/shop/orphan.js", "reason": "no template references this asset"}
    ]
    assert layers.get("asset:media/shop/app.js") == {"template"}
    assert extra_entities.get("base_view", {}).get("modules") == {"shop.views"}


def test_extract_imports_exports_static_only(tmp_path):
    """Verifies static import/export extraction with vendor + alias filtering."""
    src = (
        "import { Component } from '@angular/core';\n"
        "import { BaseService } from './base.service';\n"
        "import { Widget } from \"../widgets/widget\";\n"
        "const lazy = () => import('./lazy.module');\n"
        "import cfg from 'config-path/config';\n"
        "export class PageService extends BaseService {}\n"
        "export function loadPage() { return 1; }\n"
        "export const PAGE = 'p';\n"
    )
    root = _repo(tmp_path, {"media/shop/src/app/page.service.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/shop/src/app/page.service.ts", None, set())
    assert graph["imports"] == ["./base.service", "../widgets/widget", "./lazy.module"]
    assert sorted(graph["exports"]) == ["PAGE", "PageService", "loadPage"]


def test_resolve_specifier_infers_extensions():
    tracked = {"media/shop/src/app/base.service.ts", "media/shop/src/app/util/index.ts"}
    assert rev._resolve_specifier("./base.service", "media/shop/src/app/page.service.ts", tracked) == "media/shop/src/app/base.service.ts"
    assert rev._resolve_specifier("./util", "media/shop/src/app/page.service.ts", tracked) == "media/shop/src/app/util/index.ts"
    assert rev._resolve_specifier("@angular/core", "media/shop/src/app/page.service.ts", tracked) is None


def test_importer_closure_inverted_transitive():
    graph = {
        "a.ts": ["b.ts"],
        "b.ts": ["c.ts"],
        "c.ts": [],
    }
    out = rev.importers_of("c.ts", graph)
    assert out == {"a.ts", "b.ts"}


def test_importer_closure_caps_wide_fan_in():
    """The 500-node cap holds even for a module with 1000 direct importers."""
    graph = {"shared.ts": []}
    for i in range(1000):
        graph["m%d.ts" % i] = ["shared.ts"]
    out = rev.importers_of("shared.ts", graph)
    assert len(out) == 500


def test_min_js_excluded_from_graph():
    assert rev._is_graph_file("media/x/y.min.js") is False
    assert rev._is_graph_file("media/x/y.ts") is True


def test_import_regex_multiline_and_sideeffect(tmp_path):
    src = (
        "import {\n  Alpha,\n  Beta\n} from './multi';\n"
        "import './polyfills';\n"
        "const doc = \"import x from './fake'\";\n"
        "bellyrequire('./req');\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert "./multi" in graph["imports"]
    assert "./polyfills" in graph["imports"]
    assert "./fake" not in graph["imports"]
    assert "./req" not in graph["imports"]


def test_resolve_specifier_rejects_overclimb_and_strips_fragment():
    tracked = {"media/a/b/x.ts", "media/a/b/c.ts"}
    assert rev._resolve_specifier("../../../../c", "media/a/b/d.ts", tracked) is None
    assert rev._resolve_specifier("./x#frag", "media/a/b/d.ts", tracked) == "media/a/b/x.ts"


def test_frontend_graph_tolerates_non_dict_cache_entry(tmp_path):
    from diffimpactscout.impact.cache import SymbolCache

    root = _repo(tmp_path, {"media/a.ts": "import './b';\n"})
    cache = SymbolCache(str(tmp_path / "c.json"))
    cache.save_section("frontend", {"media/a.ts": "garbage"})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", cache, set())
    assert graph["imports"] == ["./b"]


def test_is_graph_file_excludes_min_and_bundle_variants():
    assert rev._is_graph_file("media/x/y.min.jsx") is False
    assert rev._is_graph_file("media/x/y.bundle.jsx") is False
    assert rev._is_graph_file("media/x/y.min.cjs") is False
    assert rev._is_graph_file("media/x/y.ts") is True


def test_brace_export_multiline_and_right_side_alias_names(tmp_path):
    """Verifies brace exports record the EXPORTED name, not the source name."""
    src = (
        "export {\n"
        "  a,\n"
        "  b\n"
        "};\n"
        "export { x as y } from './m';\n"
        "export { alpha, beta as gamma };\n"
        "export { default as Nav } from './nav';\n"
        "export { type Foo };\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert sorted(graph["exports"]) == ["Foo", "Nav", "a", "alpha", "b", "gamma", "y"]
    assert graph["imports"] == ["./m", "./nav"]


def test_star_export_contributes_import_specifier(tmp_path):
    root = _repo(tmp_path, {"media/a.ts": "export * from './m';\n"})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./m"]
    assert graph["exports"] == []


def test_dynamic_import_variable_produces_no_import(tmp_path):
    root = _repo(tmp_path, {"media/a.ts": "import(variableName);\n"})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == []


def test_compact_import_export_forms_capture_specifiers(tmp_path):
    src = (
        "import{a}from'./x';\n"
        "import'./side';\n"
        "export{b}from'./y';\n"
        "export * from './z';\n"
        "export { x } from './w';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./x", "./side", "./y", "./z", "./w"]
    assert sorted(graph["exports"]) == ["b", "x"]


def test_require_lookbehind_blocks_member_and_dollar_calls(tmp_path):
    src = (
        "const fs = require('./util');\n"
        "foo.require('./fake');\n"
        "$require('./fake');\n"
        "bellyrequire('./req');\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./util"]


def test_string_and_template_literals_leak_nothing(tmp_path):
    src = (
        "const s = \"call import('./dyn')\";\n"
        "const t = `use import('./in-tmpl')`;\n"
        "const u = `multi\n"
        "import x from './fake2'\n"
        "export const FAKE = 1;\n"
        "`;\n"
        "const v = \"export function foo() {}\";\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == []
    assert graph["exports"] == []


def test_frontend_graph_decodes_bom_before_scanning(tmp_path):
    root = _repo(tmp_path, {"media/a.ts": "\ufeffimport x from './b';\n"})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./b"]


def test_export_default_ident_generator_and_anonymous_default(tmp_path):
    src = (
        "export default Foo;\n"
        "export default function () {}\n"
        "export default class {}\n"
        "export default () => {};\n"
        "export function* gen() {}\n"
        "export default function named() {}\n"
        "export default class App {}\n"
        "export default async function boot() {}\n"
        "export async function run() {}\n"
        "export let count = 1;\n"
        "export var flag = 2;\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert sorted(graph["exports"]) == [
        "App", "Foo", "boot", "count", "flag", "gen", "named", "run",
    ]


def test_ts_only_export_declarations_are_skipped(tmp_path):
    src = (
        "export type Flags = { a: number };\n"
        "export interface Shape { b: number }\n"
        "export enum Kind { A }\n"
        "export namespace Inner {}\n"
        "export declare const Y: number;\n"
        "export type { Foo };\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["exports"] == []


def test_frontend_graph_cache_hit_returns_stored_graph(tmp_path):
    from diffimpactscout.impact.python_analyzer import _content_hash

    src = "import './b';\n"
    root = _repo(tmp_path, {"media/a.ts": src})
    cache = SymbolCache(str(tmp_path / "c.json"))
    first = rev.analyze_frontend_graph(root, "media/a.ts", cache, set())
    assert first["imports"] == ["./b"]
    entry = cache.load_section("frontend")["media/a.ts"]
    assert entry["v"] == rev.FRONTEND_GRAPH_VERSION
    cache.save_section("frontend", {
        "media/a.ts": {
            "hash": _content_hash(src),
            "v": rev.FRONTEND_GRAPH_VERSION,
            "graph": {"imports": ["./HIT"], "exports": ["HIT"]},
        }
    })
    second = rev.analyze_frontend_graph(root, "media/a.ts", cache, set())
    assert second == {"imports": ["./HIT"], "exports": ["HIT"]}


def test_frontend_graph_rebuilds_on_hash_version_or_shape_mismatch(tmp_path):
    from diffimpactscout.impact.python_analyzer import _content_hash

    src = "import './b';\n"
    root = _repo(tmp_path, {"media/a.ts": src})
    cache = SymbolCache(str(tmp_path / "c.json"))
    digest = _content_hash(src)
    version = rev.FRONTEND_GRAPH_VERSION
    stale = {"imports": ["./STALE"], "exports": ["STALE"]}
    seeds = [
        {"hash": "not-the-hash", "v": version, "graph": stale},
        {"hash": digest, "graph": stale},
        {"hash": digest, "v": version + 1, "graph": stale},
        {"hash": digest, "v": version, "graph": {"exports": ["./STALE"]}},
        {"hash": digest, "v": version, "graph": {"imports": ["./STALE"]}},
        {"hash": digest, "v": version, "graph": {"imports": "NOTALIST", "exports": []}},
    ]
    for seed in seeds:
        cache.save_section("frontend", {"media/a.ts": dict(seed)})
        graph = rev.analyze_frontend_graph(root, "media/a.ts", cache, set())
        assert graph == {"imports": ["./b"], "exports": []}
        entry = cache.load_section("frontend")["media/a.ts"]
        assert entry["v"] == version
        assert entry["hash"] == digest
        assert entry["graph"] == {"imports": ["./b"], "exports": []}


def test_single_quoted_region_shields_double_quoted_fp(tmp_path):
    """Doc claim: a single-quoted region blanks the double quotes inside it.

    Without the single-quote branch of _LITERAL_REGION_RE, "./x" is seen
    as its own operand region and the fake require import leaks.
    """
    src = (
        "const s = 'call require(\"./x\")';\n"
        "import real from './real';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert "./x" not in graph["imports"]
    assert "./real" in graph["imports"]


def test_escape_aware_region_shields_escaped_quotes(tmp_path):
    """Doc claim: quoted regions are escape-aware.

    Without escape handling, the region ends at the first backslash-escaped
    quote and the inner require('./x') becomes visible to the import scan.
    """
    src = (
        "const s = \"say \\\"require('./x')\\\"\";\n"
        "import real from './real';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert "./x" not in graph["imports"]
    assert "./real" in graph["imports"]


def test_unterminated_backtick_region_does_not_swallow_next_import(tmp_path):
    """Doc claim: unterminated quotes are skipped (no closing backtick).

    The region must not run to EOF; the import on the following line is
    still extracted.
    """
    src = (
        "const t = `oops\n"
        "import x from './b';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./b"]


def test_quoted_export_names_survive_literal_blank(tmp_path):
    """Quoted names in brace export lists are kept and recorded unquoted.

    Covers first position ("solo"), an alias target ("str name"), and a
    post-comma non-alias position ("later").
    """
    src = (
        "export { x as \"str name\" };\n"
        "export { \"solo\" };\n"
        "export { alpha, \"later\" };\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["exports"] == ["alpha", "later", "solo", "str name"]
    assert graph["imports"] == []


def test_consecutive_and_aliased_quoted_export_names_survive(tmp_path):
    """Quoted export names in consecutive/aliased positions are all kept."""
    src = (
        "export { \"a\", \"b\" };\n"
        "export { \"c\" as \"d\" };\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["exports"] == ["a", "b", "d"]
    assert graph["imports"] == []


def test_require_with_space_before_paren_captures(tmp_path):
    """Doc claim: require ( './sp' ) (space before paren) is an operand."""
    root = _repo(tmp_path, {"media/a.ts": "const sp = require ( './sp' );\n"})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./sp"]


def test_object_literal_key_and_type_assertion_do_not_leak_imports(tmp_path):
    """Doc claim: only an "export {" brace list keeps a quoted region.

    An object-literal key or an "as" type assertion containing import-like
    text is blanked, so neither leaks a spurious import edge.
    """
    src = (
        "const o = { \"import('./evil')\": 1 };\n"
        "const q = value as \"import('./evil2')\";\n"
        "import real from './real';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./real"]
    assert graph["exports"] == []


def test_blanked_literal_containing_export_brace_does_not_leak_next_literal(tmp_path):
    """Doc claim: operand detection runs against fully-blanked text.

    A blanked string/template that merely CONTAINS "export {" must not keep
    the following literal alive (which would leak import syntax).
    """
    src = (
        "const s = \"export { \";\n"
        "const t = \"import('./evil')\";\n"
        "const u = `export {`;\n"
        "const v = \"require('./evil2')\";\n"
        "import real from './real';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./real"]


def test_import_lookbehind_blocks_member_call(tmp_path):
    """Doc claim: obj.import('./x') is not a dynamic import (lookbehind)."""
    src = (
        "obj.import('./fake');\n"
        "$import('./fake2');\n"
        "const lazy = () => import('./real');\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./real"]


def test_quoted_export_name_containing_import_syntax_does_not_leak(tmp_path):
    """Doc claim: quoted export names are blanked for the import scan.

    The import keyword may sit anywhere in the quoted name (start, after a
    space, after a comma) and still must not produce an import edge.
    """
    src = (
        "export { \"import('./evil')\" };\n"
        "export { \"require('./evil2')\" as x };\n"
        "export { alpha, \"x import('./evil3')\" };\n"
        "export { \"x require('./evil4')\" };\n"
        "import real from './real';\n"
    )
    root = _repo(tmp_path, {"media/a.ts": src})
    graph = rev.analyze_frontend_graph(root, "media/a.ts", None, set())
    assert graph["imports"] == ["./real"]


def test_js_symbol_rows_with_deleted_high(tmp_path):
    """Verifies importer rows for changed exports, High when deleted."""
    root = _repo(tmp_path, {
        "media/shop/src/app/util.ts": "export function calc(x) { return x; }\n",
        "media/shop/src/app/page.ts": "import { calc } from './util';\nconsole.log(calc(1));\n",
        "media/shop/src/app/other.ts": "console.log('unrelated calc(');\n",
    })
    import subprocess

    for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@e.co"],
                 ["add", "-A"], ["commit", "-q", "-m", "m0"], ["remote", "add", "upstream", "."],
                 ["push", "-q", "upstream", "master"]):
        subprocess.check_call(["git"] + args, cwd=root)
    # delete the exported function
    with open(os.path.join(root, "media/shop/src/app/util.ts"), "w") as fh:
        fh.write("export function calc2(x) { return x; }\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "m1"]):
        subprocess.check_call(["git", "-C", root] + args)

    from diffimpactscout.impact.diff_parser import get_file_changes

    changes = get_file_changes(root, "refs/remotes/upstream/master", False, None, None)
    cfg = {"ignore_paths": [], "use_gitignore": False,
           "impact": {"reverse": True, "template_globs": [], "frontend_globs": []}}
    extra_entities, rows, layers, unresolved = rev.js_internal_chain(
        root, cfg, cfg["impact"], changes, None, "refs/remotes/upstream/master", False, None, None,
    )
    row = next(r for r in rows if r["path"] == "media/shop/src/app/page.ts")
    assert row["category"] == "frontend"
    assert row["_deleted"] is True
    assert not any(r["path"] == "media/shop/src/app/other.ts" for r in rows)

