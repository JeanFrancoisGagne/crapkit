"""A seed that would add same-line twin marks to a legacy-keyed file names prune first.

A marks file with no `# crapkit-keys=1` keeps the old start-only key format for as
long as one of its marks names a function the run lacks: nothing can prove what that
mark's ordinal meant. Seed keys the marks it adds by `(start, occurrence)`, and the
old format cannot hold a mark for one of two functions that start on one line, so
the publish check refused the file seed rendered. Its sentence named every twin
group seed was about to add as legacy marks to "preserve and reconcile", marks the
file did not hold, and never named the command that ends the old format: prune
drops the unseen marks, and seed then writes the positioned format. On a large
consumer repo that refusal named 170 groups, none of them marked.
"""
from pathlib import Path

import lizard
import pytest

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.ratchet import stamp_text
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

MEASURED = {"analysis_version": str(ANALYSIS_VERSION), "lizard": lizard.version}
CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n')
OLD_SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
FAILED_SHA = "4f1c0aa9d3b2e5768190a2b3c4d5e6f70819a2b3"
NEW_SHA = "7c2d1bb0e4c3f6879201b3c4d5e6f7081920a3b4"
MARKS = "crapkit-ratchet.tsv"
ROOT = Path(__file__).resolve().parents[2]
# Legacy keys: a metric stamp and no key stamp. The one mark names a file the run lacks.
LEGACY = (f"# {stamp_text(ANALYSIS_VERSION, lizard.version)}\npath\tlong_name\tcrap\n"
          "src/gone.ts\tgone( )\t50.0000\n")


def twin(occurrence: int) -> ScoredRow:
    """One of two untested ccn-9 callbacks on line 1, crap 90: seed marks both."""
    return ScoredRow("src", "src/app.ts", "(anonymous)", 1, 9, 9, 9, 9, 9, 0, 0,
                     0.0, "untested", 90.0, "decompose", 0, occurrence)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / MARKS).write_text(LEGACY, encoding="utf-8", newline="\n")
    return tmp_path


def write(repo: Path, rows: list, *, kind: str = "coverage", commit: str = NEW_SHA,
          ok: bool | None = None) -> int:
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit=commit, tool_versions=MEASURED, rows=rows, kind=kind,
                             lanes={"unit": {}})
    if ok is not None:
        store.set_verdict_ok(run_id, ok, findings=3)
    store.close()
    return run_id


def ratchet(repo: Path, capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["ratchet", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def named_run(repo: Path) -> int:
    """A legacy-twin run, a failed verify pinning it, and the positioned run to name."""
    write(repo, [twin(0), twin(0)], commit=OLD_SHA)
    write(repo, [twin(1), twin(2)], kind="verify", commit=FAILED_SHA, ok=False)
    return write(repo, [twin(1), twin(2)])


def test_seed_names_prune_instead_of_reconciling_marks_the_file_does_not_hold(repo, capsys):
    run = named_run(repo)

    code, out, err = ratchet(repo, capsys, "seed", "--baseline", str(run))

    assert code == 3 and out == ""
    assert "preserve these marks" not in err, err
    assert (f"1 mark(s) name functions run {run} does not hold, first src/gone.ts: gone( )"
            in err), err
    assert "same-line twins in 1 group(s) this seed would mark, first src/app.ts: (anonymous)" \
        in err, err
    assert f"ratchet prune --baseline {run}` first, then seed again" in err, err
    assert (repo / MARKS).read_text(encoding="utf-8") == LEGACY, "a refused seed writes nothing"


def test_a_plain_seed_names_a_plain_prune(repo, capsys):
    run = write(repo, [twin(1), twin(2)])

    code, _, err = ratchet(repo, capsys, "seed")

    assert code == 3
    assert f"run {run} does not hold" in err and "ratchet prune` first, then seed again" in err, err


def test_prune_then_seed_writes_both_twins_under_the_positioned_keys(repo, capsys):
    run = named_run(repo)

    assert ratchet(repo, capsys, "prune", "--baseline", str(run))[0] == 0
    code, _, err = ratchet(repo, capsys, "seed", "--baseline", str(run))

    assert code == 0, err
    lines = (repo / MARKS).read_text(encoding="utf-8").splitlines()
    assert lines[1:] == ["# crapkit-keys=1", "path\tlong_name\tcrap",
                         "src/app.ts\t(anonymous)\t90.0000",
                         "src/app.ts\t(anonymous)#2\t90.0000"], lines


def _after(page: str, anchor: str) -> str:
    """The 600 characters from ANCHOR on, with the page's line breaks read as spaces."""
    text = " ".join((ROOT / page).read_text(encoding="utf-8").split())
    start = text.index(anchor)
    return text[start:start + 600]


@pytest.mark.parametrize("page,anchor,recipe", [
    ("docs/upgrading.md", "After upgrading, in each repo:",
     "```sh crapkit coverage crapkit ratchet prune crapkit ratchet seed ```"),
    ("CHANGELOG.md", "In each repo run",
     "run `crapkit coverage`, then `crapkit ratchet prune`, then `crapkit ratchet seed`"),
])
def test_the_upgrade_recipe_prunes_before_it_seeds(page, anchor, recipe):
    """Seed first refuses on a legacy-keyed file whose old-name marks the run lacks."""
    pinned = _after(page, "When a failed verify pins the baseline")

    assert recipe in _after(page, anchor)
    assert (pinned.index("`crapkit ratchet prune --baseline N`")
            < pinned.index("`crapkit ratchet seed --baseline N`")), pinned


def test_a_seed_that_adds_no_twin_mark_keeps_the_legacy_file_as_before(repo, capsys):
    """Twins under the ceiling get no mark, so the old format holds every mark seed writes."""
    run = write(repo, [twin(1)._replace(crap=2.0, ccn=1), twin(2)._replace(crap=2.0, ccn=1)])

    code, out, err = ratchet(repo, capsys, "seed")

    assert code == 0, err
    assert out.startswith(f"{MARKS}: added 0, tightened 0 - 1 mark(s) vs run {run} "), out
    assert (repo / MARKS).read_text(encoding="utf-8") == LEGACY
