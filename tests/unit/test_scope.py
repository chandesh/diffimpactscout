import os
import subprocess

import diffimpactscout.base as base
import diffimpactscout.scope as scope
from diffimpactscout.scope import DevScope


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


def _append(repo, filename, content, msg):
    with open(os.path.join(repo, filename), "a") as fh:
        fh.write(content)
    _git("add", filename, cwd=repo, env=_git_env())
    _git("commit", "-m", msg, cwd=repo, env=_git_env())
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _anchor(repo, ref="HEAD"):
    sha = _sha(repo, ref)
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)
    return "refs/remotes/upstream/master"


def _make_scope(repo, cache_dir):
    return DevScope(repo, cache_dir=cache_dir)


def test_parse_hunks_simple():
    assert scope.parse_hunks("@@ -1,2 +1,3 @@\n ctx\n+add\n") == [(1, 2, 1, 3)]


def test_parse_hunks_insertion():
    assert scope.parse_hunks("@@ -10,0 +12,2 @@\n") == [(10, 0, 12, 2)]


def test_hunk_new_lines_simple():
    assert scope.hunk_new_lines("@@ -1,2 +1,3 @@\n") == {1, 2, 3}


def test_hunk_new_lines_insertion():
    assert scope.hunk_new_lines("@@ -10,0 +12,2 @@\n") == {12, 13}


def test_hunk_new_lines_multiple_hunks():
    text = "@@ -1,2 +1,3 @@\n@@ -10,0 +12,2 @@\n"
    assert scope.hunk_new_lines(text) == {1, 2, 3, 12, 13}


def test_hunk_new_lines_omitted_counts():
    assert scope.hunk_new_lines("@@ -2 +3 @@\n") == {3}


def test_hunk_new_lines_pure_deletion():
    assert scope.parse_hunks("@@ -5,3 +5,0 @@\n") == [(5, 3, 5, 0)]
    assert scope.hunk_new_lines("@@ -5,3 +5,0 @@\n") == set()


