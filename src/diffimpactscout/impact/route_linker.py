"""Extract Django and FastAPI routes, then link template and frontend references to those routes.

Example: a template tag {% url order-list %} resolves to the changed view
handler that backs the order-list route.
"""

import ast
import os
import re

from diffimpactscout.config import _matches_glob, is_excluded
from diffimpactscout.impact._parse import parse_quiet


class Route(object):
    def __init__(self, name, path, handler, module, full=None, regex=None):
        self.name = name
        self.path = path
        self.handler = handler
        self.module = module
        self.full = full or path
        self.regex = regex


_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options")
_HTTP_METHOD_RE_SRC = r"get|post|put|delete|patch"

HTTP_CALL_RE = re.compile(
    r"(\$http|HttpClient|http)\s*\.\s*(" + _HTTP_METHOD_RE_SRC + r")\s*\("
)
QUOTED_LITERAL_RE = re.compile(r"(['\"])([^'\"\r\n]{1,500})\1")
URL_TAG_RE = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]\s*%}")
URL_LITERAL_RE = re.compile(r"(['\"])([^'\"\r\n]{1,500})\1")

_URL_ASSET_EXTS = (
    ".js",
    ".ts",
    ".json",
    ".css",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".map",
    ".html",
)
_URL_MIME_PREFIXES = (
    "application/",
    "text/",
    "image/",
    "audio/",
    "video/",
    "multipart/",
    "font/",
)


def _is_url_like(value):
    """Return True when a string literal looks like an endpoint path.

    Catches HTTP-call arguments as well as URL constants in ``ENDPOINTS`` /
    ``ACTIONS`` dictionaries, ``source:`` values, and static fragments of
    concatenated URLs, while rejecting module specifiers, assets, absolute
    URLs, MIME types, and prose/error strings. Endpoint paths are anchored:
    they start with ``/`` or end with ``/``.
    """
    if not value or len(value) < 3 or len(value) > 500:
        return False
    if "/" not in value:
        return False
    if any(ch.isspace() for ch in value):
        return False
    if value.startswith(("@", ".", "//", "http://", "https://", "www.")):
        return False
    if not (value.startswith("/") or value.endswith("/")):
        return False
    lower = value.lower()
    for prefix in _URL_MIME_PREFIXES:
        if lower.startswith(prefix):
            return False
    return not lower.endswith(_URL_ASSET_EXTS)


def _str_value(node):
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return value
        return None
    Str = getattr(ast, "Str", None)
    if Str is not None and isinstance(node, Str):
        return node.s
    return None


_VENDOR_DIRS = (
    "node_modules",
    "bower_components",
    ".angular",
    "vendor",
    "third_party",
    "third-party",
    "dist",
    "build",
    ".cache",
)
_VENDOR_PREFIXES = (
    "jquery",
    "angular-",
    "angular.",
    "react",
    "react-",
    "lodash",
    "underscore",
    "moment",
    "bootstrap",
    "ckeditor",
    "amcharts",
    "d3.",
    "d3-",
    "echarts",
    "highcharts",
    "sweetalert",
    "toastr",
    "handlebars",
    "ember",
    "vue",
    "svelte",
    "backbone",
    "knockout",
)


