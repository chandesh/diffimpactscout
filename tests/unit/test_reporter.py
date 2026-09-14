import re
import sys

from diffimpactscout.impact import reporter

HEADER = "| # | Impacted File Path | Module / Subsystem | Category | Detected Reference / Usage | Severity | Action Required |"

ROWS = [
    {
        "path": "apps/orders/views.py",
        "module": "apps.orders",
        "category": "python",
        "ref": "OrderList",
        "severity": "High",
        "action": "Review & test",
    },
    {
        "path": "templates/orders.html",
        "module": "orders",
        "category": "template",
        "ref": "order-list",
        "severity": "Medium",
        "action": "Verify template",
    },
]


def test_classify_severity_cross_layer_is_high():
    assert (
        reporter.classify_severity(
            {"python", "template", "frontend"}, False, {"a.py"}, "a.py"
        )
        == "High"
    )


def test_classify_severity_cross_layer_high_even_in_changed_file():
    assert reporter.classify_severity({"python", "template"}, False, {"t.html"}, "t.html") == "High"


def test_classify_severity_deleted_renamed_is_high_regardless():
    assert reporter.classify_severity({"python"}, True, {"a.py"}, "a.py") == "High"


def test_classify_severity_same_layer_outside_changed_medium():
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "b.py") == "Medium"


def test_classify_severity_same_layer_inside_changed_low():
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "a.py") == "Low"


def test_classify_severity_empty_layers_outside_medium():
    assert reporter.classify_severity(set(), False, {"a.py"}, "b.py") == "Medium"


def test_classify_severity_no_ref_path_low():
    assert reporter.classify_severity({"python"}, False, {"a.py"}, None) == "Low"


def test_classify_severity_no_changed_paths_low():
    assert reporter.classify_severity({"python"}, False, [], "a.py") == "Low"


def test_classify_severity_layers_as_list_works():
    assert reporter.classify_severity(["python", "template"], False, {"a.py"}, "a.py") == "High"
    assert reporter.classify_severity(["python"], False, {"a.py"}, "b.py") == "Medium"


def test_render_report_header_exact():
    out = reporter.render_report([], [], 0)
    lines = out.splitlines()
    assert lines[2] == HEADER
    assert lines[3] == "| --- | --- | --- | --- | --- | --- | --- |"


def test_render_report_separator_present():
    out = reporter.render_report([], [], 0)
    assert "| --- |" in out


def test_render_report_rows_numbered_and_columns():
    out = reporter.render_report(ROWS, [], 2)
    assert "| 1 | apps/orders/views.py | apps.orders | python | OrderList | High | Review & test |" in out
    assert "| 2 | templates/orders.html | orders | template | order-list | Medium | Verify template |" in out


def test_render_report_escapes_pipe_in_cell():
    rows = [
        {
            "path": "apps/orders/views.py",
            "module": "apps.orders",
            "category": "python",
            "ref": "A | B",
            "severity": "Low",
            "action": "Review",
        }
    ]
    out = reporter.render_report(rows, [], 1)
    body = out.split(HEADER, 1)[1].splitlines()
    line = [l for l in body if l.startswith("|") and "---" not in l][0]
    assert "A \\| B" in line
    cells = [p.strip() for p in re.split(r"(?<!\\)\|", line) if p.strip()]
    assert len(cells) == 7
    assert cells[4] == "A \\| B"


def test_render_report_row_numbering_is_sequential():
    out = reporter.render_report(ROWS, [], 2)
    body = out.split(HEADER, 1)[1]
    rows = [
        l
        for l in body.splitlines()
        if l.startswith("|") and "---" not in l
    ]
    assert rows[0].startswith("| 1 |")
    assert rows[1].startswith("| 2 |")


def test_render_report_unresolved_section():
    out = reporter.render_report([], ["/api/v1/opaque/", "no-such-route"], 1)
    assert "Unresolved references (manual check required)" in out
    assert "- /api/v1/opaque/" in out
    assert "- no-such-route" in out


def test_render_report_unresolved_omitted_when_empty():
    out = reporter.render_report([], [], 0)
    assert "Unresolved references" not in out


def test_render_report_summary_changed_count():
    out = reporter.render_report(ROWS, [], 3)
    assert "3 changed file(s)" in out
    assert "High: 1" in out
    assert "Medium: 1" in out


def test_render_report_findings_with_severity_tags():
    rows = [
        {
            "path": "app/views/order.py",
            "module": "app/views",
            "category": "template",
            "ref": "{% url 'order-detail' %} at app/templates/orders.html:14",
            "severity": "High",
            "action": "review/verify",
        }
    ]
    out = reporter.render_report(rows, [], 1)
    assert "[HIGH] app/views/order.py -> {% url 'order-detail' %} at app/templates/orders.html:14" in out
    assert "Category: template" in out
    assert "review/verify" in out
    assert "Summary: High: 1" in out


def test_render_report_findings_all_severity_bands():
    rows = [
        {"path": "a.py", "module": "m", "category": "python",
         "ref": "x", "severity": "High", "action": "review/verify"},
        {"path": "b.py", "module": "m", "category": "python",
         "ref": "y", "severity": "Medium", "action": "verify"},
        {"path": "c.py", "module": "m", "category": "python",
         "ref": "z", "severity": "Low", "action": "ok"},
    ]
    out = reporter.render_report(rows, [], 3)
    assert "[HIGH] a.py -> x" in out
    assert "[MEDIUM] b.py -> y" in out
    assert "[LOW] c.py -> z" in out


def _fake_tty(monkeypatch, value):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: value)
    return reporter.interactive_tty()


def test_interactive_tty_reports_isatty(monkeypatch):
    assert _fake_tty(monkeypatch, True) is True
    assert _fake_tty(monkeypatch, False) is False


def test_should_block_skip_env_never_blocks(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_skip_env_wins_over_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_diffimpactscout_skip_never_blocks(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_diffimpactscout_skip_non_tty_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_non_tty_no_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block(None, _fake_tty(monkeypatch, False))
    assert block is False
    assert "non-interactive" in reason


def test_should_block_non_tty_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is True
    assert "strict" in reason


def test_should_block_tty_prompt(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block(None, _fake_tty(monkeypatch, True))
    assert block is True
    assert "prompt" in reason


def test_should_block_tty_prompt_wins_over_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is True
    assert "prompt" in reason
    assert "strict" not in reason


def test_should_block_strict_falsy_values_not_strict(monkeypatch):
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    for value in ("0", "FALSE", "no", "off", ""):
        block, reason = reporter.should_block(value, _fake_tty(monkeypatch, False))
        assert block is False
        assert "non-interactive" in reason