"""ESLint incremental check: reports only violations on lines the developer changed, and honors a saved baseline.

Example: a no-unused-vars error on a line the developer added fails the
guard, while the same error on an untouched upstream line is skipped.
"""

import json
import os
import subprocess

from diffimpactscout.checks.base import (
    Check,
    CheckIssue,
    CheckResult,
    register,
)


def _normalize(path):
    if path.startswith("./"):
        return path[2:]
    return path


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


def _load_baseline(root):
    path = os.path.join(root, ".eslint_baseline.json")
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and "results" in data:
        data = data.get("results")
    if isinstance(data, dict):
        entries = []
        for key, values in data.items():
            if isinstance(values, list):
                entries.append((_normalize(key), set(values)))
        return entries
    if not isinstance(data, list):
        return []
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        keys = set()
        for message in item.get("messages") or []:
            line = message.get("line")
            column = message.get("column")
            rule_id = message.get("ruleId")
            if line and column and rule_id:
                keys.add("%s:%s:%s" % (line, column, rule_id))
        file_path = item.get("filePath")
        if file_path:
            entries.append((file_path, keys))
    return entries


def _path_matches(file_path, candidate):
    if file_path == candidate:
        return True
    if file_path.endswith(os.sep + candidate):
        return True
    if file_path.endswith("/" + candidate):
        return True
    return False


def _baseline_keys(baseline, root, path):
    candidates = (path, _normalize(path), os.path.join(root, path))
    for file_path, keys in baseline:
        for candidate in candidates:
            if _path_matches(file_path, candidate):
                return keys
    return set()


def _run_npx(argv, root):
    proc = subprocess.Popen(
        argv,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    return proc, out, err


@register
class EslintCheck(Check):
    id = "eslint"
    scoped = "lines"

    def run(self, context, files):
        issues = []
        warned = []
        baseline = _load_baseline(context.root)
        for path in files or []:
            changed = _changed_lines(context, path)
            if changed is not None and not changed:
                continue
            try:
                proc, out, _err = _run_npx(
                    ["npx", "eslint", path, "--format", "json"],
                    context.root,
                )
            except OSError as exc:
                warned.append("npx eslint unavailable: %s" % exc)
                break
            if proc.returncode not in (0, 1):
                warned.append(
                    "npx eslint %s exited with code %s"
                    % (path, proc.returncode)
                )
                continue
            try:
                data = json.loads(out.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(data, dict):
                data = data.get("results")
            if not isinstance(data, list):
                continue
            baseline_keys = _baseline_keys(baseline, context.root, path)
            for file_result in data:
                if not isinstance(file_result, dict):
                    continue
                for message in file_result.get("messages") or []:
                    line = message.get("line")
                    column = message.get("column")
                    rule_id = message.get("ruleId")
                    if changed is not None and line not in changed:
                        continue
                    if (
                        line
                        and column
                        and rule_id
                        and "%s:%s:%s" % (line, column, rule_id)
                        in baseline_keys
                    ):
                        continue
                    issues.append(
                        CheckIssue(
                            path,
                            line,
                            column,
                            rule_id or self.id,
                            message.get("message") or "",
                        )
                    )
        return CheckResult(issues=issues, warned=warned)