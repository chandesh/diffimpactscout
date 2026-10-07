"""Reverse impact chains: changed templates and frontend files mapped back to Python render sites.

Example: a diff that touches only a template produces rows for the views
that render it, its route(s), and the templates extending it.
"""

import bisect
import os
import re

import diffimpactscout.gitrun as gitrun
from diffimpactscout.config import is_excluded, _matches_glob
from diffimpactscout.impact.route_linker import (
    HTTP_CALL_RE,
    QUOTED_LITERAL_RE,
    _is_url_like,
    _strip_comments,
    _read_text,
)

# Matches Django {% extends %} tags with a STATIC template name.
#   Matches:  {% extends 'common/base.html' %}   -> captures "common/base.html"
#             {% extends "shop/nav.html" %}      -> captures "shop/nav.html"
#   Skips:    {% extends base_template %}        (variable; no quotes -> not a static name)
_EXTENDS_RE = re.compile(r"{%-?\s*extends\s+['\"]([^'\"\n]+)['\"]")

# Matches Django {% include %} tags with a STATIC template name.
#   Matches:  {% include 'shop/nav.html' %}      -> captures "shop/nav.html"
#             {% include "dialogs.html" %}       -> captures "dialogs.html"
#   Skips:    {% include block.template_name %}  (variable; recorded as dynamic instead)
_INCLUDE_RE = re.compile(r"{%-?\s*include\s+['\"]([^'\"\n]+)['\"]")

# Matches {% static %} asset tags (Django staticfiles).
#   Matches:  {% static 'shop/app.js' %}         -> captures "shop/app.js"
#             {% static "css/site.css" %}        -> captures "css/site.css"
_STATIC_RE = re.compile(r"{%-?\s*static\s+['\"]([^'\"\n]+)['\"]")

# Matches the src attribute of <script> tags (case-insensitive), with or
# without other attributes before src.
#   Matches:  <script src="{{ MEDIA_URL }}shop/app.js?v=1.2"></script>
#             <script type="text/javascript" src='/x/y.js' defer></script>
#   Skips:    <script>inline code</script>       (no src attribute)
_SCRIPT_SRC_RE = re.compile(r"<script[^>]*\ssrc\s*=\s*['\"]([^'\"\n]+)['\"]", re.I)

# Matches the href attribute of <link> tags (stylesheets, favicons, ...).
#   Matches:  <link rel="stylesheet" href="{{ STATIC_URL }}css/site.css">
#   Skips:    <a href="/somewhere/">             (anchor tags, not link tags)
_LINK_HREF_RE = re.compile(r"<link[^>]*\shref\s*=\s*['\"]([^'\"\n]+)['\"]", re.I)

# Matches Angular component templateUrl config entries.
#   Matches:  templateUrl: './product.html'      -> captures "./product.html"
#             templateUrl: 'shop/page.html'      -> captures "shop/page.html"
_TEMPLATEURL_RE = re.compile(r"templateUrl\s*:\s*['\"]([^'\"\n]+)['\"]")

# Matches whole HTML comments so commented-out tags never produce matches.
#   Matches:  <!-- <script src="old.js"></script> -->   (removed before scanning)
#             <!--[if IE]><script src="shim.js"></script><![endif]-->
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# Matches Django {% include %} tags whose argument is NOT a quoted
# static name, i.e. a computed/dynamic include.
#   Matches:  {% include block.template_name %}   (dynamic)
#             {% include fragment_path %}
#   Skips:    {% include 'shop/nav.html' %}       (quoted static name)
_DYNAMIC_INCLUDE_RE = re.compile(r"{%-?\s*include\s+[^'\"%\n]+%}")


def template_name_of(path):
    """Return the Django template name for a repo path, or None.

    The name is the path after the last "templates/" component, covering
    both APP_DIRS and project DIRS layouts without reading Django settings.
    """
    if not path:
        return None
    parts = path.split("/")
    idx = None
    for i, part in enumerate(parts):
        if part == "templates":
            idx = i
    if idx is None:
        return None
    return "/".join(parts[idx + 1:]) or None


def _strip_html_comments(text):
    return _HTML_COMMENT_RE.sub("", text)


def analyze_template_graph(root, path, cache, cfg):
    """Return {"extends", "includes", "assets"} for one template, hash-gated.

    extends/includes are static-tag names only; assets are normalized
    asset paths (query stripped, prefixes stripped, comments ignored,
    dynamic {{ ... }} refs ignored). The returned dict has exactly those
    three keys on BOTH cache hits and misses (never "hash" or "graph").
    The graph is stored in the "templates" cache section keyed by path as
    {"hash": sha1 hex, "prefixes": the effective prefix list, "graph": the
    three-key dict}; the entry is trusted only when its "graph" is a dict
    AND its "prefixes" still match, so changing asset_url_prefixes rebuilds
    the graph even when the file content is unchanged.
    """
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return {"extends": [], "includes": [], "assets": []}
    digest = _digest(text)
    prefixes = _asset_prefixes(cfg)
    section = cache.load_section("templates")
    entry = section.get(path)
    if (
        isinstance(entry, dict)
        and entry.get("hash") == digest
        and entry.get("prefixes") == list(prefixes)
    ):
        cached = entry.get("graph")
        if isinstance(cached, dict):
            return cached
    graph = _template_graph_from_text(text, prefixes)
    section[path] = {
        "hash": digest,
        "prefixes": list(prefixes),
        "graph": _graph_only(graph),
    }
    cache.save_section("templates", section)
    return _graph_only(graph)


