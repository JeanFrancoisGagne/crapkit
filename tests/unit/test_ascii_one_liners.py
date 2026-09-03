"""The one-liners a shell captures are ASCII.

`$x = crapkit worklist` under code page 437 captured `ΓÇö` where the header's
separator was, and the same em dash sat in the lines `init`, `ratchet seed`,
`watch` and two refusals print. JSON is ASCII-escaped already; these are the
text lines a script reads back.
"""
from pathlib import Path

from crapkit.cli._shared import _load_repo_config
from crapkit.cli.admin import _missing_pytest_cov_note, _next_step, _watch_banner
from crapkit.cli.verifying import _require_ancestor
from crapkit.errors import ConfigError, GitError
from crapkit.mcp_server import _no_config_result
from crapkit.scaffold import LaneSpec


class _Git:
    def is_ancestor(self, commit: str) -> bool:
        return False

    def is_shallow(self) -> bool:
        return False


def test_the_watch_banner_is_ascii():
    assert _watch_banner(12, 2.0, None) == "watching 12 tracked files every 2.0s - ctrl-c to stop"
    assert _watch_banner(12, 0.5, 3) == \
        "watching 12 tracked files every 0.5s - 3 poll(s) then stop"


def test_the_init_next_step_is_ascii():
    lane = LaneSpec("py", "pytest", ".crapkit/cov.json", "coveragepy", ("python",))
    detected = _next_step({"calc": ("python",)}, (lane,))
    cc_only = _next_step({"tools": ("go",)}, ())

    assert detected.startswith("detected 1 lane(s) from this repo's own files: py - next: run `")
    assert " - next: run `" in cc_only
    assert detected.isascii() and cc_only.isascii()


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
