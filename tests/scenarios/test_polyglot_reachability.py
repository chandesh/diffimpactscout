"""End-to-end replay over polyglot linking, unresolved causes, and reachability.

The fixture is generic (no product terms) and deliberately small: scale is
covered separately by ``test_perf_budget_scale``. These tests assert the
end-to-end report contract -- body cap, summary placement, frontend call
styles, TS symbol linking, dynamic/unresolved causes, reachability delta, and
no generic-name noise.
"""

import json
import os
import re
import subprocess
import sys

import pytest

import diffimpactscout.config as config
import diffimpactscout.scope as scope
from diffimpactscout.impact import impact
from diffimpactscout.impact import reporter

CFG = {
    "impact": {
        "profile": "django",
        "urls_globs": ["**/urls.py"],
        "template_globs": ["**/templates/**/*.html"],
        "frontend_globs": ["**/src/**/*.ts", "**/src/**/*.js"],
        "reverse": True,
    }
}

URLS = (
    "from django.urls import path\n"
    "from . import views\n"
    "\n"
    "urlpatterns = [\n"
    "    path('orders/', views.orders, name='order-list'),\n"
    "]\n"
)

MODELS_BASE = (
    "from django.db import models\n"
    "\n"
    "class Book(models.Model):\n"
    "    title = models.CharField(max_length=20)\n"
    "    status = models.CharField(max_length=20)\n"
)

MODELS_HEAD = (
    "from django.db import models\n"
    "\n"
    "class Book(models.Model):\n"
    "    title = models.CharField(max_length=20)\n"
    "    status = models.CharField(max_length=20, default='new')\n"
)

VIEWS_BASE = (
    "from django.http import HttpResponse\n"
    "from app.helpers import helper_a\n"
    "\n"
    "def orders(request):\n"
    "    return HttpResponse(helper_a())\n"
)

VIEWS_HEAD = (
    "from django.http import HttpResponse\n"
    "from app.helpers import helper_a\n"
    "\n"
    "def orders(request):\n"
    "    return HttpResponse(helper_a().upper())\n"
)

HELPERS = (
    "from app.models import Book\n"
    "\n"
    "def helper_a():\n"
    "    return Book.status\n"
)

ORPHAN = (
    "from app.models import Book\n"
    "\n"
    "def orphan():\n"
    "    return Book.status\n"
)

GENERIC_USER = (
    "def render_name(obj):\n"
    "    return obj.status\n"
)

DYNAMIC_TEMPLATE = '<a href="{% url view_name %}">dynamic</a>\n'

CALL_STYLES = {
    "src/http.ts": "this.http.get('/orders/');\n",
    "src/fetch.ts": "fetch('/orders/');\n",
    "src/axios.ts": "axios.get('/orders/');\n",
    "src/spa-calls.ts": "this.$http.get('/orders/');\n",
    "src/query.ts": "useQuery(() => fetch('/orders/'));\n",
}

SYMBOL_TS = "import { orders } from './api';\n"

UNKNOWN_TS = "fetch('/orders/');\nfetch('/missing/');\n"

STANDALONE_JS = "console.log('standalone');\n"


def _git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def _git(*args, cwd):
    subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + list(args),
        cwd=cwd,
        env=_git_env(),
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
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w") as fh:
        fh.write(content)


def _commit(repo, message):
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", message, cwd=repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(["git", "rev-parse", ref], cwd=repo, env=_git_env())
    return out.decode("utf-8").strip()


def _anchor(repo, sha):
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)


