import ast
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

import diffimpactscout.config as config
import diffimpactscout.scope as scope
from diffimpactscout.impact import impact
from diffimpactscout.impact import python_analyzer as pa
from diffimpactscout.impact import route_linker

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
    "from app.models import Book\n"
    "\n"
    "def describe():\n"
    "    return Book.status\n"
)

DESCRIBE_HEAD = (
    "from app.models import Book\n"
    "\n"
    "def describe():\n"
    "    return Book.status or ''\n"
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

FASTAPI_CFG = {
    "impact": {
        "profile": "fastapi",
        "urls_globs": ["**/*.py"],
        "template_globs": [],
        "frontend_globs": ["**/src/**/*.ts"],
    }
}

FASTAPI_MAIN_BASE = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "\n"
    "@app.get('/api/v1/orders/')\n"
    "def read_orders():\n"
    "    return []\n"
)

FASTAPI_MAIN_HEAD = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "\n"
    "@app.get('/api/v1/orders/')\n"
    "def read_orders():\n"
    "    return ['orders']\n"
)

FASTAPI_TS = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
)


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
    # ASCII table data rows begin with the row index; count rows whose
    # category column matches the given category.
    return sum(
        1
        for line in out.splitlines()
        if re.match(r"^\s+\d+\s", line) and category in line
    )


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


def _build_fastapi_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(FASTAPI_CFG))
    _write(repo, "main.py", FASTAPI_MAIN_BASE)
    _write(repo, "src/orders.service.ts", FASTAPI_TS)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "main.py", FASTAPI_MAIN_HEAD)
    _commit(repo, "change handler")
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


def _build_reachability_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(
        repo,
        "app/urls.py",
        "from django.urls import path\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('orders/', views.orders, name='order-list'),\n"
        "]\n",
    )
    _write(
        repo,
        "app/views.py",
        "from django.http import HttpResponse\n"
        "from app.helpers import helper_a\n"
        "\n"
        "def orders(request):\n"
        "    return HttpResponse(helper_a())\n",
    )
    _write(
        repo,
        "app/helpers.py",
        "from app.models import Book\n"
        "\n"
        "def helper_a():\n"
        "    return Book.status\n",
    )
    _write(
        repo,
        "app/orphan.py",
        "from app.models import Book\n"
        "\n"
        "def orphan():\n"
        "    return Book.status\n",
    )
    _write(repo, "app/management/__init__.py", "")
    _write(repo, "app/management/commands/__init__.py", "")
    _write(
        repo,
        "app/management/commands/run_thing.py",
        "from django.core.management.base import BaseCommand\n"
        "from app.models import Book\n"
        "\n"
        "class Command(BaseCommand):\n"
        "    def handle(self, *args, **options):\n"
        "        return Book.status\n",
    )
    _write(
        repo,
        "app/models.py",
        "from django.db import models\n"
        "\n"
        "class Book(models.Model):\n"
        "    status = models.CharField(max_length=20)\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(
        repo,
        "app/models.py",
        "from django.db import models\n"
        "\n"
        "class Book(models.Model):\n"
        "    status = models.CharField(max_length=20, default='new')\n",
    )
    _commit(repo, "change model")
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
    """Verifies that the impact report links changed views to URL, template, and frontend refs."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "orders (attr at 5)" in out
    assert (
        "{% url 'order-list' %}" in out
    )
    assert 'http.get("/api/v1/orders/") at 2' in out
    assert "template" in out
    assert "frontend" in out
    assert "High" in out
    assert "1 changed file(s)" in out
    assert "Severity rationale" in out
    assert "multiple layers (frontend + python + template)" in out


def test_run_impact_model_field_plain(tmp_path, capsys):
    """Verifies that a changed model field is reported with medium severity."""
    repo, base, _head = _build_plain_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "status (attr at 4)" in out
    assert "Medium" in out
    assert "update call site" in out
    assert "referenced from a file outside the change-set" in out
    assert "1 changed file(s)" in out


def test_run_impact_summary_counts_only_impacted_files(tmp_path, capsys):
    """Verifies the summary excludes changed files from impacted count and Top-5.

    When the only consumer of a changed symbol is itself in the change-set
    (badge Changed), the report must count zero impacted files and must not
    list that changed file in the Top impacted paths.
    """
    repo = _make_repo(tmp_path)
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/models.py", MODELS)
    _write(repo, "app/views.py", DESCRIBE)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/models.py", MODELS_HEAD)
    _write(repo, "app/views.py", DESCRIBE_HEAD)
    _commit(repo, "change model and consumer")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    head = "\n".join(out.splitlines()[:25])
    assert "changed 2 files vs impacted 0 files" in head
    top_lines = [l for l in out.splitlines() if l.strip().startswith("- ")]
    assert not any("app/views.py" in l for l in top_lines)


def test_run_impact_deleted_function(tmp_path, capsys):
    """Verifies that a deleted function is reported with a remove-import action."""
    repo, base, _head = _build_deleted_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "remove import" in out
    assert "legacy_helper (import at 1)" in out
    assert "legacy_helper (name at 4)" in out
    assert "High" in out
    assert "Severity rationale" in out
    assert "deleted or renamed" in out


def test_run_impact_inline_edit_keeps_siblings(tmp_path, capsys):
    """Verifies that editing one function does not flag unchanged siblings as deleted."""
    repo = _make_repo(tmp_path)
    _write(
        repo,
        "app/legacy.py",
        "def target():\n"
        "    return 1\n"
        "\n"
        "def sibling():\n"
        "    return 2\n",
    )
    _write(
        repo,
        "app/service.py",
        "from app.legacy import target\n"
        "\n"
        "def call_it():\n"
        "    return target()\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(
        repo,
        "app/legacy.py",
        "def target():\n"
        "    return 10\n"
        "\n"
        "def sibling():\n"
        "    return 2\n",
    )
    _commit(repo, "edit target body")
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "target" in out
    assert "sibling (deleted or renamed in this change-set)" not in out
    assert "sibling" not in out


def test_run_impact_json_output(tmp_path, capsys):
    """Verifies that JSON output contains the expected structured impact data."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["changed_count"] == 1
    assert data["rows"]
    assert all("reason" in r for r in data["rows"])
    assert any(r["category"] == "template" for r in data["rows"])
    assert any(r["category"] == "frontend" for r in data["rows"])
    assert [u["reason"] for u in data["unresolved"]] == ["unknown-url"]
    assert data["unresolved_by_reason"] == {"unknown-url": 1}
    assert "cache_hit_rate" in data


