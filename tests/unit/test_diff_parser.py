import os
import subprocess

import diffimpactscout.impact.diff_parser as dp
from diffimpactscout.impact.diff_parser import Entity, FileChange


def _git_env(extra=None):
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if extra:
        env.update(extra)
    return env


def _git(*args, cwd, env=None):
    return subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + list(args),
        cwd=cwd,
        env=env or _git_env(),
    )


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git("init", cwd=repo)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    return repo


def _write(repo, filename, content):
    path = os.path.join(repo, filename)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w") as fh:
        fh.write(content)


def _commit(repo, msg):
    _git("add", "-A", cwd=repo, env=_git_env())
    _git("commit", "-m", msg, cwd=repo, env=_git_env())
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _sig(change):
    return (change.path, change.status, change.old_path, change.ext)


def test_entity_fields():
    """Verifies the Entity dataclass stores name, kind, line, and qualname."""
    ent = Entity("helper", "function", 3, "helper")
    assert ent.name == "helper"
    assert ent.kind == "function"
    assert ent.line == 3
    assert ent.qualname == "helper"


def test_file_change_fields():
    """Checks the FileChange dataclass stores path, status, old path, and ext."""
    fc = FileChange("a.py", "M", None, "py")
    assert fc.path == "a.py"
    assert fc.status == "M"
    assert fc.old_path is None
    assert fc.ext == "py"


def test_extract_entities_fixture():
    """Verifies entities are extracted from functions, classes, and fields."""
    source = (
        "import os\n"
        "\n"
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "async def fetch(url):\n"
        "    return url\n"
        "\n"
        "class Book(object):\n"
        "    title = models.CharField(max_length=100)\n"
        "    pages: int = 0\n"
        "\n"
        "    def __str__(self):\n"
        "        return self.title\n"
        "\n"
        "    async def save(self):\n"
        "        pass\n"
        "\n"
        "COUNT = 5\n"
        'label: str = "x"\n'
    )
    entities = dp.extract_entities(source)
    assert [
        (e.name, e.kind, e.line, e.qualname) for e in entities
    ] == [
        ("helper", "function", 3, "helper"),
        ("fetch", "function", 6, "fetch"),
        ("Book", "class", 9, "Book"),
        ("title", "class_field", 10, "Book.title"),
        ("pages", "class_field", 11, "Book.pages"),
        ("__str__", "method", 13, "Book.__str__"),
        ("save", "method", 16, "Book.save"),
        ("COUNT", "module_field", 19, "COUNT"),
        ("label", "module_field", 20, "label"),
    ]


def test_extract_entities_skips_locals_and_nested():
    """Checks that local and nested definitions are excluded from entities."""
    source = (
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    x = 2\n"
        "    return inner\n"
        "a, b = 1, 2\n"
    )
    entities = dp.extract_entities(source)
    assert [(e.name, e.kind, e.qualname) for e in entities] == [
        ("outer", "function", "outer")
    ]


def test_extract_entities_skips_imports():
    """Verifies that import statements are not extracted as entities."""
    source = "import os\nfrom x import y\n"
    assert dp.extract_entities(source) == []


def test_extract_entities_syntax_error_returns_empty():
    """Checks that a syntax error yields no extracted entities."""
    assert dp.extract_entities("def broken(:\n") == []


def test_parse_name_status_a_m_d_r():
    """Verifies name-status parsing handles added, modified, deleted, renamed."""
    data = [
        "M",
        "a.py",
        "A",
        "new.py",
        "D",
        "del.py",
        "R100",
        "old.py",
        "renamed.py",
    ]
    changes = dp._parse_name_status(data, "/repo")
    assert [_sig(c) for c in changes] == [
        ("a.py", "M", None, "py"),
        ("new.py", "A", None, "py"),
        ("del.py", "D", None, "py"),
        ("renamed.py", "R100", "old.py", "py"),
    ]


def test_parse_name_status_rename_without_score():
    """Checks that a rename without a similarity score is parsed correctly."""
    changes = dp._parse_name_status(["R", "old.py", "new.py"], "/repo")
    assert [_sig(c) for c in changes] == [("new.py", "R", "old.py", "py")]


def test_parse_name_status_resolves_absolute_paths():
    """Verifies that absolute paths are resolved relative to the repo root."""
    changes = dp._parse_name_status(
        ["A", "/tmp/root/mod.py"], "/tmp/root"
    )
    assert [_sig(c) for c in changes] == [("mod.py", "A", None, "py")]


def test_parse_name_status_handles_empty_and_garbage():
    """Checks that empty and garbage name-status input is handled safely."""
    assert dp._parse_name_status([], "/repo") == []
    assert dp._parse_name_status(["M"], "/repo") == []
    changes = dp._parse_name_status(["junk", "A", "x.py"], "/repo")
    assert [_sig(c) for c in changes] == [("x.py", "A", None, "py")]


