"""The 0.8.1 detail page describes the release and mutation tooling as the code runs it.

The last fixes of the 0.8.1 review changed contributor tooling that
docs/releases/0.8.1.md never mentioned: the deploy kit's source_hash, the
pins `release.py bump` moves, and the runs tools/accuracy/mutation.py refuses
or counts as covered. Each test here runs or reads the code a sentence
describes, and pins the sentence.
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import re
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))
sys.path.append(str(ROOT / "tools" / "release"))

import candidate  # noqa: E402
import release  # noqa: E402

NOTES = ROOT / "docs" / "releases" / "0.8.1.md"
_HEADING = re.compile(r"^(#{1,6}) ", re.M)


def _section(heading: str) -> str:
    """The notes' body under `heading`, its subsections included, joined as prose."""
    text = NOTES.read_text(encoding="utf-8")
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in _HEADING.finditer(text[start:]) if len(m[1]) <= level]
    return " ".join(text[start:start + ends[0] if ends else None].split())


def _prose(text: str | None) -> str:
    return " ".join((text or "").split())


def _load_mutation():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_notes",
                                                  ROOT / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- CI, tests and release tooling ------------------------------------------------------------

def test_the_deploy_kit_hashes_one_tree_alike_on_windows_and_linux(tmp_path):
    """candidate.py hashed the staged files in the order of a Path sort, which
    folds case on Windows alone, and reading release.py's SURFACES wrote
    __pycache__ into the staged tree. The files now go in str order of their
    POSIX name, and SURFACES is read without an import."""
    names = ["AGENTS.md", "action.yml", "kit-notes.md", "kit/entry.sh", "tools/release/release.py"]
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x\n")
    shutil.copyfile(ROOT / "tools" / "release" / "release.py", tmp_path / "tools" / "release" / "release.py")

    assert candidate.surfaces(tmp_path)
    assert [name for name, _ in candidate._files(tmp_path)] == sorted(names)
    tooling = _section("### CI, tests and release tooling")
    assert ("The deploy kit's `source_hash` orders the staged files by their POSIX path and "
            "writes no bytecode into the staged tree, so the release stage on Windows and the "
            "deploy workflow on Linux compute the same hash for one tree.") in tooling
    assert "Windows sorted the paths without regard to case" in tooling


def test_the_bump_moves_every_pin_and_a_unit_test_names_a_pin_it_misses():
    """SURFACES had no row for README's Route 4 pip line or the handbook's
    Action pin, so `release.py bump` left both at the old version."""
    rows = {(surface.path, surface.pattern) for surface in release.SURFACES}
    version = release.current_version(ROOT)
    guard = (ROOT / "tests" / "unit" / "test_release_surfaces_cover_every_pin.py").read_text(encoding="utf-8")

    assert {("README.md", "crapkit=={v}"), ("docs/handbook.html", release.REPO_SLUG + "@v{v}")} <= rows
    assert f'pip install "crapkit=={version}"' in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "def test_every_pin_of_the_current_version_has_a_surfaces_row" in guard
    tooling = _section("### CI, tests and release tooling")
    assert ("`release.py bump` moves README's `pip install \"crapkit==X\"` line and the "
            "handbook's Action pin") in tooling
    assert ("a unit test fails when a tracked file pins the current version where no "
            "`SURFACES` row reaches it") in tooling


# --- For contributors: the mutation bullet ----------------------------------------------------

def test_a_run_mutmut_ends_early_writes_no_receipt(tmp_path, monkeypatch):
    """A SIGHUP came back as -1, which died() read as the budget, and the serial
    rerun of the timeouts never had its exit code read."""
    mutation = _load_mutation()
    unfinished = mutation.Result("crapkit.score.x_crap__mutmut_1", "src/crapkit/score.py", "crap",
                                 mutation.TIMEOUT)
    monkeypatch.setattr(mutation, "_run_mutmut", lambda *args, **kwargs: 1)

    assert mutation.died(tmp_path, None) == ""
    assert "before it judged its mutants" in mutation.died(tmp_path, -1)
    with pytest.raises(mutation.RunDied, match="serial rerun"):
        mutation._rerun_timeouts(tmp_path, [unfinished])
    contributors = _section("## For contributors")
    assert "(a failed stats run, a crash, a signal, SIGHUP included) writes no receipt" in contributors
    assert ("A serial rerun of the timeouts that dies stops the run the same way, at exit 4 "
            "with no receipt") in contributors


