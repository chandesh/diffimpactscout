def test_risk_exact_scores():
    from diffimpactscout.impact import risk
    s = risk.score(severity="High", fan_in=1.0, missing_test=0.0, churn=0.0)
    assert s == 0.4 * 1.0 + 0.3 * 1.0 + 0.2 * 0.0 + 0.1 * 0.0


def test_risk_severity_mapping():
    from diffimpactscout.impact import risk
    assert risk.score(severity="Medium", fan_in=0.0, missing_test=0.0, churn=0.0) == 0.4 * 0.5
    assert risk.score(severity="Low", fan_in=0.0, missing_test=0.0, churn=0.0) == 0.0


def test_risk_terms_clamped():
    from diffimpactscout.impact import risk
    assert risk.clamp01(2.0) == 1.0
    assert risk.clamp01(-1.0) == 0.0
    assert risk.clamp01(0.5) == 0.5
    s = risk.score(severity="High", fan_in=5.0, missing_test=9.0, churn=-3.0)
    assert s == 0.4 * 1.0 + 0.3 * 1.0 + 0.2 * 1.0 + 0.1 * 0.0


def test_risk_three_symbol_fixture():
    from diffimpactscout.impact import impact as m
    rows = [
        {"path": "c.py", "ref": "low", "severity": "Low",
         "fan_in": 1, "missing_test": 0, "churn": 0},
        {"path": "b.py", "ref": "mid", "severity": "Medium",
         "fan_in": 1, "missing_test": 1, "churn": 0},
        {"path": "a.py", "ref": "hot", "severity": "High",
         "fan_in": 3, "missing_test": 1, "churn": 0},
    ]
    ranked = m.rank_rows(rows, max_fan_in=3)
    assert ranked[0]["risk"] == 0.4 * 1.0 + 0.3 * 1.0 + 0.2 * 1.0 + 0.1 * 0.0
    assert [r["ref"] for r in ranked] == ["hot", "mid", "low"]
