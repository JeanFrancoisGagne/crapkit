"""`ratchet seed --baseline ID` is the way out of issue #75's deadlock.

The store: a coverage run from before same-line positions, a failed verify, and a
newer coverage run this crapkit measured. The marks: stamped by an older crapkit.
`verify` refuses the stamp and prescribes `ratchet seed`; plain seed reads the run
the failed verify pins, which is the legacy one, and refuses its twins. Neither
could go first. Naming the newer run to seed, the audited act `verify --baseline`
already was, restamps the marks, and the next verify runs.
"""
from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import MARKS, pinned, stale_marks

from crapkit.cli import main
from crapkit.ratchet import metric_version, read_stamp


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_store_starts_in_the_deadlock_the_issue_reported(repo, capsys):
    _, _, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    assert run(["ratchet", "seed"], repo, capsys)[0] == 5, "the pinned run's twins refuse"
    code, _, err = run(["verify", "--reuse-artifacts", "--baseline", str(fresh)], repo, capsys)
    assert code == 3, err
    assert "were recorded under" in err, err


def test_seeding_the_named_newer_run_restamps_the_marks_and_the_next_verify_runs(repo, capsys):
    _, _, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, out, err = run(["ratchet", "seed", "--baseline", str(fresh)], repo, capsys)

    assert code == 0, err
    assert f"vs run {fresh} (" in out, out
    assert read_stamp((repo / MARKS).read_text(encoding="utf-8")) == metric_version()
    code, out, err = run(["verify", "--reuse-artifacts"], repo, capsys)
    assert code == 0, out + err
    assert out.startswith("verify OK"), out
