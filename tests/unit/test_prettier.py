import json
import os
import subprocess
import sys

from diffimpactscout.checks.base import (
    REGISTRY,
    CheckContext,
    make_check,
)
from diffimpactscout.scope import DevScope


FAKE_NPX = """\
import json
import os
import sys

log = os.environ.get("NPX_FAKE_LOG", "")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(sys.argv) + "\\n")

if sys.argv[1] == "prettier":
    fail = set(json.loads(os.environ.get("NPX_FAKE_PRETTIER_FAIL", "[]")))
    if sys.argv[-1] in fail:
        sys.exit(1)
    sys.exit(int(os.environ.get("NPX_FAKE_PRETTIER_RC", "0")))
sys.exit(0)
"""


def _write_fake_npx(tmp_path, monkeypatch, body=FAKE_NPX):
    bindir = str(tmp_path / "bin")
    os.makedirs(bindir)
    fn = os.path.join(bindir, "npx")
    with open(fn, "w") as fh:
        fh.write("#!" + sys.executable + "\n" + body)
    os.chmod(fn, 0o755)
    monkeypatch.setenv(
        "PATH", bindir + os.pathsep + os.environ.get("PATH", "")
    )
    return fn


class FakeScope(object):
    def __init__(self, contains=()):
        self.files = set(contains)

    def contains(self, anchor, path, from_ref=None, to_ref=None):
        return path in self.files


