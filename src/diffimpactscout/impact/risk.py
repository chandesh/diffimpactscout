"""Risk ranking: risk = 0.4*severity + 0.3*fan_in + 0.2*missing_test + 0.1*churn.

All terms are 0-1. severity maps High=1.0, Medium=0.5, Low=0.0.
fan_in = distinct referencing files for the symbol, normalized by max fan_in.
missing_test = 1 when no conventional tests/test_<stem>.py exists for the
impacted file, else 0. This is an existence proxy for "no test imports the
module": the caller does not scan test imports today (the analyses cache
holds per-file imports but _compose_rows does not consult them), so a
coincidentally-named test file scores 0 and an untested module without one
scores 1. Follow-up: scan collected test files for imports of the
referenced module. Non-python rows always score 0 (no python test
convention applies).
churn = hunk-line overlap for the entity, normalized 0-1.
"""

W_SEVERITY = 0.4
W_FAN_IN = 0.3
W_MISSING_TEST = 0.2
W_CHURN = 0.1

_SEVERITY_MAP = {
    "High": 1.0,
    "Medium": 0.5,
    "Low": 0.0,
}


def clamp01(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def score(severity, fan_in, missing_test, churn):
    try:
        sev = _SEVERITY_MAP[severity]
    except KeyError:
        raise ValueError("unknown severity: %r" % (severity,))
    return (
        W_SEVERITY * sev
        + W_FAN_IN * clamp01(fan_in)
        + W_MISSING_TEST * clamp01(missing_test)
        + W_CHURN * clamp01(churn)
    )
