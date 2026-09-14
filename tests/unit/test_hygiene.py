import os

import pytest

import diffimpactscout.checks.hygiene as hygiene
from diffimpactscout.checks.base import (
    REGISTRY,
    CheckContext,
    CheckResult,
    make_check,
)


def _write(root, name, data):
    fn = os.path.join(root, name)
    with open(fn, "wb") as fh:
        fh.write(data)
    return fn


def _read(root, name):
    with open(os.path.join(root, name), "rb") as fh:
        return fh.read()


def _ctx(root):
    return CheckContext(
        root=root,
        anchor="A",
        from_ref="F",
        to_ref="T",
        scope=None,
        config={},
        echo=False,
    )


def _cls(cid):
    return REGISTRY[cid]


def test_hygiene_checks_registered():
    """Verifies that all hygiene checks are registered as file-scoped."""
    for cid in (
        "hygiene/mixed-line-ending",
        "hygiene/trailing-whitespace",
        "hygiene/end-of-file-fixer",
    ):
        assert cid in REGISTRY
        assert REGISTRY[cid].scoped == "files"


def test_hygiene_checks_buildable_from_config():
    """Verifies that hygiene checks can be built from config."""
    check = make_check({"id": "hygiene/mixed-line-ending"})
    assert check is not None
    assert check.scoped == "files"
    assert check.blocking is True
    check2 = make_check({"id": "hygiene/trailing-whitespace"})
    assert check2 is not None
    check3 = make_check({"id": "hygiene/end-of-file-fixer"})
    assert check3 is not None


def test_mixed_line_ending_rewrites_crlf(tmp_path):
    """Verifies that CRLF line endings are rewritten to LF."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\r\ntwo\r\n")
    result = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["a.txt"])
    assert isinstance(result, CheckResult)
    assert result.fixed == ["a.txt"]
    assert result.issues == []
    assert _read(root, "a.txt") == b"one\ntwo\n"


def test_mixed_line_ending_rewrites_lone_cr(tmp_path):
    """Verifies that lone CR line endings are rewritten to LF."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\rtwo\r")
    result = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"one\ntwo\n"


def test_mixed_line_ending_lf_only_is_noop(tmp_path):
    """Verifies that an LF-only file is left unchanged."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\ntwo\n")
    result = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert _read(root, "a.txt") == b"one\ntwo\n"


def test_mixed_line_ending_mixed_content(tmp_path):
    """Verifies that mixed CR/LF content is normalized to LF."""
    root = str(tmp_path)
    _write(root, "a.txt", b"a\r\nb\rc\nd\r\n")
    result = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"a\nb\nc\nd\n"


def test_mixed_line_ending_accepts_fix_lf_at_config_time():
    """Verifies that a fix-lf argument is accepted at config time."""
    check = _cls("hygiene/mixed-line-ending")()
    out = check.extend_config({"id": "hygiene/mixed-line-ending", "args": ["--fix=lf"]})
    assert out is check
    assert check.args == ["--fix=lf"]


def test_mixed_line_ending_rejects_fix_crlf_at_config_time():
    """Verifies that a fix-crlf argument is rejected at config time."""
    check = _cls("hygiene/mixed-line-ending")()
    with pytest.raises(ValueError):
        check.extend_config({"args": ["--fix=crlf"]})


def test_mixed_line_ending_rejects_other_fix_modes():
    """Verifies that unsupported fix modes are rejected at config time."""
    for mode in ("cr", "auto", "no", "unknown"):
        check = _cls("hygiene/mixed-line-ending")()
        with pytest.raises(ValueError):
            check.extend_config({"args": ["--fix=%s" % mode]})


def test_trailing_whitespace_strips_spaces_and_tabs(tmp_path):
    """Verifies that trailing spaces and tabs are stripped from lines."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one  \ntwo\t\n three\n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"one\ntwo\n three\n"


def test_trailing_whitespace_clean_file_is_noop(tmp_path):
    """Verifies that a clean file without trailing whitespace is unchanged."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\ntwo\n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert _read(root, "a.txt") == b"one\ntwo\n"


def test_trailing_whitespace_collapses_trailing_blank_lines(tmp_path):
    """Verifies that trailing blank lines are collapsed down to a single one."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\n\n\n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"one\n"


def test_trailing_whitespace_blank_spaces_only_line(tmp_path):
    """Verifies that a line containing only spaces is stripped to blank."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\n   \ntwo\n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"one\n\ntwo\n"


def test_trailing_whitespace_whitespace_only_file(tmp_path):
    """Verifies that a whitespace-only file is emptied by the fixer."""
    root = str(tmp_path)
    _write(root, "a.txt", b"  \n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b""


def test_trailing_whitespace_lone_newline_file(tmp_path):
    """Verifies that a file with only a newline is emptied by the fixer."""
    root = str(tmp_path)
    _write(root, "a.txt", b"\n")
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b""


def test_end_of_file_adds_missing_newline(tmp_path):
    """Verifies that a missing trailing newline is added."""
    root = str(tmp_path)
    _write(root, "a.txt", b"no newline at end")
    result = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"no newline at end\n"


def test_end_of_file_collapses_multiple_trailing_newlines(tmp_path):
    """Verifies that multiple trailing newlines are collapsed to a single one."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\n\n\n\n")
    result = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), ["a.txt"])
    assert result.fixed == ["a.txt"]
    assert _read(root, "a.txt") == b"one\n"


