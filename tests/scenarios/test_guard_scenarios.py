import os
import subprocess
import sys

import pytest

import diffimpactscout
import diffimpactscout.base as base
import diffimpactscout.config as config
import diffimpactscout.guard as guard
import diffimpactscout.launcher as launcher
import diffimpactscout.scope as scope
from helpers import GitRepo

SRC = os.path.dirname(os.path.dirname(os.path.abspath(diffimpactscout.__file__)))

RUFF_BODY = (
    "import json\n"
    "import os\n"
    "import sys\n"
    "case = os.environ.get('FAKE_RUFF_CASE', 'empty')\n"
    "path = sys.argv[2]\n"
    "violations = []\n"
    "if case == 'upstream':\n"
    "    if path == 'shared.py':\n"
    "        violations.append({'filename': 'shared.py', 'location': {'row': 1, 'column': 2}, 'code': 'E501', 'message': 'upstream-line-violation'})\n"
    "    elif path == 'u2.py':\n"
    "        violations.append({'filename': 'u2.py', 'location': {'row': 1, 'column': 2}, 'code': 'E501', 'message': 'upstream-file-violation'})\n"
    "elif case == 'dev':\n"
    "    if path == 'f3.py':\n"
    "        violations.append({'filename': 'f3.py', 'location': {'row': 1, 'column': 2}, 'code': 'E501', 'message': 'dev-violation'})\n"
    "json.dump(violations, sys.stdout)\n"
)

FMT_BODY = (
    "import os\n"
    "import sys\n"
    "case = os.environ.get('FAKE_FMT_CASE', 'empty')\n"
    "if sys.argv[1] != 'format':\n"
    "    sys.exit(0)\n"
    "flag = sys.argv[2]\n"
    "path = sys.argv[3]\n"
    "if case == 'fmt-upstream' and path == 'shared.py':\n"
    "    if flag == '--check':\n"
    "        sys.exit(1)\n"
    "    if flag == '--diff':\n"
    "        sys.stdout.write('--- a/shared.py\\n+++ b/shared.py\\n@@ -1,1 +1,1 @@\\n-UPSTREAM_MARKER_LINE\\n+UPSTREAM_MARKER_LINE_FMT\\n')\n"
    "        sys.exit(0)\n"
    "elif case == 'fmt-dev' and path == 'f3.py':\n"
    "    if flag == '--check':\n"
    "        sys.exit(1)\n"
    "    if flag == '--diff':\n"
    "        sys.stdout.write('--- a/f3.py\\n+++ b/f3.py\\n@@ -1,1 +1,1 @@\\n-D3 9999\\n+D3 9999 FMT\\n')\n"
    "        sys.exit(0)\n"
    "sys.exit(0)\n"
)

ESLINT_BODY = (
    "import json\n"
    "import os\n"
    "import sys\n"
    "tool = sys.argv[1]\n"
    "if tool != 'eslint':\n"
    "    sys.exit(0)\n"
    "path = sys.argv[2]\n"
    "case = os.environ.get('FAKE_ESLINT_CASE', 'empty')\n"
    "messages = []\n"
    "if case == 'upstream':\n"
    "    if path == 'shared.ts':\n"
    "        messages.append({'line': 1, 'column': 1, 'message': 'upstream-line-msg', 'ruleId': 'fake'})\n"
    "    elif path == 'u2.ts':\n"
    "        messages.append({'line': 1, 'column': 1, 'message': 'upstream-file-msg', 'ruleId': 'fake'})\n"
    "elif case == 'dev':\n"
    "    if path == 'c.ts':\n"
    "        messages.append({'line': 1, 'column': 1, 'message': 'dev-msg', 'ruleId': 'fake'})\n"
    "json.dump([{'filePath': '/repo/' + path, 'messages': messages}], sys.stdout)\n"
)

