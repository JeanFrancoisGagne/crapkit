"""worklist_surface's `scored(repo)` takes one coverage run per worker.

Most of that file's tests open with `scored(repo)` on the copy the fixture just
made, so every one of them used to measure the same tree again. Now the first
one measures it in a worker-wide copy and the rest take that copy. A test that
touched its repo first still gets a run of its own, because its tree is no
longer the tree that was measured.
"""
from pathlib import Path

import test_worklist_surface_e2e as surface
from conftest import run_cli


def _copies(tmp_path: Path, *names: str) -> list[Path]:
    """Fixture repos for sibling tests of one private worker."""
    out = []
    for name in names:
        here = tmp_path / "worker" / name
        here.mkdir(parents=True)
        out.append(surface.repo.__wrapped__(here))
    return out


def _counting_coverage(monkeypatch) -> list[Path]:
    runs = []
    measure = surface._coverage

    def counted(repo: Path) -> Path:
        runs.append(repo)
        return measure(repo)

    monkeypatch.setattr(surface, "_coverage", counted)
    return runs


def test_untouched_copies_share_one_coverage_run(tmp_path, monkeypatch):
    runs = _counting_coverage(monkeypatch)
    first, second = _copies(tmp_path, "one", "two")

    surface.scored(first)
    surface.scored(second)

    assert len(runs) == 1 and runs[0] not in (first, second), \
        "one run, taken in the worker's copy, not in either test's repo"
    assert surface.worklist(first)["active"] == surface.worklist(second)["active"]


def test_a_copy_scored_from_the_shared_run_answers_what_its_own_run_would(tmp_path):
    shared, own = _copies(tmp_path, "one", "two")

    surface.scored(shared)
    surface._coverage(own)

    ask = ("worklist", "--json")
    assert run_cli(shared, *ask).stdout == run_cli(own, *ask).stdout


def test_a_touched_copy_takes_a_run_of_its_own(tmp_path, monkeypatch):
    runs = _counting_coverage(monkeypatch)
    (repo,) = _copies(tmp_path, "one")
    surface.cover_delta(repo)

    surface.scored(repo)

    assert runs == [repo]
    assert surface.next_item(repo, "--scope", "extra", "--exclude", "gamma")["empty"] is True, \
        "the run read the coverage plan this test wrote"
