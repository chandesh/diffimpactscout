import json
import os
import subprocess
import sys

import diffimpactscout.checks.ruff as ruff
from diffimpactscout.checks.base import (
    REGISTRY,
    CheckContext,
    make_check,
)
from diffimpactscout.scope import DevScope


FAKE_RUFF = """\
import json
import os
import sys

log = os.environ.get("RUFF_FAKE_LOG", "")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(sys.argv) + "\\n")

cmd = sys.argv[1]
if cmd == "check":
    data = json.loads(os.environ.get("RUFF_FAKE_VIOLATIONS", "[]"))
    print(json.dumps(data))
    sys.exit(int(os.environ.get("RUFF_FAKE_CHECK_RC", "1")))
elif cmd == "format":
    sub = sys.argv[2]
    if sub == "--check":
        sys.exit(int(os.environ.get("RUFF_FAKE_FORMAT_CHECK_RC", "0")))
    elif sub == "--diff":
        print(os.environ.get("RUFF_FAKE_FORMAT_DIFF", ""), end="")
        sys.exit(int(os.environ.get("RUFF_FAKE_FORMAT_DIFF_RC", "1")))
sys.exit(0)
"""

FORMAT_DIFF = (
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -2,4 +2,4 @@\n"
    "-  x = 1\n"
    "+ x = 1\n"
    "  y = 2\n"
    "-  z = 3\n"
    "+ z = 3\n"
    "@@ -8,3 +8,3 @@\n"
    "-  a = 4\n"
    "+ a = 4\n"
    "  b = 5\n"
)


def _violation(row, col=1, code="E501", message="line too long"):
    return {
        "filename": "ignored.py",
        "location": {"row": row, "column": col},
        "end_location": {"row": row, "column": col + 1},
        "code": code,
        "message": message,
    }


def _write_fake(tmp_path, monkeypatch, body=FAKE_RUFF):
    bindir = str(tmp_path / "bin")
    os.makedirs(bindir)
    fn = os.path.join(bindir, "ruff")
    with open(fn, "w") as fh:
        fh.write("#!" + sys.executable + "\n" + body)
    os.chmod(fn, 0o755)
    monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ.get("PATH", ""))
    return fn


class FakeScope(object):
    def __init__(self, changed=None, tracked=True):
        self.changed = changed
        self.tracked = tracked

    def changed_lines(self, anchor, path, from_ref=None, to_ref=None):
        return self.changed

    def is_tracked(self, path):
        return self.tracked


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
    with open(os.path.join(repo, filename), "w") as fh:
        fh.write(content)
    _git("add", filename, cwd=repo)
    _git("commit", "-m", msg, cwd=repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def test_ruff_checks_registered():
    """Verifies that ruff checks are registered with expected scope and blocking flags."""
    for cid, scoped in (("ruff", "lines"), ("ruff-format", "lines")):
        assert cid in REGISTRY
        assert REGISTRY[cid].scoped == scoped
        assert REGISTRY[cid].blocking is True


def test_ruff_checks_buildable_from_config():
    """Checks that every ruff check can be built from its config id."""
    for cid in ("ruff", "ruff-format"):
        check = make_check({"id": cid})
        assert check is not None
        assert check.scoped == "lines"


def test_ruff_reports_only_changed_line_violations(tmp_path, monkeypatch):
    """Verifies that ruff reports only violations on changed lines."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RUFF_FAKE_VIOLATIONS",
        json.dumps([_violation(5), _violation(20)]),
    )
    root = str(tmp_path)
    with open(os.path.join(root, "app.py"), "w") as fh:
        fh.write("x = 1\n" * 20)
    scope = FakeScope(changed={5})
    result = REGISTRY["ruff"]().run(_ctx(root, scope), ["app.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "app.py"
    assert issue.line == 5
    assert issue.column == 1
    assert issue.code == "E501"
    assert "line too long" in issue.message


def test_ruff_untracked_file_checks_whole_file(tmp_path, monkeypatch):
    """Verifies that untracked files are checked across the whole file."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RUFF_FAKE_VIOLATIONS",
        json.dumps([_violation(5), _violation(20)]),
    )
    root = str(tmp_path)
    with open(os.path.join(root, "u.py"), "w") as fh:
        fh.write("x = 1\n" * 20)
    scope = FakeScope(changed=None, tracked=False)
    result = REGISTRY["ruff"]().run(_ctx(root, scope), ["u.py"])
    assert not result.ok()
    assert len(result.issues) == 2
    assert [i.line for i in result.issues] == [5, 20]


