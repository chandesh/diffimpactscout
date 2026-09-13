"""Parse git diffs into file changes and extract Python definitions and usages from source content.

Example: a renamed module shows up with both its new and old path so
references to the old name can still be found.
"""

import ast
import os
import re

import diffimpactscout.gitrun as gitrun


class Entity(object):
    def __init__(self, name, kind, line, qualname):
        self.name = name
        self.kind = kind
        self.line = line
        self.qualname = qualname


class FileChange(object):
    def __init__(self, path, status, old_path=None, ext=None):
        self.path = path
        self.status = status
        self.old_path = old_path
        self.ext = ext


_STATUS_RE = re.compile(r"^[AMDCTR]\d*$")
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


def extract_entities(source):
    try:
        tree = ast.parse(source)
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
                    )
                )
            _walk(child, scope + [("function", child.name)], entities)
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            for target in _assign_targets(child):
                if not scope:
                    entities.append(
                        Entity(target, "module_field", getattr(child, "lineno", 0), target)
                    )
                elif scope[-1][0] == "class":
                    entities.append(
                        Entity(
                            target,
                            "class_field",
                            getattr(child, "lineno", 0),
                            _qualname(scope, target),
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