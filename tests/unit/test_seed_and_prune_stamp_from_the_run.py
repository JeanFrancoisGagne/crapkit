"""`ratchet seed` and `ratchet prune` stamp the marks from the run they read.

Both used to stamp the running metric. After an upgrade that changed the
analysis version, seed (the remedy verify's stamp refusal prints) or prune run
before any new coverage signed analysis 7 numbers as analysis 10, and verify
stopped refusing and compared them. Now seed signs each scope with the stamp
the run recorded for it, prune adds no numbers and keeps the recorded stamp,
and the line both print names each scope the run measured under other rules.
"""
import argparse

import lizard
import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.cli.verifying import _guard_ratchet_stamp, verify_stamp_conflict
from crapkit.config import load_config_text
from crapkit.errors import ConfigError
from crapkit.invocation import _self
from crapkit.ratchet import ScopeStamp, ScopeStamps, read_stamp, running_stamp
from crapkit.ratchetfile import RatchetFile
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
OLD_STAMP = "crapkit-analysis=7 lizard=1.17.10"
CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')
MARKS = "crapkit-ratchet.tsv"
CFG = load_config_text(CONFIG)
RUNNING = running_stamp(CFG)
# What a 0.4.4 install measured src under, had it recorded scopes: analysis 7, lizard 1.17.10.
OLD_SCOPES = ScopeStamps("1.17.10", {"src": ScopeStamp({"python": 7}, "no-lane")})


def versions(stamps: ScopeStamps, crapkit: str = "0.9.0") -> dict:
    return {"crapkit": crapkit, "lizard": stamps.lizard, "analysis_version": "13",
            "scope_stamps": {name: stamp._asdict() for name, stamp in stamps.scopes.items()}}


OLD = versions(OLD_SCOPES, "0.4.4")
CURRENT = versions(RUNNING)
OLD_SAID = (f"scope src under [lizard=1.17.10 analysis python=7; coverage no-lane], not this "
            f"crapkit's [lizard={lizard.version} {RUNNING.scopes['src'].render()}]")


def hot(ccn: int = 8) -> ScoredRow:
    return ScoredRow("src", "src/a.py", "hot( n )", 1, 9, ccn, ccn, ccn, 5, 1, 1,
                     0.0, "measured", float(ccn * ccn + ccn), "decompose")


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    return tmp_path


def measured_run(repo, tool_versions: dict) -> int:
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit=SHA, tool_versions=tool_versions, rows=[hot()],
                             kind="coverage", lanes={"unit": {}})
    store._conn.close()
    return run_id


def write_marks(repo, stamp: str) -> None:
    (repo / MARKS).write_text(f"# {stamp}\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
                              "src/a.py\thot( n )\t72.0000\n", encoding="utf-8")


def ratchet(repo, action: str) -> int:
    return cmd_ratchet(argparse.Namespace(action=action, repo=str(repo), baseline=None))


def stamp(repo) -> str:
    return read_stamp((repo / MARKS).read_text(encoding="utf-8"))


def after_seed() -> str:
    """The line every seed ends with: the README's next two steps."""
    return f"-> next: commit {MARKS}, then run `{_self()} verify`\n"


# --- seed ----------------------------------------------------------------------

def test_seed_from_a_run_under_an_older_metric_signs_that_metric(repo, capsys):
    """The line names the scope, the step that restamps the marks, and no verify
    follows it: that verify would refuse the stamp this seed signed."""
    run_id = measured_run(repo, OLD)
    write_marks(repo, OLD_STAMP)

    assert ratchet(repo, "seed") == 0

    assert stamp(repo) == OLD_SCOPES
    assert verify_stamp_conflict(RatchetFile.read(repo / MARKS), CFG).refuses, "verify still refuses"
    assert capsys.readouterr().out == (
        f"{MARKS}: added 0, tightened 0 - 1 mark(s) vs run {run_id} ({SHA[:11]}); "
        f"run {run_id} measured {OLD_SAID}, so verify refuses these marks until a fresh "
        f"`{_self()} coverage` and another seed\n")


def test_seed_from_a_run_under_the_running_metric_ends_with_the_commit_and_verify(repo, capsys):
    run_id = measured_run(repo, CURRENT)

    assert ratchet(repo, "seed") == 0

    assert stamp(repo) == RUNNING
    assert capsys.readouterr().out == (
        f"{MARKS}: added 1, tightened 0 - 1 mark(s) vs run {run_id} ({SHA[:11]})\n{after_seed()}")


@pytest.mark.parametrize("tool_versions", [{}, {"crapkit": "0.8.1", "lizard": "1.24.0", "analysis_version": "13"}],
                         ids=["nothing", "single-number"])
def test_seed_refuses_a_run_that_recorded_no_stamp_per_scope(repo, tool_versions):
    run_id = measured_run(repo, tool_versions)
    write_marks(repo, OLD_STAMP)
    before = (repo / MARKS).read_bytes()

    with pytest.raises(ConfigError, match=f"ratchet seed: run {run_id} recorded no metric per scope"):
        ratchet(repo, "seed")

    assert (repo / MARKS).read_bytes() == before


# --- prune ---------------------------------------------------------------------

def test_prune_keeps_the_recorded_stamp_when_the_run_is_newer(repo, capsys):
    """Prune adds no numbers. Stamping the run's metric would relabel analysis 7
    marks as current and verify would compare them."""
    run_id = measured_run(repo, CURRENT)
    write_marks(repo, OLD_STAMP)

    assert ratchet(repo, "prune") == 0

    assert stamp(repo) == OLD_STAMP
    assert capsys.readouterr().out == (
        f"{MARKS}: pruned 0, followed 0 rename(s) - 1 mark(s) vs run {run_id} ({SHA[:11]})\n")


def test_prune_against_a_run_under_an_older_metric_says_so(repo, capsys):
    run_id = measured_run(repo, OLD)
    write_marks(repo, OLD_STAMP)

    assert ratchet(repo, "prune") == 0

    assert stamp(repo) == OLD_STAMP
    assert capsys.readouterr().out.endswith(
        f"; run {run_id} measured {OLD_SAID}, and the marks keep their recorded stamp\n")


def test_prune_against_a_run_that_recorded_no_metric_says_so(repo, capsys):
    run_id = measured_run(repo, {})
    write_marks(repo, OLD_STAMP)

    assert ratchet(repo, "prune") == 0

    assert stamp(repo) == OLD_STAMP
    assert capsys.readouterr().out.endswith(
        f"; run {run_id} was measured under an unrecorded metric, not this crapkit's rules for "
        "scope(s) src, and the marks keep their recorded stamp\n")


def test_prune_that_creates_the_marks_file_stamps_the_running_metric(repo, capsys):
    """The new file holds no mark, so the running metric relabels no number.
    Stamping the older run's metric made the next verify refuse a file with
    zero marks, and the line claimed a recorded stamp there was none of."""
    run_id = measured_run(repo, OLD)

    assert ratchet(repo, "prune") == 0

    assert stamp(repo) == RUNNING
    assert _guard_ratchet_stamp(RatchetFile.read(repo / MARKS), CFG) is None
    assert capsys.readouterr().out == (
        f"{MARKS}: pruned 0, followed 0 rename(s) - 0 mark(s) vs run {run_id} ({SHA[:11]}); "
        f"run {run_id} measured {OLD_SAID}\n")