def _digest(text):
    from diffimpactscout.impact.python_analyzer import _content_hash

    return _content_hash(text)


def _asset_prefixes(cfg):
    """Effective asset URL prefixes: DEFAULT_ASSET_PREFIXES plus any user list.

    A user-supplied ``impact.asset_url_prefixes`` APPENDS to the built-in
    defaults (it cannot replace or clear them); duplicates are dropped.
    """
    impact = (cfg or {}).get("impact") or {}
    user = impact.get("asset_url_prefixes")
    if not isinstance(user, (list, tuple)):
        return DEFAULT_ASSET_PREFIXES
    prefixes = list(DEFAULT_ASSET_PREFIXES)
    for prefix in user:
        if isinstance(prefix, str) and prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return tuple(prefixes)


def _template_graph_from_text(text, prefixes):
    clean = _strip_html_comments(text)
    graph = {
        "extends": _EXTENDS_RE.findall(clean),
        "includes": _INCLUDE_RE.findall(clean),
        "assets": _assets_from_text(clean, prefixes),
        "hash": _digest(text),
    }
    return graph


def _graph_only(graph):
    return {k: v for k, v in graph.items() if k != "hash"}


def _assets_from_text(clean, prefixes):
    values = []
    values.extend(_STATIC_RE.findall(clean))
    values.extend(_SCRIPT_SRC_RE.findall(clean))
    values.extend(_LINK_HREF_RE.findall(clean))
    assets = []
    for value in values:
        norm = _normalize_asset_ref(value, prefixes)
        if norm:
            assets.append(norm)
    return assets


DEFAULT_ASSET_PREFIXES = ("{{ MEDIA_URL }}", "{{ STATIC_URL }}", "{% static '", "url('")


def _normalize_asset_ref(value, prefixes=None):
    """Normalize a template asset reference to a repo-relative path or None.

    Strips template-variable prefixes, query/version strings, "./" leads.
    Returns None for absolute/CDN URLs and for dynamic ({{ ... }}-only)
    references: dynamic template asset references are ignored, not
    resolved.
    """
    s = value.strip()
    if prefixes is None:
        prefixes = DEFAULT_ASSET_PREFIXES
    for prefix in prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.split("?", 1)[0]
    s = s.strip("'\" ")
    if s.startswith(("http://", "https://", "//")):
        return None
    if s.startswith("{{"):
        return None
    while s.startswith("./"):
        s = s[2:]
    if not s or s.endswith(".html") and "templateUrl" not in value:
        return None
    return s


def _tracked_files(root, cfg, *patterns):
    files = []
    for path in gitrun.git_nul(["ls-files", "-z"] + list(patterns), root):
        if not is_excluded(path, cfg, root):
            files.append(path)
    return files


def find_render_sites(analyses, template_names):
    """Return hits where a changed template name appears as a string usage.

    analyses maps path -> cache entry or raw analysis (same contract as
    python_analyzer.find_references). Each hit adds "path" and
    "ctx_leaf" (the enclosing function/method name = the render site).

    Only usages inside a function, method or class scope are kept:
    module-scope constants and bare expressions (ctx_kind "module") are
    skipped so module-level assignments such as ``TEMPLATE = 'x.html'``
    do not become render sites. This is a heuristic -- a string argument
    that merely looks like a template inside a function still matches.
    """
    wanted = set(template_names)
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
            if usage.get("kind") != "str" or usage.get("name") not in wanted:
                continue
            if usage.get("ctx_kind") not in ("function", "method", "class"):
                continue
            ctx = usage.get("ctx_qname") or ""
            hits.append({
                "path": path,
                "line": usage.get("line", 0) or 0,
                "template": usage.get("name"),
                "ctx_qname": ctx,
                "ctx_leaf": ctx.rsplit(".", 1)[-1] if ctx else "",
            })
    hits.sort(key=lambda h: (h["path"], h["line"]))
    return hits


def descendants_of(name, graphs):
    """Map child template name -> the direct extends target for all descendants of ``name``.

    Walks the inverted extends graph transitively with a cycle guard; a
    child is included only when reached through a chain of extends edges.
    When the seed itself sits on an extends cycle the result is empty
    (fail-closed): a partial or ambiguous descendant set is worse than
    none, and Django rejects extends cycles at render time anyway.
    """
    children = {}
    for child, graph in graphs.items():
        for target in graph.get("extends") or []:
            children.setdefault(target, []).append(child)
    out = {}
    queue = [name]
    seen = {name}
    while queue:
        current = queue.pop(0)
        for child in children.get(current, []):
            if child in seen:
                # Seed re-reached: the seed participates in an extends
                # cycle, so fail closed with no descendants rather than a
                # partial/ambiguous set (Django rejects cycles at render).
                if child == name:
                    return {}
                continue
            seen.add(child)
            targets = [t for t in graphs.get(child, {}).get("extends") or []]
            out[child] = [t for t in targets if t == current] or targets
            queue.append(child)
    return out


