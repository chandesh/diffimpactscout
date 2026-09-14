import os
import subprocess

import pytest

from diffimpactscout.checks.base import (
    REGISTRY,
    CheckContext,
    CheckResult,
    make_check,
)


def _git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def _git(*args, cwd):
    return subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"] + list(args),
        cwd=cwd,
        env=_git_env(),
    )


def _repo(tmp_path):
    root = str(tmp_path / "repo")
    os.makedirs(root)
    _git("init", cwd=root)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    return root


def _commit(root, name, content="content\n"):
    with open(os.path.join(root, name), "w") as fh:
        fh.write(content)
    _git("add", name, cwd=root)
    _git("commit", "-m", name, cwd=root)


def _write(root, name, data):
    fn = os.path.join(root, name)
    with open(fn, "wb") as fh:
        fh.write(data)
    return fn


def _ctx(root):
    return CheckContext(
        root=root,
        anchor="A",
        from_ref="F",
        to_ref="T",
        scope=None,
        config={},
        echo=False,
    )


def _cls(cid):
    return REGISTRY[cid]


def test_repo_checks_registered():
    assert "repo/large-files" in REGISTRY
    assert REGISTRY["repo/large-files"].scoped == "files"
    assert REGISTRY["repo/large-files"].blocking is True
    assert REGISTRY["repo/large-files"].always_block is False
    assert "repo/private-key" in REGISTRY
    assert REGISTRY["repo/private-key"].scoped == "files"
    assert REGISTRY["repo/private-key"].blocking is True
    assert REGISTRY["repo/private-key"].always_block is True
    assert "repo/case-conflict" in REGISTRY
    assert REGISTRY["repo/case-conflict"].scoped == "files"
    assert REGISTRY["repo/case-conflict"].blocking is True


def test_repo_checks_buildable_from_config():
    lf = make_check({"id": "repo/large-files"})
    assert lf is not None
    assert lf.scoped == "files"
    pk = make_check({"id": "repo/private-key"})
    assert pk is not None
    assert pk.scoped == "files"
    assert pk.always_block is True
    cc = make_check({"id": "repo/case-conflict"})
    assert cc is not None
    assert cc.scoped == "files"
    assert cc.blocking is True


def test_case_conflict_no_collision_when_single_file(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "file.txt")
    result = _cls("repo/case-conflict")().run(_ctx(root), ["file.txt"])
    assert result.ok()
    assert result.issues == []


