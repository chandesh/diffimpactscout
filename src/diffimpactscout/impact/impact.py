"""Blast-radius orchestration: link changed symbols to template and frontend references and emit a report.

Example: deleting an endpoint class raises a High row for every place still
referencing its old name.
"""

import ast
import json
import os
import re
import sys

import diffimpactscout.env as env
import diffimpactscout.gitrun as gitrun
import diffimpactscout.scope as scope
from diffimpactscout.config import is_excluded
from diffimpactscout.impact import reporter
from diffimpactscout.impact import reverse as reverse_mod
from diffimpactscout.impact import route_linker
from diffimpactscout.impact import python_analyzer as pa
from diffimpactscout.impact._parse import parse_quiet as _parse_quiet
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


def to_json_payload(rows, endpoints, unresolved, changed_count, hidden_count,
                    top_n=20, expand=False):
    """Build the JSON payload; additive shown/hidden_counts, backward compatible."""
    rows = list(rows or [])
    shown_count, hidden_counts = reporter.top_n_counts(rows, top_n=top_n, expand=expand)
    return {
        "changed_count": changed_count,
        "rows": rows,
        "endpoints": endpoints,
        "unresolved": unresolved,
        "hidden_count": hidden_count,
        "shown_count": shown_count,
        "hidden_counts": hidden_counts,
    }