def test_ruff_empty_changed_set_skips_file(tmp_path, monkeypatch):
    """Checks that an empty changed-line set skips running ruff on the file."""
    log = str(tmp_path / "log.txt")
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_LOG", log)
    monkeypatch.setenv("RUFF_FAKE_VIOLATIONS", json.dumps([_violation(5)]))
    root = str(tmp_path)
    with open(os.path.join(root, "app.py"), "w") as fh:
        fh.write("x = 1\n" * 10)
    scope = FakeScope(changed=set())
    result = REGISTRY["ruff"]().run(_ctx(root, scope), ["app.py"])
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_ruff_missing_tool_warns_no_block(tmp_path, monkeypatch):
    """Verifies that a missing ruff tool warns without blocking."""
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    monkeypatch.setenv("PATH", empty)
    root = str(tmp_path)
    with open(os.path.join(root, "app.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff"]().run(
        _ctx(root, FakeScope(changed={1})), ["app.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "ruff" in result.warned[0]


def test_ruff_rc2_tool_problem_warns(tmp_path, monkeypatch):
    """Verifies that a ruff rc=2 exit is reported as a warning."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_CHECK_RC", "2")
    root = str(tmp_path)
    with open(os.path.join(root, "app.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff"]().run(
        _ctx(root, FakeScope(changed={1})), ["app.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "ruff" in result.warned[0]
    assert "2" in result.warned[0]


def test_ruff_non_py_files_skipped(tmp_path, monkeypatch):
    """Checks that non-Python files are skipped by the ruff check."""
    log = str(tmp_path / "log.txt")
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_LOG", log)
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x = 1;\n")
    result = REGISTRY["ruff"]().run(_ctx(root), ["app.js"])
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_ruff_real_repo_incremental(tmp_path, monkeypatch):
    """Verifies incremental ruff checks on a real git repo report only changed lines."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RUFF_FAKE_VIOLATIONS",
        json.dumps([_violation(5), _violation(7)]),
    )
    repo = _make_repo(tmp_path)
    _commit(repo, "app.py", "a = 1\nb = 2\nc = 3\nd = 4\nBAD = 5\n", "initial")
    anchor = _sha(repo)
    _commit(
        repo,
        "app.py",
        "a = 1\nb = 2\nc = 3\nd = 4\nBAD = 5\ne = 6\nBAD = 7\n",
        "edit",
    )
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
    assert ctx.changed_lines("app.py") == {6, 7}
    result = REGISTRY["ruff"]().run(ctx, ["app.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "app.py"
    assert result.issues[0].line == 7


def test_ruff_real_repo_untracked_whole_file(tmp_path, monkeypatch):
    """Verifies untracked files in a real repo are checked across the whole file."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_VIOLATIONS", json.dumps([_violation(3)]))
    repo = _make_repo(tmp_path)
    _commit(repo, "tracked.py", "x = 1\n", "initial")
    with open(os.path.join(repo, "u.py"), "w") as fh:
        fh.write("a = 1\nb = 2\nBAD = 3\n")
    scope = DevScope(repo, cache_dir=str(tmp_path / "cache"))
    ctx = CheckContext(
        root=repo,
        anchor=_sha(repo),
        from_ref=None,
        to_ref=None,
        scope=scope,
        config={},
        echo=False,
    )
    assert ctx.changed_lines("u.py") is None
    assert ctx.is_tracked("u.py") is False
    result = REGISTRY["ruff"]().run(ctx, ["u.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line == 3


def test_filter_diff_by_lines_keeps_only_changed_blocks():
    """Verifies that diff filtering keeps only hunks touching changed lines."""
    kept = ruff._filter_diff_by_lines(FORMAT_DIFF, {4})
    assert "-  z = 3" in kept
    assert "+ z = 3" in kept
    assert "-  x = 1" not in kept
    assert "-  a = 4" not in kept
    assert "@@ -2,4 +2,4 @@" in kept


def test_filter_diff_by_lines_none_keeps_whole_diff():
    """Verifies that a None filter keeps the whole diff unchanged."""
    assert ruff._filter_diff_by_lines(FORMAT_DIFF, None) == FORMAT_DIFF


def test_filter_diff_by_lines_empty_returns_empty():
    """Verifies that an empty filter set returns an empty diff."""
    assert ruff._filter_diff_by_lines(FORMAT_DIFF, set()) == ""


def test_filter_diff_by_lines_no_match_returns_empty():
    """Verifies that a filter with no matching lines returns an empty diff."""
    assert ruff._filter_diff_by_lines(FORMAT_DIFF, {50}) == ""


def test_ruff_format_reports_only_changed_blocks(tmp_path, monkeypatch):
    """Verifies that ruff-format reports issues only for changed blocks."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "1")
    monkeypatch.setenv("RUFF_FAKE_FORMAT_DIFF", FORMAT_DIFF)
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    scope = FakeScope(changed={4})
    result = REGISTRY["ruff-format"]().run(_ctx(root, scope), ["f.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "f.py"
    assert issue.code == "ruff-format"
    assert "-  z = 3" in issue.message
    assert "+ z = 3" in issue.message
    assert "-  x = 1" not in issue.message
    assert "-  a = 4" not in issue.message
    assert "Fix with: ruff format f.py" in issue.message


def test_ruff_format_clean_passes(tmp_path, monkeypatch):
    """Verifies that a clean ruff-format check passes with no issues."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "0")
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff-format"]().run(
        _ctx(root, FakeScope(changed={1})), ["f.py"]
    )
    assert result.ok()
    assert result.issues == []


def test_ruff_format_other_rc_warns(tmp_path, monkeypatch):
    """Verifies that a non-zero ruff-format rc is reported as a warning."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "2")
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff-format"]().run(
        _ctx(root, FakeScope(changed={1})), ["f.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "ruff" in result.warned[0]


def test_ruff_format_missing_tool_warns(tmp_path, monkeypatch):
    """Verifies that a missing ruff tool warns for the format check."""
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    monkeypatch.setenv("PATH", empty)
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff-format"]().run(
        _ctx(root, FakeScope(changed={1})), ["f.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1


def test_ruff_format_diff_rc2_warns_no_block(tmp_path, monkeypatch):
    """Verifies that a ruff-format diff rc=2 warns without blocking."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "1")
    monkeypatch.setenv("RUFF_FAKE_FORMAT_DIFF_RC", "2")
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    result = REGISTRY["ruff-format"]().run(
        _ctx(root, FakeScope(changed={1})), ["f.py"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "ruff" in result.warned[0]
    assert "2" in result.warned[0]


def test_filter_diff_by_lines_body_starts_with_hunk_header():
    """Verifies diff filtering keeps blocks when the diff body starts with a hunk header."""
    diff = (
        "@@ -2,4 +2,4 @@\n"
        "-  x = 1\n"
        "+ x = 1\n"
        "  y = 2\n"
        "@@ -8,3 +8,3 @@\n"
        "-  a = 4\n"
        "+ a = 4\n"
    )
    kept = ruff._filter_diff_by_lines(diff, {2})
    assert "-  x = 1" in kept
    assert "+ x = 1" in kept
    assert "-  a = 4" not in kept
    assert "@@ -2,4 +2,4 @@" in kept


def test_filter_diff_by_lines_all_hunks_no_header():
    """Verifies diff filtering keeps hunks when no file header is present."""
    diff = (
        "@@ -1,2 +1,2 @@\n"
        "- a\n"
        "+ b\n"
    )
    kept = ruff._filter_diff_by_lines(diff, {1})
    assert "- a" in kept
    assert "+ b" in kept


def test_filter_diff_by_lines_handles_dashed_content():
    """Verifies diff filtering correctly handles lines containing dashes."""
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,3 +1,3 @@\n"
        "---OLD---\n"
        "+-NEW+++\n"
        "  keep\n"
    )
    kept = ruff._filter_diff_by_lines(diff, {1})
    assert "---OLD---" in kept
    assert "+-NEW+++" in kept
    assert "@@ -1,3 +1,3 @@" in kept
    assert "  keep" not in kept


def test_ruff_format_untracked_keeps_whole_diff(tmp_path, monkeypatch):
    """Verifies that untracked files keep the whole format diff."""
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "1")
    monkeypatch.setenv("RUFF_FAKE_FORMAT_DIFF", FORMAT_DIFF)
    root = str(tmp_path)
    with open(os.path.join(root, "u.py"), "w") as fh:
        fh.write("x = 1\n")
    scope = FakeScope(changed=None, tracked=False)
    result = REGISTRY["ruff-format"]().run(_ctx(root, scope), ["u.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert "-  x = 1" in result.issues[0].message
    assert "-  a = 4" in result.issues[0].message
    assert "Fix with: ruff format u.py" in result.issues[0].message


def test_ruff_format_empty_changed_set_skips(tmp_path, monkeypatch):
    """Checks that an empty changed-line set skips running ruff-format."""
    log = str(tmp_path / "log.txt")
    _write_fake(tmp_path, monkeypatch)
    monkeypatch.setenv("RUFF_FAKE_LOG", log)
    monkeypatch.setenv("RUFF_FAKE_FORMAT_CHECK_RC", "1")
    monkeypatch.setenv("RUFF_FAKE_FORMAT_DIFF", FORMAT_DIFF)
    root = str(tmp_path)
    with open(os.path.join(root, "f.py"), "w") as fh:
        fh.write("x = 1\n")
    scope = FakeScope(changed=set())
    result = REGISTRY["ruff-format"]().run(_ctx(root, scope), ["f.py"])
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)