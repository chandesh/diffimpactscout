"""Repo-wide checks for oversized files, leaked private keys, and case-colliding file paths.

Example: a 300 MB binary is flagged with a git-lfs hint, any file
containing a private key block always blocks the push, and a pushed
`FILE.TXT` colliding with a tracked `file.txt` is flagged because the two
names clobber each other on case-insensitive filesystems (macOS, Windows).
"""

import os

import diffimpactscout.gitrun as gitrun
from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)

PRIVATE_KEY_MARKERS = (
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"-----BEGIN PGP PRIVATE KEY BLOCK-----",
)


@register
class CaseConflictCheck(Check):
    id = "repo/case-conflict"
    scoped = "files"

    def run(self, context, files):
        tracked = gitrun.git_nul(["ls-files", "-z"], context.root)
        by_lower = {}
        for path in tracked:
            by_lower.setdefault(path.lower(), []).append(path)
        issues = []
        for path in files or []:
            for other in by_lower.get(path.lower(), ()):
                if other != path:
                    issues.append(
                        CheckIssue(
                            path,
                            None,
                            None,
                            self.id,
                            "case conflict detected with %s" % other,
                        )
                    )
                    break
        return CheckResult(issues=issues)


@register
class LargeFilesCheck(Check):
    id = "repo/large-files"
    scoped = "files"
    max_kb = 250000

    def extend_config(self, entry):
        super(LargeFilesCheck, self).extend_config(entry)
        max_kb = 250000
        for arg in self.args:
            if arg.startswith("--maxkb"):
                if not arg.startswith("--maxkb="):
                    raise ValueError("--maxkb must be --maxkb=<positive int>")
                value = arg[len("--maxkb="):]
                try:
                    max_kb = int(value)
                except ValueError:
                    raise ValueError("invalid --maxkb value: %r" % value)
                if max_kb <= 0:
                    raise ValueError("--maxkb must be a positive integer")
        self.max_kb = max_kb
        return self

    def run(self, context, files):
        issues = []
        for path in files or []:
            fn = os.path.join(context.root, path)
            try:
                if not os.path.exists(fn):
                    continue
                size = os.path.getsize(fn)
            except OSError:
                continue
            if size > self.max_kb * 1024:
                size_kb = size // 1024
                issues.append(
                    CheckIssue(
                        path,
                        None,
                        None,
                        self.id,
                        "file size %d kB exceeds limit of %d kB; consider git lfs"
                        % (size_kb, self.max_kb),
                    )
                )
        return CheckResult(issues=issues)


@register
class PrivateKeyCheck(Check):
    id = "repo/private-key"
    scoped = "files"
    always_block = True

    def run(self, context, files):
        issues = []
        for path in files or []:
            fn = os.path.join(context.root, path)
            try:
                with open(fn, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            for marker in PRIVATE_KEY_MARKERS:
                if marker in data:
                    issues.append(
                        CheckIssue(path, None, None, self.id, "private key detected")
                    )
                    break
        return CheckResult(issues=issues)