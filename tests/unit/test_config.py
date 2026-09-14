import json
import os
import subprocess

import diffimpactscout.config as config


def _git(*args, cwd):
    return subprocess.check_call(["git"] + list(args), cwd=cwd)


def _write_cfg(root, data):
    with open(os.path.join(root, config.CFG_NAME), "w") as fh:
        json.dump(data, fh)


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git("init", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    return repo


def test_load_config_defaults_when_no_file(tmp_path):
    """Verifies that default config values are used when no config file exists."""
    cfg = config.load_config(str(tmp_path))
    assert cfg["mode"] == config.MODE_PRE_PUSH
    assert cfg["impact"]["profile"] == config.DEFAULT_PROFILE
    assert cfg["guard"]["blocking"] == "warn"
    assert cfg["impact"]["cache_file"] == ".impact_analysis_cache.json"
    assert cfg["impact"]["fast_mode"] is False
    assert cfg["impact"]["threads"] == 4
    assert "**/node_modules/**" in cfg["ignore_paths"]
    assert "**/.git/**" in cfg["ignore_paths"]
    ids = [c["id"] for c in cfg["guard"]["checks"]]
    assert ids == [
        "hygiene/mixed-line-ending",
        "hygiene/trailing-whitespace",
        "hygiene/end-of-file-fixer",
        "syntax/json-syntax",
        "syntax/ast-syntax",
        "syntax/merge-conflict",
        "repo/large-files",
        "repo/private-key",
        "repo/case-conflict",
    ]
    assert cfg["guard"]["checks"][6]["args"] == ["--maxkb=250000"]


def test_deep_merge_nested_dicts_and_scalars():
    """Verifies deep merging of nested dicts and scalar overrides."""
    base = {"a": 1, "n": {"x": 1}}
    override = {"a": 2, "n": {"y": 2}, "b": 3}
    merged = config._deep_merge(base, override)
    assert merged == {"a": 2, "n": {"x": 1, "y": 2}, "b": 3}


def test_deep_merge_lists_concatenate():
    """Checks that deep merging concatenates lists."""
    merged = config._deep_merge({"l": [1]}, {"l": [2]})
    assert merged["l"] == [1, 2]


def test_load_config_profile_overrides_defaults(tmp_path):
    """Verifies that a selected profile overrides default config values."""
    _write_cfg(tmp_path, {"impact": {"profile": "django"}})
    cfg = config.load_config(str(tmp_path))
    assert cfg["impact"]["profile"] == "django"
    assert "**/urls.py" in cfg["impact"]["urls_globs"]
    assert "**/templates/**/*.html" in cfg["impact"]["template_globs"]
    assert "**/src/**/*.ts" in cfg["impact"]["frontend_globs"]
    ids = [c["id"] for c in cfg["guard"]["checks"]]
    assert "hygiene/mixed-line-ending" in ids
    assert "ruff" in ids
    assert "ruff-format" in ids


def test_load_config_user_overrides_profile_and_defaults(tmp_path):
    """Verifies that user config overrides both profile and default values."""
    _write_cfg(
        tmp_path,
        {"impact": {"profile": "django", "threads": 8}, "guard": {"blocking": "strict"}},
    )
    cfg = config.load_config(str(tmp_path))
    assert cfg["impact"]["threads"] == 8
    assert cfg["guard"]["blocking"] == "strict"
    assert cfg["impact"]["profile"] == "django"
    assert "**/urls.py" in cfg["impact"]["urls_globs"]


def test_user_guard_checks_replace_not_merge(tmp_path):
    """Checks that user guard checks replace the default list rather than merge."""
    _write_cfg(tmp_path, {"guard": {"checks": [{"id": "custom-check"}]}})
    cfg = config.load_config(str(tmp_path))
    assert [c["id"] for c in cfg["guard"]["checks"]] == ["custom-check"]


def test_load_config_invalid_json_fails_open(tmp_path, capsys):
    """Checks that invalid JSON config fails open with a warning."""
    with open(os.path.join(str(tmp_path), config.CFG_NAME), "w") as fh:
        fh.write("{ not valid json")
    cfg = config.load_config(str(tmp_path))
    assert cfg["guard"]["blocking"] == "warn"
    assert cfg["impact"]["profile"] == config.DEFAULT_PROFILE
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert captured.out == ""


def test_load_profile_django():
    """Verifies the django profile's impact globs and guard checks."""
    prof = config.load_profile("django")
    assert prof["impact"]["profile"] == "django"
    assert prof["impact"]["urls_globs"] == ["**/urls.py"]
    assert prof["impact"]["template_globs"] == ["**/templates/**/*.html"]
    assert "**/src/**/*.ts" in prof["impact"]["frontend_globs"]
    ids = [c["id"] for c in prof["guard"]["checks"]]
    assert ids == ["ruff", "ruff-format"]


def test_load_profile_fastapi():
    """Verifies the fastapi profile's impact globs and guard checks."""
    prof = config.load_profile("fastapi")
    assert prof["impact"]["profile"] == "fastapi"
    assert prof["impact"]["template_globs"] == []
    assert "**/src/**/*.ts" in prof["impact"]["frontend_globs"]
    assert "**/urls.py" not in prof["impact"]["urls_globs"]
    ids = [c["id"] for c in prof["guard"]["checks"]]
    assert ids == ["ruff", "ruff-format"]


def test_load_profile_generic():
    """Verifies the generic profile has empty impact globs and no guard config."""
    prof = config.load_profile("generic")
    assert prof["impact"]["profile"] == "generic"
    assert prof["impact"]["urls_globs"] == []
    assert prof["impact"]["template_globs"] == []
    assert prof["impact"]["frontend_globs"] == []
    assert "guard" not in prof


def test_load_profile_python():
    """Verifies the python profile's impact config and guard checks."""
    prof = config.load_profile("python")
    assert prof["impact"]["profile"] == "python"
    assert prof["impact"]["urls_globs"] == []
    prof_ids = [c["id"] for c in prof["guard"]["checks"]]
    assert prof_ids == ["ruff", "ruff-format"]


def test_load_profile_frontend():
    """Verifies the frontend profile's impact globs and guard checks."""
    prof = config.load_profile("frontend")
    assert prof["impact"]["profile"] == "frontend"
    assert "**/*.html" in prof["impact"]["template_globs"]
    assert "**/src/**/*.ts" in prof["impact"]["frontend_globs"]
    prof_ids = [c["id"] for c in prof["guard"]["checks"]]
    assert prof_ids == ["eslint", "prettier"]


def test_all_profile_choices_loadable():
    """Checks that every configured profile choice is loadable."""
    for name in config.PROFILE_CHOICES:
        prof = config.load_profile(name)
        assert "impact" in prof, "profile %s missing impact config" % name


def test_is_excluded_via_ignore_paths(tmp_path):
    """Checks that ignore_paths cause matching paths to be excluded."""
    cfg = {"ignore_paths": ["**/node_modules/**"]}
    assert config.is_excluded("src/node_modules/x/y.js", cfg, str(tmp_path)) is True
    assert config.is_excluded("src/app.py", cfg, str(tmp_path)) is False


def test_is_excluded_strips_leading_dot_slash(tmp_path):
    """Checks that a leading ./ is stripped before matching ignore paths."""
    cfg = {"ignore_paths": ["**/node_modules/**"]}
    assert config.is_excluded("./src/node_modules/x/y.js", cfg, str(tmp_path)) is True


def test_is_excluded_via_gitignore(tmp_path):
    """Checks that gitignore rules exclude matching paths when enabled."""
    repo = _make_repo(tmp_path)
    with open(os.path.join(repo, ".gitignore"), "w") as fh:
        fh.write("generated/\n")
    cfg = {"use_gitignore": True}
    assert config.is_excluded("generated/out.txt", cfg, repo) is True
    assert config.is_excluded("a.txt", cfg, repo) is False


def test_is_excluded_git_failure_does_not_raise(tmp_path):
    """Checks that a git failure during exclusion check does not raise."""
    cfg = {"use_gitignore": True}
    assert config.is_excluded("a.txt", cfg, str(tmp_path)) is False


def test_is_excluded_root_level_ignore(tmp_path):
    """Checks that root-level ignore patterns exclude matching paths."""
    cfg = {"ignore_paths": ["**/node_modules/**", "**/dist/**"]}
    assert config.is_excluded("node_modules/x.js", cfg, str(tmp_path)) is True
    assert config.is_excluded("node_modules", cfg, str(tmp_path)) is True
    assert config.is_excluded("src/node_modules/x/y.js", cfg, str(tmp_path)) is True
    assert config.is_excluded("dist/x", cfg, str(tmp_path)) is True
    assert config.is_excluded("a/dist/x", cfg, str(tmp_path)) is True
    assert config.is_excluded("src/app.py", cfg, str(tmp_path)) is False


def test_is_excluded_leading_double_star_patterns(tmp_path):
    """Checks that leading ** glob patterns match at any depth."""
    cfg = {"ignore_paths": ["**/urls.py", "**/*.py"]}
    assert config.is_excluded("urls.py", cfg, str(tmp_path)) is True
    assert config.is_excluded("app/urls.py", cfg, str(tmp_path)) is True
    assert config.is_excluded("main.py", cfg, str(tmp_path)) is True
    assert config.is_excluded("src/main.py", cfg, str(tmp_path)) is True


def test_matches_glob_flat_templates():
    """Checks that flat and nested template paths match the template glob."""
    assert (
        config._matches_glob("app/templates/orders.html", "**/templates/**/*.html")
        is True
    )
    assert (
        config._matches_glob("templates/orders.html", "**/templates/**/*.html")
        is True
    )
    assert (
        config._matches_glob("app/templates/base/orders.html", "**/templates/**/*.html")
        is True
    )


def test_load_config_unknown_profile_fails_open(tmp_path, capsys):
    """Checks that an unknown profile falls back to defaults with a warning."""
    _write_cfg(tmp_path, {"impact": {"profile": "django2"}})
    cfg = config.load_config(str(tmp_path))
    assert cfg["impact"]["profile"] == config.DEFAULT_PROFILE
    assert cfg["guard"]["blocking"] == "warn"
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert captured.out == ""


def test_load_config_null_profile_fails_open(tmp_path, capsys):
    """Checks that a null profile falls back to defaults."""
    _write_cfg(tmp_path, {"impact": {"profile": None}})
    cfg = config.load_config(str(tmp_path))
    assert cfg["impact"]["profile"] == config.DEFAULT_PROFILE
    assert cfg["guard"]["blocking"] == "warn"


def test_load_config_non_list_guard_checks_ignored(tmp_path, capsys):
    """Checks that a non-list guard checks value is ignored with a warning."""
    _write_cfg(tmp_path, {"guard": {"checks": "not-a-list"}})
    cfg = config.load_config(str(tmp_path))
    ids = [c["id"] for c in cfg["guard"]["checks"]]
    assert ids[0] == "hygiene/mixed-line-ending"
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert captured.out == ""


def test_load_config_user_use_gitignore_flows_through(tmp_path):
    """Verifies that a user's use_gitignore setting flows through config."""
    _write_cfg(tmp_path, {"use_gitignore": True})
    cfg = config.load_config(str(tmp_path))
    assert cfg["use_gitignore"] is True


def test_load_config_default_use_gitignore_false(tmp_path):
    """Checks that use_gitignore defaults to False when unset."""
    cfg = config.load_config(str(tmp_path))
    assert cfg["use_gitignore"] is False


def test_deep_merge_list_vs_scalar():
    """Checks that a scalar overrides a list during deep merge."""
    assert config._deep_merge({"l": [1]}, {"l": "x"}) == {"l": "x"}
    assert config._deep_merge({"l": "x"}, {"l": [1]}) == {"l": [1]}


def test_deep_merge_nested_scalar_override():
    """Checks that a scalar overrides a nested dict during deep merge."""
    assert config._deep_merge({"n": {"x": 1}}, {"n": 5}) == {"n": 5}