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
    REGISTRY.pop(FakeCheck.id, None)
    try:
        assert register(FakeCheck) is FakeCheck
        assert REGISTRY[FakeCheck.id] is FakeCheck
    finally:
        REGISTRY.pop(FakeCheck.id, None)


def test_register_rejects_idless_class():
    class NoIdCheck(Check):
        scoped = "files"

    with pytest.raises(ValueError):
        register(NoIdCheck)
    assert None not in REGISTRY


def test_register_later_wins_on_collision():
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
    check = ExternalCheck(
        {"id": "ext/str", "command": ["echo", "hi"], "always_block": "false"}
    )
    assert check.always_block is False
    check = ExternalCheck(
        {"id": "ext/str2", "command": ["echo", "hi"], "always_block": "true"}
    )
    assert check.always_block is True


def test_make_check_appends_args_to_fixed_args():
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
    assert make_check({"id": "nope/nope"}) is None


def test_make_check_non_dict_returns_none():
    assert make_check(None) is None
    assert make_check("oops") is None


def test_external_check_created_for_external_type():
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
    check = make_check({"type": "external", "command": ["echo", "hi"]})
    assert isinstance(check, ExternalCheck)
    assert check.id == "external:echo"


def test_external_check_inplace_file_substitution_and_rc(tmp_path):
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
    root = str(tmp_path)
    script = 'echo "got=$1"; exit 3'
    check = ExternalCheck({"id": "ext/append", "command": ["sh", "-c", script, "sh"]})
    result = check.run(_ctx(root=root), ["a.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].path == "a.txt"
    assert "got=a.txt" in result.issues[0].message


def test_external_check_zero_rc_is_clean(tmp_path):
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/ok", "command": ["sh", "-c", "exit 0", "sh", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert result.ok()
    assert not result.has_issues()
    assert result.issues == []


def test_external_check_missing_command_warns_not_blocks(tmp_path):
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
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/missing-many", "command": ["/nonexistent/tool-xyz", "{file}"]}
    )
    result = check.run(_ctx(root=root), ["a.txt", "b.txt", "c.txt"])
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1


def test_external_check_nonzero_rc_no_output_falls_back_to_exit_code(tmp_path):
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
    root = str(tmp_path)
    check = ExternalCheck({"id": "ext/none", "command": ["/bin/echo", "hi"]})
    result = check.run(_ctx(root=root), [])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_external_check_path_with_spaces_stays_single_argv(tmp_path):
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
    root = str(tmp_path)
    check = ExternalCheck(
        {"id": "ext/repo-missing", "scoped": "repo", "command": ["/nonexistent/repo-tool"]}
    )
    result = check.run(_ctx(root=root), ["a.txt"])
    assert result.ok()
    assert result.issues == []
    assert len(result.warned) == 1


def test_context_changed_lines_delegates_to_scope():
    scope = FakeScope()
    ctx = _ctx(scope=scope)
    assert ctx.changed_lines("a.txt") == {3}
    assert scope.calls == [
        ("changed_lines", "A", "a.txt", "F", "T"),
    ]


def test_context_is_tracked_delegates_to_scope():
    scope = FakeScope()
    ctx = _ctx(scope=scope)
    assert ctx.is_tracked("a.txt") is True
    assert ctx.is_tracked("b.txt") is False
    assert scope.calls == [("is_tracked", "a.txt"), ("is_tracked", "b.txt")]


def test_context_fail_open_on_scope_errors():
    ctx = _ctx(scope=BoomScope())
    assert ctx.changed_lines("a.txt") is None
    assert ctx.is_tracked("a.txt") is False


def test_context_echo_note_respects_flag(capsys):
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
    issue = CheckIssue("a.txt", 3, 5, "RUF100", "unused noqa")
    assert issue.format() == "a.txt:3:5: RUF100 unused noqa"


def test_issue_format_zero_position():
    issue = CheckIssue("b.txt", 0, 0, "ext/tool", "failed")
    assert issue.format() == "b.txt:0:0: ext/tool failed"


def test_result_ok_and_has_issues():
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
    result = CheckResult(warned=["missing tool"])
    assert result.ok()
    assert not result.has_issues()


def test_extend_config_ignores_unknown_keys():
    check = FakeCheck()
    check.extend_config({"id": "test/fake", "unknown": 1, "args": [], "blocking": True})
    assert check.blocking is True
    assert check.args == []


def test_check_requires_id():
    with pytest.raises(ValueError):
        Check()


def test_external_check_requires_command():
    with pytest.raises(ValueError):
        ExternalCheck({"id": "ext/none"})