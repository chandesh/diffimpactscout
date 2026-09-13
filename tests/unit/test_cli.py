import json
import os
import subprocess
import sys

import pytest

import diffimpactscout.cli as cli
import diffimpactscout.scope as scope
from diffimpactscout import __version__

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

UTILS = (
    "def helper():\n"
    "    return 'x'\n"
)

TEMPLATE = "<a href=\"{% url 'order-list' %}\">orders</a>\n"

TS = (
    "import { HttpClient } from '@angular/common/http';\n"
    "this.http.get('/api/v1/orders/');\n"
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


def _commit(repo, filename, content, msg):
    _write(repo, filename, content)
    _git("add", filename, cwd=repo, env=_git_env())
    _git("commit", "-m", msg, cwd=repo, env=_git_env())
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _anchor(repo, sha=None):
    if sha is None:
        sha = _sha(repo)
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)
    return "refs/remotes/upstream/master"


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
    _git("add", "-A", cwd=repo, env=_git_env())
    _git("commit", "-m", "base", cwd=repo, env=_git_env())
    base = _sha(repo)
    _write(repo, "app/views.py", VIEWS_HEAD)
    _git("add", "-A", cwd=repo, env=_git_env())
    _git("commit", "-m", "change views", cwd=repo, env=_git_env())
    head = _sha(repo)
    return repo, base, head


def _json_repo(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.json", "{}", "base")
    _anchor(repo)
    _commit(repo, "bad.json", "{ not valid\n", "dev")
    return repo


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "default_cache_dir", lambda: str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _non_tty(monkeypatch):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "IMPACT_CHECK_SKIP",
        "DIFFIMPACTSCOUT_SKIP",
        "IMPACT_CHECK_STRICT",
        "PRE_COMMIT_FROM_REF",
        "PRE_COMMIT_TO_REF",
    ):
        monkeypatch.delenv(key, raising=False)


