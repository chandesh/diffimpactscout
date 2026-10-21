"""Syntax checks for JSON, Python AST, and merge-conflict markers.

Example: a "=======" marker in a dev-changed region is flagged as a leftover
merge conflict, and a dev-added invalid JSON file is flagged.
"""

import ast
import json
import os
import warnings

from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)


_JSONC_BASENAMES = ("tsconfig.json", "jsconfig.json", "devcontainer.json")


def _is_jsonc(path):
    """Return True when a path is JSON-with-comments by convention.

    TypeScript, VS Code, and their tooling treat these filenames as JSONC
    (comments and trailing commas allowed) while every other ``.json`` file
    stays strict JSON, matching how editors and linters infer the dialect
    from the filename rather than the extension.
    """
    posix = (path or "").replace("\\", "/")
    base = posix.rsplit("/", 1)[-1]
    if base in _JSONC_BASENAMES:
        return True
    if base.endswith(".code-workspace"):
        return True
    if (base.startswith("tsconfig.") or base.startswith("jsconfig.")) \
            and base.endswith(".json"):
        return True
    if "/.vscode/" in posix or posix.startswith(".vscode/"):
        return base.endswith(".json")
    return False


def _strip_jsonc(text):
    """Return JSONC ``text`` with comments and trailing commas blanked out.

    Comment and trailing-comma characters are replaced by spaces (newlines
    kept) so parse-error line and column numbers still point at the original
    source. A small scanner tracks string state, so comment markers inside
    string literals (for example ``"http://x"``) are never mistaken for
    comments -- something a regular-expression approach cannot do safely.
    """
    out = []
    state = "code"
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == '"':
                state = "string"
                out.append(ch)
            elif ch == "/" and nxt == "/":
                state = "line"
                out.append(" ")
                i += 1
                out.append(" ")
            elif ch == "/" and nxt == "*":
                state = "block"
                out.append(" ")
                i += 1
                out.append(" ")
            else:
                out.append(ch)
        elif state == "string":
            out.append(ch)
            if ch == "\\":
                state = "escape"
            elif ch == '"':
                state = "code"
        elif state == "escape":
            out.append(ch)
            state = "string"
        elif state == "line":
            if ch == "\n":
                state = "code"
                out.append(ch)
            else:
                out.append(" ")
        elif ch == "*" and nxt == "/":
            out.append(" ")
            i += 1
            out.append(" ")
            state = "code"
        elif ch == "\n":
            out.append(ch)
        else:
            out.append(" ")
        i += 1
    return _strip_trailing_commas("".join(out))


def _strip_trailing_commas(text):
    """Blank commas that immediately precede a ``}`` or ``]`` outside strings."""
    out = list(text)
    state = "code"
    i = 0
    n = len(out)
    while i < n:
        ch = out[i]
        if state == "code":
            if ch == '"':
                state = "string"
            elif ch == ",":
                j = i + 1
                while j < n and out[j] in " \t\r\n":
                    j += 1
                if j < n and out[j] in "}]":
                    out[i] = " "
        elif state == "string":
            if ch == "\\":
                i += 1
            elif ch == '"':
                state = "code"
        i += 1
    return "".join(out)


@register
class JsonSyntaxCheck(Check):
    id = "syntax/json-syntax"
    scoped = "files"

    def run(self, context, files):
        issues = []
        warned = []
        for path in files or []:
            if not path.endswith(".json"):
                continue
            fn = os.path.join(context.root, path)
            try:
                with open(fn, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if not data.strip():
                warned.append("%s (empty file, nothing to parse)" % path)
                continue
            try:
                if _is_jsonc(path):
                    json.loads(_strip_jsonc(data.decode("utf-8-sig")))
                else:
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
        return CheckResult(issues=issues, warned=warned)


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
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
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