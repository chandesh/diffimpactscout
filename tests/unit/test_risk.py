def test_risk_exact_scores():
    from diffimpactscout.impact import risk
    s = risk.score(severity="High", fan_in=1.0, missing_test=0.0, churn=0.0)
    assert s == 0.4 * 1.0 + 0.3 * 1.0 + 0.2 * 0.0 + 0.1 * 0.0
