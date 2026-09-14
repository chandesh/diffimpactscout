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

if sys.argv[1] == "eslint":
    data = json.loads(os.environ.get("NPX_FAKE_ESLINT_JSON", "[]"))
    print(json.dumps(data))
    sys.exit(int(os.environ.get("NPX_FAKE_ESLINT_RC", "0")))
sys.exit(0)
"""


def _message(line, col=2, rule="no-undef", text="x is not defined"):
    return {"line": line, "column": col, "ruleId": rule, "message": text}


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


def _set_json(monkeypatch, messages):
    monkeypatch.setenv(
        "NPX_FAKE_ESLINT_JSON",
        json.dumps([{"filePath": "/repo/app.js", "messages": messages}]),
    )


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


def _write_baseline(root, data):
    with open(os.path.join(root, ".eslint_baseline.json"), "w") as fh:
        fh.write(json.dumps(data))


def test_eslint_checks_registered():
    """Verifies that the eslint check is registered as scoped and blocking."""
    assert "eslint" in REGISTRY
    assert REGISTRY["eslint"].scoped == "lines"
    assert REGISTRY["eslint"].blocking is True


def test_eslint_buildable_from_config():
    """Checks that the eslint check can be built from configuration."""
    check = make_check({"id": "eslint"})
    assert check is not None
    assert check.scoped == "lines"


def test_eslint_reports_only_changed_line_issues(tmp_path, monkeypatch):
    """Verifies that eslint only reports issues on changed lines."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5), _message(20)])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 20)
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "app.js"
    assert issue.line == 5
    assert issue.column == 2
    assert issue.code == "no-undef"
    assert "not defined" in issue.message


def test_eslint_untracked_file_checks_whole_file(tmp_path, monkeypatch):
    """Checks that untracked files are checked across the whole file."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5), _message(20)])
    root = str(tmp_path)
    with open(os.path.join(root, "u.js"), "w") as fh:
        fh.write("var x;\n" * 20)
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed=None, tracked=False)), ["u.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 2
    assert [i.line for i in result.issues] == [5, 20]


def test_eslint_empty_changed_set_skips_file(tmp_path, monkeypatch):
    """Verifies that a file with no changed lines is skipped entirely."""
    log = str(tmp_path / "log.txt")
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_LOG", log)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 10)
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed=set())), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert not os.path.exists(log)


def test_eslint_missing_npx_warns_no_block(tmp_path, monkeypatch):
    """Checks that a missing npx warns without blocking the run."""
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    monkeypatch.setenv("PATH", empty)
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n")
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={1})), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "eslint" in result.warned[0]


def test_eslint_rc2_tool_problem_warns(tmp_path, monkeypatch):
    """Verifies that an rc2 exit code warns about a tool problem."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_ESLINT_RC", "2")
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n")
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={1})), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1
    assert "eslint" in result.warned[0]
    assert "2" in result.warned[0]


def test_eslint_invalid_json_ignored(tmp_path, monkeypatch):
    """Checks that invalid JSON output from eslint is silently ignored."""
    _write_fake_npx(tmp_path, monkeypatch)
    monkeypatch.setenv("NPX_FAKE_ESLINT_JSON", "not-json")
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n")
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={1})), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []


def _write_src_file(root):
    srcdir = os.path.join(root, "src")
    os.makedirs(srcdir)
    with open(os.path.join(srcdir, "a.js"), "w") as fh:
        fh.write("var x;\n" * 10)


