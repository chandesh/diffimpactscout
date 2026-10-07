"""Reverse impact chains: changed templates and frontend files mapped back to Python render sites.

Example: a diff that touches only a template produces rows for the views
that render it, its route(s), and the templates extending it.
"""

import os
import re

import diffimpactscout.gitrun as gitrun
from diffimpactscout.config import is_excluded

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
    {"hash": sha1 hex, "graph": the three-key dict}; the cache entry is
    only trusted when its "graph" is a dict, otherwise it is rebuilt.
    """
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return {"extends": [], "includes": [], "assets": []}
    digest = _digest(text)
    section = cache.load_section("templates")
    entry = section.get(path)
    if isinstance(entry, dict) and entry.get("hash") == digest:
        cached = entry.get("graph")
        if isinstance(cached, dict):
            return cached
    graph = _template_graph_from_text(text)
    section[path] = {"hash": digest, "graph": _graph_only(graph)}
    cache.save_section("templates", section)
    return _graph_only(graph)


def _digest(text):
    from diffimpactscout.impact.python_analyzer import _content_hash

    return _content_hash(text)


def _template_graph_from_text(text):
    clean = _strip_html_comments(text)
    graph = {
        "extends": _EXTENDS_RE.findall(clean),
        "includes": _INCLUDE_RE.findall(clean),
        "assets": _assets_from_text(clean),
        "hash": _digest(text),
    }
    return graph


def _graph_only(graph):
    return {k: v for k, v in graph.items() if k != "hash"}


def _assets_from_text(clean):
    values = []
    values.extend(_STATIC_RE.findall(clean))
    values.extend(_SCRIPT_SRC_RE.findall(clean))
    values.extend(_LINK_HREF_RE.findall(clean))
    assets = []
    for value in values:
        norm = _normalize_asset_ref(value, DEFAULT_ASSET_PREFIXES)
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
    for prefix in (prefixes or ()):
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
                "line": usage.get("line", 0),
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
