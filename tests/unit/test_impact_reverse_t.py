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


def test_reverse_off_keeps_empty_table(tmp_path, capsys):
    """Verifies that reverse=False preserves today's behavior."""
    root = _repo(tmp_path)
    with open(os.path.join(root, "shop", "templates", "shop", "base.html"), "w") as fh:
        fh.write("<html><body>x</body></html>\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "m1", cwd=root)
    cfg = config._defaults()
    rc = impact_module.run_impact(root, cfg, json_out=True)
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
    impact_module.run_impact(root, cfg, fast=True, json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == []
