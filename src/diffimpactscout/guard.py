"""Orchestrate the pre-push guard: run the configured checks over the developer's change-set only.

Example: a sync push that only re-checks upstream-merged files exits 0,
while a dev-added trailing space or key leak fails the guard.
"""

import sys

import diffimpactscout.env as env
import diffimpactscout.gitrun as gitrun
import diffimpactscout.scope as scope
from diffimpactscout.checks.base import CheckContext, make_check

DIFF_FILTER = "ACMRT"


def _skip_requested():
    return env.skip_requested()


def _guard_mode(cfg):
    if env.strict_requested():
        return "strict"
    mode = cfg.get("guard", {}).get("blocking") or "warn"
    if mode == "strict":
        return "strict"
    return "warn"


def _staged_files(root):
    return gitrun.git_nul(
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=" + DIFF_FILTER],
        root,
    )


def _all_files(root):
    return gitrun.git_nul(["ls-files", "-z"], root)


def _entry_id(entry):
    if isinstance(entry, dict):
        return entry.get("id")
    return entry


def _warn(text):
    sys.stderr.write("diffimpactscout: warning: %s\n" % text)


def run_guard(root, cfg, staged=False, all_files=False, files=None):
    if _skip_requested():
        return 0

    from_ref, to_ref = env.pre_commit_refs()

    dev_scope = scope.DevScope(root)
    anchor = None

    if files is not None:
        file_set = [files] if isinstance(files, str) else list(files)
    elif staged:
        file_set = _staged_files(root)
    elif all_files:
        file_set = _all_files(root)
    else:
        anchor = dev_scope.scope_base(from_ref, to_ref)
        file_set = dev_scope.dev_files(anchor, from_ref, to_ref)

    mode = _guard_mode(cfg)
    ctx = CheckContext(
        root=root,
        anchor=anchor,
        from_ref=from_ref,
        to_ref=to_ref,
        scope=dev_scope,
        config=cfg,
        echo=True,
    )

    ran = 0
    total_issues = 0
    blocked = False

    for entry in cfg.get("guard", {}).get("checks") or []:
        check = None
        try:
            check = make_check(entry)
        except Exception as exc:
            _warn("check %r failed to build: %s" % (_entry_id(entry), exc))
            continue
        if check is None:
            _warn("unknown or invalid check %r; skipping" % (_entry_id(entry),))
            continue
        ran += 1
        check_files = [] if check.scoped == "repo" else file_set
        try:
            result = check.run(ctx, check_files)
            for issue in result.issues:
                print(issue.format())
                total_issues += 1
            for path in result.fixed:
                print("[fixed] %s" % path)
            for text in result.warned:
                _warn(text)
            should_block = check.always_block or (check.blocking and mode == "strict")
            if should_block and result.has_issues():
                blocked = True
        except Exception as exc:
            _warn("check %r failed: %s" % (check.id, exc))
            continue

    print(
        "diffimpactscout: %d check(s), %d issue(s)" % (ran, total_issues)
    )
    return 1 if blocked else 0