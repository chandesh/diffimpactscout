import pytest

from diffimpactscout.checks.base import (
    REGISTRY,
    Check,
    CheckContext,
    CheckIssue,
    CheckResult,
    ExternalCheck,
    make_check,
    register,
)


class FakeCheck(Check):
    id = "test/fake"
    scoped = "files"
    blocking = True
    always_block = False


class AlwaysBlockCheck(Check):
    id = "test/always-block"
    blocking = True
    always_block = True


class FakeScope(object):
    def __init__(self):
        self.calls = []

    def changed_lines(self, anchor, path, from_ref, to_ref):
        self.calls.append(("changed_lines", anchor, path, from_ref, to_ref))
        return {3}

    def is_tracked(self, path):
        self.calls.append(("is_tracked", path))
        return path == "a.txt"


class BoomScope(object):
    def changed_lines(self, anchor, path, from_ref, to_ref):
        raise OSError("boom")

    def is_tracked(self, path):
        raise OSError("boom")


def _ctx(root="/repo", scope=None, anchor="A", from_ref="F", to_ref="T"):
    return CheckContext(
        root=root,
        anchor=anchor,
        from_ref=from_ref,
        to_ref=to_ref,
        scope=scope or FakeScope(),
        config={},
        echo=False,
    )


def test_register_stores_by_id_and_returns_class():
    """Verifies that register stores the check by id and returns the class."""
    REGISTRY.pop(FakeCheck.id, None)
    try:
        assert register(FakeCheck) is FakeCheck
        assert REGISTRY[FakeCheck.id] is FakeCheck
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_register_rejects_idless_class():
    """Checks that register raises ValueError for a class without an id."""
    class NoIdCheck(Check):
        scoped = "files"

    with pytest.raises(ValueError):
        register(NoIdCheck)
    assert None not in REGISTRY


def test_register_later_wins_on_collision():
    """Checks that registering a later check with the same id wins."""
    REGISTRY.pop(FakeCheck.id, None)

    class OtherCheck(Check):
        id = "test/fake"
        scoped = "lines"

    try:
        register(OtherCheck)
        assert REGISTRY[FakeCheck.id] is OtherCheck
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_make_check_instantiates_registered_class():
    """Verifies that make_check instantiates the registered check class."""
    REGISTRY[FakeCheck.id] = FakeCheck
    try:
        check = make_check({"id": "test/fake"})
        assert isinstance(check, FakeCheck)
        assert check.scoped == "files"
        assert check.blocking is True
        assert check.args == []
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_make_check_applies_entry_args_and_blocking():
    """Checks that make_check applies entry args and blocking flag."""
    REGISTRY[FakeCheck.id] = FakeCheck
    try:
        check = make_check(
            {"id": "test/fake", "args": ["--maxkb=100", "--verbose"], "blocking": False}
        )
        assert check.args == ["--maxkb=100", "--verbose"]
        assert check.blocking is False
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_make_check_parses_string_bool_values():
    """Checks that make_check parses string boolean values correctly."""
    REGISTRY[FakeCheck.id] = FakeCheck
    try:
        check = make_check(
            {"id": "test/fake", "blocking": "false", "always_block": "true"}
        )
        assert check.blocking is False
        assert check.always_block is True
        check = make_check(
            {"id": "test/fake", "blocking": "true", "always_block": "false"}
        )
        assert check.blocking is True
        assert check.always_block is False
        check = make_check(
            {"id": "test/fake", "blocking": "0", "always_block": "no"}
        )
        assert check.blocking is False
        assert check.always_block is False
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_external_check_parses_string_always_block():
    """Checks that ExternalCheck parses string always_block values."""
    check = ExternalCheck(
        {"id": "ext/str", "command": ["echo", "hi"], "always_block": "false"}
    )
    assert check.always_block is False
    check = ExternalCheck(
        {"id": "ext/str2", "command": ["echo", "hi"], "always_block": "true"}
    )
    assert check.always_block is True


def test_make_check_appends_args_to_fixed_args():
    """Checks that make_check appends entry args to the check's fixed args."""
    REGISTRY[FakeCheck.id] = FakeCheck
    try:

        class FixedArgsCheck(Check):
            id = "test/fixed-args"

            def __init__(self):
                super(FixedArgsCheck, self).__init__()
                self.args = ["--base"]

        REGISTRY[FixedArgsCheck.id] = FixedArgsCheck
        check = make_check({"id": "test/fixed-args", "args": ["--extra"]})
        assert check.args == ["--base", "--extra"]
    finally:
        REGISTRY.pop(FakeCheck.id, None)
        REGISTRY.pop("test/fixed-args", None)


