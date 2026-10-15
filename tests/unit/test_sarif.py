"""SARIF output and validation for impact rows."""

import pytest


def _row(**overrides):
    row = {
        "path": "b.py",
        "ref": "x",
        "severity": "High",
        "why_matters": "b uses x",
        "key": "b.py:3",
    }
    row.update(overrides)
    return row


def test_sarif_shape():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([_row()])
    assert doc["version"] == "2.1.0"
    result = doc["runs"][0]["results"][0]
    assert result["ruleId"] == "diffimpactscout/high"
    assert result["level"] == "error"
    assert result["message"] == {"text": "b uses x"}
    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "b.py"
    assert physical["region"]["startLine"] == 3


@pytest.mark.parametrize("severity,level,rule", [
    ("High", "error", "diffimpactscout/high"),
    ("Medium", "warning", "diffimpactscout/medium"),
    ("Low", "note", "diffimpactscout/low"),
])
def test_sarif_severity_levels(severity, level, rule):
    from diffimpactscout.impact import sarif
    result = sarif.to_sarif([_row(severity=severity)])["runs"][0]["results"][0]
    assert result["level"] == level
    assert result["ruleId"] == rule


def test_sarif_rules_carry_name_and_description():
    from diffimpactscout.impact import sarif
    rules = sarif.to_sarif([_row()])["runs"][0]["tool"]["driver"]["rules"]
    assert rules == [{
        "id": "diffimpactscout/high",
        "name": "diffimpactscout/high",
        "shortDescription": {"text": "impact severity: error"},
    }]


def test_sarif_edge_fallbacks():
    from diffimpactscout.impact import sarif
    # Empty why_matters/ref falls back to key for the message; unknown
    # severity normalizes to Low; empty path falls back to key for the uri.
    doc = sarif.to_sarif([{
        "path": "", "ref": "", "severity": "Weird", "key": "k.py:9",
    }])
    result = doc["runs"][0]["results"][0]
    assert result["level"] == "note"
    assert result["ruleId"] == "diffimpactscout/low"
    assert result["message"] == {"text": "k.py:9"}
    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "k.py:9"


def test_sarif_validate_accepts_valid_document():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([_row(), _row(severity="Medium"), _row(severity="Low")])
    assert sarif.validate(doc) == []


def test_sarif_validate_rejects_bad_version():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([_row()])
    doc["version"] = "2.0.0"
    assert any("version" in problem for problem in sarif.validate(doc))


def test_sarif_validate_rejects_missing_rule_id():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([_row()])
    doc["runs"][0]["results"][0]["ruleId"] = ""
    assert any("ruleId" in problem for problem in sarif.validate(doc))


def test_sarif_validate_rejects_bad_start_line():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([_row()])
    region = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
    region["startLine"] = 0
    assert any("startLine" in problem for problem in sarif.validate(doc))


def test_sarif_validate_rejects_empty_runs():
    from diffimpactscout.impact import sarif
    assert sarif.validate({"version": "2.1.0", "runs": []})


def test_sarif_validate_rejects_non_object():
    from diffimpactscout.impact import sarif
    assert sarif.validate([])