def run_impact(root, cfg, staged=False, fast=False, json_out=False, markdown=False,
               expand=False, show_all=False, top_n=20, changes=None,
               changed_paths=None, anchor=None):
    if _skip_requested():
        return 0
    impact_cfg = cfg.get("impact") or {}
    profile = impact_cfg.get("profile") or "generic"
    from_ref, to_ref = env.pre_commit_refs()
    if anchor is None and not staged:
        anchor = scope.DevScope(root).scope_base(from_ref, to_ref)
    if changes is None:
        changes = get_file_changes(root, anchor, staged, from_ref, to_ref)
    if changed_paths is None:
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
    reverse_rows, reverse_layers, reverse_unresolved = [], {}, []
    pseudo_entities = set()
    if impact_cfg.get("reverse") and not fast:
        real_names = set(entities)
        extra_t, rows_t, layers_t, unresolved_t = reverse_mod.template_chain(
            root, cfg, impact_cfg, changes, analyses, cache, old_ref, staged, from_ref, to_ref
        )
        _merge_entities(entities, extra_t)
        extra_f, rows_f, layers_f, unresolved_f = reverse_mod.frontend_chain(
            root, cfg, impact_cfg, changes, entities, analyses, cache, old_ref, staged, from_ref, to_ref,
            experimental=bool(impact_cfg.get("experimental")),
        )
        _merge_entities(entities, extra_f)
        # Reverse render-site pseudo-entities exist to re-seed route/frontend
        # linking, not to be reference-searched: their enclosing function's
        # leaf name can be generic (e.g. "get"), which would explode
        # find_references with unrelated same-name usages. The chains already
        # emit their own rows; keep the entities (for _affected_routes) but
        # skip reference search for the ones that are not real changed entities.
        pseudo_entities = (set(extra_t) | set(extra_f)) - real_names
        reverse_rows = rows_t + rows_f
        reverse_layers = {**layers_t}
        for name, cats in layers_f.items():
            reverse_layers.setdefault(name, set()).update(cats)
        reverse_unresolved = unresolved_t + unresolved_f
    rows, endpoints, unresolved, hidden_count = _compose_rows(
        root, impact_cfg, profile, entities, analyses, changed_paths, fast, cfg,
        extra_rows=reverse_rows,
        extra_layers=reverse_layers,
        extra_unresolved=reverse_unresolved,
        pseudo_entities=pseudo_entities,
        show_all=show_all,
    )
    changed_count = len(changes)
    effective_expand = bool(expand or show_all)
    if json_out:
        sys.stdout.write(json.dumps(to_json_payload(
            rows, endpoints, unresolved, changed_count, hidden_count,
            top_n=top_n, expand=effective_expand,
        )) + "\n")
    else:
        sys.stdout.write(
            reporter.render_report(
                rows, endpoints, unresolved, changed_count,
                markdown=markdown, hidden_count=hidden_count, expand=effective_expand,
                top_n=top_n,
            )
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


def _merge_entities(entities, extra):
    for name, ent in (extra or {}).items():
        existing = entities.get(name)
        if existing is None:
            entities[name] = {
                "kind": ent.get("kind", "function"),
                "deleted": bool(ent.get("deleted")),
                "modules": set(ent.get("modules") or ()),
            }
            continue
        existing.setdefault("modules", set()).update(ent.get("modules") or ())


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


def _row_severity(row, layers, delrow, changed_paths):
    """Return (severity, reason) for a row, honoring its confidence tier.

    Untiered rows (the Python-symbol pipeline) keep the historical rules.
    Reverse rows never reach High from the >=2-layers rule: frontend changed
    rows are High, deleted/renamed stays High, backend-certain is Medium, and
    structural/heuristic rows are capped at Medium.
    """
    base = reporter.classify_severity(layers, delrow, changed_paths, row.get("path"))
    tier = row.get("tier")
    if tier is None:
        return base, reporter.severity_reason(layers, delrow, changed_paths, row.get("path"))
    if delrow:
        return "High", reporter.severity_reason(layers, delrow, changed_paths, row.get("path"))
    if tier == reverse_mod.TIER_FRONTEND and (row.get("ref") or "").startswith("changed"):
        return "High", "the file itself is in the change-set"
    if tier == reverse_mod.TIER_BACKEND:
        return "Medium", "renders a changed template"
    if base == "High":
        return "Medium", "reachable through the template/import graph"
    return base, reporter.severity_reason(layers, delrow, changed_paths, row.get("path"))


def _confidence_for_python_hit(hit, analysis, entity_mods, path):
    """Return (confidence, evidence) for one python reference hit.

    high: the referencing file binds the symbol through an import from one
    of the defining modules (import_chain lists the defining modules).
    medium: same-module reference without a qualifying import
    (import_chain holds the referencing module). low: neither resolves
    (unresolved import; hidden by default, shown under Transitive
    with --all).
    """
    mods = set(entity_mods or ())
    if not mods:
        return "low", {"unresolved": True}
    name = hit.get("name") or ""
    how = hit.get("how") or "name"
    if how == "import":
        if pa._imports_from(analysis, name, mods, path):
            return "high", {"import_chain": sorted(mods)}
    elif how == "attr":
        base = _hit_base(analysis, hit)
        head = (base or "").split(".")[0]
        if head and pa._imports_from(analysis, head, mods, path):
            return "high", {"import_chain": sorted(mods)}
    else:
        if pa._imports_from(analysis, name, mods, path):
            return "high", {"import_chain": sorted(mods)}
    if _module_of(path) in mods:
        return "medium", {"import_chain": [_module_of(path)]}
    return "low", {"unresolved": True}


def _hit_base(analysis, hit):
    """Return the base chain for a hit by matching its usage record."""
    usages = None
    if isinstance(analysis, dict):
        usages = analysis.get("usages")
    if not isinstance(usages, list):
        return ""
    for u in usages:
        if not isinstance(u, dict):
            continue
        if u.get("name") != hit.get("name"):
            continue
        if u.get("kind", "name") != hit.get("how", "name"):
            continue
        if u.get("line", 0) != hit.get("line", 0):
            continue
        return u.get("base") or ""
    return ""


def _confidence_for_tiered_row(row):
    """Return (confidence, evidence) for reverse/template/frontend rows.

    frontend tier rows name a changed file directly (high); backend tier
    rows render a changed template through a known API (medium);
    heuristic rows are transitive context (low, hidden by default).
    """
    tier = row.get("tier")
    if tier == reverse_mod.TIER_FRONTEND:
        return "high", {"import_chain": [row.get("path") or ""]}
    if tier == reverse_mod.TIER_BACKEND:
        return "medium", {"import_chain": [row.get("module") or ""]}
    if tier == reverse_mod.TIER_HEURISTIC:
        return "low", {"unresolved": True}
    return "medium", {"import_chain": [row.get("module") or ""]}


def _compose_rows(
    root, impact_cfg, profile, entities, analyses, changed_paths, fast, cfg=None,
    extra_rows=None, extra_layers=None, extra_unresolved=None, pseudo_entities=None,
    show_all=False,
):
    rows = []
    layers = {}
    for name, cats in (extra_layers or {}).items():
        layers.setdefault(name, set()).update(cats)
    rows.extend(extra_rows or [])
    for row in rows:
        if "confidence" not in row:
            conf, ev = _confidence_for_tiered_row(row)
            row["confidence"] = conf
            row["evidence"] = ev
    modules = {name: ent.get("modules") or set() for name, ent in entities.items()}
    deleted = {name for name, ent in entities.items() if ent.get("deleted")}
    weak_attr = [
        name for name, ent in entities.items() if ent.get("kind") == "class_field"
    ]
    weak = (
        set(weak_attr)
        | set(pa.DEFAULT_IGNORE_SYMBOLS)
        | set(impact_cfg.get("ignore_symbols") or ())
    )
    pseudo = set(pseudo_entities or ())
    for name, ent in entities.items():
        layers.setdefault(name, set())
        kinds = _ENTITY_KINDS.get(ent["kind"], _DEFAULT_KINDS)
        hits = pa.find_references(
            analyses,
            [name],
            kinds=kinds,
            modules=modules,
            changed_paths=changed_paths,
            weak_attr=sorted(weak),
            deleted=deleted,
            require_attr_base=(name in pseudo),
        )
        for hit in hits:
            layers[name].add("python")
            row = _python_row(hit, name, ent)
            if name in pseudo:
                # References to a reverse pseudo-entity (e.g. a URLconf
                # binding to an affected view) are heuristic context, not a
                # certain impact.
                row["tier"] = reverse_mod.TIER_HEURISTIC
            analysis = _peel(analyses.get(hit.get("path")))
            conf, ev = _confidence_for_python_hit(
                hit, analysis, modules.get(name) or set(), hit.get("path")
            )
            if row.get("tier") == reverse_mod.TIER_HEURISTIC:
                conf, ev = "low", {"unresolved": True}
            row["confidence"] = conf
            row["evidence"] = ev
            rows.append(row)
    routes = _routes(root, impact_cfg, profile, cfg)
    affected = _affected_routes(routes, analyses, entities, weak) if not fast else []
    template_refs, frontend_refs = _scoped_refs(root, impact_cfg, cfg, affected, fast)
    template_matched, _template_unresolved = route_linker.match_template_refs(
        template_refs, affected
    )
    frontend_matched, _frontend_unresolved = route_linker.match_endpoints(
        frontend_refs, affected
    )
    unresolved = list(extra_unresolved or [])
    # Stoplist-pruned generic hits (REQ-7) are drop-always: find_references
    # already pruned unqualified uses, and they are never demoted or
    # resurfaced here -- even with show_all -- so generic-name noise cannot
    # return via --all. Only confidence-low rows (unresolved imports that
    # still emitted, e.g. inside the change-set) resurface with --all.
    for hit in template_matched:
        row = _tier_linked_row(
            _linked_row(hit, entities, layers, "template", changed_paths),
            pseudo, changed_paths,
        )
        if row is not None:
            if "confidence" not in row:
                conf, ev = _confidence_for_tiered_row(row)
                row["confidence"] = conf
                row["evidence"] = ev
            rows.append(row)
    for hit in frontend_matched:
        row = _tier_linked_row(
            _linked_row(hit, entities, layers, "frontend", changed_paths),
            pseudo, changed_paths,
        )
        if row is not None:
            if "confidence" not in row:
                conf, ev = _confidence_for_tiered_row(row)
                row["confidence"] = conf
                row["evidence"] = ev
            rows.append(row)
    rows = _dedup_rows(rows)
    hidden_count = 0
    if not show_all:
        kept = []
        for r in rows:
            if r.get("tier") == reverse_mod.TIER_HEURISTIC:
                hidden_count += 1
            elif r.get("confidence") == "low":
                hidden_count += 1
            else:
                kept.append(r)
        rows = kept
    rows.sort(key=_row_key)
    for row in rows:
        name = row.pop("_entity")
        delrow = row.pop("_deleted")
        severity, reason = _row_severity(row, layers.get(name) or (), delrow, changed_paths)
        row["severity"] = severity
        row["reason"] = reason
        row["action"] = _action(severity, delrow)
        hint = "transitive" if row.get("tier") == reverse_mod.TIER_HEURISTIC else "direct"
        if row.get("confidence") == "low":
            hint = "transitive"
        finished = finish_row(
            row, severity=severity,
            changed_paths=changed_paths, section_hint=hint, deleted=delrow,
        )
        row.update(finished)
        if severity in ("Medium", "High"):
            chain = (row.get("evidence") or {}).get("import_chain") or []
            row.update(attach_why_action(
                row, symbol=name, import_chain=list(chain),
                deleted=delrow,
                test_path=_test_path_for_row(root, row.get("path")),
            ))
    endpoints = _endpoint_rows(affected)
    return rows, endpoints, unresolved, hidden_count


def _endpoint_rows(routes):
    seen = set()
    out = []
    for r in routes:
        key = r.full or r.path
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "url": r.full or r.path,
                "handler": r.handler or "",
                "name": r.name or "",
                "module": r.module or "",
            }
        )
    return out