def make_row(path, module, category, ref, entity_name, deleted=False):
    """Build a row dict shaped for the existing report pipeline."""
    return {
        "path": path,
        "module": module,
        "category": category,
        "ref": ref,
        "_entity": entity_name,
        "_deleted": deleted,
    }


def _module_of(path):
    if not path:
        return ""
    stem = path[:-3] if path.endswith(".py") else path
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def template_chain(root, cfg, impact_cfg, changes, analyses, cache, anchor, staged, from_ref, to_ref):
    """Chain T: changed templates -> render sites -> view pseudo-entities + rows.

    Returns (extra_entities, rows, extra_layers, unresolved).
    """
    template_globs = impact_cfg.get("template_globs") or []
    changed_templates = [c.path for c in changes if c.ext == "html"]
    changed_names = []
    for path in changed_templates:
        name = template_name_of(path)
        if name:
            changed_names.append(name)
    extra_entities = {}
    rows = []
    layers = {}
    unresolved = []
    if not changed_names:
        return extra_entities, rows, layers, unresolved
    template_files = _tracked_files(root, cfg, "*.html")
    if template_globs:
        template_files = [p for p in template_files if _glob_match_any(p, template_globs)]
    graphs = {}
    for path in template_files:
        graphs[template_name_of(path) or path] = analyze_template_graph(root, path, cache, cfg)
    desc_map = {}
    for name in changed_names:
        for child, targets in descendants_of(name, graphs).items():
            desc_map.setdefault(child, set()).update(targets or [])
    affected_names = set(changed_names) | set(desc_map)
    for hit in find_render_sites(analyses, affected_names):
        entity = hit["ctx_leaf"]
        if not entity:
            continue
        module = _module_of(hit["path"])
        ent = extra_entities.setdefault(
            entity, {"kind": "function", "deleted": False, "modules": set()}
        )
        ent["modules"].add(module)
        rows.append(
            make_row(
                hit["path"], module, "python",
                "renders '%s' (line %d)" % (hit["template"], hit["line"]),
                entity,
            )
        )
        layers.setdefault(entity, set()).add("template")
    for child in sorted(desc_map):
        targets = sorted(desc_map[child])
        child_path = _path_for_name(template_files, child)
        rows.append(
            make_row(
                child_path, _dir_of(child_path), "template",
                "extends '%s'" % (", ".join(targets[:2])), "extends:%s" % child,
            )
        )
    for path in changed_templates:
        if _has_dynamic_include(root, path):
            unresolved.append({"file": path, "reason": "dynamic include; manual check required"})
    return extra_entities, rows, layers, unresolved


_JS_EXT_TRACKED = (".ts", ".tsx", ".js", ".jsx")

# Schema version for the "frontend" cache section. Bump when the graph
# shape or the extraction regexes change so entries written by older
# code are rebuilt instead of trusted.
FRONTEND_GRAPH_VERSION = 1

# Matches the quoted MODULE SPECIFIER of static and dynamic import forms.
# Every branch carries at most ONE variable-length quantifier over free
# text: adjacent quantifiers never share a character class (keyword
# boundaries use \b, keyword-to-quote spacing uses \s*), so backtracking
# stays linear and compact valid syntax still matches. The free-text scan
# in the two "from" branches is capped at 4096 chars so an import/export
# line with no "from" ahead cannot scan to EOF (O(k*n) -> O(k*4096)).
#   Matches:  import { BaseService } from './base.service';  -> "./base.service"
#             import {\n  Alpha,\n  Beta\n} from './multi';  -> "./multi"
#             import{a}from'./x';                           -> "./x"
#             import'./side';                               -> "./side"
#             export * from './z';                          -> "./z"
#             export{b}from'./y';                           -> "./y"
#             export { x } from './w';                      -> "./w"
#             const lazy = () => import('./lazy.module');   -> "./lazy.module"
#             const fs = require('./util');                 -> "./util"
#   Captures: import { C } from '@angular/core';              -> "@angular/core"
#             (package/alias specifiers are captured here, then dropped by
#              the leading-dot filter in analyze_frontend_graph)
#   Skips:    literal contents are blanked first by _blank_literals, so
#             const s = "import x from './fake'"; and
#             const t = `import('./in-tmpl')`;  contribute nothing
#             import(variableName);             (non-literal; no quote follows)
#             obj.import('./req');              (lookbehind blocks the ".")
#             bellyrequire('./req');            (no word boundary before
#                                                "require")
#             foo.require('./req');             (lookbehind blocks the ".")
#             $require('./req');                (lookbehind blocks the "$")
#             export { "import('./x')" };       (quoted export names are kept
#                                                by _blank_literals, but the
#                                                quote lookbehind stops their
#                                                contents being re-scanned)
_IMPORT_SPEC_RE = re.compile(
    r"(?:^\s*import\b[^'\";]{0,4096}?\bfrom\s*['\"]([^'\"\n]+)['\"]"
    r"|^\s*export\b[^'\";]{0,4096}?\bfrom\s*['\"]([^'\"\n]+)['\"]"
    r"|^\s*import\s*['\"]([^'\"\n]+)['\"]"
    r"|(?<![.\w$'\"`])import\s*\(\s*['\"]([^'\"\n]+)['\"]"
    r"|(?<![.\w$'\"`])require\s*\(\s*['\"]([^'\"\n]+)['\"])",
    re.M,
)

