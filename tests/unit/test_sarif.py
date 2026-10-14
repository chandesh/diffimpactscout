"""SARIF output for impact rows (Phase B REQ-13)."""


def test_sarif_shape():
    from diffimpactscout.impact import sarif
    doc = sarif.to_sarif([{"path": "b.py", "ref": "x", "severity": "High", "why_matters": "b uses x", "key": "b.py:3"}])
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["results"][0]["ruleId"]
    assert doc["runs"][0]["results"][0]["locations"]
