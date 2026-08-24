"""Failure classes with distinct exit codes. A broken pipeline never renders as a healthy zero."""
from __future__ import annotations


class CrapkitError(Exception):
    exit_code = 1


class ConfigError(CrapkitError):
    exit_code = 3


class GitError(CrapkitError):
    exit_code = 4


class ToolError(CrapkitError):
    exit_code = 5
