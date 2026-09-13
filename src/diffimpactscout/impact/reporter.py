"""Render the impact report table, classify severity, and decide whether a push should be blocked.

Example: a changed view referenced from a template and a frontend service
gets High severity, while a reference inside the changed file gets Low.
"""

import sys

import diffimpactscout.env as env

_HEADER = "| # | Impacted File Path | Module / Subsystem | Category | Detected Reference / Usage | Severity | Action Required |"
_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- |"


def classify_severity(layers, deleted_renamed, changed_paths, ref_path):
    if deleted_renamed:
        return "High"
    if len(set(layers or ())) >= 2:
        return "High"
    if ref_path in (changed_paths or ()):
        return "Low"
    if not ref_path or not changed_paths:
        return "Low"
    return "Medium"


def _cell(row, key):
    if isinstance(row, dict):
        value = row.get(key)
    else:
        value = getattr(row, key, None)
    if value is None:
        return ""
    return str(value).replace("|", "\\|")


def _counts(rows):
    counts = {"High": 0, "Medium": 0, "Low": 0}
    for row in rows:
        sev = _cell(row, "severity")
        if sev in counts:
            counts[sev] += 1
    return counts


def render_report(rows, unresolved, changed_count):
    lines = []
    lines.append("# Impact Analysis Report")
    lines.append("")
    lines.append(_HEADER)
    lines.append(_SEPARATOR)
    for i, row in enumerate(rows or [], start=1):
        lines.append(
            "| %d | %s | %s | %s | %s | %s | %s |"
            % (
                i,
                _cell(row, "path"),
                _cell(row, "module"),
                _cell(row, "category"),
                _cell(row, "ref"),
                _cell(row, "severity"),
                _cell(row, "action"),
            )
        )
    if unresolved:
        lines.append("")
        lines.append("## Unresolved references (manual check required)")
        for ref in unresolved:
            lines.append("- %s" % str(ref))
    lines.append("")
    counts = _counts(rows or [])
    lines.append(
        "Summary: %d changed file(s); High: %d, Medium: %d, Low: %d"
        % (changed_count, counts["High"], counts["Medium"], counts["Low"])
    )
    return "\n".join(lines) + "\n"


def interactive_tty():
    return sys.stdin.isatty()


def confirm(prompt_text):
    sys.stdout.write(prompt_text + " ")
    sys.stdout.flush()
    answer = (sys.stdin.readline() or "").strip().lower()
    if answer in ("", "y", "yes"):
        return True
    return False


def _is_true(value):
    return env.is_true(value)


def _skip_requested():
    return env.skip_requested()


def should_block(strict_env, tty):
    if _skip_requested():
        return (False, "skip env var set; impact check skipped")
    if tty:
        return (True, "interactive; prompt user to proceed")
    if _is_true(strict_env):
        return (True, "strict mode; blocking in non-interactive context")
    return (False, "non-interactive; report shown as warning, commit not blocked")