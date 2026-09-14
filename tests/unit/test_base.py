import os
import subprocess

import diffimpactscout.base as base


def test_default_limit_ignores_oldest_sprint(tmp_path):
    repo = _make_repo(tmp_path)
    master_sha = _commit(repo, "a.txt", "one\n", "m0", date="2023-01-01T00:00:00")
    s3_sha = _commit(repo, "s3.txt", "s3\n", "s3", date="2026-03-01T00:00:00")
    s4_sha = _commit(repo, "s4.txt", "s4\n", "s4", date="2026-04-01T00:00:00")
    s5_sha = _commit(repo, "s5.txt", "s5\n", "s5", date="2026-05-01T00:00:00")
    s6_sha = _commit(repo, "s6.txt", "s6\n", "s6", date="2026-06-01T00:00:00")
    s2_sha = _commit(repo, "s2.txt", "s2\n", "s2", date="2026-02-01T00:00:00")
    s1_sha = _commit(repo, "s1.txt", "s1\n", "s1", date="2026-01-01T00:00:00")

    _git("update-ref", "refs/remotes/upstream/master", master_sha, cwd=repo)
    for name, sha in (
        ("s6", s6_sha),
        ("s5", s5_sha),
        ("s4", s4_sha),
        ("s3", s3_sha),
        ("s2", s2_sha),
        ("s1", s1_sha),
    ):
        _git("update-ref", "refs/remotes/upstream/sprint/%s" % name, sha, cwd=repo)

    # s1 is the oldest sprint and its tree is identical to HEAD (zero diff).
    # With the default candidate limit it is dropped, so the closest remaining
    # sprint (s2, one file short of HEAD) is chosen instead.
    assert base.resolve_change_base(repo) == "refs/remotes/upstream/sprint/s2"
    # An explicit limit large enough to include s1 makes it win via zero diff.
    assert (
        base.resolve_change_base(repo, limit=6) == "refs/remotes/upstream/sprint/s1"
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