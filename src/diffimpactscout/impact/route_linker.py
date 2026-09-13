"""Extract Django and FastAPI routes, then link template and frontend references to those routes.

Example: a template tag {% url order-list %} resolves to the changed view
handler that backs the order-list route.
"""

import ast
import os
import re

from diffimpactscout.config import _matches_glob


class Route(object):
    def __init__(self, name, path, handler, module):
        self.name = name
        self.path = path
        self.handler = handler
        self.module = module


_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options")
_HTTP_METHOD_RE_SRC = r"get|post|put|delete|patch"

HTTP_CALL_RE = re.compile(
    r"(\$http|HttpClient|http)\s*\.\s*(" + _HTTP_METHOD_RE_SRC + r")\s*\("
)
QUOTED_LITERAL_RE = re.compile(r"(['\"])([^'\"\r\n]{1,500})\1")
URL_TAG_RE = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]\s*%}")


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


def _iter_files(root, globs, exts):
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
            for pattern in globs:
                if _matches_glob(posix, pattern):
                    yield posix
                    break


def _parse_file(full):
    text = _read_text(full)
    if text is None:
        return None
    try:
        return ast.parse(text)
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


def extract_django_routes(root, urls_globs):
    routes = []
    for posix in _iter_files(root, urls_globs, (".py",)):
        full = os.path.join(root, posix) if root else posix
        tree = _parse_file(full)
        if tree is None:
            continue
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
            routes.append(Route(name_val, path_val, handler, posix))
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


def extract_fastapi_routes(root, py_globs):
    routes = []
    for posix in _iter_files(root, py_globs, (".py",)):
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


def extract_template_refs(root, template_globs):
    refs = []
    for posix in _iter_files(root, template_globs, (".html",)):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        for m in URL_TAG_RE.finditer(text):
            refs.append((posix, m.group(1)))
    return refs


def _block_repl(match):
    return "\n" * match.group(0).count("\n")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", _block_repl, text, flags=re.S)
    text = re.sub(r"(?m)(?<!:)//.*$", "", text)
    return text


def extract_frontend_refs(root, frontend_globs):
    refs = []
    for posix in _iter_files(root, frontend_globs, (".ts", ".js", ".tsx", ".jsx")):
        full = os.path.join(root, posix) if root else posix
        text = _read_text(full)
        if text is None:
            continue
        text = _strip_comments(text)
        for m in HTTP_CALL_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            method = m.group(2)
            window = text[m.end():m.end() + 300]
            stripped = window.lstrip()
            if stripped[:1] in ("'", '"'):
                lit = QUOTED_LITERAL_RE.match(stripped)
                if lit:
                    refs.append(
                        {
                            "file": posix,
                            "ref": lit.group(2),
                            "dynamic": False,
                            "line": line,
                            "method": method,
                        }
                    )
                    continue
            litm = QUOTED_LITERAL_RE.search(window)
            literal = litm.group(2) if litm else None
            refs.append(
                {
                    "file": posix,
                    "ref": literal,
                    "dynamic": True,
                    "line": line,
                    "method": method,
                }
            )
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


def _exact_route(ref_path, routes):
    norm = _norm_slash(ref_path)
    for route in routes:
        if _norm_slash(route.path) == norm:
            return route
    return None


def _best_route_match(ref_path, routes):
    ref_norm = _norm_slash(ref_path)
    for route in routes:
        if _norm_slash(route.path) == ref_norm:
            return route
    for route in routes:
        if _route_regex(_norm_slash(route.path or "")).match(ref_norm):
            return route
    for route in routes:
        route_norm = _norm_slash(route.path or "")
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
