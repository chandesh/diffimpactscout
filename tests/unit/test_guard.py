import os
import subprocess

import pytest

import diffimpactscout.config as config
import diffimpactscout.guard as guard
import diffimpactscout.scope as scope
from diffimpactscout.checks.base import make_check as _real_make_check


def _git_env(extra=None):
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if extra:
        env.update(extra)
    return env


def _git(*args, cwd, env=None):
    return subprocess.check_call(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + list(args),
        cwd=cwd,
        env=env or _git_env(),
    )


def _make_repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git("init", cwd=repo)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    return repo


def _commit(repo, filename, content, msg):
    with open(os.path.join(repo, filename), "w") as fh:
        fh.write(content)
    _git("add", filename, cwd=repo, env=_git_env())
    _git("commit", "-m", msg, cwd=repo, env=_git_env())
    return _sha(repo)


def _sha(repo, ref="HEAD"):
    out = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=repo, env=_git_env()
    )
    return out.decode("utf-8").strip()


def _anchor(repo):
    sha = _sha(repo)
    _git("update-ref", "refs/remotes/upstream/master", sha, cwd=repo)
    return "refs/remotes/upstream/master"


def _cfg(repo, checks, blocking=None):
    cfg = config.load_config(repo)
    cfg["guard"]["checks"] = checks
    if blocking is not None:
        cfg["guard"]["blocking"] = blocking
    return cfg


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, "default_cache_dir", lambda: str(tmp_path / "cache"))


def _json_repo(tmp_path, content="{ not valid"):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.json", "{}", "base")
    _anchor(repo)
    _commit(repo, "bad.json", content, "dev")
    return repo


def test_skip_env_returns_zero_and_runs_no_checks(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "1")
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_impact_check_skip_env_returns_zero(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("IMPACT_CHECK_SKIP", "1")
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_warn_mode_blocking_issue_returns_zero(tmp_path, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "syntax/json-syntax" in captured.out
    assert "bad.json" in captured.out
    assert "1 check(s), 1 issue(s)" in captured.out


def test_strict_env_blocking_issue_returns_one(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert guard.run_guard(repo, cfg) == 1
    captured = capsys.readouterr()
    assert "invalid JSON" in captured.out


def test_strict_config_blocking_issue_returns_one(tmp_path, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}], blocking="strict")
    assert guard.run_guard(repo, cfg) == 1
    captured = capsys.readouterr()
    assert "invalid JSON" in captured.out


def test_blocking_false_never_blocks_in_strict(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax", "blocking": False}])
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "syntax/json-syntax" in captured.out


def test_private_key_always_blocks_in_warn_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    _commit(repo, "key.pem", "-----BEGIN RSA PRIVATE KEY-----\n", "dev")
    cfg = _cfg(repo, [{"id": "repo/private-key"}])
    assert guard.run_guard(repo, cfg) == 1
    captured = capsys.readouterr()
    assert "private key detected" in captured.out


def test_missing_external_tool_warns_and_returns_zero(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg = _cfg(
        repo,
        [
            {
                "type": "external",
                "id": "ext/missing",
                "command": ["nonexistent_tool_xyz", "{file}"],
            }
        ],
    )
    assert guard.run_guard(repo, cfg, files=["a.txt"]) == 0
    captured = capsys.readouterr()
    assert "nonexistent_tool_xyz" in captured.err
    assert "warning" in captured.err
    assert "0 issue(s)" in captured.out


def test_explicit_files_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _commit(repo, "bad.json", "{ not valid", "dev")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg, files=["bad.json"]) == 0
    captured = capsys.readouterr()
    assert "bad.json" in captured.out


def test_staged_mode_uses_cached_diff(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.json", "{}", "base")
    with open(os.path.join(repo, "staged.json"), "w") as fh:
        fh.write("{ not valid")
    _git("add", "staged.json", cwd=repo)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg, staged=True) == 0
    captured = capsys.readouterr()
    assert "staged.json" in captured.out


def test_all_files_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "ok.json", "{}", "base")
    _commit(repo, "bad.json", "{ not valid", "dev")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg, all_files=True) == 0
    captured = capsys.readouterr()
    assert "bad.json" in captured.out


