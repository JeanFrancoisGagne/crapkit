"""The 16d7bfd guard: every function a calcs.tsv row names, in every packet, runs
a body line in production.

A test of a copy of a rule proves nothing about the rule: doctor's tests once
exercised a helper that production never called (16d7bfd). First the golden CLI
run over the small corpus runs under coverage.py (kit.reach.measured_lines).
It starts no hook, gate, ratchet, claim or mutation command and no tool under
tools/, so for each function it misses, the row's own independent test runs
alone under coverage.py, src/crapkit and tools/ measured down to subprocess
children and pool workers, at the push tier's example counts
(kit.reach.independent_lines). An independent test drives crapkit the way a
user does, so what it runs is production code. A function neither run reaches
fails its row, unless a rulings row pins that gap as an open defect: then the
row is a strict xfail until the gap closes, and a gap that moves fails.

Linux only: the answer does not depend on the OS, and the Windows nightly cell
already runs every other check of the tier.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from accuracy.kit import calcs, corpus_run, reach, rulings

ROWS = calcs.load()
NODE = ("tests/accuracy/suite_strength/test_calc_reach.py::"
        "test_each_calc_function_runs_on_the_golden_run_or_its_independent_test")
PINNED = {row.test: row for row in rulings.load().values() if row.test.startswith(NODE + "[")}


def _param(row: calcs.Calc):
    """The row, with the rulings row that pins its reach gap and that row's mark."""
    pinned = PINNED.get(f"{NODE}[{row.calc}]")
    marks = [rulings.applies(pinned.id)] if pinned and pinned.ruling == "defect" else []
    return pytest.param(row, pinned and pinned.id, id=row.calc, marks=marks)


@pytest.fixture(scope="session")
def golden_lines(tmp_path_factory):
    """The lines the golden CLI run executes, measured once per worker."""
    corpus = corpus_run.SMALL if corpus_run.SMALL.is_dir() else corpus_run.SEED
    return reach.measured_lines(corpus, tmp_path_factory.mktemp("golden"))


def missed(functions: tuple[str, ...], measured: dict, repo: Path = reach.REPO) -> list[str]:
    """The functions whose body ran no line in `measured`, missing ones included."""
    return [function for function in functions if reach.unreached([function], measured, repo)]


def unrun(problems: list[str]) -> str:
    """The functions `reach.unreached` named, as a rulings cell: comma-joined, or none."""
    return ",".join(sorted(problem.split(": ")[0] for problem in problems)) or "none"


def test_every_packet_names_calcs():
    assert {row.packet for row in ROWS} >= {"score_model", "verdict_model", "analysis_oracles",
                                            "coverage_oracles", "history_oracles",
                                            "corpus_goldens", "suite_strength"}


def test_every_pinned_gap_names_a_calc():
    assert sorted(set(PINNED) - {f"{NODE}[{row.calc}]" for row in ROWS}) == []


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.platform("linux")
@pytest.mark.parametrize(("row", "ruling"), [_param(row) for row in ROWS])
def test_each_calc_function_runs_on_the_golden_run_or_its_independent_test(
        row, ruling, golden_lines, tmp_path):
    left = missed(row.functions, golden_lines)
    measured = reach.independent_lines(row.independent_test, tmp_path) if left else {}
    problems = reach.unreached(left, measured)

    if ruling:
        rulings.pin_ruling(ruling, crapkit=unrun(problems), oracle="none")
    assert problems == [], f"{row.independent_test} leaves unrun:\n" + "\n".join(problems)


def test_a_function_whose_body_never_ran_is_named(tmp_path):
    """The rule itself, on a module whose one function the measurement never entered."""
    module = tmp_path / "m.py"
    module.write_bytes(b"def f(x):\n    return x + 1\n\n\ndef g():\n    return 2\n")
    measured = {module.resolve(): {1, 2, 5}}

    assert reach.unreached(["m.py:f"], {module.resolve(): {1}}, repo=tmp_path) == [
        "m.py:f: no line of its body ran"]
    assert reach.unreached(["m.py:f"], measured, repo=tmp_path) == []
    assert missed(("m.py:f", "m.py:g", "m.py:h"), {module.resolve(): {1, 2}}, tmp_path) == [
        "m.py:g", "m.py:h"]
    assert unrun(["b.py:g: no such function", "a.py:f: no line of its body ran"]) == "a.py:f,b.py:g"
    assert unrun([]) == "none"
