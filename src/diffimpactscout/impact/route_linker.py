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

QUOTED_LITERAL_RE = re.compile(r"(['\"])([^'\"\r\n]{1,500})\1")

# Matches a frontend HTTP call head across call styles so the URL literal that
# follows can be linked to a route. Dotted calls carry an explicit method;
# bare fetch()/axios() default to GET (an options object may override it).
# The dotted alternative needs a guard that blocks a preceding WORD char but
# ALLOWS a preceding "." so "myaxios"/"myhttp"/"xHttpClient" are rejected while
# "this.http"/"this.$http"/"this.axios" still match.
#   Matches:  http.get(   HttpClient.post(   $http.delete(   axios.put(
#             this.http.get(   this.$http.get(   this.axios.get(
#             axios(      fetch(
#   Captures: group 1 = explicit method (get|post|put|delete|patch) or None
#             group 2 = bare callee name ("fetch" | "axios") or None
#   Skips:    myhttp.get(        (lookbehind blocks the word run before "http")
#             myaxios(           (lookbehind blocks the word run before "axios")
#             xHttpClient.put(   (lookbehind blocks the word run before it)
#             response.fetch(    (lookbehind blocks the ".")
#             fetch = 1          (no "(" after the callee)
#             fetchMore(         (the mandatory "\s*\(" rejects "M")
_CALL_HEAD_RE = re.compile(
    r"(?<![\w])(?:\$http|HttpClient|http|axios)\s*\.\s*("
    + _HTTP_METHOD_RE_SRC
    + r")\s*\(|(?<![\w$.])(fetch|axios)\s*\("
)

# Matches an explicit method override inside a fetch()/axios() options object.
#   Matches:  { method: 'POST' }   -> "POST"
#             {method:"delete"}    -> "delete"
#   Captures: group 1 = the HTTP method name
#   Skips:    method: verb          (non-literal value)
#             methodName: 'post'     (no word boundary before "method")
_METHOD_OPTION_RE = re.compile(
    r"\bmethod\s*:\s*['\"](get|post|put|delete|patch)['\"]", re.IGNORECASE
)

# Matches a backtick template literal's one-line body so a STATIC template URL
# resolves.
#   Matches:  `/api/orders/`          -> "/api/orders/"
#             `/shop/items/`
#   Captures: group 1 = the backtick body
#   Skips:    `multi\nline`           (no newline allowed inside)
#   Note:     an interpolated body such as `/api/items/${id}/` still matches;
#             the caller rejects it as non-literal on "${".
_BACKTICK_LITERAL_RE = re.compile(r"`([^`\r\n]{1,500})`")

# Matches a leading concatenation fragment at the START of a call's argument
# list so a URL built as "base + '/path/'" still resolves its static part,
# while a later statement's literal is never borrowed (only the first operand
# run before the "+" is consumed, and only the first "+" chain is read).
#   Matches:  environment.apiUrl + '/api/orders/'  -> "/api/orders/"
#             BASE + '/shop/'
#   Captures: group 1 = the quote character, group 2 = the literal body
#   Skips:    apiUrl              (bare identifier; no "+")
#             build() + '/x/'     (first run cannot span "(")
_ARG_CONCAT_RE = re.compile(
    r"""[\w$.\[\]'"]*\s*\+\s*(['"])([^'"\r\n]{1,500})\1"""
)

# Matches a bare identifier opening a call's argument list so a one-level
# constant (see reverse._CONST_ASSIGN_RE/_CONST_CONCAT_RE) can be substituted.
#   Matches:  LIST)   -> "LIST"     apiUrl,
#   Captures: group 1 = the identifier
#   Skips:    '/path/'  (handled by the quote/backtick branches)
#             obj.path  (only the leading "obj" run is read)
_ARG_NAME_RE = re.compile(r"([A-Za-z_$][\w$]*)")

# Bound for the argument window scanned after a call head for its URL literal.
_CALL_WINDOW = 300
URL_TAG_RE = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]\s*%}")

# Matches a DYNAMIC {% url %} tag: the route name is NOT a quoted literal (a
# template variable or computed expression) so it can never be linked to a
# named route. Such tags degrade to an unresolved "dynamic-ref" entry.
#   Matches:  {% url view_name %}        -> dynamic
#             {% url next_page fallback %}
#   Captures: none (match presence is the only signal)
#   Skips:    {% url 'order-list' %}     (quoted literal -> URL_TAG_RE)
#             {% url "order-list" %}
#             {% url %}                  (no argument follows; also whitespace-only)
_URL_TAG_DYNAMIC_RE = re.compile(r"{%\s*url\s+(?!['\"%}\s])[^%]{1,200}?%}")

