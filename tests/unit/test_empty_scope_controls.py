"""Control: a scope whose readable files hold no function draws no warning, as in 0.8.0.

test_cli_scoring_inproc.py pins this row with 0.8.1's `empty_scopes` field, a
KeyError on 0.8.0. This test replays it and asserts the exit code and stderr
alone, so it passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import pytest

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401
from test_cli_scoring_inproc import _add_constants_scope, run


@pytest.mark.parametrize("command", ["inventory", "coverage"])
def test_a_scope_of_constants_is_not_named_on_stderr(repo, capsys, command):  # noqa: F811
    _add_constants_scope(repo)
    seed_artifacts(repo)
    argv = [command, "--json"] + (["--reuse-artifacts"] if command == "coverage" else [])

    code, out, err = run(argv, repo, capsys)

    assert code == 0, err
    assert "'consts'" not in err, err
