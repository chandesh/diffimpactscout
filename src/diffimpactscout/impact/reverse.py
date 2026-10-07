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