def test_init_writes_config(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.chdir(proj)
    assert cli.main(["init", "--profile", "django"]) == 0
    path = os.path.join(proj, ".diffimpactscout.json")
    assert os.path.exists(path)
    with open(path) as fh:
        data = json.load(fh)
    assert data["impact"]["profile"] == "django"
    assert data["impact"]["urls_globs"] == ["**/urls.py"]


def test_init_default_profile_is_generic(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.chdir(proj)
    assert cli.main(["init"]) == 0
    path = os.path.join(proj, ".diffimpactscout.json")
    with open(path) as fh:
        data = json.load(fh)
    assert data["impact"]["profile"] == "generic"


def test_init_second_run_idempotent(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.chdir(proj)
    assert cli.main(["init", "--profile", "generic"]) == 0
    path = os.path.join(proj, ".diffimpactscout.json")
    assert os.path.exists(path)
    with open(path, "w") as fh:
        fh.write("{\"custom\": true}\n")
    assert cli.main(["init", "--profile", "django"]) == 0
    with open(path) as fh:
        assert fh.read() == "{\"custom\": true}\n"


def test_guard_clean_returns_zero(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    _commit(repo, "ok.txt", "hello\n", "dev")
    monkeypatch.chdir(repo)
    assert cli.main(["guard"]) == 0
    out = capsys.readouterr().out
    assert "0 issue(s)" in out


def test_guard_violation_warn_returns_zero(tmp_path, capsys, monkeypatch):
    repo = _json_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert cli.main(["guard"]) == 0
    out = capsys.readouterr().out
    assert "syntax/json-syntax" in out
    assert "1 issue(s)" in out


def test_guard_violation_strict_returns_one(tmp_path, capsys, monkeypatch):
    repo = _json_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert cli.main(["guard"]) == 1


def test_guard_dispatches_flags(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    seen = {}

    def fake(root, cfg, **kwargs):
        seen["kwargs"] = kwargs
        return 7

    monkeypatch.setattr(cli.guard, "run_guard", fake)
    assert cli.main(["guard", "--staged", "--all", "a.txt", "b.py"]) == 7
    assert seen["kwargs"]["staged"] is True
    assert seen["kwargs"]["all_files"] is True
    assert seen["kwargs"]["files"] == ["a.txt", "b.py"]


def test_impact_non_tty_returns_zero(tmp_path, capsys, monkeypatch):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    monkeypatch.chdir(repo)
    assert cli.main(["impact"]) == 0
    out = capsys.readouterr().out
    assert "Impact Analysis Report" in out
    assert "orders (attr at app/urls.py:5)" in out


def test_impact_strict_returns_one(tmp_path, capsys, monkeypatch):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert cli.main(["impact"]) == 1


def test_impact_json_stdout(tmp_path, capsys, monkeypatch):
    repo, base, _head = _build_django_repo(tmp_path)
    _anchor(repo, base)
    monkeypatch.chdir(repo)
    assert cli.main(["impact", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["changed_count"] == 1
    assert data["rows"]


def test_impact_dispatches_flags(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    seen = {}

    def fake(root, cfg, **kwargs):
        seen["kwargs"] = kwargs
        return 3

    monkeypatch.setattr(cli.impact_module, "run_impact", fake)
    assert cli.main(["impact", "--staged", "--fast", "--json"]) == 3
    assert seen["kwargs"]["staged"] is True
    assert seen["kwargs"]["fast"] is True
    assert seen["kwargs"]["json_out"] is True


def test_check_bad_json_returns_one(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _write(repo, "bad.json", "{ not valid\n")
    _git("add", "-A", cwd=repo, env=_git_env())
    _git("commit", "-m", "dev", cwd=repo, env=_git_env())
    monkeypatch.chdir(repo)
    assert cli.main(["check", "syntax/json-syntax", "bad.json"]) == 1
    out = capsys.readouterr().out
    assert "syntax/json-syntax" in out
    assert "bad.json" in out


def test_check_good_json_returns_zero(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "ok.json", "{}\n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["check", "syntax/json-syntax", "ok.json"]) == 0
    assert capsys.readouterr().out == ""


def test_check_trailing_whitespace_fixes(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "trail.txt", "hello   \n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["check", "hygiene/trailing-whitespace", "trail.txt"]) == 0
    out = capsys.readouterr().out
    assert "[fixed] trail.txt" in out
    with open(os.path.join(repo, "trail.txt")) as fh:
        assert fh.read() == "hello\n"


def test_check_missing_id_errors(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "x\n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["check", "nope/missing"]) == 1
    captured = capsys.readouterr()
    assert "nope/missing" in captured.err
    assert "unknown" in captured.err


def test_install_hooks_writes_executable(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["install-hooks"]) == 0
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    assert os.path.exists(hook)
    assert os.stat(hook).st_mode & 0o111
    with open(hook) as fh:
        assert "diffimpactscout guard" in fh.read()


def test_unknown_subcommand_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2


def test_not_a_repo_returns_one(tmp_path, capsys, monkeypatch):
    nonrepo = str(tmp_path / "nonrepo")
    os.makedirs(nonrepo)
    monkeypatch.chdir(nonrepo)
    assert cli.main(["guard"]) == 1
    captured = capsys.readouterr()
    assert "not a git repository" in captured.err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_args_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_install_hooks_non_repo_returns_one(tmp_path, capsys, monkeypatch):
    nonrepo = str(tmp_path / "nonrepo")
    os.makedirs(nonrepo)
    monkeypatch.chdir(nonrepo)
    assert cli.main(["install-hooks"]) == 1
    captured = capsys.readouterr()
    assert "not a git repository" in captured.err


def test_install_hooks_prints_installed(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["install-hooks"]) == 0
    assert "pre-push hook installed" in capsys.readouterr().out


def test_install_hooks_refuses_foreign_without_force(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\n")
    assert cli.main(["install-hooks"]) == 1
    captured = capsys.readouterr()
    assert "pre-push hook exists" in captured.err
    with open(hook) as fh:
        assert fh.read() == "#!/bin/sh\n"


def test_install_hooks_force_overwrites_foreign(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\n")
    assert cli.main(["install-hooks", "--force"]) == 0
    assert "pre-push hook installed" in capsys.readouterr().out
    with open(hook) as fh:
        assert "diffimpactscout pre-push hook" in fh.read()


def test_install_hooks_uninstall(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    assert cli.main(["install-hooks"]) == 0
    assert os.path.exists(hook)
    assert cli.main(["install-hooks", "--uninstall"]) == 0
    assert "pre-push hook removed" in capsys.readouterr().out
    assert not os.path.exists(hook)
    assert cli.main(["install-hooks", "--uninstall"]) == 0
    assert "nothing to remove" in capsys.readouterr().out


def test_install_hooks_uninstall_leaves_foreign(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\n")
    assert cli.main(["install-hooks", "--uninstall"]) == 0
    assert "nothing to remove" in capsys.readouterr().out
    assert os.path.exists(hook)


def test_install_hooks_uninstall_with_force(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    hook = os.path.join(repo, ".git", "hooks", "pre-push")
    assert cli.main(["install-hooks"]) == 0
    assert os.path.exists(hook)
    assert cli.main(["install-hooks", "--uninstall", "--force"]) == 0
    assert "pre-push hook removed" in capsys.readouterr().out
    assert not os.path.exists(hook)


def _install(repo, *args):
    return cli.main(["install-hooks"] + list(args))


def _read_cfg(repo):
    path = os.path.join(repo, ".diffimpactscout.json")
    with open(path) as fh:
        return json.load(fh)


def test_install_hooks_writes_generic_config_on_first_run(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert _install(repo) == 0
    cfg = _read_cfg(repo)
    assert cfg["impact"]["profile"] == "generic"
    assert cfg["guard"]["blocking"] == "warn"


def test_install_hooks_keeps_existing_config(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg_path = os.path.join(repo, ".diffimpactscout.json")
    with open(cfg_path, "w") as fh:
        json.dump({"impact": {"profile": "django"}}, fh)
    monkeypatch.chdir(repo)
    assert _install(repo) == 0
    assert _read_cfg(repo)["impact"]["profile"] == "django"


def test_install_hooks_reconfigure_overwrites(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg_path = os.path.join(repo, ".diffimpactscout.json")
    with open(cfg_path, "w") as fh:
        json.dump({"impact": {"profile": "django"}}, fh)
    monkeypatch.chdir(repo)
    assert _install(repo, "--reconfigure") == 0
    assert _read_cfg(repo)["impact"]["profile"] == "generic"


def test_install_hooks_profile_flag(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert _install(repo, "--profile", "python") == 0
    cfg = _read_cfg(repo)
    assert cfg["impact"]["profile"] == "python"
    assert {"id": "ruff"} in cfg["guard"]["checks"]


def test_install_hooks_yes_flag_keeps_existing_config(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg_path = os.path.join(repo, ".diffimpactscout.json")
    with open(cfg_path, "w") as fh:
        json.dump({"impact": {"profile": "django"}}, fh)
    monkeypatch.chdir(repo)
    assert _install(repo, "--yes") == 0
    assert _read_cfg(repo)["impact"]["profile"] == "django"


def test_install_hooks_blocking_flag(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert _install(repo, "--blocking", "strict") == 0
    assert _read_cfg(repo)["guard"]["blocking"] == "strict"


def _install_answers(repo, answers, args=None):
    queue = list(answers)
    orig_tty = sys.stdin.isatty
    sys.stdin.isatty = lambda: True

    def readline():
        return (queue.pop(0) + "\n") if queue else "\n"

    orig_readline = sys.stdin.readline
    sys.stdin.readline = readline
    try:
        return cli.main(["install-hooks"] + (args or []))
    finally:
        sys.stdin.isatty = orig_tty
        sys.stdin.readline = orig_readline


def test_interactive_confirm_yes_writes(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert _install_answers(repo, ["y"]) == 0
    cfg = _read_cfg(repo)
    assert cfg["impact"]["profile"] == "generic"


def test_interactive_confirm_no_keeps_config(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg_path = os.path.join(repo, ".diffimpactscout.json")
    with open(cfg_path, "w") as fh:
        json.dump({"impact": {"profile": "django"}}, fh)
    monkeypatch.chdir(repo)
    assert _install_answers(repo, ["n"]) == 0
    assert _read_cfg(repo)["impact"]["profile"] == "django"


def test_interactive_override_profile(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "py/x.py", "x = 1\n", "base")
    monkeypatch.chdir(repo)
    answers = ["n", "web", "", "y", "y", "y"]
    assert _install_answers(repo, answers) == 0
    cfg = _read_cfg(repo)
    assert cfg["impact"]["profile"] == "web"
    assert {"id": "eslint"} in cfg["guard"]["checks"]


def test_interactive_override_keep_lint_no(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "py/x.py", "x = 1\n", "base")
    monkeypatch.chdir(repo)
    answers = ["n", "python", "", "n", "y", "y"]
    assert _install_answers(repo, answers) == 0
    cfg = _read_cfg(repo)
    assert {"id": "ruff"} not in cfg["guard"]["checks"]


def test_yes_flag_skips_confirm_even_when_tty(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    monkeypatch.chdir(repo)
    assert _install_answers(repo, [], args=["--yes"]) == 0
    assert _read_cfg(repo)["impact"]["profile"] == "generic"


def test_install_hooks_other_stack_notes_future_layout(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "go.mod", "module x\n", "base")
    monkeypatch.chdir(repo)
    assert _install(repo, "--yes") == 0
    captured = capsys.readouterr()
    assert "planned for future releases" in captured.err
    assert "Go" in captured.err
    assert _read_cfg(repo)["impact"]["profile"] == "generic"


def test_check_missing_file_returns_one(tmp_path, capsys, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "x\n", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["check", "hygiene/trailing-whitespace", "nope.txt"]) == 1
    captured = capsys.readouterr()
    assert "no such file: nope.txt" in captured.err


def test_init_unwritable_parent_returns_one(tmp_path, capsys, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    blocker = os.path.join(proj, "blocker")
    with open(blocker, "w") as fh:
        fh.write("x")
    monkeypatch.setattr(cli.os, "getcwd", lambda: os.path.join(blocker, "sub"))
    assert cli.main(["init"]) == 1
    captured = capsys.readouterr()
    assert "cannot write" in captured.err