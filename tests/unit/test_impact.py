import ast
import hashlib
import json
import os
import subprocess
import sys

import pytest

import diffimpactscout.config as config
import diffimpactscout.scope as scope
from diffimpactscout.impact import impact

URLS = (
    "from django.urls import path\n"
    "from . import views\n"
    "\n"
    "urlpatterns = [\n"
    "    path('orders/', views.orders, name='order-list'),\n"
    "    path('api/v1/orders/', views.orders, name='api-order-list'),\n"
    "]\n"
)

VIEWS_BASE = (
    "from django.http import HttpResponse\n"
    "\n"
    "def orders(request):\n"
    "    return HttpResponse('orders')\n"
)

VIEWS_HEAD = (
    "from django.http import HttpResponse\n"
    "\n"
    "def orders(request):\n"
    "    return HttpResponse('orders updated')\n"
)

MODELS = (
    "from django.db import models\n"
    "\n"
    "class Book(models.Model):\n"
    "    status = models.CharField(max_length=20)\n"
)

MODELS_HEAD = (
    "from django.db import models\n"
    "\n"
    "class Book(models.Model):\n"
    "    status = models.CharField(max_length=20, default='new')\n"
)

DESCRIBE = (
    "def describe(book):\n"
    "    return book.status\n"
)

UTILS = (
    "def helper():\n"
    "    return 'x'\n"
)

UTILS_NEW = (
    "def helper():\n"
    "    return 'x'\n"
    "\n"
    "def new_util():\n"
    "    return 'y'\n"
)

TEMPLATE = "<a href=\"{% url 'order-list' %}\">orders</a>\n"

TS = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
    "this.http.get(environment.apiUrl + '/api/v1/dynamic/');\n"
)

TS_SINGLE = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
)

TS_PLUS_ONE = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
    "this.http.get('/api/v1/orders/');\n"
)

TS_SAME_LINE = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/'); this.http.get('/api/v1/orders/'); this.http.get('/api/v1/orders/');\n"
)

TS_DIFF_LINES = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
    "\n"
    "this.http.get('/api/v1/orders/');\n"
)

TEMPLATE_DUP = (
    "<a href=\"{% url 'order-list' %}\">orders</a>\n"
    "<a href=\"{% url 'order-list' %}\">again</a>\n"
)

CBV_URLS = (
    "from django.urls import path\n"
    "from . import views\n"
    "\n"
    "urlpatterns = [\n"
    "    path('orders/', views.OrderList.as_view(), name='order-list'),\n"
    "]\n"
)

CBV_VIEWS_BASE = (
    "from django.http import HttpResponse\n"
    "from django.views import View\n"
    "\n"
    "class OrderList(View):\n"
    "    def get(self, request):\n"
    "        return HttpResponse('orders')\n"
)

CBV_VIEWS_HEAD = (
    "from django.http import HttpResponse\n"
    "from django.views import View\n"
    "\n"
    "class OrderList(View):\n"
    "    def get(self, request):\n"
    "        return HttpResponse('orders updated')\n"
)

RENAME_SRC = (
    "def old_helper():\n"
    "    return 1\n"
    "\n"
    "def keep_me():\n"
    "    return 2\n"
)

RENAME_SRC_NEW = (
    "def new_helper():\n"
    "    return 1\n"
    "\n"
    "def keep_me():\n"
    "    return 2\n"
)

RENAME_SERVICE = (
    "from app.legacy import old_helper\n"
    "\n"
    "def call_it():\n"
    "    return old_helper()\n"
)

UNRELATED = (
    "def unrelated_helper():\n"
    "    return 99\n"
)

GADGET = (
    "def gadget():\n"
    "    return 'node_modules'\n"
)

DJANGO_CFG = {
    "impact": {
        "profile": "django",
        "urls_globs": ["**/urls.py"],
        "template_globs": ["**/templates/**/*.html"],
        "frontend_globs": ["**/src/**/*.ts"],
    }
}


def _git_env(extra=None):
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if extra:
        env.update(extra)
    return env


def _git(*args, cwd, env=None):
    return subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + list(args),
        cwd=cwd,
        env=env or _git_env(),
    )


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git("init", cwd=repo)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    return repo


def _write(repo, filename, content):
    path = os.path.join(repo, filename)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w") as fh:
        fh.write(content)


def _commit(repo, msg):
    _git("add", "-A", cwd=repo, env=_git_env())
    _git("commit", "-m", msg, cwd=repo, env=_git_env())
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _anchor(repo, sha):
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)
    return "refs/remotes/upstream/master"


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "default_cache_dir", lambda: str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _non_tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _count_category(out, category):
    needle = "| %s |" % category
    return sum(1 for line in out.splitlines() if needle in line)


def _build_ts_repo(tmp_path, ts_src):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "src/orders.service.ts", ts_src)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _commit(repo, "change views")
    head = _sha(repo)
    return repo, base, head


