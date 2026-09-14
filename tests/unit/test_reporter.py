import re
import sys

from diffimpactscout.impact import reporter

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
    """Verifies that references spanning multiple layers are classified High."""
    assert (
        reporter.classify_severity(
            {"python", "template", "frontend"}, False, {"a.py"}, "a.py"
        )
        == "High"
    )


def test_classify_severity_cross_layer_high_even_in_changed_file():
    """Verifies that cross-layer references stay High even when in the changed file."""
    assert reporter.classify_severity({"python", "template"}, False, {"t.html"}, "t.html") == "High"


def test_classify_severity_deleted_renamed_is_high_regardless():
    """Verifies that deleted or renamed references are always classified High."""
    assert reporter.classify_severity({"python"}, True, {"a.py"}, "a.py") == "High"


def test_classify_severity_same_layer_outside_changed_medium():
    """Verifies that same-layer references outside the changed file are Medium."""
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "b.py") == "Medium"


def test_classify_severity_same_layer_inside_changed_low():
    """Verifies that same-layer references inside the changed file are Low."""
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "a.py") == "Low"


def test_classify_severity_empty_layers_outside_medium():
    """Verifies that empty layers classify outside-file references as Medium."""
    assert reporter.classify_severity(set(), False, {"a.py"}, "b.py") == "Medium"


def test_classify_severity_no_ref_path_low():
    """Verifies that a missing reference path is classified Low."""
    assert reporter.classify_severity({"python"}, False, {"a.py"}, None) == "Low"


def test_classify_severity_no_changed_paths_low():
    """Verifies that an empty changed-paths set classifies references as Low."""
    assert reporter.classify_severity({"python"}, False, [], "a.py") == "Low"


def test_classify_severity_layers_as_list_works():
    """Verifies that severity classification accepts layers as a list."""
    assert reporter.classify_severity(["python", "template"], False, {"a.py"}, "a.py") == "High"
    assert reporter.classify_severity(["python"], False, {"a.py"}, "b.py") == "Medium"


def test_render_report_header_exact():
    """Verifies that the rendered ASCII report includes the title banner."""
    out = reporter.render_report([], [], 0)
    lines = out.splitlines()
    assert lines[0] == "=" * 70
    assert "Impact Analysis Report" in lines[1]


def test_render_report_ascii_columns_aligned():
    """Verifies that ASCII rows are aligned and include the expected columns."""
    out = reporter.render_report(ROWS, [], 2)
    assert "apps/orders/views.py" in out
    assert "apps.orders" in out
    assert "OrderList" in out
    assert "templates/orders.html" in out


def test_render_report_markdown_rows_numbered_and_columns():
    """Verifies that markdown rows are numbered and display the expected columns."""
    out = reporter.render_report(ROWS, [], 2, markdown=True)
    assert "| 1 | apps/orders/views.py | apps.orders | python | OrderList | High | Review & test |" in out
    assert "| 2 | templates/orders.html | orders | template | order-list | Medium | Verify template |" in out


def test_render_report_markdown_escapes_pipe_in_cell():
    """Verifies that literal pipes in markdown cell values are escaped."""
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
    out = reporter.render_report(rows, [], 1, markdown=True)
    line = [l for l in out.splitlines() if l.startswith("| ") and l.lstrip("| ")[0].isdigit()][0]
    assert "A \\| B" in line
    cells = [p.strip() for p in re.split(r"(?<!\\)\|", line) if p.strip()]
    assert len(cells) == 7
    assert cells[4] == "A \\| B"


def test_render_report_row_numbering_is_sequential():
    """Verifies that report rows are numbered sequentially starting at one."""
    out = reporter.render_report(ROWS, [], 2, markdown=True)
    rows = [
        l
        for l in out.splitlines()
        if l.startswith("| ") and l.lstrip("| ")[0].isdigit()
    ]
    assert rows[0].startswith("| 1 |")
    assert rows[1].startswith("| 2 |")


def test_render_report_unresolved_section():
    """Verifies that unresolved references appear in a dedicated report section."""
    out = reporter.render_report([], ["/api/v1/opaque/", "no-such-route"], 1)
    assert "Unresolved references (manual check required)" in out
    assert "/api/v1/opaque/" in out
    assert "no-such-route" in out


def test_render_report_unresolved_omitted_when_empty():
    """Verifies that the unresolved section is omitted when there are none."""
    out = reporter.render_report([], [], 0)
    assert "Unresolved references" not in out


def test_render_report_summary_changed_count():
    """Verifies that the report summary shows the changed file count and severities."""
    out = reporter.render_report(ROWS, [], 3)
    assert "3 changed file(s)" in out
    assert "High: 1" in out
    assert "Medium: 1" in out
    out = reporter.render_report(ROWS, [], 3, markdown=True)
    assert "3 changed file(s)" in out
    assert "High: 1" in out


def test_render_report_no_redundant_findings_block():
    """Verifies that the report no longer emits a separate Findings block."""
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
    assert "[HIGH]" not in out
    assert "Findings" not in out
    md = reporter.render_report(rows, [], 1, markdown=True)
    assert "[HIGH]" not in md
    assert "Findings" not in md


def _fake_tty(monkeypatch, value):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: value)
    return reporter.interactive_tty()


def test_interactive_tty_reports_isatty(monkeypatch):
    """Verifies that interactive_tty reports the underlying isatty result."""
    assert _fake_tty(monkeypatch, True) is True
    assert _fake_tty(monkeypatch, False) is False


def test_should_block_skip_env_never_blocks(monkeypatch):
    """Verifies that the skip env var causes should_block to never block."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_skip_env_wins_over_strict(monkeypatch):
    """Verifies that the skip env var wins even when strict mode is set."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_diffimpactscout_skip_never_blocks(monkeypatch):
    """Verifies that the DIFFIMPACTSCOUT_SKIP env var never blocks."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_diffimpactscout_skip_non_tty_strict(monkeypatch):
    """Verifies that DIFFIMPACTSCOUT_SKIP prevents blocking on non-tty strict mode."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is False
    assert "skip" in reason.lower()


def test_should_block_non_tty_no_strict(monkeypatch):
    """Verifies that non-tty without strict mode does not block."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block(None, _fake_tty(monkeypatch, False))
    assert block is False
    assert "non-interactive" in reason


def test_should_block_non_tty_strict(monkeypatch):
    """Verifies that non-tty with strict mode blocks the change."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, False))
    assert block is True
    assert "strict" in reason


def test_should_block_tty_prompt(monkeypatch):
    """Verifies that an interactive tty triggers a prompt to block."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block(None, _fake_tty(monkeypatch, True))
    assert block is True
    assert "prompt" in reason


def test_should_block_tty_prompt_wins_over_strict(monkeypatch):
    """Verifies that the tty prompt takes precedence over strict mode."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    block, reason = reporter.should_block("1", _fake_tty(monkeypatch, True))
    assert block is True
    assert "prompt" in reason
    assert "strict" not in reason


def test_should_block_strict_falsy_values_not_strict(monkeypatch):
    """Verifies that falsy strict values are treated as not strict."""
    monkeypatch.delenv("IMPACT_CHECK_SKIP", raising=False)
    for value in ("0", "FALSE", "no", "off", ""):
        block, reason = reporter.should_block(value, _fake_tty(monkeypatch, False))
        assert block is False
        assert "non-interactive" in reason