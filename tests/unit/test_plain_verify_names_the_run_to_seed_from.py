"""A plain verify's stamp refusal names the seed that clears it on a pinned store.

The store: a coverage run an older crapkit measured, a failed verify, and a newer
coverage run this crapkit measured. The marks carry the older stamp. verify's
rule stays on the old run until a verify passes, and so does `ratchet seed`. The
refusal used to end with the stock "run coverage, then re-baseline with ratchet
seed": that seed read the old run, kept the old stamp, and verify refused again
in the same words. A fresh coverage run sits behind the failure too, so
following the remedy looped. The refusal now names the run the taint warning
above it names.
"""
from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import OLDER, failed_verify, fresh_run, pinned, stale_marks, twin, write_run

from crapkit.cli import main
from crapkit.invocation import _self


def run(repo, capsys, *argv: str) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def metric_pinned(repo) -> int:
    """[older-metric coverage, failed verify, this crapkit's coverage]; the fresh run's id."""
    write_run(repo, [twin(1), twin(2)], versions=OLDER)
    failed_verify(repo)
    return fresh_run(repo)


def test_the_refusal_names_the_seed_that_clears_the_stamp_and_that_seed_does(repo, capsys):
    fresh = metric_pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "verify", "--reuse-artifacts")

    assert code == 3
    seed = f"{_self()} ratchet seed --baseline {fresh}"
    assert err.strip().endswith(f"re-baseline from run {fresh} with `{seed}`"), err
    code, _, err = run(repo, capsys, "ratchet", "seed", "--baseline", str(fresh))
    assert code == 0, err
    code, out, err = run(repo, capsys, "verify", "--reuse-artifacts")
    assert "were recorded under" not in err, err
    assert code == 0 and out.startswith("verify OK"), out + err


def test_a_verify_measured_from_a_merge_base_names_the_same_seed(repo, capsys):
    """`--base` picks its own baseline, but seed still reads the pinned run, so
    the stock remedy loops there too."""
    _, _, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = run(repo, capsys, "verify", "--reuse-artifacts", "--base", "HEAD")

    assert code == 3
    assert err.strip().endswith(
        f"re-baseline from run {fresh} with `{_self()} ratchet seed --baseline {fresh}`"), err


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
