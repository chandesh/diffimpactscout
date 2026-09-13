"""Auto-fixing checks for line endings, trailing whitespace, and a missing final newline.

Example: a dev-changed file whose line ends with a trailing space is
rewritten without it, while an upstream file with the same flaw is left
alone.
"""

import os

from diffimpactscout.checks.base import (
    Check,
    CheckResult,
    register,
)


def fix_content(fixer, data):
    if fixer == 'mixed-line-ending':
        if b'\r' not in data:
            return None
        return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    if fixer == 'end-of-file-fixer':
        if not data:
            return None
        strip = data.rstrip(b'\n')
        if data == strip:
            return data + b'\n'
        if len(data) - len(strip) > 1:
            return strip + b'\n'
        return None
    if fixer == 'trailing-whitespace':
        lines = data.split(b'\n')
        new_lines = [line.rstrip(b' \t') for line in lines]
        changed = new_lines != lines
        while new_lines and not new_lines[-1]:
            new_lines.pop()
            changed = True
        out = b'\n'.join(new_lines)
        if out:
            out += b'\n'
        return out if out != data and changed else None
    return None


class _HygieneCheck(Check):
    scoped = "files"
    fixer = None

    def run(self, context, files):
        fixed = []
        warned = []
        for path in files or []:
            fn = os.path.join(context.root, path)
            try:
                with open(fn, 'rb') as fh:
                    data = fh.read()
            except OSError:
                continue
            new = fix_content(self.fixer, data)
            if new is not None:
                try:
                    with open(fn, 'wb') as fh:
                        written = fh.write(new)
                except OSError as exc:
                    warned.append("could not write %s: %s" % (path, exc))
                    continue
                if written != len(new):
                    warned.append(
                        "partial write to %s (%d of %d bytes)"
                        % (path, written, len(new))
                    )
                    continue
                fixed.append(path)
        return CheckResult(fixed=fixed, warned=warned)


@register
class MixedLineEndingCheck(_HygieneCheck):
    id = "hygiene/mixed-line-ending"
    fixer = "mixed-line-ending"

    def extend_config(self, entry):
        super(MixedLineEndingCheck, self).extend_config(entry)
        mode = "lf"
        for arg in self.args:
            if arg.startswith("--fix="):
                mode = arg[len("--fix="):]
        if mode != "lf":
            raise ValueError(
                "mixed-line-ending only supports --fix=lf (got --fix=%s)" % mode
            )
        return self


@register
class TrailingWhitespaceCheck(_HygieneCheck):
    id = "hygiene/trailing-whitespace"
    fixer = "trailing-whitespace"


@register
class EndOfFileFixerCheck(_HygieneCheck):
    id = "hygiene/end-of-file-fixer"
    fixer = "end-of-file-fixer"