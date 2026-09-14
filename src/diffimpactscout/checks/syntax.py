"""Syntax checks for JSON, Python AST, and merge-conflict markers.

Example: a "=======" marker in a dev-changed region is flagged as a leftover
merge conflict, and a dev-added invalid JSON file is flagged.
"""

import ast
import json
import os

from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)


@register
class JsonSyntaxCheck(Check):
    id = "syntax/json-syntax"
    scoped = "files"

    def run(self, context, files):
        issues = []
        for path in files or []:
            if not path.endswith(".json"):
                continue
            fn = os.path.join(context.root, path)
            try:
                with open(fn, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            try:
                json.loads(data)
            except ValueError as exc:
                issues.append(
                    CheckIssue(
                        path,
                        getattr(exc, "lineno", None),
                        getattr(exc, "colno", None),
                        self.id,
                        "invalid JSON: %s" % exc,
                    )
                )
        return CheckResult(issues=issues)


@register
class AstSyntaxCheck(Check):
    id = "syntax/ast-syntax"
    scoped = "files"

    def run(self, context, files):
        issues = []
        for path in files or []:
            if not path.endswith(".py"):
                continue
            fn = os.path.join(context.root, path)
            try:
                with open(fn, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            try:
                ast.parse(data.decode("utf-8-sig"), filename=path)
            except SyntaxError as exc:
                issues.append(
                    CheckIssue(
                        path,
                        exc.lineno,
                        exc.offset,
                        self.id,
                        "invalid python syntax: %s" % (exc.msg or "syntax error"),
                    )
                )
            except ValueError as exc:
                issues.append(
                    CheckIssue(
                        path,
                        None,
                        None,
                        self.id,
                        "invalid python source: %s" % exc,
                    )
                )
        return CheckResult(issues=issues)


@register
class MergeConflictCheck(Check):
    id = "syntax/merge-conflict"
    scoped = "lines"
    markers = ("<<<<<<< ", "=======", ">>>>>>> ")

    def run(self, context, files):
        issues = []
        for path in files or []:
            fn = os.path.join(context.root, path)
            try:
                with open(fn, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            lines = data.decode("utf-8", errors="replace").splitlines()
            changed = self._changed_lines(context, path)
            if changed is None:
                changed = set(range(1, len(lines) + 1))
            if not changed:
                continue
            for lineno in sorted(changed):
                if lineno < 1 or lineno > len(lines):
                    continue
                line = lines[lineno - 1]
                if self._is_marker(line):
                    issues.append(
                        CheckIssue(path, lineno, None, self.id, "conflict marker found")
                    )
        return CheckResult(issues=issues)

    def _is_marker(self, line):
        if line == "=======":
            return True
        return line.startswith("<<<<<<< ") or line.startswith(">>>>>>> ")

    def _changed_lines(self, context, path):
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