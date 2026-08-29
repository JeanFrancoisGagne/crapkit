"""Where the 0.4.3 field fixes meet: one trust rule, one key.

Three fixes landed against run history and the ratchet key in the same release,
and each was measured on its own branch. These pin the seams BETWEEN them, which
no branch could see:

- `ratchet seed` skips a failed verify (#16) and verify's tighten damping (#15)
  reads the same commit's earlier run. Two readers of run history, so they need
  one answer to "is this run a measurement": `store.is_trusted`.
- The damping compares a mark against what an earlier run measured (#15), and a
  mark is now keyed by twin ordinal (#17). Both sides of that comparison have to
  be the same key or the numbers belong to different functions.
- The git merge driver merges marks files (0.3.x) that now carry `#N` keys (#17).
"""
import argparse
from pathlib import Path

from crapkit.cli.ratchet_cmds import _latest_full_run, _ratchet_merge
from crapkit.cli.verifying import _prior_crap
from crapkit.ratchet import RatchetEntry, load_ratchet, merge_ratchets, unstable_marks
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
PATH = "src/models.py"
NAME = "__post_init__( self )"
STAMP = "# crapkit-analysis=4 lizard=1.17.31"
HEADER = "path\tlong_name\tcrap"


def scored(crap: float, *, start: int = 1, ccn: int = 8, name: str = NAME) -> ScoredRow:
    """One scored row at a stated CRAP. `start` is what tells two twins apart."""
    return ScoredRow("src", PATH, name, start, start + 20, ccn, ccn, ccn, 20, 1, 2,
                     0.0, "measured", crap, "decompose")


def run(store: SnapshotStore, kind: str, rows: list, *, ok: bool | None = None) -> int:
    run_id = store.write_run(commit=SHA, tool_versions={}, rows=rows, kind=kind,
                             lanes={"unit": {"tests_total": 40}})
    if ok is not None:
        store.set_verdict_ok(run_id, ok, findings=0 if ok else 3)
    return run_id


def test_a_failed_verify_is_invisible_to_both_readers_of_run_history(tmp_path: Path):
    """The one fact both fixes turn on, asserted against both.

    Run 1 measured CRAP 20.0 and passed. Run 2 measured 72.0 and FAILED. Seeding
    from run 2 signs debt at a red tree's numbers (#16); damping against run 2
    calls a 20.0 -> 20.0 measurement a jump and freezes a mark that never moved
    (#15). Neither may see it.
    """
    store = SnapshotStore(tmp_path / "crap.sqlite")
    trusted = run(store, "coverage", [scored(20.0)])
    failed = run(store, "verify", [scored(72.0)], ok=False)
    asking = run(store, "verify", [scored(20.0)])

    latest, skipped = _latest_full_run(store)

    assert (latest["id"], [r["id"] for r in skipped]) == (trusted, [failed]), "seed (#16)"
    assert store.prior_scored_run(commit=SHA, before=asking) == trusted, "damping (#15)"
    assert _prior_crap(store, SHA, asking) == {(PATH, NAME): 20.0}
    assert unstable_marks([RatchetEntry(PATH, NAME, 90.0)], [scored(20.0)],
                          _prior_crap(store, SHA, asking), max_jump=2.0) == []


