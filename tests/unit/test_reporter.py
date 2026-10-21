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


def test_split_token_keeps_slash_on_wrapped_path_lines():
    """Verifies wrapped path lines keep a trailing slash so the path reads continuous."""
    token = "apps/orders/cart/checkout/views.py"
    lines = reporter._split_token(token, 18)
    assert lines == ["apps/orders/cart/", "checkout/views.py"]
    assert all(len(l) <= 18 for l in lines)
    assert all(l.endswith("/") for l in lines[:-1])


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


def test_severity_reason_deleted_renamed_is_high():
    """Verifies that deleted or renamed symbols carry a deleted/renamed reason."""
    reason = reporter.severity_reason({"python"}, True, {"a.py"}, "a.py")
    assert "deleted or renamed" in reason
    assert reporter.classify_severity({"python"}, True, {"a.py"}, "a.py") == "High"


def test_severity_reason_multiple_layers_lists_layers():
    """Verifies that multi-layer reasons list the layers involved."""
    reason = reporter.severity_reason({"python", "template"}, False, {"a.py"}, "a.py")
    assert "multiple layers" in reason
    assert "python" in reason
    assert "template" in reason
    assert reporter.classify_severity({"python", "template"}, False, {"a.py"}, "a.py") == "High"


def test_severity_reason_reference_inside_changed_file():
    """Verifies that in-file references carry an inside-the-change-set reason."""
    reason = reporter.severity_reason({"python"}, False, {"a.py"}, "a.py")
    assert "inside the changed file" in reason
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "a.py") == "Low"


def test_severity_reason_no_external_reference():
    """Verifies that missing reference paths or empty change-sets carry a no-external reason."""
    reason = reporter.severity_reason({"python"}, False, {"a.py"}, None)
    assert "external reference" in reason
    assert reporter.classify_severity({"python"}, False, {"a.py"}, None) == "Low"
    reason = reporter.severity_reason({"python"}, False, [], "a.py")
    assert "external reference" in reason
    assert reporter.classify_severity({"python"}, False, [], "a.py") == "Low"


def test_severity_reason_outside_changed_file():
    """Verifies that outside-file references carry an outside-the-change-set reason."""
    reason = reporter.severity_reason({"python"}, False, {"a.py"}, "b.py")
    assert "outside the change-set" in reason
    assert reporter.classify_severity({"python"}, False, {"a.py"}, "b.py") == "Medium"


def test_severity_reason_layers_as_list_works():
    """Verifies that severity reasons accept layers as a list."""
    reason = reporter.severity_reason(["python", "template"], False, {"a.py"}, "a.py")
    assert "multiple layers" in reason
    assert reporter.severity_reason(["python"], False, {"a.py"}, "b.py") == (
        "referenced from a file outside the change-set"
    )


def _rows_with_reason():
    return [
        {
            "path": "apps/orders/views.py",
            "module": "apps.orders",
            "category": "python",
            "ref": "OrderList (import at 6)",
            "severity": "High",
            "action": "verify dangling references",
            "reason": "symbol was deleted or renamed in this change-set",
        },
        {
            "path": "templates/orders.html",
            "module": "orders",
            "category": "template",
            "ref": "order-list",
            "severity": "Medium",
            "action": "Verify template",
            "reason": "referenced from a file outside the change-set",
        },
    ]


def test_render_report_ascii_severity_rationale_section():
    """Verifies that the ASCII report lists a per-row severity rationale."""
    out = reporter.render_report(_rows_with_reason(), [], [], 2)
    assert "Severity rationale" in out
    assert "1. High - apps/orders/views.py -> OrderList" in out
    assert "symbol was deleted or renamed in this change-set" in out
    assert "2. Medium - templates/orders.html -> order-list" in out
    assert "referenced from a file outside the change-set" in out


def test_render_report_markdown_severity_rationale_section():
    """Verifies that the markdown report includes a severity rationale section."""
    out = reporter.render_report(_rows_with_reason(), [], [], 2, markdown=True)
    assert "## Severity rationale" in out
    assert "1. High - apps/orders/views.py -> OrderList" in out
    assert "symbol was deleted or renamed in this change-set" in out
    assert "2. Medium - templates/orders.html -> order-list" in out


