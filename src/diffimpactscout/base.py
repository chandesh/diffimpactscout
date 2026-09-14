"""Resolve the change base the developer's diff is measured against (upstream master, upstream sprint, or the default origin branch).

Example: when the repo has an upstream/sprint/12 ref, diffs are measured
against it instead of origin/master.

================================================================================
ARCHITECTURAL OVERVIEW: 2-DOT TREE DIFF FOR INCREMENTAL CHECKS
================================================================================

Why a 2-dot tree diff (`git diff <anchor> HEAD`):

In standard Git workflows a 3-dot diff (`git diff <anchor>...HEAD`) computes
changes relative to the common merge-base ancestor of the two tips. Whenever a
developer syncs their branch with upstream (merge, rebase, or squash), the
merge-base moves and the 3-dot range silently grows to include every commit
introduced by upstream since the branch diverged. Every pre-existing upstream
violation (a stale trailing space, a long line, an old lint error) then shows up
as a "new" issue in the pre-push guard, producing false positives the developer
did not author and cannot reasonably fix in that push.

A 2-dot tree diff (`git diff <anchor> HEAD`, no `...`) compares the exact
filesystem snapshot ("tree") of the resolved upstream anchor directly against
the `HEAD` tree. Consequences:

  1. Zero Upstream Blame: any file or line that exists byte-identically in the
     anchor tree contributes no diff entries. Only files and lines actually
     added or changed by the developer are passed to the checks.
  2. Sync Agnostic: behaves identically whether the branch was synced with
     `git merge`, `git rebase`, or `git merge --squash`, because it never walks
     commit ancestry, only the two tip trees.
  3. Shallow-Clone Safe: computing the name-only diff needs just the two tip
     trees (plus any blobs referenced in the range), so it works on shallow
     clones that lack full commit history.

Sprint branch resolution:

In sprint-based development models (e.g. Contify) work is branched from active
sprint branches such as `refs/remotes/upstream/sprint/33.1` rather than from
`master`. The resolver inspects `refs/remotes/upstream/master` plus the top
`DEFAULT_MAX_SPRINT_CANDIDATES` (5) most recent sprint branches sorted by
committer date, and picks the candidate whose tree is closest to `HEAD` (the
fewest changed files on a `--diff-filter=ACMRT` name-only diff). The closest
tree means the smallest honest change-set, which is what incremental checks
want to target.
================================================================================
"""

import diffimpactscout.gitrun as gitrun

MASTER_REF = "refs/remotes/upstream/master"
SPRINT_PREFIX = "sprint/"
DEFAULT_MAX_SPRINT_CANDIDATES = 5
REMOTE_FALLBACKS = ("main", "master", "develop")


def resolve_change_base(root, limit=DEFAULT_MAX_SPRINT_CANDIDATES):
    """Return the remote ref closest to HEAD, or None.

    `limit` caps how many `sprint/*` refs are considered: only the
    `limit` (default 5) most-recent ones by committer date, unlike the
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