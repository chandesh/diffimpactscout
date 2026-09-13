"""Resolve the change base the developer's diff is measured against (upstream master, upstream sprint, or the default origin branch).

Example: when the repo has an upstream/sprint/12 ref, diffs are measured
against it instead of origin/master.
"""

import diffimpactscout.gitrun as gitrun

MASTER_REF = "refs/remotes/upstream/master"
SPRINT_PREFIX = "sprint/"
DEFAULT_MAX_SPRINT_CANDIDATES = 10
REMOTE_FALLBACKS = ("main", "master", "develop")


def resolve_change_base(root, limit=DEFAULT_MAX_SPRINT_CANDIDATES):
    """Return the remote ref closest to HEAD, or None.

    `limit` caps how many `sprint/*` refs are considered: only the
    `limit` (default 10) most-recent ones by committer date, unlike the
    bash original dev_base.sh which considered every sprint branch.
    """
    for remote in _remote_order(root):
        ref = _resolve_remote(root, remote, limit)
        if ref is not None:
            return ref
    return None


def _remote_order(root):
    names = set()
    for name in gitrun.git_lines(["remote"], root):
        if name:
            names.add(name)
    for ref in gitrun.git_lines(
        ["for-each-ref", "--format=%(refname)", "refs/remotes/"], root
    ):
        parts = ref.split("/")
        if len(parts) >= 3:
            names.add(parts[2])
    ordered = []
    for name in ("upstream", "origin"):
        if name in names:
            ordered.append(name)
    for name in sorted(names):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _resolve_remote(root, remote, limit):
    if remote == "upstream":
        return _closest_tree(root, _upstream_candidates(root, limit))
    short = gitrun.git_out(
        ["symbolic-ref", "--short", "refs/remotes/%s/HEAD" % remote], root
    )
    if short.startswith(remote + "/"):
        ref = "refs/remotes/" + short
        if gitrun.git_ok(["rev-parse", "--verify", ref], root):
            return ref
    for branch in REMOTE_FALLBACKS:
        ref = "refs/remotes/%s/%s" % (remote, branch)
        if gitrun.git_ok(["rev-parse", "--verify", ref], root):
            return ref
    return None


def _upstream_candidates(root, limit):
    candidates = [MASTER_REF]
    pattern = "refs/remotes/upstream/%s" % SPRINT_PREFIX
    sprints = gitrun.git_lines(
        ["for-each-ref", "--sort=-committerdate", "--format=%(refname)", pattern], root
    )
    candidates.extend(sprints[:limit])
    return candidates


def _closest_tree(root, candidates):
    best = None
    best_count = None
    for ref in candidates:
        if not ref:
            continue
        if not gitrun.git_ok(["rev-parse", "--verify", ref], root):
            continue
        count = len(
            gitrun.git_nul(
                ["diff", "--name-only", "-z", "--diff-filter=ACMRT", ref, "HEAD"], root
            )
        )
        if best_count is None or count < best_count:
            best = ref
            best_count = count
        if count == 0:
            break
    return best