def _ctx(root, scope=None):
    return CheckContext(
        root=root,
        anchor="A",
        from_ref="F",
        to_ref="T",
        scope=scope or FakeScope(),
        config={},
        echo=False,
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


def _commit(repo, filename, content, msg):
    full = os.path.join(repo, filename)
    parent = os.path.dirname(full)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(full, "w") as fh:
        fh.write(content)
    _git("add", filename, cwd=repo)
    _git("commit", "-m", msg, cwd=repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _write_file(root, relpath, content):
    full = os.path.join(root, relpath)
    parent = os.path.dirname(full)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(full, "w") as fh:
        fh.write(content)


def test_prettier_checks_registered():
    """Verifies that the prettier check is registered as file-scoped."""
    assert "prettier" in REGISTRY
    assert REGISTRY["prettier"].scoped == "files"
    assert REGISTRY["prettier"].blocking is True


def test_prettier_buildable_from_config():
    """Checks that the prettier check can be built from configuration."""
    check = make_check({"id": "prettier"})
    assert check is not None
    assert check.scoped == "files"


def test_prettier_reports_unformatted_file(tmp_path, monkeypatch):
    """Verifies that an unformatted file produces a prettier issue."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["web/src/app.js"]))
    root = str(tmp_path)
    _write_file(root, "web/src/app.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["web/src/app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "web/src/app.js"
    assert issue.code == "prettier"
    assert "not formatted" in issue.message
    assert "npx prettier --write web/src/app.js" in issue.message


def test_prettier_formatted_file_passes(tmp_path, monkeypatch):
    """Checks that a formatted file passes without issues."""
    _write_fake_npx(tmp_path, monkeypatch)
    root = str(tmp_path)
    _write_file(root, "web/src/app.js", "const x = 1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["web/src/app.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_prettier_unsupported_extension_skipped(tmp_path, monkeypatch):
    """Verifies that unsupported extensions are skipped by prettier."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["web/src/app.py"]))
    root = str(tmp_path)
    _write_file(root, "web/src/app.py", "x = 1\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["web/src/app.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_prettier_path_guard_skips_outside_src_app(tmp_path, monkeypatch):
    """Checks that files outside src/ and app/ dirs are skipped."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["lib/app.js"]))
    root = str(tmp_path)
    _write_file(root, "lib/app.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["lib/app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_prettier_top_level_src_checked(tmp_path, monkeypatch):
    """Verifies that a top-level src/ file is checked by prettier."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["src/app.js"]))
    root = str(tmp_path)
    _write_file(root, "src/app.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["src/app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "src/app.js"
    assert "npx prettier --write src/app.js" in result.issues[0].message


def test_prettier_top_level_app_checked(tmp_path, monkeypatch):
    """Checks that a top-level app/ file is checked by prettier."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["app/main.js"]))
    root = str(tmp_path)
    _write_file(root, "app/main.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["app/main.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "app/main.js"


def test_prettier_component_guard_skips_ambiguous_dirs(tmp_path, monkeypatch):
    """Verifies that ambiguous directory names are skipped by the component guard."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    monkeypatch.setenv(
        "NPX_FAKE_PRETTIER_FAIL",
        json.dumps(["asset/src_thing/x.js", "elsewhere/x.js"]),
    )
    root = str(tmp_path)
    _write_file(root, "asset/src_thing/x.js", "const x=1;\n")
    _write_file(root, "elsewhere/x.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["asset/src_thing/x.js", "elsewhere/x.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_prettier_baseline_crlf_and_dotprefix_normalized(
    tmp_path, monkeypatch
):
    """Checks that baseline entries normalize CRLF and dot prefixes."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    root = str(tmp_path)
    with open(os.path.join(root, ".prettierignore_baseline"), "w") as fh:
        fh.write("web/src/app.js\r\n./web/src/other.js\r\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root, FakeScope()), ["web/src/app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)
    result = REGISTRY["prettier"]().run(
        _ctx(root, FakeScope()), ["web/src/other.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_prettier_baseline_skip_unless_dev_changed(tmp_path, monkeypatch):
    """Verifies baseline skips a file unless the dev actually changed it."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    root = str(tmp_path)
    with open(os.path.join(root, ".prettierignore_baseline"), "w") as fh:
        fh.write("web/src/app.js\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root, FakeScope()), ["web/src/app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["web/src/app.js"]))
    result = REGISTRY["prettier"]().run(
        _ctx(root, FakeScope(contains=["web/src/app.js"])), ["web/src/app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1


def test_prettier_missing_npx_warns(tmp_path, monkeypatch):
    """Checks that a missing npx produces a warning without failing."""
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    monkeypatch.setenv("PATH", empty)
    root = str(tmp_path)
    _write_file(root, "web/src/app.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["web/src/app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "prettier" in result.warned[0]


def test_prettier_other_rc_warns(tmp_path, monkeypatch):
    """Verifies that an unexpected prettier exit code triggers a warning."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_RC", "2")
    root = str(tmp_path)
    _write_file(root, "web/src/app.js", "const x=1;\n")
    result = REGISTRY["prettier"]().run(
        _ctx(root), ["web/src/app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "prettier" in result.warned[0]
    assert "2" in result.warned[0]


def test_prettier_real_repo_dev_changed_overrides_baseline(
    tmp_path, monkeypatch
):
    """Checks that a dev-changed file overrides the baseline in a real repo."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["web/src/app.js"]))
    repo = _make_repo(tmp_path)
    _commit(repo, "web/src/app.js", "const x = 1;\n", "initial")
    anchor = _sha(repo)
    _commit(repo, "web/src/app.js", "const x = 1;\nconst y = 2;\n", "edit")
    with open(os.path.join(repo, ".prettierignore_baseline"), "w") as fh:
        fh.write("web/src/app.js\n")
    scope = DevScope(repo, cache_dir=str(tmp_path / "cache"))
    ctx = CheckContext(
        root=repo,
        anchor=anchor,
        from_ref=None,
        to_ref=None,
        scope=scope,
        config={},
        echo=False,
    )
    result = REGISTRY["prettier"]().run(ctx, ["web/src/app.js"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "web/src/app.js"
    assert "npx prettier --write web/src/app.js" in result.issues[0].message


def test_prettier_real_repo_baseline_skip_not_dev_changed(
    tmp_path, monkeypatch
):
    """Verifies a baseline file is skipped when the dev did not change it."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    monkeypatch.setenv("NPX_FAKE_PRETTIER_FAIL", json.dumps(["web/src/app.js"]))
    repo = _make_repo(tmp_path)
    _commit(repo, "web/src/app.js", "const x = 1;\n", "initial")
    _commit(repo, "web/src/other.js", "const y = 1;\n", "other")
    anchor = _sha(repo)
    _commit(repo, "web/src/app.js", "const x = 1;\nconst z = 2;\n", "edit")
    with open(os.path.join(repo, ".prettierignore_baseline"), "w") as fh:
        fh.write("web/src/app.js\nweb/src/other.js\n")
    scope = DevScope(repo, cache_dir=str(tmp_path / "cache"))
    ctx = CheckContext(
        root=repo,
        anchor=anchor,
        from_ref=None,
        to_ref=None,
        scope=scope,
        config={},
        echo=False,
    )
    result = REGISTRY["prettier"]().run(
        ctx, ["web/src/app.js", "web/src/other.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "web/src/app.js"