"""The readers that walk run history over runs that lack rows.

A run where one lane failed stores that lane's scopes at the no-lane stand-in,
cov 0, and is typed partial. `explain` lists it labeled, while `trend` and
`digest` leave it out: none of them reads the stand-in as a measurement. A run
`runs prune` removed is gone from `trend`, never listed as a run of 0
functions.
"""
import json

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main


def _run(repo, capsys, *argv: str) -> tuple[int, str]:
    code = main([*argv, "--repo", str(repo)])
    return code, capsys.readouterr().out


def _trend(repo, capsys) -> dict[int, int]:
    code, out = _run(repo, capsys, "trend", "--json")
    assert code == 0
    return {r["run_id"]: r["functions"] for r in json.loads(out)["runs"]}


def _partial_between_two_full_runs(repo, capsys) -> None:
    seed_artifacts(repo)
    assert _run(repo, capsys, "coverage", "--reuse-artifacts")[0] == 0
    (repo / "coverage" / "unit.json").unlink()
    _run(repo, capsys, "coverage", "--reuse-artifacts")
    seed_artifacts(repo)
    assert _run(repo, capsys, "coverage", "--reuse-artifacts")[0] == 0
    capsys.readouterr()


def test_the_fixture_stores_a_partial_run_between_two_full_ones(repo, capsys):
    from crapkit.store import SnapshotStore

    _partial_between_two_full_runs(repo, capsys)

    runs = SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()
    assert [r["kind"] for r in runs] == ["coverage", "partial", "coverage"], runs


def test_explain_labels_the_partial_runs_stand_in(repo, capsys):
    _partial_between_two_full_runs(repo, capsys)

    code, out = _run(repo, capsys, "explain", "src/app.ts", "dispatch")

    (line,) = [row for row in out.splitlines() if row.strip().startswith("run   2")]
    assert code == 0
    assert "partial" in line and "no-lane" in line, line


def test_trend_leaves_the_partial_run_out(repo, capsys):
    _partial_between_two_full_runs(repo, capsys)

    assert list(_trend(repo, capsys)) == [1, 3]


def test_digest_pairs_the_full_runs_and_stays_quiet(repo, capsys):
    _partial_between_two_full_runs(repo, capsys)

    code = main(["digest", "--repo", str(repo)])
    out = capsys.readouterr()

    assert (code, out.out) == (0, ""), out
    assert "skipping" not in out.err, "a partial run is not trusted, so it is not skipped over"


def test_trend_over_a_pruned_history_lists_only_the_runs_that_remain(repo, capsys):
    seed_artifacts(repo)
    for _ in range(3):
        assert _run(repo, capsys, "coverage", "--reuse-artifacts")[0] == 0
    before = _trend(repo, capsys)

    assert _run(repo, capsys, "runs", "prune", "--keep", "1")[0] == 0
    after = _trend(repo, capsys)

    assert set(after) < set(before), (before, after)
    assert after == {run_id: before[run_id] for run_id in after}
    assert 0 not in after.values(), "a pruned run is gone, not a run of 0 functions"