def test_a_passing_verify_is_the_comparison_point_for_both(tmp_path: Path):
    """Passing is what makes a verify trusted; the rule is not "never a verify"."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run(store, "coverage", [scored(20.0)])
    passed = run(store, "verify", [scored(20.0)], ok=True)
    asking = run(store, "verify", [scored(72.0)])

    latest, _ = _latest_full_run(store)

    assert latest["id"] == passed
    assert store.prior_scored_run(commit=SHA, before=asking) == passed


def test_a_partial_run_cannot_damp_a_mark(tmp_path: Path):
    """A lane whose worker crashed writes a `partial` run (#21). Its coverage is
    a fraction of the suite's, so its CRAP is inflated: reading it as the earlier
    measurement holds every mark in the repo against numbers nothing vouches for."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    trusted = run(store, "coverage", [scored(20.0)])
    run(store, "partial", [scored(72.0)])
    asking = run(store, "verify", [scored(20.0)])

    assert store.prior_scored_run(commit=SHA, before=asking) == trusted
    assert _prior_crap(store, SHA, asking) == {(PATH, NAME): 20.0}


def test_the_damping_compares_each_twin_against_its_own_earlier_score(tmp_path: Path):
    """Two `__post_init__` in one module: the shape #17 was reported on.

    Twin #1 measured 20.0 twice and has not moved. Twin #2 bounced 8.0 -> 72.0.
    Keyed by long_name alone, both sides collapse to the worst twin and the
    answer comes out backwards: twin #1 reads as the one that jumped.
    """
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run(store, "coverage", [scored(20.0, start=1), scored(8.0, start=40)])
    asking = run(store, "verify", [scored(20.0, start=1), scored(72.0, start=40)])

    previous = _prior_crap(store, SHA, asking)

    assert previous == {(PATH, NAME): 20.0, (PATH, f"{NAME}#2"): 8.0}
    marks = [RatchetEntry(PATH, NAME, 90.0), RatchetEntry(PATH, f"{NAME}#2", 90.0)]
    fresh = [scored(20.0, start=1), scored(72.0, start=40)]
    refusals = unstable_marks(marks, fresh, previous, max_jump=2.0)
    assert [(r.long_name, r.previous, r.fresh) for r in refusals] == [(f"{NAME}#2", 8.0, 72.0)]


def marks_file(path: Path, body: str) -> Path:
    path.write_text(f"{STAMP}\n{HEADER}\n{body}", encoding="utf-8", newline="\n")
    return path


def test_the_merge_driver_resolves_two_marks_files_carrying_twin_keys(tmp_path: Path):
    """git hands the driver BASE OURS THEIRS and reads OURS back.

    A `#2` sits in the NAME field, so a marks line never starts with the `#` the
    stamp reader claims, and the driver's per-key 3-way rule needs no ordinal
    knowledge: each twin is its own key, so one branch tightening twin #1 and the
    other tightening twin #2 is not a conflict at all.
    """
    base = marks_file(tmp_path / "base.tsv",
                      f"{PATH}\t{NAME}\t90.0000\n{PATH}\t{NAME}#2\t90.0000\n")
    ours = marks_file(tmp_path / "ours.tsv",
                      f"{PATH}\t{NAME}\t20.0000\n{PATH}\t{NAME}#2\t90.0000\n")
    theirs = marks_file(tmp_path / "theirs.tsv",
                        f"{PATH}\t{NAME}\t90.0000\n{PATH}\t{NAME}#2\t8.0000\n")

    assert _ratchet_merge([str(base), str(ours), str(theirs)]) == 0

    merged = {(e.path, e.long_name): e.crap
              for e in load_ratchet(ours.read_text(encoding="utf-8"))}
    assert merged == {(PATH, NAME): 20.0, (PATH, f"{NAME}#2"): 8.0}


def test_both_branches_tightening_one_twin_keeps_the_lower_mark():
    """The both-changed rule, on a key an ordinal built: still min, still no conflict."""
    key = f"{NAME}#3"
    merged = merge_ratchets([RatchetEntry(PATH, key, 90.0)], [RatchetEntry(PATH, key, 40.0)],
                            [RatchetEntry(PATH, key, 12.0)])

    assert merged == [RatchetEntry(PATH, key, 12.0)]


def seed(repo: Path) -> int:
    from crapkit.cli.ratchet_cmds import cmd_ratchet

    return cmd_ratchet(argparse.Namespace(action="seed", repo=str(repo)))


def test_seed_records_every_twin_and_verify_gates_each_of_them(tmp_path: Path):
    """The whole point of #17, through the command that writes the file: two
    functions of one name, two marks, at their own values."""
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
        'languages = ["python"]\n', encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")
    run(store, "coverage", [scored(20.0, start=1), scored(72.0, start=40)])

    assert seed(tmp_path) == 0

    text = (tmp_path / "crapkit-ratchet.tsv").read_text(encoding="utf-8")
    assert {(e.long_name, e.crap) for e in load_ratchet(text)} == {(NAME, 20.0),
                                                                  (f"{NAME}#2", 72.0)}
