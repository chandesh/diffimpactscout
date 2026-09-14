"""Check framework: registers checks, defines the check context and results, and turns config entries into runnable checks.

Example: a check entry with type external runs a given command per file and
reports its output as issues.
"""

import subprocess

import diffimpactscout.env as env

REGISTRY = {}


class Check(object):
    id = None
    scoped = "files"
    blocking = True
    always_block = False

    def __init__(self, id=None, scoped=None, blocking=None, always_block=None):
        if id is not None:
            self.id = id
        if scoped is not None:
            self.scoped = scoped
        if blocking is not None:
            self.blocking = blocking
        if always_block is not None:
            self.always_block = always_block
        if getattr(self, "id", None) is None:
            raise ValueError("Check subclass must define an id")
        self.args = []

    def extend_config(self, entry):
        if isinstance(entry, dict):
            args = entry.get("args")
            if args:
                self.args = list(self.args) + list(args)
            blocking = entry.get("blocking")
            if blocking is not None:
                self.blocking = env.is_true(blocking)
            always_block = entry.get("always_block")
            if always_block is not None:
                self.always_block = env.is_true(always_block)
        return self

    def run(self, context, files):
        raise NotImplementedError("Check subclasses must implement run()")


class ExternalCheck(Check):
    scoped = "files"

    def __init__(self, entry):
        if not isinstance(entry, dict):
            entry = {}
        command = list(entry.get("command") or [])
        if not command:
            raise ValueError("external check requires a command")
        cid = entry.get("id")
        if not cid:
            cid = "external:" + command[0]
        super(ExternalCheck, self).__init__(
            id=cid,
            scoped=entry.get("scoped") or "files",
            blocking=entry.get("blocking"),
        )
        self.command = command
        self.always_block = env.is_true(entry.get("always_block"))

    def _argv_for(self, path):
        has_placeholder = any("{file}" in token for token in self.command)
        argv = [token.replace("{file}", path) for token in self.command]
        if not has_placeholder:
            argv.append(path)
        return argv

    def run(self, context, files):
        issues = []
        fixed = []
        skipped = []
        warned = []
        if self.scoped == "repo":
            try:
                proc = subprocess.Popen(
                    list(self.command),
                    cwd=context.root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                warned.append("command %r unavailable: %s" % (self.command[0], exc))
                return CheckResult(
                    issues=issues, fixed=fixed, skipped=skipped, warned=warned
                )
            out, err = proc.communicate()
            if proc.returncode != 0:
                output = (out or err).decode("utf-8", errors="replace").strip()
                if not output:
                    output = "exit code %s" % proc.returncode
                issues.append(CheckIssue(".", None, None, self.id, output))
            return CheckResult(issues=issues, fixed=fixed, skipped=skipped, warned=warned)
        for path in files or []:
            argv = self._argv_for(path)
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=context.root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                warned.append("command %r unavailable: %s" % (self.command[0], exc))
                break
            out, err = proc.communicate()
            if proc.returncode != 0:
                output = (out or err).decode("utf-8", errors="replace").strip()
                if not output:
                    output = "exit code %s" % proc.returncode
                issues.append(CheckIssue(path, None, None, self.id, output))
        return CheckResult(issues=issues, fixed=fixed, skipped=skipped, warned=warned)


class CheckContext(object):
    def __init__(self, root, anchor, from_ref, to_ref, scope, config=None, echo=True):
        self.root = root
        self.anchor = anchor
        self.from_ref = from_ref
        self.to_ref = to_ref
        self.scope = scope
        self.config = config if config is not None else {}
        self.echo = echo

    def changed_lines(self, path):
        """Changed line numbers for path, or None when the path is untracked (whole file) or scope lookup failed; pair with is_tracked()."""
        try:
            return self.scope.changed_lines(
                self.anchor, path, self.from_ref, self.to_ref
            )
        except Exception:
            return None

    def is_tracked(self, path):
        try:
            return bool(self.scope.is_tracked(path))
        except Exception:
            return False

    def note(self, text):
        if self.echo:
            print(text)


class CheckIssue(object):
    def __init__(self, path, line=None, column=None, code=None, message=None):
        self.path = path
        self.line = line
        self.column = column
        self.code = code
        self.message = message

    def format(self):
        if self.line is None and self.column is None:
            location = self.path
        else:
            line = "-" if self.line is None else str(self.line)
            column = "-" if self.column is None else str(self.column)
            location = "%s:%s:%s" % (self.path, line, column)
        return "%s: %s %s" % (location, self.code, self.message)


class CheckResult(object):
    def __init__(self, issues=None, fixed=None, skipped=None, warned=None):
        self.issues = list(issues) if issues else []
        self.fixed = list(fixed) if fixed else []
        self.skipped = list(skipped) if skipped else []
        self.warned = list(warned) if warned else []

    def ok(self):
        return not self.issues

    def has_issues(self):
        return bool(self.issues)


def register(cls):
    if not getattr(cls, "id", None):
        raise ValueError("registered check class must define an id")
    REGISTRY[cls.id] = cls
    return cls


def make_check(entry):
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "external":
        try:
            return ExternalCheck(entry)
        except ValueError:
            return None
    cls = REGISTRY.get(entry.get("id"))
    if cls is None:
        return None
    try:
        return cls().extend_config(entry)
    except Exception:
        return None