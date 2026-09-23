"""rescore's preview floors a one-line Python def, as the coverage run does.

A Python def with its body on its colon line has one line, and that line is also
the `def` statement, which runs when the module is imported. coverage.py counts
lines and arcs, not calls, so the coverage run scores such a def as uncovered
with remedy split-lines. The preview joined it by name to its baseline row
instead: a def collapsed onto one line after the run kept its old measured
number and passed, and one the run had already floored read add-tests, advice
no test can follow. rescore --gate and check_gate read the same preview.
"""
import subprocess
import sys

from crapkit.analyze import analyze_source
from crapkit.coverage_py import parse_coveragepy_both_file
from crapkit.score import ScoredRow, overlay_stale_coverage, score_rows
from crapkit.snapshot import InventoryRow, build_inventory_rows
from untraced_child import untraced_env

MOD = "src/mod.py"

# ccn 3 at cov 0 scores 3 * 3 * 1 + 3 = 12, over a ceiling of 6.
ONE_LINE = InventoryRow("src", MOD, "f( x , y )", 1, 1, 3, 3, 3, 1, 2, 0, 0, 1)


def view(rows) -> list[tuple]:
    return [(r.flag, r.cov, r.remedy) for r in rows]


def preview_of(rows, baseline, lane_scopes=frozenset({"src"})):
    return view(overlay_stale_coverage(rows, baseline, lane_scopes=set(lane_scopes), target=6))


def test_a_def_collapsed_onto_one_line_after_the_run_previews_as_uncovered():
    two_line = ScoredRow("src", MOD, "f( x , y )", 1, 2, 3, 3, 3, 2, 2, 0,
                         1.0, "measured", 3.0, "ok", 0, 1)

    assert preview_of([ONE_LINE], [two_line]) == [("untested", 0.0, "split-lines")]


def test_a_one_line_def_the_run_already_floored_previews_as_split_lines():
    floored = ScoredRow("src", MOD, "f( x , y )", 1, 1, 3, 3, 3, 1, 2, 0,
                        0.0, "untested", 12.0, "split-lines", 0, 1)

    assert preview_of([ONE_LINE], [floored]) == [("untested", 0.0, "split-lines")]


def test_a_one_line_typescript_function_keeps_its_baseline_number():
    row = ONE_LINE._replace(path="src/app.ts", long_name="f")
    measured = ScoredRow("src", "src/app.ts", "f", 1, 1, 3, 3, 3, 1, 0, 0,
                         1.0, "measured", 3.0, "ok", 0, 1)

    assert preview_of([row], [measured]) == [("measured", 1.0, "ok")]


def test_a_one_line_def_in_a_scope_with_no_lane_stays_no_lane():
    assert preview_of([ONE_LINE], [], lane_scopes=frozenset()) == [("no-lane", 0.0, "add-tests")]


def test_a_one_line_def_in_a_file_no_report_names_reads_split_lines_in_both():
    """A module no test imports is absent from the report. A test that imports
    it makes the def measured, and then it is floored: tests cannot clear it."""
    run = score_rows([ONE_LINE], {}, lane_scopes={"src"}, target=6)

    assert view(run) == preview_of([ONE_LINE], []) == [("untested", 0.0, "split-lines")]


TWO_LINE = "def f(x, y):\n    return 1 if x and y else 2\n"
COLLAPSED = "def f(x, y): return 1 if x and y else 2\n"
DRIVER = "import mod\nmod.f(1, 1)\nmod.f(0, 0)\n"


def _run(tmp_path, source: str) -> tuple[list, list]:
    """Rows scored against a real coverage.py report of `source`, imported and
    called by a driver the way a test suite calls it."""
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8")
    env = {**untraced_env(), "COVERAGE_FILE": str(tmp_path / ".coverage")}
    for command in (["run", "--branch", "driver.py"], ["json", "-o", "coverage.json"]):
        subprocess.run([sys.executable, "-m", "coverage", *command], cwd=tmp_path, env=env,
                       check=True, capture_output=True, text=True)
    functions, _, _ = parse_coveragepy_both_file(tmp_path / "coverage.json", path_prefix="")
    rows = build_inventory_rows({"src": analyze_source("mod.py", source)})
    return rows, score_rows(rows, functions, lane_scopes={"src"}, target=6)


def test_the_preview_of_a_collapsed_def_says_what_the_next_coverage_run_says(tmp_path):
    _, baseline = _run(tmp_path, TWO_LINE)
    collapsed_rows, next_run = _run(tmp_path, COLLAPSED)

    preview = overlay_stale_coverage(collapsed_rows, baseline, lane_scopes={"src"}, target=6)

    assert view(baseline) == [("measured", 1.0, "ok")]
    assert view(preview) == view(next_run) == [("untested", 0.0, "split-lines")]
