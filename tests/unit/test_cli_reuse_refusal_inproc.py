"""`coverage --reuse-artifacts` after a failed lane, driven in-process.

Every assertion is what the operator reads: the exit code, the stderr line,
the runs the store holds. The template's lanes run `python -c pass`, which
writes nothing, so a plain `coverage` over seeded artifacts is the failed
attempt this file is about.
"""
import os

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.store import SnapshotStore


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def runs(repo) -> list[dict]:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()


def _salvage(repo, rel: str) -> None:
    artifact = repo / rel
    later = artifact.stat().st_mtime_ns + 5_000_000_000
    os.utime(artifact, ns=(later, later))


def test_reuse_refuses_what_the_failed_attempt_left_and_scores_the_salvage(repo, capsys):
    seed_artifacts(repo)
    code, _, err = run(["coverage"], repo, capsys)
    assert code == 5 and err.count("wrote no artifact this run") == 2, err

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 5, err
    assert err.count("wrote no artifact on its last attempt") == 2, err
    assert "every lane failed (2 of 2)" in err
    assert runs(repo) == [], "no run is written over refused artifacts"

    _salvage(repo, "coverage/unit.json")
    _salvage(repo, "coverage/ui.json")
    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 0, err
    assert [r["kind"] for r in runs(repo)] == ["coverage"]


def test_verify_cannot_conclude_over_a_refused_artifact(repo, capsys):
    """A verdict with a blind lane is not a verdict: the refused lane is a
    failed lane, and verify says so instead of judging the other lane's
    numbers and writing itself in as the baseline."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0, "the baseline"
    assert run(["coverage"], repo, capsys)[0] == 5, "the failed attempt"
    _salvage(repo, "coverage/unit.json")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 5, err
    assert "verify cannot conclude with failed lanes: ui" in err, err
    assert "wrote no artifact on its last attempt" in err
    assert out == ""
    assert [r["kind"] for r in runs(repo)] == ["coverage"], "verify wrote no run of any kind"


def test_one_refused_lane_makes_the_reuse_run_partial(repo, capsys):
    """The other lane's artifact is read; the refused one's scopes fall back to
    no-lane and the run can never be a baseline."""
    seed_artifacts(repo)
    assert run(["coverage"], repo, capsys)[0] == 5
    _salvage(repo, "coverage/unit.json")

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 5, err
    assert "lane 'ui' FAILED: lane 'ui' wrote no artifact on its last attempt" in err
    assert "lane 'unit' FAILED" not in err
    assert [r["kind"] for r in runs(repo)] == ["partial"]
