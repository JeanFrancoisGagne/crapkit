"""The mutation floors: which modules each floor covers, its rate, and the canary.

The floors come from the accuracy plan's mutation section, written out below
before any run: 95 percent after equivalents for score, digest, worklist,
ratchet, verify, keys and coverage_istanbul; 85 percent for the lizard
readers; 100 percent for tests/accuracy/kit/exact.py; 90 percent for the
tools under tools/accuracy. gate.py takes verify's 95 percent in a group of
its own: 0.9.0 moved verify's touch, ceiling and pardon rules there. A rate is
kills over mutants once proven equivalents are set aside, and a timeout is not
a kill.

The canary is score.crap. A weekly shard whose score.crap mutants do not all
die is void, so the killer suite has to kill a hand-made one: each mutant below
is one operator mutmut 3 applies to score.crap's two lines,
`uncovered = 1.0 - cov` and `ccn * ccn * (uncovered * uncovered * uncovered) + ccn`,
applied to a copy of src/, and the unit tests of score.py must fail on it.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

import hang_guard
from accuracy.kit import exact, rulings, source_tree

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TABLES = HERE / "mutation"
RECORDED = TABLES / "recorded"

# The plan's floors, as its mutation section states them.
PLAN_FLOORS = {
    "core": (95.0, {"src/crapkit/score.py", "src/crapkit/digest.py", "src/crapkit/worklist.py",
                    "src/crapkit/ratchet.py", "src/crapkit/verify.py", "src/crapkit/keys.py",
                    "src/crapkit/coverage_istanbul.py"}),
    "readers": (85.0, {"src/crapkit/lizard*.py"}),
    "kit-exact": (100.0, {"tests/accuracy/kit/exact.py"}),
    "accuracy-tools": (90.0, {"tools/accuracy/change_control.py", "tools/accuracy/wheel_diff.py",
                              "tools/accuracy/retro.py", "tools/accuracy/mutation.py",
                              "tools/accuracy/run.py"}),
    "gate": (95.0, {"src/crapkit/gate.py"}),
}
# Floor paths another packet's tools fill, each until its file is here. Empty since
# change_control.py and wheel_diff.py landed with their packets.
LANDING: dict[str, str] = {}
KILLED = {"killed", "caught by type check"}


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_floors_tool",
                                                  REPO / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load()


def _floors() -> list[dict]:
    return mutation.read_table(TABLES / "floors.tsv", mutation.FLOOR_COLUMNS)


def _patterns(row: dict) -> set[str]:
    return {part.strip() for part in row["paths"].split(",")}


def test_the_floors_are_the_plan_s():
    table = {row["group"]: (float(row["floor"]), _patterns(row)) for row in _floors()}

    assert table == PLAN_FLOORS
    assert all(row["source"].startswith("accuracy plan, mutation section") for row in _floors())


def _files(pattern: str) -> list[Path]:
    return [path for path in REPO.glob(pattern) if path.is_file()]


def _pattern_problem(pattern: str) -> str | None:
    found = _files(pattern)
    if found and pattern in LANDING:
        return f"{pattern} has landed: drop its LANDING entry"
    if not found and pattern not in LANDING:
        return f"{pattern} names no file"
    return None


def test_every_floor_pattern_names_a_file_or_one_a_packet_brings():
    problems = [_pattern_problem(pattern) for row in _floors() for pattern in _patterns(row)]

    assert [problem for problem in problems if problem] == []


def _floor_of_each_file() -> list[tuple[Path, str]]:
    return [(path, row["group"]) for row in _floors() for pattern in _patterns(row)
            for path in _files(pattern)]


def test_no_module_sits_under_two_floors():
    pairs = _floor_of_each_file()
    paths = [path for path, _ in pairs]

    assert sorted({path for path in paths if paths.count(path) > 1}) == []


def _mutated() -> set[str]:
    """Every file a mutation config mutates: pyproject.toml's paths_to_mutate for the
    weekly run and TOOL_TARGETS for the tools config."""
    with (REPO / "pyproject.toml").open("rb") as handle:
        weekly = tomllib.load(handle)["tool"]["mutmut"]["paths_to_mutate"]
    return set(weekly) | set(mutation.TOOL_TARGETS)


def test_every_file_under_a_floor_is_mutated():
    """A floor counts only the mutants of the files a config mutates. paths_to_mutate is
    the union of the calcs.tsv modules, so a reader no calcs.tsv row names is never
    mutated and the readers floor passes without it."""
    names = sorted({path.relative_to(REPO).as_posix() for path, _ in _floor_of_each_file()})

    assert [name for name in names if name not in _mutated()] == []


def test_kit_exact_has_no_grade_for_an_empty_target():
    """kit/exact.py sits under a 100 percent floor. Its own refusal cases all have
    more over the target than in it, so `total <= 0` weakened to `total < 0`
    lived there: grade(0, 0) then divides by zero instead of refusing."""
    with pytest.raises(ValueError, match="^no grade for 0 over target of 0$"):
        exact.grade(0, 0)


# --- recorded runs meet their floors ---------------------------------------------------------------

def _recorded() -> list[Path]:
    return sorted(RECORDED.glob("*.json")) if RECORDED.is_dir() else []


def _member(module: str, row: dict) -> bool:
    return any(fnmatch.fnmatchcase(module, pattern) for pattern in _patterns(row))


def _counted_rate(results: list[dict], row: dict) -> tuple[int, int]:
    """Kills over mutants in the group, counted from the receipt with json.load."""
    mine = [result for result in results if _member(result["module"], row)]
    return sum(result["status"] in KILLED for result in mine), len(mine)


def _group_rows(receipt: Path, equivalents: list[dict]) -> list[tuple]:
    """(receipt, group, the tool's kills and mutants, this file's, whether the floor
    holds) for each group the receipt mutated."""
    results = json.loads(receipt.read_text(encoding="utf-8"))["results"]
    tool = {floor.group: (floor.killed, floor.counted) for floor in mutation.floors(
        mutation.load_results([receipt]), equivalents, _floors())}
    counted = {row["group"]: (_counted_rate(results, row), float(row["floor"])) for row in _floors()}
    return [(receipt.stem, group, tool[group], mine, 100 * mine[0] >= floor * mine[1])
            for group, (mine, floor) in counted.items() if mine[1]]


# --- the canary ----------------------------------------------------------------------------------

ORIGINAL = ("uncovered = 1.0 - cov\n"
            "    return ccn * ccn * (uncovered * uncovered * uncovered) + ccn")
CANARIES = {
    "multiply-to-divide": ORIGINAL.replace("ccn * ccn", "ccn / ccn"),
    "minus-to-plus": ORIGINAL.replace("1.0 - cov", "1.0 + cov"),
    "one-to-two": ORIGINAL.replace("1.0 - cov", "2.0 - cov"),
    "plus-to-minus": ORIGINAL.replace(") + ccn", ") - ccn"),
}
# (1 - cov)^4 agrees with (1 - cov)^3 at cov 0 and 1, the only coverages
# test_score.py checks. Rulings row SS2 recorded it as a survivor until the
# score-model packet's grid joined the killer suite; the row is now fixed.
EXPONENT = ORIGINAL.replace("uncovered * uncovered * uncovered",
                            "uncovered * uncovered * uncovered * uncovered")
# The score tests of the killer suite: the unit file, and the score-model
# packet's accuracy tests once they are in the tree.
SCORE_TESTS = [target for target in ("tests/unit/test_score.py", "tests/accuracy/score_model")
               if (REPO / target).exists()]


def _tree(tmp_path: Path, line: str) -> Path:
    tree = tmp_path / "tree"
    shutil.copytree(REPO / "src", tree / "src")
    score = tree / "src" / "crapkit" / "score.py"
    text = score.read_text(encoding="utf-8")
    assert text.count(ORIGINAL) == 1, "score.crap no longer reads as the canary expects"
    score.write_text(text.replace(ORIGINAL, line), encoding="utf-8")
    return tree


def _suite(tree: Path) -> subprocess.CompletedProcess:
    """The score tests with the tree's src/ first, as the killer suite runs them."""
    env = {**mutation.killer_env(tree, dict(os.environ)), "PYTHONPATH": os.pathsep.join(
        [str(tree / "src"), str(REPO / "tests")])}
    probe = "import crapkit, sys; sys.stdout.write(crapkit.__file__)"
    where = hang_guard.run([sys.executable, "-c", probe], env=env, cwd=REPO, text=True)
    assert Path(where.stdout).resolve().is_relative_to(tree.resolve()), where.stdout
    argv = [sys.executable, "-m", "pytest", *SCORE_TESTS, "-m", mutation.INDEPENDENT_ONLY, "-q", "-x",
            "-p", "no:cacheprovider",
            "-p", "no:randomly", "-n", "0"]
    return hang_guard.run(argv, env=env, cwd=REPO, text=True, encoding="utf-8", errors="replace")


