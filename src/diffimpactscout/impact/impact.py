"""Blast-radius orchestration: link changed symbols to template and frontend references and emit a report.

Example: deleting an endpoint class raises a High row for every place still
referencing its old name.
"""

import ast
import json
import os
import sys

import diffimpactscout.env as env
import diffimpactscout.gitrun as gitrun
import diffimpactscout.scope as scope
from diffimpactscout.config import is_excluded
from diffimpactscout.impact import reporter
from diffimpactscout.impact import route_linker
from diffimpactscout.impact import python_analyzer as pa
from diffimpactscout.impact.cache import SymbolCache
from diffimpactscout.impact.diff_parser import (
    extract_entities,
    get_changed_lines,
    get_file_changes,
    read_path_at_ref,
)

_ENTITY_KINDS = {
    "class_field": ("attr",),
    "function": ("name", "attr", "import"),
    "module_field": ("name", "attr", "import"),
    "method": ("name", "attr"),
    "class": ("name", "attr", "import"),
}

_DEFAULT_KINDS = ("name",)


def run_impact(root, cfg, staged=False, fast=False, json_out=False, markdown=False):
    if _skip_requested():
        return 0
    impact_cfg = cfg.get("impact") or {}
    profile = impact_cfg.get("profile") or "generic"
    from_ref, to_ref = env.pre_commit_refs()
    anchor = None
    if not staged:
        anchor = scope.DevScope(root).scope_base(from_ref, to_ref)
    changes = get_file_changes(root, anchor, staged, from_ref, to_ref)
    changed_paths = [c.path for c in changes]
    if staged:
        old_ref = "HEAD"
    elif anchor:
        old_ref = anchor
    else:
        old_ref = from_ref
    entities = _changed_entities(
        changes, root, old_ref, anchor, from_ref, to_ref, staged
    )
    cache_file = impact_cfg.get("cache_file") or ".impact_analysis_cache.json"
    if root and not os.path.isabs(cache_file):
        cache_file = os.path.join(root, cache_file)
    cache = SymbolCache(cache_file)
    py_files = _python_files(root, cfg)
    if fast:
        sys.stderr.write(
            "diffimpactscout: fast mode; template/frontend scanning skipped, "
            "python scans limited to import-linked files\n"
        )
        scan = _fast_subset(root, py_files, cache.load(), entities, changes)
    else:
        scan = py_files
    for path in scan:
        pa.analyze_path(path, root, cache)
    cache.prune(py_files)
    cache.save(cache.load())
    analyses = cache.load()
    rows, unresolved = _compose_rows(
        root, impact_cfg, profile, entities, analyses, changed_paths, fast
    )
    changed_count = len(changes)
    if json_out:
        sys.stdout.write(
            json.dumps(
                {
                    "changed_count": changed_count,
                    "rows": rows,
                    "unresolved": unresolved,
                }
            )
            + "\n"
        )
    else:
        sys.stdout.write(
            reporter.render_report(rows, unresolved, changed_count, markdown=markdown)
        )
    strict_env = os.environ.get("IMPACT_CHECK_STRICT")
    tty = reporter.interactive_tty()
    block, reason = reporter.should_block(strict_env, tty)
    if block:
        if tty:
            block = not reporter.confirm("Proceed with push? (Y/n)")
        else:
            sys.stderr.write("diffimpactscout: %s\n" % reason)
    return 1 if block else 0


def _skip_requested():
    return env.skip_requested()


def _changed_entities(
    changes, root, old_ref, anchor=None, from_ref=None, to_ref=None, staged=False
):
    entities = {}
    for change in changes:
        if change.ext != "py":
            continue
        old_path = change.old_path
        if old_path is None or change.status[0] != "R":
            old_path = change.path
        old_src = read_path_at_ref(root, old_path, old_ref) if old_ref else ""
        new_src = _read_disk(root, change.path)
        old_entities = extract_entities(old_src)
        new_entities = extract_entities(new_src)
        old_lines, new_lines = get_changed_lines(
            root, change, anchor, from_ref, to_ref, staged
        )
        old_map = {e.name: e for e in old_entities}
        new_map = {e.name: e for e in new_entities}
        if new_lines is not None:
            new_map = {
                name: e
                for name, e in new_map.items()
                if _entity_overlaps_changed(e, new_lines)
            }
        if old_lines is not None:
            old_map = {
                name: e
                for name, e in old_map.items()
                if _entity_overlaps_changed(e, old_lines)
            }
        module = _module_of(change.path)
        for name, ent in new_map.items():
            if name not in entities:
                entities[name] = {"kind": ent.kind, "deleted": False, "modules": set()}
            elif entities[name].get("deleted"):
                entities[name] = {"kind": ent.kind, "deleted": False, "modules": set()}
            entities[name]["modules"].add(module)
        old_module = _module_of(old_path)
        for name, ent in old_map.items():
            if name not in new_map:
                if name not in entities:
                    entities[name] = {"kind": ent.kind, "deleted": True, "modules": set()}
                entities[name]["modules"].add(old_module)
    return entities


def _module_of(path):
    if not path:
        return ""
    stem = path[:-3] if path.endswith(".py") else path
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _entity_overlaps_changed(entity, changed_lines):
    if not changed_lines:
        return False
    start = getattr(entity, "line", 0)
    end = getattr(entity, "end_line", None) or start
    for line_num in range(start, end + 1):
        if line_num in changed_lines:
            return True
    return False