def test_render_report_rationale_omitted_without_reason():
    """Verifies that the rationale section is omitted when rows carry no reason."""
    out = reporter.render_report(ROWS, [], [], 2)
    assert "Severity rationale" not in out
    md = reporter.render_report(ROWS, [], [], 2, markdown=True)
    assert "Severity rationale" not in md


def test_render_report_header_exact():
    """Verifies that the rendered ASCII report includes the title banner."""
    out = reporter.render_report([], [], [], 0)
    lines = out.splitlines()
    assert lines[0] == "=" * 70
    assert "Impact Analysis Report" in lines[1]


def test_render_report_ascii_columns_aligned():
    """Verifies that ASCII rows are aligned and include the expected columns."""
    out = reporter.render_report(ROWS, [], [], 2)
    assert "apps/orders/views.py" in out
    assert "apps.orders" in out
    assert "OrderList" in out
    assert "templates/orders.html" in out


def test_render_report_markdown_rows_numbered_and_columns():
    """Verifies that markdown rows are numbered and display the expected columns."""
    out = reporter.render_report(ROWS, [], [], 2, markdown=True)
    assert "| 1 | apps/orders/views.py | apps.orders | python | OrderList | High | Review & test |  |" in out
    assert "| 2 | templates/orders.html | orders | template | order-list | Medium | Verify template |  |" in out


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
            "badge": "Impacted",
        }
    ]
    out = reporter.render_report(rows, [], [], 1, markdown=True)
    line = [l for l in out.splitlines() if l.startswith("| ") and l.lstrip("| ")[0].isdigit()][0]
    assert "A \\| B" in line
    cells = [p.strip() for p in re.split(r"(?<!\\)\|", line) if p.strip()]
    assert len(cells) == 8
    assert cells[4] == "A \\| B"


def test_render_report_row_numbering_is_sequential():
    """Verifies that report rows are numbered sequentially starting at one."""
    out = reporter.render_report(ROWS, [], [], 2, markdown=True)
    rows = [
        l
        for l in out.splitlines()
        if l.startswith("| ") and l.lstrip("| ")[0].isdigit()
    ]
    assert rows[0].startswith("| 1 |")
    assert rows[1].startswith("| 2 |")


def test_render_report_unresolved_section():
    """Verifies that unresolved references appear in a dedicated report section."""
    out = reporter.render_report([], [], ["/api/v1/opaque/", "no-such-route"], 1)
    assert "Unresolved references (manual check required)" in out
    assert "/api/v1/opaque/" in out
    assert "no-such-route" in out


def test_render_report_unresolved_dict_entry():
    """Verifies that reverse-chain unresolved dicts render readably."""
    out = reporter.render_report(
        [], [],
        [{"file": "shop/templates/x.html", "reason": "dynamic include; manual check required"}],
        1,
    )
    assert "shop/templates/x.html (dynamic include; manual check required)" in out
    out_md = reporter.render_report(
        [], [], [{"file": "a.ts", "ref": "/x/", "reason": "no matching route"}], 1, markdown=True
    )
    assert "a.ts: /x/ (no matching route)" in out_md


def test_render_report_unresolved_omitted_when_empty():
    """Verifies that the unresolved section is omitted when there are none."""
    out = reporter.render_report([], [], [], 0)
    assert "Unresolved references" not in out


def test_unresolved_audit_line():
    """Verifies the report ends with an unresolved count broken down by cause."""
    unresolved = [
        {"file": "a.html", "reason": "dynamic-ref"},
        {"file": "b.ts", "reason": "unknown-url"},
        {"file": "c.py", "reason": "no-import-chain"},
        {"file": "d.bin", "reason": "binary/unsupported"},
    ]
    out = reporter.render_report([], [], unresolved, 1)
    assert "Unresolved: 4 (" in out
    assert "dynamic-ref" in out and "unknown-url" in out
    assert "no-import-chain" in out and "binary/unsupported" in out
    assert reporter.unresolved_reason_counts(unresolved) == {
        "dynamic-ref": 1,
        "unknown-url": 1,
        "no-import-chain": 1,
        "binary/unsupported": 1,
    }