def test_hygiene_fixer_prints_fixed(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    _commit(repo, "trail.txt", "hello   \n", "dev")
    cfg = _cfg(repo, [{"id": "hygiene/trailing-whitespace"}])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "[fixed] trail.txt" in captured.out
    with open(os.path.join(repo, "trail.txt")) as fh:
        assert fh.read() == "hello\n"


def test_unknown_check_warns_and_skips(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    cfg = _cfg(repo, [{"id": "nope/unknown"}])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "nope/unknown" in captured.err
    assert "warning" in captured.err
    assert "0 check(s), 0 issue(s)" in captured.out


def test_invalid_check_config_warns_and_skips(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    cfg = _cfg(repo, [{"id": "hygiene/mixed-line-ending", "args": ["--fix=crlf"]}])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err


def test_check_run_exception_fails_open(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])

    def exploding(entry):
        check = _real_make_check(entry)

        def run(context, files):
            raise RuntimeError("boom")

        check.run = run
        return check

    monkeypatch.setattr(guard, "make_check", exploding)
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "boom" in captured.err


def test_repo_scoped_external_runs_once(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg = _cfg(
        repo,
        [
            {
                "type": "external",
                "id": "ext/repo",
                "scoped": "repo",
                "command": ["nonexistent_repo_tool_xyz"],
            }
        ],
    )
    assert guard.run_guard(repo, cfg, files=["a.txt"]) == 0
    captured = capsys.readouterr()
    assert "nonexistent_repo_tool_xyz" in captured.err
    assert "warning" in captured.err


def test_skip_env_empty_value_still_skips(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("DIFFIMPACTSCOUT_SKIP", "")
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_strict_env_zero_is_not_strict(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "0")
    assert guard.run_guard(repo, cfg) == 0


def test_strict_env_false_is_not_strict(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "FALSE")
    assert guard.run_guard(repo, cfg) == 0


def test_check_run_returning_none_fails_open(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])

    def none_result(entry):
        check = _real_make_check(entry)

        def run(context, files):
            return None

        check.run = run
        return check

    monkeypatch.setattr(guard, "make_check", none_result)
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "syntax/json-syntax" in captured.err
    assert "failed" in captured.err


def test_private_key_always_block_disabled_in_warn_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    _commit(repo, "key.pem", "-----BEGIN RSA PRIVATE KEY-----\n", "dev")
    cfg = _cfg(repo, [{"id": "repo/private-key", "always_block": False}])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "private key detected" in captured.out


def test_blocking_check_zero_issues_returns_zero(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "ok.json", "{}", "base")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}], blocking="strict")
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "0 issue(s)" in captured.out


def test_empty_checks_config_returns_zero(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg = _cfg(repo, [])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "0 check(s), 0 issue(s)" in captured.out


def test_empty_file_set_returns_zero(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg, files=[]) == 0
    captured = capsys.readouterr()
    assert "0 issue(s)" in captured.out


def test_single_file_string_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _commit(repo, "bad.json", "{ not valid", "dev")
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg, files="bad.json") == 0
    captured = capsys.readouterr()
    assert "bad.json" in captured.out
    assert "1 check(s), 1 issue(s)" in captured.out


def test_non_dict_check_entry_warns_and_skips(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    _anchor(repo)
    cfg = _cfg(repo, ["syntax/json-syntax"])
    assert guard.run_guard(repo, cfg) == 0
    captured = capsys.readouterr()
    assert "syntax/json-syntax" in captured.err
    assert "warning" in captured.err
    assert "0 check(s), 0 issue(s)" in captured.out


def test_guard_prints_pass_status_and_verdict(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "ok.json", "{}\n", "base")
    _anchor(repo)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    assert guard.run_guard(repo, cfg) == 0
    out = capsys.readouterr().out
    assert "[PASS]" in out
    assert "syntax/json-syntax" in out
    assert "[ALLOWED]" in out


def test_guard_prints_fail_status_and_blocked_verdict(tmp_path, monkeypatch, capsys):
    repo = _json_repo(tmp_path)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    monkeypatch.setenv("IMPACT_CHECK_STRICT", "1")
    assert guard.run_guard(repo, cfg) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "[BLOCKED]" in out
    assert "DiffImpactScout Guard" in out


def test_guard_prints_no_emojis(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "ok.json", "{}\n", "base")
    _anchor(repo)
    cfg = _cfg(repo, [{"id": "syntax/json-syntax"}])
    guard.run_guard(repo, cfg)
    out = capsys.readouterr().out
    for ch in ("\u2605", "\u2713", "\u2717", "\u2714", "\u26a1", "\u2728", "\U0001f7e2", "\U0001f534"):
        assert ch not in out, "emoji %r leaked into guard output" % ch


def test_external_check_always_block_blocks_in_warn_mode(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    _commit(repo, "base.txt", "base\n", "base")
    cfg = _cfg(
        repo,
        [
            {
                "type": "external",
                "id": "ext/fail",
                "command": ["sh", "-c", "exit 1", "sh", "{file}"],
                "always_block": True,
            }
        ],
    )
    assert guard.run_guard(repo, cfg, files=["a.txt"]) == 1
    captured = capsys.readouterr()
    assert "ext/fail" in captured.out