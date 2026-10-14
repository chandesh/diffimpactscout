"""SARIF 2.1.0 output for impact rows (Phase B REQ-13).

Severity maps High->error, Medium->warning, Low->note. Each result
carries a stable ruleId, a message from why_matters (falling back to
ref, then key/path/severity), and one location (path plus the 1-based
line from key/ref). ``validate`` is a minimal stdlib-only structural
check used before writing a document; it is not a full JSON-Schema
validation.
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
    return "%s/%s" % (_TOOL_NAME, (severity or "Low").lower())


def to_sarif(rows):
    """Return a SARIF 2.1.0 dict for the given impact rows."""
    results = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        # Unknown/missing severity normalizes to Low so both the ruleId
        # suffix and the level stay in the contracted vocabulary.
        severity = row.get("severity")
        if severity not in _LEVELS:
            severity = "Low"
        message = row.get("why_matters") or row.get("ref") or ""
        if not message:
            message = row.get("key") or row.get("path") or severity
        uri = row.get("path") or row.get("key") or "unknown"
        results.append({
            "ruleId": _rule_id(severity),
            "level": _LEVELS.get(severity, "note"),
            "message": {"text": message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {"startLine": _line_of(row)},
                },
            }],
        })
    rules = []
    seen = set()
    for result in results:
        rule_id = result["ruleId"]
        if rule_id in seen:
            continue
        seen.add(rule_id)
        rules.append({
            "id": rule_id,
            "name": rule_id,
            "shortDescription": {"text": "impact severity: %s" % result["level"]},
        })
    return {
        "version": VERSION,
        "$schema": _SCHEMA,
        "runs": [{
            "tool": {"driver": {"name": _TOOL_NAME, "rules": rules}},
            "results": results,
        }],
    }


def validate(doc):
    """Return SARIF 2.1.0 conformance violations (empty list == valid).

    Minimal stdlib-only structural check (not full JSON-Schema): version
    is 2.1.0, runs is a non-empty list, each run has
    tool.driver.rules (list) and results (list), and each result has a
    non-empty ruleId, a level in {error,warning,note}, non-empty
    message.text, and a non-empty locations list whose entries carry
    physicalLocation.artifactLocation.uri and region.startLine >= 1.
    """
    if not isinstance(doc, dict):
        return ["document is not an object"]
    problems = []
    if doc.get("version") != VERSION:
        problems.append("version must be %r" % VERSION)
    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        problems.append("runs must be a non-empty list")
        return problems
    for run_idx, run in enumerate(runs):
        problems.extend(_validate_run(run, run_idx))
    return problems


def _validate_run(run, run_idx):
    where = "runs[%d]" % run_idx
    problems = []
    if not isinstance(run, dict):
        return ["%s is not an object" % where]
    tool = run.get("tool")
    driver = tool.get("driver") if isinstance(tool, dict) else None
    if not isinstance(driver, dict):
        problems.append("%s.tool.driver must be an object" % where)
    elif not isinstance(driver.get("rules"), list):
        problems.append("%s.tool.driver.rules must be a list" % where)
    results = run.get("results")
    if not isinstance(results, list):
        problems.append("%s.results must be a list" % where)
        return problems
    for res_idx, result in enumerate(results):
        problems.extend(_validate_result(result, "%s.results[%d]" % (where, res_idx)))
    return problems


def _validate_result(result, where):
    problems = []
    if not isinstance(result, dict):
        return ["%s is not an object" % where]
    if not result.get("ruleId"):
        problems.append("%s.ruleId must be non-empty" % where)
    if result.get("level") not in _LEVELS.values():
        problems.append("%s.level must be one of %s" % (where, sorted(_LEVELS.values())))
    message = result.get("message")
    if not isinstance(message, dict) or not message.get("text"):
        problems.append("%s.message.text must be non-empty" % where)
    problems.extend(_validate_locations(result.get("locations"), where))
    return problems


def _validate_locations(locations, where):
    if not isinstance(locations, list) or not locations:
        return ["%s.locations must be a non-empty list" % where]
    problems = []
    for loc_idx, location in enumerate(locations):
        loc_where = "%s.locations[%d]" % (where, loc_idx)
        if not isinstance(location, dict):
            problems.append("%s is not an object" % loc_where)
            continue
        physical = location.get("physicalLocation")
        if not isinstance(physical, dict):
            problems.append("%s.physicalLocation is required" % loc_where)
            continue
        artifact = physical.get("artifactLocation")
        if not isinstance(artifact, dict) or not artifact.get("uri"):
            problems.append("%s.artifactLocation.uri must be non-empty" % loc_where)
        region = physical.get("region")
        if not isinstance(region, dict):
            problems.append("%s.region is required" % loc_where)
            continue
        start = region.get("startLine")
        if not isinstance(start, int) or start < 1:
            problems.append("%s.region.startLine must be an integer >= 1" % loc_where)
    return problems
