def test_owner_last_match_wins(tmp_path):
    from diffimpactscout.impact import owners
    (tmp_path / "CODEOWNERS").write_text("*.py @team-a\napps/orders/* @team-b\n")
    assert owners.owner_for("apps/orders/views.py", str(tmp_path)) == "team-b"
    assert owners.owner_for("other.py", str(tmp_path)) == "team-a"
    assert owners.owner_for("x.py", "/nonexistent") == ""
    assert owners.owner_for("Makefile", str(tmp_path)) == ""
