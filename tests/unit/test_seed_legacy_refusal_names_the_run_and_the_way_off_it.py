"""A seed or prune that cannot key a legacy run's twins says which run, and how to leave it.

The refusal used to read "ambiguous legacy function identity in src/app.ts:
(anonymous); refresh analysis before selecting or comparing these functions". It
named no run. Behind a failed verify the advice was also wrong: the run seed reads
is pinned there, so a fresh coverage run changes nothing, and one usually existed
already (#75). Now the sentence names the run, the verify that pins it or the
`--baseline` that named it, and the command that reads another run.
"""
from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import failed_verify, fresh_run, legacy_run, pinned, stale_marks

import pytest

from crapkit.cli import main
from crapkit.invocation import _self
from crapkit.keys import REFRESH_ADVICE

TWINS = "ambiguous legacy function identity in src/app.ts: (anonymous) in run"


def ratchet(repo, capsys, *argv: str) -> tuple[int, str]:
    code = main(["ratchet", *argv, "--repo", str(repo)])
    return code, capsys.readouterr().err.strip()


@pytest.mark.parametrize("action", ["seed", "prune"])
def test_behind_a_failed_verify_it_names_the_verify_and_the_newer_run(repo, capsys, action):
    legacy, failed, fresh = pinned(repo)
    capsys.readouterr()

    code, err = ratchet(repo, capsys, action)

    assert code == 5
    assert err == (f"crapkit: {TWINS} {legacy}; {action} reads run {legacy} because verify run "
                   f"{failed} FAILED after it and no verify has passed since, so a fresh "
                   f"`{_self()} coverage` alone changes nothing; pass `--baseline {fresh}` "
                   f"to read run {fresh}")
    assert REFRESH_ADVICE not in err


def test_with_two_failures_it_names_the_verify_that_verifys_warning_names(repo, capsys):
    """[legacy, failed verify, fresh, failed verify, fresh]: the second failure is
    the one no verify has answered, so verify's warning names it. Seed named the
    first, a failure whose findings are not the outstanding ones."""
    legacy, _, _ = pinned(repo)
    outstanding = failed_verify(repo)
    fresh_run(repo)
    stale_marks(repo)  # verify stops at the stamp guard, after its warning
    capsys.readouterr()

    verify_code = main(["verify", "--reuse-artifacts", "--repo", str(repo)])
    warning = capsys.readouterr().err
    code, err = ratchet(repo, capsys, "seed")

    assert verify_code == 3 and f"verify run {outstanding} FAILED with" in warning, warning
    assert code == 5
    assert f"seed reads run {legacy} because verify run {outstanding} FAILED after it" in err, err


def test_with_no_newer_run_behind_the_failure_it_asks_for_one_and_its_name(repo, capsys):
    legacy = legacy_run(repo)
    failed = failed_verify(repo)

    code, err = ratchet(repo, capsys, "seed")

    assert code == 5
    assert err.endswith(f"in run {legacy}; seed reads run {legacy} because verify run {failed} "
                        f"FAILED after it and no verify has passed since, so a fresh "
                        f"`{_self()} coverage` alone changes nothing; run `{_self()} coverage` "
                        "and pass the run it writes to `--baseline`"), err


def test_a_named_legacy_run_names_the_newer_run_to_pass_instead(repo, capsys):
    legacy = legacy_run(repo)
    fresh = fresh_run(repo)
    capsys.readouterr()

    code, err = ratchet(repo, capsys, "seed", "--baseline", str(legacy))

    assert code == 5
    assert err.endswith(f"in run {legacy}; seed reads run {legacy} because `--baseline {legacy}` "
                        f"names it, so a fresh `{_self()} coverage` alone changes nothing; pass "
                        f"`--baseline {fresh}` to read run {fresh}"), err


def test_the_newest_run_keeps_the_refresh_advice_a_coverage_run_answers(repo, capsys):
    """Nothing pins this run: the next coverage run is the one seed reads."""
    legacy = legacy_run(repo)

    code, err = ratchet(repo, capsys, "seed")

    assert code == 5
    assert err == f"crapkit: {TWINS} {legacy}; {REFRESH_ADVICE}"
