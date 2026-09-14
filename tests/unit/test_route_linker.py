import ast
import os

import diffimpactscout.impact.route_linker as rl
from diffimpactscout.impact.route_linker import Route

URLS = (
    "from django.urls import path, re_path\n"
    "from django.conf.urls import url\n"
    "from . import views\n"
    "\n"
    "urlpatterns = [\n"
    "    path('orders/', views.OrderList.as_view(), name='order-list'),\n"
    "    re_path(r'^items/(?P<pk>[0-9]+)/$', views.items, name='item-detail'),\n"
    "    path('reports/', views.reports, name='report-list'),\n"
    "    url(r'^legacy/$', views.legacy, name='legacy-list'),\n"
    "]\n"
)

FASTAPI = (
    "from fastapi import FastAPI\n"
    "from fastapi import APIRouter\n"
    "\n"
    "app = FastAPI()\n"
    "router = APIRouter()\n"
    "\n"
    "@router.get('/items')\n"
    "def list_items():\n"
    "    return []\n"
    "\n"
    "@app.post('/items')\n"
    "def create_item():\n"
    "    return {}\n"
    "\n"
    "@router.get('/items/{item_id}')\n"
    "def get_item(item_id: int):\n"
    "    return {}\n"
    "\n"
    "@app.delete('/items/{item_id}')\n"
    "def delete_item(item_id: int):\n"
    "    return {}\n"
)

TEMPLATES = {
    "base.html": (
        "<a href=\"{% url 'order-list' %}\">orders</a>\n"
        "<a href=\"{% url \"item-detail\" %}\">item</a>\n"
        "<a href=\"{% url 'no-such-route' %}\">broken</a>\n"
        "<p>not a url tag</p>\n"
    ),
}

FRONTEND = {
    "orders.ts": (
        "import { HttpClient } from '@angular/common/http';\n"
        "this.http.get('/api/v1/orders/');\n"
        "http.post('/api/v1/reports/');\n"
        "HttpClient.put('/api/v1/orders/5/');\n"
        "$http.delete('/api/v1/orders/5/');\n"
        "this.http.patch(environment.apiUrl + '/api/v1/orders/');\n"
        "const opaque = '/api/v1/orders/opaque';\n"
    ),
}


def _write(root, rel, content):
    full = os.path.join(root, rel)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)
    return rel


def _build_tree(tmp_path):
    _write(str(tmp_path), "orders/urls.py", URLS)
    for name, content in TEMPLATES.items():
        _write(str(tmp_path), "app/templates/base/" + name, content)
    for name, content in FRONTEND.items():
        _write(str(tmp_path), "src/app/" + name, content)


def _ref(file, path, dynamic=False, line=1, method="get"):
    return {
        "file": file,
        "ref": path,
        "dynamic": dynamic,
        "line": line,
        "method": method,
    }


def test_str_value_constant():
    """Verifies that _str_value extracts a string constant's value."""
    node = ast.parse("x = 'orders/'").body[0].value
    assert rl._str_value(node) == "orders/"


def test_str_value_nonstring():
    """Checks that _str_value returns None for non-string nodes."""
    node = ast.parse("x = 123").body[0].value
    assert rl._str_value(node) is None


def test_iter_files_globs():
    """Checks that _iter_files returns no files for a nonexistent root."""
    root = "/nonexistent/dir"
    assert list(rl._iter_files(root, ["**/urls.py"], (".py",))) == []


def test_iter_files_walk(tmp_path):
    """Verifies that _iter_files walks the tree and applies glob filters."""
    _write(str(tmp_path), "a/b/urls.py", "x = 1\n")
    _write(str(tmp_path), "a/c.py", "y = 1\n")
    _write(str(tmp_path), "plain.html", "<p></p>\n")
    py = sorted(rl._iter_files(str(tmp_path), ["**/urls.py"], (".py",)))
    assert py == ["a/b/urls.py"]
    html = sorted(rl._iter_files(str(tmp_path), ["**/templates/**/*.html"], (".html",)))
    assert html == []
    all_py = sorted(rl._iter_files(str(tmp_path), ["**/*.py"], (".py",)))
    assert all_py == ["a/b/urls.py", "a/c.py"]


def test_extract_django_routes(tmp_path):
    """Verifies that Django routes are extracted with name, path, and handler."""
    _build_tree(tmp_path)
    routes = rl.extract_django_routes(str(tmp_path), ["**/urls.py"])
    by_name = {r.name: r for r in routes}
    assert by_name["order-list"].path == "orders/"
    assert by_name["order-list"].handler == "OrderList.as_view"
    assert by_name["order-list"].module == "orders/urls.py"
    assert by_name["item-detail"].handler == "items"
    assert by_name["report-list"].path == "reports/"
    assert by_name["legacy-list"].handler == "legacy"
    assert len(routes) == 4


def test_extract_django_routes_empty_globs(tmp_path):
    """Checks that extract_django_routes returns no routes for empty globs."""
    _build_tree(tmp_path)
    assert rl.extract_django_routes(str(tmp_path), []) == []