URL_LITERAL_RE = re.compile(r"(['\"])([^'\"\r\n]{1,500})\1")

# Matches the named-binding brace list of an ES/TS import, tolerant of
# missing whitespace and a leading "type" modifier. Only the brace body is
# captured; the module specifier is deliberately ignored here (reverse.py's
# _IMPORT_SPEC_RE owns the module graph).
#   Matches:  import { Alpha, Beta } from './m'   -> "Alpha, Beta"
#             import{Alpha}from'./m'              -> "Alpha"
#             import type { Shape } from './s'    -> "Shape"
#             import { type Foo, Bar } from './m' -> "type Foo, Bar"
#   Captures: group 1 = the text between the braces
#   Skips:    import Foo from './m'              (default import; no brace)
#             import './side'                     (side-effect import)
#             import('./dyn')                     (dynamic import)
#             obj.import { x }                    (lookbehind blocks the ".")
#             reimport { x }                      (no word boundary before
#                                                  "import")
_TS_IMPORT_SYMBOLS_RE = re.compile(r"(?<![\w$.])import\s*(?:type\s+)?\{([^}]*)\}")

# Matches CommonJS require() bindings: a destructured brace list or a single
# default binding. The module specifier is ignored (see _TS_IMPORT_SYMBOLS_RE).
#   Matches:  const { Alpha, Beta } = require('./m') -> group 1 "Alpha, Beta"
#             const {Alpha} = require('./m')         -> group 1 "Alpha"
#             const Req = require('./r')             -> group 2 "Req"
#             let x = require('./x')                 -> group 2 "x"
#   Captures: group 1 = destructured brace body (or None)
#             group 2 = default binding name (or None)
#   Skips:    foo.require('./m')                        (no keyword before)
#             const x = import('./m')                   (ESM dynamic import)
_TS_REQUIRE_SYMBOLS_RE = re.compile(
    r"(?:const|let|var)\s+(?:\{([^}]*)\}|([A-Za-z_$][\w$]*))\s*=\s*"
    r"require\s*\(\s*['\"][^'\"]*['\"]\s*\)"
)

_TS_EXTS = (".ts", ".js", ".tsx", ".jsx")
_TS_DEFAULT_GLOBS = ("**/*.ts", "**/*.js", "**/*.tsx", "**/*.jsx")

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


MANAGEMENT_COMMAND_GLOBS = ("**/management/commands/*.py",)