PRETTIER_BODY = (
    "import os\n"
    "import sys\n"
    "tool = sys.argv[1]\n"
    "if tool != 'prettier':\n"
    "    sys.exit(0)\n"
    "if sys.argv[2] != '--check':\n"
    "    sys.exit(0)\n"
    "path = sys.argv[3]\n"
    "case = os.environ.get('FAKE_PRETTIER_CASE', 'empty')\n"
    "fail = (case == 'upstream' and path == 'media/src/u2.ts') or (case == 'dev' and path == 'media/src/c.ts')\n"
    "sys.exit(1 if fail else 0)\n"
)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "default_cache_dir", lambda: str(tmp_path / "cache"))
    for name in (
        "PRE_COMMIT_FROM_REF",
        "PRE_COMMIT_TO_REF",
        "IMPACT_CHECK_SKIP",
        "IMPACT_CHECK_STRICT",
        "DIFFIMPACTSCOUT_SKIP",
        "FAKE_RUFF_CASE",
        "FAKE_FMT_CASE",
        "FAKE_ESLINT_CASE",
        "FAKE_PRETTIER_CASE",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def _setup(base):
    repo = GitRepo.init(os.path.join(base, "work"))
    repo.write("m0.txt", "M0\n")
    repo.commit("M0")
    repo.remote("origin")
    return repo


def _cfg(repo, checks):
    cfg = config.load_config(repo.root)
    cfg["guard"]["checks"] = checks
    return cfg


def _dev_files(repo):
    anchor = base.resolve_change_base(repo.root)
    assert anchor is not None
    return sorted(scope.DevScope(repo.root).dev_files(anchor))


def _append_line(repo, path, line):
    with open(os.path.join(repo.root, path), "a") as fh:
        fh.write(line + "\n")


def _read_bytes(repo, path):
    with open(os.path.join(repo.root, path), "rb") as fh:
        return fh.read()


def _write_bin(tmp_path, name, body):
    bindir = os.path.join(str(tmp_path), "bin")
    if not os.path.isdir(bindir):
        os.makedirs(bindir)
    fn = os.path.join(bindir, name)
    with open(fn, "w") as fh:
        fh.write("#!" + sys.executable + "\n" + body)
    os.chmod(fn, 0o755)
    return fn


def _prepend_bin(monkeypatch, tmp_path):
    bindir = os.path.join(str(tmp_path), "bin")
    monkeypatch.setenv(
        "PATH", bindir + os.pathsep + os.environ.get("PATH", "")
    )


def test_01_direct_push(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.txt", "three\n")
    repo.commit("D3")
    repo.write("f4.txt", "four\n")
    repo.commit("D4")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt", "f3.txt", "f4.txt"]


def test_02_merge_sync(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.txt", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("shared.txt", "UPSTREAM_MARKER_LINE\n")
    repo.write("u2.txt", "u2\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    _append_line(repo, "shared.txt", "DEV_MARKER_LINE")
    repo.commit("D4")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt", "f3.txt", "shared.txt"]
    changed = scope.DevScope(repo.root).changed_lines(anchor, "shared.txt")
    assert changed == set([2])


def test_03_rebase_sync(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.txt", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("u1.txt", "u1\n")
    repo.commit("U1")
    repo.write("u2.txt", "u2\n")
    repo.commit("U2")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.rebase("origin/master")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt", "f3.txt"]


def test_04_squash_sync(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.txt", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("u1.txt", "u1\n")
    repo.commit("U1")
    repo.write("u2.txt", "u2\n")
    repo.commit("U2")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.must("merge", "-q", "--squash", "origin/master")
    repo.must("commit", "-q", "-m", "squash sync master")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt", "f3.txt"]


def test_05_force_push(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.txt", "three\n")
    repo.commit("D3")
    repo.write("f3.txt", "rewritten\n")
    repo.must("add", "-A")
    repo.must("commit", "-q", "--amend", "-m", "D3 (rewritten)")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt", "f3.txt"]


def test_06_new_branch(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["f1.txt", "f2.txt"]


def test_07_stacked_branch(tmp_path):
    repo = _setup(str(tmp_path))
    repo.branch("f1", "master")
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    repo.write("f2.txt", "two\n")
    repo.commit("D2")
    repo.push("origin", "f1")
    repo.branch("f2", "f1")
    repo.write("e1.txt", "e1\n")
    repo.commit("E1")
    repo.push("origin", "f2")
    repo.write("e2.txt", "e2\n")
    repo.commit("E2")
    repo.checkout("master")
    repo.write("u1.txt", "u1\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("f2")
    anchor = base.resolve_change_base(repo.root)
    assert anchor == "refs/remotes/origin/master"
    assert _dev_files(repo) == ["e1.txt", "e2.txt", "f1.txt", "f2.txt"]


def test_08_shallow_clones(tmp_path, capsys):
    seed = _setup(str(tmp_path))
    seed.write("u1.txt", "u1\n")
    seed.commit("U1")
    seed.write("u2.txt", "u2\n")
    seed.commit("U2")
    seed.push("origin", "master")
    seed.branch("feature", "master")
    seed.write("f1.txt", "f1\n")
    seed.commit("D1")
    seed.push("origin", "feature")
    bare = os.path.join(os.path.dirname(seed.root), "origin.git")
    shallow_f = GitRepo.clone(
        bare, os.path.join(str(tmp_path), "shallow-feature"),
        branch="feature", depth=1,
    )
    refs = shallow_f.git_lines("for-each-ref", "--format=%(refname)", "refs/remotes/")
    assert refs == ["refs/remotes/origin/feature"]
    assert base.resolve_change_base(shallow_f.root) is None
    cfg = _cfg(shallow_f, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(shallow_f.root, cfg) == 0
    captured = capsys.readouterr()
    assert "falling back to push range" in captured.err
    shallow_m = GitRepo.clone(
        bare, os.path.join(str(tmp_path), "shallow-master"), depth=1
    )
    anchor = base.resolve_change_base(shallow_m.root)
    assert anchor == "refs/remotes/origin/master"
    shallow_m.branch("feature", "master")
    shallow_m.write("f1.txt", "f1\n")
    shallow_m.commit("D1")
    assert _dev_files(shallow_m) == ["f1.txt"]


def test_09_no_anchor(tmp_path, capsys):
    repo = _setup(str(tmp_path))
    repo.write("f1.txt", "one\n")
    repo.commit("D1")
    res = repo.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    if not res.ok():
        try:
            os.remove(
                os.path.join(repo.root, ".git", "refs", "remotes", "origin", "HEAD")
            )
        except OSError:
            pass
    repo.must("update-ref", "-d", "refs/remotes/origin/master")
    assert base.resolve_change_base(repo.root) is None
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo.root, cfg) == 0
    captured = capsys.readouterr()
    assert "falling back to push range" in captured.err


def test_10_ruff_sync(tmp_path, monkeypatch, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.py", "one\n")
    repo.commit("D1")
    repo.write("f2.py", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.py", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("shared.py", "UPSTREAM_MARKER_LINE\n")
    repo.write("u2.py", "u2\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    _append_line(repo, "shared.py", "DEV_MARKER_LINE")
    repo.commit("D4")
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    _write_bin(tmp_path, "ruff", RUFF_BODY)
    _prepend_bin(monkeypatch, tmp_path)
    cfg = _cfg(repo, [{"id": "ruff"}])
    monkeypatch.setenv("FAKE_RUFF_CASE", "upstream")
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert "shared.py" not in out
    monkeypatch.setenv("FAKE_RUFF_CASE", "dev")
    assert guard.run_guard(repo.root, cfg) == 1
    out = capsys.readouterr().out
    assert "dev-violation" in out


def test_11_eslint_sync(tmp_path, monkeypatch, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("a.ts", "one\n")
    repo.commit("D1")
    repo.write("b.ts", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("c.ts", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("shared.ts", "UPSTREAM_MARKER_LINE\n")
    repo.write("u2.ts", "u2\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    _append_line(repo, "shared.ts", "DEV_MARKER_LINE")
    repo.commit("D4")
    assert _dev_files(repo) == ["a.ts", "b.ts", "c.ts", "shared.ts"]
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    _write_bin(tmp_path, "npx", ESLINT_BODY)
    _prepend_bin(monkeypatch, tmp_path)
    cfg = _cfg(repo, [{"id": "eslint"}])
    monkeypatch.setenv("FAKE_ESLINT_CASE", "upstream")
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert "shared.ts" not in out
    monkeypatch.setenv("FAKE_ESLINT_CASE", "dev")
    assert guard.run_guard(repo.root, cfg) == 1
    out = capsys.readouterr().out
    assert "dev-msg" in out


def test_12_format_check_sync(tmp_path, monkeypatch, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.py", "one\n")
    repo.commit("D1")
    repo.write("f2.py", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("f3.py", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("shared.py", "UPSTREAM_MARKER_LINE\n")
    repo.write("u2.py", "u2\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    _append_line(repo, "shared.py", "DEV_MARKER_LINE")
    repo.commit("D4")
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    _write_bin(tmp_path, "ruff", FMT_BODY)
    _prepend_bin(monkeypatch, tmp_path)
    cfg = _cfg(repo, [{"id": "ruff-format"}])
    monkeypatch.setenv("FAKE_FMT_CASE", "fmt-upstream")
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert "UPSTREAM_MARKER_LINE_FMT" not in out
    monkeypatch.setenv("FAKE_FMT_CASE", "fmt-dev")
    assert guard.run_guard(repo.root, cfg) == 1
    out = capsys.readouterr().out
    assert "D3 9999 FMT" in out


def test_13_prettier_sync(tmp_path, monkeypatch, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("media/src/a.ts", "one\n")
    repo.commit("D1")
    repo.write("media/src/b.ts", "two\n")
    repo.commit("D2")
    repo.push("origin", "feature")
    repo.write("media/src/c.ts", "three\n")
    repo.commit("D3")
    repo.checkout("master")
    repo.write("media/src/shared.ts", "UPSTREAM_MARKER_LINE\n")
    repo.write("media/src/u2.ts", "u2\n")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    _append_line(repo, "media/src/shared.ts", "DEV_MARKER_LINE")
    repo.commit("D4")
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    _write_bin(tmp_path, "npx", PRETTIER_BODY)
    _prepend_bin(monkeypatch, tmp_path)
    cfg = _cfg(repo, [{"id": "prettier"}])
    monkeypatch.setenv("FAKE_PRETTIER_CASE", "upstream")
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert "media/src/u2.ts" not in out
    monkeypatch.setenv("FAKE_PRETTIER_CASE", "dev")
    assert guard.run_guard(repo.root, cfg) == 1
    out = capsys.readouterr().out
    assert "media/src/c.ts" in out


def test_14_scope_fixer(tmp_path, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("dev_crlf.py", "x\r\ny\r\n")
    repo.commit("D1")
    repo.push("origin", "feature")
    repo.checkout("master")
    repo.write("up_crlf.py", "r\r\ns\r\n")
    repo.write("up_ws.py", "a \nb\n\n")
    repo.write("up_eof.py", "z")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    repo.write("dev_2.py", "dev \r\n")
    repo.commit("D2")

    cfg = _cfg(repo, [{"id": "hygiene/mixed-line-ending"}])
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert _read_bytes(repo, "dev_2.py") == b"dev \n"
    assert _read_bytes(repo, "up_crlf.py") == b"r\r\ns\r\n"
    assert "dev_2.py" in out
    assert "up_crlf" not in out

    cfg = _cfg(repo, [{"id": "hygiene/trailing-whitespace"}])
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert _read_bytes(repo, "dev_2.py") == b"dev\n"
    assert _read_bytes(repo, "up_ws.py") == b"a \nb\n\n"
    assert "up_ws.py" not in out

    cfg = _cfg(repo, [{"id": "hygiene/end-of-file-fixer"}])
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert _read_bytes(repo, "dev_2.py") == b"dev\n"
    assert _read_bytes(repo, "up_eof.py") == b"z"
    assert "up_eof.py" not in out

    cfg = _cfg(repo, [{"id": "hygiene/mixed-line-ending", "args": ["--fix=crlf"]}])
    assert guard.run_guard(repo.root, cfg, files=["up_crlf.py"]) == 0
    captured = capsys.readouterr()
    assert "mixed-line-ending" in captured.err
    assert _read_bytes(repo, "up_crlf.py") == b"r\r\ns\r\n"

    repo.must("remote", "remove", "origin")
    cfg = _cfg(repo, [{"id": "hygiene/mixed-line-ending"}])
    assert guard.run_guard(repo.root, cfg, files=["up_crlf.py"]) == 0
    assert _read_bytes(repo, "up_crlf.py") == b"r\ns\n"


def test_15_dev_scope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "fakefrom")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "faketo")
    repo = _setup(str(tmp_path))
    repo.must("remote", "remove", "origin")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo.root, cfg) == 0
    stderr1 = capsys.readouterr().err
    assert "falling back to push range" in stderr1
    assert guard.run_guard(repo.root, cfg) == 0
    stderr2 = capsys.readouterr().err
    assert "falling back to push range" in stderr2
    cache = os.path.join(str(tmp_path), "cache")
    anchor_files = [f for f in os.listdir(cache) if f.startswith("anchor.")]
    devset_files = [f for f in os.listdir(cache) if f.startswith("devset.")]
    assert anchor_files
    assert devset_files
    with open(os.path.join(cache, anchor_files[0])) as fh:
        assert fh.read() == "none"

    repo_b = _setup(os.path.join(str(tmp_path), "b"))
    repo_b.write("f1.txt", "one\n")
    repo_b.commit("D1")
    frm = repo_b.ref("origin/master")
    to = repo_b.ref("HEAD")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", frm)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", to)
    dev = scope.DevScope(repo_b.root)
    anchor = dev.scope_base(frm, to)
    assert anchor == "refs/remotes/origin/master"
    assert dev.dev_files(anchor, frm, to) == ["f1.txt"]
    res = repo_b.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    if not res.ok():
        try:
            os.remove(
                os.path.join(repo_b.root, ".git", "refs", "remotes", "origin", "HEAD")
            )
        except OSError:
            pass
    repo_b.must("update-ref", "-d", "refs/remotes/origin/master")
    assert scope.DevScope(repo_b.root).scope_base(frm, to) == anchor
    assert scope.DevScope(repo_b.root).dev_files(anchor, frm, to) == ["f1.txt"]


def test_16_upstream_sync(tmp_path, capsys):
    prefix = str(tmp_path)
    canon = GitRepo.init(os.path.join(prefix, "canon"))
    canon.write("m0.txt", "M0\n")
    canon.commit("M0")
    m0 = canon.ref("HEAD")
    upstream_bare = canon.remote("upstream")
    canon.write("shared.py", "def shared():\n    return 1 \n")
    canon.write("u2.py", "x = 1 \n")
    canon.commit("U1")
    canon.push("upstream", "master")
    canon.branch("sprint/33.1", "master")
    canon.write("sprint_feat.py", "def sprint():\n    return 2 \n")
    canon.commit("S1")
    canon.push("upstream", "sprint/33.1")
    fork = os.path.join(prefix, "fork.git")
    canon.must("clone", "-q", "--bare", upstream_bare, fork)
    canon.must("--git-dir", fork, "update-ref", "refs/heads/master", m0)
    work = GitRepo.clone(fork, os.path.join(prefix, "work"))
    work.must("remote", "add", "upstream", upstream_bare)
    work.must("fetch", "-q", "upstream")
    work.must("remote", "set-head", "origin", "master")
    work.must(
        "symbolic-ref",
        "refs/remotes/upstream/HEAD",
        "refs/remotes/upstream/sprint/33.1",
    )

    work.checkout("master")
    work.must("pull", "-q", "upstream", "master", "--rebase")
    frm = work.ref("origin/master")
    to = work.ref("HEAD")
    range_files = work.git_lines("diff", "--name-only", frm + ".." + to)
    assert "shared.py" in range_files
    anchor = base.resolve_change_base(work.root)
    assert anchor == "refs/remotes/upstream/master"
    assert scope.DevScope(work.root).dev_files(anchor) == []
    cfg = _cfg(work, [{"id": "ruff"}])
    assert guard.run_guard(work.root, cfg) == 0
    capsys.readouterr()
    cfg = _cfg(work, [{"id": "hygiene/trailing-whitespace"}])
    assert guard.run_guard(work.root, cfg) == 0
    capsys.readouterr()
    assert _read_bytes(work, "shared.py") == b"def shared():\n    return 1 \n"
    assert _read_bytes(work, "u2.py") == b"x = 1 \n"
    work.write("dev_1.py", "dev = 1 \n")
    work.commit("D1")
    anchor = base.resolve_change_base(work.root)
    assert anchor == "refs/remotes/upstream/master"
    assert sorted(scope.DevScope(work.root).dev_files(anchor)) == ["dev_1.py"]
    assert guard.run_guard(work.root, cfg) == 0
    out = capsys.readouterr().out
    assert "[fixed] dev_1.py" in out
    assert _read_bytes(work, "dev_1.py") == b"dev = 1\n"
    assert _read_bytes(work, "shared.py") == b"def shared():\n    return 1 \n"
    work.must("checkout", "--", "dev_1.py")

    work.branch("sprint-work", "origin/master")
    work.push("origin", "sprint-work")
    work.must("pull", "-q", "upstream", "sprint/33.1", "--rebase")
    work.write("dev_sprint.py", "sdev = 1 \n")
    work.commit("SD1")
    frm3 = work.ref("origin/sprint-work")
    to3 = work.ref("HEAD")
    range3 = work.git_lines("diff", "--name-only", frm3 + ".." + to3)
    assert "sprint_feat.py" in range3
    assert "dev_sprint.py" in range3
    anchor3 = base.resolve_change_base(work.root)
    assert anchor3 == "refs/remotes/upstream/sprint/33.1"
    assert sorted(scope.DevScope(work.root).dev_files(anchor3)) == ["dev_sprint.py"]
    assert guard.run_guard(work.root, cfg) == 0
    out = capsys.readouterr().out
    assert "[fixed] dev_sprint.py" in out
    assert "sprint_feat.py" not in out
    assert _read_bytes(work, "dev_sprint.py") == b"sdev = 1\n"
    assert _read_bytes(work, "sprint_feat.py") == b"def sprint():\n    return 2 \n"
    assert _read_bytes(work, "shared.py") == b"def shared():\n    return 1 \n"


def test_17_json_sync(tmp_path, monkeypatch, capsys):
    repo = _setup(str(tmp_path))
    repo.branch("feature", "master")
    repo.write("f1.json", "{\"a\":1}\n")
    repo.commit("D1")
    repo.push("origin", "feature")
    repo.checkout("master")
    repo.write("broken.json", "{\"broken\":")
    repo.commit("U1")
    repo.push("origin", "master")
    repo.checkout("feature")
    repo.merge("origin/master")
    repo.write("f2.json", "{\"b\":2}\n")
    repo.commit("D2")
    frm = repo.ref("origin/feature")
    to = repo.ref("HEAD")
    range_files = repo.git_lines("diff", "--name-only", frm + ".." + to)
    assert "broken.json" in range_files
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo.root, cfg) == 0
    out = capsys.readouterr().out
    assert "broken.json" not in out
    repo.write("dev_bad.json", "{\"dev_bad\":")
    repo.commit("D3")
    assert guard.run_guard(repo.root, cfg) == 1
    out = capsys.readouterr().out
    assert "dev_bad.json" in out


def test_18_hook_execution(tmp_path, monkeypatch):
    repo = _setup(str(tmp_path))
    orig_path = os.environ.get("PATH", "")
    missing_exe = os.path.join(str(tmp_path), "missing-python")
    hook = os.path.join(str(tmp_path), "hook-missing")
    with open(hook, "w") as fh:
        fh.write(launcher.hook_body(missing_exe))
    os.chmod(hook, 0o755)
    emptybin = os.path.join(str(tmp_path), "emptybin")
    os.makedirs(emptybin)
    monkeypatch.setenv("PATH", emptybin)
    proc = subprocess.run(
        ["/bin/sh", hook, "origin", "git@github.com:x.git"],
        cwd=repo.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0
    assert b"skipped (tool not found" in proc.stderr

    env = dict(os.environ)
    env["PATH"] = orig_path
    env["PYTHONPATH"] = SRC + os.pathsep + os.environ.get("PYTHONPATH", "")
    live_hook = os.path.join(str(tmp_path), "hook-live")
    with open(live_hook, "w") as fh:
        fh.write(launcher.hook_body(sys.executable))
    os.chmod(live_hook, 0o755)
    proc2 = subprocess.run(
        ["/bin/sh", live_hook, "origin", "git@github.com:x.git"],
        cwd=repo.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc2.returncode == 0
    assert b"diffimpactscout:" in proc2.stdout

    assert launcher.install_hook(repo.root) is True
    assert launcher.hook_installed(repo.root) is True
    assert os.path.isfile(os.path.join(repo.root, ".git", "hooks", "pre-push"))
    assert launcher.uninstall_hook(repo.root) is True
    assert not launcher.hook_installed(repo.root)