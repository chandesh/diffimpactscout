"""Per-repo symbol cache keyed by a file content hash, and never blocking on read or write errors.

Example: re-analyzing an unchanged file skips parsing and reuses the cached
analysis.
"""

import json
import os
import sys


class SymbolCache(object):
    def __init__(self, path):
        self.path = path
        self._data = None

    def load(self):
        if self._data is None:
            self._data = _read_cache(self.path)
        return self._data

    def save(self, data):
        tmp = self.path + ".tmp"
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except (OSError, ValueError, TypeError) as exc:
            print(
                "warning: could not write %s: %s" % (self.path, exc),
                file=sys.stderr,
            )
            return
        self._data = data

    def prune(self, existing):
        keep = set(existing)
        data = self.load()
        for key in [k for k in data if k not in keep]:
            del data[key]
        return data


def _read_cache(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data