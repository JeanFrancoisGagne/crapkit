"""The 0.8.1 detail page describes the release and mutation tooling as the code runs it.

The last fixes of the 0.8.1 review changed contributor tooling that
docs/releases/0.8.1.md never mentioned: the deploy kit's source_hash, the
pins `release.py bump` moves, and the runs tools/accuracy/mutation.py refuses
or counts as covered. Each test here runs or reads the code a sentence
describes, and pins the sentence.
"""
from __future__ import annotations

import importlib.util
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
