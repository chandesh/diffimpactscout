"""Load and merge the .diffimpactscout.json config, built-in profiles, and glob-based file exclusions."""

import fnmatch
import json
import os
import sys

import diffimpactscout.gitrun as gitrun

CFG_NAME = ".diffimpactscout.json"
DEFAULT_PROFILE = "generic"
PROFILE_CHOICES = ("generic", "django", "fastapi", "python", "web")
MODE_PRE_PUSH = "pre-push"

DEFAULT_IGNORE_PATHS = [
    "**/node_modules/**",
    "**/venv/**",
    "**/.venv/**",
    "**/migrations/**",
    "**/staticfiles/**",
    "**/dist/**",
    "**/build/**",
    "**/.git/**",
]

DEFAULT_GUARD_CHECKS = [
    {"id": "hygiene/mixed-line-ending"},
    {"id": "hygiene/trailing-whitespace"},
    {"id": "hygiene/end-of-file-fixer"},
    {"id": "syntax/json-syntax"},
    {"id": "syntax/ast-syntax"},
    {"id": "syntax/merge-conflict"},
    {"id": "repo/large-files", "args": ["--maxkb=250000"]},
    {"id": "repo/private-key"},
]


def _defaults():
    return {
        "version": 1,
        "mode": MODE_PRE_PUSH,
        "ignore_paths": list(DEFAULT_IGNORE_PATHS),
        "use_gitignore": False,
        "guard": {
            "checks": [dict(c) for c in DEFAULT_GUARD_CHECKS],
            "blocking": "warn",
        },
        "impact": {
            "profile": DEFAULT_PROFILE,
            "urls_globs": [],
            "template_globs": [],
            "frontend_globs": [],
            "cache_file": ".impact_analysis_cache.json",
            "fast_mode": False,
            "threads": 4,
        },
    }


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return base + override
    return override


def _fallback_profile():
    return {
        "impact": {
            "profile": DEFAULT_PROFILE,
            "urls_globs": [],
            "template_globs": [],
            "frontend_globs": [],
        }
    }


def load_profile(name):
    if not isinstance(name, str):
        print(
            "warning: invalid profile %r; using %r" % (name, DEFAULT_PROFILE),
            file=sys.stderr,
        )
        name = DEFAULT_PROFILE
    path = os.path.join(os.path.dirname(__file__), "profiles", name + ".json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        print(
            "warning: profile %r unavailable (%s); using %r"
            % (name, exc, DEFAULT_PROFILE),
            file=sys.stderr,
        )
        return _fallback_profile()
    if not isinstance(data, dict):
        print(
            "warning: profile %r is not an object; using %r"
            % (name, DEFAULT_PROFILE),
            file=sys.stderr,
        )
        return _fallback_profile()
    return data


def _read_user(root):
    path = os.path.join(root, CFG_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        print("warning: %s: %s" % (path, exc), file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print("warning: %s: expected a JSON object" % path, file=sys.stderr)
        return None
    return data


def load_config(root):
    cfg = _defaults()
    user = _read_user(root)
    profile = DEFAULT_PROFILE
    if user is not None and isinstance(user.get("impact"), dict):
        profile = user["impact"].get("profile") or DEFAULT_PROFILE
    profile_data = load_profile(profile)
    cfg = _deep_merge(cfg, profile_data)
    if user is not None:
        cfg = _deep_merge(cfg, user)
        if isinstance(user.get("guard"), dict) and "checks" in user["guard"]:
            if isinstance(user["guard"]["checks"], list):
                cfg["guard"]["checks"] = user["guard"]["checks"]
            else:
                cfg["guard"]["checks"] = _defaults()["guard"]["checks"]
                print("warning: guard.checks must be a list; ignoring", file=sys.stderr)
    cfg["impact"]["profile"] = profile_data.get("impact", {}).get(
        "profile", DEFAULT_PROFILE
    )
    return cfg


def _collapse_mid(pattern):
    i = 0
    while True:
        idx = pattern.find("/**/", i)
        if idx == -1:
            return None
        if idx > 0:
            return pattern[:idx] + "/" + pattern[idx + 4:]
        i = idx + 1


def _glob_variants(pattern):
    out = [pattern]
    lead = pattern
    while lead.startswith("**/"):
        lead = lead[3:]
    if lead != pattern:
        out.append(lead)
    if pattern.endswith("/**"):
        out.append(pattern[:-3])
        if lead != pattern and lead.endswith("/**"):
            out.append(lead[:-3])
    for base in (pattern, lead):
        collapsed = _collapse_mid(base)
        if collapsed is not None and collapsed not in out:
            out.append(collapsed)
    return out


def _path_suffixes(path):
    parts = path.split("/")
    return ["/".join(parts[i:]) for i in range(len(parts))]


def _matches_glob(path, pattern):
    if path.startswith("./"):
        path = path[2:]
    for variant in _glob_variants(pattern):
        for candidate in _path_suffixes(path):
            if fnmatch.fnmatch(candidate, variant):
                return True
    return False


def is_excluded(path, cfg, root):
    norm = path[2:] if path.startswith("./") else path
    for pattern in cfg.get("ignore_paths") or []:
        if _matches_glob(norm, pattern):
            return True
    if cfg.get("use_gitignore"):
        try:
            if gitrun.git_ok(["check-ignore", "--quiet", norm], root):
                return True
        except Exception:
            pass
    return False