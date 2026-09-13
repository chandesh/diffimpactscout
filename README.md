# DiffImpactScout

An incremental pre-push guard and AST blast-radius impact analyzer for git repositories.

- Runs a battery of hygiene, syntax, and linter checks on just the files you changed, before you push.
- Estimates the blast radius of a change: which Python symbols you touched and where they are referenced across Python, Django templates, and frontend code.
- Installs as a git pre-push hook that is non-blocking by default, so it informs without getting in the way.

## Features

- Incremental checks: every check is scoped to the change-set or changed lines, not the whole repository.
- Auto-fixing hygiene checks (line endings, trailing whitespace, end-of-file newline) that clean files in place.
- Python AST symbol map: detects changed classes, functions, methods, module fields, and class fields, including deletions and renames.
- Cross-layer impact linking: Python symbols to Django `{% url %}` template tags and frontend `http.get()/post()`-style calls.
- Symbol cache (`.impact_analysis_cache.json`) so repeated runs skip unchanged files.
- Profiles for `generic`, `django`, `fastapi`, `python`, and `web` projects.
- Configurable pre-push hook installation that never silently clobbers an existing hook.

## Core idea

DiffImpactScout answers one question for a developer about to push: did I break something?

- **Only your changes are analyzed.** Every guard check and the impact report are computed against the diff between your branch and the resolved change base (upstream master or sprint branch when present, otherwise closest origin branch). Pre-existing violations that arrived from upstream or from sync merges are not re-reported, so a run answers "did I introduce this?" instead of "does this file have a problem?"
- **Non-blocking by default.** A push is only blocked when strictness is explicitly requested (strict mode or an always-block check). Default runs inform without getting in the way.
- **Fixes over complaints.** Hygiene problems that can be auto-fixed are fixed in place instead of failing the run.
- **Blast radius, not just lint.** The impact report shows which code outside your diff could be affected by your change, so the "not my change" threshold is applied to blame, not to risk.

The change-set also handles the full breadth of realistic push shapes: direct pushes, force pushes, new branches, sync merges (merge, rebase, squash), stacked branches, shallow clones, repos without a remote anchor, and fork upstream sync. All are covered by the automated pre-push scenario suite, so a missing upstream anchor never silently means "nothing was checked".

## Install

```sh
pip install diffimpactscout
```

The package is pure Python (standard library only at runtime) and requires Python 3.6 or newer and the `git` CLI on `PATH`.

Optional tools are only needed for the checks that use them: `ruff`, `eslint` (via `npx`), and `prettier` (via `npx`). If one is missing, the affected check emits a warning and the guard continues; it never blocks a push because a tool is absent.

## Quick start

```sh
# 1. Write a .diffimpactscout.json config (optional; defaults are used without one)
diffimpactscout init

# 2. Run the guard checks on your change-set
diffimpactscout guard

# 3. Analyze the blast radius of your change
diffimpactscout impact

# 4. Run the guard automatically on every push
diffimpactscout install-hooks
```

