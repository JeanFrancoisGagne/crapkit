"""The one-liners a shell captures are ASCII.

`$x = crapkit worklist` under code page 437 captured `ΓÇö` where the header's
separator was, and the same em dash sat in the lines `init`, `ratchet seed`,
`watch` and two refusals print. The lines a command prints are pinned where
the process prints them (tests/e2e/test_encoding_e2e.py, and the watch banner
in tests/unit/test_watch_shell.py); these are the three no e2e run reaches: a
lane that cannot import pytest-cov, a rewritten history, and a directory with
no crapkit.toml, on both the CLI and the MCP side. doctor's runner line is
pinned here in each of its shapes.
"""
from pathlib import Path

import pytest

from crapkit import launchers
from crapkit.cli._shared import _load_repo_config
from crapkit.cli.admin import _missing_pytest_cov_note, _runner_line
from crapkit.config import Lane
from crapkit.cli.verifying import _require_ancestor
from crapkit.errors import ConfigError, GitError
from crapkit.lane_command import LaunchSpec
from crapkit.mcp_server import _no_config_result
from crapkit.toolchain import Inferred


class _Git:
    def is_ancestor(self, commit: str) -> bool:
        return False

    def is_shallow(self) -> bool:
        return False

    def branches_containing(self, commit: str) -> list[str]:
        return []


@pytest.mark.parametrize("uv_made, install", [
    (False, "python -P -m pip install pytest-cov"),
    (True, "uv pip install --python python pytest-cov"),
], ids=["pip-venv", "uv-venv"])
def test_the_pytest_cov_note_is_ascii(monkeypatch, uv_made, install):
    """The install differs in a venv uv made, so the venv kind is pinned: the
    `python` this machine's PATH resolves to is the suite runner's business."""
    monkeypatch.setattr(launchers, "_uv_made", lambda python: uv_made)
    note = _missing_pytest_cov_note("py", "python", LaunchSpec(Path.cwd()))

    assert f"cannot import pytest_cov - run `{install}`" in note
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
    assert at_cli.startswith(f"no crapkit.toml at {tmp_path} - nothing to analyze; run `")
    assert at_cli.isascii()


@pytest.mark.parametrize("command, found, line", [
    ("python -m pytest --cov", Inferred("pytest", "command", ("pytest",)),
     "lane 'x': runs pytest (named in its command)"),
    ("npm test", Inferred("vitest", "script", ("vitest",), "test"),
     "lane 'x': runs vitest (named in package.json script \"test\")"),
    ("make cov", Inferred("vitest", "package.json"),
     "lane 'x': runs vitest (package.json devDependencies; the command names no runner)"),
    ("npm run cov", Inferred(None, None),
     "lane 'x': runner unknown (npm run cov names none crapkit knows); runner-specific hints "
     "and refusals are off for it"),
    ("npx vitest && pytest", Inferred(None, None, ("vitest", "pytest")),
     "lane 'x': runner unknown (it runs more than one: vitest, pytest); runner-specific hints "
     "are off for it; the refusals still read each segment of its command by the runner that "
     "segment names"),
], ids=["command", "script", "devdependencies", "unknown", "two-runners"])
def test_each_shape_of_doctors_runner_line_is_ascii(command, found, line):
    lane = Lane(name="x", command=command, artifact="x.json", parser="istanbul", scopes=("s",))

    assert _runner_line(lane, found).text == line
    assert line.isascii()
