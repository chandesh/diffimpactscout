"""Ruff and ruff-format incremental checks scoped to the developer's changed lines.

Example: an E501 on a line the developer added blocks the push, while an
E501 on an untouched upstream line does not.
"""

import json
import re
import subprocess

from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _split_hunks(text):
    hunks = []
    current = []
    for line in text.split("\n"):
        if line.startswith("@@"):
            if current:
                hunks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append("\n".join(current))
    return hunks


def _truncate(text, limit=30):
    lines = text.split("\n")
    if len(lines) <= limit:
        return text
    return "\n".join(lines[:limit]) + "\n... (%d more lines)" % (
        len(lines) - limit
    )


def _changed_lines(context, path):
    try:
        tracked = context.is_tracked(path)
        changed = context.changed_lines(path)
    except Exception:
        return set()
    if changed is not None:
        return changed
    if tracked:
        return set()
    return None


def _filter_diff_by_lines(diff, changed):
    if changed is None:
        return diff
    if not changed:
        return ""
    lines = diff.split("\n")
    header = []
    i = 0
    while i < len(lines) and (
        lines[i].startswith("--- ") or lines[i].startswith("+++ ")
    ):
        header.append(lines[i])
        i += 1
    out = list(header)
    body = "\n".join(lines[i:])
    for hunk in _split_hunks(body):
        if not hunk.startswith("@@"):
            continue
        m = _HUNK_RE.match(hunk)
        if not m:
            continue
        old_ln = int(m.group(1))
        block = []
        block_old_lines = []
        found = []
        for ln in hunk.split("\n")[1:]:
            if ln.startswith("-"):
                block.append(ln)
                block_old_lines.append(old_ln)
                old_ln += 1
            elif ln.startswith("+"):
                block.append(ln)
            else:
                if block and any(o in changed for o in block_old_lines):
                    found.extend(block)
                block = []
                block_old_lines = []
                if ln.startswith(" "):
                    old_ln += 1
        if block and any(o in changed for o in block_old_lines):
            found.extend(block)
        if found:
            out.append(hunk.split("\n")[0])
            out.extend(found)
    if len(out) > len(header):
        return "\n".join(out)
    return ""


def _run_ruff(argv, root):
    proc = subprocess.Popen(
        argv,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    return proc, out, err


@register
class RuffCheck(Check):
    id = "ruff"
    scoped = "lines"

    def run(self, context, files):
        issues = []
        warned = []
        for path in files or []:
            if not path.endswith(".py"):
                continue
            changed = _changed_lines(context, path)
            if changed is not None and not changed:
                continue
            try:
                proc, out, err = _run_ruff(
                    ["ruff", "check", path, "--output-format", "json"],
                    context.root,
                )
            except OSError as exc:
                warned.append("ruff unavailable: %s" % exc)
                break
            if proc.returncode not in (0, 1):
                warned.append(
                    "ruff check %s exited with code %s"
                    % (path, proc.returncode)
                )
                continue
            try:
                data = json.loads(out.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(data, list):
                continue
            for violation in data:
                location = violation.get("location") or {}
                row = location.get("row")
                if changed is not None and row not in changed:
                    continue
                issues.append(
                    CheckIssue(
                        path,
                        row or 0,
                        location.get("column") or 0,
                        violation.get("code") or self.id,
                        violation.get("message") or "",
                    )
                )
        return CheckResult(issues=issues, warned=warned)


@register
class RuffFormatCheck(Check):
    id = "ruff-format"
    scoped = "lines"

    def run(self, context, files):
        issues = []
        warned = []
        for path in files or []:
            if not path.endswith(".py"):
                continue
            changed = _changed_lines(context, path)
            if changed is not None and not changed:
                continue
            try:
                proc, _out, _err = _run_ruff(
                    ["ruff", "format", "--check", path], context.root
                )
            except OSError as exc:
                warned.append("ruff unavailable: %s" % exc)
                break
            if proc.returncode == 0:
                continue
            if proc.returncode != 1:
                warned.append(
                    "ruff format --check %s exited with code %s"
                    % (path, proc.returncode)
                )
                continue
            try:
                diff_proc, out, _err = _run_ruff(
                    ["ruff", "format", "--diff", path], context.root
                )
            except OSError as exc:
                warned.append("ruff unavailable: %s" % exc)
                break
            if diff_proc.returncode not in (0, 1):
                warned.append(
                    "ruff format --diff %s exited with code %s"
                    % (path, diff_proc.returncode)
                )
                continue
            diff = out.decode("utf-8", errors="replace")
            kept = _filter_diff_by_lines(diff, changed)
            if not kept:
                continue
            issues.append(
                CheckIssue(
                    path,
                    0,
                    0,
                    self.id,
                    "formatting issue in changed lines:\n%s\nFix with: ruff format %s"
                    % (_truncate(kept), path),
                )
            )
        return CheckResult(issues=issues, warned=warned)