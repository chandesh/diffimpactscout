import os
import subprocess

import diffimpactscout.base as base


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


def _commit(repo, filename, content, msg, date=None):
    with open(os.path.join(repo, filename), "w") as fh:
        fh.write(content)
    env = _git_env(
        {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
    )
    _git("add", filename, cwd=repo, env=env)
    _git("commit", "-m", msg, cwd=repo, env=env)
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def test_no_remote_tracking_refs_returns_none(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "initial")
    _commit(repo, "b.txt", "two\n", "second")
    assert base.resolve_change_base(repo) is None


def test_origin_master_resolved(tmp_path):
    repo = _make_repo(tmp_path)
    sha = _commit(repo, "a.txt", "one\n", "initial")
    _git("update-ref", "refs/remotes/origin/master", sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/origin/master"


def test_origin_head_symbolic_ref_target(tmp_path):
    repo = _make_repo(tmp_path)
    sha = _commit(repo, "a.txt", "one\n", "initial")
    _git("update-ref", "refs/remotes/origin/master", sha, cwd=repo)
    _git(
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/master",
        cwd=repo,
    )
    assert base.resolve_change_base(repo) == "refs/remotes/origin/master"


def test_upstream_preferred_over_origin_same_tree(tmp_path):
    repo = _make_repo(tmp_path)
    sha = _commit(repo, "a.txt", "one\n", "initial")
    _git("update-ref", "refs/remotes/origin/master", sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/upstream/master"


def test_closest_tree_zero_diff_short_circuit(tmp_path):
    repo = _make_repo(tmp_path)
    master_sha = _commit(repo, "a.txt", "one\n", "initial")
    head_sha = _commit(repo, "b.txt", "two\n", "second")
    _git("update-ref", "refs/remotes/upstream/master", master_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s1", head_sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/upstream/sprint/s1"


def test_closest_tree_smallest_diff_count_wins(tmp_path):
    repo = _make_repo(tmp_path)
    master_sha = _commit(repo, "a.txt", "one\n", "initial")
    sprint_sha = _commit(repo, "b.txt", "two\n", "second")
    _commit(repo, "c.txt", "three\n", "third")
    _git("update-ref", "refs/remotes/upstream/master", master_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s1", sprint_sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/upstream/sprint/s1"


def test_limit_caps_sprint_candidates(tmp_path):
    repo = _make_repo(tmp_path)
    s2_sha = _commit(repo, "a.txt", "one\n", "s2", date="2025-01-01T00:00:00")
    s1_sha = _commit(repo, "b.txt", "two\n", "s1", date="2024-01-01T00:00:00")
    _commit(repo, "c.txt", "three\n", "c1", date="2023-01-01T00:00:00")
    master_sha = _commit(repo, "d.txt", "four\n", "master", date="2022-01-01T00:00:00")
    _commit(repo, "e.txt", "five\n", "c2", date="2021-01-01T00:00:00")
    zero_sha = _commit(repo, "f.txt", "six\n", "zero", date="2020-01-01T00:00:00")
    _git("update-ref", "refs/remotes/upstream/master", master_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s1", s1_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s2", s2_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s3", zero_sha, cwd=repo)
    assert base.resolve_change_base(repo, limit=2) == "refs/remotes/upstream/master"
    assert base.resolve_change_base(repo, limit=3) == "refs/remotes/upstream/sprint/s3"


def test_closest_tree_master_wins_tie(tmp_path):
    repo = _make_repo(tmp_path)
    master_sha = _commit(repo, "a.txt", "one\n", "initial")
    _commit(repo, "b.txt", "two\n", "second")
    _git("update-ref", "refs/remotes/upstream/master", master_sha, cwd=repo)
    _git("update-ref", "refs/remotes/upstream/sprint/s1", master_sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/upstream/master"


def test_origin_fallback_main_before_master(tmp_path):
    repo = _make_repo(tmp_path)
    sha = _commit(repo, "a.txt", "one\n", "initial")
    _git("update-ref", "refs/remotes/origin/main", sha, cwd=repo)
    assert base.resolve_change_base(repo) == "refs/remotes/origin/main"