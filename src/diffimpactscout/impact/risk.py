"""Risk ranking: risk = 0.4*severity + 0.3*fan_in + 0.2*missing_test + 0.1*churn.

All terms are 0-1. severity maps High=1.0, Medium=0.5, Low=0.0.
fan_in = distinct referencing files for the symbol, normalized by max fan_in.
missing_test = 1 when no test file imports the referenced module, else 0.
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


def score(severity, fan_in, missing_test, churn):
    sev = _SEVERITY_MAP[severity]
    return W_SEVERITY * sev + W_FAN_IN * fan_in + W_MISSING_TEST * missing_test + W_CHURN * churn
