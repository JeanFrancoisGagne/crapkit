"""`ratchet seed --baseline ID` and `ratchet prune --baseline ID` read the run named.

The same audited escape `verify --baseline ID` is: naming a run steps past the
taint rule, and past nothing else. One admission rule serves all three commands,
so a named run is refused for the four reasons a run cannot serve as a baseline
(a failed verify, a hook run, a partial run, an inventory run) in the words verify
uses. Driven through `main`, the entry point `python -m crapkit` reaches.
"""
from pathlib import Path

import lizard
import pytest

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.cli.verifying import _named_baseline
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

MEASURED = {"analysis_version": str(ANALYSIS_VERSION), "lizard": lizard.version}
CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')
OLD_SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
FAILED_SHA = "4f1c0aa9d3b2e5768190a2b3c4d5e6f70819a2b3"
NEW_SHA = "7c2d1bb0e4c3f6879201b3c4d5e6f7081920a3b4"
MARKS = "crapkit-ratchet.tsv"


def scored(name: str = "hot( n )", ccn: int = 8) -> ScoredRow:
    return ScoredRow("src", "src/a.py", name, 1, 9, ccn, ccn, ccn, 5, 1, 1,
                     0.0, "measured", float(ccn * ccn + ccn), "decompose")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    return tmp_path


def store_of(repo: Path) -> SnapshotStore:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite")


def write(repo: Path, rows: list, *, kind: str = "coverage", commit: str = OLD_SHA,
          ok: bool | None = None) -> int:
    store = store_of(repo)
    run_id = store.write_run(commit=commit, tool_versions=MEASURED, rows=rows, kind=kind,
                             lanes={"unit": {}})
    if ok is not None:
        store.set_verdict_ok(run_id, ok, findings=0 if ok else 3)
    return run_id


def ratchet(repo: Path, capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["ratchet", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def pinned(repo: Path) -> tuple[int, int, int]:
    """[coverage at ccn 8, failed verify, coverage at ccn 9]: verify's rule stays on run 1."""
    old = write(repo, [scored(ccn=8)])
    failed = write(repo, [scored(ccn=20)], kind="verify", commit=FAILED_SHA, ok=False)
    new = write(repo, [scored(ccn=9)], commit=NEW_SHA)
    return old, failed, new


def test_seed_signs_the_marks_of_the_run_it_names_past_a_failed_verify(repo, capsys):
    _, _, new = pinned(repo)

    code, out, err = ratchet(repo, capsys, "seed", "--baseline", str(new))

    assert code == 0, err
    assert out.strip().endswith(f"vs run {new} ({NEW_SHA[:11]})"), "a named run skipped nothing"
    marks = (repo / MARKS).read_text(encoding="utf-8").splitlines()
    assert marks[-1] == "src/a.py\thot( n )\t90.0000", "run 3's 9*9+9, not run 1's 8*8+8"


def test_prune_drops_the_marks_the_named_run_no_longer_holds(repo, capsys):
    old = write(repo, [scored("gone( n )"), scored()])
    write(repo, [scored("gone( n )"), scored()], kind="verify", commit=FAILED_SHA, ok=False)
    new = write(repo, [scored()], commit=NEW_SHA)
    assert ratchet(repo, capsys, "seed", "--baseline", str(old))[0] == 0

    code, out, err = ratchet(repo, capsys, "prune", "--baseline", str(new))

    assert code == 0, err
    assert out.startswith(f"{MARKS}: pruned 1, followed 0 rename(s) - 1 mark(s) vs run {new} "), out
    assert "gone( n )" not in (repo / MARKS).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind,ok,reason", [
    ("verify", False, "a failed verify"),
    ("hook", None, "a hook run"),
    ("partial", None, "a partial run (a lane subset, or a lane that failed)"),
    ("inventory", None, "an inventory run (no coverage was measured)"),
])
@pytest.mark.parametrize("action", ["seed", "prune"])
def test_a_named_run_that_cannot_serve_is_refused_in_verifys_words(repo, capsys, action,
                                                                   kind, ok, reason):
    trusted = write(repo, [scored()])
    named = write(repo, [], kind=kind, commit=NEW_SHA, ok=ok)

    code, out, err = ratchet(repo, capsys, action, "--baseline", str(named))

    assert code == 1 and out == ""
    assert err.strip() == (f"crapkit: run {named} is {reason} and cannot serve as a baseline; "
                           f"trusted runs: {trusted}; pass `--baseline {trusted}` for the newest")
    with pytest.raises(CrapkitError) as verify_refusal:
        _named_baseline(store_of(repo), repo, named)
    assert err.strip() == f"crapkit: {verify_refusal.value}", "one rule, one sentence"
    assert not (repo / MARKS).exists(), "a refused run writes nothing"


def test_a_run_the_store_does_not_hold_is_named_as_missing(repo, capsys):
    trusted = write(repo, [scored()])

    code, _, err = ratchet(repo, capsys, "seed", "--baseline", "99")

    assert code == 1
    assert "no run 99 in the store" in err and f"trusted runs: {trusted}" in err, err


def test_a_store_with_no_trusted_run_keeps_seeds_own_line(repo, capsys):
    named = write(repo, [], kind="inventory")

    code, _, err = ratchet(repo, capsys, "seed", "--baseline", str(named))

    assert code == 1
    assert "no trusted full run to work from" in err, err


@pytest.mark.parametrize("action", ["report", "move", "merge"])
def test_an_action_that_reads_no_run_refuses_the_flag(repo, capsys, action):
    """Silently ignoring it would let `ratchet report --baseline 3` read as a
    report about run 3."""
    code, _, err = ratchet(repo, capsys, action, "--baseline", "1")

    assert code == 3
    assert f"ratchet {action} reads no run, so it takes no --baseline" in err, err
