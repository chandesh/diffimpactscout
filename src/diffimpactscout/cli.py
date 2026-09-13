"""Command-line interface: init, guard, impact, check, and install-hooks subcommands."""

import argparse
import json
import os
import sys

import diffimpactscout.config as config
import diffimpactscout.detect as detect
import diffimpactscout.env as env
import diffimpactscout.gitrun as gitrun
import diffimpactscout.guard as guard
import diffimpactscout.launcher as launcher
import diffimpactscout.scope as scope
from diffimpactscout import __version__
from diffimpactscout.checks.base import CheckContext, make_check
from diffimpactscout.impact import impact as impact_module


def _parser():
    parser = argparse.ArgumentParser(
        prog="diffimpactscout",
        description="Incremental pre-push guard and AST blast-radius impact analyzer.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    p_init = sub.add_parser("init", help="write a .diffimpactscout.json config")
    p_init.add_argument(
        "--profile",
        choices=config.PROFILE_CHOICES,
        default=config.DEFAULT_PROFILE,
    )
    p_init.set_defaults(func=_cmd_init)

    p_guard = sub.add_parser("guard", help="run the pre-push guard checks")
    p_guard.add_argument("--staged", action="store_true")
    p_guard.add_argument("--all", action="store_true", dest="all_files")
    p_guard.add_argument("files", nargs="*")
    p_guard.set_defaults(func=_cmd_guard)

    p_impact = sub.add_parser(
        "impact", help="analyze the blast radius of the change-set"
    )
    p_impact.add_argument("--staged", action="store_true")
    p_impact.add_argument("--fast", action="store_true")
    p_impact.add_argument("--json", action="store_true")
    p_impact.set_defaults(func=_cmd_impact)

    p_check = sub.add_parser(
        "check", help="run a single check by id against files or the change-set"
    )
    p_check.add_argument(
        "check_id", metavar="CHECK_ID", help="check id, e.g. syntax/json-syntax"
    )
    p_check.add_argument("files", nargs="*")
    p_check.set_defaults(func=_cmd_check)

    p_hooks = sub.add_parser("install-hooks", help="install the pre-push git hook")
    p_hooks.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing hook that diffimpactscout did not install",
    )
    p_hooks.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the diffimpactscout pre-push hook",
    )
    p_hooks.add_argument(
        "--profile",
        choices=config.PROFILE_CHOICES,
        help="pre-select the setup profile (skips the stack question)",
    )
    p_hooks.add_argument(
        "--blocking",
        choices=("warn", "strict"),
        help="blocking mode for guard checks",
    )
    p_hooks.add_argument(
        "--yes", "-y", action="store_true", help="accept defaults without prompts"
    )
    p_hooks.add_argument(
        "--reconfigure",
        action="store_true",
        help="rewrite an existing .diffimpactscout.json",
    )
    p_hooks.set_defaults(func=_cmd_install_hooks)

    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    return args.func(args)


def _require_repo():
    root = gitrun.repo_root()
    if root:
        return root
    sys.stderr.write("diffimpactscout: not a git repository (run inside a repo)\n")
    return None


def _cmd_init(args):
    data = config._defaults()
    data = config._deep_merge(data, config.load_profile(args.profile))
    path = os.path.join(os.getcwd(), config.CFG_NAME)
    if os.path.exists(path):
        print("diffimpactscout: %s already exists; leaving it unchanged" % path)
        return 0
    try:
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write(
            "diffimpactscout: cannot write %s: %s\n" % (path, exc)
        )
        return 1
    return 0


def _cmd_guard(args):
    root = _require_repo()
    if root is None:
        return 1
    cfg = config.load_config(root)
    return guard.run_guard(
        root,
        cfg,
        staged=args.staged,
        all_files=args.all_files,
        files=args.files or None,
    )


def _cmd_impact(args):
    root = _require_repo()
    if root is None:
        return 1
    cfg = config.load_config(root)
    return impact_module.run_impact(
        root,
        cfg,
        staged=args.staged,
        fast=args.fast,
        json_out=args.json,
    )


def _cmd_check(args):
    root = _require_repo()
    if root is None:
        return 1
    try:
        check = make_check({"id": args.check_id})
    except Exception as exc:
        sys.stderr.write(
            "diffimpactscout: check %r invalid: %s\n" % (args.check_id, exc)
        )
        return 1
    if check is None:
        sys.stderr.write("diffimpactscout: unknown check %r\n" % args.check_id)
        return 1
    cfg = config.load_config(root)
    dev_scope = scope.DevScope(root)
    from_ref, to_ref = env.pre_commit_refs()
    if args.files:
        for path in args.files:
            if not os.path.exists(path):
                sys.stderr.write("diffimpactscout: no such file: %s\n" % path)
                return 1
        files = list(args.files)
        anchor = None
    else:
        anchor = dev_scope.scope_base(from_ref, to_ref)
        files = dev_scope.dev_files(anchor, from_ref, to_ref)
    ctx = CheckContext(
        root=root,
        anchor=anchor,
        from_ref=from_ref,
        to_ref=to_ref,
        scope=dev_scope,
        config=cfg,
        echo=True,
    )
    check_files = [] if check.scoped == "repo" else files
    try:
        result = check.run(ctx, check_files)
    except Exception as exc:
        sys.stderr.write(
            "diffimpactscout: check %r failed: %s\n" % (args.check_id, exc)
        )
        return 1
    if result is None:
        sys.stderr.write(
            "diffimpactscout: check %r produced no result\n" % args.check_id
        )
        return 1
    for issue in result.issues:
        print(issue.format())
    for path in result.fixed:
        print("[fixed] %s" % path)
    for text in result.warned:
        sys.stderr.write("diffimpactscout: warning: %s\n" % text)
    return 1 if result.has_issues() else 0


