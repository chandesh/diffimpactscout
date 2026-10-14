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
_COLUMN_WIDTHS = (2, 38, 20, 8, 38, 8, 26)


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


def compute_summary_risk_score(rows):
    """Phase-A placeholder risk score (0-100) from severity counts.

    Weights High as 1.0 and Medium as 0.5 over all counted rows;
    Phase-B replaces this with the fan_in formula.
    """
    counts = _counts(rows or [])
    total = counts["High"] + counts["Medium"] + counts["Low"]
    if not total:
        return 0
    return min(100, int(round(
        100.0 * (counts["High"] * 1.0 + counts["Medium"] * 0.5) / total)))


_SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _impacted_count(rows, changed_paths=None):
    """Count distinct impacted paths excluding the changed set."""
    changed = set(changed_paths or ())
    seen = set()
    for row in rows or []:
        path = _cell(row, "path")
        if not path or path in changed:
            continue
        seen.add(path)
    return len(seen)


def _top_impacted_paths(rows, limit=5, changed_paths=None):
    """Rank the top impacted files for the summary header.

    Returns up to ``limit`` ``(path, owner)`` pairs ordered by severity
    rank (High, Medium, Low) then path. Paths in ``changed_paths`` are
    excluded so the Top list only contains impacted files, consistent
    with ``_impacted_count`` (Phase-A placeholder ranking).
    """
    changed = set(changed_paths or ())

    def _key(row):
        sev = _cell(row, "severity")
        return (_SEVERITY_RANK.get(sev, 99), _cell(row, "path") or "")

    top = []
    for row in sorted(rows or [], key=_key):
        if len(top) >= limit:
            break
        path = _cell(row, "path")
        if not path or path in changed:
            continue
        top.append((path, _cell(row, "owner") or ""))
    return top


def _summary_top(rows, limit=5, changed_paths=None):
    """Backward-compatible wrapper for _top_impacted_paths."""
    return _top_impacted_paths(rows, limit=limit, changed_paths=changed_paths)


def _summary_line(rows, changed_count):
    counts = _counts(rows or [])
    return "Summary: High: %d, Medium: %d, Low: %d (%d changed file(s))" % (
        counts["High"],
        counts["Medium"],
        counts["Low"],
        changed_count,
    )


def _rationale_lines(rows):
    """Severity rationale lines, numbered to match the rendered table order."""
    lines = []
    for i, row in enumerate(rows or [], start=1):
        reason = _cell(row, "reason")
        if not reason:
            continue
        label = "%s -> %s" % (_cell(row, "path") or "-", _cell(row, "ref") or "-")
        lines.append("    %d. %s - %s (%s)" % (i, _cell(row, "severity") or "?", label, reason))
    return lines


def _wrap(text, width):
    """Wrap a cell value to a fixed width, breaking paths at slashes."""
    text = "" if text is None else str(text)
    if width <= 0 or not text:
        return [text]
    lines = []
    cur = ""
    for token in text.split(" "):
        if not cur:
            cur = token
        elif len(cur) + 1 + len(token) <= width:
            cur += " " + token
        else:
            lines.extend(_split_token(cur, width))
            cur = token
    if cur:
        lines.extend(_split_token(cur, width))
    return lines or [""]


def _split_token(token, width):
    if len(token) <= width:
        return [token]
    if "/" in token:
        parts = token.split("/")
        out = []
        cur = ""
        for part in parts:
            candidate = part if not cur else cur + "/" + part
            if len(candidate) <= width:
                cur = candidate
            else:
                if cur:
                    out.append(cur)
                cur = part
        if cur:
            out.append(cur)
        result = []
        for i, line in enumerate(out):
            if i < len(out) - 1 and "/" in line and len(line) < width:
                line = line + "/"
            result.extend([line[j : j + width] for j in range(0, len(line), width)])
        return result
    return [token[i : i + width] for i in range(0, len(token), width)]


def _ascii_table(rows, start=1):
    widths = list(_COLUMN_WIDTHS)
    header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(_COLUMNS))
    body = []
    for i, row in enumerate(rows or [], start=start):
        cells = [_wrap(v, widths[j]) for j, v in enumerate(_row_values(row, i))]
        height = max(len(c) for c in cells)
        for line_idx in range(height):
            parts = []
            for j, cell_lines in enumerate(cells):
                value = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                parts.append(value.ljust(widths[j]))
            body.append("  " + "  ".join(parts))
    return header, body


def _endpoint_lines(endpoints):
    lines = []
    for i, ep in enumerate(endpoints or [], start=1):
        url = ep.get("url") or ""
        handler = ep.get("handler") or ""
        name = ep.get("name") or ""
        label = handler
        if name:
            label = "%s (%s)" % (handler, name)
        lines.append("  %2d  %-55s %s" % (i, url, label))
    return lines


def _unresolved_line(ref):
    """Render one unresolved reference.

    Reverse-chain entries are dicts (``file``/``path``, optional ``ref`` and
    ``reason``); they render as ``location: ref (reason)``. Plain strings
    (legacy) are rendered unchanged.
    """
    if not isinstance(ref, dict):
        return str(ref)
    location = ref.get("file") or ref.get("path") or ""
    detail = ref.get("ref")
    reason = ref.get("reason") or ""
    head = ": ".join(str(part) for part in (location, detail) if part) or "?"
    return "%s (%s)" % (head, reason) if reason else head


_TIER_LABELS = (
    ("frontend", "Frontend impacts"),
    ("backend", "Backend impacts - certain"),
    ("heuristic", "Heuristic / transitive impacts"),
)


def _split_tiers(rows):
    """Return (tiered, untiered); tiered maps each tier value to its rows."""
    tiered = {"frontend": [], "backend": [], "heuristic": []}
    untiered = []
    for row in rows or []:
        tier = row.get("tier")
        if tier in tiered:
            tiered[tier].append(row)
        else:
            untiered.append(row)
    return tiered, untiered