def test_unresolved_audit_line_markdown():
    """Verifies the Markdown report carries the same audit line."""
    out = reporter.render_report(
        [], [], [{"file": "a.ts", "reason": "unknown-url"}], 1, markdown=True
    )
    assert "Unresolved: 1 (unknown-url: 1)" in out


def test_unresolved_audit_line_zero():
    """Verifies the audit line is present with a zero count when there are none."""
    out = reporter.render_report([], [], [], 0)
    assert "Unresolved: 0" in out


def test_unresolved_legacy_string_counts_as_unknown_url():
    """Verifies legacy string refs (no reason) count under unknown-url."""
    counts = reporter.unresolved_reason_counts(["/api/v1/opaque/", "no-such-route"])
    assert counts == {"unknown-url": 2}


def test_reason_category_maps_legacy_reasons():
    """Verifies free-text reasons map into the canonical buckets by kind."""
    assert reporter.reason_category("dynamic include; manual check required") == "dynamic-ref"
    assert reporter.reason_category("no matching route") == "unknown-url"
    assert reporter.reason_category("no template references this asset") == "no-import-chain"
    assert reporter.reason_category("") == "unknown-url"


def test_render_report_summary_changed_count():
    """Verifies that the report summary shows the changed file count and severities."""
    out = reporter.render_report(ROWS, [], [], 3)
    assert "3 changed file(s)" in out
    assert "High: 1" in out
    assert "Medium: 1" in out
    out = reporter.render_report(ROWS, [], [], 3, markdown=True)
    assert "3 changed file(s)" in out
    assert "High: 1" in out


def test_render_report_no_redundant_findings_block():
    """Verifies that the report no longer emits a separate Findings block."""
    rows = [
        {
            "path": "app/views/order.py",
            "module": "app/views",
            "category": "template",
            "ref": "{% url 'order-detail' %} at 14",
            "severity": "High",
            "action": "review/verify",
        }
    ]
    out = reporter.render_report(rows, [], [], 1)
    assert "[HIGH]" not in out
    assert "Findings" not in out
    md = reporter.render_report(rows, [], [], 1, markdown=True)
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


def test_render_report_tier_sections_and_hidden_footer():
    rows = [
        {"path": "app/templates/base.html", "module": "app", "category": "template",
         "ref": "changed - 10 templates directly extend it", "severity": "High",
         "action": "review/verify", "tier": "frontend", "related": ["app/templates/a.html"]},
        {"path": "app/views.py", "module": "app", "category": "python",
         "ref": "renders 'base.html' (line 4)", "severity": "High",
         "action": "review/verify", "tier": "backend"},
    ]
    out = reporter.render_report(rows, [], [], 1, hidden_count=42)
    assert "Frontend impacts" in out
    assert "Backend impacts" in out
    assert "42 lower-confidence impacts hidden" in out


def test_render_report_expand_lists_related():
    rows = [
        {"path": "app/templates/base.html", "module": "app", "category": "template",
         "ref": "changed - 2 templates directly extend it", "severity": "High",
         "action": "review/verify", "tier": "frontend",
         "related": ["app/templates/a.html", "app/templates/b.html"]},
    ]
    out = reporter.render_report(rows, [], [], 1, expand=True)
    assert "app/templates/a.html" in out
    assert "app/templates/b.html" in out
    md = reporter.render_report(rows, [], [], 1, markdown=True, expand=True)
    assert "<details>" in md


