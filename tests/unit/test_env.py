from diffimpactscout import env


def test_skip_requested_true_for_diffimpactscout_skip(monkeypatch):
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    assert env.skip_requested() is True


def test_skip_requested_true_for_impact_check_skip(monkeypatch):
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    assert env.skip_requested() is True


def test_skip_requested_empty_value_still_skips(monkeypatch):
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "")
    assert env.skip_requested() is True


def test_skip_requested_both_vars_present(monkeypatch):
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "x")
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "")
    assert env.skip_requested() is True


def test_skip_requested_not_set(monkeypatch):
    monkeypatch.delenv("DIFFIMPACTSCOUT_SKIP", raising=False)
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    assert env.skip_requested() is False


def test_strict_requested_falsy_values(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_STRICT", raising=False)
    assert env.strict_requested() is False
    for value in ("", "0", "false", "FALSE", "no", "off", " 0 "):
        monkeypatch.setenv("IMPACT_CHECK_STRICT", value)
        assert env.strict_requested() is False


def test_strict_requested_truthy_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "strict", "on", " 1 "):
        monkeypatch.setenv("IMPACT_CHECK_STRICT", value)
        assert env.strict_requested() is True


def test_pre_commit_refs_unset(monkeypatch):
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    assert env.pre_commit_refs() == (None, None)


def test_pre_commit_refs_both_set(monkeypatch):
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "abc123")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "def456")
    assert env.pre_commit_refs() == ("abc123", "def456")


def test_pre_commit_refs_one_set(monkeypatch):
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "abc123")
    assert env.pre_commit_refs() == ("abc123", None)
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "def456")
    assert env.pre_commit_refs() == (None, "def456")


def test_is_true_semantics():
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