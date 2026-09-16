"""Check that the tools referenced by the guard checks and impact profile are available.

Example: a Django profile that enables ruff/ruff-format reports `ruff` as
missing with an install hint the first time hooks are set up.
"""

import shutil
import sys

_CHECK_TOOLS = (
    ("ruff", "ruff", "pip install ruff"),
    ("eslint", "eslint", "npm install --save-dev eslint"),
    ("prettier", "prettier", "npm install --save-dev prettier"),
)

_TOOL_CHECK_IDS = {
    "ruff": "ruff",
    "ruff-format": "ruff",
    "eslint": "eslint",
    "prettier": "prettier",
}


def required_tools(cfg):
    """Return the set of tool names referenced by the current config."""
    tools = set()
    for entry in cfg.get("guard", {}).get("checks") or []:
        if isinstance(entry, dict):
            check_id = entry.get("id")
        else:
            check_id = entry
        mapped = _TOOL_CHECK_IDS.get(check_id)
        if mapped:
            tools.add(mapped)
    return sorted(tools)


def _resolve(binary):
    return shutil.which(binary) or shutil.which(binary + ".exe")


def check_tools(cfg):
    """Return a list of {"tool", "found", "hint"} for every config-referenced tool."""
    results = []
    for tool, binary, hint in _CHECK_TOOLS:
        if tool in required_tools(cfg):
            results.append(
                {
                    "tool": tool,
                    "binary": binary,
                    "found": _resolve(binary) is not None,
                    "hint": hint,
                }
            )
    return results


def run_dependency_check(root, cfg):
    results = check_tools(cfg)
    if not results:
        print("No external tools are referenced by the current configuration.")
        return 0
    missing = [r for r in results if not r["found"]]
    print("=" * 70)
    print("  DiffImpactScout Dependency Check")
    print("=" * 70)
    for result in results:
        state = "OK" if result["found"] else "MISSING"
        location = ""
        if result["found"]:
            location = " (%s)" % _resolve(result["binary"])
        print(
            "  [%s] %s%s" % (state, result["tool"], location)
        )
    print("-" * 70)
    if not missing:
        print("  All referenced tools are available.")
        print("=" * 70)
        return 0
    for result in missing:
        sys.stderr.write(
            "diffimpactscout: %s is required by the guard checks but was not "
            "found. Install it with: %s\n" % (result["tool"], result["hint"])
        )
    sys.stderr.write(
        "diffimpactscout: missing tools degrade the affected checks to warnings; "
        "install them to enforce the full guard.\n"
    )
    print("=" * 70)
    return 1


def report_missing(cfg, tty=None):
    """Print a non-fatal warning for every referenced-but-missing tool."""
    if tty is None:
        try:
            tty = sys.stdin.isatty()
        except (OSError, ValueError):
            tty = False
    if not tty:
        return
    missing = [r for r in check_tools(cfg) if not r["found"]]
    for result in missing:
        sys.stderr.write(
            "diffimpactscout: %s was not found; the affected checks will only "
            "warn until it is installed (%s).\n"
            % (result["tool"], result["hint"])
        )