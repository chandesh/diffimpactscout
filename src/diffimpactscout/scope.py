"""DevScope: which files and which lines the developer changed relative to the resolved change base.

Example: after syncing from upstream, only the developer's own commit files
appear in the change-set, so upstream-merged files are never re-checked.
"""

import os
import re
import sys
import zlib

import diffimpactscout.base as base
import diffimpactscout.gitrun as gitrun

CACHE_DIRNAME = "diffimpactscout"
NONE_SENTINEL = "none"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def default_cache_dir():
    base_dir = os.environ.get("XDG_CACHE_HOME")
    if not base_dir:
        base_dir = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base_dir, CACHE_DIRNAME)


def cache_key(gitdir, from_ref, to_ref):
    data = "%s|%s|%s" % (gitdir or "none", from_ref or "", to_ref or "")
    return "%x" % (zlib.crc32(data.encode("utf-8")) & 0xFFFFFFFF)


def parse_hunks(text):
    hunks = []
    for line in text.split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            hunks.append(
                (
                    int(m.group(1)),
                    int(m.group(2) or 1),
                    int(m.group(3)),
                    int(m.group(4) or 1),
                )
            )
    return hunks


def hunk_new_lines(text):
    lines = set()
    for _old_start, _old_count, new_start, new_count in parse_hunks(text):
        for i in range(new_start, new_start + new_count):
            lines.add(i)
    return lines


class DevScope(object):
    def __init__(self, root, cache_dir=None):
        self.root = root
        self.cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
        self._gitdir_cache = None

    def _gitdir(self):
        if self._gitdir_cache is None:
            self._gitdir_cache = gitrun.git_out(
                ["rev-parse", "--absolute-git-dir"], self.root
            )
        return self._gitdir_cache

    def _cache_path(self, kind, from_ref, to_ref):
        return os.path.join(
            self.cache_dir,
            "%s.%s" % (kind, cache_key(self._gitdir(), from_ref, to_ref)),
        )

    def _write(self, path, text):
        try:
            if not os.path.isdir(self.cache_dir):
                os.makedirs(self.cache_dir)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except OSError:
            pass

    def _write_list(self, path, files):
        try:
            if not os.path.isdir(self.cache_dir):
                os.makedirs(self.cache_dir)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                if files:
                    fh.write("\n".join(files) + "\n")
            os.replace(tmp, path)
        except OSError:
            pass

    def _read(self, path):
        try:
            with open(path, "r") as fh:
                return fh.read()
        except (OSError, ValueError):
            return None

    def _read_list(self, path):
        content = self._read(path)
        if content is None:
            return None
        return [line for line in content.split("\n") if line]

    def _warn_no_anchor(self):
        sys.stderr.write(
            "diffimpactscout: no default upstream ref; falling back to push range\n"
        )

    def scope_base(self, from_ref=None, to_ref=None):
        """Resolve the default anchor ref, cached on the (from, to) range."""
        if from_ref and to_ref:
            cached = self._cache_path("anchor", from_ref, to_ref)
            value = self._read(cached)
            if value is not None:
                value = value.strip()
                if value == NONE_SENTINEL:
                    self._warn_no_anchor()
                    return None
                return value
            ref = base.resolve_change_base(self.root)
            if ref is None:
                self._warn_no_anchor()
                self._write(cached, NONE_SENTINEL)
                return None
            self._write(cached, ref)
            return ref
        ref = base.resolve_change_base(self.root)
        if ref is None:
            self._warn_no_anchor()
        return ref

    def dev_files(self, anchor, from_ref=None, to_ref=None):
        """Dev files for the change, cached on the (from, to) range only.

        The anchor is not part of the cache key, so a different anchor with
        the same range hits a stale cache.
        """
        if from_ref and to_ref:
            cached = self._cache_path("devset", from_ref, to_ref)
            files = self._read_list(cached)
            if files is not None:
                return files
            files = self._dev_files_uncached(anchor, from_ref, to_ref)
            self._write_list(cached, files)
            return files
        return self._dev_files_uncached(anchor, from_ref, to_ref)

    def _dev_files_uncached(self, anchor, from_ref, to_ref):
        if anchor:
            return gitrun.git_nul(
                ["diff", "--name-only", "-z", "--diff-filter=ACMRT", anchor, "HEAD"],
                self.root,
            )
        if from_ref and to_ref:
            return gitrun.git_nul(
                [
                    "diff",
                    "--name-only",
                    "-z",
                    "--diff-filter=ACMRT",
                    "%s...%s" % (from_ref, to_ref),
                ],
                self.root,
            )
        files = set()
        for args in (
            ["diff", "--name-only", "-z", "--diff-filter=ACMRT", "HEAD"],
            ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT"],
            ["diff", "--name-only", "-z", "--diff-filter=ACMRT"],
        ):
            for f in gitrun.git_nul(args, self.root):
                files.add(f)
        return sorted(files)

    def changed_lines(self, anchor, path, from_ref=None, to_ref=None):
        if not self.is_tracked(path):
            return None
        if anchor:
            text = gitrun.git_out(
                ["diff", "--unified=0", anchor, "HEAD", "--", path], self.root
            )
            return hunk_new_lines(text)
        if from_ref and to_ref:
            text = gitrun.git_out(
                ["diff", "--unified=0", "%s...%s" % (from_ref, to_ref), "--", path],
                self.root,
            )
            return hunk_new_lines(text)
        lines = set()
        for args in (
            ["diff", "--unified=0", "HEAD", "--", path],
            ["diff", "--cached", "--unified=0", "--", path],
            ["diff", "--unified=0", "--", path],
        ):
            lines |= hunk_new_lines(gitrun.git_out(args, self.root))
        return lines

    def is_tracked(self, path):
        return gitrun.git_ok(["ls-files", "--error-unmatch", "--", path], self.root)

    def contains(self, anchor, path, from_ref=None, to_ref=None):
        return path in self.dev_files(anchor, from_ref, to_ref)