def _usage_index(analyses):
    index = {}
    for path, entry in (analyses or {}).items():
        analysis = _peel(entry)
        if not isinstance(analysis, dict):
            continue
        for u in analysis.get("usages") or []:
            name = u.get("name")
            if name:
                index.setdefault(name, []).append((path, analysis, u))
    return index


def _hop_callers(index, name, mods, weak):
    callers = {}
    for path, analysis, u in index.get(name, []):
        if u.get("kind") == "str":
            continue
        if u.get("kind") == "attr" and not u.get("base"):
            continue
        if not pa._usage_resolves(analysis, u, name, mods, path, weak):
            continue
        ctx = u.get("ctx_qname") or ""
        if not ctx:
            continue
        fn = ctx.rsplit(".", 1)[-1]
        callers.setdefault(fn, set()).add(_module_of(path))
    return callers


def _handler_leaf(handler):
    if not handler:
        return None
    h = handler
    if h.endswith("()"):
        h = h[:-2]
    if h.endswith(".as_view"):
        h = h[: -len(".as_view")]
    return h.rsplit(".", 1)[-1]


def _route_binds_handler(urls_entry, handler, mods, path, weak):
    if not mods:
        return True
    if _module_of(path) in mods:
        return True
    analysis = _peel(urls_entry)
    if not isinstance(analysis, dict):
        return True
    for u in analysis.get("usages") or []:
        if u.get("name") != handler:
            continue
        if u.get("kind") == "str":
            continue
        if pa._usage_resolves(analysis, u, handler, mods, path, weak):
            return True
    return False


