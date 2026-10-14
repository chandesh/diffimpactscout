"""Render the impact report, classify severity, and decide whether a push should be blocked.

The terminal/stdout default is a plain aligned ASCII table that matches the
guard report's visual style; a Markdown table is available via an explicit
`--markdown` output mode for piping into PR comments or docs pages.

For scripting, prefer `--json` output: it carries the stable per-row fields
(`path`, `line`, `section`, `badge`, `key`) unbroken. The ASCII table wraps
long `path`/`module` cells for display and repeats each row's stable
`path:line` key as an unbroken trailing token on the first physical line of
that logical row so parsers can rejoin wrapped rows.

Example: a changed view referenced from a template and a frontend service
gets High severity, while a reference inside the changed file gets Low.
"""

import re
import sys

import diffimpactscout.env as env

_HEADER = "| # | Impacted File Path | Module / Subsystem | Category | Detected Reference / Usage | Severity | Action Required | Badge |"
_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- |"
_COLUMNS = ("#", "Impacted File Path", "Module / Subsystem", "Category", "Detected Reference / Usage", "Severity", "Action Required", "Badge")
_COLUMN_WIDTHS = (2, 38, 20, 8, 38, 8, 26, 8)


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
    return [str(index)] + [_cell(row, k) for k in ("path", "module", "category", "ref", "severity", "action", "badge")]


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
    seen = set()
    for row in sorted(rows or [], key=_key):
        if len(top) >= limit:
            break
        path = _cell(row, "path")
        if not path or path in changed or path in seen:
            continue
        seen.add(path)
        top.append((path, _cell(row, "owner") or ""))
    return top


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


def _row_key(row):
    """Return the stable ``path:line`` key for an ASCII row suffix token.

    Prefers the row's ``key`` field (set by ``finish_row``); falls back to
    ``path`` plus the line parsed from ``ref`` (``at <N>``/``line <N>``)
    or the row's ``line`` field, else 0. The token is emitted unbroken so
    parsers can rejoin wrapped rows; never wrap or truncate it here.
    """
    key = _cell(row, "key")
    if key:
        return key
    path = _cell(row, "path")
    ref = _cell(row, "ref")
    match = re.search(r"at (\d+)", ref)
    if match:
        return "%s:%s" % (path, match.group(1))
    match = re.search(r"line (\d+)", ref)
    if match:
        return "%s:%s" % (path, match.group(1))
    line = row.get("line") if isinstance(row, dict) else getattr(row, "line", None)
    return "%s:%s" % (path, line or 0)


def _ascii_table(rows, start=1):
    widths = list(_COLUMN_WIDTHS)
    header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(_COLUMNS))
    body = []
    for i, row in enumerate(rows or [], start=start):
        cells = [_wrap(v, widths[j]) for j, v in enumerate(_row_values(row, i))]
        key = _row_key(row)
        height = max(len(c) for c in cells)
        for line_idx in range(height):
            parts = []
            for j, cell_lines in enumerate(cells):
                value = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                parts.append(value.ljust(widths[j]))
            line = "  " + "  ".join(parts)
            if line_idx == 0 and key and _cell(row, "path"):
                line = line.rstrip() + "  " + key
            body.append(line)
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


_SECTION_ORDER = ("Breaking", "Direct", "Transitive")

_SECTION_LABELS = {
    "Breaking": "Breaking",
    "Direct": "Direct consumers",
    "Transitive": "Transitive (collapsed)",
}


def _normalized_section(row):
    """Map a row to its display section; missing/unknown goes to Direct."""
    sec = _cell(row, "section")
    if sec in _SECTION_ORDER:
        return sec
    return "Direct"


def _section_rank(row):
    """Rank a row for section display order: Breaking, Direct, Transitive.

    Rows with missing/unknown section sort last under Direct (after known
    Direct rows, before Transitive).
    """
    sec = _cell(row, "section")
    if sec in _SECTION_ORDER:
        return (_SECTION_ORDER.index(sec), 0)
    return (_SECTION_ORDER.index("Direct"), 1)


def _order_by_section(rows):
    """Stable-sort rows by section order for rendering; tiers stay as data."""
    return sorted(rows or [], key=_section_rank)


def _split_tiers(rows):
    """Return (tiered, untiered); tiered maps each tier value to its rows."""
    tiered = {"frontend": [], "backend": [], "heuristic": []}
    untiered = []
    for row in rows or []:
        tier = row.get("tier") if isinstance(row, dict) else getattr(row, "tier", None)
        if tier in tiered:
            tiered[tier].append(row)
        else:
            untiered.append(row)
    return tiered, untiered


def _table_cap(top_n, expand):
    """Return the table-body cap, or None when bypassed via --all/--expand."""
    if expand:
        return None
    if top_n is None:
        return 20
    if isinstance(top_n, bool):
        return 20
    try:
        cap = int(top_n)
    except (TypeError, ValueError):
        return 20
    if cap < 0:
        return 0
    return cap


def _full_ordered(rows):
    """Flatten rows into display order: section outer, tier inner, stable."""
    tiered, untiered = _split_tiers(rows)
    for tier in tiered:
        tiered[tier] = _order_by_section(tiered[tier])
    untiered = _order_by_section(untiered)
    full = []
    for section in _SECTION_ORDER:
        for tier, _label in _TIER_LABELS:
            bucket = [r for r in tiered.get(tier, []) if _normalized_section(r) == section]
            full.extend(bucket)
        full.extend([r for r in untiered if _normalized_section(r) == section])
    return full