def test_case_conflict_flags_against_tracked_file(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "file.txt")
    result = _cls("repo/case-conflict")().run(_ctx(root), ["FILE.TXT"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "FILE.TXT"
    assert issue.code == "repo/case-conflict"
    assert "file.txt" in issue.message
    assert "case conflict" in issue.message


def test_case_conflict_flags_colliding_side_of_changed_pair(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "file.txt")
    # Only the incoming name that diverges from the tracked sibling is
    # flagged; the tracked file itself is not a collision.
    result = _cls("repo/case-conflict")().run(_ctx(root), ["file.txt", "FILE.TXT"])
    assert not result.ok()
    assert [i.path for i in result.issues] == ["FILE.TXT"]


def test_case_conflict_same_name_only_is_clean(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "ReadMe.md")
    result = _cls("repo/case-conflict")().run(_ctx(root), ["ReadMe.md"])
    assert result.ok()
    assert result.issues == []


def test_case_conflict_empty_and_none_file_list(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "file.txt")
    for files in ([], None):
        result = _cls("repo/case-conflict")().run(_ctx(root), files)
        assert result.ok()
        assert result.issues == []


def test_case_conflict_untracked_uknown_path_ok(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "file.txt")
    result = _cls("repo/case-conflict")().run(_ctx(root), ["ghost.txt"])
    assert result.ok()
    assert result.issues == []


def test_large_files_default_max_kb():
    check = make_check({"id": "repo/large-files"})
    assert check.max_kb == 250000


def test_large_files_under_limit_is_clean(tmp_path):
    root = str(tmp_path)
    _write(root, "small.txt", b"x" * 1024)
    result = _cls("repo/large-files")().run(_ctx(root), ["small.txt"])
    assert isinstance(result, CheckResult)
    assert result.ok()
    assert result.issues == []


def test_large_files_over_limit_reports_issue(tmp_path):
    root = str(tmp_path)
    _write(root, "big.txt", b"x" * (2 * 1024))
    check = _cls("repo/large-files")()
    check.extend_config({"args": ["--maxkb=1"]})
    result = check.run(_ctx(root), ["big.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "big.txt"
    assert issue.code == "repo/large-files"
    assert "2 kB" in issue.message
    assert "1 kB" in issue.message


def test_large_files_maxkb_via_make_check(tmp_path):
    root = str(tmp_path)
    _write(root, "big.txt", b"x" * (2 * 1024))
    check = make_check({"id": "repo/large-files", "args": ["--maxkb=1"]})
    assert check is not None
    assert check.max_kb == 1
    result = check.run(_ctx(root), ["big.txt"])
    assert not result.ok()
    assert len(result.issues) == 1


def test_large_files_default_limit_enforced(tmp_path, monkeypatch):
    root = str(tmp_path)
    _write(root, "huge.txt", b"")
    monkeypatch.setattr("os.path.getsize", lambda fn: 250001 * 1024)
    result = _cls("repo/large-files")().run(_ctx(root), ["huge.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert "250001 kB" in result.issues[0].message
    assert "250000 kB" in result.issues[0].message


def test_large_files_accepts_maxkb_at_config_time():
    check = _cls("repo/large-files")()
    out = check.extend_config({"id": "repo/large-files", "args": ["--maxkb=100"]})
    assert out is check
    assert check.args == ["--maxkb=100"]
    assert check.max_kb == 100


def test_large_files_rejects_invalid_maxkb():
    check = _cls("repo/large-files")()
    for bad in ("abc", "", "0", "-5", "1.5"):
        with pytest.raises(ValueError):
            check.extend_config({"args": ["--maxkb=%s" % bad]})
    assert make_check({"id": "repo/large-files", "args": ["--maxkb=abc"]}) is None


def test_large_files_rejects_separate_maxkb_value():
    check = _cls("repo/large-files")()
    with pytest.raises(ValueError):
        check.extend_config({"args": ["--maxkb", "100"]})
    assert make_check({"id": "repo/large-files", "args": ["--maxkb", "100"]}) is None


def test_large_files_missing_file_skipped(tmp_path):
    root = str(tmp_path)
    result = _cls("repo/large-files")().run(_ctx(root), ["ghost.txt"])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_large_files_empty_and_none_file_list(tmp_path):
    root = str(tmp_path)
    for files in ([], None):
        result = _cls("repo/large-files")().run(_ctx(root), files)
        assert result.ok()
        assert result.issues == []


def test_private_key_flags_rsa_marker(tmp_path):
    root = str(tmp_path)
    _write(
        root,
        "key.pem",
        b"-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\n",
    )
    result = _cls("repo/private-key")().run(_ctx(root), ["key.pem"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "key.pem"
    assert issue.code == "repo/private-key"
    assert issue.line == 0
    assert issue.column == 0


def test_private_key_flags_all_markers(tmp_path):
    root = str(tmp_path)
    markers = [
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
        b"-----BEGIN DSA PRIVATE KEY-----",
        b"-----BEGIN PGP PRIVATE KEY BLOCK-----",
    ]
    for i, marker in enumerate(markers):
        name = "m%d.txt" % i
        _write(root, name, marker)
        result = _cls("repo/private-key")().run(_ctx(root), [name])
        assert not result.ok(), marker
        assert len(result.issues) == 1, marker


def test_private_key_marker_beyond_16kb_flagged(tmp_path):
    root = str(tmp_path)
    data = b"x" * 16384 + b"-----BEGIN RSA PRIVATE KEY-----"
    _write(root, "deep.txt", data)
    result = _cls("repo/private-key")().run(_ctx(root), ["deep.txt"])
    assert not result.ok()
    assert len(result.issues) == 1


def test_private_key_marker_straddling_16kb_flagged(tmp_path):
    root = str(tmp_path)
    marker = b"-----BEGIN OPENSSH PRIVATE KEY-----"
    data = b"x" * (16384 - len(marker)) + marker
    _write(root, "straddle.txt", data)
    result = _cls("repo/private-key")().run(_ctx(root), ["straddle.txt"])
    assert not result.ok()
    assert len(result.issues) == 1


def test_private_key_marker_just_inside_16kb_flagged(tmp_path):
    root = str(tmp_path)
    marker = b"-----BEGIN RSA PRIVATE KEY-----"
    data = b"x" * (16384 - len(marker)) + marker
    _write(root, "edge.txt", data)
    result = _cls("repo/private-key")().run(_ctx(root), ["edge.txt"])
    assert not result.ok()
    assert len(result.issues) == 1


def test_private_key_clean_file_is_clean(tmp_path):
    root = str(tmp_path)
    _write(root, "clean.txt", b"no secrets here\n")
    result = _cls("repo/private-key")().run(_ctx(root), ["clean.txt"])
    assert result.ok()
    assert result.issues == []


def test_private_key_missing_file_skipped(tmp_path):
    root = str(tmp_path)
    result = _cls("repo/private-key")().run(_ctx(root), ["ghost.txt"])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_private_key_empty_and_none_file_list(tmp_path):
    root = str(tmp_path)
    for files in ([], None):
        result = _cls("repo/private-key")().run(_ctx(root), files)
        assert result.ok()
        assert result.issues == []


def test_private_key_always_block_overridable():
    check = _cls("repo/private-key")()
    assert check.always_block is True
    out = check.extend_config(
        {"id": "repo/private-key", "blocking": False, "always_block": False}
    )
    assert out is check
    assert check.always_block is False
    assert check.blocking is False


def test_private_key_always_block_not_overridable_via_make_check():
    check = make_check({"id": "repo/private-key", "blocking": False})
    assert check is not None
    assert check.always_block is True
    assert check.blocking is False