"""The pytest-cov hint fires when pytest rejects a --cov flag, not any flag.

The hint looked for `--cov` anywhere in the lane log's tail, and the tail
always holds the lane's own command line, which the log echoes first. With
pytest-cov installed, a lane that passed an unknown flag got `unrecognized
arguments: --no-such-flag` quoted back and then the advice to install the
package it already had. The check now reads pytest's own line: the hint fires
only when a rejected argument starts with --cov.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import _missing_plugin_hint, _raise_no_artifact

HINT = "the --cov flags come from the pytest-cov package"
COMMAND = ("python -m pytest --cov=scripts --cov-branch "
           "--cov-report=json:.crapkit/cov/scripts.json --no-such-flag -p no:cacheprovider")
USAGE = "ERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]"


def _lane() -> Lane:
    return Lane(name="scripts", command=COMMAND, artifact=".crapkit/cov/scripts.json",
                parser="coveragepy", scopes=("scripts",))


def _refusal(root: Path, rejected: str) -> str:
    """The no-artifact refusal over a log whose usage error rejected `rejected`."""
    log = root / ".crapkit" / "lane-scripts.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join([f"$ {COMMAND}", USAGE,
                              f"python -m pytest: error: unrecognized arguments: {rejected}",
                              "  inifile: None", f"  rootdir: {root}", "", "(exit 4)"]),
                   encoding="utf-8", newline="\n")
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(root, _lane(), log, 4)
    return str(raised.value)


def test_an_unknown_flag_beside_installed_pytest_cov_draws_no_hint(tmp_path):
    message = _refusal(tmp_path, "--no-such-flag")

    assert "unrecognized arguments: --no-such-flag" in message, message
    assert HINT not in message, message


@pytest.mark.parametrize("rejected", [
    "--cov=scripts --cov-branch --cov-report=json:.crapkit/cov/scripts.json",
    "--no-such-flag --cov=scripts",
])
def test_a_rejected_cov_flag_still_draws_the_hint(tmp_path, rejected):
    assert HINT in _refusal(tmp_path, rejected)


@pytest.mark.parametrize("tail", [
    f"$ {COMMAND}\npython -m pytest: error: unrecognized arguments: --no-such-flag",
    "unrecognized arguments: --no-such-flag\n--cov=scripts",
    "error: unrecognized arguments: --discover",
    "error: unrecognized arguments: x--cov",
])
def test_a_cov_word_outside_the_rejected_arguments_is_not_the_signature(tmp_path, tail):
    assert _missing_plugin_hint(tail, tmp_path, _lane()) == ""