def iter_command_files(root, cfg=None):
    """Yield posix paths of Django management command files under ``root``.

    Management commands are legitimate entry points (run via ``manage.py``),
    so reachability treats every def in them as reachable.
    """
    return _iter_files(root, list(MANAGEMENT_COMMAND_GLOBS), (".py",), cfg=cfg)


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
                if inc_path is not None:
                    child_path = ppath + inc_path
                    child_regex = _concat_regex(pregex, _route_regex_source(inc_path))
                else:
                    child_path = ppath + _literal_from_regex(inc_regex)
                    child_regex = _concat_regex(pregex, inc_regex)
                queue.append((target, child_path, child_regex))
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
            path_val = _str_value(node.args[0]) if node.args else None
            if path_val is None:
                continue
            handler = _handler_name(node)
            if handler is None or handler == "include":
                continue
            name_val = _str_value(_call_kwarg_value(node, "name"))
            if func.id == "path":
                composed_path = ppath + path_val
                composed_regex = _concat_regex(pregex, _route_regex_source(path_val))
                routes.append(
                    Route(
                        name_val,
                        path_val,
                        handler,
                        posix,
                        full=composed_path,
                        regex=_anchored_regex(composed_regex),
                    )
                )
            else:
                composed_path = ppath + _literal_from_regex(path_val)
                composed_regex = _concat_regex(pregex, path_val)
                routes.append(
                    Route(
                        name_val,
                        path_val,
                        handler,
                        posix,
                        full=composed_path,
                        regex=_anchored_regex(composed_regex),
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
    """Extract {% url %} template refs as (file, name) pairs.

    A literal name is kept only when it is in ``names`` (None scans all). A
    DYNAMIC tag whose name is not a quoted literal yields a ``(file, None)``
    pair: it can never be attributed to a named route, so ``match_template_refs``
    routes it to the unresolved ``dynamic-ref`` bucket and never links it.
    Dynamic refs ignore ``names`` because they carry no name to filter on.
    """
    refs = []
    for posix in _iter_files(root, template_globs, (".html",), cfg=cfg):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        for m in URL_TAG_RE.finditer(text):
            if names is None or m.group(1) in names:
                refs.append((posix, m.group(1)))
        for _ in _URL_TAG_DYNAMIC_RE.finditer(text):
            refs.append((posix, None))
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


def _call_method(match, window):
    """Resolve a call head's method: explicit dotted name, options override, or GET."""
    explicit = match.group(1)
    if explicit:
        return explicit
    opt = _METHOD_OPTION_RE.search(window)
    if opt:
        return opt.group(1).lower()
    return "get"


def _call_window_end(blanked, start):
    """Return the index just past a call's matching close paren (bounded).

    Scanning the literal-blanked copy means parens inside string/template
    literals are spaces and cannot miscount, so the window is the call's own
    argument list rather than every character up to ``_CALL_WINDOW``. An
    unbalanced call is cut at the bound.
    """
    depth = 1
    limit = min(len(blanked), start + _CALL_WINDOW)
    i = start
    while i < limit:
        ch = blanked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return limit


def _call_url(window, consts=None):
    """Return the URL literal opening a call's argument list, else None.

    Only a literal that OPENS the call's own argument list resolves: a quoted
    (single/double) or static backtick first argument, a leading concatenation
    fragment of the same call (``base + '/path/'``), or a bare identifier bound
    in the one-level ``consts`` map. A later statement's literal is never
    borrowed, so a non-literal call has no URL and yields None -- it can never
    become a pair or a false link.
    """
    stripped = window.lstrip()
    head = stripped[:1]
    if head in ("'", '"'):
        lit = QUOTED_LITERAL_RE.match(stripped)
        return lit.group(2) if lit else None
    if head == "`":
        lit = _BACKTICK_LITERAL_RE.match(stripped)
        if lit and "${" not in lit.group(1):
            return lit.group(1)
        return None
    concat = _ARG_CONCAT_RE.match(stripped)
    if concat:
        return concat.group(2)
    name = _ARG_NAME_RE.match(stripped)
    if name and consts and name.group(1) in consts:
        return consts[name.group(1)]
    return None


def iter_call_refs(text, consts=None):
    """Yield (line, method, url) for each frontend HTTP call in ``text``.

    The single canonical call-site scan: dotted ``http``/``HttpClient``/
    ``$http``/``axios`` calls, bare ``fetch()``/``axios()``, and their React
    Query wrappers. ``useQuery``/``useMutation`` are covered TRANSITIVELY
    through the ``fetch``/``axios`` call they wrap; named user-defined fetchers
    are not detected. Call-head positions are located on a literal-blanked copy
    so a call inside a string/template literal cannot match; the URL is read
    from the original ``text`` (blanking preserves length and newlines, so
    offsets and line numbers stay aligned). The window is the call's own
    argument list, so a later method option or URL literal is never borrowed.
    ``consts`` (optional) is a one-level name->literal map: a bare identifier
    first argument present in it resolves to its value.
    """
    from diffimpactscout.impact import reverse

    blanked = reverse._blank_literals(text, keep_export_names=False)
    for m in _CALL_HEAD_RE.finditer(blanked):
        end = _call_window_end(blanked, m.end())
        window = text[m.end():end]
        line = text[:m.start()].count("\n") + 1
        yield line, _call_method(m, window), _call_url(window, consts)


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
        for line, method, url in iter_call_refs(text):
            ref = _url_ref(posix, line, url, method, seen)
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


def extract_call_pairs(root, frontend_globs=None, cfg=None):
    """Return ``(method, url)`` pairs for frontend HTTP calls across call styles.

    A thin projection over the same canonical call-head scan as
    ``extract_frontend_refs`` (``iter_call_refs``: dotted
    http/HttpClient/$http/axios, bare fetch/axios, and their React Query
    wrappers), so every pair is also a linkable ref. Only URL-like string
    literals produce a pair; a non-literal call yields none and can never
    false-link.
    """
    globs = _TS_DEFAULT_GLOBS if frontend_globs is None else frontend_globs
    pairs = []
    seen = set()
    for posix in _iter_files(root, globs, _TS_EXTS, cfg=cfg, vendor=True):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        text = _strip_comments(text)
        for _line, method, url in iter_call_refs(text):
            if not _is_url_like(url):
                continue
            key = (method, url)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    return pairs


def _ts_import_names(block):
    """Extract imported binding names from a named-import clause.

    ``X as Y`` yields the original name ``X`` (the changed symbol); a bare
    ``X`` yields ``X``. A leading ``type`` modifier is ignored.
    """
    names = set()
    for part in block.split(","):
        token = part.strip()
        if not token:
            continue
        token = re.split(r"\s+as\s+", token, maxsplit=1)[0].strip()
        if token.startswith("type "):
            token = token[len("type "):].strip()
        if token:
            names.add(token)
    return names


def _ts_import_symbol_lines(text):
    """Map each imported/required symbol name to its 1-based line number.

    Covers ES named imports and CommonJS require bindings; reuses
    ``_ts_import_names`` for the binding grammar (alias left-hand side, the
    ``type`` modifier) on both the import brace list and a require brace list.
    Exported names are handled separately by ``reverse._export_lines`` so this
    helper stays import-only.
    """
    out = {}
    for m in _TS_IMPORT_SYMBOLS_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        for name in _ts_import_names(m.group(1)):
            out.setdefault(name, line)
    for m in _TS_REQUIRE_SYMBOLS_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        if m.group(1) is not None:
            for name in _ts_import_names(m.group(1)):
                out.setdefault(name, line)
        elif m.group(2):
            out.setdefault(m.group(2), line)
    return out


def extract_ts_symbol_refs(root, changed_names, frontend_globs=None, cfg=None):
    """Link changed symbol names to TS/JS files that import, require, or export them.

    A changed Python symbol may be referenced by name in a frontend module
    (``import { CustomTag } from './models'``); this emits a ``frontend-symbol``
    ref per (file, name, line) so those consumers surface instead of silence.

    Shared lexer: this reuses reverse.py's stdlib TS/JS lexer rather than a
    second parser. ``_strip_comments`` then ``_blank_literals`` blank
    string/template literals so import-looking text inside them cannot match;
    ``reverse._export_lines`` (wrapping ``_EXPORT_NAMED_RE`` /
    ``_EXPORT_BRACE_RE``) supplies exported names; ``reverse._is_graph_file``
    drops minified/bundled files. The only new logic is symbol-name extraction
    from import/require braces: reverse's graph tracks MODULE specifiers,
    whereas this links changed SYMBOL names to the files that bind them, so
    the two are intentionally distinct.

    Globs: ``frontend_globs is None`` scans the built-in ``_TS_DEFAULT_GLOBS``;
    an explicit list -- including ``[]`` -- is honored verbatim, so the generic
    profile's ``frontend_globs: []`` scans nothing (matching
    ``extract_frontend_refs``).
    """
    names_wanted = set(changed_names or ())
    refs = []
    if not names_wanted:
        return refs
    from diffimpactscout.impact import reverse

    globs = _TS_DEFAULT_GLOBS if frontend_globs is None else frontend_globs
    for posix in _iter_files(root, globs, _TS_EXTS, cfg=cfg, vendor=True):
        if not reverse._is_graph_file(posix):
            continue
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        text = _strip_comments(text)
        text = reverse._blank_literals(text, keep_export_names=False)
        found = _ts_import_symbol_lines(text)
        for name, line in reverse._export_lines(text).items():
            found.setdefault(name, line)
        for name in sorted(found.keys() & names_wanted):
            refs.append(
                {
                    "file": posix,
                    "ref": name,
                    "line": found[name],
                    "category": "frontend-symbol",
                    "confidence": "medium",
                }
            )
    return refs


def _route_regex_source(route_path):
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
    return "".join(parts)


def _route_regex(route_path):
    return re.compile("^" + _route_regex_source(route_path) + "$")


def _literal_from_regex(source):
    """Best-effort literal path for a regex prefix/route (strips ``^`` and ``$``).

    Used to compose the human-readable ``full`` URL when a path prefix and a
    regex prefix are mixed in the include chain; matching still uses ``regex``.
    """
    if not source:
        return ""
    s = source
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("$"):
        s = s[:-1]
    return s


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
        # Defensive: refs are never dynamic today (_url_ref hard-codes
        # "dynamic": False), so this branch does not fire at runtime.
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
                unresolved.append(
                    {
                        "file": file,
                        "path": path,
                        "dynamic": True,
                        "reason": "dynamic-ref",
                    }
                )
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
            unresolved.append(
                {
                    "file": file,
                    "path": path,
                    "dynamic": False,
                    "reason": "unknown-url",
                }
            )
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
    """Match extracted template refs to routes; never link a dynamic ref.

    A ``None`` name marks a dynamic {% url %} tag: it always lands in the
    unresolved bucket with reason ``dynamic-ref`` and is never linked. A
    literal name with no matching route is unresolved with reason ``unknown-url``.
    """
    by_name = {r.name: r for r in routes if r.name is not None}
    matched = []
    unresolved = []
    for file, name in refs:
        if name is None:
            unresolved.append({"file": file, "ref": None, "reason": "dynamic-ref"})
            continue
        route = by_name.get(name)
        if route is not None:
            matched.append({"route": route, "file": file, "ref": name})
        else:
            unresolved.append({"file": file, "ref": name, "reason": "unknown-url"})
    return matched, unresolved
