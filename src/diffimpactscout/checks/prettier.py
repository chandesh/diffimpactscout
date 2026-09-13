"""Prettier incremental check: flags files the developer changed whose formatting differs from prettier.

Example: a dev-changed .ts file that prettier would reformat produces an
issue and a hint to run prettier on it.
"""

import os
import subprocess

from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)

_PRETTIER_EXTS = (".ts", ".js", ".html", ".css", ".scss", ".json")


def _run_npx(argv, root):
    proc = subprocess.Popen(
        argv,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    return proc, out, err


def _load_baseline(root):
    path = os.path.join(root, ".prettierignore_baseline")
    try:
        with open(path, "r") as fh:
            content = fh.read()
    except (OSError, ValueError):
        return set()
    entries = set()
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("./"):
            line = line[2:]
        entries.add(line)
    return entries


def _dev_changed(context, path):
    try:
        return bool(
            context.scope.contains(
                context.anchor, path, context.from_ref, context.to_ref
            )
        )
    except Exception:
        return False


@register
class PrettierCheck(Check):
    id = "prettier"
    scoped = "files"

    def run(self, context, files):
        issues = []
        warned = []
        baseline = _load_baseline(context.root)
        for path in files or []:
            if not path.endswith(_PRETTIER_EXTS):
                continue
            parts = path.split("/")
            if "src" not in parts and "app" not in parts:
                continue
            if path in baseline and not _dev_changed(context, path):
                continue
            try:
                proc, _out, _err = _run_npx(
                    ["npx", "prettier", "--check", path],
                    context.root,
                )
            except OSError as exc:
                warned.append("npx prettier unavailable: %s" % exc)
                break
            if proc.returncode == 0:
                continue
            if proc.returncode != 1:
                warned.append(
                    "npx prettier --check %s exited with code %s"
                    % (path, proc.returncode)
                )
                continue
            issues.append(
                CheckIssue(
                    path,
                    0,
                    0,
                    self.id,
                    "%s is not formatted. Fix with: npx prettier --write %s"
                    % (path, path),
                )
            )
        return CheckResult(issues=issues, warned=warned)