`init` accepts `--profile generic|django|fastapi|python|web` to seed profile-appropriate settings. `install-hooks` detects the stack automatically (see [Supported setups](#supported-setups)).

## Supported setups

`install-hooks` detects the project stack on first run and writes a `.diffimpactscout.json` that fits it:

| Detection signal | Profile | Guard checks | Impact globs |
| --- | --- | --- | --- |
| `manage.py` + `settings.py`/`wsgi.py` | django | default + `ruff`, `ruff-format` | urls, templates, frontend |
| `fastapi` in deps or `main.py` | fastapi | default + `ruff`, `ruff-format` | `**/*.py` routes + frontend globs |
| `package.json` + `src`/`app` `*.ts`/`*.js` | web | default + `eslint`, `prettier` | templates + frontend globs |
| `*.py` present | python | default + `ruff`, `ruff-format` | Python module references |
| anything else (or no signal) | generic | default from `init` | none |

Other ecosystems (Go, Rust, Java, Ruby, ...) are detected and reported as "planned for future releases" in the preview; the generic profile still installs the universal guard checks. Add a language linter with an [external check](#configuration).

## Commands

The `init` command only needs a directory; every other command must be run inside a git repository.

### `diffimpactscout init`

Writes a `.diffimpactscout.json` into the current directory. If the file already exists it is left unchanged.

| Flag | Description |
| --- | --- |
| `--profile {generic,django,fastapi}` | Seed defaults from the named profile (default: `generic`). |

### `diffimpactscout guard`

Runs every check listed in `guard.checks` over the change-set and prints any issues. Exits non-zero only when the run is in blocking mode and a blocking check found issues (see [Pre-push hook](#pre-push-hook) and [Configuration](#configuration)).

| Flag | Description |
| --- | --- |
| `--staged` | Check only staged files (`git diff --cached`). |
| `--all` | Check every file tracked by git. |
| `files...` | Check only the given paths. |

With no flags, the change-set is the diff between your branch and the closest remote ref (upstream preferred, then origin), falling back to the union of committed, staged, and worktree changes when no remote base can be resolved.

### `diffimpactscout impact`

Analyzes the blast radius of the change-set. In an interactive terminal it prints the report and asks `Proceed with push? (Y/n)`; answering `n` or `no` exits non-zero.

| Flag | Description |
| --- | --- |
| `--staged` | Analyze the staged diff (base ref becomes `HEAD`). |
| `--fast` | Skip template and frontend scanning; limit Python scanning to the changed files plus import-linked files. |
| `--json` | Emit the report as JSON (`{changed_count, rows, unresolved}`) instead of a markdown table. |

### `diffimpactscout check CHECK_ID [files...]`

Runs a single check by id (see [Checks reference](#checks-reference)). With no file arguments it runs against the change-set.

### `diffimpactscout install-hooks`

Installs a `pre-push` git hook that runs `diffimpactscout guard` on push.

On first run the command detects the stack, prints a preview of the `.diffimpactscout.json` it would write, and (when stdin is a terminal) asks for confirmation. Answer `n` to override profile, blocking mode, extra checks, and impact. Use `--yes` in CI to skip prompts, `--profile` and `--blocking` to pre-select settings, and `--reconfigure` to rewrite an existing config. Without `--reconfigure` an existing config is left untouched.

| Flag | Description |
| --- | --- |
| `--profile {generic,django,fastapi,python,web}` | Pre-select the setup profile; skips the stack question. |
| `--blocking {warn,strict}` | Blocking mode for guard checks (default: `warn`). |
| `--yes`, `-y` | Accept defaults and skip prompts (non-interactive). |
| `--reconfigure` | Rewrite an existing `.diffimpactscout.json`. |
| `--force` | Overwrite an existing `pre-push` hook that DiffImpactScout did not install. |
| `--uninstall` | Remove the DiffImpactScout `pre-push` hook. |

### `diffimpactscout --version`

Prints the installed version.

## Configuration

DiffImpactScout is configured by a `.diffimpactscout.json` file in the repository root. Without one, the defaults below are used.

```json
{
  "version": 1,
  "mode": "pre-push",
  "ignore_paths": [
    "**/node_modules/**",
    "**/venv/**",
    "**/.venv/**",
    "**/migrations/**",
    "**/staticfiles/**",
    "**/dist/**",
    "**/build/**",
    "**/.git/**"
  ],
  "use_gitignore": false,
  "guard": {
    "checks": [
      {"id": "hygiene/mixed-line-ending"},
      {"id": "hygiene/trailing-whitespace"},
      {"id": "hygiene/end-of-file-fixer"},
      {"id": "syntax/json-syntax"},
      {"id": "syntax/ast-syntax"},
      {"id": "syntax/merge-conflict"},
      {"id": "repo/large-files", "args": ["--maxkb=250000"]},
      {"id": "repo/private-key"}
    ],
    "blocking": "warn"
  },
  "impact": {
    "profile": "generic",
    "urls_globs": [],
    "template_globs": [],
    "frontend_globs": [],
    "cache_file": ".impact_analysis_cache.json",
    "fast_mode": false,
    "threads": 4
  }
}
```

| Section | Key | Meaning |
| --- | --- | --- |
| top-level | `ignore_paths` | Glob patterns (matching any path suffix) excluded from all checks and from impact scanning. |
| top-level | `use_gitignore` | When `true`, also excludes paths ignored by `git check-ignore`. |
| `guard` | `checks` | Ordered list of check entries; each `{"id": ...}` may add `args`, `blocking`, and `always_block` overrides. |
| `guard` | `blocking` | `"warn"` (default) or `"strict"`. In strict mode every check marked blocking can fail the run. |
| `impact` | `profile` | `generic`, `django`, or `fastapi`; selects route extraction plus default globs. |
| `impact` | `urls_globs` / `template_globs` / `frontend_globs` | Glob patterns for route files, Django templates, and frontend sources. |
| `impact` | `cache_file` | Path of the symbol cache (relative to the repo root). |
| `impact` | `fast_mode`, `threads` | Reserved defaults from `init`; fast mode is currently selected with the `impact --fast` flag. |

A config entry can also declare a custom external check:

```json
{"id": "my-lint", "type": "external", "command": ["make", "lint"]}
```

External checks run `command` (each `{file}` placeholder is replaced with the file path) and report a failed exit status as an issue. `scoped` can be `"files"` (command runs once per file) or `"repo"` (once for the whole repository).

### Profiles

- `generic`: no route or cross-layer globs; impact analysis reports only Python references.
- `python`: same impact globs as `generic`; guard checks append `ruff` and `ruff-format`.
- `django`: route files `**/urls.py`, templates `**/templates/**/*.html`, frontend `**/src/**/*.ts` and `**/app/**/*.js`; guard checks append `ruff` and `ruff-format`.
- `fastapi`: routes extracted from `**/*.py`, same frontend globs; guard checks append `ruff` and `ruff-format`.
- `web`: template globs `**/*.html` and frontend globs `**/src/**/*.ts` and `**/app/**/*.js`; guard checks append `eslint` and `prettier`.

### Blocking behavior

- `guard.blocking` is `"warn"` by default: issues are reported but the run exits `0`. Only checks marked `always_block` (by default just `repo/private-key`) fail the run in warn mode.
- Set `guard.blocking` to `"strict"` (or export `IMPACT_CHECK_STRICT=1`): every check marked `{"blocking": true}` that finds issues fails the run.
- Override per check, for example:

```json
{"id": "syntax/ast-syntax", "always_block": true}
```

## Pre-push hook

`diffimpactscout install-hooks` writes a `pre-push` hook that runs `diffimpactscout guard` on every push.

- The hook directory is resolved from `git rev-parse --git-common-dir` and honors `core.hooksPath`.
- The hook script is marked with `# diffimpactscout pre-push hook`. On `--uninstall` (or a re-install with `--force`), only a hook carrying this marker is touched.
- If a `pre-push` hook already exists without the marker, installation refuses with a `use --force to overwrite` message.
- The hook runs `diffimpactscout guard`; if the tool is not found, it prints a notice and exits `0`, so pushes are never blocked by a missing install.
- Because the guard is warn-by-default, an installed hook reports issues without blocking the push unless you enable strict mode.

### Environment variables

| Variable | Effect |
| --- | --- |
| `DIFFIMPACTSCOUT_SKIP` | When set (any value), `guard` and `impact` skip entirely and exit `0`. |
| `IMPACT_CHECK_SKIP` | Legacy alias with the same behavior as `DIFFIMPACTSCOUT_SKIP`. |
| `IMPACT_CHECK_STRICT` | Falsy values are `0`, `false`, `no`, `off`, and empty; anything else puts `guard` in strict mode and makes `impact` non-interactive runs fail on High/Medium hits. |
| `PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF` | Override the diff range used to compute the change-set (e.g. set by a pre-commit-style wrapper). |

## Impact analysis

The `impact` command answers: if I push these changes, what else could break?

1. It parses the change-set (`git diff --find-renames`) and extracts the Python entities you added, removed, or renamed: classes, functions, methods, module fields, and class fields.
2. It builds an AST symbol map of every tracked Python file in the repo (skipping excluded paths), cached in `impact.cache_file`, and finds every reference to the changed symbols. Lookup kind follows the entity: class fields match attribute loads, methods match name and attribute loads, functions/classes also match imports.
3. Depending on the profile it extracts routes:
   - `django`: `path()`/`re_path()`/`url()` entries in `urls_globs` that carry a `name=`.
   - `fastapi`: `get`/`post`/`put`/`delete`/`patch`/`options` decorators on `router`/`app`-style objects in `urls_globs`.
4. It links layers: Django templates using `{% url 'name' %}` resolve through the route to its handler; frontend calls (`http.get(...)`, `HttpClient.post(...)`, `$http.delete(...)`) resolve the quoted endpoint to a route by path. References that cannot be resolved to a route land in an "unresolved" bucket flagged for manual checking.
5. Every impacted reference gets a severity:

| Condition | Severity |
| --- | --- |
| Symbol was deleted or renamed | High |
| Symbol is referenced across 2+ layers (python / template / frontend) | High |
| Reference is inside a file that is itself in the change-set | Low |
| No reference path or no changed paths to compare | Low |
| Otherwise | Medium |

Actions follow severity: High rows say `review/verify`, Medium rows `verify`, Low rows `ok`, and deleted symbols `verify dangling references`.

### Example report excerpt

```
# Impact Analysis Report
| # | Impacted File Path | Module / Subsystem | Category | Detected Reference / Usage | Severity | Action Required |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | app/views/dashboard.py | app/views | python | render_dashboard (import at dashboard.py:12) | Medium | verify |
| 2 | app/templates/dashboard.html | app/templates | template | {% url 'dashboard' %} at app/templates/dashboard.html:8 | High | review/verify |
| 3 | frontend/src/api.ts | frontend/src | frontend | http.get("/dashboard/") at api.ts:41 | Medium | verify |

## Unresolved references (manual check required)
- {'file': 'frontend/src/api.ts', 'path': '/account/settings', 'dynamic': False}

Summary: 3 changed file(s); High: 1, Medium: 2, Low: 0
```

In a terminal, `impact` then asks `Proceed with push? (Y/n)`. On a non-interactive run (for example after a CI trigger), the report is shown as a warning and the push is not blocked unless `IMPACT_CHECK_STRICT` is set.

## Checks reference

All checks are incremental: file-scoped checks run on files in the change-set, and line-scoped checks flag only changed lines.

| id | What it checks | Blocks by default? |
| --- | --- | --- |
| `hygiene/mixed-line-ending` | Converts CRLF/CR to LF (`--fix=lf` only); auto-fixes files in place. | No (fixes) |
| `hygiene/trailing-whitespace` | Strips trailing spaces/tabs and trailing blank lines; auto-fixes files in place. | No (fixes) |
| `hygiene/end-of-file-fixer` | Ensures files end with exactly one newline; auto-fixes files in place. | No (fixes) |
| `syntax/json-syntax` | Validates that `.json` files parse. | No |
| `syntax/ast-syntax` | Validates that `.py` files parse as Python. | No |
| `syntax/merge-conflict` | Flags `<<<<<<<` / `=======` / `>>>>>>>` conflict markers on changed lines. | No |
| `repo/large-files` | Flags files over `--maxkb` (default 250000). | No |
| `repo/private-key` | Flags files containing private-key material (RSA, EC, OpenSSH, DSA, PGP blocks). | **Always** |
| `ruff` | Runs `ruff check` on changed lines of `.py` files (needs `ruff`). | No |
| `ruff-format` | Runs `ruff format --check` on changed lines (needs `ruff`). | No |
| `eslint` | Runs `npx eslint` on changed files, honoring a baseline (needs `eslint`). | No |
| `prettier` | Runs `npx prettier --check` on changed files under `src/` or `app/`, honoring a baseline (needs `prettier`). | No |

"Always" means the check has `always_block: true`, so `repo/private-key` fails the guard even in warn mode: a private key should never be pushed. Every other blocking check only fails the run in strict mode (`guard.blocking: "strict"` or `IMPACT_CHECK_STRICT=1`).

## Requirements

- Python 3.6 or newer.
- Standard library only at runtime; no third-party dependencies.
- The `git` CLI on `PATH`.
- Optional: `ruff`, `eslint`, and `prettier` for their respective checks (missing tools degrade to warnings).

## FAQ

**Why is nothing failing?**
DiffImpactScout is warn-by-default. `guard` reports issues but exists `0` unless a `repo/private-key` hit, or strict mode is active. The same applies to `impact` in non-interactive contexts.

**How do I make it block?**
Set `IMPACT_CHECK_STRICT=1` (or any truthy value), or set `"blocking": "strict"` in the `guard` section of `.diffimpactscout.json`. You can also force specific checks to always block with `"always_block": true` on a check entry.

**I want to skip the guard, how?**
Export either `DIFFIMPACTSCOUT_SKIP` or `IMPACT_CHECK_SKIP` (any value); both `guard` and `impact` will exit `0` without doing anything.

**I already have a pre-push hook.**
`install-hooks` refuses to overwrite a hook it did not install. Review the existing hook and re-run with `--force` to replace it, or call `guard` from your own hook.

**My templates are in nested or unusual locations.**
Set `impact.template_globs` (and `frontend_globs`) in the config to cover your layout; use the `django` profile for a sensible starting point.

**The hygiene checks modified my files.**
That is by design: mixed line endings, trailing whitespace, and missing final newlines are fixed in place. Checks that can fix do so rather than failing; run `diffimpactscout check hygiene/end-of-file-fixer <path>` to apply one fixer to specific files.

## License

MIT. See [LICENSE](LICENSE).