def test_covered_takes_a_diff_receipt_only_for_a_judged_mutant_at_heads_text():
    mutation = _load_mutation()
    head = "a" * 40
    receipt = {"kind": "diff", "complete": True, "head": head,
               "functions": [["m.py", "f"], ["m.py", "g"]],
               "results": [{"name": "m.x_f__mutmut_1", "module": "m.py", "function": "f",
                            "status": "killed"}]}

    def same(commit, module, name):
        return f"def {name}():\n    return 1\n"

    def edited(commit, module, name):
        return same(commit, module, name) if commit == head else f"def {name}():\n    return 2\n"

    assert mutation.uncovered([("m.py", "f")], [receipt], same) == []
    assert mutation.uncovered([("m.py", "g")], [receipt], same) == [
        ("m.py:g", f"the diff run at {head[:12]} made no mutant of it")]
    assert mutation.uncovered([("m.py", "f")], [receipt], edited) == [
        ("m.py:f", f"changed again after the diff run at {head[:12]}")]
    assert ("mutmut 3.8 makes no mutant of a function decorated with anything but a lone "
            "staticmethod or classmethod") in _prose(mutation.uncovered.__doc__)
    assert "those that entered calc scope after `base`" in _prose(mutation.since.__doc__)
    contributors = _section("## For contributors")
    assert ("A function a calcs.tsv row brought into calc scope after the weekly run needs a "
            "diff run too.") in contributors
    assert ("counts a changed calculation function as covered only by a complete diff receipt "
            "that holds a judged mutant of it") in contributors
    assert ("mutmut 3.8 makes no mutant of a function decorated with anything but a lone "
            "`staticmethod` or `classmethod`") in contributors


# --- For contributors: the known gap in the mutation floors -----------------------------------

CHANGELOG = ROOT / "CHANGELOG.md"


def _rows(mutation, module: str, killed: int, counted: int) -> list:
    stem = Path(module).stem
    return [mutation.Result(f"crapkit.{stem}.x_f__mutmut_{n}", module, "f",
                            "killed" if n < killed else mutation.SURVIVED)
            for n in range(counted)]


def test_each_weekly_shard_judges_a_floor_over_the_modules_it_holds():
    """At f4def958 both floors hold over each whole group, while shard 6, whose one
    reader is lizardjava.py, reports the readers floor below: `weekly` judges the
    floors over the rows of its own shard."""
    mutation = _load_mutation()
    groups = mutation.read_table(mutation.TABLES / "floors.tsv", mutation.FLOOR_COLUMNS)
    java = _rows(mutation, "src/crapkit/lizardjava.py", 199, 240)
    readers = java + _rows(mutation, "src/crapkit/lizardpython.py", 5630 - 199, 6101 - 240)
    core = _rows(mutation, "src/crapkit/score.py", 3455, 3526)

    def judged(results: list, group: str):
        found = next(floor for floor in mutation.floors(results, [], groups) if floor.group == group)
        return found.rate, found.floor, found.ok

    assert judged(core, "core") == (97.99, 95.0, True)
    assert judged(readers, "readers") == (92.28, 85.0, True)
    assert judged(java, "readers") == (82.92, 85.0, False)
    assert "return _judge(rows, update=False)" in inspect.getsource(mutation._weekly)
    contributors = _section("## For contributors")
    assert ("passes both floors over each group as a whole: at f4def958 the calculation modules "
            "score 97.99% (3455 of 3526) against 95% and the lizard readers 92.28% (5630 of "
            "6101) against 85%.") in contributors
    assert ("Each weekly shard also judges a floor over only the modules it holds, so shard 6, "
            "whose one reader is lizardjava.py at 82.92% (199 of 240, the rate the full floor "
            "run measured for it), reports the readers floor below.") in contributors
    assert "0.9.0 judges each floor over its whole group" in contributors
    summary = " ".join(CHANGELOG.read_text(encoding="utf-8").split("\n## 0.8.0 ", 1)[0].split())
    assert ("The weekly mutation run passes both floors over each whole group: the calculation "
            "modules score 97.99% against 95% and the lizard readers 92.28% against 85%.") in summary
    assert ("Each shard also judges the floors over its own modules, so shard 6, whose one "
            "reader is lizardjava.py at 82.92%, reports the readers floor below.") in summary