def _compose_rows(root, impact_cfg, profile, entities, analyses, changed_paths, fast):
    rows = []
    layers = {}
    modules = {name: ent.get("modules") or set() for name, ent in entities.items()}
    weak_attr = [
        name for name, ent in entities.items() if ent.get("kind") == "class_field"
    ]
    for name, ent in entities.items():
        kinds = _ENTITY_KINDS.get(ent["kind"], _DEFAULT_KINDS)
        hits = pa.find_references(
            analyses,
            [name],
            kinds=kinds,
            modules=modules,
            changed_paths=changed_paths,
            weak_attr=weak_attr,
        )
        layers.setdefault(name, set())
        for hit in hits:
            layers[name].add("python")
            rows.append(_python_row(hit, name, ent))
    routes = _routes(root, impact_cfg, profile)
    if fast:
        template_refs = []
        frontend_refs = []
    else:
        template_refs = route_linker.extract_template_refs(
            root, impact_cfg.get("template_globs")
        )
        frontend_refs = route_linker.extract_frontend_refs(
            root, impact_cfg.get("frontend_globs")
        )
    template_matched, template_unresolved = route_linker.match_template_refs(
        template_refs, routes
    )
    frontend_matched, frontend_unresolved = route_linker.match_endpoints(
        frontend_refs, routes
    )
    unresolved = list(template_unresolved)
    unresolved.extend(frontend_unresolved)
    for hit in template_matched:
        name = _handler_entity(hit.get("route"), entities)
        if name is None:
            continue
        layers.setdefault(name, set()).add("template")
        rows.append(_template_row(hit, name, entities[name]))
    for hit in frontend_matched:
        name = _handler_entity(hit.get("route"), entities)
        if name is None:
            continue
        layers.setdefault(name, set()).add("frontend")
        rows.append(_frontend_row(hit, name, entities[name]))
    rows = _dedup_rows(rows)
    rows.sort(key=_row_key)
    for row in rows:
        name = row.pop("_entity")
        deleted = row.pop("_deleted")
        row["severity"] = reporter.classify_severity(
            layers.get(name) or (), deleted, changed_paths, row.get("path")
        )
        row["reason"] = reporter.severity_reason(
            layers.get(name) or (), deleted, changed_paths, row.get("path")
        )
        row["action"] = _action(row["severity"], deleted)
    return rows, unresolved


def _dedup_rows(rows):
    out = []
    seen = set()
    for row in rows:
        key = (row.get("path") or "", row.get("category") or "", row.get("ref") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _row_key(row):
    return (row.get("path") or "", row.get("category") or "", row.get("ref") or "")


def _python_row(hit, name, ent):
    path = hit.get("path") or ""
    ctx = hit.get("ctx_qname") or ""
    return {
        "path": path,
        "module": ctx if ctx else _dir_of(path),
        "category": "python",
        "ref": "%s (%s at %s:%d)" % (hit.get("name"), hit.get("how"), path, hit.get("line", 0)),
        "_entity": name,
        "_deleted": bool(ent.get("deleted")),
    }


def _template_row(hit, name, ent):
    path = hit.get("file") or ""
    return {
        "path": path,
        "module": _dir_of(path),
        "category": "template",
        "ref": "{%% url '%s' %%} at %s" % (hit.get("ref"), path),
        "_entity": name,
        "_deleted": bool(ent.get("deleted")),
    }


def _frontend_row(hit, name, ent):
    path = hit.get("file") or ""
    method = hit.get("method") or "get"
    line = hit.get("line") or 0
    return {
        "path": path,
        "module": _dir_of(path),
        "category": "frontend",
        "ref": 'http.%s("%s") at %s:%d' % (method, hit.get("ref"), path, line),
        "_entity": name,
        "_deleted": bool(ent.get("deleted")),
    }


def _handler_entity(route, entities):
    if route is None or not route.handler:
        return None
    handler = route.handler
    if handler.endswith("()"):
        handler = handler[:-2]
    if handler.endswith(".as_view"):
        handler = handler[:-len(".as_view")]
    base = handler.rsplit(".", 1)[-1]
    if base in entities:
        return base
    return None


def _routes(root, impact_cfg, profile):
    if profile == "django":
        return route_linker.extract_django_routes(root, impact_cfg.get("urls_globs"))
    if profile == "fastapi":
        return route_linker.extract_fastapi_routes(root, impact_cfg.get("urls_globs"))
    return []


def _action(severity, deleted):
    if deleted:
        return "verify dangling references"
    if severity == "High":
        return "review/verify"
    if severity == "Medium":
        return "verify"
    return "ok"


def _python_files(root, cfg):
    files = []
    for path in gitrun.git_nul(["ls-files", "-z", "*.py"], root):
        if not is_excluded(path, cfg, root):
            files.append(path)
    return files


def _fast_subset(root, py_files, cache_data, entities, changes):
    seed = set(entities.keys())
    for change in changes:
        if change.ext == "py":
            seed.add(change.path[:-3].rsplit("/", 1)[-1])
    index = {}
    for path in py_files:
        analysis = _peel(cache_data.get(path))
        if isinstance(analysis, dict):
            index[path] = _analysis_imports(analysis)
        else:
            index[path] = _import_names(_read_disk(root, path))
    subset = set()
    for change in changes:
        if change.ext == "py":
            subset.add(change.path)
    for path in py_files:
        if index.get(path) and (index[path] & seed):
            subset.add(path)
    return sorted(subset)


def _peel(entry):
    if isinstance(entry, dict):
        analysis = entry.get("analysis")
        if isinstance(analysis, dict):
            return analysis
    return entry


def _analysis_imports(analysis):
    names = set()
    if not isinstance(analysis, dict):
        return names
    for usage in analysis.get("usages") or []:
        if usage.get("kind") == "import" and usage.get("name"):
            names.add(usage["name"])
    return names


def _import_names(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


def _read_disk(root, path):
    if not path:
        return ""
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return ""
    return data.decode("utf-8", "replace")


def _dir_of(path):
    return os.path.dirname(path or "") or ""