def _is_tty():
    return sys.stdin.isatty()


def _read_answer():
    try:
        return (sys.stdin.readline() or "").strip().lower()
    except (OSError, ValueError):
        return ""


def _prompt_yes_default(text, default):
    sys.stdout.write(text + " ")
    sys.stdout.flush()
    answer = _read_answer()
    if answer in ("y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    return default


def _prompt_choice(text, choices, default):
    sys.stdout.write("%s [%s] (default: %s): " % (text, "/".join(choices), default))
    sys.stdout.flush()
    answer = _read_answer()
    if answer in choices:
        return answer
    return default


def _check_id(entry):
    if isinstance(entry, dict):
        return entry.get("id", "?")
    return str(entry)


def _resolve_profile(args, detected):
    if args.profile:
        return args.profile
    if detected in config.PROFILE_CHOICES:
        return detected
    return config.DEFAULT_PROFILE


def _render_preview(detected, profile, cfg):
    checks = cfg["guard"]["checks"]
    impact_on = cfg["impact"].get("profile") != config.DEFAULT_PROFILE
    blocking = cfg["guard"].get("blocking", "warn")
    lines = []
    if detected == profile:
        lines.append("detected framework: %s" % detected)
    lines.append("")
    lines.append("diffimpactscout will write .diffimpactscout.json")
    lines.append("  profile   : %s" % profile)
    lines.append("  blocking  : %s" % blocking)
    lines.append("  impact    : %s" % ("on" if impact_on else "off"))
    lines.append("  checks    : %d" % len(checks))
    for entry in checks:
        lines.append("    - %s" % _check_id(entry))
    lines.append("")
    lines.append(
        "  then installs the pre-push hook -> runs `diffimpactscout guard` on each push."
    )
    lines.append("")
    return "\n".join(lines)


def _build_config_data(profile, args):
    data = config._defaults()
    data = config._deep_merge(data, config.load_profile(profile))
    if args.blocking:
        data["guard"]["blocking"] = args.blocking
    return data


def _write_config(path, data):
    try:
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("diffimpactscout: cannot write %s: %s\n" % (path, exc))
        return 1
    return 0


_LINT_HINT = {
    "django": "ruff + ruff-format",
    "fastapi": "ruff + ruff-format",
    "python": "ruff + ruff-format",
    "web": "eslint + prettier",
}


def _ask_overrides(data, args):
    profile = data["impact"].get("profile") or config.DEFAULT_PROFILE
    if profile not in config.PROFILE_CHOICES:
        profile = config.DEFAULT_PROFILE
    profile = _prompt_choice("Profile", list(config.PROFILE_CHOICES), profile)
    merged = config._deep_merge(config._defaults(), config.load_profile(profile))
    if args.blocking:
        merged["guard"]["blocking"] = args.blocking
    data["guard"] = merged["guard"]
    data["impact"] = merged["impact"]
    if not args.blocking:
        strict = _prompt_yes_default(
            "Block the push when checks report issues? (strict mode) [y/N]", False
        )
        data["guard"]["blocking"] = "strict" if strict else "warn"
    hint = _LINT_HINT.get(profile)
    if hint:
        keep = _prompt_yes_default(
            "Keep the extra lint checks? (%s) [Y/n]" % hint, True
        )
        if not keep:
            data["guard"]["checks"] = [dict(c) for c in config.DEFAULT_GUARD_CHECKS]
    impact_on = _prompt_yes_default(
        "Enable impact analysis? [Y/n]",
        merged["impact"].get("profile") != config.DEFAULT_PROFILE,
    )
    if not impact_on:
        generic = config._deep_merge(
            config._defaults(), config.load_profile("generic")
        )
        data["impact"] = generic["impact"]


def _cmd_install_hooks(args):
    root = _require_repo()
    if root is None:
        return 1
    if args.uninstall:
        try:
            removed = launcher.uninstall_hook(root)
        except OSError as exc:
            sys.stderr.write(
                "diffimpactscout: cannot remove pre-push hook: %s\n" % exc
            )
            return 1
        if removed:
            print("pre-push hook removed")
        else:
            print("nothing to remove")
        return 0
    cfg_path = os.path.join(root, config.CFG_NAME)
    has_config = os.path.exists(cfg_path)
    if has_config and not args.reconfigure:
        print(
            "diffimpactscout: %s already exists; leaving it unchanged "
            "(pass --reconfigure to rewrite)" % config.CFG_NAME
        )
    else:
        detected = detect.detect_stack(root)
        profile = _resolve_profile(args, detected)
        data = _build_config_data(profile, args)
        sys.stdout.write(_render_preview(detected, profile, data))
        interactive = not args.yes and _is_tty()
        proceed = True
        if interactive:
            proceed = _prompt_yes_default("Proceed? [Y/n]", True)
            if not proceed:
                _ask_overrides(data, args)
                sys.stdout.write(_render_preview(detected, profile, data))
                proceed = _prompt_yes_default("Proceed? [Y/n]", True)
        if proceed:
            rc = _write_config(cfg_path, data)
            if rc != 0:
                return rc
    try:
        installed = launcher.install_hook(root, force=args.force)
    except OSError as exc:
        sys.stderr.write(
            "diffimpactscout: cannot install pre-push hook: %s\n" % exc
        )
        return 1
    if not installed:
        return 1
    print("pre-push hook installed")
    return 0