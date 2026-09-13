"""Thin subprocess wrapper around git: run commands, capture output, and resolve the repo root and git dir."""

import os
import subprocess


class Result(object):
    def __init__(self, returncode, out_, err_):
        self.returncode = returncode
        self.out_ = out_
        self.err_ = err_

    def ok(self):
        return self.returncode == 0


def git(args, cwd):
    try:
        proc = subprocess.Popen(
            ["git"] + list(args),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return Result(127, "", "")
    out, err = proc.communicate()
    return Result(
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


def git_ok(args, cwd):
    return git(args, cwd).ok()


def git_out(args, cwd, default=""):
    result = git(args, cwd)
    if not result.ok():
        return default
    return result.out_.rstrip("\n")


def git_lines(args, cwd):
    out = git_out(args, cwd)
    if not out:
        return []
    return out.split("\n")


def git_nul(args, cwd):
    out = git_out(args, cwd)
    if not out:
        return []
    return [part for part in out.split("\0") if part]


def repo_root(cwd=None):
    result = git(["rev-parse", "--show-toplevel"], cwd)
    if not result.ok():
        return None
    return result.out_.rstrip("\n")


def git_abs_dir(root, path):
    return os.path.join(root, path)