# Matches single-symbol export declarations plus the
# "export default <ident>;" reference form.
#   Matches:  export function loadPage() {}        -> "loadPage"
#             export function* gen() {}            -> "gen"
#             export class PageService {}          -> "PageService"
#             export const PAGE = 'p';             -> "PAGE"
#             export let x = 1;                    -> "x"
#             export var y = 2;                    -> "y"
#             export default class App {}          -> "App"
#             export default function f() {}       -> "f"
#             export async function run() {}       -> "run"
#             export default async function f() {} -> "f"
#             export default Foo;                  -> "Foo"
#   Skips:    function loadPage() {}               (no export keyword)
#             export default function () {}        (anonymous default; no
#                                                   pseudo-name "default")
#             export default class {}              (anonymous default)
#             export default () => {};             (anonymous default)
#             export type Flags = {...};           (TS-only declaration)
#             export interface Shape {}            (TS-only declaration)
#             export enum Kind {}                  (TS-only declaration)
#             export namespace Inner {}            (TS-only declaration)
#             export declare const X: T;           (TS-only declaration)
_EXPORT_NAMED_RE = re.compile(
    r"(?:export\s+(?:(?:default|async)\s+)*"
    r"(?:function\s*(?:\*\s*)?(\w+)|class\s+(\w+)|const\s+(\w+)|let\s+(\w+)|var\s+(\w+))"
    r"|export\s+default\s+(?!(?:function|class|const|let|var|async)\b)"
    r"([A-Za-z_$][\w$]*))"
)

# Matches brace-group export lists; contents are split on commas and the
# EXPORTED (right-hand) side of each "as" alias is recorded. The body span
# is capped at 4096 chars so an unmatched "{" cannot scan to EOF (keeps the
# scan linear on malformed input).
#   Matches:  export { alpha, beta as gamma };        -> ["alpha", "gamma"]
#             export {\n  alpha,\n  beta\n};          -> ["alpha", "beta"]
#   Captures: export { default as Nav } from './nav'; -> ["Nav"]
#             export { type Foo };                    -> ["Foo"]
#   Skips:    export * from './mod';                  (star export has no brace list)
#             export type { Foo };                    ("type" sits before the
#                                                      brace; not a value export)
_EXPORT_BRACE_RE = re.compile(r"export\s*\{([^}]{0,4096})\}")