def test_eslint_baseline_suppresses_issue_on_changed_line(tmp_path, monkeypatch):
    """Verifies that a baseline issue on a changed line is suppressed."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    _write_src_file(root)
    _write_baseline(
        root,
        [
            {
                "filePath": "src/a.js",
                "messages": [_message(5, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["src/a.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_eslint_baseline_reports_non_suppressed_issue_on_changed_line(
    tmp_path, monkeypatch
):
    """Checks that non-suppressed issues on changed lines are reported."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5, col=1), _message(5, col=2)])
    root = str(tmp_path)
    _write_src_file(root)
    _write_baseline(
        root,
        [
            {
                "filePath": "src/a.js",
                "messages": [_message(5, col=1, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["src/a.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].column == 2


def test_eslint_baseline_absolute_filepath_suppresses(tmp_path, monkeypatch):
    """Verifies that absolute baseline file paths suppress matching issues."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    _write_src_file(root)
    _write_baseline(
        root,
        [
            {
                "filePath": os.path.join(root, "src", "a.js"),
                "messages": [_message(5, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["src/a.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_eslint_baseline_results_wrapper_suppresses(tmp_path, monkeypatch):
    """Checks that a results-wrapped baseline suppresses matching issues."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    _write_src_file(root)
    _write_baseline(
        root,
        {
            "results": [
                {
                    "filePath": "src/a.js",
                    "messages": [_message(5, rule="no-undef")],
                }
            ]
        },
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["src/a.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_eslint_baseline_dict_form_suppresses(tmp_path, monkeypatch):
    """Verifies that a dict-form baseline suppresses matching issues."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 10)
    _write_baseline(root, {"app.js": ["5:2:no-undef"]})
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_eslint_baseline_changed_elsewhere_still_reports_new(
    tmp_path, monkeypatch
):
    """Checks that new issues on lines changed elsewhere are still reported."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(
        monkeypatch,
        [_message(5), _message(6, rule="no-var", text="avoid var")],
    )
    root = str(tmp_path)
    _write_src_file(root)
    _write_baseline(
        root,
        [
            {
                "filePath": "src/a.js",
                "messages": [_message(5, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5, 6})), ["src/a.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line == 6
    assert result.issues[0].code == "no-var"


def test_eslint_baseline_otherapp_js_does_not_suppress(tmp_path, monkeypatch):
    """Verifies that a baseline for another app does not suppress issues."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 10)
    _write_baseline(
        root,
        [
            {
                "filePath": "/repo/otherapp.js",
                "messages": [_message(5, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "app.js"


def test_eslint_baseline_suffix_otherapp_js_does_not_suppress(
    tmp_path, monkeypatch
):
    """Checks that a matching suffix baseline for another app does not suppress."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5)])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 10)
    _write_baseline(
        root,
        [
            {
                "filePath": "vendor/deep/otherapp.js",
                "messages": [_message(5, rule="no-undef")],
            }
        ],
    )
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["app.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "app.js"


def test_eslint_message_without_line_tracked_not_reported(
    tmp_path, monkeypatch
):
    """Checks that line-less messages on tracked files are not reported."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [{"fatal": True, "message": "Parsing error"}])
    root = str(tmp_path)
    with open(os.path.join(root, "app.js"), "w") as fh:
        fh.write("var x;\n" * 10)
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed={5})), ["app.js"]
    )
    assert result.ok()
    assert result.issues == []


def test_eslint_message_without_line_untracked_reported(
    tmp_path, monkeypatch
):
    """Verifies that line-less messages on untracked files are reported."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [{"fatal": True, "message": "Parsing error"}])
    root = str(tmp_path)
    with open(os.path.join(root, "u.js"), "w") as fh:
        fh.write("var x;\n")
    result = REGISTRY["eslint"]().run(
        _ctx(root, FakeScope(changed=None, tracked=False)), ["u.js"]
    )
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line is None
    assert result.issues[0].code == "eslint"


def test_eslint_real_repo_incremental(tmp_path, monkeypatch):
    """Verifies incremental linting reports only issues on changed lines."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(3), _message(7)])
    repo = _make_repo(tmp_path)
    _commit(repo, "app.js", "var a;\nvar b;\nvar c;\n", "initial")
    anchor = _sha(repo)
    _commit(
        repo,
        "app.js",
        "var a;\nvar b;\nvar c;\nvar d;\nvar e;\nvar f;\nvar g;\n",
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
    assert ctx.changed_lines("app.js") == {4, 5, 6, 7}
    result = REGISTRY["eslint"]().run(ctx, ["app.js"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line == 7


def test_eslint_real_repo_baseline_suppression(tmp_path, monkeypatch):
    """Checks that a real-repo baseline suppresses issues on changed lines."""
    _write_fake_npx(tmp_path, monkeypatch)
    _set_json(monkeypatch, [_message(5), _message(6)])
    repo = _make_repo(tmp_path)
    _commit(
        repo,
        "app.js",
        "var a;\nvar b;\nvar c;\nvar d;\nvar e;\n",
        "initial",
    )
    anchor = _sha(repo)
    _commit(
        repo,
        "app.js",
        "var a;\nvar b;\nvar c;\nvar d;\nvar e;\nvar f;\n",
        "edit",
    )
    _write_baseline(
        repo,
        [
            {
                "filePath": os.path.join(repo, "app.js"),
                "messages": [_message(6, rule="no-undef")],
            }
        ],
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
    assert ctx.changed_lines("app.js") == {6}
    result = REGISTRY["eslint"]().run(ctx, ["app.js"])
    assert result.ok()
    assert result.issues == []