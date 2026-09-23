"""A seed that read an older crapkit's run behind a failed verify names the seed that clears the stamp.

Seed signs the metric of the run it reads. The line used to say "verify refuses
these marks until a fresh `crapkit coverage` and another seed". Behind a failed
verify that is false: the fresh run lands behind the failure, the next plain seed
reads the old run again and keeps the old stamp. The same holds when
`--baseline` named the old run. On those paths the line names the run to seed
from, or asks for a coverage run and its id when the store holds none this
crapkit measured.
"""
from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import OLDER, STALE, failed_verify, fresh_run, stale_marks, twin, write_run

from crapkit.cli import main
from crapkit.invocation import _self
from crapkit.ratchet import metric_version


def older_run(repo) -> int:
    return write_run(repo, [twin(1), twin(2)], versions=OLDER)


def seed(repo, capsys, *argv: str) -> str:
    capsys.readouterr()
    code = main(["ratchet", "seed", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    assert code == 0, out.err
    return out.out.strip()


def clause(run_id: int, way_off: str) -> str:
    return (f"; run {run_id} was measured under [{STALE}], not this crapkit's "
            f"[{metric_version()}], so verify refuses these marks until a seed from a run "
            f"this crapkit measured: {way_off}")


def test_behind_a_failure_it_names_the_newer_run_this_crapkit_measured(repo, capsys):
    older = older_run(repo)
    failed_verify(repo)
    fresh = fresh_run(repo)
    stale_marks(repo)

    line = seed(repo, capsys)

    assert line.endswith(clause(older, f"pass `--baseline {fresh}` to read run {fresh}")), line


def test_behind_a_failure_with_no_newer_run_it_asks_for_a_coverage_run_and_its_id(repo, capsys):
    older = older_run(repo)
    failed_verify(repo)
    stale_marks(repo)

    line = seed(repo, capsys)

    assert line.endswith(clause(
        older, f"run `{_self()} coverage` and pass the run it writes to `--baseline`")), line


def test_a_newer_run_the_older_crapkit_measured_too_is_not_the_one_to_name(repo, capsys):
    """Seeding from it signs the same old stamp."""
    older = older_run(repo)
    failed_verify(repo)
    older_run(repo)
    stale_marks(repo)

    line = seed(repo, capsys)

    assert line.endswith(clause(
        older, f"run `{_self()} coverage` and pass the run it writes to `--baseline`")), line


def test_a_named_older_run_names_the_newer_run_to_pass_instead(repo, capsys):
    older = older_run(repo)
    fresh = fresh_run(repo)
    stale_marks(repo)

    line = seed(repo, capsys, "--baseline", str(older))

    assert line.endswith(clause(older, f"pass `--baseline {fresh}` to read run {fresh}")), line
