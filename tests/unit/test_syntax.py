import os
import warnings

import diffimpactscout.checks.syntax as syntax
from diffimpactscout.checks.base import (
    REGISTRY,
    CheckContext,
    CheckResult,
    make_check,
)


def _write(root, name, text):
    fn = os.path.join(root, name)
    with open(fn, "w") as fh:
        fh.write(text)
    return fn


def _write_bytes(root, name, data):
    fn = os.path.join(root, name)
    with open(fn, "wb") as fh:
        fh.write(data)
    return fn


def _ctx(root, scope=None):
    return CheckContext(
        root=root,
        anchor="A",
        from_ref="F",
        to_ref="T",
        scope=scope,
        config={},
        echo=False,
    )


def _cls(cid):
    return REGISTRY[cid]


class FakeScope(object):
    def __init__(self, changed=None, tracked=True):
        self.changed = changed
        self.tracked = tracked

    def changed_lines(self, anchor, path, from_ref=None, to_ref=None):
        return self.changed

    def is_tracked(self, path):
        return self.tracked


class BoomCtx(object):
    def __init__(self, root):
        self.root = root

    def is_tracked(self, path):
        raise OSError("boom")

    def changed_lines(self, path):
        raise OSError("boom")


def test_syntax_checks_registered():
    """Verifies that all syntax checks are registered with expected scope and blocking flags."""
    for cid, scoped in (
        ("syntax/json-syntax", "files"),
        ("syntax/ast-syntax", "files"),
        ("syntax/merge-conflict", "lines"),
    ):
        assert cid in REGISTRY
        assert REGISTRY[cid].scoped == scoped
        assert REGISTRY[cid].blocking is True


def test_syntax_checks_buildable_from_config():
    """Checks that every syntax check can be built from its config id."""
    for cid in ("syntax/json-syntax", "syntax/ast-syntax", "syntax/merge-conflict"):
        check = make_check({"id": cid})
        assert check is not None
        assert check.scoped in ("files", "lines")


def test_json_valid_is_clean(tmp_path):
    """Verifies that a valid JSON file passes with no issues."""
    root = str(tmp_path)
    _write(root, "good.json", '{"a": 1}\n')
    result = _cls("syntax/json-syntax")().run(_ctx(root), ["good.json"])
    assert isinstance(result, CheckResult)
    assert result.ok()
    assert result.issues == []


def test_json_invalid_reports_issue_with_position(tmp_path):
    """Verifies that invalid JSON reports a single issue with position details."""
    root = str(tmp_path)
    _write(root, "bad.json", '{\n"a": 1,\n"b": bad\n}\n')
    result = _cls("syntax/json-syntax")().run(_ctx(root), ["bad.json"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "bad.json"
    assert issue.code == "syntax/json-syntax"
    assert issue.line == 3
    assert issue.column > 0
    assert "invalid JSON" in issue.message


def test_json_non_json_files_skipped(tmp_path):
    """Checks that non-JSON files are skipped by the JSON syntax check."""
    root = str(tmp_path)
    _write(root, "data.txt", "{ not json\n")
    result = _cls("syntax/json-syntax")().run(_ctx(root), ["data.txt"])
    assert result.ok()
    assert result.issues == []


def test_json_missing_file_skipped(tmp_path):
    """Verifies that a missing JSON file is skipped without warnings."""
    root = str(tmp_path)
    result = _cls("syntax/json-syntax")().run(_ctx(root), ["nope.json"])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_ast_valid_is_clean(tmp_path):
    """Verifies that a valid Python file passes with no issues."""
    root = str(tmp_path)
    _write(root, "good.py", "x = 1\ndef f():\n    return x\n")
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["good.py"])
    assert result.ok()
    assert result.issues == []


def test_ast_invalid_escape_emits_no_syntax_warning(tmp_path):
    """Verifies that the AST check does not leak SyntaxWarnings from analyzed code."""
    root = str(tmp_path)
    _write(root, "esc.py", "SQL = '^(.*?)\\.SO'\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _cls("syntax/ast-syntax")().run(_ctx(root), ["esc.py"])
    assert result.ok()
    assert not [w for w in caught if issubclass(w.category, SyntaxWarning)]


def test_ast_invalid_reports_issue_with_line(tmp_path):
    """Verifies that invalid Python syntax reports an issue with its line."""
    root = str(tmp_path)
    _write(root, "bad.py", "x = 1\n\ndef bad(:\n    pass\n")
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["bad.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "bad.py"
    assert issue.code == "syntax/ast-syntax"
    assert issue.line == 3
    assert issue.column > 0
    assert "invalid python syntax" in issue.message


def test_ast_non_py_files_skipped(tmp_path):
    """Checks that non-Python files are skipped by the AST syntax check."""
    root = str(tmp_path)
    _write(root, "script.sh", "def broken(:\n")
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["script.sh"])
    assert result.ok()
    assert result.issues == []


def test_ast_missing_file_skipped(tmp_path):
    """Verifies that a missing Python file is skipped with no issues."""
    root = str(tmp_path)
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["nope.py"])
    assert result.ok()
    assert result.issues == []


def test_ast_utf8_bom_parses_clean(tmp_path):
    """Verifies that a UTF-8 BOM Python file parses cleanly."""
    root = str(tmp_path)
    _write_bytes(root, "bom.py", b"\xef\xbb\xbfx = 1\n")
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["bom.py"])
    assert result.ok()
    assert result.issues == []


