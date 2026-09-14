import os
import stat
import subprocess
import sys

import diffimpactscout.launcher as launcher


def _git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def _git(*args, cwd):
    return subprocess.check_call(["git"] + list(args), cwd=cwd, env=_git_env())


def _make_repo(tmp_path, directory="repo"):
    root = str(tmp_path / directory)
    os.makedirs(root)
    _git("init", cwd=root)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    return root


def _commit(root, filename="a.txt"):
    path = os.path.join(root, filename)
    with open(path, "w") as fh:
        fh.write("x\n")
    _git("add", filename, cwd=root)
    _git("commit", "-m", "init", cwd=root)


def _read_hook(root):
    path = os.path.join(root, ".git", "hooks", "pre-push")
    with open(path) as fh:
        return fh.read()


def _write_foreign(root, content):
    directory = os.path.join(root, ".git", "hooks")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    path = os.path.join(directory, "pre-push")
    with open(path, "w") as fh:
        fh.write(content)


def test_hook_body_has_no_staged_and_calls_guard(tmp_path):
    """Verifies the hook body avoids --staged and invokes the guard."""
    body = launcher.hook_body("/venv/bin/python")
    assert "--staged" not in body
    assert "diffimpactscout guard" in body
    assert "/venv/bin/python" in body
    assert "#!/bin/sh" in body


def test_hook_body_missing_tool_exits_zero():
    """Checks that a missing tool makes the hook exit zero with guidance."""
    body = launcher.hook_body("/venv/bin/python")
    assert "exit 0" in body
    assert "exit 1" not in body
    assert "activate your venv" in body


def test_hook_body_probes_version_and_single_quotes_exec():
    """Verifies the hook probes the version and single-quotes the exec path."""
    body = launcher.hook_body("/venv/bin/python")
    assert "command -v diffimpactscout >/dev/null 2>&1 && diffimpactscout --version >/dev/null 2>&1" in body
    assert "-x '/venv/bin/python'" in body
    assert "exec '/venv/bin/python' -m diffimpactscout guard" in body


def test_hook_body_single_quotes_spaces_in_path():
    """Checks that paths containing spaces are single-quoted in the hook."""
    body = launcher.hook_body("/tmp/venv with space/bin/python")
    assert "[ -x '/tmp/venv with space/bin/python' ]" in body
    assert "exec '/tmp/venv with space/bin/python' -m diffimpactscout guard" in body


def test_hook_body_escapes_quote_in_executable():
    """Verifies that quotes in the executable path are properly escaped."""
    body = launcher.hook_body("/venv'x/bin/python")
    assert "-x '/venv'\"'\"'x/bin/python'" in body


LOCAL_OID = "1111111111111111111111111111111111111111"
REMOTE_OID = "2222222222222222222222222222222222222222"
ZERO_OID = "0" * 40


def _write_shim(tmp_path):
    bin_dir = str(tmp_path / "shim")
    os.makedirs(bin_dir)
    shim = os.path.join(bin_dir, "diffimpactscout")
    with open(shim, "w") as fh:
        fh.write(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  echo \"0.1.0\"\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"guard\" ]; then\n"
            "  echo \"${PRE_COMMIT_FROM_REF}/${PRE_COMMIT_TO_REF}\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        )
    os.chmod(shim, 0o755)
    return bin_dir


def _run_hook(tmp_path, stdin_data):
    bin_dir = _write_shim(tmp_path)
    hook_path = str(tmp_path / "prepush")
    with open(hook_path, "w") as fh:
        fh.write(launcher.hook_body("/venv/bin/python"))
    os.chmod(hook_path, 0o755)
    env = dict(os.environ)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["sh", hook_path],
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        universal_newlines=True,
    )


def test_hook_forwards_env_for_single_real_push(tmp_path):
    """Verifies the hook forwards refs for a single real push."""
    line = "refs/heads/master %s refs/heads/master %s\n" % (LOCAL_OID, REMOTE_OID)
    proc = _run_hook(tmp_path, line)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "%s/%s" % (REMOTE_OID, LOCAL_OID)
    assert proc.stderr == ""


def test_hook_skips_env_for_new_branch_zero_remote_oid(tmp_path):
    """Checks that a new branch with a zero remote oid skips env forwarding."""
    line = "refs/heads/feat %s refs/heads/feat %s\n" % (LOCAL_OID, ZERO_OID)
    proc = _run_hook(tmp_path, line)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "/"
    assert REMOTE_OID not in proc.stdout


def test_hook_skips_env_for_deletion_zero_local_oid(tmp_path):
    """Verifies that a deletion with a zero local oid skips env forwarding."""
    line = "refs/heads/del %s refs/heads/del %s\n" % (ZERO_OID, REMOTE_OID)
    proc = _run_hook(tmp_path, line)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "/"
    assert LOCAL_OID not in proc.stdout