# Matches quoted literal regions so their contents can be blanked before
# the import/export scan (string/template false positives).
#   Matches:  "./x"    (double-quoted, single line, escape-aware)
#             './x'    (single-quoted, single line, escape-aware)
#             `a\nb`   (backtick template, may span lines, escape-aware)
#   Captures: nothing (no groups; the whole match is the region)
#   Skips:    unterminated quotes (no closing quote on the same line for
#             '...'/"..."; no closing backtick at all)
_LITERAL_REGION_RE = re.compile(
    r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`"
)

# Matches the prefix immediately before a quoted literal region; a region
# survives only when this matches, i.e. it sits in an operand position or
# names a quoted export inside an EXPORT brace list. Used by the EXPORT scan
# (_blank_literals(keep_export_names=True)); the import scan uses
# _LITERAL_IMPORT_OPERAND_RE so export-brace names never reach it. Tested
# against the FULLY-blanked text (every region replaced by spaces), so a
# literal that merely CONTAINS "export {" cannot make a later literal look
# like an operand. The brace branch is anchored on the "export" keyword (not
# a bare "{") so object-literal keys and "as" type assertions are blanked.
#   Matches:  "import x from "  (from operand:  from './b')
#             "import("         (dynamic import operand: import('./b'))
#             "require ("       (require operand: require('./b'))
#             "import "         (side-effect operand: import './b')
#             "export { "        (quoted export name: export { "solo" })
#             "export { x as "   (alias target: export { x as "str name" })
#             "export { a, "     (later quoted name: export { a, "b" })
#   Captures: nothing (used as a boolean via re.search)
#   Skips:    "const s = "      (assignment RHS -> region is blanked;
#                                 covers any code/assignment prefix)
#             "const o = { "     (object-literal key -> region is blanked;
#                                 only an "export {" list is kept)
#             "value as "        (type assertion -> region is blanked; "as"
#                                 only counts inside an export brace list)
#             "bellyrequire("    (no word boundary before "require")
_LITERAL_OPERAND_RE = re.compile(r"\b(?:from|import|require)\s*\(?\s*\Z|\bexport\s*\{[^}]*\Z")

# Import-scan variant of _LITERAL_OPERAND_RE: only the transport operands are
# kept, so a quoted name inside an "export {...}" list is blanked and its
# contents can never leak into the import graph.
#   Matches:  "import x from "  (from operand:  from './b')
#             "import("         (dynamic import operand: import('./b'))
#             "require ("       (require operand: require('./b'))
#             "import "         (side-effect operand: import './b')
#   Captures: nothing (used as a boolean via re.search)
#   Skips:    "export { "        (quoted export name -> blanked for imports)
#             "const s = "       (assignment RHS -> region is blanked)
_LITERAL_IMPORT_OPERAND_RE = re.compile(r"\b(?:from|import|require)\s*\(?\s*\Z")

_VENDOR_SUFFIXES = (".min.js", ".bundle.js")


def _is_graph_file(path):
    lowered = path.lower()
    basename = os.path.basename(lowered)
    if ".min." in basename or ".bundle." in basename:
        return False
    return not lowered.endswith(_VENDOR_SUFFIXES)


# _OPERAND_WINDOW bounds the prefix look-back in _blank_literals: only
# the 512 chars before a region are checked for an operand keyword (an
# operand keyword sits immediately before its quote; the cap keeps the
# check O(1) per region instead of re-scanning the whole prefix).
_OPERAND_WINDOW = 512


def _blank_region(region):
    """Replace a literal region with spaces, keeping "\\n"/"\\r" for line numbers."""
    return "".join(ch if ch in "\r\n" else " " for ch in region)


def _blank_literals(text, keep_export_names=True):
    """Blank quoted literal regions that are not operands.

    Scans left to right for non-overlapping regions (double-quoted,
    single-quoted, backtick) and replaces each non-operand region's
    characters with spaces of identical length, preserving "\\n" and "\\r"
    so line numbers never shift. Operand regions are copied through
    unchanged; every other region is blanked, including regions that follow
    an operand region. ``keep_export_names`` selects the operand set: True
    (the export scan) also keeps quoted names inside an "export {...}" list;
    False (the import scan) keeps only from/import/require operands, so a
    quoted export name is blanked and cannot leak its contents as imports.
    Operand detection runs against a copy of the text in which ALL regions
    are already blanked, so a literal whose contents look like an operand
    prefix cannot keep a later literal alive.
    """
    regions = list(_LITERAL_REGION_RE.finditer(text))
    if not regions:
        return text
    operand_re = _LITERAL_OPERAND_RE if keep_export_names else _LITERAL_IMPORT_OPERAND_RE
    pieces = []
    pos = 0
    for match in regions:
        pieces.append(text[pos:match.start()])
        pieces.append(_blank_region(match.group(0)))
        pos = match.end()
    pieces.append(text[pos:])
    blanked = "".join(pieces)
    out = []
    pos = 0
    for match in regions:
        window = max(0, match.start() - _OPERAND_WINDOW)
        if operand_re.search(blanked, window, match.start()):
            out.append(text[pos:match.end()])
        else:
            out.append(text[pos:match.start()])
            out.append(_blank_region(match.group(0)))
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def analyze_frontend_graph(root, path, cache, tracked):
    """Return {"imports": [...], "exports": [...]} for one js/ts file.

    imports lists raw static specifiers (relative only resolved later);
    alias specifiers (no leading dot) are dropped -- they are surfaced as
    config hints by the chain. Stored in the "frontend" cache section as
    {"hash", "v", "graph"}; an entry is only trusted when its hash, its
    FRONTEND_GRAPH_VERSION marker and both graph values (lists) match.
    ``tracked`` is accepted for caller symmetry; extraction returns raw
    specifiers and does not resolve them against it.
    """
    if not path:
        return {"imports": [], "exports": []}
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8-sig", "replace")
    except (OSError, ValueError):
        return {"imports": [], "exports": []}
    from diffimpactscout.impact.python_analyzer import _content_hash

    digest = _content_hash(text)
    section = cache.load_section("frontend") if cache else {}
    entry = section.get(path)
    graph = entry.get("graph") if isinstance(entry, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("hash") == digest
        and entry.get("v") == FRONTEND_GRAPH_VERSION
        and isinstance(graph, dict)
        and isinstance(graph.get("imports"), list)
        and isinstance(graph.get("exports"), list)
    ):
        return graph
    from diffimpactscout.impact.route_linker import _strip_comments

    clean = _strip_comments(text)
    import_text = _blank_literals(clean, keep_export_names=False)
    export_text = _blank_literals(clean, keep_export_names=True)
    imports = [
        spec
        for groups in _IMPORT_SPEC_RE.findall(import_text)
        for spec in groups
        if spec and spec.startswith(".")
    ]
    exports = []
    for groups in _EXPORT_NAMED_RE.findall(export_text):
        for name in groups:
            if name:
                exports.append(name)
    for brace in _EXPORT_BRACE_RE.findall(export_text):
        for raw in brace.split(","):
            piece = re.split(r"\s+as\s+", raw.strip())[-1].strip()
            piece = re.sub(r"^type\s+", "", piece).strip().strip("'\"")
            if piece:
                exports.append(piece)
    graph = {"imports": list(dict.fromkeys(imports)), "exports": sorted(set(exports))}
    if cache:
        section[path] = {"hash": digest, "v": FRONTEND_GRAPH_VERSION, "graph": graph}
        cache.save_section("frontend", section)
    return graph


def _resolve_specifier(spec, importer_path, tracked):
    """Resolve a relative specifier to a tracked path, or None."""
    if not spec.startswith("."):
        return None
    base = spec.split("?", 1)[0].split("#", 1)[0]
    parts = importer_path.split("/")[:-1]
    for seg in base.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
            else:
                return None
        elif seg == ".":
            continue
        elif seg:
            parts.append(seg)
    base_path = "/".join(parts)
    candidates = [base_path]
    for ext in _JS_EXT_TRACKED:
        candidates.append(base_path + ext)
    candidates.extend(base_path + "/index" + ext for ext in _JS_EXT_TRACKED)
    for cand in candidates:
        if cand in tracked:
            return cand
    return None


def importers_of(target, graph, max_nodes=500):
    """Return the full transitive set of files importing ``target``.

    Traversal is cycle-guarded and capped at ``max_nodes`` reached files so
    a pathological import graph cannot blow up the row set (spec: 500-node
    cap).
    """
    inverted = {}
    for path, deps in graph.items():
        for dep in deps:
            inverted.setdefault(dep, set()).add(path)
    out = set()
    queue = [target]
    while queue and len(out) < max_nodes:
        current = queue.pop(0)
        for parent in inverted.get(current, []):
            if parent not in out:
                out.add(parent)
                queue.append(parent)
                if len(out) >= max_nodes:
                    break
    out.discard(target)
    return out


def _newline_offsets(text):
    """Index of every "\\n" in ``text`` (for O(log n) line lookups)."""
    return [i for i, ch in enumerate(text) if ch == "\n"]


def _line_at(newlines, pos):
    """1-based line number of character offset ``pos``."""
    return bisect.bisect_left(newlines, pos) + 1


def _export_lines(text):
    """Map exported name -> line number (1-based).

    Reuses _EXPORT_NAMED_RE / _EXPORT_BRACE_RE so the name set is identical
    to analyze_frontend_graph's graph["exports"] (no export form is missed).
    Each brace-list name is mapped to its OWN line (per comma-segment
    offset), not the opener line, so changed-line scoping stays correct for
    multi-line export lists.
    """
    newlines = _newline_offsets(text)
    out = {}
    for m in _EXPORT_NAMED_RE.finditer(text):
        for name in m.groups():
            if name:
                out.setdefault(name, _line_at(newlines, m.start()))
    for m in _EXPORT_BRACE_RE.finditer(text):
        body_start = m.start(1)
        offset = 0
        for raw in m.group(1).split(","):
            seg_start = body_start + offset
            offset += len(raw) + 1
            lead = len(raw) - len(raw.lstrip())
            piece = re.split(r"\s+as\s+", raw.strip())[-1].strip()
            piece = re.sub(r"^type\s+", "", piece).strip().strip("'\"")
            if piece:
                out.setdefault(piece, _line_at(newlines, seg_start + lead))
    return out


def js_internal_chain(root, cfg, impact_cfg, changes, cache, anchor, staged, from_ref, to_ref):
    """Chain I: changed js/ts exports -> referencing files via the import graph.

    Returns (extra_entities, rows, extra_layers, unresolved). extra_entities
    stays empty: rows are attributed to pseudo-entities named
    "js:<symbol>" so layer aggregation groups per symbol.
    """
    from diffimpactscout.impact.diff_parser import get_changed_lines, read_path_at_ref
    from diffimpactscout.impact.route_linker import _is_vendor, _read_text

    changed_js = [c for c in changes if c.ext in ("js", "ts", "jsx", "tsx")]
    tracked = set(_tracked_files(root, cfg, "*.ts", "*.tsx", "*.js", "*.jsx"))
    tracked = {p for p in tracked if _is_graph_file(p) and not _is_vendor(p)}
    deleted_js = {
        c.path for c in changed_js
        if c.status[0] == "D" and _is_graph_file(c.path) and not _is_vendor(c.path)
    }
    resolvable = tracked | deleted_js
    if cache is not None:
        cache.prune_section("frontend", tracked)
    graph = {}
    for path in tracked:
        graph[path] = _resolve_imports_for_graph(root, path, cache, resolvable)
    rows = []
    layers = {}
    unresolved = []
    old_ref_eff = anchor or from_ref
    for change in changed_js:
        if not _is_graph_file(change.path) or _is_vendor(change.path):
            continue
        new_text = _read_text(os.path.join(root, change.path) if root else change.path) or ""
        old_text = read_path_at_ref(root, change.path, old_ref_eff) if old_ref_eff else ""
        old_lines, new_lines = get_changed_lines(root, change, anchor, from_ref, to_ref, staged)
        new_exports = {
            n: ln for n, ln in _export_lines(_clean_for_exports(new_text)).items()
            if new_lines is None or ln in new_lines
        }
        old_exports = {
            n: ln for n, ln in _export_lines(_clean_for_exports(old_text)).items()
            if old_lines is None or ln in old_lines
        }
        deleted = set(old_exports) - set(new_exports)
        changed_names = set(new_exports) | deleted
        if not changed_names:
            continue
        importers = importers_of(change.path, graph)
        for importer in importers:
            importer_text = _read_text(os.path.join(root, importer) if root else importer) or ""
            binds = change.path in (graph.get(importer) or [])
            if not binds:
                continue
            for name in sorted(changed_names):
                if not re.search(r"(?<![\w$])%s(?![\w$])" % re.escape(name), importer_text):
                    continue
                pseudo = "js:%s" % name
                rows.append(
                    make_row(
                        importer, _dir_of(importer), "frontend",
                        "%s (imported from %s)" % (name, change.path), pseudo,
                        deleted=name in deleted,
                    )
                )
                layers.setdefault(pseudo, set()).add("frontend")
        for name in sorted(deleted):
            if not importers:
                unresolved.append({
                    "file": change.path,
                    "reason": "export '%s' deleted; no tracked importers found" % name,
                })
    return {}, rows, layers, unresolved


def _clean_for_exports(text):
    """Comment-stripped, literal-blanked text for export-name line scoping.

    Blanking keeps the length/newlines, so line numbers from _export_lines
    still match the original file, while comments, strings and template
    literals can no longer contribute phantom export names.
    """
    from diffimpactscout.impact.route_linker import _strip_comments

    return _blank_literals(_strip_comments(text), keep_export_names=True)


def _resolve_imports_for_graph(root, path, cache, resolvable):
    """Return the resolved paths this file imports (graph edge list).

    ``resolvable`` is the set of tracked graph files plus any changed deleted
    paths, so a dangling import to a deleted module still forms an edge.
    """
    graph = analyze_frontend_graph(root, path, cache, resolvable)
    edges = []
    for spec in graph["imports"]:
        resolved = _resolve_specifier(spec, path, resolvable)
        if resolved and resolved in resolvable:
            edges.append(resolved)
    return edges


def frontend_chain(root, cfg, impact_cfg, changes, entities, analyses, cache, anchor, staged, from_ref, to_ref, experimental=False):
    """Chains A (+I, E in later tasks): changed js/ts -> reverse rows.

    Returns (extra_entities, rows, extra_layers, unresolved).
    """
    changed_js = [c for c in changes if c.ext in ("js", "ts", "jsx", "tsx")]
    if not changed_js:
        return {}, [], {}, []
    extra_entities = {}
    rows = []
    layers = {}
    unresolved = []
    template_globs = impact_cfg.get("template_globs") or []
    template_files = _tracked_files(root, cfg, "*.html")
    if template_globs:
        template_files = [p for p in template_files if _glob_match_any(p, template_globs)]
    graphs = {}
    for path in template_files:
        graphs[path] = analyze_template_graph(root, path, cache, cfg)
    prefixes = _asset_prefixes(cfg)
    for change in changed_js:
        matching = []
        for path, entry in graphs.items():
            assets = entry.get("assets")
            if not isinstance(assets, list):
                continue
            for ref in assets:
                norm = _normalize_asset_ref(ref, prefixes)
                if norm and _asset_matches(norm, change.path):
                    matching.append((path, ref))
        for tpath, ref in sorted(set(matching)):
            rows.append(
                make_row(tpath, _dir_of(tpath), "template", "loads '%s'" % ref, "asset:%s" % change.path)
            )
            layers.setdefault("asset:%s" % change.path, set()).add("template")
        if not matching and change.path:
            unresolved.append({"file": change.path, "reason": "no template references this asset"})
    linked_names = [
        template_name_of(r["path"]) for r in rows if r["category"] == "template"
    ]
    linked_names = [n for n in linked_names if n]
    for hit in find_render_sites(analyses, linked_names):
        entity = hit["ctx_leaf"]
        if not entity:
            continue
        module = _module_of(hit["path"])
        ent = extra_entities.setdefault(
            entity, {"kind": "function", "deleted": False, "modules": set()}
        )
        ent["modules"].add(module)
        rows.append(
            make_row(hit["path"], module, "python",
                     "renders '%s' (line %d)" % (hit["template"], hit["line"]), entity)
        )
        layers.setdefault(entity, set()).add("template")
    e_entities, e_rows, e_layers, e_unresolved = js_internal_chain(
        root, cfg, impact_cfg, changes, cache, anchor, staged, from_ref, to_ref
    )
    rows.extend(e_rows)
    for name, cats in e_layers.items():
        layers.setdefault(name, set()).update(cats)
    unresolved.extend(e_unresolved)
    return extra_entities, rows, layers, unresolved


def _asset_matches(norm, tracked_path):
    """True when a normalized ref names the tracked path (exact or path suffix)."""
    if not norm or not tracked_path:
        return False
    return tracked_path == norm or tracked_path.endswith("/" + norm)


def _glob_match_any(path, patterns):
    if isinstance(patterns, str):
        patterns = [patterns]
    return any(_matches_glob(path, p) for p in patterns)


def _dir_of(path):
    return os.path.dirname(path or "") or ""


def _path_for_name(template_files, name):
    for path in template_files:
        if template_name_of(path) == name:
            return path
    return name


def _has_dynamic_include(root, path):
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    return bool(_DYNAMIC_INCLUDE_RE.search(_strip_html_comments(text)))


# Matches native fetch() calls (second transport besides the $http/HttpClient
# families in route_linker.HTTP_CALL_RE).
#   Matches:  fetch('/shop/items/')  -> scan continues after "(" for the arg
#   Skips:    fetch(variableUrl)     (unquoted arg; only quoted literals or
#                                     known constants resolve)
_FETCH_RE = re.compile(r"\bfetch\s*\(")

# Matches string-constant ASSIGNMENTS used for one-level constant propagation.
# Both free-text quantifiers are bounded (\w{1,100}, [^=\n]{0,200}) so a long
# word run after a keyword cannot backtrack quadratically; "\b" stops
# "myreadonly FOO = ..." from matching.
#   Matches:  const API = '/shop/';                       -> API -> "/shop/"
#             static readonly LIST: string = '/shop/l/';  -> LIST -> "/shop/l/"
#   Skips:    const API = buildUrl();          (non-literal RHS)
_CONST_ASSIGN_RE = re.compile(
    r"""\b(?:const|let|var|readonly|static\s+readonly|public\s+readonly)\s+(\w{1,100})[^=\n]{0,200}=\s*['"`]([^'"`\n]{1,300})['"`]"""
)

# Matches PREFIX-CONCATENATION assignments so one-level propagation resolves them.
# Quantifiers bounded as in _CONST_ASSIGN_RE.
#   Matches:  const LIST = API + 'items/';   -> LIST -> consts["API"] + "items/"
#   Skips:    const X = A + B;               (no string literal part)
_CONST_CONCAT_RE = re.compile(
    r"""\b(?:const|let|var|readonly|static\s+readonly)\s+(\w{1,100})[^=\n]{0,200}=\s*(\w{1,100})\s*\+\s*['"`]([^'"`\n]{1,300})['"`]"""
)


def _http_refs_for_files(root, paths, tracked=None):
    """Extract HTTP endpoint refs from the given js/ts files.

    One-level constant propagation only: a string const, or a prefix
    concatenation of one string const plus a literal, is substituted into a
    call argument. Deeper chains and runtime-built URLs are dropped
    (unresolved). Const resolution is file-global; a name assigned more than
    once is dropped rather than resolved to an arbitrary occurrence.
    """
    refs = []
    for path in paths:
        text = _read_text(os.path.join(root, path) if root else path)
        if not text:
            continue
        clean = _strip_comments(text)
        consts = {}
        ambiguous = set()
        for m in _CONST_ASSIGN_RE.finditer(clean):
            name = m.group(1)
            if name in consts or name in ambiguous:
                consts.pop(name, None)
                ambiguous.add(name)
            else:
                consts[name] = m.group(2)
        literals = dict(consts)
        for m in _CONST_CONCAT_RE.finditer(clean):
            name = m.group(1)
            base = literals.get(m.group(2))
            if base and name not in consts and name not in ambiguous:
                consts[name] = base + m.group(3)
        for regex, has_method in ((HTTP_CALL_RE, True), (_FETCH_RE, False)):
            for m in regex.finditer(clean):
                value = _arg_value_after(clean, m.end(), consts)
                if value and _is_url_like(value):
                    line = clean[: m.start()].count("\n") + 1
                    refs.append(
                        {
                            "file": path,
                            "ref": value,
                            "dynamic": False,
                            "line": line,
                            "method": m.group(2) if has_method else None,
                        }
                    )
    return refs


def _arg_value_after(text, offset, consts):
    """Return the endpoint string at a call site: quoted literal or known constant."""
    window = text[offset: offset + 300]
    stripped = window.lstrip()
    if stripped[:1] in ("'", '"'):
        lit = QUOTED_LITERAL_RE.match(stripped)
        if lit:
            return lit.group(2)
    name_match = re.match(r"(\w+)", stripped)
    if name_match and name_match.group(1) in consts:
        return consts[name_match.group(1)]
    return None
