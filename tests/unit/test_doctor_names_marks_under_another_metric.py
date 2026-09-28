"""doctor names a marks file that verify refuses for its metric stamp.

Right after an upgrade that moves the analysis version, every mark carries the
older stamp and `crapkit verify` refuses them at exit 3 before any lane runs.
doctor said `doctor: no problems found` in that state, so the one command meant
to say what is wrong with a setup cleared the repo that verify refused. It now
prints the refusal verify would print, with the same remedy.

Marks an older metric stamped WARN, exit 0: the upgrade guide runs doctor
first, resolves its failures, then measures, reviews and re-seeds, so a FAIL
there stopped the guide at its first step and sent the user to re-seed before
the review. Marks a newer crapkit stamped FAIL: only an upgrade of this
install clears them.
"""
import json

import lizard
import pytest

from cli_inproc_repo import commit_all, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit import invocation
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version, stamp_text

MARKS = "crapkit-ratchet.tsv"


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The lines name `crapkit` when PATH resolves it to this interpreter's
    console script, as it does where crapkit is installed."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)


def _marks(root, stamp: str, *more: RatchetEntry) -> None:
    entry = RatchetEntry("src/app.ts", "dispatch( kind : string )", 90.0)
    (root / MARKS).write_text(dump_ratchet([entry, *more], stamp=stamp), encoding="utf-8")


def _doctor(root, capsys, kind: str = "problems") -> tuple[int, list[str]]:
    """doctor's exit and its `kind` lines ("problems" are FAILs, "warnings"
    WARNs) that name the marks file."""
    code = main(["doctor", "--json", "--repo", str(root)])
    payload = json.loads(capsys.readouterr().out)
    return code, [text for text in payload[kind] if MARKS in text]


def test_marks_an_older_analysis_stamped_warn_with_verifys_remedy_at_exit_0(repo, capsys):
    older = stamp_text(ANALYSIS_VERSION - 1, lizard.version)
    _marks(repo, older)

    code, warnings = _doctor(repo, capsys, "warnings")

    assert code == 0, "the upgrade guide's first step is doctor, and it must pass"
    assert warnings == [
        f"`crapkit verify` refuses {MARKS} at exit 3: ratchet marks were recorded under "
        f"[{older}] but this run measures [{metric_version()}] - CRAP scores are not comparable "
        "across metric versions; run `crapkit coverage`, then `crapkit ratchet prune`, then "
        "re-baseline with `crapkit ratchet seed`"]
    assert _doctor(repo, capsys)[1] == []


def test_the_guide_order_doctor_then_the_export_runs_on_marks_an_older_analysis_stamped(
        repo, capsys):
    """docs/upgrading.md, Measure before changing marks: `crapkit doctor`, then
    `crapkit coverage --export`, both before any mark changes."""
    _marks(repo, stamp_text(ANALYSIS_VERSION - 1, lizard.version))
    commit_all(repo, "marks an older crapkit stamped")
    seed_artifacts(repo)
    before = (repo / MARKS).read_bytes()

    assert main(["doctor", "--repo", str(repo)]) == 0
    assert main(["coverage", "--reuse-artifacts", "--export", ".crapkit/current-functions.tsv",
                 "--repo", str(repo)]) == 0
    assert (repo / MARKS).read_bytes() == before


def test_marks_a_newer_crapkit_stamped_fail_doctor_with_the_upgrade(repo, capsys):
    _marks(repo, stamp_text(ANALYSIS_VERSION + 1, lizard.version))

    code, problems = _doctor(repo, capsys)

    assert code == 1
    assert len(problems) == 1 and "the marks come from a newer crapkit" in problems[0], problems


@pytest.mark.parametrize("stamp", ["current", ""])
def test_marks_this_metric_stamped_or_unstamped_raise_no_problem(repo, capsys, stamp):
    _marks(repo, metric_version() if stamp == "current" else "")

    assert _doctor(repo, capsys)[1] == []
    assert _doctor(repo, capsys, "warnings")[1] == []


def test_no_marks_file_raises_no_problem(repo, capsys):
    assert _doctor(repo, capsys)[1] == []


def test_a_marks_path_doctor_cannot_read_is_a_problem_not_a_traceback(repo, capsys):
    (repo / MARKS).mkdir()

    code, problems = _doctor(repo, capsys)

    assert code == 1
    assert len(problems) == 1 and problems[0].startswith(f"cannot read ratchet {MARKS}"), problems


def test_the_remedy_doctor_names_clears_it_and_prune_drops_the_moved_key(repo, capsys):
    """coverage, prune, seed: after them verify passes and doctor has nothing to
    say about the marks. The second mark sits under a key the new metric no
    longer produces, as a UTF-16 source's or a cp1252-byte name's did; seed
    never drops a mark, so without the prune it would stay in the file."""
    moved = RatchetEntry("src/app.ts", "�( x )", 12.0)
    _marks(repo, stamp_text(ANALYSIS_VERSION - 1, lizard.version), moved)
    commit_all(repo, "marks an older crapkit stamped")
    seed_artifacts(repo)

    for step in (["coverage", "--reuse-artifacts"], ["ratchet", "prune"], ["ratchet", "seed"]):
        assert main([*step, "--repo", str(repo)]) == 0, step
    marks = (repo / MARKS).read_text(encoding="utf-8")
    capsys.readouterr()

    assert metric_version() in marks and moved.long_name not in marks, marks
    assert _doctor(repo, capsys) == (0, [])
    assert _doctor(repo, capsys, "warnings") == (0, [])
    assert main(["verify", "--reuse-artifacts", "--repo", str(repo)]) == 0
