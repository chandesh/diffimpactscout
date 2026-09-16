import diffimpactscout.dependency_check as dependency_check


def _cfg(checks, profile="python"):
    return {
        "mode": "guard-and-impact-check",
        "guard": {"checks": checks, "blocking": "warn"},
        "impact": {"profile": profile},
    }


def _no_resolve(monkeypatch):
    monkeypatch.setattr(dependency_check, "_resolve", lambda *a: None)


def test_required_tools_maps_ruff_check_ids():
    """Checks that ruff and ruff-format both map to the ruff tool."""
    cfg = _cfg([{"id": "ruff"}, {"id": "ruff-format"}])
    assert dependency_check.required_tools(cfg) == ["ruff"]


def test_required_tools_maps_eslint_and_prettier():
    """Verifies that eslint and prettier check ids map to their tools."""
    cfg = _cfg([{"id": "eslint"}, {"id": "prettier"}])
    assert dependency_check.required_tools(cfg) == ["eslint", "prettier"]


def test_required_tools_accepts_plain_string_entries():
    """Verifies that string-form check entries are recognized."""
    cfg = _cfg(["ruff"])
    assert dependency_check.required_tools(cfg) == ["ruff"]


def test_required_tools_empty_for_unreferenced_tools():
    """Checks that profiles without external tools report no requirements."""
    cfg = _cfg([{"id": "syntax/json-syntax"}], profile="generic")
    assert dependency_check.required_tools(cfg) == []


def test_check_tools_found(monkeypatch):
    """Verifies that check_tools reports an installed binary as found."""
    monkeypatch.setattr(dependency_check, "_resolve", lambda *a: "/usr/bin/ruff")
    assert dependency_check.check_tools(_cfg([{"id": "ruff"}])) == [
        {"tool": "ruff", "binary": "ruff", "found": True, "hint": "pip install ruff"}
    ]


def test_check_tools_missing(monkeypatch):
    """Checks that check_tools flags an unavailable binary as missing."""
    _no_resolve(monkeypatch)
    results = dependency_check.check_tools(_cfg([{"id": "ruff"}]))
    assert results[0]["found"] is False


def test_run_dependency_check_no_tools(capsys, monkeypatch):
    """Verifies that a config without external tools exits zero."""
    _no_resolve(monkeypatch)
    assert dependency_check.run_dependency_check("/repo", _cfg([], "generic")) == 0
    assert "No external tools" in capsys.readouterr().out


def test_run_dependency_check_missing_returns_one(capsys, monkeypatch):
    """Checks that a missing referenced tool exits one with an install hint."""
    _no_resolve(monkeypatch)
    assert dependency_check.run_dependency_check("/repo", _cfg([{"id": "ruff"}])) == 1
    captured = capsys.readouterr()
    assert "[MISSING] ruff" in captured.out
    assert "pip install ruff" in captured.err


def test_run_dependency_check_all_found_returns_zero(capsys, monkeypatch):
    """Verifies that all-present tools exit zero with an OK banner."""
    monkeypatch.setattr(dependency_check, "_resolve", lambda *a: "/usr/bin/ruff")
    assert dependency_check.run_dependency_check("/repo", _cfg([{"id": "ruff"}])) == 0
    captured = capsys.readouterr()
    assert "[OK] ruff" in captured.out
    assert "All referenced tools are available." in captured.out


def test_report_missing_tty_prints(capsys, monkeypatch):
    """Checks that report_missing warns about missing tools on a tty."""
    _no_resolve(monkeypatch)
    dependency_check.report_missing(_cfg([{"id": "ruff"}]), tty=True)
    assert "ruff was not found" in capsys.readouterr().err


def test_report_missing_non_tty_silent(capsys, monkeypatch):
    """Verifies that report_missing stays silent when not on a tty."""
    _no_resolve(monkeypatch)
    dependency_check.report_missing(_cfg([{"id": "ruff"}]), tty=False)
    assert capsys.readouterr().err == ""