def test_summary_block_computed_path():
    from diffimpactscout.impact import reporter
    changed_paths = {"src/changed_a.py", "src/changed_b.py"}

    def _row(path, severity, owner):
        return {
            "path": path, "module": "m", "category": "python",
            "ref": "sym (name at 1)", "severity": severity,
            "action": "review/verify", "owner": owner,
        }

    rows = []
    for i in range(8):
        rows.append(_row("imp/high_%02d.py" % i, "High", "team-h%d" % i))
    for i in range(20):
        rows.append(_row("imp/med_%02d.py" % i, "Medium", "team-m%d" % i))
    for i in range(12):
        rows.append(_row("imp/low_%02d.py" % i, "Low", "team-l%d" % i))
    # Rows on changed files: counted in severity totals but excluded
    # from impacted count and Top-5.
    rows.append(_row("src/changed_a.py", "High", "team-c"))
    rows.append(_row("src/changed_a.py", "High", "team-c"))
    for i in range(3):
        rows.append(_row("src/changed_b.py", "Medium", "team-c"))
    for i in range(5):
        rows.append(_row("src/changed_a.py", "Low", "team-c"))
    assert len(rows) == 50

    expected_risk = reporter.compute_summary_risk_score(rows)
    assert expected_risk == 43  # 100*(10 + 23*0.5)/50
    out = reporter.render_report(rows, [], [], 2, changed_paths=changed_paths)
    head = "\n".join(out.splitlines()[:25])
    assert "Risk: 43/100" in head
    assert "High: 10, Medium: 23, Low: 17" in head
    assert "changed 2 files vs impacted 40 files" in head
    for i in range(5):
        assert "imp/high_%02d.py (team-h%d)" % (i, i) in head
    # Changed files must not appear in the Top-5 lines themselves.
    top_lines = [l for l in out.splitlines()[:25] if l.strip().startswith("- ")]
    assert len(top_lines) == 5
    assert not any("src/changed_" in l for l in top_lines)


def test_summary_block_first_25_lines():
    from diffimpactscout.impact import reporter
    rows = [
        {"path": "b.py", "module": "b", "category": "python",
         "ref": "x (name at 1)", "severity": "High", "action": "review/verify",
         "section": "Breaking", "badge": "Impacted", "owner": "team-a"},
    ]
    out = reporter.render_report(rows, [], [], 2, summary_extra={
        "risk": 72, "impacted_count": 1,
        "top": [("b.py", "team-a")],
    })
    head = "\n".join(out.splitlines()[:25])
    assert "Risk:" in head
    assert "High:" in head and "Medium:" in head and "Low:" in head
    assert "changed" in head and "impacted" in head
    assert "b.py (team-a)" in head


def test_summary_extra_zero_overrides_respected():
    """Explicit 0/[] overrides must not be clobbered by or-fallbacks."""
    rows = [
        {"path": "b.py", "module": "b", "category": "python",
         "ref": "x (name at 1)", "severity": "High", "action": "review/verify",
         "owner": "team-a"},
    ]
    out = reporter.render_report(rows, [], [], 1, summary_extra={
        "risk": 0, "impacted_count": 0, "top": [],
    })
    assert "Risk: 0/100" in out
    assert "impacted 0 files" in out
    top_lines = [l for l in out.splitlines() if l.strip().startswith("- ")]
    assert top_lines == []


def test_summary_extra_none_falls_back_to_computed():
    """Explicit None in summary_extra falls back to computed values."""
    rows = [
        {"path": "b.py", "module": "b", "category": "python",
         "ref": "x (name at 1)", "severity": "High", "action": "review/verify",
         "owner": "team-a"},
    ]
    expected_risk = reporter.compute_summary_risk_score(rows)
    assert expected_risk == 100
    out = reporter.render_report(rows, [], [], 1, summary_extra={
        "risk": None, "impacted_count": None, "top": None,
    })
    assert ("Risk: %d/100" % expected_risk) in out
    assert "impacted 1 files" in out
    assert "b.py (team-a)" in out


def test_markdown_summary_block_before_rows():
    """Markdown runs also print the summary block before any row output."""
    rows = [
        {"path": "b.py", "module": "b", "category": "python",
         "ref": "x (name at 1)", "severity": "High", "action": "review/verify",
         "section": "Breaking", "badge": "Impacted", "owner": "team-a"},
    ]
    out = reporter.render_report(rows, [], [], 2, markdown=True, summary_extra={
        "risk": 72, "impacted_count": 1,
        "top": [("b.py", "team-a")],
    })
    assert "Risk: 72/100" in out
    assert "High: 1, Medium: 0, Low: 0" in out
    assert "changed 2 files vs impacted 1 files" in out
    assert "b.py (team-a)" in out
    assert out.index("Risk: 72/100") < out.index("| 1 |")