def test_dev_files_only_dev_changes(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    anchor = _anchor(repo)
    _commit(repo, "a.txt", "one\n", "dev a")
    _commit(repo, "b.txt", "two\n", "dev b")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.dev_files(anchor) == ["a.txt", "b.txt"]


def test_dev_files_excludes_upstream_sync(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "one")
    _anchor(repo)
    _commit(repo, "b.txt", "two\n", "upstream-only change")
    _git("update-ref", "refs/remotes/upstream/master", _sha(repo), cwd=repo)
    _commit(repo, "c.txt", "three\n", "dev change")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.dev_files("refs/remotes/upstream/master") == ["c.txt"]


def test_dev_files_manual_union(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("two\n")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.dev_files(None) == ["a.txt"]


def test_dev_files_range_fallback(tmp_path):
    repo = _make_repo(tmp_path)
    sha_base = _commit(repo, "a.txt", "one\n", "base")
    _commit(repo, "b.txt", "two\n", "dev b")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.dev_files(None, sha_base, _sha(repo)) == ["b.txt"]


def test_diverged_range_uses_merge_base(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _git("checkout", "-b", "temp_remote", cwd=repo)
    _commit(repo, "remote_only.txt", "remote\n", "remote change")
    _git("update-ref", "refs/remotes/origin/dev", _sha(repo), cwd=repo)
    _git("checkout", "master", cwd=repo)
    _commit(repo, "local.txt", "local\n", "dev local")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    files = ds.dev_files(None, "refs/remotes/origin/dev", "master")
    assert "remote_only.txt" not in files
    assert files == ["local.txt"]


def test_dev_files_cache_hit_after_repo_change(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    anchor = _anchor(repo)
    _commit(repo, "b.txt", "two\n", "dev b")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.dev_files(anchor, "from", "to") == ["b.txt"]
    _commit(repo, "c.txt", "three\n", "dev c")
    assert ds.dev_files(anchor, "from", "to") == ["b.txt"]


def test_contains(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "one")
    anchor = _anchor(repo)
    _commit(repo, "b.txt", "two\n", "dev b")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.contains(anchor, "b.txt")
    assert not ds.contains(anchor, "a.txt")
    assert not ds.contains(anchor, "nope.txt")


def test_changed_lines_append(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\ntwo\n", "base")
    anchor = _anchor(repo)
    _append(repo, "a.txt", "three\n", "append")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.changed_lines(anchor, "a.txt") == {3}


def test_changed_lines_range(tmp_path):
    repo = _make_repo(tmp_path)
    sha_base = _commit(repo, "a.txt", "one\ntwo\n", "base")
    sha_to = _append(repo, "a.txt", "three\n", "append")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.changed_lines(None, "a.txt", sha_base, sha_to) == {3}


def test_changed_lines_manual_union(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\ntwo\nthree\n", "base")
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("four\n")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.changed_lines(None, "a.txt") == {4}


def test_is_tracked_and_new_file(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    anchor = _anchor(repo)
    with open(os.path.join(repo, "new.txt"), "w") as fh:
        fh.write("new\n")
    ds = _make_scope(repo, str(tmp_path / "cache"))
    assert ds.is_tracked("a.txt")
    assert not ds.is_tracked("new.txt")
    assert ds.changed_lines(anchor, "new.txt") is None


def test_scope_base_cached_once(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    ref = _anchor(repo)
    ds = _make_scope(repo, str(tmp_path / "cache"))
    calls = []

    def fake_resolve(root, limit=10):
        calls.append(root)
        return ref

    monkeypatch.setattr(base, "resolve_change_base", fake_resolve)
    assert ds.scope_base("from", "to") == ref
    assert ds.scope_base("from", "to") == ref
    assert len(calls) == 1


def test_scope_base_cache_keyed_by_range(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    ref = _anchor(repo)
    ds = _make_scope(repo, str(tmp_path / "cache"))
    calls = []

    def fake_resolve(root, limit=10):
        calls.append(root)
        return ref

    monkeypatch.setattr(base, "resolve_change_base", fake_resolve)
    assert ds.scope_base("from1", "to1") == ref
    assert ds.scope_base("from2", "to2") == ref
    assert len(calls) == 2


def test_scope_base_manual_resolves_fresh(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    ref = _anchor(repo)
    ds = _make_scope(repo, str(tmp_path / "cache"))
    calls = []

    def fake_resolve(root, limit=10):
        calls.append(root)
        return ref

    monkeypatch.setattr(base, "resolve_change_base", fake_resolve)
    assert ds.scope_base() == ref
    assert ds.scope_base() == ref
    assert len(calls) == 2


def test_cache_write_failure_degrades(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    sha_base = _sha(repo)
    _commit(repo, "b.txt", "two\n", "dev b")
    anchor = _anchor(repo)
    ds = _make_scope(repo, str(tmp_path / "cache"))

    def boom(*args, **kwargs):
        raise OSError("cache dir unwritable")

    monkeypatch.setattr(os, "makedirs", boom)
    assert ds.scope_base("from", "to") == anchor
    assert ds.dev_files(None, sha_base, _sha(repo)) == ["b.txt"]


def test_corrupt_cache_file_tolerated(tmp_path):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    sha_base = _sha(repo)
    _commit(repo, "b.txt", "two\n", "dev b")
    anchor = _anchor(repo)
    cache_dir = str(tmp_path / "cache")
    ds = _make_scope(repo, cache_dir)
    os.makedirs(cache_dir)
    gitdir = ds._gitdir()
    dev_path = os.path.join(
        cache_dir, "devset." + scope.cache_key(gitdir, sha_base, _sha(repo))
    )
    anchor_path = os.path.join(
        cache_dir, "anchor." + scope.cache_key(gitdir, "from", "to")
    )
    with open(dev_path, "wb") as fh:
        fh.write(b"\x00\xff\xfe garbage\n")
    with open(anchor_path, "wb") as fh:
        fh.write(b"\x00\xff\xfe garbage\n")
    assert ds.dev_files(None, sha_base, _sha(repo)) == ["b.txt"]
    assert ds.scope_base("from", "to") == anchor


def test_scope_base_none_sentinel(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _commit(repo, "a.txt", "one\n", "base")
    cache_dir = str(tmp_path / "cache")
    ds = _make_scope(repo, cache_dir)

    def fake_resolve(root, limit=10):
        return None

    monkeypatch.setattr(base, "resolve_change_base", fake_resolve)
    assert ds.scope_base("from", "to") is None
    assert ds.scope_base("from", "to") is None
    names = os.listdir(cache_dir)
    anchor_files = [n for n in names if n.startswith("anchor.")]
    assert len(anchor_files) == 1
    with open(os.path.join(cache_dir, anchor_files[0])) as fh:
        assert fh.read().strip() == "none"


def test_default_cache_dir_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg/cache")
    monkeypatch.setenv("HOME", "/home/test")
    assert scope.default_cache_dir() == os.path.join("/xdg/cache", "diffimpactscout")


def test_default_cache_dir_home(monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/test")
    assert scope.default_cache_dir() == os.path.join(
        "/home/test", ".cache", "diffimpactscout"
    )


def test_cache_key_stable_and_distinct():
    key = scope.cache_key("/repo/.git", "from1", "to1")
    assert key == scope.cache_key("/repo/.git", "from1", "to1")
    assert key != scope.cache_key("/repo/.git", "from2", "to1")
    assert key != scope.cache_key("/other/.git", "from1", "to1")
    assert isinstance(key, str)
    assert set(key) <= set("0123456789abcdef")