def _is_vendor(posix):
    parts = posix.split("/")
    if any(p in _VENDOR_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    lowered = name.lower()
    if lowered.endswith(".min.js") or lowered.endswith(".bundle.js"):
        return True
    for prefix in _VENDOR_PREFIXES:
        if lowered.startswith(prefix):
            return True
    return False


def _iter_files(root, globs, exts, cfg=None, vendor=False):
    if isinstance(globs, str):
        globs = [globs]
    globs = globs or []
    exts = tuple(exts or ())
    exts = tuple(e.lower() for e in exts)
    if not root or not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
        for name in filenames:
            if exts and os.path.splitext(name)[1].lower() not in exts:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            posix = rel.replace(os.sep, "/")
            if cfg and is_excluded(posix, cfg, root):
                continue
            if vendor and _is_vendor(posix):
                continue
            for pattern in globs:
                if _matches_glob(posix, pattern):
                    yield posix
                    break


def _parse_file(full):
    text = _read_text(full)
    if text is None:
        return None
    try:
        return parse_quiet(text)
    except (SyntaxError, ValueError, TypeError):
        return None


def _read_text(full):
    try:
        with open(full, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return None
    return data.decode("utf-8", "replace")


def _call_has_kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return True
    return False


def _call_kwarg_value(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _attribute_qname(node):
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    if len(parts) >= 2:
        parts = parts[1:]
    return ".".join(parts)


def _resolve_handler(node):
    if isinstance(node, ast.Call):
        return _resolve_handler(node.func)
    if isinstance(node, ast.Attribute):
        return _attribute_qname(node)
    if isinstance(node, ast.Name):
        return node.id
    return None


def _handler_name(call):
    if len(call.args) < 2:
        return None
    return _resolve_handler(call.args[1])


def _url_module(posix):
    stem = posix[:-3] if posix.endswith(".py") else posix
    return stem.replace("/", ".")


def _concat_regex(prefix, route_regex):
    if prefix is None:
        return route_regex
    if route_regex is None:
        return prefix
    return prefix + route_regex.lstrip("^")


def _anchored_regex(source):
    if not source:
        return None
    s = source
    if not s.startswith("^"):
        s = "^" + s
    if not s.endswith("$"):
        s = s + "$"
    try:
        return re.compile(s)
    except re.error:
        return None


def compose_url_prefixes(root, urls_globs, cfg=None):
    """Map each urls module to its composed (path_prefix, regex_prefix).

    Walks the ``include()`` tree from root url modules so a leaf route's full
    URL is e.g. ``app/cart/checkout/<order_id>/`` rather than only the
    app-local ``checkout/<order_id>/``.
    """
    files = {}
    for posix in _iter_files(root, urls_globs, (".py",), cfg=cfg):
        files[_url_module(posix)] = posix
    includes = {}
    for module, posix in files.items():
        tree = _parse_file(os.path.join(root, posix))
        entries = []
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id in ("path", "re_path", "url")):
                    continue
                if len(node.args) < 2:
                    continue
                inc = node.args[1]
                if not (
                    isinstance(inc, ast.Call)
                    and isinstance(inc.func, ast.Name)
                    and inc.func.id == "include"
                ):
                    continue
                target = _str_value(inc.args[0]) if inc.args else None
                if not target:
                    continue
                prefix_val = _str_value(node.args[0]) if node.args else None
                if func.id == "path":
                    entries.append((prefix_val or "", None, target))
                else:
                    entries.append((None, prefix_val, target))
        includes[module] = entries
    targeted = {
        t
        for entries in includes.values()
        for (_p, _r, t) in entries
        if t in files
    }
    roots = [m for m in files if m not in targeted]
    prefix_map = {}
    queue = [(m, "", None) for m in roots]
    while queue:
        module, ppath, pregex = queue.pop(0)
        if module in prefix_map:
            continue
        prefix_map[module] = (ppath, pregex)
        for inc_path, inc_regex, target in includes.get(module, []):
            if target in files and target not in prefix_map:
                queue.append((target, ppath + (inc_path or ""), _concat_regex(pregex, inc_regex)))
    return prefix_map


def extract_django_routes(root, urls_globs, cfg=None):
    routes = []
    prefixes = compose_url_prefixes(root, urls_globs, cfg=cfg)
    for posix in _iter_files(root, urls_globs, (".py",), cfg=cfg):
        full = os.path.join(root, posix) if root else posix
        tree = _parse_file(full)
        if tree is None:
            continue
        ppath, pregex = prefixes.get(_url_module(posix), ("", None))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name) or func.id not in ("path", "re_path", "url"):
                continue
            if not _call_has_kwarg(node, "name"):
                continue
            path_val = _str_value(node.args[0]) if node.args else None
            if path_val is None:
                continue
            name_val = _str_value(_call_kwarg_value(node, "name"))
            handler = _handler_name(node)
            if func.id == "path":
                composed = ppath + path_val
                routes.append(
                    Route(
                        name_val,
                        path_val,
                        handler,
                        posix,
                        full=composed,
                        regex=_route_regex(composed),
                    )
                )
            else:
                composed = _concat_regex(pregex, path_val)
                routes.append(
                    Route(
                        name_val,
                        path_val,
                        handler,
                        posix,
                        full=composed,
                        regex=_anchored_regex(composed),
                    )
                )
    return routes


_ROUTE_BASES = ("router", "app", "api", "bp", "blueprint", "APIRouter", "FastAPI")


def _decorator_route_path(deco):
    if not isinstance(deco, ast.Call) or not deco.args:
        return None
    if not isinstance(deco.func, ast.Attribute):
        return None
    if deco.func.attr not in _HTTP_METHODS:
        return None
    if not isinstance(deco.func.value, ast.Name):
        return None
    if deco.func.value.id not in _ROUTE_BASES:
        return None
    return _str_value(deco.args[0])


def extract_fastapi_routes(root, py_globs, cfg=None):
    routes = []
    for posix in _iter_files(root, py_globs, (".py",), cfg=cfg):
        full = os.path.join(root, posix) if root else posix
        tree = _parse_file(full)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                path = _decorator_route_path(deco)
                if path is not None:
                    routes.append(Route(None, path, node.name, posix))
    return routes


def extract_template_refs(root, template_globs, cfg=None, names=None):
    refs = []
    for posix in _iter_files(root, template_globs, (".html",), cfg=cfg):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        for m in URL_TAG_RE.finditer(text):
            if names is None or m.group(1) in names:
                refs.append((posix, m.group(1)))
    return refs


def _block_repl(match):
    return "\n" * match.group(0).count("\n")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", _block_repl, text, flags=re.S)
    text = re.sub(r"(?m)(?<!:)//.*$", "", text)
    return text


def _url_ref(file, line, value, method, seen):
    if not _is_url_like(value):
        return None
    key = (file, line, value)
    if key in seen:
        return None
    seen.add(key)
    return {
        "file": file,
        "ref": value,
        "dynamic": False,
        "line": line,
        "method": method,
    }


def extract_frontend_refs(root, frontend_globs, cfg=None, vendor=False, needles=None):
    refs = []
    for posix in _iter_files(
        root, frontend_globs, (".ts", ".js", ".tsx", ".jsx"), cfg=cfg, vendor=vendor
    ):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        if needles and not any(n and n in text for n in needles):
            continue
        text = _strip_comments(text)
        seen = set()
        for m in HTTP_CALL_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            method = m.group(2)
            window = text[m.end():m.end() + 300]
            stripped = window.lstrip()
            if stripped[:1] in ("'", '"'):
                lit = QUOTED_LITERAL_RE.match(stripped)
                if lit:
                    ref = _url_ref(posix, line, lit.group(2), method, seen)
                    if ref is not None:
                        refs.append(ref)
                    continue
            litm = QUOTED_LITERAL_RE.search(window)
            literal = litm.group(2) if litm else None
            ref = _url_ref(posix, line, literal, method, seen)
            if ref is not None:
                refs.append(ref)
        for m in URL_LITERAL_RE.finditer(text):
            lit = m.group(2)
            if not _is_url_like(lit):
                continue
            line = text[:m.start()].count("\n") + 1
            ref = _url_ref(posix, line, lit, None, seen)
            if ref is not None:
                refs.append(ref)
    return refs


def _route_regex(route_path):
    parts = []
    i = 0
    n = len(route_path)
    while i < n:
        ch = route_path[i]
        if ch == "<":
            j = route_path.find(">", i)
            if j == -1:
                parts.append(re.escape(route_path[i:]))
                break
            parts.append(r"[^/]+")
            i = j + 1
        elif ch == "{":
            j = route_path.find("}", i)
            if j == -1:
                parts.append(re.escape(route_path[i:]))
                break
            parts.append(r"[^/]+")
            i = j + 1
        else:
            parts.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def _is_path_prefix(ref, route_path):
    if not route_path.startswith(ref):
        return False
    if len(ref) == len(route_path):
        return False
    nxt = route_path[len(ref)]
    return ref.endswith("/") or nxt in ("/", "<", "{")


def match_endpoints(refs, routes):
    matched = []
    unresolved = []
    for ref in refs:
        file = ref.get("file")
        path = ref.get("ref")
        dynamic = ref.get("dynamic", False)
        line = ref.get("line")
        method = ref.get("method")
        if dynamic or path is None:
            exact = _exact_route(path, routes) if path is not None else None
            if exact is not None:
                matched.append(
                    {
                        "route": exact,
                        "file": file,
                        "ref": path,
                        "line": line,
                        "method": method,
                    }
                )
            else:
                unresolved.append({"file": file, "path": path, "dynamic": True})
            continue
        hit = _best_route_match(path, routes)
        if hit is not None:
            matched.append(
                {
                    "route": hit,
                    "file": file,
                    "ref": path,
                    "line": line,
                    "method": method,
                }
            )
        else:
            unresolved.append({"file": file, "path": path, "dynamic": False})
    return matched, unresolved


def _norm_slash(path):
    if path and path.startswith("/") and not path.startswith("//"):
        return path[1:]
    return path


def _norm_ref(path):
    """Normalize a frontend URL literal for route matching.

    Strips the leading slash and any query string and cuts at the first
    runtime interpolation marker (``${``, ``{``, ``' + ``) so a literal such
    as ``/cart/checkout/${id}/?src=x`` becomes the static prefix
    ``cart/checkout/``.
    """
    if not path:
        return path
    s = path.split("?", 1)[0]
    s = _norm_slash(s)
    for marker in ("${", "' + ", '" + '):
        idx = s.find(marker)
        if idx != -1:
            s = s[:idx]
            break
    return s


def _route_matcher(route):
    if route.regex is not None:
        return route.regex
    return _route_regex(_norm_slash(route.full or ""))


def _exact_route(ref_path, routes):
    norm = _norm_slash(ref_path)
    for route in routes:
        if _norm_slash(route.full) == norm:
            return route
    return None


def _best_route_match(ref_path, routes):
    ref_norm = _norm_ref(ref_path)
    for route in routes:
        if _norm_slash(route.full or "") == ref_norm:
            return route
    for route in routes:
        matcher = _route_matcher(route)
        if matcher is not None and matcher.match(ref_norm):
            return route
    for route in routes:
        route_norm = _norm_slash(route.full or "")
        if ref_norm and route_norm and _is_path_prefix(ref_norm, route_norm):
            return route
    return None


def match_template_refs(refs, routes):
    by_name = {r.name: r for r in routes if r.name is not None}
    matched = []
    unresolved = []
    for file, name in refs:
        route = by_name.get(name)
        if route is not None:
            matched.append({"route": route, "file": file, "ref": name})
        else:
            unresolved.append({"file": file, "ref": name})
    return matched, unresolved
