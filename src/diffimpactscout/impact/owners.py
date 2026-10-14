"""CODEOWNERS lookup with last-match-wins semantics."""

import fnmatch
import os


def _matches(pattern, path):
    # fnmatch has no ** support, so fold ** to * (* already spans / here).
    pat = pattern.replace("**", "*").strip("/")
    target = path.replace(os.sep, "/").strip("/")
    base = target.rsplit("/", 1)[-1] if "/" in target else target
    if fnmatch.fnmatch(target, pat):
        return True
    # Patterns without a slash match against the basename as well.
    if "/" not in pat and fnmatch.fnmatch(base, pat):
        return True
    return False


def owner_for(path, root):
    target = path.replace(os.sep, "/").strip("/")
    owners_path = os.path.join(root, "CODEOWNERS")
    if not os.path.isfile(owners_path):
        return ""
    try:
        with open(owners_path, "r") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""
    owner = ""
    for raw in lines:
        # Strip inline comments and skip blanks/comment-only lines.
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        if _matches(pattern, target):
            owner = parts[-1].lstrip("@")
            # Keep scanning: last matching pattern wins.
    return owner
