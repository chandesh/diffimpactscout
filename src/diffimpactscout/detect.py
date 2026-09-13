"""Detect the project stack for first-run setup.

Example: a repo with manage.py plus settings.py resolves to 'django'; a
repo containing only go.mod falls back to the generic profile.
"""

import os

_IGNORED_DIRS = {".git", "node_modules", "venv", ".venv", "env", "dist", "build"}

_OTHER_STACKS = (
    ("Go", "go.mod"),
    ("Rust", "Cargo.toml"),
    ("Java", "pom.xml"),
    ("Ruby", "Gemfile"),
)


def _walk(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for name in files:
            yield os.path.join(base, name)


def _has(root, rel):
    return os.path.exists(os.path.join(root, rel))


def _mentions(root, name, token):
    path = os.path.join(root, name)
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        return token in fh.read()


def _has_django(root):
    if not _has(root, "manage.py"):
        return False
    for path in _walk(root):
        if path.endswith("settings.py") or path.endswith("wsgi.py"):
            return True
    return False


def _has_fastapi(root):
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        if _mentions(root, name, "fastapi"):
            return True
    main = os.path.join(root, "main.py")
    if os.path.exists(main):
        with open(main) as fh:
            return "fastapi" in fh.read()
    return False


def _has_web(root):
    if not _has(root, "package.json"):
        return False
    for path in _walk(root):
        parts = path.split(os.sep)
        if ("src" in parts or "app" in parts) and path.endswith((".ts", ".js")):
            return True
    return False


def _has_python(root):
    for path in _walk(root):
        if path.endswith(".py"):
            return True
    return False


def detect_stack(root):
    if _has_django(root):
        return "django"
    if _has_fastapi(root):
        return "fastapi"
    if _has_web(root):
        return "web"
    if _has_python(root):
        return "python"
    return "generic"


def describe_other_stack(root):
    for label, marker in _OTHER_STACKS:
        if _has(root, marker):
            return label
    return None