"""Parse git diffs into file changes and extract Python definitions and usages from source content.

Example: a renamed module shows up with both its new and old path so
references to the old name can still be found.
"""

import ast
import os
import re

import diffimpactscout.gitrun as gitrun
from diffimpactscout.impact._parse import parse_quiet


class Entity(object):
    def __init__(self, name, kind, line, qualname, end_line=None):
        self.name = name
        self.kind = kind
        self.line = line
        self.qualname = qualname
        self.end_line = end_line


class FileChange(object):
    def __init__(self, path, status, old_path=None, ext=None):
        self.path = path
        self.status = status
        self.old_path = old_path
        self.ext = ext


_STATUS_RE = re.compile(r"^[AMDCTR]\d*$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def get_file_changes(root, anchor=None, staged=False, from_ref=None, to_ref=None):
    if staged:
        return _diff_changes(root, ["--cached"])
    if anchor:
        return _diff_changes(root, [anchor, "HEAD"])
    if from_ref and to_ref:
        return _diff_changes(root, [from_ref, to_ref])
    changes = {}
    for tail in (["--cached"], ["HEAD"], []):
        for change in _diff_changes(root, tail):
            if change.path not in changes:
                changes[change.path] = change
    return list(changes.values())


def _diff_changes(root, tail):
    args = ["diff", "--name-status", "-z", "--diff-filter=ACMRTD", "--find-renames"]
    args.extend(tail)
    changes = _parse_name_status(gitrun.git_nul(args, root), root)
    for change in changes:
        if change.status[0] == "D" and change.old_path is None:
            change.old_path = change.path
    return changes


def _parse_name_status(data, root):
    changes = []
    i = 0
    n = len(data)
    while i < n:
        field = data[i]
        if not _STATUS_RE.match(field):
            i += 1
            continue
        status = field
        i += 1
        if status[0] in "RC":
            if i + 1 >= n:
                break
            old_path = _relpath(root, data[i])
            new_path = _relpath(root, data[i + 1])
            i += 2
            changes.append(
                FileChange(new_path, status, old_path, _ext(new_path))
            )
        else:
            if i >= n:
                break
            path = _relpath(root, data[i])
            i += 1
            changes.append(FileChange(path, status, None, _ext(path)))
    return changes


def _relpath(root, path):
    if path is None:
        return None
    if os.path.isabs(path) and root:
        return os.path.relpath(path, root)
    return path


def _ext(path):
    return os.path.splitext(path)[1].lstrip(".").lower()


def read_path_at_ref(root, path, ref):
    result = gitrun.git(["show", "%s:%s" % (ref, path)], root)
    if not result.ok():
        return ""
    return result.out_


def get_changed_lines(root, change, anchor=None, from_ref=None, to_ref=None, staged=False):
    """Return the (old, new) line spans touched by a change.

    Each side is a set of 1-indexed line numbers in that version of the file,
    or None to signal that the file did not exist / all lines count (added or
    deleted wholesale, renamed, or a modified file with no line hunks such as
    a mode-only change). Scoping both maps prevents unchanged sibling symbols
    in the old version from being misreported as deleted.
    """
    status = change.status[0]
    if status == "A":
        return set(), None
    if status == "D":
        return None, set()
    if status == "R":
        return None, None
    args = ["diff", "-U0", "--find-renames"]
    if staged:
        args.append("--cached")
    elif anchor:
        args.extend([anchor, "HEAD"])
    elif from_ref and to_ref:
        args.extend([from_ref, to_ref])
    else:
        args.append("HEAD")
    args.extend(["--", change.path])
    output = gitrun.git(args, root).out_
    old_lines = set()
    new_lines = set()
    for text_line in output.splitlines():
        m = _HUNK_RE.match(text_line)
        if m:
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            for i in range(old_start, old_start + old_count):
                old_lines.add(i)
            for i in range(new_start, new_start + new_count):
                new_lines.add(i)
    if not old_lines and not new_lines:
        return None, None
    return old_lines, new_lines


def extract_entities(source):
    try:
        tree = parse_quiet(source)
    except SyntaxError:
        return []
    entities = []
    _walk(tree, [], entities)
    return entities


def _walk(node, scope, entities):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            entities.append(
                Entity(
                    child.name,
                    "class",
                    getattr(child, "lineno", 0),
                    _qualname(scope, child.name),
                    _end_line(child),
                )
            )
            _walk(child, scope + [("class", child.name)], entities)
        elif isinstance(child, _FUNCS):
            kind = None
            if not scope:
                kind = "function"
            elif scope[-1][0] == "class":
                kind = "method"
            if kind is not None:
                entities.append(
                    Entity(
                        child.name,
                        kind,
                        getattr(child, "lineno", 0),
                        _qualname(scope, child.name),
                        _end_line(child),
                    )
                )
            _walk(child, scope + [("function", child.name)], entities)
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            for target in _assign_targets(child):
                if not scope:
                    entities.append(
                        Entity(
                            target,
                            "module_field",
                            getattr(child, "lineno", 0),
                            target,
                            _end_line(child),
                        )
                    )
                elif scope[-1][0] == "class":
                    entities.append(
                        Entity(
                            target,
                            "class_field",
                            getattr(child, "lineno", 0),
                            _qualname(scope, target),
                            _end_line(child),
                        )
                    )
            _walk(child, scope, entities)
        else:
            _walk(child, scope, entities)


def _assign_targets(node):
    if isinstance(node, ast.AnnAssign):
        target = node.target
        if isinstance(target, ast.Name):
            return [target.id]
        return []
    targets = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            targets.append(target.id)
    return targets


def _qualname(scope, name):
    parts = [n for _kind, n in scope]
    parts.append(name)
    return ".".join(parts)


def _end_line(node):
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    best = getattr(node, "lineno", 0)
    for child in ast.walk(node):
        line = getattr(child, "lineno", None)
        if line is not None and line > best:
            best = line
    return best