def test_markdown_summary_extra_zero_overrides_respected():
    """Explicit 0/[] markdown overrides are honored, same as ASCII."""
    rows = [
        {"path": "b.py", "module": "b", "category": "python",
         "ref": "x (name at 1)", "severity": "High", "action": "review/verify",
         "owner": "team-a"},
    ]
    out = reporter.render_report(rows, [], [], 1, markdown=True, summary_extra={
        "risk": 0, "impacted_count": 0, "top": [],
    })
    assert "Risk: 0/100" in out
    assert "impacted 0 files" in out
    assert "b.py (team-a)" not in out


def test_top_n_limits_rendered_lines():
    """Body cap stays decoupled from the summary Top-5 header."""
    def _row(path, severity):
        return {"path": path, "module": "m", "category": "python",
                "ref": "x", "severity": severity, "action": "a", "owner": "t"}

    rows = [_row("f%d.py" % i, "High") for i in range(5)]
    out1 = reporter.render_report(rows, [], [], 1, top_n=1)
    top1 = [l for l in out1.splitlines() if l.strip().startswith("- ")]
    assert len(top1) == 5
    assert out1.count("... +") >= 1
    out0 = reporter.render_report(rows, [], [], 1, top_n=0)
    top0 = [l for l in out0.splitlines() if l.strip().startswith("- ")]
    assert len(top0) == 5
    out_big = reporter.render_report(rows, [], [], 1, top_n=20)
    top_big = [l for l in out_big.splitlines() if l.strip().startswith("- ")]
    assert len(top_big) == 5
    out_none = reporter.render_report(rows, [], [], 1, top_n=None)
    top_none = [l for l in out_none.splitlines() if l.strip().startswith("- ")]
    assert len(top_none) == 5


def test_top_impacted_paths_dedupes_duplicate_paths():
    """Duplicate rows for one path yield it once in Top-5."""
    def _row(ref):
        return {"path": "dup.py", "module": "m", "category": "python",
                "ref": ref, "severity": "High", "action": "a", "owner": "t"}

    rows = [_row("x"), _row("y")]
    assert reporter._impacted_count(rows, None) == 1
    assert reporter._top_impacted_paths(rows, 5, None) == [("dup.py", "t")]


def test_ascii_never_truncates_path():
    from diffimpactscout.impact import reporter
    long_path = "apps/" + "verylongdirname/" * 8 + "views.py"
    rows = [{"path": long_path, "module": "m", "category": "python",
             "ref": "x (name at 101)", "severity": "Medium", "action": "verify",
             "section": "Direct", "badge": "Impacted", "key": long_path + ":101"}]
    out = reporter.render_report(rows, [], [], 1)
    assert long_path in out.replace("\n", "")
    assert (long_path + ":101").replace("\n", "") in out.replace("\n", "")


def test_ascii_row_keys_strict_parser_zero_broken():
    """Strict parser rejoins every logical row via unbroken key tokens.

    120-row fixture with long wrapped paths (path column width 38 simulates
    a narrow 80-col terminal): each row's stable ``path:line`` key must
    appear as an unbroken whitespace-delimited token, and exactly one
    physical line per logical row may end with a known key (0 broken rows).
    """
    from diffimpactscout.impact import reporter
    sections = ("Breaking", "Direct", "Transitive")
    severities = ("High", "Medium", "Low")
    rows = []
    expected = set()
    for i in range(120):
        if i % 3 == 0:
            path = "apps/" + "verylongdirname/" * 6 + "mod%03d/views.py" % i
        else:
            path = "apps/service%03d/views.py" % i
        line = 10 + i
        key = "%s:%d" % (path, line)
        expected.add(key)
        rows.append({"path": path, "module": "apps.service%03d" % i,
                     "category": "python",
                     "ref": "sym%d (name at %d)" % (i, line),
                     "severity": severities[i % 3], "action": "verify",
                     "section": sections[i % 3], "badge": "Impacted",
                     "key": key})
    out = reporter.render_report(rows, [], [], len(rows), top_n=200)
    # Fixture must actually wrap: long paths span more physical lines than rows.
    assert sum(1 for l in out.splitlines() if "verylongdirname" in l) > 40
    tokens = set()
    for phys in out.splitlines():
        tokens.update(phys.split())
    missing = sorted(k for k in expected if k not in tokens)
    assert missing == []
    starts = sum(1 for phys in out.splitlines()
                 if phys.split() and phys.split()[-1] in expected)
    assert starts == len(rows)