import os

import pytest

from helpers import GitRepo

pytestmark = pytest.mark.smoke


def _check_direct_push(repo):
    assert repo.head_branch() == "feature"
    assert repo.count("master") == 1
    assert repo.count("master..HEAD") == 1
    assert repo.ref("refs/remotes/upstream/master") == repo.ref("master")
    assert os.path.exists(repo.path("base.txt"))
    assert os.path.exists(repo.path("f1.txt"))


def _check_deep_sync(repo):
    assert repo.count("master") == 1
    assert repo.count("master..HEAD") == 3
    assert repo.ref("refs/remotes/upstream/master") == repo.ref("master")
    for name in ("f1.txt", "f2.txt", "f3.txt"):
        assert os.path.exists(repo.path(name))


def _check_force_push(repo):
    assert repo.count("HEAD") == 5
    assert repo.count("refs/remotes/origin/feature") == 4
    assert repo.merge_base("HEAD", "refs/remotes/origin/feature") == repo.ref("master~1")
    assert repo.ref("HEAD") != repo.ref("refs/remotes/origin/feature")
    names = repo.git_lines("diff", "--name-only", "refs/remotes/origin/feature", "HEAD")
    assert "r.txt" in names
    assert "f3.txt" in names


def _check_new_branch(repo):
    assert repo.head_branch() == "feature"
    assert repo.ref("refs/remotes/origin/feature") is None
    assert repo.ref("refs/remotes/origin/master") == repo.ref("master")
    assert repo.count("refs/remotes/origin/master..HEAD") == 2
    for name in ("f1.txt", "f2.txt"):
        assert os.path.exists(repo.path(name))


def _check_shallow(repo):
    assert repo.head_branch() == "feature"
    assert os.path.exists(repo.path(os.path.join(".git", "shallow")))
    assert repo.ref("refs/remotes/origin/master") is not None
    assert repo.count("refs/remotes/origin/master..HEAD") == 1
    assert repo.count("HEAD") == 2
    assert os.path.exists(repo.path("f1.txt"))


def _check_stacked(repo):
    assert repo.head_branch() == "f2"
    assert repo.ref("refs/remotes/origin/f1") == repo.ref("f1")
    assert repo.ref("refs/remotes/origin/f2") == repo.ref("f2~1")
    assert repo.merge_base("f1", "f2") == repo.ref("f1")
    for name in ("f1.txt", "f2.txt", "e1.txt", "e2.txt"):
        assert os.path.exists(repo.path(name))


def _check_no_anchor(repo):
    assert repo.count("HEAD") == 1
    assert repo.git_out("remote") == ""
    assert repo.git_lines("for-each-ref", "--format=%(refname)", "refs/remotes") == []


def _check_upstream_lags(repo):
    assert repo.count("HEAD") == 3
    assert repo.ref("refs/remotes/upstream/master") == repo.ref("master~1")
    assert repo.ref("refs/remotes/origin/master") == repo.ref("master~2")
    assert repo.count("refs/remotes/origin/master..HEAD") == 2
    for name in ("m0.txt", "u2.txt", "dev_1.txt"):
        assert os.path.exists(repo.path(name))


CHECKERS = {
    "direct_push": _check_direct_push,
    "deep_sync": _check_deep_sync,
    "force_push": _check_force_push,
    "new_branch": _check_new_branch,
    "shallow": _check_shallow,
    "stacked": _check_stacked,
    "no_anchor": _check_no_anchor,
    "upstream_lags": _check_upstream_lags,
}


def test_gitrepo_scenarios(gitrepo):
    assert gitrepo.git_ok("rev-parse", "--verify", "HEAD")
    CHECKERS[gitrepo.scenario](gitrepo)


def test_repo_helpers_rm_mv_merge(tmp_path):
    repo = GitRepo.init(str(tmp_path / "repo"))
    repo.write("a.txt", "a\n")
    repo.commit("A")
    repo.branch("topic")
    repo.write("topic.txt", "t\n")
    repo.commit("T")
    repo.checkout("master")
    repo.merge("topic")
    assert repo.read("topic.txt") == "t\n"
    repo.rm("a.txt")
    repo.commit("R")
    assert not os.path.exists(repo.path("a.txt"))
    repo.write("e.txt", "e\n")
    repo.commit("E")
    repo.mv("e.txt", "e2.txt")
    repo.commit("V")
    assert not os.path.exists(repo.path("e.txt"))
    assert repo.read("e2.txt") == "e\n"
    assert repo.ref("HEAD") == repo.ref("master")