"""CODEOWNERS lookup with last-match-wins semantics."""

import fnmatch
import os


def _matches(pattern, path):
    # fnmatch has no ** support, so fold ** to * (* already spans / here).
    pat = pattern.replace("**", "*").strip("/")
    target = path.replace(os.sep, "/").strip("/")
    base = target.rsplit("/", 1)[-1] if "/" in target else target
    if fnmatch.fnmatchcase(target, pat):
        return True
    # Patterns without a slash match against the basename as well.
    if "/" not in pat and fnmatch.fnmatchcase(base, pat):
        return True
    return False


_OWNER_FILES = ("CODEOWNERS", os.path.join(".github", "CODEOWNERS"), os.path.join("docs", "CODEOWNERS"))


def owner_for(path, root):
    target = path.replace(os.sep, "/").strip("/")
    owners_path = None
    for candidate in _OWNER_FILES:
        full = os.path.join(root, candidate)
        if os.path.isfile(full):
            owners_path = full
            break
    if owners_path is None:
        return ""
    try:
        with open(owners_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, ValueError):
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