def test_extract_fastapi_routes(tmp_path):
    """Verifies that FastAPI routes are extracted with their handler paths."""
    _write(str(tmp_path), "main.py", FASTAPI)
    routes = rl.extract_fastapi_routes(str(tmp_path), ["**/*.py"])
    paths = {r.handler: r.path for r in routes}
    assert paths["list_items"] == "/items"
    assert paths["create_item"] == "/items"
    assert paths["get_item"] == "/items/{item_id}"
    assert paths["delete_item"] == "/items/{item_id}"
    assert len(routes) == 4


def test_fastapi_base_allowlist(tmp_path):
    """Checks that only routes on allowlisted base objects are extracted."""
    src = (
        "config = Config()\n"
        "db = Database()\n"
        "router = APIRouter()\n"
        "app = FastAPI()\n"
        "api = APIRouter()\n"
        "\n"
        "@config.get('/cfg')\n"
        "def read_cfg():\n"
        "    return {}\n"
        "\n"
        "@db.get('/cfg')\n"
        "def read_db():\n"
        "    return {}\n"
        "\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n"
        "\n"
        "@app.post('/y')\n"
        "def create_y():\n"
        "    return {}\n"
        "\n"
        "@api.put('/z')\n"
        "def put_z():\n"
        "    return {}\n"
    )
    _write(str(tmp_path), "main.py", src)
    routes = rl.extract_fastapi_routes(str(tmp_path), ["**/*.py"])
    handlers = {r.handler: r.path for r in routes}
    assert "read_cfg" not in handlers
    assert "read_db" not in handlers
    assert handlers["list_items"] == "/items"
    assert handlers["create_y"] == "/y"
    assert handlers["put_z"] == "/z"
    assert len(routes) == 3


def test_extract_template_refs(tmp_path):
    """Verifies that Django template url tags are extracted as references."""
    _build_tree(tmp_path)
    refs = rl.extract_template_refs(str(tmp_path), ["**/templates/**/*.html"])
    names = {name for _f, name in refs}
    assert names == {"order-list", "item-detail", "no-such-route"}
    assert all(f.startswith("app/templates/") for f, _n in refs)


def test_extract_template_refs_flat_template(tmp_path):
    """Checks that a flat template's url tag is captured as a reference."""
    _write(
        str(tmp_path),
        "app/templates/orders.html",
        "<a href=\"{% url 'order-list' %}\">orders</a>\n",
    )
    refs = rl.extract_template_refs(str(tmp_path), ["**/templates/**/*.html"])
    assert refs == [("app/templates/orders.html", "order-list")]


def test_match_template_refs(tmp_path):
    """Verifies that template references match to known routes or stay unresolved."""
    _build_tree(tmp_path)
    refs = rl.extract_template_refs(str(tmp_path), ["**/templates/**/*.html"])
    routes = [
        Route("order-list", "orders/", "OrderList.as_view", "orders/urls.py"),
        Route("item-detail", "items/", "items", "orders/urls.py"),
    ]
    matched, unresolved = rl.match_template_refs(refs, routes)
    matched_names = {m["ref"] for m in matched}
    assert matched_names == {"order-list", "item-detail"}
    unresolved_names = {u["ref"] for u in unresolved}
    assert unresolved_names == {"no-such-route"}


def test_extract_frontend_refs(tmp_path):
    """Verifies that frontend HTTP calls are extracted as static and dynamic refs."""
    _build_tree(tmp_path)
    refs = rl.extract_frontend_refs(str(tmp_path), ["**/src/**/*.ts"])
    static = [r["ref"] for r in refs if not r["dynamic"]]
    dynamic = [(r["file"], r["ref"]) for r in refs if r["dynamic"]]
    assert "/api/v1/orders/" in static
    assert "/api/v1/reports/" in static
    assert "/api/v1/orders/5/" in static
    assert not any(p == "/api/v1/orders/opaque" for p in static)
    assert dynamic == [("src/app/orders.ts", "/api/v1/orders/")]
    assert all(
        "file" in r and "ref" in r and "line" in r and "method" in r for r in refs
    )


def test_extract_frontend_refs_ignores_comments(tmp_path):
    """Checks that commented-out HTTP calls are not extracted as refs."""
    src = (
        "// this.http.get('/fake/')\n"
        "/* this.http.get('/blocked/') and http.post('/x') */\n"
        "this.http.get('/api/v1/orders/');\n"
        "const url = 'http://example.com/path';\n"
    )
    _write(str(tmp_path), "app.ts", src)
    refs = rl.extract_frontend_refs(str(tmp_path), ["**/*.ts"])
    static = [r["ref"] for r in refs if not r["dynamic"]]
    assert static == ["/api/v1/orders/"]
    assert not any("/fake/" in p for p in static)
    assert not any("/blocked/" in p for p in static)
    assert not any("/x" in p for p in static)