def test_run_impact_strict_blocks(tmp_path, monkeypatch, capsys):
    """Verifies that strict mode blocks with a non-zero exit code."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert impact.run_impact(repo, cfg) == 1
    out = capsys.readouterr().out
    assert "Impact Analysis Report" in out


def test_run_impact_skip_env_returns_zero_no_output(tmp_path, monkeypatch, capsys):
    """Verifies that the skip env var returns zero and produces no output."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    assert impact.run_impact(repo, cfg) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_impact_unmatched_frontend_ref_unresolved(tmp_path, capsys):
    """Verifies an unmatched frontend URL is reported unresolved, never linked."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "src/orders.service.ts: /api/v1/dynamic/ (unknown-url)" in out
    assert "Unresolved: 1 (unknown-url: 1)" in out
    assert 'http.get("/api/v1/dynamic/")' not in out


def test_run_impact_cache_skips_reparse_on_second_run(tmp_path, monkeypatch):
    """Verifies that the cache prevents reparsing on a second impact run."""
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
    """Verifies that fast mode skips template and frontend analysis."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, fast=True) == 0
    captured = capsys.readouterr()
    assert "fast mode" in captured.err
    assert "orders (attr at 5)" in captured.out
    assert not any(c in captured.out for c in ("template", "frontend"))


def test_run_impact_pre_commit_env_range(tmp_path, monkeypatch, capsys):
    """Verifies that pre-commit ref env vars are honored as the change range."""
    repo, _base, head = _build_django_repo(tmp_path)
    cfg = config.load_config(repo)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "HEAD~1")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "orders (attr at 5)" in out


def test_run_impact_env_refs_yield_to_resolved_anchor(tmp_path, monkeypatch, capsys):
    """Verifies that a resolved anchor takes precedence over env-provided refs."""
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
    assert "orders (attr at 5)" in out
    assert "2 changed file(s)" in out


def test_run_impact_staged(tmp_path, capsys):
    """Verifies that staged changes are analyzed for impact."""
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
    assert "orders (attr at 5)" in out
    assert "{% url 'order-list' %}" in out


def test_run_impact_django_cbv_linked(tmp_path, capsys):
    """Verifies that Django class-based views are linked to URL and template refs."""
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
    assert "{% url 'order-list' %}" in out
    assert _count_category(out, "template") == 1
    assert "OrderList" in out


def test_run_impact_frontend_same_line_dedup(tmp_path, capsys):
    """Verifies that duplicate frontend refs on the same line are deduplicated."""
    repo, base, _head = _build_ts_repo(tmp_path, TS_SAME_LINE)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert _count_category(out, "frontend") == 1
    # Dedup verified at the row level: exactly one table row references the
    # call; the severity rationale block repeats it, so only table rows count.
    table_rows = [
        line
        for line in out.splitlines()
        if re.match(r"^\s+\d+\s", line)
        and 'http.get("/api/v1/orders/") at 2' in line
    ]
    assert len(table_rows) == 1


def test_run_impact_frontend_duplicate_calls_distinct_lines(tmp_path, capsys):
    """Verifies that duplicate frontend refs on distinct lines are both reported."""
    repo, base, _head = _build_ts_repo(tmp_path, TS_DIFF_LINES)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert _count_category(out, "frontend") == 2
    assert 'http.get("/api/v1/orders/") at 2' in out
    assert 'http.get("/api/v1/orders/") at 4' in out