def _apply_top_n(rows, top_n, expand):
    """Split section-ordered rows into (shown, hidden) for the Top-N cap."""
    full = _full_ordered(rows)
    cap = _table_cap(top_n, expand)
    if cap is None:
        return full, []
    return full[:cap], full[cap:]


def _overflow_lines(hidden):
    """One collapsed line per module; sums exactly to len(hidden)."""
    by_module = {}
    for row in hidden or []:
        mod = _cell(row, "module") or "unknown"
        by_module.setdefault(mod, []).append(row)
    lines = []
    for mod in sorted(by_module):
        bucket = by_module[mod]
        n = len(bucket)
        h = sum(1 for r in bucket if _cell(r, "severity") == "High")
        lines.append("... +%d more in %s (%d High)" % (n, mod, h))
    return lines


def top_n_counts(rows, top_n=None, expand=False):
    """Return (shown_count, hidden_counts) for JSON; additive, backward compatible."""
    _shown, hidden = _apply_top_n(rows or [], top_n, expand)
    by_module = {}
    for row in hidden:
        mod = _cell(row, "module") or "unknown"
        by_module[mod] = by_module.get(mod, 0) + 1
    return len(_shown), by_module


def _markdown_table(rows, start=1):
    lines = [_HEADER, _SEPARATOR]
    for i, row in enumerate(rows or [], start=start):
        cells = [str(i)] + [_cell(row, k).replace("|", "\\|") for k in ("path", "module", "category", "ref", "severity", "action", "badge")]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _render_ascii(rows, endpoints, unresolved, changed_count, hidden_count=0, expand=False,
                  summary_extra=None, top_n=None, changed_paths=None):
    lines = []
    lines.append("=" * 70)
    lines.append("  Impact Analysis Report")
    lines.append("  Blast radius: %d changed file(s)" % changed_count)
    lines.append("=" * 70)
    limit = 5
    if summary_extra is not None:
        _risk = summary_extra.get("risk")
        risk = _risk if _risk is not None else compute_summary_risk_score(rows)
        _impacted = summary_extra.get("impacted_count")
        impacted = _impacted if _impacted is not None else _impacted_count(rows, changed_paths)
        _top = summary_extra.get("top")
        top = _top if _top is not None else _top_impacted_paths(rows, limit, changed_paths)
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
    shown, hidden = _apply_top_n(rows, top_n, expand)
    tiered, untiered = _split_tiers(shown)
    for tier in tiered:
        tiered[tier] = _order_by_section(tiered[tier])
    untiered = _order_by_section(untiered)
    ordered = []
    start = 1
    for section in _SECTION_ORDER:
        section_label = _SECTION_LABELS[section]
        section_buckets = {
            tier: [r for r in tiered[tier] if _normalized_section(r) == section]
            for tier in tiered
        }
        section_untiered = [r for r in untiered if _normalized_section(r) == section]
        if not any(section_buckets.values()) and not section_untiered:
            continue
        lines.append("")
        lines.append("  " + section_label)
        lines.append("  " + "-" * 66)
        for tier, tier_label in _TIER_LABELS:
            bucket = section_buckets.get(tier) or []
            if not bucket:
                continue
            lines.append("")
            lines.append("  " + tier_label)
            lines.append("  " + "-" * 66)
            header, body = _ascii_table(bucket, start)
            start += len(bucket)
            lines.append(header)
            lines.extend(body)
            ordered.extend(bucket)
            if expand:
                for row in bucket:
                    for rel in (row.get("related") if isinstance(row, dict) else None) or []:
                        lines.append("      - %s" % rel)
        if section_untiered:
            header, body = _ascii_table(section_untiered, start)
            start += len(section_untiered)
            lines.append(header)
            lines.extend(body)
            ordered.extend(section_untiered)
    for overflow in _overflow_lines(hidden):
        lines.append("  " + overflow)
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


def _render_markdown(rows, endpoints, unresolved, changed_count, hidden_count=0, expand=False,
                     top_n=None):
    lines = []
    lines.append("# Impact Analysis Report")
    shown, hidden = _apply_top_n(rows, top_n, expand)
    tiered, untiered = _split_tiers(shown)
    for tier in tiered:
        tiered[tier] = _order_by_section(tiered[tier])
    untiered = _order_by_section(untiered)
    ordered = []
    start = 1
    for section in _SECTION_ORDER:
        section_label = _SECTION_LABELS[section]
        section_buckets = {
            tier: [r for r in tiered[tier] if _normalized_section(r) == section]
            for tier in tiered
        }
        section_untiered = [r for r in untiered if _normalized_section(r) == section]
        if not any(section_buckets.values()) and not section_untiered:
            continue
        lines.append("")
        lines.append("### " + section_label)
        lines.append("")
        for tier, tier_label in _TIER_LABELS:
            bucket = section_buckets.get(tier) or []
            if not bucket:
                continue
            lines.append("### " + tier_label)
            lines.append("")
            lines.extend(_markdown_table(bucket, start))
            start += len(bucket)
            ordered.extend(bucket)
            if expand:
                for row in bucket:
                    related = (row.get("related") if isinstance(row, dict) else None) or []
                    if not related:
                        continue
                    lines.append("")
                    lines.append("  <details><summary>%d templates</summary>" % len(related))
                    lines.append("")
                    for rel in related:
                        lines.append("  - %s" % rel)
                    lines.append("")
                    lines.append("  </details>")
        if section_untiered:
            lines.append("")
            lines.extend(_markdown_table(section_untiered, start))
            start += len(section_untiered)
            ordered.extend(section_untiered)
    for overflow in _overflow_lines(hidden):
        lines.append("")
        lines.append(overflow)
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
                                hidden_count=hidden_count, expand=expand,
                                top_n=top_n)
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