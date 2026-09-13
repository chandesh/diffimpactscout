import os
import shutil
import subprocess

USER_NAME = "Fixture"
USER_EMAIL = "fixture@example.com"

SCENARIOS = (
    "direct_push",
    "deep_sync",
    "force_push",
    "new_branch",
    "shallow",
    "stacked",
    "no_anchor",
    "upstream_lags",
)


class GitResult(object):
    def __init__(self, returncode, out_, err_):
        self.returncode = returncode
        self.out_ = out_
        self.err_ = err_

    def ok(self):
        return self.returncode == 0


def git_env(extra=None):
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if extra:
        env.update(extra)
    return env


def _cmd(args, git_dir=None):
    cmd = ["git", "-c", "user.name=%s" % USER_NAME, "-c", "user.email=%s" % USER_EMAIL]
    if git_dir:
        cmd.extend(["--git-dir", git_dir])
    cmd.extend(args)
    return cmd


def _run(args, cwd, env=None, git_dir=None):
    env = env or git_env()
    proc = subprocess.Popen(
        _cmd(args, git_dir),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    return GitResult(
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


class GitRepo(object):
    def __init__(self, root, env=None):
        self.root = root
        self.env = env or git_env()

    @classmethod
    def init(cls, root, env=None):
        repo = cls(root, env)
        os.makedirs(root)
        repo.git("init")
        repo.git("symbolic-ref", "HEAD", "refs/heads/master")
        repo.git("config", "user.name", USER_NAME)
        repo.git("config", "user.email", USER_EMAIL)
        return repo

    @classmethod
    def clone(cls, bare, dest, branch=None, depth=None):
        args = ["clone", "-q"]
        if branch:
            args.extend(["--branch", branch])
        if depth is not None:
            args.extend(["--depth", str(depth)])
        args.extend(["file://" + os.path.abspath(bare), dest])
        env = git_env()
        res = _run(args, os.path.dirname(dest), env)
        if not res.ok():
            raise RuntimeError(res.err_ or res.out_)
        return cls(dest, env)

    @classmethod
    def scenario(cls, name, base):
        builder = getattr(cls, "_scenario_" + name, None)
        if builder is None:
            raise ValueError("unknown scenario: %s" % name)
        return builder(base)

    def git(self, *args):
        return _run(list(args), self.root, self.env)

    def git_ok(self, *args):
        return self.git(*args).ok()

    def git_out(self, *args, default=""):
        res = self.git(*args)
        if not res.ok():
            return default
        return res.out_.rstrip("\n")

    def git_lines(self, *args):
        out = self.git_out(*args)
        if not out:
            return []
        return out.split("\n")

    def must(self, *args):
        res = self.git(*args)
        if not res.ok():
            raise RuntimeError(res.err_ or res.out_)
        return res

    def write(self, path, content):
        full = os.path.join(self.root, path)
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(full, "w") as fh:
            fh.write(content)

    def read(self, path):
        with open(os.path.join(self.root, path)) as fh:
            return fh.read()

    def path(self, path):
        return os.path.join(self.root, path)

    def commit(self, message):
        self.must("add", "-A")
        self.must("commit", "-m", message)
        return self.ref("HEAD")

    def rm(self, path):
        self.must("rm", path)

    def mv(self, src, dst):
        self.must("mv", src, dst)

    def branch(self, name, base=None):
        if base:
            self.must("checkout", "-q", "-b", name, base)
        else:
            self.must("checkout", "-q", "-b", name)

    def checkout(self, name):
        self.must("checkout", "-q", name)

    def merge(self, name):
        self.must("merge", "-q", "--no-edit", name)

    def rebase(self, ref):
        self.must("rebase", ref)

    def ref(self, name="HEAD"):
        return self.git_out("rev-parse", "--verify", name) or None

    def head_branch(self):
        return self.git_out("symbolic-ref", "--short", "HEAD") or "master"

    def count(self, ref):
        return int(self.git_out("rev-list", "--count", ref) or "0")

    def merge_base(self, a, b):
        return self.git_out("merge-base", a, b) or None

    def remote(self, name, branch=None):
        bare = os.path.join(os.path.dirname(self.root), name + ".git")
        self.must("init", "--bare", bare)
        self.must("--git-dir", bare, "symbolic-ref", "HEAD", "refs/heads/master")
        if name not in self.git_lines("remote"):
            self.must("remote", "add", name, bare)
        if branch is None:
            branch = self.head_branch()
        if branch:
            self.push(name, branch)
        return bare

    def push(self, name, branch=None, force=False):
        if branch is None:
            branch = self.head_branch()
        args = ["push", "-q", "-u", name, branch]
        if force:
            args.append("--force-with-lease")
        self.must(*args)

    def remove(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @classmethod
    def _scenario_direct_push(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        repo.remote("upstream")
        repo.branch("feature", "master")
        repo.write("f1.txt", "one\n")
        repo.commit("F1")
        return repo

    @classmethod
    def _scenario_deep_sync(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        repo.remote("upstream")
        repo.branch("feature", "master")
        for content, fname, msg in (
            ("one", "f1.txt", "F1"),
            ("two", "f2.txt", "F2"),
            ("three", "f3.txt", "F3"),
        ):
            repo.write(fname, content + "\n")
            repo.commit(msg)
        return repo

    @classmethod
    def _scenario_force_push(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        repo.remote("origin")
        repo.branch("feature", "master")
        repo.write("f1.txt", "one\n")
        repo.commit("D1")
        repo.write("f2.txt", "two\n")
        repo.commit("D2")
        repo.write("f3.txt", "three\n")
        repo.commit("D3")
        repo.push("origin", "feature")
        repo.checkout("master")
        repo.write("r.txt", "remote\n")
        repo.commit("R")
        repo.push("origin", "master")
        repo.checkout("feature")
        repo.rebase("origin/master")
        repo.write("f3.txt", "three-rewritten\n")
        repo.must("add", "-A")
        repo.must("commit", "--amend", "-m", "D3 rebased")
        return repo

    @classmethod
    def _scenario_new_branch(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        repo.remote("origin")
        repo.branch("feature", "master")
        repo.write("f1.txt", "one\n")
        repo.commit("D1")
        repo.write("f2.txt", "two\n")
        repo.commit("D2")
        return repo

    @classmethod
    def _scenario_shallow(cls, base):
        seed = cls.init(os.path.join(base, "seed"))
        seed.write("base.txt", "base\n")
        seed.commit("M0")
        seed.write("s2.txt", "two\n")
        seed.commit("M1")
        bare = seed.remote("origin")
        repo = cls.clone(bare, os.path.join(base, "work"), branch="master", depth=1)
        repo.branch("feature", "master")
        repo.write("f1.txt", "one\n")
        repo.commit("F1")
        return repo

    @classmethod
    def _scenario_stacked(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        repo.remote("origin")
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
        return repo

    @classmethod
    def _scenario_no_anchor(cls, base):
        repo = cls.init(os.path.join(base, "work"))
        repo.write("base.txt", "base\n")
        repo.commit("M0")
        return repo

    @classmethod
    def _scenario_upstream_lags(cls, base):
        canon = cls.init(os.path.join(base, "canon"))
        canon.write("m0.txt", "m0\n")
        canon.commit("M0")
        m0 = canon.ref("HEAD")
        canon.write("u2.txt", "u2\n")
        canon.commit("U1")
        bare_up = os.path.join(base, "upstream.git")
        canon.must("init", "--bare", bare_up)
        canon.must("--git-dir", bare_up, "symbolic-ref", "HEAD", "refs/heads/master")
        canon.must("push", "-q", bare_up, "master")
        fork = os.path.join(base, "fork.git")
        canon.must("clone", "-q", "--bare", bare_up, fork)
        canon.must("--git-dir", fork, "update-ref", "refs/heads/master", m0)
        repo = cls.clone(fork, os.path.join(base, "work"))
        repo.must("remote", "add", "upstream", bare_up)
        repo.must("fetch", "-q", "upstream")
        repo.checkout("master")
        repo.must("pull", "-q", "upstream", "master", "--rebase")
        repo.write("dev_1.txt", "dev\n")
        repo.commit("D1")
        return repo