def _build_django_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/models.py", MODELS)
    _write(repo, "app/utils.py", UTILS)
    _write(repo, "app/templates/orders.html", TEMPLATE)
    _write(repo, "src/orders.service.ts", TS)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _commit(repo, "change views")
    head = _sha(repo)
    return repo, base, head


def _build_plain_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/models.py", MODELS)
    _write(repo, "app/views.py", DESCRIBE)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/models.py", MODELS_HEAD)
    _commit(repo, "change model")
    head = _sha(repo)
    return repo, base, head


def _build_deleted_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, "app/__init__.py", "")
    _write(
        repo,
        "app/legacy.py",
        "def legacy_helper():\n"
        "    return 1\n"
        "\n"
        "def keep_me():\n"
        "    return 2\n",
    )
    _write(
        repo,
        "app/service.py",
        "from app.legacy import legacy_helper\n"
        "\n"
        "def call_it():\n"
        "    return legacy_helper()\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/legacy.py", "def keep_me():\n    return 2\n")
    _commit(repo, "delete helper")
    head = _sha(repo)
    return repo, base, head


def _count_parse(monkeypatch):
    calls = {"n": 0, "hashes": {}}
    real = ast.parse

    def counting(src, *args, **kwargs):
        calls["n"] += 1
        if isinstance(src, str):
            h = hashlib.sha1(src.encode("utf-8")).hexdigest()
            calls["hashes"][h] = calls["hashes"].get(h, 0) + 1
        return real(src, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    return calls


def test_run_impact_cross_layer_report(tmp_path, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "orders (attr at app/urls.py:5)" in out
    assert (
        "{% url 'order-list' %} at app/templates/orders.html" in out
    )
    assert 'http.get("/api/v1/orders/") at src/orders.service.ts:2' in out
    assert "| template |" in out
    assert "| frontend |" in out
    assert "| High |" in out
    assert "1 changed file(s)" in out


def test_run_impact_model_field_plain(tmp_path, capsys):
    repo, base, _head = _build_plain_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "status (attr at app/views.py:2)" in out
    assert "| Medium | verify |" in out
    assert "1 changed file(s)" in out


def test_run_impact_deleted_function(tmp_path, capsys):
    repo, base, _head = _build_deleted_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "verify dangling references" in out
    assert "legacy_helper (import at app/service.py:1)" in out
    assert "legacy_helper (name at app/service.py:4)" in out
    assert "| High | verify dangling references |" in out


def test_run_impact_json_output(tmp_path, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["changed_count"] == 1
    assert data["rows"]
    assert any(r["category"] == "template" for r in data["rows"])
    assert any(r["category"] == "frontend" for r in data["rows"])
    unresolved_paths = [u.get("path") for u in data["unresolved"]]
    assert "/api/v1/dynamic/" in unresolved_paths


def test_run_impact_strict_blocks(tmp_path, monkeypatch, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert impact.run_impact(repo, cfg) == 1
    out = capsys.readouterr().out
    assert "Impact Analysis Report" in out


def test_run_impact_skip_env_returns_zero_no_output(tmp_path, monkeypatch, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    assert impact.run_impact(repo, cfg) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_impact_unresolved_dynamic_not_false_matched(tmp_path, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "Unresolved references (manual check required)" in out
    assert "/api/v1/dynamic/" in out
    assert 'http.get("/api/v1/dynamic/")' not in out


def test_run_impact_cache_skips_reparse_on_second_run(tmp_path, monkeypatch):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    utils_hash = hashlib.sha1(UTILS.encode("utf-8")).hexdigest()
    calls = _count_parse(monkeypatch)
    assert impact.run_impact(repo, cfg) == 0
    cache_file = os.path.join(repo, ".impact_analysis_cache.json")
    assert os.path.exists(cache_file)
    assert calls["hashes"].get(utils_hash, 0) == 1
    calls["hashes"] = {}
    calls["n"] = 0
    assert impact.run_impact(repo, cfg) == 0
    assert calls["hashes"].get(utils_hash, 0) == 0


def test_run_impact_fast_mode(tmp_path, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, fast=True) == 0
    captured = capsys.readouterr()
    assert "fast mode" in captured.err
    assert "orders (attr at app/urls.py:5)" in captured.out
    assert "| template |" not in captured.out
    assert "| frontend |" not in captured.out


def test_run_impact_pre_commit_env_range(tmp_path, monkeypatch, capsys):
    repo, _base, head = _build_django_repo(tmp_path)
    cfg = config.load_config(repo)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "HEAD~1")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "orders (attr at app/urls.py:5)" in out


def test_run_impact_env_refs_yield_to_resolved_anchor(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/utils.py", UTILS)
    _write(repo, "app/templates/orders.html", TEMPLATE)
    _write(repo, "src/orders.service.ts", TS)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _commit(repo, "change views")
    range_from = _sha(repo)
    _write(repo, "app/utils.py", UTILS_NEW)
    _commit(repo, "change utils")
    head = _sha(repo)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", range_from)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "orders (attr at app/urls.py:5)" in out
    assert "2 changed file(s)" in out


def test_run_impact_staged(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/templates/orders.html", TEMPLATE)
    _write(repo, "src/orders.service.ts", TS)
    _commit(repo, "base")
    _write(repo, "app/views.py", VIEWS_HEAD)
    _git("add", "app/views.py", cwd=repo)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, staged=True) == 0
    out = capsys.readouterr().out
    assert "orders (attr at app/urls.py:5)" in out
    assert "{% url 'order-list' %}" in out


def test_run_impact_django_cbv_linked(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", CBV_URLS)
    _write(repo, "app/views.py", CBV_VIEWS_BASE)
    _write(repo, "app/templates/orders.html", TEMPLATE)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", CBV_VIEWS_HEAD)
    _commit(repo, "change cbv")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "{% url 'order-list' %} at app/templates/orders.html" in out
    assert _count_category(out, "template") == 1
    assert "OrderList" in out


def test_run_impact_frontend_same_line_dedup(tmp_path, capsys):
    repo, base, _head = _build_ts_repo(tmp_path, TS_SAME_LINE)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert _count_category(out, "frontend") == 1
    assert out.count('http.get("/api/v1/orders/") at src/orders.service.ts:2') == 1


def test_run_impact_frontend_duplicate_calls_distinct_lines(tmp_path, capsys):
    repo, base, _head = _build_ts_repo(tmp_path, TS_DIFF_LINES)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert _count_category(out, "frontend") == 2
    assert 'http.get("/api/v1/orders/") at src/orders.service.ts:2' in out
    assert 'http.get("/api/v1/orders/") at src/orders.service.ts:4' in out


def test_run_impact_frontend_rows_monotonic(tmp_path, capsys):
    repo, base, _head = _build_ts_repo(tmp_path, TS_SINGLE)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    before = _count_category(capsys.readouterr().out, "frontend")
    _write(repo, "src/orders.service.ts", TS_PLUS_ONE)
    _commit(repo, "add ts call")
    assert impact.run_impact(repo, cfg) == 0
    after_out = capsys.readouterr().out
    after = _count_category(after_out, "frontend")
    assert after == before + 1
    assert 'http.get("/api/v1/orders/") at src/orders.service.ts:3' in after_out


def test_run_impact_template_duplicate_tag_dedup(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/templates/orders.html", TEMPLATE_DUP)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _commit(repo, "change views")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert _count_category(out, "template") == 1
    assert out.count("{% url 'order-list' %}") == 1


def test_run_impact_tty_accept_proceeds(tmp_path, monkeypatch, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "y\n")
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "Proceed with push" in out


def test_run_impact_tty_decline_blocks(tmp_path, monkeypatch, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "n\n")
    assert impact.run_impact(repo, cfg) == 1


def test_run_impact_tty_empty_input_accepts(tmp_path, monkeypatch, capsys):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "\n")
    assert impact.run_impact(repo, cfg) == 0


def test_run_impact_rename_only(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/legacy.py", RENAME_SRC)
    _write(repo, "app/service.py", RENAME_SERVICE)
    _commit(repo, "base")
    base = _sha(repo)
    _git("mv", "app/legacy.py", "app/renamed.py", cwd=repo)
    _write(repo, "app/renamed.py", RENAME_SRC_NEW)
    _git("add", "app/renamed.py", cwd=repo)
    _commit(repo, "rename helper")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "old_helper (import at app/service.py:1)" in out
    assert "old_helper (name at app/service.py:4)" in out
    assert "| High | verify dangling references |" in out


def test_run_impact_non_python_change_set(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "README.md", "# base\n")
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "README.md", "# updated\n")
    _commit(repo, "docs change")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "Impact Analysis Report" in out
    assert "1 changed file(s)" in out
    assert _count_category(out, "python") == 0


def test_run_impact_empty_change_set(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/templates/orders.html", TEMPLATE)
    _commit(repo, "base")
    base = _sha(repo)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "0 changed file(s)" in out
    assert _count_category(out, "python") == 0
    assert _count_category(out, "template") == 0


def test_run_impact_fast_mode_excludes_unrelated(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/unrelated.py", UNRELATED)
    _write(repo, "node_modules/pkg/gadget.py", GADGET)
    _write(repo, "src/orders.service.ts", TS)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _commit(repo, "change views")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, fast=True) == 0
    captured = capsys.readouterr()
    assert "fast mode" in captured.err
    assert "Impact Analysis Report" in captured.out
    with open(os.path.join(repo, ".impact_analysis_cache.json")) as fh:
        data = json.load(fh)
    assert "app/unrelated.py" not in data
    assert "node_modules/pkg/gadget.py" not in data