def test_extract_frontend_refs_real_lines(tmp_path):
    """Verifies that extracted refs report the real source line number."""
    src = (
        "import { HttpClient } from '@angular/common/http';\n"
        "// comment line\n"
        "\n"
        "this.http.get('/api/v1/orders/');\n"
    )
    _write(str(tmp_path), "app.ts", src)
    refs = rl.extract_frontend_refs(str(tmp_path), ["**/*.ts"])
    static = [r for r in refs if not r["dynamic"]]
    assert [(r["line"], r["method"]) for r in static] == [(4, "get")]


def test_extract_frontend_refs_block_comment_preserves_lines(tmp_path):
    """Checks that block comments preserve accurate line numbers for refs."""
    src = (
        "/* this.http.get('/blocked/')\n"
        "   still blocked */\n"
        "this.http.get('/api/v1/orders/');\n"
    )
    _write(str(tmp_path), "app.ts", src)
    refs = rl.extract_frontend_refs(str(tmp_path), ["**/*.ts"])
    static = [r for r in refs if not r["dynamic"]]
    assert [(r["line"], r["method"]) for r in static] == [(3, "get")]


def test_match_endpoints_wildcard_and_prefix(tmp_path):
    """Verifies that refs match routes via wildcard and prefix patterns."""
    routes = [Route("item-detail", "/api/v1/orders/<int:id>/", "items", "u.py")]
    refs = [
        _ref("f.ts", "/api/v1/orders/5/"),
        _ref("f.ts", "/api/v1/orders/"),
    ]
    matched, unresolved = rl.match_endpoints(refs, routes)
    assert len(matched) == 2
    assert unresolved == []
    assert all(m["route"].name == "item-detail" for m in matched)
    assert [m["line"] for m in matched] == [1, 1]


def test_match_endpoints_exact_and_dynamic(tmp_path):
    """Checks that exact, dynamic, and unknown refs are handled by match_endpoints."""
    routes = [
        Route("order-list", "/api/v1/orders/", "OrderList.as_view", "u.py"),
        Route("report-list", "/api/v1/reports/", "reports", "u.py"),
    ]
    refs = [
        _ref("f.ts", "/api/v1/orders/"),
        _ref("f.ts", "/api/v1/orders/", dynamic=True),
        _ref("f.ts", None, dynamic=True),
        _ref("f.ts", "/api/v1/unknown/"),
    ]
    matched, unresolved = rl.match_endpoints(refs, routes)
    matched_refs = [(m["file"], m["ref"]) for m in matched]
    assert ("f.ts", "/api/v1/orders/") in matched_refs
    unresolved_paths = {u["path"] for u in unresolved}
    assert None in unresolved_paths
    assert "/api/v1/unknown/" in unresolved_paths


def test_match_endpoints_leading_slash_tolerant(tmp_path):
    """Verifies that match_endpoints tolerates a leading slash difference."""
    routes = [
        Route("order-list", "api/v1/orders/", "OrderList.as_view", "u.py"),
        Route("report-list", "api/v1/reports/", "reports", "u.py"),
    ]
    refs = [
        _ref("f.ts", "/api/v1/orders/"),
        _ref("f.ts", "/api/v1/reports/"),
    ]
    matched, unresolved = rl.match_endpoints(refs, routes)
    assert unresolved == []
    assert len(matched) == 2
    assert matched[0]["ref"] == "/api/v1/orders/"
    assert matched[1]["ref"] == "/api/v1/reports/"


def test_match_endpoints_prefix_boundary_nonmatch(tmp_path):
    """Checks that a partial prefix at a boundary does not match the route."""
    routes = [Route("order-list", "/api/v1/orders/", "OrderList.as_view", "u.py")]
    refs = [_ref("f.ts", "/api/v1/order")]
    matched, unresolved = rl.match_endpoints(refs, routes)
    assert matched == []
    assert len(unresolved) == 1
    assert unresolved[0]["path"] == "/api/v1/order"


def test_re_path_route_extracted_as_regex_source(tmp_path):
    """Verifies that re_path routes store their raw regex as the path."""
    _build_tree(tmp_path)
    routes = rl.extract_django_routes(str(tmp_path), ["**/urls.py"])
    by_name = {r.name: r for r in routes}
    assert by_name["item-detail"].path == r"^items/(?P<pk>[0-9]+)/$"
    matched, unresolved = rl.match_endpoints(
        [_ref("f.ts", "/api/v1/order")], [by_name["item-detail"]]
    )
    assert matched == []
    assert len(unresolved) == 1
    assert unresolved[0]["path"] == "/api/v1/order"


def test_template_url_tag_with_args_not_captured(tmp_path):
    """Checks that url tags with extra arguments are not captured as refs."""
    _write(
        str(tmp_path),
        "app/templates/orders.html",
        "<a href=\"{% url 'order-list' page 2 %}\">orders</a>\n",
    )
    refs = rl.extract_template_refs(str(tmp_path), ["**/templates/**/*.html"])
    assert refs == []


def test_route_class():
    """Verifies that the Route class stores name, path, handler, and module."""
    r = Route("order-list", "orders/", "OrderList.as_view", "urls.py")
    assert r.name == "order-list"
    assert r.path == "orders/"
    assert r.handler == "OrderList.as_view"
    assert r.module == "urls.py"
