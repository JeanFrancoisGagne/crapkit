"""A refusal for want of a baseline names what stands in the way, not a step that cannot help.

A clone whose first verify failed held one run, that failed verify. `ratchet seed`
said to run `crapkit coverage` first; after it, seed said a fresh coverage would
only be refused the same way. verify, on a store whose only run was partial
because a lane failed, said to run `crapkit coverage` first, and every coverage
came back partial while that lane kept failing.
"""
from cli_inproc_repo import repo as inproc_repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit.cli import main
from test_seed_and_prune_read_a_named_run import (FAILED_SHA, NEW_SHA, ratchet,  # noqa: F401
                                                  repo, scored, write)


@pytest.mark.parametrize("action", ["seed", "prune"])
def test_a_store_holding_only_a_failed_verify_names_it(repo, capsys, action):
    write(repo, [scored()], kind="verify", commit=FAILED_SHA, ok=False)

    code, _, err = ratchet(repo, capsys, action)

    assert code == 1
    assert "no run to work from: verify run 1 FAILED with 3 finding(s)" in err, err
    assert "coverage` first" not in err, err


@pytest.mark.parametrize("action", ["seed", "prune"])
def test_the_refusal_does_not_change_once_its_advice_is_followed(repo, capsys, action):
    """The line before the coverage run and the line after it are one line."""
    write(repo, [scored()], kind="verify", commit=FAILED_SHA, ok=False)
    _, _, before = ratchet(repo, capsys, action)
    write(repo, [scored()], kind="coverage", commit=NEW_SHA)

    _, _, after = ratchet(repo, capsys, action)

    assert before == after


def test_verify_on_a_store_holding_only_a_partial_run_names_the_missing_lane(
        inproc_repo, capsys):
    seed_artifacts(inproc_repo)
    main(["coverage", "--lane", "unit", "--reuse-artifacts", "--repo", str(inproc_repo)])
    capsys.readouterr()

    code = main(["verify", "--reuse-artifacts", "--repo", str(inproc_repo)])
    err = capsys.readouterr().err

    assert code == 1
    assert "run 1 is partial, measured without lane ui" in err, err
    assert "coverage` first" not in err, err
