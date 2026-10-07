"""Per-repo symbol cache keyed by a file content hash, and never blocking on read or write errors.

Example: re-analyzing an unchanged file skips parsing and reuses the cached
analysis.
"""

import json
import os
import sys


SECTIONS = ("py", "templates", "frontend")


class SymbolCache(object):
    def __init__(self, path):
        self.path = path
        self._data = None

    def _sections(self):
        if self._data is None:
            self._data = _read_sections(self.path)
        return self._data

    def load(self):
        """Return the py section (the historical flat path -> entry dict)."""
        return self._sections()["py"]

    def load_section(self, name):
        """Return a named section dict ("py", "templates" or "frontend")."""
        return self._sections()[name]

    def save(self, data):
        """Store the py section, preserving the other sections."""
        self.save_section("py", data)

    def save_section(self, name, data):
        self._sections()[name] = data
        _write_cache(self.path, self._data)

    def prune(self, existing):
        """Drop py-section keys not in ``existing``; return the pruned section."""
        keep = set(existing)
        data = self._sections()["py"]
        for key in [k for k in data if k not in keep]:
            del data[key]
        return data

    def prune_section(self, name, existing):
        """Drop keys not in ``existing`` from the named section."""
        keep = set(existing)
        data = self._sections()[name]
        for key in [k for k in data if k not in keep]:
            del data[key]
        _write_cache(self.path, self._sections())
        return data


def _read_sections(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not any(name in data for name in SECTIONS):
        data = {"py": data, "templates": {}, "frontend": {}}
    for name in SECTIONS:
        if not isinstance(data.get(name), dict):
            data[name] = {}
    return data


def _write_cache(path, data):
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError) as exc:
        print(
            "warning: could not write %s: %s" % (path, exc),
            file=sys.stderr,
        )
