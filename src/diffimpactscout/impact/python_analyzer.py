"""AST analysis of Python files: definitions, usages, and reference lookup.

Example: renaming a class field reports attribute usages of the old name
from other modules as references.
"""

import ast
import hashlib
import os

from diffimpactscout.impact._parse import parse_quiet as _parse_quiet

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def analyze_source(src):
    """Parse source text and build a per-file symbol analysis.

    Returns a dict with keys:
      hash   -- sha1 hex digest of the source text
      defs   -- {name: {"kind", "line", "end_line", "qname"}}
      usages -- [{"line", "name", "kind", "ctx_qname", "ctx_kind"}]
    Returns None when the source cannot be parsed.

    defs covers functions, classes, methods, module fields and class
    fields. usages covers name loads ("name"), attribute loads ("attr")
    and imports ("import"). Definitions and assignment targets are never
    recorded as usages. An attribute load records only the attribute name
    (obj.status -> name "status", kind "attr"); the base object name is
    not recorded so a field search matches obj.status exactly once.
    Imports record the imported names, with dotted module names reduced
    to their top-level part, plus the raw module string ("module") and
    relative-import level ("level") so a lookup can resolve whether a
    file actually binds a name from a given module. ctx_qname is the
    dotted enclosing scope ("" at module level) and ctx_kind is one of
    "module", "function", "class" or "method".
    """
    if isinstance(src, bytes):
        src = src.decode("utf-8", "replace")
    tree = _parse(src)
    if tree is None:
        return None
    defs = {}
    usages = []
    _walk(tree, [], defs, usages)
    return {"hash": _content_hash(src), "defs": defs, "usages": usages}


def analyze_path(path, root, cache):
    """Analyze one repo file, skipping re-parse for unchanged content.

    path is the repo-root-relative posix path (as git emits paths); root is
    the absolute repo root used to resolve the file on disk. cache is a
    SymbolCache, a plain dict, or None; cache entries are
    {"hash": sha1 hex, "analysis": analyze_source result}. On a cache hit
    (the stored hash matches the file's current hash) the stored analysis
    is returned without re-parsing; otherwise the file is analyzed and the
    entry stored under path. Returns the analysis dict, or None on a
    read/parse failure. The cache's loaded data is mutated in place so a
    caller can SymbolCache.save() once after scanning all files.
    """
    full = path
    if root:
        full = os.path.join(root, path)
    text = _read_text(full)
    if text is None:
        return None
    data = _cache_data(cache)
    entry = None
    if data is not None:
        entry = data.get(path)
    if isinstance(entry, dict) and entry.get("hash") == _content_hash(text):
        analysis = entry.get("analysis")
        if isinstance(analysis, dict):
            return analysis
    analysis = analyze_source(text)
    if analysis is None:
        return None
    if data is not None:
        data[path] = {"hash": analysis["hash"], "analysis": analysis}
    return analysis


def find_references(analyses, names, kinds=None, modules=None, changed_paths=None, weak_attr=()):
    """Return reference hits for the given entity names, optionally by kind.

    analyses maps a repo-root-relative path to either a cache entry
    ({"hash": ..., "analysis": {...}}) or a raw analysis dict. names is an
    iterable of entity names to search for (changed, deleted, or renamed
    entities). kinds is an optional iterable of usage kinds ("name", "attr",
    "import") to match; when None (default) all kinds are matched. Every
    matching usage becomes a hit dict {"name", "path", "line", "how",
    "ctx_qname", "ctx_kind"} sorted deterministically by (path, line, name,
    how). Empty when nothing matches.

    modules, when given, maps each entity name to the set of dotted module
    paths where that entity was defined in the change-set. changed_paths,
    when given, is the iterable of repo-root-relative paths in the
    change-set. When both are supplied a hit that is not itself in a
    changed file is kept only when its file actually binds the entity name
    from one of those modules, pruning same-name false positives across
    unrelated files (e.g. a generic 'create' method). Attribute loads are
    verified through their base chain (``views.orders`` resolves when the
    file imports ``views`` from the changed module); attributes inside
    weak_attr names (typically class fields accessed through arbitrary
    object variables) bypass the import check.

    Caller contract:
      changed class_field    -> kinds={"attr"}
      changed function/module_field -> kinds={"name","attr","import"}
      changed method         -> kinds={"name","attr"}
      changed class          -> kinds={"name","attr","import"}
    """
    wanted = set(names)
    kinds = None if kinds is None else set(kinds)
    mods = modules or {}
    changed = set(changed_paths or ())
    weak = set(weak_attr or ())
    hits = []
    for path, entry in (analyses or {}).items():
        analysis = entry
        if isinstance(entry, dict) and isinstance(entry.get("analysis"), dict):
            analysis = entry["analysis"]
        if not isinstance(analysis, dict):
            continue
        usages = analysis.get("usages")
        if not isinstance(usages, list):
            continue
        for usage in usages:
            name = usage.get("name")
            if name not in wanted:
                continue
            how = usage.get("kind", "name")
            if kinds is not None and how not in kinds:
                continue
            if changed and name in mods:
                if path not in changed and not _usage_resolves(
                    analysis, usage, name, mods.get(name) or set(), path, weak
                ):
                    continue
            hits.append(
                {
                    "name": name,
                    "path": path,
                    "line": usage.get("line", 0),
                    "how": how,
                    "ctx_qname": usage.get("ctx_qname", ""),
                    "ctx_kind": usage.get("ctx_kind", "module"),
                }
            )
    hits.sort(key=lambda h: (h["path"], h["line"], h["name"], h["how"]))
    return hits


