"""Pre-push hook lifecycle.

Handles everything about the git pre-push hook: resolves where hook files
live (core.hooksPath, linked-worktree common dirs), renders the hook body
that forwards the pushed refs from stdin so the guard checks exactly the
range being pushed, falls back to PATH or the recorded interpreter, warns
without blocking when the tool is missing, and installs, inspects, and
removes the hook without overwriting a foreign one unless forced.
"""

import os
import sys

import diffimpactscout.gitrun as gitrun

MARKER = "# diffimpactscout pre-push hook"
HOOK_NAME = "pre-push"


def _sh_single_quote(path):
    return "'" + path.replace("'", "'\"'\"'") + "'"


def _hooks_dir(root):
    configured = gitrun.git_out(["config", "core.hooksPath"], root)
    if configured:
        if os.path.isabs(configured):
            return configured
        return gitrun.git_abs_dir(root, configured)
    git_dir = gitrun.git_out(["rev-parse", "--git-common-dir"], root)
    if git_dir:
        if os.path.isabs(git_dir):
            base = git_dir
        else:
            base = os.path.join(root, git_dir)
        return os.path.normpath(os.path.join(base, "hooks"))
    return os.path.join(root, ".git", "hooks")


def _hook_path(root):
    return os.path.join(_hooks_dir(root), HOOK_NAME)


_ZERO_OID = "0000000000000000000000000000000000000000"


def hook_body(executable, command="guard"):
    quoted = _sh_single_quote(executable)
    return (
        "#!/bin/sh\n"
        + MARKER
        + "\n"
        + "if IFS= read -r pre_push_line && [ -n \"$pre_push_line\" ]; then\n"
        + "  set -- $pre_push_line\n"
        + "  if [ $# -eq 4 ] && [ \"$2\" != \"%s\" ] && [ \"$4\" != \"%s\" ] && ! IFS= read -r pre_push_extra; then\n"
        + "    PRE_COMMIT_FROM_REF=\"$4\"\n"
        + "    PRE_COMMIT_TO_REF=\"$2\"\n"
        + "    export PRE_COMMIT_FROM_REF PRE_COMMIT_TO_REF\n"
        + "  fi\n"
        + "fi\n"
        + "if command -v diffimpactscout >/dev/null 2>&1 && diffimpactscout --version >/dev/null 2>&1; then\n"
        + "  exec diffimpactscout %s\n"
        + "elif [ -x %s ]; then\n"
        + "  exec %s -m diffimpactscout %s\n"
        + "else\n"
        + "  echo \"diffimpactscout: pre-push check skipped (tool not found; install via 'pip install diffimpactscout' or activate your venv).\" >&2\n"
        + "  exit 0\n"
        + "fi\n"
    ) % (_ZERO_OID, _ZERO_OID, command, quoted, quoted, command)


def hook_installed(root):
    path = _hook_path(root)
    if not os.path.isfile(path):
        return False
    with open(path) as fh:
        return MARKER in fh.read()


def install_hook(root, force=False, command=None):
    directory = _hooks_dir(root)
    path = os.path.join(directory, HOOK_NAME)
    if os.path.isfile(path):
        with open(path) as fh:
            content = fh.read()
        if MARKER not in content and not force:
            sys.stderr.write(
                "diffimpactscout: pre-push hook exists (use --force to overwrite)\n"
            )
            return False
    if not os.path.isdir(directory):
        os.makedirs(directory)
    if command is None:
        command = "guard"
    with open(path, "w") as fh:
        fh.write(hook_body(sys.executable, command))
    os.chmod(path, 0o755)
    return True


def uninstall_hook(root):
    path = _hook_path(root)
    if not os.path.isfile(path):
        return False
    with open(path) as fh:
        content = fh.read()
    if MARKER not in content:
        return False
    os.remove(path)
    return True