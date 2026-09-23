"""A one-line def measured by coverage.py scores as uncovered, with remedy split-lines.

A def whose body sits on its colon line has one line, and that line is also the
`def` statement, which runs when the module is imported. coverage.py counts
lines and arcs, not calls, so its region for such a function reads as run
whether a test called it or not: an uncalled `def one(x): return x` reads 1 of
2 branches covered. The join floors it the way it floors a function on a shared
span, because only moving the body off the def line lets coverage.py see a call.
istanbul keeps a call counter per function, so its one-line functions keep
their numbers.

The report here is a real one: coverage.py runs a driver that imports the module
and calls two of its three functions.
"""
import subprocess
import sys

from crapkit.analyze import analyze_source
from crapkit.coverage_istanbul import FnCoverage
from crapkit.coverage_py import parse_coveragepy_both_file
from crapkit.score import SharedSpanFold, score_rows
from crapkit.snapshot import build_inventory_rows
from untraced_child import untraced_env

MODULE = ("def one(x): return x\n"
          "def called(x): return x\n"
          "def two(x):\n"
          "    return x\n")
DRIVER = "import mod\nmod.called(1)\nmod.two(1)\n"

# At ceiling 1 an uncovered ccn-1 function scores 1 + 1 = 2 and fails it, so the
# remedy says which way out there is; a covered one scores 1 and passes.
CEILING = 1


def _coverage_py_report(tmp_path):
    (tmp_path / "mod.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8")
    env = {**untraced_env(), "COVERAGE_FILE": str(tmp_path / ".coverage")}
    for command in (["run", "--branch", "driver.py"], ["json", "-o", "coverage.json"]):
        subprocess.run([sys.executable, "-m", "coverage", *command], cwd=tmp_path, env=env,
                       check=True, capture_output=True, text=True)
    functions, _, _ = parse_coveragepy_both_file(tmp_path / "coverage.json", path_prefix="")
    return functions


def _scored(rows, functions, fold=None):
    return {r.long_name.split("(")[0].strip(): (r.flag, r.cov, r.remedy)
            for r in score_rows(rows, functions, lane_scopes={"src"}, target=CEILING,
                                shared_spans=fold)}


def test_a_one_line_def_reads_uncovered_whether_or_not_a_test_called_it(tmp_path):
    rows = build_inventory_rows({"src": analyze_source("mod.py", MODULE)})

    scored = _scored(rows, _coverage_py_report(tmp_path))

    assert scored == {"one": ("untested", 0.0, "split-lines"),
                      "called": ("untested", 0.0, "split-lines"),
                      "two": ("measured", 1.0, "ok")}


def test_a_one_line_def_is_not_named_as_a_span_two_functions_share(tmp_path):
    rows = build_inventory_rows({"src": analyze_source("mod.py", MODULE)})
    fold = SharedSpanFold()

    _scored(rows, _coverage_py_report(tmp_path), fold)

    assert fold.sites == []


def test_an_istanbul_one_line_function_keeps_its_measured_coverage():
    rows = build_inventory_rows({"src": analyze_source("app.ts", "function f() { return 1; }\n")})

    scored = _scored(rows, {"app.ts": [FnCoverage("f", 1, 1, True, 0, 0)]})

    assert scored == {"f": ("measured", 1.0, "ok")}