def _build_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo, ".diffimpactscout.json", json.dumps(CFG))
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/urls.py", URLS)
    _write(repo, "app/models.py", MODELS_BASE)
    _write(repo, "app/views.py", VIEWS_BASE)
    _write(repo, "app/helpers.py", HELPERS)
    _write(repo, "app/orphan.py", ORPHAN)
    _write(repo, "app/generic_user.py", GENERIC_USER)
    _write(repo, "app/templates/dynamic.html", DYNAMIC_TEMPLATE)
    for path, text in CALL_STYLES.items():
        _write(repo, path, text)
    _write(repo, "src/symbol.ts", SYMBOL_TS)
    _write(repo, "src/unknown.ts", UNKNOWN_TS)
    _write(repo, "src/standalone.js", STANDALONE_JS)
    _commit(repo, "base")
    base = _sha(repo)
    _write(repo, "app/models.py", MODELS_HEAD)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _write(repo, "src/standalone.js", STANDALONE_JS + "console.log('touched');\n")
    _commit(repo, "change model, view, asset")
    _anchor(repo, base)
    return repo


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "default_cache_dir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _data_rows(out):
    return [line for line in out.splitlines() if re.match(r"^\s+\d+\s{2}", line)]


def test_report_body_and_summary_contract(tmp_path, capsys):
    repo = _build_repo(tmp_path)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg) == 0
    out = capsys.readouterr().out

    head = "\n".join(out.splitlines()[:25])
    assert "Risk:" in head
    assert "High:" in head and "Medium:" in head and "Low:" in head
    assert "changed" in head and "impacted" in head

    body = _data_rows(out)
    assert 0 < len(body) <= 20
    assert re.search(r"Unresolved: \d+ \(", out)


def test_polyglot_and_reachability_shapes(tmp_path, capsys):
    repo = _build_repo(tmp_path)
    cfg = config.load_config(repo)
    assert impact.run_impact(repo, cfg, json_out=True) == 0
    data = json.loads(capsys.readouterr().out)
    rows = data["rows"]

    # TS/JS symbol linking: a changed symbol imported by a TS module.
    assert any(
        row["path"] == "src/symbol.ts" and row["category"] == "frontend-symbol"
        for row in rows
    )

    # Five frontend call styles all link to the affected route.
    for path in CALL_STYLES:
        assert any(
            row["path"] == path and row["category"] == "frontend" for row in rows
        ), path

    # Dynamic template refs never link; they surface as exactly one cause.
    reasons = data["unresolved_by_reason"]
    assert reasons.get("dynamic-ref") == 1
    assert not any(row["path"] == "app/templates/dynamic.html" for row in rows)

    # Unknown URL and no-import-chain are counted by cause.
    assert reasons.get("unknown-url") == 1
    assert reasons.get("no-import-chain") == 1

    # Reachability: the route-reachable consumer stays one level above the
    # otherwise identical unreachable consumer.
    rank = {"High": 3, "Medium": 2, "Low": 1}
    reachable = [
        r for r in rows
        if r["path"] == "app/helpers.py" and r["ref"].startswith("status")
    ]
    unreachable = [
        r for r in rows
        if r["path"] == "app/orphan.py" and r["ref"].startswith("status")
    ]
    assert reachable and unreachable
    assert rank[reachable[0]["severity"]] == rank[unreachable[0]["severity"]] + 1
    assert unreachable[0].get("reachability") == "unreachable"

    # Generic unqualified attribute uses are pruned, never Medium+ noise.
    assert not any(
        row["path"] == "app/generic_user.py"
        and row["severity"] in ("High", "Medium")
        for row in rows
    )


def test_unresolved_reason_buckets_include_binary_and_unsupported():
    """Classify every canonical cause, including legacy free-text reasons.

    The pipeline has no binary/unsupported producer today; that bucket exists
    for legacy free-text reasons, so it is exercised at the renderer boundary.
    """
    unresolved = [
        {"file": "a.html", "reason": "dynamic-ref"},
        {"file": "b.ts", "reason": "unknown-url"},
        {"file": "c.py", "reason": "no-import-chain"},
        {"file": "d.bin", "reason": "binary/unsupported"},
    ]
    counts = reporter.unresolved_reason_counts(unresolved)
    assert counts == {
        "dynamic-ref": 1,
        "unknown-url": 1,
        "no-import-chain": 1,
        "binary/unsupported": 1,
    }
    out = reporter.render_report([], [], unresolved, 1)
    assert "Unresolved: 4 (" in out
    assert "binary/unsupported: 1" in out