def test_run_impact_frontend_rows_monotonic(tmp_path, capsys):
    """Verifies that frontend findings grow monotonically with added calls."""
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
    assert 'http.get("/api/v1/orders/") at 3' in after_out


def test_run_impact_template_duplicate_tag_dedup(tmp_path, capsys):
    """Verifies that duplicate template tags are deduplicated."""
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
    # Dedup verified at the row level: exactly one table row carries the tag;
    # the severity rationale block repeats it, so only table rows count.
    table_rows = [
        line
        for line in out.splitlines()
        if re.match(r"^\s+\d+\s", line) and "{% url 'order-list' %}" in line
    ]
    assert len(table_rows) == 1


def test_run_impact_tty_accept_proceeds(tmp_path, monkeypatch, capsys):
    """Verifies that confirming on a TTY proceeds with the impact run."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "y\n")
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "Proceed with push" in out


def test_run_impact_tty_decline_blocks(tmp_path, monkeypatch, capsys):
    """Verifies that declining on a TTY blocks with a non-zero exit code."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "n\n")
    assert impact.run_impact(repo, cfg) == 1


def test_run_impact_tty_empty_input_accepts(tmp_path, monkeypatch, capsys):
    """Verifies that empty input on a TTY proceeds with the impact run."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "\n")
    assert impact.run_impact(repo, cfg) == 0


def test_run_impact_rename_only(tmp_path, capsys):
    """Verifies that a pure rename reports dangling references for removed helpers."""
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
    assert "old_helper (import at 1)" in out
    assert "old_helper (name at 4)" in out
    assert "High" in out


def test_run_impact_non_python_change_set(tmp_path, capsys):
    """Verifies that non-Python changes produce no Python impact rows."""
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
    """Verifies that an empty change set reports zero changed files and no rows."""
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
    """Verifies that fast mode excludes unrelated and dependency files from analysis."""
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

def test_affected_routes_binds_fastapi_handler_by_decorator():
    """Verifies a FastAPI handler bound only by its decorator marks its route affected.

    FastAPI binds ``read_orders`` to ``@app.get(...)`` in the handler's own
    module; the binding must not require a same-named usage, or ``affected``
    (and its endpoints/template/frontend rows) is silently empty.
    """
    analysis = pa.analyze_source(FASTAPI_MAIN_BASE)
    route = route_linker.Route(None, "/api/v1/orders/", "read_orders", "main.py")
    analyses = {"main.py": analysis}
    entities = {
        "read_orders": {"kind": "function", "deleted": False, "modules": {"main"}}
    }
    affected = impact._affected_routes([route], analyses, entities, set())
    assert [r.path for r in affected] == ["/api/v1/orders/"]


def test_run_impact_fastapi_links_frontend_to_decorated_route(tmp_path, capsys):
    """Verifies a changed FastAPI handler surfaces its frontend consumers."""
    repo, base, _head = _build_fastapi_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert [ep["url"] for ep in data["endpoints"]] == ["/api/v1/orders/"]
    frontend_rows = [r for r in data["rows"] if r["category"] == "frontend"]
    assert len(frontend_rows) == 1
    assert frontend_rows[0]["path"] == "src/orders.service.ts"
    assert "/api/v1/orders/" in frontend_rows[0]["ref"]


def test_str_usage_not_spurious_caller_hop_or_route_binding():
    """Verifies that a string literal equal to an entity name is not a caller hop.

    A module that imports 'create' and also contains getattr(obj, 'create')
    records a 'str' usage whose name equals the changed entity. Without a kind
    guard the string is treated as a call site of 'create', marking 'config'
    reachable and spuriously flagging its route as affected. reverse('create'),
    redirect('create') and getattr(model, 'create') all hit this pattern.
    """
    analyses = {
        "app/other.py": pa.analyze_source(
            "from app.views import create\n"
            "\n"
            "def config():\n"
            "    return getattr(obj, 'create')\n"
        )
    }
    index = impact._usage_index(analyses)
    assert impact._hop_callers(index, "create", {"app.views"}, set()) == {}
    route = route_linker.Route(None, "/config/", "config", "app/other.py")
    entities = {
        "create": {"kind": "function", "deleted": False, "modules": {"app.views"}}
    }
    assert impact._affected_routes([route], analyses, entities, set()) == []


def test_route_binds_handler_ignores_str_usage():
    """Verifies that a string literal never binds a route handler by itself.

    The urls module binds the name 'create' only as an alias to a different
    object (from app.views import orders as create); the changed entity
    'create' is not actually imported. reverse('create') records a 'str'
    usage that resolves through that alias, so without the kind guard the
    route would be falsely bound even though no genuine import names the
    handler. The module (app.urls) is outside the handler's mods so the
    same-module shortcut cannot mask the result.
    """
    urls = pa.analyze_source(
        "from app.views import orders as create\n"
        "\n"
        "def reverse_url():\n"
        "    return reverse('create')\n"
    )
    assert not impact._route_binds_handler(
        urls, "create", {"app.views"}, "app/urls.py", set()
    )


def test_section_and_badge_assignment():
    """Verifies that finish_row assigns section and Changed/Impacted badge."""
    from diffimpactscout.impact import impact as m
    from diffimpactscout.impact import reporter
    row = m._python_row(
        {"path": "b.py", "name": "x", "how": "name", "line": 3, "ctx_qname": ""},
        "x", {"deleted": False},
    )
    row2 = m.finish_row(row, severity="Medium",
                        changed_paths={"a.py"}, section_hint="direct")
    assert row2["badge"] in ("Changed", "Impacted")
    assert row2["section"] in ("Breaking", "Direct", "Transitive")
    assert row2["badge"] == "Impacted"
    assert row2["key"] == "b.py:3"
    # Changed branch: path in the change-set renders Changed.
    row_changed = m.finish_row(row, severity="Medium",
                               changed_paths={"b.py"}, section_hint="direct")
    assert row_changed["badge"] == "Changed"
    # Section rules.
    assert m.finish_row(row, severity="High", changed_paths={"a.py"})["section"] == "Breaking"
    assert m.finish_row(row, severity="Medium", changed_paths={"a.py"},
                        deleted=True)["section"] == "Breaking"
    assert m.finish_row(row, severity="Medium", changed_paths={"a.py"},
                        section_hint="transitive")["section"] == "Transitive"
    # Global section order: Breaking -> Direct -> Transitive.
    breaking = dict(row2, path="br.py", section="Breaking")
    direct = dict(row2, path="di.py", section="Direct")
    transitive = dict(row2, path="tr.py", section="Transitive")
    ordered = reporter._order_by_section([transitive, breaking, direct])
    assert [r["section"] for r in ordered] == ["Breaking", "Direct", "Transitive"]


def _confidence_fixture():
    analyses = {
        "app/other.py": pa.analyze_source(
            "from app.mod import helper\n"
            "\n"
            "def f():\n"
            "    return helper()\n"
        ),
        "app/mod.py": pa.analyze_source(
            "def helper():\n"
            "    return 1\n"
            "\n"
            "def g():\n"
            "    return helper()\n"
        ),
        "app/other2.py": pa.analyze_source(
            "def f():\n"
            "    return helper()\n"
        ),
    }
    entities = {
        "helper": {"kind": "function", "deleted": False, "modules": {"app.mod"}}
    }
    changed = ["app/mod.py", "app/other2.py"]
    return analyses, entities, changed


def test_rows_carry_confidence_and_evidence():
    """Verifies every composed row carries confidence and evidence."""
    analyses, entities, changed = _confidence_fixture()
    rows, _endpoints, _unresolved, _hidden = impact._compose_rows(
        None, {}, "generic", entities, analyses, changed, True,
        show_all=True,
    )
    assert rows
    for r in rows:
        assert r["confidence"] in ("high", "medium", "low")
        assert "evidence" in r and r["evidence"]
    confs = {r["confidence"] for r in rows}
    assert "high" in confs
    assert "medium" in confs
    assert "low" in confs


def test_hide_low_by_default_and_show_all():
    """Verifies low-confidence rows hide by default and show under Transitive."""
    analyses, entities, changed = _confidence_fixture()
    rows, _e, _u, hidden = impact._compose_rows(
        None, {}, "generic", entities, analyses, changed, True,
        show_all=False,
    )
    assert rows
    assert all(r["confidence"] != "low" for r in rows)
    assert hidden > 0
    rows_all, _e2, _u2, _h2 = impact._compose_rows(
        None, {}, "generic", entities, analyses, changed, True,
        show_all=True,
    )
    lows = [r for r in rows_all if r["confidence"] == "low"]
    assert lows
    assert all(r["section"] == "Transitive" for r in lows)


def test_why_and_action_present():
    from diffimpactscout.impact import impact as m
    row = m.finish_row({"path": "b.py", "ref": "x", "key": "b.py:3"}, severity="Medium", changed_paths={"a.py"})
    row = m.attach_why_action(row, symbol="x", import_chain=["b"], deleted=False, test_path="tests/test_b.py")
    assert row["why_matters"] and "x" in row["why_matters"]
    assert row["suggested_action"] != "verify"
    assert row["suggested_action"] in ("remove import", "update call site", "check migration mapping", "run tests/test_b.py")


def test_why_and_action_deleted():
    from diffimpactscout.impact import impact as m
    row = m.finish_row({"path": "b.py", "ref": "x", "key": "b.py:3"}, severity="High", changed_paths={"a.py"})
    row = m.attach_why_action(row, symbol="x", import_chain=[], deleted=True, test_path=None)
    assert "x" in row["why_matters"]
    assert "deleted" in row["why_matters"]
    assert row["suggested_action"] == "remove import"
    assert row["action"] == "remove import"


def test_no_bare_verify_action_on_medium_plus(tmp_path):
    """Lint: no Medium+ composed row may carry bare verify as its sole action."""
    from diffimpactscout.impact import reverse as reverse_mod
    analyses, entities, changed = _confidence_fixture()
    # Deleted branch: symbol removed in the change-set, still referenced.
    entities["gone"] = {"kind": "function", "deleted": True, "modules": {"app.mod"}}
    analyses["app/user.py"] = pa.analyze_source(
        "from app.mod import gone\n"
        "\n"
        "def f():\n"
        "    return gone()\n"
    )
    # Tiered branches: reverse rows keyed by pseudo-symbol entities.
    extra_rows = [
        reverse_mod.make_row(
            "templates/base.html", "templates", "template",
            "changed", "changed:base", tier=reverse_mod.TIER_FRONTEND,
        ),
        reverse_mod.make_row(
            "static/app.js", "static", "frontend",
            "fetch /api/order/", "extends:order", tier=reverse_mod.TIER_BACKEND,
        ),
    ]
    # run-<test> branch: conventional unit test exists under the root.
    test_file = tmp_path / "tests" / "test_other.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_other():\n    pass\n")
    rows, _e, _u, _h = impact._compose_rows(
        str(tmp_path), {}, "generic", entities, analyses, changed, True,
        show_all=True, extra_rows=extra_rows,
    )
    assert rows
    allowed = ("remove import", "update call site", "check migration mapping")
    medium_plus = [r for r in rows if r.get("severity") in ("Medium", "High")]
    assert medium_plus
    for r in medium_plus:
        assert r.get("why_matters"), "Medium+ row missing why_matters: %r" % (r,)
        assert "\n" not in r["why_matters"], "why_matters must be one line: %r" % (r,)
        assert r.get("suggested_action") != "verify", "bare verify: %r" % (r,)
        action = r.get("suggested_action") or ""
        assert action in allowed or action.startswith("run "), "unexpected action: %r" % (r,)
        assert r.get("action") == r.get("suggested_action")
    # Each attach branch fired at least once.
    assert any(r.get("suggested_action") == "remove import" for r in medium_plus)
    assert any((r.get("suggested_action") or "").startswith("run ") for r in medium_plus)
    assert any(r.get("suggested_action") == "update call site" for r in medium_plus)
    # Pseudo-symbol prefixes never leak into user-facing text.
    for r in medium_plus:
        assert "changed:" not in (r.get("why_matters") or "")
        assert "extends:" not in (r.get("why_matters") or "")


def test_stoplist_pruned_stay_hidden_with_all():
    """Verifies stoplist-pruned generic hits stay dropped even with --all.

    Carry-forward disposition from Task 2: pruned hits are
    drop-always, never demoted/resurfaced. Task 2 deferred
    "--all demotion+resurfacing of stoplist-pruned hits" here; chosen
    behavior is drop-always so generic-name noise cannot return via --all.
    """
    analyses = {
        "app/other.py": pa.analyze_source(
            "def f(obj):\n"
            "    return obj.status\n"
        ),
    }
    entities = {
        "status": {"kind": "class_field", "deleted": False, "modules": {"app.models"}}
    }
    rows, _e, _u, _h = impact._compose_rows(
        None, {}, "generic", entities, analyses, ["app/models.py"], True,
        show_all=True,
    )
    assert rows == []


def test_compose_rows_links_ts_symbol_refs(tmp_path):
    """Verifies changed symbol names link to TS imports as frontend-symbol rows."""
    _write(
        str(tmp_path),
        "src/app.ts",
        "import { CustomTag } from './models';\nconsole.log(CustomTag);\n",
    )
    entities = {"CustomTag": {"kind": "function", "deleted": False, "modules": set()}}
    rows, _e, _u, _h = impact._compose_rows(
        str(tmp_path), {"frontend_globs": ["**/*.ts"]}, "generic",
        entities, {}, ["app/models.py"], False,
    )
    sym = [r for r in rows if r.get("category") == "frontend-symbol"]
    assert len(sym) == 1
    assert sym[0]["path"] == "src/app.ts"
    assert sym[0]["confidence"] == "medium"
    assert "CustomTag" in sym[0]["ref"]
    assert sym[0]["severity"] == "Medium"


def test_compose_rows_ts_symbol_rows_have_distinct_keys(tmp_path):
    """Verifies two symbols imported in one file produce distinct path:line keys (WR-05)."""
    _write(
        str(tmp_path),
        "src/app.ts",
        "import { Alpha } from './a';\n"
        "import { Beta } from './b';\n",
    )
    entities = {
        "Alpha": {"kind": "function", "deleted": False, "modules": set()},
        "Beta": {"kind": "function", "deleted": False, "modules": set()},
    }
    rows, _e, _u, _h = impact._compose_rows(
        str(tmp_path), {"frontend_globs": ["**/*.ts"]}, "generic",
        entities, {}, ["app/models.py"], False,
    )
    sym = [r for r in rows if r.get("category") == "frontend-symbol"]
    assert len(sym) == 2
    assert {r["key"] for r in sym} == {"src/app.ts:1", "src/app.ts:2"}


def test_compose_rows_fast_skips_ts_symbol_refs(tmp_path):
    """Verifies fast mode skips TS symbol linking (consistent with _scoped_refs)."""
    _write(
        str(tmp_path),
        "src/app.ts",
        "import { CustomTag } from './models';\n",
    )
    entities = {"CustomTag": {"kind": "function", "deleted": False, "modules": set()}}
    rows, _e, _u, _h = impact._compose_rows(
        str(tmp_path), {"frontend_globs": ["**/*.ts"]}, "generic",
        entities, {}, ["app/models.py"], True,
    )
    assert not [r for r in rows if r.get("category") == "frontend-symbol"]


def test_breaking_detectors():
    from diffimpactscout.impact import impact as m
    rows = m.detect_breaking([
        {"path": "tests/test_x.py", "removed_ratio": 0.8},
        {"path": "pkg/mod.py", "removed_export": "foo"},
        {"path": "pkg/migrations/0001.py", "touched": True},
        {"path": "pkg/added.py", "added_only": True},
    ])
    assert len([r for r in rows if r["severity"] == "High"]) == 3
    assert any("restore or justify removal" in r["suggested_action"] for r in rows)
    by_path = {r["path"]: r for r in rows}
    test_row = by_path["tests/test_x.py"]
    assert test_row["ref"] == "deleted test coverage"
    assert test_row["suggested_action"] == "restore or justify removal"
    assert test_row["section"] == "Breaking"
    assert "risk" in test_row
    api_row = by_path["pkg/mod.py"]
    assert api_row["ref"] == "breaking API change: foo"
    assert api_row["suggested_action"] == "restore export or justify breaking change"
    assert api_row["section"] == "Breaking"
    mig_row = by_path["pkg/migrations/0001.py"]
    assert mig_row["ref"] == "migration/infra change - needs rollout note"
    assert mig_row["suggested_action"] == "add rollout note"
    assert mig_row["section"] == "Breaking"
    for r in rows:
        assert r["why_matters"] and "\n" not in r["why_matters"]
        assert "verify" not in r["suggested_action"]
        assert r["path"] in r["why_matters"]


def test_breaking_rule1_threshold():
    """Verifies rule 1 fires only for deleted test files or >50% removed."""
    from diffimpactscout.impact import impact as m
    # Small ratios must not fire.
    assert m.detect_breaking([{"path": "tests/test_x.py", "removed_ratio": 0.01}]) == []
    assert m.detect_breaking([{"path": "tests/test_x.py", "removed_ratio": 0.2}]) == []
    # Touched alone must not fire.
    assert m.detect_breaking([{"path": "tests/test_x.py", "touched": True}]) == []
    # Deleted test file fires.
    deleted_rows = m.detect_breaking([{"path": "tests/test_x.py", "status": "D"}])
    assert len(deleted_rows) == 1
    assert deleted_rows[0]["ref"] == "deleted test coverage"
    # Non-test ratio above 0.5 must not be labeled deleted test coverage.
    other_rows = m.detect_breaking([{"path": "pkg/util.py", "removed_ratio": 0.6}])
    assert [r for r in other_rows if r["ref"] == "deleted test coverage"] == []
    # Deleted source file must not be mislabeled; it falls through
    # (no deleted entities here, so no rows at all).
    src_rows = m.detect_breaking([{"path": "pkg/util.py", "status": "D"}])
    assert [r for r in src_rows if r["ref"] == "deleted test coverage"] == []
    # Deleted source with a deleted entity falls through to breaking-API.
    entities = {"helper": {"kind": "function", "deleted": True, "modules": {"pkg.util"}}}
    api_rows = m.detect_breaking(
        [{"path": "pkg/util.py", "status": "D"}], entities=entities
    )
    assert len(api_rows) == 1
    assert api_rows[0]["ref"] == "breaking API change: helper"


def test_breaking_dict_change_with_root_no_crash():
    """Regression: dict change with root set must not crash the diff ratio.

    _br_ratio_from_diff calls get_changed_lines, which needs
    change.status/change.path attributes; a plain dict raises
    AttributeError. The helper must swallow it and return no rows.
    """
    from diffimpactscout.impact import impact as m
    assert m.detect_breaking(
        [{"path": "tests/test_x.py", "touched": True}], root="/tmp"
    ) == []


def test_breaking_migration_infra_matchers():
    """Verifies lockfile and CI matchers fire as migration/infra rows."""
    from diffimpactscout.impact import impact as m
    paths = [
        "pkg/migrations/0002.py",
        ".github/workflows/ci.yml",
        ".gitlab-ci.yml",
        ".circleci/config.yml",
        "Jenkinsfile",
        ".travis.yml",
        "azure-pipelines.yml",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Pipfile.lock",
        "poetry.lock",
        "Gemfile.lock",
        "Cargo.lock",
        "go.sum",
        "pkg/app.toml",
    ]
    rows = m.detect_breaking([{"path": p, "status": "M"} for p in paths])
    assert len(rows) == len(paths)
    for r in rows:
        assert r["ref"] == "migration/infra change - needs rollout note"
        assert r["suggested_action"] == "add rollout note"
        assert r["section"] == "Breaking"


def test_breaking_modified_test_ratio_wired(tmp_path):
    """Verifies a modified test file with >50% removed lines fires end to end."""
    from diffimpactscout.impact import diff_parser
    from diffimpactscout.impact import impact as m
    repo = _make_repo(tmp_path)
    base_src = "".join("line %d\n" % i for i in range(10))
    _write(repo, "tests/test_x.py", base_src)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "tests/test_x.py", "line 0\nline 1\n")
    _commit(repo, "trim test")
    anchor = _anchor(repo, base)
    changes = diff_parser.get_file_changes(repo, anchor, False, None, None)
    assert [c.path for c in changes] == ["tests/test_x.py"]
    rows = m.detect_breaking(changes, root=repo, anchor=anchor)
    assert len(rows) == 1
    assert rows[0]["ref"] == "deleted test coverage"
    assert rows[0]["section"] == "Breaking"
    # Small removal stays quiet.
    _write(repo, "tests/test_x.py", "".join("line %d\n" % i for i in range(9)))
    _commit(repo, "small trim")
    changes2 = diff_parser.get_file_changes(repo, anchor, False, None, None)
    test_changes = [c for c in changes2 if c.path == "tests/test_x.py"]
    rows2 = m.detect_breaking(test_changes, root=repo, anchor=anchor)
    assert rows2 == []


def test_breaking_pure_addition_yields_no_high():
    from diffimpactscout.impact import impact as m
    rows = m.detect_breaking([
        {"path": "pkg/added.py", "added_only": True},
        {"path": "pkg/other.py", "added_only": True},
        {"path": "pkg/migrations/0003.py", "added_only": True},
        {"path": "tests/test_new.py", "added_only": True},
        {"path": "pkg/migrations/0004.py", "status": "A"},
        {"path": ".github/workflows/ci.yml", "status": "A"},
    ])
    assert [r for r in rows if r["severity"] == "High"] == []


def test_high_fan_in_ranks_first():
    from diffimpactscout.impact import impact as m
    rows = [
        {"path": "single.py", "ref": "helper", "severity": "Medium",
         "fan_in": 1, "missing_test": 0, "churn": 0},
        {"path": "used.py", "ref": "util", "severity": "Medium",
         "fan_in": 10, "missing_test": 0, "churn": 0},
    ]
    ranked = m.rank_rows(rows, max_fan_in=10)
    assert ranked[0]["ref"] == "util"


def _build_deleted_import_repo(tmp_path):
    """A Python-only repo: a deleted symbol referenced by two import styles."""
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "pkg/__init__.py", "")
    _write(
        repo,
        "pkg/mod.py",
        "def keep():\n"
        "    return 1\n"
        "\n"
        "def target():\n"
        "    return 2\n",
    )
    _write(
        repo,
        "pkg/user_from.py",
        "from pkg.mod import target\n"
        "\n"
        "def use():\n"
        "    return target()\n",
    )
    _write(
        repo,
        "pkg/user_import.py",
        "import pkg.mod\n"
        "\n"
        "def use2():\n"
        "    return pkg.mod.target()\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "pkg/mod.py", "def keep():\n    return 1\n")
    _commit(repo, "delete target")
    _anchor(repo, base)
    return repo, base


def _build_inner_class_edits_repo(tmp_path):
    """A repo whose branch commit only removes lines inside a class method."""
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    _write(
        repo,
        "app/billing.py",
        "class Widget:\n"
        "    def build(self):\n"
        "        rules = []\n"
        "        extra = self.scope()\n"
        "        rules.append(extra)\n"
        "        return rules\n"
        "\n"
        "    def scope(self):\n"
        "        return 1\n",
    )
    _write(
        repo,
        "app/user.py",
        "from app.billing import Widget\n"
        "\n"
        "def use():\n"
        "    return Widget()\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(
        repo,
        "app/billing.py",
        "class Widget:\n"
        "    def build(self):\n"
        "        rules = []\n"
        "        return rules\n"
        "\n"
        "    def scope(self):\n"
        "        return 1\n",
    )
    _commit(repo, "trim build method")
    _anchor(repo, base)
    return repo, base


def test_pure_deletion_inside_class_not_deleted(tmp_path):
    """A pure-deletion hunk inside a method must not flag the class deleted.

    The branch commit only removes lines from ``Widget.build`` -- the class
    and method definitions still exist in the new source -- so the report
    must not emit High ``remove import`` rows for its importers.
    """
    repo, base = _build_inner_class_edits_repo(tmp_path)
    from diffimpactscout.impact import diff_parser

    entities = impact._changed_entities(
        diff_parser.get_file_changes(repo, from_ref=base, to_ref="HEAD"),
        repo,
        base,
    )
    assert entities["Widget"]["deleted"] is False
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        impact.run_impact(repo, config.load_config(repo), json_out=True)
    data = json.loads(buf.getvalue())
    deleted_rows = [
        r
        for r in data["rows"]
        if r.get("action") == "remove import"
        or r.get("reason") == "symbol was deleted or renamed in this change-set"
    ]
    assert deleted_rows == []


def _build_scale_repo(tmp_path, n=200):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(DJANGO_CFG))
    _write(repo, "app/__init__.py", "")
    for i in range(n):
        _write(
            repo,
            "app/mod%03d.py" % i,
            "def func_%03d():\n"
            "    return %d\n"
            "\n"
            "def helper_%03d():\n"
            "    return func_%03d()\n" % (i, i, i, i),
        )
    _write(
        repo,
        "app/mod009.py",
        "def func_009():\n"
        "    return 9\n"
        "\n"
        "def helper_009():\n"
        "    return func_009()\n"
        "\n"
        "def removed_009():\n"
        "    return 0\n",
    )
    _commit(repo, "base")
    base = _sha(repo)
    _write(
        repo,
        "app/consumer.py",
        "from app.mod009 import removed_009\n"
        "\n"
        "def use():\n"
        "    return removed_009()\n",
    )
    _write(
        repo,
        "app/mod009.py",
        "def func_009():\n"
        "    return 9\n"
        "\n"
        "def helper_009():\n"
        "    return func_009()\n",
    )
    _commit(repo, "delete removed_009")
    _anchor(repo, base)
    return repo, base


def _high_keys(repo, cfg, fast):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        impact.run_impact(repo, cfg, json_out=True, fast=fast)
    data = json.loads(buf.getvalue())
    return {r["key"] for r in data["rows"] if r["severity"] == "High"}


def test_fast_full_high_agreement(tmp_path):
    """Verifies fast and full modes report identical High-row sets."""
    full_repo, _b = _build_deleted_import_repo(tmp_path / "full")
    fast_repo, _b = _build_deleted_import_repo(tmp_path / "fast")
    full = _high_keys(full_repo, config.load_config(full_repo), fast=False)
    fast = _high_keys(fast_repo, config.load_config(fast_repo), fast=True)
    assert full
    assert full == fast


def test_perf_budget_scale(tmp_path):
    """Verifies a full-mode run on a 200-file repo stays within budget."""
    import time
    MAX_SECONDS = 30
    repo, _base = _build_scale_repo(tmp_path)
    cfg = config.load_config(repo)
    impact.run_impact(repo, cfg, json_out=True)
    t0 = time.time()
    impact.run_impact(repo, cfg, json_out=True)
    assert time.time() - t0 < MAX_SECONDS


def test_cache_hit_rate_in_json(tmp_path, capsys):
    """Verifies cache_hit_rate is 0 on a cold run and 1 on a warm run."""
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    cold = json.loads(capsys.readouterr().out)
    assert cold["cache_hit_rate"] == 0.0
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    warm = json.loads(capsys.readouterr().out)
    assert warm["cache_hit_rate"] == 1.0


def test_unreachable_downgraded_one_level():
    """Verifies unreachable rows drop one severity level and are marked."""
    from diffimpactscout.impact import impact as m
    rows = [
        {"path": "p.py", "severity": "High", "reachable": True, "key": "a"},
        {"path": "p.py", "severity": "High", "reachable": False, "key": "b"},
        {"path": "p.py", "severity": "Low", "reachable": False, "key": "c"},
    ]
    out = {r["key"]: r for r in m.apply_reachability(rows)}
    assert out["a"]["severity"] == "High"
    assert "reachability" not in out["a"]
    assert out["b"]["severity"] == "Medium"
    assert out["b"]["reachability"] == "unreachable"
    assert out["c"]["severity"] == "Low"
    assert out["c"]["reachability"] == "unreachable"


def test_reachability_downgrades_unreached_consumer(tmp_path, capsys):
    """Verifies a consumer no entry reaches ranks below a reached consumer."""
    repo, base, _head = _build_reachability_repo(tmp_path)
    _anchor(repo, base)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    data = json.loads(capsys.readouterr().out)
    by_ctx = {}
    for row in data["rows"]:
        if row.get("category") == "python" and row.get("module") in (
            "helper_a",
            "orphan",
            "Command.handle",
        ):
            by_ctx[row["module"]] = row
    # Reached from the route handler `orders`.
    assert by_ctx["helper_a"]["severity"] == "Medium"
    assert "reachability" not in by_ctx["helper_a"]
    # A management command is an entry point even with no calling route.
    assert by_ctx["Command.handle"]["severity"] == "Medium"
    assert "reachability" not in by_ctx["Command.handle"]
    # Reached by nothing: downgraded one level and marked.
    assert by_ctx["orphan"]["severity"] == "Low"
    assert by_ctx["orphan"]["reachability"] == "unreachable"