def _affected_routes(routes, analyses, entities, weak):
    """Routes reachable from the change via a backward caller closure."""
    if not routes:
        return []
    index = _usage_index(analyses)
    reached = set(entities.keys())
    func_mods = {name: ent.get("modules") or set() for name, ent in entities.items()}
    queue = list(entities.keys())
    max_nodes = 500
    hops = 0
    while queue and hops < 5 and len(reached) < max_nodes:
        hops += 1
        nxt = []
        for name in queue:
            mods = func_mods.get(name) or set()
            if not mods:
                continue
            for caller, fmods in _hop_callers(index, name, mods, weak).items():
                if caller in reached:
                    continue
                reached.add(caller)
                func_mods.setdefault(caller, set()).update(fmods)
                nxt.append(caller)
        queue = nxt
    affected = []
    for route in routes:
        handler = _handler_leaf(route.handler)
        if not handler or handler not in func_mods:
            continue
        urls = analyses.get(route.module)
        if _route_binds_handler(urls, handler, func_mods[handler], route.module, weak):
            affected.append(route)
    return affected


def _norm_needle(full):
    if not full:
        return None
    s = full.lstrip("^")
    for marker in ("<", "{", "(?"):
        idx = s.find(marker)
        if idx != -1:
            s = s[:idx]
            break
    s = s.lstrip("/").rstrip("$")
    return s or None


def _scoped_refs(root, impact_cfg, cfg, affected, fast):
    if fast:
        return [], []
    names = {r.name for r in affected if r.name}
    template_refs = route_linker.extract_template_refs(
        root, impact_cfg.get("template_globs"), cfg=cfg, names=names
    )
    needles = [_norm_needle(r.full or r.path) for r in affected]
    needles = [n for n in needles if n]
    frontend_refs = route_linker.extract_frontend_refs(
        root,
        impact_cfg.get("frontend_globs"),
        cfg=cfg,
        vendor=True,
        needles=needles or None,
    )
    return template_refs, frontend_refs


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
    """Sort key for deterministic row order: path, category, ref."""
    return (row.get("path") or "", row.get("category") or "", row.get("ref") or "")


def _line_of(row):
    """Extract the 1-based line number from a row's ref text.

    Parses ``at <N>`` (python/frontend rows) and ``line <N>`` (render-site
    rows); falls back to the row's ``line`` field, else 0.
    """
    ref = row.get("ref") or ""
    m = re.search(r"at (\d+)", ref)
    if m:
        return int(m.group(1))
    m = re.search(r"line (\d+)", ref)
    if m:
        return int(m.group(1))
    return row.get("line") or 0


def finish_row(row, severity, changed_paths, section_hint=None, deleted=False):
    """Attach display fields to a composed row: severity, badge, section, key.

    ``badge`` is ``Changed`` when the row's path is in the change-set, else
    ``Impacted``. ``section`` is ``Breaking`` for deleted/high rows,
    ``Transitive`` when ``section_hint`` is ``transitive``, else ``Direct``.
    ``key`` is the stable ``path:line`` string. Returns a new dict.
    """
    row = dict(row)
    row["severity"] = severity
    row["badge"] = "Changed" if (row.get("path") in (changed_paths or ())) else "Impacted"
    if deleted or severity == "High":
        row["section"] = "Breaking"
    elif section_hint == "transitive":
        row["section"] = "Transitive"
    else:
        row["section"] = "Direct"
    row["key"] = "%s:%s" % (row.get("path") or "", _line_of(row))
    return row