def test_make_check_always_block_default_and_override():
    """Checks that always_block uses its default and respects overrides."""
    REGISTRY[AlwaysBlockCheck.id] = AlwaysBlockCheck
    try:
        default = make_check({"id": "test/always-block", "blocking": False})
        assert default.always_block is True
        assert default.blocking is False
        flipped = make_check({"id": "test/always-block", "always_block": False})
        assert flipped.always_block is False
    finally:
        REGISTRY.pop(AlwaysBlockCheck.id, None)


def test_make_check_unknown_id_returns_none():
    """Checks that make_check returns None for an unknown check id."""
    assert make_check({"id": "nope/nope"}) is None


def test_make_check_non_dict_returns_none():
    """Checks that make_check returns None for non-dict entries."""
    assert make_check(None) is None
    assert make_check("oops") is None


def test_external_check_created_for_external_type():
    """Verifies that make_check creates an ExternalCheck for external type."""
    check = make_check(
        {
            "type": "external",
            "id": "ext/echo",
            "command": ["/bin/echo", "{file}"],
        }
    )
    assert isinstance(check, ExternalCheck)
    assert check.id == "ext/echo"


def test_make_check_external_without_id_derives_default():
    """Checks that external checks without an id get a derived default id."""
    check = make_check({"type": "external", "command": ["echo", "hi"]})
    assert isinstance(check, ExternalCheck)
    assert check.id == "external:echo"


def test_external_check_inplace_file_substitution_and_rc(tmp_path):
    """Verifies external checks substitute {file} in place and use the rc."""
    root = str(tmp_path)
    script = 'echo "mid=$1 end=$2"; exit 7'
    check = ExternalCheck(
        {"id": "ext/rc", "command": ["sh", "-c", script, "sh", "{file}", "post"]}
    )
    result = check.run(_ctx(root=root), ["a.txt", "b.txt"])
    assert not result.ok()
    assert result.has_issues()
    assert len(result.issues) == 2
    assert result.issues[0].path == "a.txt"
    assert result.issues[1].path == "b.txt"
    assert "mid=a.txt end=post" in result.issues[0].message
    assert "mid=b.txt end=post" in result.issues[1].message


def test_external_check_appends_path_when_no_placeholder(tmp_path):
    """Checks that external checks append the path when no placeholder exists."""
    root = str(tmp_path)
    script = 'echo "got=$1"; exit 3'
    check = ExternalCheck({"id": "ext/append", "command": ["sh", "-c", script, "sh"]})
    result = check.run(_ctx(root=root), ["a.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "a.txt"
    assert "got=a.txt" in result.issues[0].message


def test_external_check_zero_rc_is_clean(tmp_path):
    """Checks that an external check returning zero is clean."""
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/ok", "command": ["sh", "-c", "exit 0", "sh", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert result.ok()
    assert not result.has_issues()
    assert result.issues == []


def test_external_check_missing_command_warns_not_blocks(tmp_path):
    """Checks that a missing external command warns without blocking."""
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/missing", "command": ["/nonexistent/tool-xyz", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert result.ok()
    assert not result.has_issues()
    assert result.issues == []
    assert len(result.warned) == 1


def test_external_check_missing_command_warns_once_for_many_files(tmp_path):
    """Checks that a missing command warns only once across many files."""
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/missing-many", "command": ["/nonexistent/tool-xyz", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt", "b.txt", "c.txt"])
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1


