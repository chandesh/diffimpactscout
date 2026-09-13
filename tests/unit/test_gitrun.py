import os
import subprocess
import tempfile

import diffimpactscout.gitrun as gitrun


def _git(*args, cwd):
    return subprocess.check_call(["git"] + list(args), cwd=cwd)


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git("init", cwd=repo)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("hello\n")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    return repo


def test_git_out_branch_name(tmp_path):
    repo = _make_repo(tmp_path)
    assert gitrun.git_out(["rev-parse", "--abbrev-ref", "HEAD"], repo) == "master"


def test_git_out_default_on_failure(tmp_path):
    repo = _make_repo(tmp_path)
    assert gitrun.git_out(["rev-parse", "--verify", "HEAD~999"], repo, default="X") == "X"


def test_git_nul_ls_files(tmp_path):
    repo = _make_repo(tmp_path)
    assert gitrun.git_nul(["ls-files", "-z"], repo) == ["a.txt"]


def test_repo_root(tmp_path):
    repo = _make_repo(tmp_path)
    root = gitrun.repo_root(cwd=repo)
    subdir = os.path.join(repo, "packages", "proj")
    os.makedirs(subdir)
    assert gitrun.repo_root(cwd=subdir) == root


def test_git_lines(tmp_path):
    repo = _make_repo(tmp_path)
    assert gitrun.git_lines(["ls-files"], repo) == ["a.txt"]
    assert isinstance(gitrun.git_lines(["ls-files"], repo), list)


def test_git_ok(tmp_path):
    repo = _make_repo(tmp_path)
    assert gitrun.git_ok(["rev-parse", "--verify", "HEAD"], repo) is True
    assert gitrun.git_ok(["rev-parse", "--verify", "HEAD~999"], repo) is False


def test_git_oserror_returns_127(tmp_path):
    missing = os.path.join(str(tmp_path), "does-not-exist")
    result = gitrun.git(["rev-parse", "--show-toplevel"], missing)
    assert result.returncode == 127
    assert result.ok() is False


def test_git_abs_dir(tmp_path):
    root = str(tmp_path)
    assert gitrun.git_abs_dir(root, "subdir") == os.path.join(root, "subdir")


def test_repo_root_none_outside_repo():
    tmp = tempfile.mkdtemp()
    assert gitrun.repo_root(cwd=tmp) is None