import json
import os
import subprocess

import diffimpactscout.config as config
from diffimpactscout.impact import impact as impact_module


def _git(*args, cwd):
    subprocess.check_call(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.co"] + list(args), cwd=cwd
    )


def _repo(tmp_path):
    root = str(tmp_path)
    _git("init", cwd=root)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=root)
    os.makedirs(os.path.join(root, "shop", "templates", "shop"))
    os.makedirs(os.path.join(root, "shop"), exist_ok=True)
    with open(os.path.join(root, "shop", "views.py"), "w") as fh:
        fh.write("from django.shortcuts import render\n\n"
                 "def product_view(request):\n"
                 "    return render(request, 'shop/product.html')\n")
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html></html>\n")
    with open(os.path.join(root, "shop", "templates", "shop", "product.html"), "w") as fh:
        fh.write("{% extends 'shop/base.html' %}\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m0", cwd=root)
    _git("remote", "add", "upstream", ".", cwd=root)
    _git("push", "-q", "upstream", "master", cwd=root)
    return root


def test_template_only_diff_produces_rows(tmp_path, capsys):
    """Verifies that a template-only diff yields render-site and extends rows."""
    root = _repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html><body>x</body></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    cfg["impact"]["reverse"] = True
    rc = impact_module.run_impact(root, cfg, json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["changed_count"] == 1
    paths = {(r["path"], r["category"]) for r in out["rows"]}
    assert ("shop/views.py", "python") in paths
    assert ("shop/templates/shop/product.html", "template") in paths
    cats = {r["category"] for r in out["rows"]}
    assert cats <= {"python", "template"}
    assert all(r.get("severity") for r in out["rows"])
    assert all(r.get("reason") for r in out["rows"])


def test_reverse_off_keeps_empty_table(tmp_path, capsys):
    """Verifies that reverse=False preserves today's behavior."""
    root = _repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html><body>x</body></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    assert impact_module.run_impact(root, cfg, json_out=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == []


def test_fast_skips_reverse(tmp_path, capsys):
    """Verifies that --fast skips reverse chains."""
    root = _repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html><body>x</body></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    cfg["impact"]["reverse"] = True
    assert impact_module.run_impact(root, cfg, fast=True, json_out=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == []


def _template_repo(tmp_path):
    root = str(tmp_path)
    _git("init", cwd=root)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=root)
    os.makedirs(os.path.join(root, "shop", "templates", "shop"), exist_ok=True)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m0", cwd=root)
    _git("remote", "add", "upstream", ".", cwd=root)
    _git("push", "-q", "upstream", "master", cwd=root)
    return root


def test_dynamic_include_surfaces_in_unresolved(tmp_path, capsys):
    """A changed template with a dynamic include must appear in unresolved."""
    root = _template_repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("{% include fragment_template %}\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    cfg["impact"]["reverse"] = True
    rc = impact_module.run_impact(root, cfg, json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    files = {u.get("file") for u in out["unresolved"]}
    assert "shop/templates/shop/base.html" in files


def test_merge_entities_preserves_existing_kind_and_deleted():
    """Reverse pseudo-entities must not clobber a real changed/deleted symbol."""
    entities = {"detail": {"kind": "class", "deleted": True, "modules": {"app_a.views"}}}
    extra = {"detail": {"kind": "function", "deleted": False, "modules": {"app_b.views"}}}
    impact_module._merge_entities(entities, extra)
    assert entities["detail"]["deleted"] is True
    assert entities["detail"]["kind"] == "class"
    assert entities["detail"]["modules"] == {"app_a.views", "app_b.views"}
    impact_module._merge_entities(
        entities, {"newview": {"kind": "function", "deleted": False, "modules": {"x"}}}
    )
    assert entities["newview"] == {"kind": "function", "deleted": False, "modules": {"x"}}


def test_mixed_static_and_dynamic_include_is_unresolved(tmp_path, capsys):
    """A template with both a static and a dynamic include is still unresolved."""
    root = _template_repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("{% include 'shop/nav.html' %}\n{% include fragment_template %}\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    cfg["impact"]["reverse"] = True
    assert impact_module.run_impact(root, cfg, json_out=True) == 0
    out = json.loads(capsys.readouterr().out)
    files = {u.get("file") for u in out["unresolved"]}
    assert "shop/templates/shop/base.html" in files


def test_unrelated_template_view_produces_no_row(tmp_path, capsys):
    """A view rendering a template that is neither changed nor a descendant gets no row."""
    root = _repo(tmp_path)
    with open(os.path.join(root, "shop", "views.py"), "w") as fh:
        fh.write("from django.shortcuts import render\n\n"
                 "def product_view(request):\n"
                 "    return render(request, 'shop/product.html')\n\n"
                 "def other_view(request):\n"
                 "    return render(request, 'shop/unrelated.html')\n")
    with open(os.path.join(root, "shop", "templates", "shop", "unrelated.html"), "w") as fh:
        fh.write("<html>unrelated</html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    _git("push", "-q", "upstream", "master", cwd=root)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html><body>y</body></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m2", cwd=root)
    cfg = config._defaults()
    cfg["impact"]["reverse"] = True
    rc = impact_module.run_impact(root, cfg, json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    for row in out["rows"]:
        assert "unrelated" not in (row.get("ref") or "")
        assert "unrelated" not in (row.get("path") or "")
