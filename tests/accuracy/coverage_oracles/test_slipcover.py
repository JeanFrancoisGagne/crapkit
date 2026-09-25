"""slipcover 1.1.0, a second Python coverage tool, split into functions by the ast.

slipcover reports lines and branches per file, not per function. The test
splits them by each function's body as Python's ast gives it (the body's first
line to the function's last, nested functions cut out) and takes the README
ratio (branches, else statements, else called), with no crapkit involved. On
the same probe and the same calls, every function then reads what crapkit reads
off coverage.py's report of that scenario, except:

- a match statement: coverage.py counts each case pattern's two exits,
  slipcover one arm per case body from the match line (ruling D8: coverage.py
  is the Python producer a lane reads, docs/lanes.md#which-languages-a-lane-can-measure);
- the def-line layouts the README floors (a one-line def, a body on a
  multi-line signature's last line), which no score reads;
- the two functions the probe's coveragerc excludes, which slipcover has no
  way to read.

slipcover runs here under this interpreter, so the test is nightly.
"""
import ast
import json
import shutil
import sys
from fractions import Fraction

import pytest

import hang_guard
from accuracy.coverage_oracles import probe_repo, under_test
from accuracy.kit import rulings, tiers

pytestmark = [pytest.mark.nightly, pytest.mark.process]
COVERAGE_PY = under_test.crapkit("coverage_py")
PROBE = "py/shapes.py"
PARTED = {"match_case": "D8", "one_line": "floor", "body_on_signature": "floor",
          "excluded": "coveragerc", "exclude_also": "coveragerc"}


@pytest.fixture(scope="module")
def slipcover(tmp_path_factory, oracle):
    """{scenario: slipcover's JSON file member for the probe}."""
    oracle("slipcover")
    work = tmp_path_factory.mktemp("slipcover")
    shutil.copytree(probe_repo.PROBES / "py", work / "py")
    reports = {}
    for scenario in probe_repo.SCENARIOS:
        tiers.require_process("slipcover")
        argv = [sys.executable, "-m", "slipcover", "--branch", "--json", "--out",
                f"{scenario}.json", "--source", "py", "py/drive.py", scenario]
        done = hang_guard.run(argv, cwd=work, text=True, encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stdout + done.stderr
        files = json.loads((work / f"{scenario}.json").read_text(encoding="utf-8"))["files"]
        reports[scenario] = {key.replace("\\", "/"): value for key, value in files.items()}[PROBE]
    return reports


def _functions(tree: ast.AST, prefix: str = ""):
    """(qualified name, node) for every def, nested and method ones as a.b."""
    for node in ast.iter_child_nodes(tree):
        name = f"{prefix}{getattr(node, 'name', '')}"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield name, node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _functions(node, name + ".")


def body_lines() -> dict[str, set[int]]:
    """{function: its body's lines, nested functions' whole spans cut out}."""
    tree = ast.parse((probe_repo.PROBES / PROBE).read_bytes())
    found = dict(_functions(tree))
    spans = {name: set(range(node.body[0].lineno, node.end_lineno + 1)) for name, node in found.items()}
    return {name: lines - _nested(name, found) for name, lines in spans.items()}


def _nested(name: str, found: dict) -> set[int]:
    inner = [node for other, node in found.items() if other.startswith(name + ".")]
    return {line for node in inner for line in range(node.lineno, node.end_lineno + 1)}


def _from(pairs: list, lines: set[int]) -> list:
    return [pair for pair in pairs if pair[0] in lines]


def slipcover_ratio(member: dict, lines: set[int]) -> Fraction:
    """README.md#crapkit: branches in the body, else statements, else called
    (a body that ran a line was called)."""
    arms = _from(member["executed_branches"] + member["missing_branches"], lines)
    if arms:
        return Fraction(len(_from(member["executed_branches"], lines)), len(arms))
    statements = lines & set(member["executed_lines"] + member["missing_lines"])
    ran = lines & set(member["executed_lines"])
    return Fraction(len(ran), len(statements)) if statements else Fraction(0)


def _crapkit(scenario: str) -> dict[str, float]:
    report = probe_repo.RECORDED / "coveragepy-7.16.1" / f"{scenario}.json"
    return {fn.name: fn.coverage for fn in
            COVERAGE_PY.parse_coveragepy_both_file(report, path_prefix="")[0][PROBE]}


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_slipcover_split_by_the_ast_reads_what_crapkit_reads(slipcover, scenario):
    ours = _crapkit(scenario)
    bodies = body_lines()

    assert {name: float(slipcover_ratio(slipcover[scenario], lines))
            for name, lines in bodies.items() if name not in PARTED} == {
        name: ours[name] for name in bodies if name not in PARTED}


@rulings.applies("D8")
def test_d8_a_match_statement_reads_differently_under_slipcover(slipcover):
    """match_case("go") and match_case("halt"): coverage.py sees 3 of the case
    patterns' 4 exits taken, slipcover 2 of the 3 case bodies entered."""
    theirs = slipcover_ratio(slipcover["call"], body_lines()["match_case"])

    rulings.pin_ruling("D8", crapkit=round(_crapkit("call")["match_case"], 4),
                       oracle=round(float(theirs), 4))
