"""The one-liners a shell captures are ASCII.

`$x = crapkit worklist` under code page 437 captured `ΓÇö` where the header's
separator was, and the same em dash sat in the lines `init`, `ratchet seed`,
`watch` and two refusals print. The lines a command prints are pinned where
the process prints them (tests/e2e/test_encoding_e2e.py, and the watch banner
in tests/unit/test_watch_shell.py); these are the three no e2e run reaches: a
lane that cannot import pytest-cov, a rewritten history, and a directory with
no crapkit.toml, on both the CLI and the MCP side.
"""
from pathlib import Path

from crapkit.cli._shared import _load_repo_config
from crapkit.cli.admin import _missing_pytest_cov_note
from crapkit.cli.verifying import _require_ancestor
from crapkit.errors import ConfigError, GitError
from crapkit.mcp_server import _no_config_result


class _Git:
    def is_ancestor(self, commit: str) -> bool:
        return False

    def is_shallow(self) -> bool:
        return False


def test_the_pytest_cov_note_is_ascii():
    note = _missing_pytest_cov_note("py", "python")

    assert "cannot import pytest_cov - run `python -m pip install pytest-cov`" in note
    assert note.isascii()


def test_the_rewritten_history_refusal_is_ascii():
    try:
        _require_ancestor(_Git(), "0123456789abcdef")
    except GitError as refused:
        assert "(rebase or amend rewrote history) - run `" in str(refused)
        assert str(refused).isascii()
    else:
        raise AssertionError("a commit that is not an ancestor must be refused")


def test_the_no_config_lines_are_ascii(tmp_path: Path):
    over_mcp = _no_config_result(str(tmp_path))["content"][0]["text"]
    try:
        _load_repo_config(tmp_path)
    except ConfigError as refused:
        at_cli = str(refused)
    else:
        raise AssertionError("a directory with no crapkit.toml must be refused")

    assert over_mcp.startswith(f"no crapkit.toml in {tmp_path} - nothing measured here.")
    assert at_cli == f"no crapkit.toml at {tmp_path} - nothing to analyze"
