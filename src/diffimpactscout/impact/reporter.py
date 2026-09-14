"""Render the impact report, classify severity, and decide whether a push should be blocked.

The terminal/stdout default is a plain aligned ASCII table that matches the
guard report's visual style; a Markdown table is available via an explicit
`--markdown` output mode for piping into PR comments or docs pages.

Example: a changed view referenced from a template and a frontend service
gets High severity, while a reference inside the changed file gets Low.
"""

import sys

import diffimpactscout.env as env

_HEADER = "| # | Impacted File Path | Module / Subsystem | Category | Detected Reference / Usage | Severity | Action Required |"
_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- |"
_COLUMNS = ("#", "Impacted File Path", "Module / Subsystem", "Category", "Detected Reference / Usage", "Severity", "Action Required")


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


def severity_reason(layers, deleted_renamed, changed_paths, ref_path):
    if deleted_renamed:
        return "symbol was deleted or renamed in this change-set"
    layers = set(layers or ())
    if len(layers) >= 2:
        return "referenced from multiple layers (%s)" % " + ".join(sorted(layers))
    if ref_path in (changed_paths or ()):
        return "reference is inside the changed file itself"
    if not ref_path or not changed_paths:
        return "no external reference beyond the change-set"
    return "referenced from a file outside the change-set"


def _cell(row, key):
    if isinstance(row, dict):
        value = row.get(key)
    else:
        value = getattr(row, key, None)
    if value is None:
        return ""
    return str(value)


def _row_values(row, index):
    return [str(index)] + [_cell(row, k) for k in ("path", "module", "category", "ref", "severity", "action")]


def _counts(rows):
    counts = {"High": 0, "Medium": 0, "Low": 0}
    for row in rows:
        sev = _cell(row, "severity")
        if sev in counts:
            counts[sev] += 1
    return counts


def _summary_line(rows, changed_count):
    counts = _counts(rows or [])
    return "Summary: High: %d, Medium: %d, Low: %d (%d changed file(s))" % (
        counts["High"],
        counts["Medium"],
        counts["Low"],
        changed_count,
    )


def _rationale_lines(rows):
    lines = []
    for i, row in enumerate(rows or [], start=1):
        reason = _cell(row, "reason")
        if not reason:
            continue
        label = "%s -> %s" % (_cell(row, "path") or "-", _cell(row, "ref") or "-")
        lines.append("    %d. %s - %s (%s)" % (i, _cell(row, "severity") or "?", label, reason))
    return lines


def _ascii_table(rows):
    values = [_row_values(row, i) for i, row in enumerate(rows or [], start=1)]
    widths = []
    for i in range(len(_COLUMNS)):
        width = len(_COLUMNS[i])
        for vals in values:
            width = max(width, len(vals[i]))
        widths.append(width)
    header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(_COLUMNS))
    body = []
    for vals in values:
        body.append("  " + "  ".join(v.ljust(widths[i]) for i, v in enumerate(vals)))
    return header, body


def _render_ascii(rows, unresolved, changed_count):
    lines = []
    lines.append("=" * 70)
    lines.append("  Impact Analysis Report")
    lines.append("  Blast radius: %d changed file(s)" % changed_count)
    lines.append("=" * 70)
    lines.append("")
    header, body = _ascii_table(rows)
    lines.append(header)
    for line in body:
        lines.append(line)
    if unresolved:
        lines.append("")
        lines.append("  Unresolved references (manual check required):")
        for ref in unresolved:
            lines.append("    - %s" % str(ref))
    rationale = _rationale_lines(rows)
    if rationale:
        lines.append("")
        lines.append("  Severity rationale:")
        lines.extend(rationale)
    lines.append("")
    lines.append("-" * 70)
    lines.append("  " + _summary_line(rows, changed_count))
    lines.append("=" * 70)
    return "\n".join(lines) + "\n"


def _render_markdown(rows, unresolved, changed_count):
    lines = []
    lines.append("# Impact Analysis Report")
    lines.append("")
    lines.append(_HEADER)
    lines.append(_SEPARATOR)
    for i, row in enumerate(rows or [], start=1):
        cells = [str(i)] + [_cell(row, k).replace("|", "\\|") for k in ("path", "module", "category", "ref", "severity", "action")]
        lines.append("| " + " | ".join(cells) + " |")
    if unresolved:
        lines.append("")
        lines.append("## Unresolved references (manual check required)")
        for ref in unresolved:
            lines.append("- %s" % str(ref))
    rationale = _rationale_lines(rows)
    if rationale:
        lines.append("")
        lines.append("## Severity rationale")
        for line in rationale:
            lines.append("- " + line.lstrip())
    lines.append("")
    lines.append(_summary_line(rows, changed_count))
    return "\n".join(lines) + "\n"


def render_report(rows, unresolved, changed_count, markdown=False):
    if markdown:
        return _render_markdown(rows, unresolved, changed_count)
    return _render_ascii(rows, unresolved, changed_count)


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