from diffimpactscout import env


def test_skip_requested_true_for_diffimpactscout_skip(monkeypatch):
    """Verifies skip_requested honors DIFFIMPACTSCOUT_SKIP set."""
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    assert env.skip_requested() is True


def test_skip_requested_true_for_impact_check_skip(monkeypatch):
    """Checks skip_requested honors IMPACT_CHECK_SKIP set."""
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    assert env.skip_requested() is True


def test_skip_requested_empty_value_still_skips(monkeypatch):
    """Verifies that an empty skip variable still triggers a skip."""
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "")
    assert env.skip_requested() is True


def test_skip_requested_both_vars_present(monkeypatch):
    """Checks skip_requested returns true when either skip var is present."""
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "x")
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "")
    assert env.skip_requested() is True


def test_skip_requested_not_set(monkeypatch):
    """Verifies skip_requested is false when neither skip var is set."""
    monkeypatch.delenv("DIFFIMPACTSCOUT_SKIP", raising=False)
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    assert env.skip_requested() is False


def test_strict_requested_falsy_values(monkeypatch):
    """Checks strict_requested is false for falsy IMPACT_CHECK_STRICT values."""
    monkeypatch.delenv("IMPACT_CHECK_STRICT", raising=False)
    assert env.strict_requested() is False
    for value in ("", "0", "false", "FALSE", "no", "off", " 0 "):
        monkeypatch.setenv("IMPACT_CHECK_STRICT", value)
        assert env.strict_requested() is False


def test_strict_requested_truthy_values(monkeypatch):
    """Verifies strict_requested is true for truthy IMPACT_CHECK_STRICT values."""
    for value in ("1", "true", "TRUE", "yes", "strict", "on", " 1 "):
        monkeypatch.setenv("IMPACT_CHECK_STRICT", value)
        assert env.strict_requested() is True


def test_pre_commit_refs_unset(monkeypatch):
    """Checks pre_commit_refs returns (None, None) when unset."""
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    assert env.pre_commit_refs() == (None, None)


def test_pre_commit_refs_both_set(monkeypatch):
    """Verifies pre_commit_refs returns both refs when set."""
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "abc123")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "def456")
    assert env.pre_commit_refs() == ("abc123", "def456")


def test_pre_commit_refs_one_set(monkeypatch):
    """Checks pre_commit_refs handles a single ref being set."""
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "abc123")
    assert env.pre_commit_refs() == ("abc123", None)
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "def456")
    assert env.pre_commit_refs() == (None, "def456")


def test_is_true_semantics():
    """Verifies is_true maps various values to booleans correctly."""
    assert env.is_true(None) is False
    assert env.is_true(0) is False
    assert env.is_true(1) is True
    assert env.is_true("0") is False
    assert env.is_true("false") is False
    assert env.is_true("no") is False
    assert env.is_true("off") is False
    assert env.is_true("") is False
    assert env.is_true("true") is True
    assert env.is_true("YES") is True
    assert env.is_true("strict") is True