def test_ext_lowercase_and_dotfiles():
    """Verifies _ext lowercases extensions and ignores dotfiles."""
    assert dp._ext("IMG.PNG") == "png"
    assert dp._ext(".gitignore") == ""
    assert dp._ext("Makefile") == ""
    assert dp._ext("a.py") == "py"


def test_get_file_changes_anchor_add_modify_rename(tmp_path):
    """Verifies file changes report adds, modifies, and renames against anchor."""
    repo = _make_repo(tmp_path)
    _write(repo, "a.py", "a1\n")
    _write(repo, "old.py", "old\n")
    _commit(repo, "base")
    _write(repo, "a.py", "a2\n")
    _write(repo, "new.py", "new\n")
    _git("mv", "old.py", "renamed.py", cwd=repo)
    _commit(repo, "dev")
    changes = dp.get_file_changes(repo, anchor="HEAD~1")
    sigs = sorted(_sig(c) for c in changes)
    assert sigs == [
        ("a.py", "M", None, "py"),
        ("new.py", "A", None, "py"),
        ("renamed.py", "R100", "old.py", "py"),
    ]


def test_get_file_changes_anchor_deletion(tmp_path):
    """Checks that deleted files are reported and readable at the anchor ref."""
    repo = _make_repo(tmp_path)
    _write(repo, "del.py", "def helper():\n    return 1\n")
    _commit(repo, "base")
    _git("rm", "del.py", cwd=repo)
    _commit(repo, "delete")
    changes = dp.get_file_changes(repo, anchor="HEAD~1")
    assert [_sig(c) for c in changes] == [("del.py", "D", "del.py", "py")]
    assert dp.read_path_at_ref(repo, "del.py", "HEAD~1") == (
        "def helper():\n    return 1\n"
    )


def test_get_file_changes_staged(tmp_path):
    """Verifies that staged changes are reported when staged=True."""
    repo = _make_repo(tmp_path)
    _write(repo, "a.py", "one\n")
    _commit(repo, "base")
    _write(repo, "b.py", "two\n")
    _git("add", "b.py", cwd=repo)
    changes = dp.get_file_changes(repo, staged=True)
    assert [_sig(c) for c in changes] == [("b.py", "A", None, "py")]


def test_get_file_changes_range(tmp_path):
    """Checks that changes between explicit from/to refs are reported."""
    repo = _make_repo(tmp_path)
    _write(repo, "a.py", "one\n")
    from_sha = _commit(repo, "base")
    _write(repo, "b.py", "two\n")
    to_sha = _commit(repo, "dev")
    changes = dp.get_file_changes(repo, from_ref=from_sha, to_ref=to_sha)
    assert [_sig(c) for c in changes] == [("b.py", "A", None, "py")]


def test_get_file_changes_heuristic_worktree(tmp_path):
    """Verifies that uncommitted worktree edits are detected heuristically."""
    repo = _make_repo(tmp_path)
    _write(repo, "a.py", "one\n")
    _commit(repo, "base")
    _write(repo, "a.py", "two\n")
    changes = dp.get_file_changes(repo)
    assert [_sig(c) for c in changes] == [("a.py", "M", None, "py")]


def test_get_file_changes_heuristic_union_dedup(tmp_path):
    """Checks that heuristic changes union staged and worktree without dupes."""
    repo = _make_repo(tmp_path)
    _write(repo, "a.py", "one\n")
    _write(repo, "plain.py", "base\n")
    _commit(repo, "base")
    _write(repo, "staged.py", "s\n")
    _git("add", "staged.py", cwd=repo)
    _write(repo, "staged.py", "s2\n")
    _write(repo, "plain.py", "changed\n")
    changes = dp.get_file_changes(repo)
    sigs = sorted(_sig(c) for c in changes)
    assert sigs == [
        ("plain.py", "M", None, "py"),
        ("staged.py", "A", None, "py"),
    ]


def test_read_path_at_ref_deleted_file(tmp_path):
    """Verifies reading a file at a ref before and after deletion."""
    repo = _make_repo(tmp_path)
    _write(repo, "del.py", "old-content\n")
    _commit(repo, "base")
    _git("rm", "del.py", cwd=repo)
    _commit(repo, "delete")
    assert dp.read_path_at_ref(repo, "del.py", "HEAD~1") == "old-content\n"
    assert dp.read_path_at_ref(repo, "del.py", "HEAD") == ""
    assert dp.read_path_at_ref(repo, "missing.py", "HEAD") == ""


def test_read_path_at_ref_renamed_old_path(tmp_path):
    """Checks that renamed files can be read via old and new paths."""
    repo = _make_repo(tmp_path)
    _write(repo, "old.py", "payload\n")
    _commit(repo, "base")
    _git("mv", "old.py", "new.py", cwd=repo)
    _commit(repo, "rename")
    assert dp.read_path_at_ref(repo, "old.py", "HEAD~1") == "payload\n"
    assert dp.read_path_at_ref(repo, "new.py", "HEAD") == "payload\n"