def _markdown_table(rows, start=1):
    lines = [_HEADER, _SEPARATOR]
    for i, row in enumerate(rows or [], start=start):
        cells = [str(i)] + [_cell(row, k).replace("|", "\\|") for k in ("path", "module", "category", "ref", "severity", "action")]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _render_ascii(rows, endpoints, unresolved, changed_count, hidden_count=0, expand=False,
                  summary_extra=None, top_n=None, changed_paths=None):
    lines = []
    lines.append("=" * 70)
    lines.append("  Impact Analysis Report")
    lines.append("  Blast radius: %d changed file(s)" % changed_count)
    lines.append("=" * 70)
    limit = top_n or 5
    if summary_extra is not None:
        risk = summary_extra.get("risk") or compute_summary_risk_score(rows)
        impacted = summary_extra.get("impacted_count") or _impacted_count(rows, changed_paths)
        top = summary_extra.get("top") or _top_impacted_paths(rows, limit, changed_paths)
    else:
        risk = compute_summary_risk_score(rows)
        impacted = _impacted_count(rows, changed_paths)
        top = _top_impacted_paths(rows, limit, changed_paths)
    counts = _counts(rows or [])
    lines.append("  Risk: %d/100 (High*1.0 + Medium*0.5 over rows; Phase-B replaces with fan_in formula)" % risk)
    lines.append("  High: %d, Medium: %d, Low: %d" % (counts["High"], counts["Medium"], counts["Low"]))
    lines.append("  changed %d files vs impacted %d files" % (changed_count, impacted))
    for path, owner in (top or [])[:limit]:
        lines.append("  - %s (%s)" % (path, owner or "unowned"))
    tiered, untiered = _split_tiers(rows)
    ordered = []
    start = 1
    for tier, label in _TIER_LABELS:
        if not tiered[tier]:
            continue
        lines.append("")
        lines.append("  " + label)
        lines.append("  " + "-" * 66)
        header, body = _ascii_table(tiered[tier], start)
        start += len(tiered[tier])
        lines.append(header)
        lines.extend(body)
        ordered.extend(tiered[tier])
        if expand:
            for row in tiered[tier]:
                for rel in row.get("related") or []:
                    lines.append("      - %s" % rel)
    if untiered:
        lines.append("")
        header, body = _ascii_table(untiered, start)
        lines.append(header)
        lines.extend(body)
        ordered.extend(untiered)
    if endpoints:
        lines.append("")
        lines.append("  Affected WebURL/API-URL endpoints:")
        lines.extend(_endpoint_lines(endpoints))
    if unresolved:
        lines.append("")
        lines.append("  Unresolved references (manual check required):")
        for ref in unresolved:
            lines.append("    - %s" % _unresolved_line(ref))
    rationale = _rationale_lines(ordered)
    if rationale:
        lines.append("")
        lines.append("  Severity rationale:")
        lines.extend(rationale)
    lines.append("")
    lines.append("-" * 70)
    lines.append("  " + _summary_line(rows, changed_count))
    if hidden_count:
        lines.append("  %d lower-confidence impacts hidden - use --all to expand" % hidden_count)
    lines.append("=" * 70)
    return "\n".join(lines) + "\n"


def _render_markdown(rows, endpoints, unresolved, changed_count, hidden_count=0, expand=False):
    lines = []
    lines.append("# Impact Analysis Report")
    tiered, untiered = _split_tiers(rows)
    ordered = []
    start = 1
    for tier, label in _TIER_LABELS:
        if not tiered[tier]:
            continue
        lines.append("")
        lines.append("### " + label)
        lines.append("")
        lines.extend(_markdown_table(tiered[tier], start))
        start += len(tiered[tier])
        ordered.extend(tiered[tier])
        if expand:
            for row in tiered[tier]:
                related = row.get("related") or []
                if not related:
                    continue
                lines.append("")
                lines.append("  <details><summary>%d templates</summary>" % len(related))
                lines.append("")
                for rel in related:
                    lines.append("  - %s" % rel)
                lines.append("")
                lines.append("  </details>")
    if untiered:
        lines.append("")
        lines.extend(_markdown_table(untiered, start))
        ordered.extend(untiered)
    if endpoints:
        lines.append("")
        lines.append("## Affected WebURL/API-URL endpoints")
        for ep in endpoints or []:
            lines.append(
                "- `%s` -> %s (%s)" % (ep.get("url") or "", ep.get("handler") or "", ep.get("name") or "")
            )
    if unresolved:
        lines.append("")
        lines.append("## Unresolved references (manual check required)")
        for ref in unresolved:
            lines.append("- %s" % _unresolved_line(ref))
    rationale = _rationale_lines(ordered)
    if rationale:
        lines.append("")
        lines.append("## Severity rationale")
        for line in rationale:
            lines.append("- " + line.lstrip())
    lines.append("")
    lines.append(_summary_line(rows, changed_count))
    if hidden_count:
        lines.append("%d lower-confidence impacts hidden - use --all to expand" % hidden_count)
    return "\n".join(lines) + "\n"


def render_report(rows, endpoints, unresolved, changed_count, markdown=False,
                  hidden_count=0, expand=False, summary_extra=None, top_n=None,
                  changed_paths=None):
    if markdown:
        return _render_markdown(rows, endpoints, unresolved, changed_count,
                                hidden_count=hidden_count, expand=expand)
    return _render_ascii(rows, endpoints, unresolved, changed_count,
                         hidden_count=hidden_count, expand=expand,
                         summary_extra=summary_extra, top_n=top_n,
                         changed_paths=changed_paths)


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