def test_end_of_file_single_newline_is_noop(tmp_path):
    """Verifies that a file ending with a single newline is unchanged."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one\n")
    result = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert _read(root, "a.txt") == b"one\n"


def test_end_of_file_empty_file_is_noop(tmp_path):
    """Verifies that an empty file is left unchanged."""
    root = str(tmp_path)
    _write(root, "a.txt", b"")
    result = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert _read(root, "a.txt") == b""


def test_fix_content_unknown_fixer_returns_none():
    """Verifies that an unknown fixer returns None."""
    assert hygiene.fix_content("bogus", b"anything") is None


def test_fix_content_mixed_line_ending_semantics():
    """Verifies the mixed-line-ending fixer's content semantics."""
    assert hygiene.fix_content("mixed-line-ending", b"a\r\nb") == b"a\nb"
    assert hygiene.fix_content("mixed-line-ending", b"a\nb") is None
    assert hygiene.fix_content("mixed-line-ending", b"") is None


def test_fix_content_trailing_whitespace_semantics():
    """Verifies the trailing-whitespace fixer's content semantics."""
    assert hygiene.fix_content("trailing-whitespace", b"one  \ntwo\t\n three\n") == b"one\ntwo\n three\n"
    assert hygiene.fix_content("trailing-whitespace", b"one\n") is None
    assert hygiene.fix_content("trailing-whitespace", b"  \n") == b""
    assert hygiene.fix_content("trailing-whitespace", b"\n") == b""
    assert hygiene.fix_content("trailing-whitespace", b"one\n\n\n") == b"one\n"
    assert hygiene.fix_content("trailing-whitespace", b"one\n   \ntwo\n") == b"one\n\ntwo\n"


def test_fix_content_end_of_file_fixer_semantics():
    """Verifies the end-of-file fixer's content semantics."""
    assert hygiene.fix_content("end-of-file-fixer", b"no newline at end") == b"no newline at end\n"
    assert hygiene.fix_content("end-of-file-fixer", b"one\n\n\n\n") == b"one\n"
    assert hygiene.fix_content("end-of-file-fixer", b"one\n") is None
    assert hygiene.fix_content("end-of-file-fixer", b"") is None


def test_check_skips_missing_file(tmp_path):
    """Verifies that a missing file is skipped while existing files are fixed."""
    root = str(tmp_path)
    _write(root, "exists.txt", b"one\r\n")
    result = _cls("hygiene/mixed-line-ending")().run(
        _ctx(root), ["missing.txt", "exists.txt"]
    )
    assert result.fixed == ["exists.txt"]
    assert not os.path.exists(os.path.join(root, "missing.txt"))


def test_write_oserror_is_warned(tmp_path, monkeypatch):
    """Verifies that an OSError during write is reported as a warning."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one  \n")
    real_open = open

    def failing_open(fn, mode="r", *args, **kwargs):
        if mode == "wb":
            raise OSError("read-only filesystem")
        return real_open(fn, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert any("a.txt" in w for w in result.warned)


def test_partial_write_is_warned(tmp_path, monkeypatch):
    """Verifies that a partial write is reported as a warning."""
    root = str(tmp_path)
    _write(root, "a.txt", b"one  \n")
    real_open = open

    class ShortFile(object):
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    def short_open(fn, mode="r", *args, **kwargs):
        if mode == "wb":
            return ShortFile(real_open(fn, mode, *args, **kwargs))
        return real_open(fn, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", short_open)
    result = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["a.txt"])
    assert result.fixed == []
    assert any("a.txt" in w for w in result.warned)


def test_check_skips_all_missing_files(tmp_path):
    """Verifies that runs over all-missing files produce no results."""
    root = str(tmp_path)
    result = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["ghost.txt"])
    assert result.fixed == []
    assert result.issues == []
    assert result.warned == []


def test_check_empty_and_none_file_list(tmp_path):
    """Verifies that empty and None file lists produce no results."""
    root = str(tmp_path)
    for files in ([], None):
        result = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), files)
        assert result.fixed == []
        assert result.issues == []


def test_check_multi_fixer_fixes_different_files(tmp_path):
    """Verifies that each fixer targets only the files it needs to fix."""
    root = str(tmp_path)
    _write(root, "win.txt", b"a\r\nb\r\n")
    _write(root, "trail.txt", b"a  \n")
    _write(root, "eof.txt", b"a")
    lf = _cls("hygiene/mixed-line-ending")().run(_ctx(root), ["win.txt", "trail.txt", "eof.txt"])
    assert lf.fixed == ["win.txt"]
    ts = _cls("hygiene/trailing-whitespace")().run(_ctx(root), ["win.txt", "trail.txt", "eof.txt"])
    assert ts.fixed == ["trail.txt"]
    eof = _cls("hygiene/end-of-file-fixer")().run(_ctx(root), ["win.txt", "trail.txt", "eof.txt"])
    assert eof.fixed == ["eof.txt"]