def canary_count() -> int:
    """How many times score.py holds score.crap's canary line, in the source tree
    kit.source_tree names (the calc mutation stage's checkout there)."""
    return (source_tree.root() / "score.py").read_text(encoding="utf-8").count(ORIGINAL)


def test_score_crap_reads_as_the_canary_expects():
    """The canaries run nightly; this check runs on every push, so an edit to
    score.crap that the canary texts no longer match fails before the nightly."""
    assert canary_count() == 1
    assert all(mutant != ORIGINAL for mutant in [*CANARIES.values(), EXPONENT])


def test_the_canary_line_is_read_from_the_tree_the_stage_names(tmp_path, monkeypatch):
    """mutmut's copy of score.py holds every mutant's body beside the original,
    so the calc stage names its checkout's src/crapkit and the check reads it."""
    (tmp_path / "score.py").write_text(ORIGINAL * 2, encoding="utf-8")
    monkeypatch.setenv(source_tree.ENV, str(tmp_path))

    assert canary_count() == 2


@pytest.mark.nightly
@pytest.mark.process
def test_the_unmutated_copy_passes_the_score_tests(tmp_path):
    done = _suite(_tree(tmp_path, ORIGINAL))

    assert done.returncode == 0, done.stdout[-2000:]


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("name", sorted(CANARIES))
def test_every_canary_mutant_of_score_crap_dies(tmp_path, name):
    done = _suite(_tree(tmp_path, CANARIES[name]))

    assert done.returncode == 1, f"{name} survived or broke the run:\n{done.stdout[-2000:]}"


def _verdict(done: subprocess.CompletedProcess) -> str:
    """pytest exit 1 is a failed test (a kill), 0 a pass (a survivor); anything
    else is a run that broke, which proves neither."""
    verdicts = {0: "survived", 1: "killed"}
    assert done.returncode in verdicts, f"the suite broke:\n{done.stdout[-2000:]}"
    return verdicts[done.returncode]


@rulings.applies("SS2")
@pytest.mark.nightly
@pytest.mark.process
def test_ss2_the_exponent_canary_dies(tmp_path):
    rulings.pin_ruling("SS2", crapkit=_verdict(_suite(_tree(tmp_path, EXPONENT))),
                       oracle="killed")
