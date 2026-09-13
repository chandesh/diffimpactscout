"""Shared environment-variable parsing: skip flags, strict mode, and pre-commit push-range refs."""

import os

_FALSY = ("", "0", "false", "no", "off")


def is_true(value):
    if value is None:
        return False
    if not isinstance(value, str):
        return bool(value)
    return value.strip().lower() not in _FALSY


def skip_requested():
    if "IMPACT_CHECK_SKIP" in os.environ:
        return True
    if "DIFFIMPACTSCOUT_SKIP" in os.environ:
        return True
    return False


def strict_requested():
    return is_true(os.environ.get("IMPACT_CHECK_STRICT"))


def pre_commit_refs():
    return (os.environ.get("PRE_COMMIT_FROM_REF"), os.environ.get("PRE_COMMIT_TO_REF"))