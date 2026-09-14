import os

import diffimpactscout.detect as detect


def _make_repo(tmp_path, files):
    repo = str(tmp_path)
    for rel, content in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    return repo


def _files(*pairs):
    return {rel: "" for rel in pairs}


def test_detect_django(tmp_path):
    """Verifies detect_stack identifies a Django project layout."""
    repo = _make_repo(
        tmp_path,
        _files("manage.py", "project/settings.py", "project/wsgi.py", "app/models.py"),
    )
    assert detect.detect_stack(repo) == "django"


def test_detect_django_manage_py_alone_is_python(tmp_path):
    """Checks that a lone manage.py is detected as python, not django."""
    repo = _make_repo(tmp_path, _files("manage.py"))
    assert detect.detect_stack(repo) == "python"


def test_detect_fastapi_requirements(tmp_path):
    """Verifies detect_stack detects fastapi via requirements.txt."""
    repo = _make_repo(tmp_path, {"requirements.txt": "fastapi==0.100.0\n", "main.py": "app = None\n"})
    assert detect.detect_stack(repo) == "fastapi"


def test_detect_fastapi_main_import(tmp_path):
    """Checks detect_stack detects fastapi via a main.py import."""
    repo = _make_repo(tmp_path, {"main.py": "from fastapi import FastAPI\napp = FastAPI()\n"})
    assert detect.detect_stack(repo) == "fastapi"


def test_detect_web_package_and_frontend(tmp_path):
    """Verifies detect_stack identifies a web package as frontend."""
    repo = _make_repo(
        tmp_path,
        _files("package.json", "src/orders.service.ts", "app/orders.js", "index.html"),
    )
    assert detect.detect_stack(repo) == "frontend"


def test_detect_web_requires_frontend_sources(tmp_path):
    """Checks that a web package without frontend sources is generic."""
    repo = _make_repo(tmp_path, _files("package.json", "README.md"))
    assert detect.detect_stack(repo) == "generic"


def test_detect_python(tmp_path):
    """Verifies detect_stack identifies a python package layout."""
    repo = _make_repo(tmp_path, _files("app/__init__.py", "app/service.py"))
    assert detect.detect_stack(repo) == "python"


def test_detect_ignores_venv_and_node_modules(tmp_path):
    """Checks that venv and node_modules files are ignored by detection."""
    repo = _make_repo(tmp_path, _files("venv/lib/pkg/x.py"))
    assert detect.detect_stack(repo) == "generic"


def test_detect_generic_empty(tmp_path):
    """Verifies an empty project detects as generic."""
    repo = _make_repo(tmp_path, _files("README.md", "base.txt"))
    assert detect.detect_stack(repo) == "generic"


def test_describe_other_stack_go(tmp_path):
    """Checks describe_other_stack identifies a Go project."""
    repo = _make_repo(tmp_path, _files("go.mod", "main.go"))
    assert detect.describe_other_stack(repo) == "Go"


def test_describe_other_stack_none(tmp_path):
    """Verifies describe_other_stack returns None for an unknown project."""
    repo = _make_repo(tmp_path, _files("base.txt"))
    assert detect.describe_other_stack(repo) is None