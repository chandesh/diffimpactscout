def test_owner_last_match_wins(tmp_path):
    from diffimpactscout.impact import owners
    (tmp_path / "CODEOWNERS").write_text("*.py @team-a\napps/orders/* @team-b\n")
    assert owners.owner_for("apps/orders/views.py", str(tmp_path)) == "team-b"
    assert owners.owner_for("other.py", str(tmp_path)) == "team-a"
    assert owners.owner_for("x.py", "/nonexistent") == ""
    assert owners.owner_for("Makefile", str(tmp_path)) == ""


def test_owner_resolves_github_codeowners(tmp_path):
    from diffimpactscout.impact import owners
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("*.py @gh-team\n")
    assert owners.owner_for("views.py", str(tmp_path)) == "gh-team"


def test_owner_resolves_docs_codeowners(tmp_path):
    from diffimpactscout.impact import owners
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CODEOWNERS").write_text("*.py @docs-team\n")
    assert owners.owner_for("views.py", str(tmp_path)) == "docs-team"


def test_owner_root_codeowners_wins_over_nested(tmp_path):
    """The first location in root, .github, docs order wins, no merging."""
    from diffimpactscout.impact import owners
    (tmp_path / "CODEOWNERS").write_text("*.py @root-team\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("*.py @gh-team\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CODEOWNERS").write_text("*.py @docs-team\n")
    assert owners.owner_for("views.py", str(tmp_path)) == "root-team"


def test_owner_only_nested_codeowners_used(tmp_path):
    from diffimpactscout.impact import owners
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("*.py @gh-team\n")
    assert owners.owner_for("views.py", str(tmp_path)) == "gh-team"