def _python_row(hit, name, ent):
    path = hit.get("path") or ""
    ctx = hit.get("ctx_qname") or ""
    return {
        "path": path,
        "module": ctx if ctx else _dir_of(path),
        "category": "python",
        "ref": "%s (%s at %d)" % (hit.get("name"), hit.get("how"), hit.get("line", 0)),
        "_entity": name,
        "_deleted": bool(ent.get("deleted")),
    }


def _template_row(hit, name, ent):
    path = hit.get("file") or ""
    return {
        "path": path,
        "module": _dir_of(path),
        "category": "template",
        "ref": "{%% url '%s' %%}" % (hit.get("ref"),),
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
        "ref": 'http.%s("%s") at %d' % (method, hit.get("ref"), line),
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


def _tier_linked_row(row, pseudo, changed_paths):
    """Tag a linked route row heuristic when its handler is a reverse pseudo-entity.

    A route is bound to a reverse pseudo-entity only because its view renders a
    changed template, so its template/frontend consumers are reached through the
    template graph -- transitive context, hidden by default (shown with --all).
    A row whose own file is in the change-set stays visible regardless.
    """
    if (
        row is not None
        and row.get("_entity") in pseudo
        and row.get("path") not in changed_paths
    ):
        row["tier"] = reverse_mod.TIER_HEURISTIC
    return row


def _linked_row(hit, entities, layers, category, changed_paths):
    """Build a template/frontend row for a matched affected route.

    Attributes the row to the route's handler (a changed entity or a function
    reached through the caller closure) so consumers of transitively-affected
    endpoints are surfaced, not silently dropped.
    """
    route = hit.get("route")
    name = _handler_entity(route, entities)
    if name is None:
        handler = _handler_leaf(route.handler) if route else None
        if not handler:
            return None
        name = handler
        ent = {"kind": "function", "deleted": False}
    else:
        ent = entities[name]
    layers.setdefault(name, set()).add(category)
    if category == "template":
        return _template_row(hit, name, ent)
    return _frontend_row(hit, name, ent)


def _routes(root, impact_cfg, profile, cfg=None):
    if profile == "django":
        return route_linker.extract_django_routes(
            root, impact_cfg.get("urls_globs"), cfg=cfg
        )
    if profile == "fastapi":
        return route_linker.extract_fastapi_routes(
            root, impact_cfg.get("urls_globs"), cfg=cfg
        )
    return []


def _action(severity, deleted):
    # Medium/High actions are owned by attach_why_action (which overwrites
    # row["action"); Low rows need no action. Deleted rows are always High,
    # so the old deleted/Medium/High branches here could never survive.
    return "ok"


def attach_why_action(row, symbol, import_chain, deleted, test_path):
    """Attach REQ-9 why_matters/suggested_action to a Medium+ row (new dict).

    Deleted rows suggest ``remove import``; rows with a conventional unit
    test suggest ``run <test path>``; otherwise ``update call site``. The
    row's ``action`` is set to the suggested action. Reverse pseudo-symbol
    prefixes (``changed:``/``extends:``) are stripped for user-facing text.
    """
    row = dict(row)
    if isinstance(symbol, str) and symbol.split(":", 1)[0] in ("changed", "extends"):
        symbol = symbol.split(":", 1)[-1]
    chain = " via " + "->".join(import_chain) if import_chain else " (unresolved import)"
    if deleted:
        row["why_matters"] = "%s uses deleted symbol %s%s" % (row.get("ref") or "", symbol, chain)
        row["suggested_action"] = "remove import"
    else:
        row["why_matters"] = "%s uses %s%s" % (row.get("ref") or "", symbol, chain)
        row["suggested_action"] = "run %s" % test_path if test_path else "update call site"
    row["action"] = row["suggested_action"]
    return row


def _test_path_for_row(root, path):
    """Return the conventional unit-test path for a row, or None when absent.

    Maps ``pkg/mod.py`` to ``tests/test_mod.py`` when that file exists on
    disk; anything else (non-python rows, missing files) yields None so the
    caller falls back to ``update call site``.
    """
    base = os.path.basename(path or "")
    if not base or not base.endswith(".py") or not root:
        return None
    stem = base[:-3]
    if not stem or stem.startswith("test_"):
        return None
    candidate = os.path.join("tests", "test_%s.py" % stem)
    try:
        found = os.path.isfile(os.path.join(root, candidate))
    except (OSError, ValueError):
        return None
    return candidate if found else None


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
        tree = _parse_quiet(source)
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