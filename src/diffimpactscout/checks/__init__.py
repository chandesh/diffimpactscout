"""Checks package: registry of all guard checks and the make_check factory."""

from diffimpactscout.checks import base
from diffimpactscout.checks.base import (
    REGISTRY,
    Check,
    CheckContext,
    CheckIssue,
    CheckResult,
    ExternalCheck,
    make_check,
    register,
)

import diffimpactscout.checks.hygiene
import diffimpactscout.checks.syntax
import diffimpactscout.checks.repo_checks
import diffimpactscout.checks.ruff
import diffimpactscout.checks.eslint
import diffimpactscout.checks.prettier
