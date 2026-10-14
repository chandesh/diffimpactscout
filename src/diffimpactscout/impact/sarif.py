"""SARIF 2.1.0 output for impact rows (Phase B REQ-13).

Severity maps High->error, Medium->warning, Low->note. Each result
carries a stable ruleId, a message from why_matters (falling back to
ref), and one location (path plus the 1-based line from key/ref).
"""

import re

VERSION = "2.1.0"
_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_TOOL_NAME = "diffimpactscout"

_LEVELS = {"High": "error", "Medium": "warning", "Low": "note"}


def _line_of(row):
    """Return the 1-based line for a row; SARIF needs >= 1, so default 1."""
    key = row.get("key") or ""
    if ":" in key:
        try:
            return max(1, int(key.rsplit(":", 1)[-1]))
        except (TypeError, ValueError):
            pass
    ref = row.get("ref") or ""
    for pattern in (r"at (\d+)", r"line (\d+)"):
        match = re.search(pattern, ref)
        if match:
            return max(1, int(match.group(1)))
    try:
        return max(1, int(row.get("line") or 1))
    except (TypeError, ValueError):
        return 1


def _rule_id(severity):
    return "%s/%s" % (_TOOL_NAME, (severity or "note").lower())


def to_sarif(rows):
    """Return a SARIF 2.1.0 dict for the given impact rows."""
    results = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        severity = row.get("severity") or "Low"
        message = row.get("why_matters") or row.get("ref") or ""
        results.append({
            "ruleId": _rule_id(severity),
            "level": _LEVELS.get(severity, "note"),
            "message": {"text": message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": row.get("path") or ""},
                    "region": {"startLine": _line_of(row)},
                },
            }],
        })
    rules = []
    seen = set()
    for result in results:
        if result["ruleId"] in seen:
            continue
        seen.add(result["ruleId"])
        rules.append({"id": result["ruleId"]})
    return {
        "version": VERSION,
        "$schema": _SCHEMA,
        "runs": [{
            "tool": {"driver": {"name": _TOOL_NAME, "rules": rules}},
            "results": results,
        }],
    }