def test_external_check_nonzero_rc_no_output_falls_back_to_exit_code(tmp_path):
    """Checks that a silent nonzero rc falls back to the exit code message."""
    root = str(tmp_path)
    script = "exit 9"
    check = ExternalCheck(
        {"id": "ext/silent", "command": ["sh", "-c", script, "sh", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].message == "exit code 9"


def test_external_check_empty_file_list_is_clean(tmp_path):
    """Checks that an external check with no files is clean."""
    root = str(tmp_path)
    check = ExternalCheck({"id": "ext/none", "command": ["/bin/echo", "hi"]})
    result = check.run(_ctx(root=root), [])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_external_check_path_with_spaces_stays_single_argv(tmp_path):
    """Checks that paths with spaces remain a single argv entry."""
    root = str(tmp_path)
    script = 'echo "got=$1"; exit 4'
    check = ExternalCheck(
        {"id": "ext/space", "command": ["sh", "-c", script, "sh"]}
    )
    result = check.run(_ctx(root=root), ["dir with space/a file.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert "got=dir with space/a file.txt" in result.issues[0].message


def test_external_check_repo_scoped_runs_once_no_file(tmp_path):
    """Checks that repo-scoped external checks run once without a file."""
    root = str(tmp_path)
    script = 'echo "repo-check"; exit 5'
    check = ExternalCheck(
        {
            "id": "ext/repo",
            "scoped": "repo",
            "command": ["sh", "-c", script, "sh"],
        }
    )
    result = check.run(_ctx(root=root), ["a.txt", "b.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "."
    assert "repo-check" in result.issues[0].message


def test_external_check_repo_scoped_zero_rc_is_clean(tmp_path):
    """Checks that a repo-scoped external check with zero rc is clean."""
    root = str(tmp_path)
    check = ExternalCheck(
        {
            "id": "ext/repo-ok",
            "scoped": "repo",
            "command": ["sh", "-c", "exit 0", "sh"],
        }
    )
    result = check.run(_ctx(root=root), ["a.txt", "b.txt"])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_external_check_repo_scoped_missing_command_warns(tmp_path):
    """Checks that a missing repo-scoped command warns without blocking."""
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/repo-missing", "scoped": "repo", "command": ["/nonexistent/repo-tool"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1


def test_context_changed_lines_delegates_to_scope():
    """Verifies that context.changed_lines delegates to the scope object."""
    scope = FakeScope()
    ctx = _ctx(scope=scope)
    assert ctx.changed_lines("a.txt") == {3}
    assert scope.calls == [
        ("changed_lines", "A", "a.txt", "F", "T"),
    ]


def test_context_is_tracked_delegates_to_scope():
    """Verifies that context.is_tracked delegates to the scope object."""
    scope = FakeScope()
    ctx = _ctx(scope=scope)
    assert ctx.is_tracked("a.txt") is True
    assert ctx.is_tracked("b.txt") is False
    assert scope.calls == [("is_tracked", "a.txt"), ("is_tracked", "b.txt")]


def test_context_fail_open_on_scope_errors():
    """Checks that scope errors fail open rather than raising."""
    ctx = _ctx(scope=BoomScope())
    assert ctx.changed_lines("a.txt") is None
    assert ctx.is_tracked("a.txt") is False


def test_context_echo_note_respects_flag(capsys):
    """Checks that context.note respects the echo flag."""
    scope = FakeScope()
    quiet = _ctx(scope=scope, root="/repo")
    loud = _ctx(scope=scope, root="/repo")
    loud.echo = True
    quiet.echo = False
    quiet.note("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    loud.note("hello")
    captured = capsys.readouterr()
    assert captured.out == "hello\n"


def test_issue_format():
    """Verifies that CheckIssue formats path, line, column, code, and message."""
    issue = CheckIssue("a.txt", 3, 5, "RUF100", "unused noqa")
    assert issue.format() == "a.txt:3:5: RUF100 unused noqa"


def test_issue_format_file_level_no_position():
    """Checks that a file-level issue renders the path without a position."""
    issue = CheckIssue("b.txt", None, None, "ext/tool", "failed")
    assert issue.format() == "b.txt: ext/tool failed"


def test_issue_format_line_only_renders_dash_column():
    """Checks that a missing column renders as a dash."""
    issue = CheckIssue("c.txt", 7, None, "ext/tool", "failed")
    assert issue.format() == "c.txt:7:-: ext/tool failed"


def test_result_ok_and_has_issues():
    """Verifies CheckResult.ok and has_issues for clean and dirty results."""
    clean = CheckResult()
    assert clean.ok()
    assert not clean.has_issues()
    assert clean.issues == []
    assert clean.fixed == []
    assert clean.skipped == []
    assert clean.warned == []

    dirty = CheckResult(
        issues=[CheckIssue("a.txt", 1, 0, "X", "m")],
        fixed=["a.txt"],
        warned=["missing tool"],
    )
    assert not dirty.ok()
    assert dirty.has_issues()
    assert dirty.fixed == ["a.txt"]
    assert dirty.warned == ["missing tool"]


def test_warned_alone_is_ok():
    """Checks that a result with only warnings is still ok."""
    result = CheckResult(warned=["missing tool"])
    assert result.ok()
    assert not result.has_issues()


def test_extend_config_ignores_unknown_keys():
    """Checks that extend_config ignores unknown config keys."""
    check = FakeCheck()
    check.extend_config({"id": "test/fake", "unknown": 1, "args": [], "blocking": True})
    assert check.blocking is True
    assert check.args == []


def test_check_requires_id():
    """Checks that instantiating a Check without an id raises ValueError."""
    with pytest.raises(ValueError):
        Check()


def test_external_check_requires_command():
    """Checks that ExternalCheck without a command raises ValueError."""
    with pytest.raises(ValueError):
        ExternalCheck({"id": "ext/none"})