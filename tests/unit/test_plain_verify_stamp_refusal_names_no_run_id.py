"""A plain verify's stamp refusal names the pin, never a run id from this store.

The store: a coverage run an older crapkit measured, a failed verify, and a newer
coverage run this crapkit measured. The marks carry the older stamp. verify's
rule stays on the old run until a verify passes, and so does a plain `ratchet
seed` (#75), so the refusal has to say more than "run coverage, then seed".

It used to end with `re-baseline from run N with ratchet seed --baseline N`. The
Action quotes that line in a pull request comment, and on a runner that keeps
its workspace run N was the pull request head's own coverage run: an id the
reader does not have, whose seed would sign the breach the failed verify found as
the new ceiling. The refusal now says a failed verify pins the seed and leaves
the id to seed's own line and, under a plain verify, to the taint warning printed
above the refusal: a terminal shows both, and the comment quotes neither.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import OLDER, failed_verify, fresh_run, pinned, stale_marks, twin, write_run

from crapkit.cli import main
from crapkit.invocation import _self


def run(repo, capsys, *argv: str) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def metric_pinned(repo) -> tuple[int, int]:
    """[older-metric coverage, failed verify, this crapkit's coverage]: (failed, fresh)."""
    write_run(repo, [twin(1), twin(2)], versions=OLDER)
    failed = failed_verify(repo)
    return failed, fresh_run(repo)


PINNED = ("a failed verify in this store pins a plain seed to an older run, and seed's line "
          "then names the newer run to read instead")


def test_the_json_refusal_names_the_pin_and_no_run_id(repo, capsys):
    failed, fresh = metric_pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, out, err = run(repo, capsys, "verify", "--reuse-artifacts", "--json")

    message = json.loads(out)["error"]["message"]
    assert code == 3
    assert message.endswith(
        f"run `{_self()} coverage`, then re-baseline with `{_self()} ratchet seed`; {PINNED}"), message
    assert "--baseline" not in message and "from run " not in message, message
    assert f"warning: run {fresh} is not the baseline: verify run {failed} FAILED" in err, err
    assert f"pass `--baseline {fresh}`" in err, err


def test_the_run_the_warning_names_clears_the_stamp(repo, capsys):
    _, fresh = metric_pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "ratchet", "seed", "--baseline", str(fresh))
    assert code == 0, err
    code, out, err = run(repo, capsys, "verify", "--reuse-artifacts")
    assert "were recorded under" not in err, err
    assert code == 0 and out.startswith("verify OK"), out + err


def test_a_verify_measured_from_a_merge_base_names_the_same_pin(repo, capsys):
    """`--base` picks its own baseline, but seed still reads the pinned run."""
    pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "verify", "--reuse-artifacts", "--base", "HEAD")

    assert code == 3
    assert err.strip().endswith(PINNED), err
    assert "--baseline" not in err.strip().splitlines()[-1], err


def test_with_nothing_pinned_the_refusal_keeps_coverage_then_seed(repo, capsys):
    """The next coverage run is the one a plain seed reads, so the stock remedy works."""
    fresh_run(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "verify", "--reuse-artifacts")

    assert code == 3
    assert "is not the baseline" not in err, err
    assert err.strip().endswith(
        f"run `{_self()} coverage`, then re-baseline with `{_self()} ratchet seed`"), err


def test_a_run_named_with_baseline_is_still_the_seed_the_refusal_names(repo, capsys):
    """`--baseline ID` is the caller's own act on the caller's own store."""
    _, fresh = metric_pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "verify", "--reuse-artifacts", "--baseline", str(fresh))

    assert code == 3
    assert err.strip().endswith(
        f"re-baseline from run {fresh} with `{_self()} ratchet seed --baseline {fresh}`"), err