def test_hook_skips_env_for_empty_stdin(tmp_path):
    """Checks that empty stdin makes the hook skip env forwarding."""
    proc = _run_hook(tmp_path, "")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "/"
    assert LOCAL_OID not in proc.stdout


def test_hook_skips_env_for_multi_line_push(tmp_path):
    """Verifies that a multi-line push skips env forwarding."""
    lines = (
        "refs/heads/a %s refs/heads/a %s\n"
        "refs/heads/b %s refs/heads/b %s\n"
        % (LOCAL_OID, REMOTE_OID, LOCAL_OID, REMOTE_OID)
    )
    proc = _run_hook(tmp_path, lines)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "/"
    assert LOCAL_OID not in proc.stdout


def test_install_hook_linked_worktree(tmp_path):
    """Verifies hook install/remove works for a linked worktree."""
    main = _make_repo(tmp_path, "main")
    _commit(main)
    wt = str(tmp_path / "wt")
    _git("worktree", "add", "-b", "wtbranch", wt, cwd=main)
    assert launcher.hook_installed(wt) is False
    assert launcher.install_hook(wt) is True
    common_hook = os.path.join(main, ".git", "hooks", "pre-push")
    assert os.path.isfile(common_hook)
    assert launcher.hook_installed(wt) is True
    assert not os.path.exists(os.path.join(wt, ".git", "hooks", "pre-push"))
    assert launcher.uninstall_hook(wt) is True
    assert not os.path.exists(common_hook)


def test_hooks_dir_honors_core_hooks_path_relative(tmp_path):
    """Checks that a relative core.hooksPath is honored for hooks dir."""
    main = _make_repo(tmp_path)
    _commit(main)
    custom = os.path.join(main, "custom-hooks")
    os.makedirs(custom)
    _git("config", "core.hooksPath", "custom-hooks", cwd=main)
    assert launcher._hooks_dir(main) == custom
    assert launcher.install_hook(main) is True
    assert os.path.isfile(os.path.join(custom, "pre-push"))
    assert launcher.hook_installed(main) is True


def test_hooks_dir_honors_core_hooks_path_absolute(tmp_path):
    """Verifies that an absolute core.hooksPath is honored for hooks dir."""
    main = _make_repo(tmp_path)
    _commit(main)
    custom = str(tmp_path / "abs-hooks")
    os.makedirs(custom)
    _git("config", "core.hooksPath", custom, cwd=main)
    assert launcher._hooks_dir(main) == custom
    assert launcher.install_hook(main) is True
    assert os.path.isfile(os.path.join(custom, "pre-push"))


def test_install_creates_hook_with_exec_bit_and_recorded_python(tmp_path):
    """Checks that install creates an executable hook recording the python path."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    assert launcher.install_hook(root) is True
    body = _read_hook(root)
    assert launcher.MARKER in body
    assert sys.executable in body
    path = os.path.join(root, ".git", "hooks", "pre-push")
    assert os.stat(path).st_mode & stat.S_IXUSR


def test_reinstall_over_own_hook_ok(tmp_path):
    """Verifies that reinstalling over our own hook is allowed."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    assert launcher.install_hook(root) is True
    assert launcher.install_hook(root) is True


def test_refuse_foreign_hook_without_force(tmp_path, capsys):
    """Checks that install refuses a foreign hook without force."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    _write_foreign(root, "#!/bin/sh\necho not ours\n")
    assert launcher.install_hook(root) is False
    captured = capsys.readouterr()
    assert "pre-push hook exists" in captured.err
    assert "use --force" in captured.err
    assert _read_hook(root) == "#!/bin/sh\necho not ours\n"


def test_force_overwrites_foreign_hook(tmp_path):
    """Verifies that force overwrites an existing foreign hook."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    _write_foreign(root, "#!/bin/sh\necho not ours\n")
    assert launcher.install_hook(root, force=True) is True
    body = _read_hook(root)
    assert launcher.MARKER in body
    assert sys.executable in body


def test_uninstall_removes_only_our_hook(tmp_path):
    """Checks that uninstall only removes hooks installed by us."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    path = os.path.join(root, ".git", "hooks", "pre-push")
    assert launcher.uninstall_hook(root) is False
    launcher.install_hook(root)
    assert os.path.exists(path)
    assert launcher.uninstall_hook(root) is True
    assert not os.path.exists(path)
    assert launcher.uninstall_hook(root) is False
    _write_foreign(root, "#!/bin/sh\necho not ours\n")
    assert launcher.uninstall_hook(root) is False
    assert os.path.exists(path)


def test_hook_installed_true_and_false(tmp_path):
    """Verifies hook_installed reports correctly for owned and foreign hooks."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    assert launcher.hook_installed(root) is False
    launcher.install_hook(root)
    assert launcher.hook_installed(root) is True
    _write_foreign(root, "#!/bin/sh\necho not ours\n")
    assert launcher.hook_installed(root) is False