def _usage_resolves(analysis, usage, name, entity_mods, path, weak):
    if not entity_mods:
        return True
    if usage.get("kind") != "attr":
        return _imports_from(analysis, name, entity_mods, path)
    if name in weak:
        return True
    base = usage.get("base") or ""
    if not base:
        return True
    return _imports_from(analysis, base.split(".")[0], entity_mods, path)


def _imports_from(analysis, name, entity_mods, path):
    """Return True when the file imports ``name`` from any of ``entity_mods``.

    Resolves relative ImportFrom levels against the file's own package so
    ``from . import views`` inside ``app/service.py`` resolves to the
    ``app.views`` module.
    """
    if not entity_mods:
        return True
    usages = (analysis or {}).get("usages")
    if not isinstance(usages, list):
        return False
    for usage in usages:
        if usage.get("kind") != "import":
            continue
        imported = usage.get("name")
        if imported != name and usage.get("alias") != name:
            continue
        module = _import_module(usage, path)
        if module in entity_mods:
            return True
        if imported and ".".join([module, imported]) in entity_mods:
            return True
    return False


def _import_module(usage, path):
    """Resolve an import usage to the dotted module path it binds or loads.

    Absolute imports (``from app.views import orders``) resolve to the raw
    module. Relative imports resolve against the importing file's own
    package; ``from . import views`` inside ``app/urls.py`` binds the name
    ``views`` to the submodule ``app.views``.
    """
    module = usage.get("module") or ""
    level = int(usage.get("level") or 0)
    name = usage.get("name") or ""
    if level == 0:
        return module
    parts = (path or "").split("/")
    depth = max(len(parts) - 1, 0)
    if parts and parts[-1] == "__init__.py":
        depth -= 1
    pkg_depth = depth - (level - 1)
    pkg = ".".join(parts[:pkg_depth]) if pkg_depth > 0 else ""
    if module:
        return ".".join([pkg, module]) if pkg else module
    if name:
        return ".".join([pkg, name]) if pkg else name
    return pkg


def _walk(node, scope, defs, usages):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNCS):
            _visit_func(child, scope, defs, usages)
        elif isinstance(child, ast.ClassDef):
            _record_def(defs, child.name, "class", _qualname(scope, child.name), child)
            _walk(child, scope + [("class", child.name)], defs, usages)
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            _visit_assign(child, scope, defs, usages)
        elif isinstance(child, ast.Import):
            for alias in child.names:
                u = _usage(child, alias.name.split(".")[0], "import", scope)
                u["module"] = alias.name
                u["level"] = 0
                if alias.asname:
                    u["alias"] = alias.asname
                usages.append(u)
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                u = _usage(child, alias.name, "import", scope)
                u["module"] = child.module or ""
                u["level"] = getattr(child, "level", 0) or 0
                if alias.asname:
                    u["alias"] = alias.asname
                usages.append(u)
        elif isinstance(child, ast.Attribute):
            _visit_attribute(child, scope, defs, usages)
        elif isinstance(child, ast.Name):
            if _is_load(child):
                usages.append(_usage(child, child.id, "name", scope))
        else:
            _walk(child, scope, defs, usages)


def _visit_func(child, scope, defs, usages):
    kind = None
    if not scope:
        kind = "function"
    elif scope[-1][0] == "class":
        kind = "method"
    if kind is not None:
        _record_def(defs, child.name, kind, _qualname(scope, child.name), child)
    _walk(child, scope + [("function", child.name)], defs, usages)


def _visit_attribute(node, scope, defs, usages):
    if _is_load(node):
        u = _usage(node, node.attr, "attr", scope)
        u["base"] = _attr_base(node)
        usages.append(u)
    value = node.value
    if isinstance(value, ast.Name):
        return
    if isinstance(value, ast.Attribute):
        _visit_attribute(value, scope, defs, usages)
    else:
        _walk(value, scope, defs, usages)


def _attr_base(node):
    """Return the dotted base chain of an attribute load, e.g. ``views.orders`` -> ``views``."""
    parts = []
    current = node.value
    while isinstance(current, ast.Attribute) and _is_load(current):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _visit_assign(child, scope, defs, usages):
    kind = None
    if not scope:
        kind = "module_field"
    elif scope[-1][0] == "class":
        kind = "class_field"
    if kind is not None:
        for target in _assign_targets(child):
            _record_def(defs, target, kind, _qualname(scope, target), child)
    _walk(child, scope, defs, usages)


def _record_def(defs, name, kind, qname, node):
    line = getattr(node, "lineno", 0)
    defs[name] = {"kind": kind, "line": line, "end_line": _end_line(node), "qname": qname}


def _usage(node, name, kind, scope):
    return {
        "line": getattr(node, "lineno", 0),
        "name": name,
        "kind": kind,
        "ctx_qname": _ctx_qname(scope),
        "ctx_kind": _ctx_kind(scope),
    }


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


def _ctx_qname(scope):
    return ".".join([n for _kind, n in scope])


def _ctx_kind(scope):
    if not scope:
        return "module"
    kind, _name = scope[-1]
    if kind == "function":
        if len(scope) >= 2 and scope[-2][0] == "class":
            return "method"
        return "function"
    return "class"


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


def _is_load(node):
    return isinstance(getattr(node, "ctx", None), ast.Load)


def _parse(src):
    try:
        return _parse_quiet(src)
    except (SyntaxError, ValueError, TypeError):
        return None


def _content_hash(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _read_text(full):
    try:
        with open(full, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return None
    return data.decode("utf-8", "replace")


def _cache_data(cache):
    if cache is None:
        return None
    if isinstance(cache, dict):
        return cache
    loader = getattr(cache, "load", None)
    if callable(loader):
        data = loader()
        if isinstance(data, dict):
            return data
    return None