def test_ast_decode_failure_reports_issue_at_origin(tmp_path):
    """Verifies that undecodable source reports an issue at the file origin."""
    root = str(tmp_path)
    _write_bytes(root, "bad.py", b"\xff\xff\xff\xff\n")
    result = _cls("syntax/ast-syntax")().run(_ctx(root), ["bad.py"])
    assert not result.ok()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == "bad.py"
    assert issue.code == "syntax/ast-syntax"
    assert issue.line is None
    assert issue.column is None
    assert "invalid python source" in issue.message


def test_merge_conflict_reports_markers_on_changed_lines(tmp_path):
    """Verifies that merge-conflict markers on changed lines are reported as issues."""
    root = str(tmp_path)
    _write(root, "a.txt", "<<<<<<< HEAD\none\n=======\ntwo\n>>>>>>> master\n")
    scope = FakeScope(changed={1, 3, 5})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert not result.ok()
    assert len(result.issues) == 3
    assert [i.line for i in result.issues] == [1, 3, 5]
    assert all(i.code == "syntax/merge-conflict" for i in result.issues)
    assert all(i.column is None for i in result.issues)


def test_merge_conflict_ignores_markers_outside_changed_lines(tmp_path):
    """Checks that markers outside the changed lines are ignored."""
    root = str(tmp_path)
    _write(root, "a.txt", "<<<<<<< HEAD\n")
    scope = FakeScope(changed={5})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_untracked_checks_whole_file(tmp_path):
    """Verifies that untracked files are checked across the whole file."""
    root = str(tmp_path)
    _write(root, "u.txt", "a\n=======\nb\n")
    scope = FakeScope(changed=None, tracked=False)
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["u.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line == 2


def test_merge_conflict_empty_changed_set_skips(tmp_path):
    """Checks that an empty changed-line set skips the merge-conflict check."""
    root = str(tmp_path)
    _write(root, "a.txt", "<<<<<<< HEAD\n")
    scope = FakeScope(changed=set())
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_tracked_scope_failure_skips(tmp_path):
    """Verifies that a failed tracked scope lookup causes the check to skip."""
    root = str(tmp_path)
    _write(root, "a.txt", "=======\n")
    scope = FakeScope(changed=None, tracked=True)
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_scope_error_skips(tmp_path):
    """Verifies that a scope error causes the merge-conflict check to skip cleanly."""
    root = str(tmp_path)
    _write(root, "a.txt", "<<<<<<< HEAD\n")
    result = _cls("syntax/merge-conflict")().run(BoomCtx(root), ["a.txt"])
    assert result.ok()
    assert result.issues == []
    assert result.warned == []


def test_merge_conflict_missing_file_skipped(tmp_path):
    """Verifies that a missing file is skipped by the merge-conflict check."""
    root = str(tmp_path)
    scope = FakeScope(changed={1})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["nope.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_does_not_flag_similar_non_markers(tmp_path):
    """Checks that similar-looking lines are not flagged as merge markers."""
    root = str(tmp_path)
    _write(root, "a.txt", "======\n<<<<<< HEAD\n>>>>>> master\n")
    scope = FakeScope(changed={1, 2, 3})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_flags_exact_seven_equals(tmp_path):
    """Verifies that an exact seven-equals line is flagged as a merge marker."""
    root = str(tmp_path)
    _write(root, "a.txt", "x\n=======\ny\n")
    scope = FakeScope(changed={2})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert not result.ok()
    assert len(result.issues) == 1
    assert result.issues[0].line == 2


def test_merge_conflict_does_not_flag_rst_underline(tmp_path):
    """Checks that an RST-style underline is not flagged as a merge marker."""
    root = str(tmp_path)
    _write(root, "a.txt", "Title\n==========\nbody\n")
    scope = FakeScope(changed={1, 2, 3})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_out_of_range_lineno_skipped(tmp_path):
    """Verifies that out-of-range line numbers cause the check to skip."""
    root = str(tmp_path)
    _write(root, "a.txt", "<<<<<<< HEAD\n")
    scope = FakeScope(changed={0, 10, -3})
    result = _cls("syntax/merge-conflict")().run(_ctx(root, scope), ["a.txt"])
    assert result.ok()
    assert result.issues == []


def test_merge_conflict_empty_and_none_file_list(tmp_path):
    """Verifies that empty or None file lists pass the merge-conflict check cleanly."""
    root = str(tmp_path)
    check = _cls("syntax/merge-conflict")()
    for files in ([], None):
        result = check.run(_ctx(root, FakeScope(changed={1})), files)
        assert result.ok()
        assert result.issues == []