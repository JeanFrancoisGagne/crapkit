"""Shared spans and the def-line floor: functions whose coverage no producer can tell apart.

README.md#remedy-what-to-do-about-it: a function on a line span another one
shares, or a Python def whose body starts on the line its signature ends (a
one-line def, a body on a multi-line signature's last line, a body that goes
on from the colon's line inside brackets or after a backslash), scores
uncovered, untested, and takes `split-lines` when its CRAP passes the ceiling.
README.md#command-reference (rescore): the preview scores them the same way.

- Python, under this interpreter's coverage.py: each layout twice, one copy
  called with both arms and one never called. coverage.py measures each copy as
  its arc model says: an idle def-line copy reads half its arms covered from the
  import alone, which is why no number there can be trusted. crapkit floors
  every such copy, a called one-line def included (ruling CO6), while the
  own-lines control keeps its measured number (R48).
- JS, a hand-written istanbul artifact: two arrows on one line, one called and
  one not, read 0 for both (R23); two arrows on another line whose CRAP at zero
  stays at the ceiling are floored too, and the run's note counts the measured
  shared spans and names the one that holds a function over its target.
- Every surface reads a floored function the same way; rescore's preview
  equals the run; splitting the definitions onto their own lines measures them.
"""
import json
from pathlib import Path
import shutil
import sys

import pytest

import hang_guard
from accuracy.coverage_oracles import counts_table, mini_repo
from accuracy.kit import drive, repos, rulings, surfaces, tiers

TARGET = 2
# (name, source). Every function has ccn 2, so at zero coverage its CRAP is
# 2^2 + 2 = 6, over the ceiling of 2 (README.md: split-lines needs crap > ceiling).
LAYOUTS = [
    ("own", "def {name}(x):\n    return 1 if x else 2\n"),
    ("one", "def {name}(x): return 1 if x else 2\n"),
    ("sig", "def {name}(x,\n        y): return 1 if x else y\n"),
    ("bracket", "def {name}(x): return (1 if x\n                        else 2)\n"),
    ("slash", "def {name}(x): return 1 if x \\\n    else 2\n"),
]
FLOORED = {"one", "sig", "bracket", "slash"}


def _python_source() -> str:
    blocks = [source.format(name=f"{name}_{twin}") for name, source in LAYOUTS
              for twin in ("called", "idle")]
    return "\n\n".join(blocks)


DRIVER = "import layouts\n\nfor value in (0, 1):\n" + "".join(
    f"    layouts.{name}_called(value, 2)\n" if name == "sig" else f"    layouts.{name}_called(value)\n"
    for name, _ in LAYOUTS)
RC = "[run]\nbranch = true\nrelative_files = true\ninclude = py/layouts.py\n"


def _coverage_py(work: Path) -> bytes:
    """This interpreter's coverage.py over the layouts, as a JSON report."""
    tiers.require_process("coverage.py")
    for args in (("run", "--rcfile=py/coveragerc", "py/drive.py"),
                 ("json", "--rcfile=py/coveragerc", "-o", "report.json")):
        done = hang_guard.run([sys.executable, "-m", "coverage", *args], cwd=work, text=True,
                              encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stdout + done.stderr
    return (work / "report.json").read_bytes()


# --- the JS shared spans, as an istanbul artifact ---------------------------------------------------

JS = ("const pair = [(x) => (x ? 1 : 2), (y) => (y ? 3 : 4)];\n"
      "function own(x) {\n  return x ? 1 : 2;\n}\n"
      "const quiet = [(p) => p, (q) => q];\n"
      "module.exports = { pair, own, quiet };\n")
# (name, line, first column, last column, calls, arm hits or None): the first arrow
# ran both arms, its sibling never ran; `quiet`'s two arrows have no branch.
FUNCTIONS = [
    ("(anonymous_0)", 1, 14, 31, 2, [1, 1]), ("(anonymous_1)", 1, 33, 50, 0, [0, 0]),
    ("own", 2, 0, 1, 2, [1, 1]),
    ("(anonymous_3)", 5, 15, 23, 1, None), ("(anonymous_4)", 5, 25, 33, 0, None),
]


def _position(line: int, column: int) -> dict:
    return {"line": line, "column": column}


def _span(line: int, first: int, last: int, end_line: int | None = None) -> dict:
    return {"start": _position(line, first), "end": _position(end_line or line, last)}


def _istanbul_entry(path: str) -> dict:
    """fnMap, f, statementMap, s, branchMap and b for FUNCTIONS: one statement
    per body, one cond-expr per branchy body, at the function's lines."""
    entry = {"path": path, "fnMap": {}, "f": {}, "statementMap": {}, "s": {}, "branchMap": {},
             "b": {}}
    for index, (name, line, first, last, calls, arms) in enumerate(FUNCTIONS):
        end_line = line + 2 if name == "own" else line
        key = str(index)
        entry["fnMap"][key] = {"name": name, "decl": _span(line, first, first + 1),
                               "loc": _span(line, first, last, end_line), "line": line}
        entry["f"][key] = calls
        body = line + 1 if name == "own" else line
        entry["statementMap"][key] = _span(body, first + 1, last - 1)
        entry["s"][key] = calls
        if arms is not None:
            entry["branchMap"][key] = {"type": "cond-expr", "line": body,
                                       "loc": _span(body, first + 1, last - 1),
                                       "locations": [_span(body, first + 2, first + 3)] * 2}
            entry["b"][key] = arms
    return entry


# --- one repo, scored once -----------------------------------------------------------------------

def _config() -> str:
    scopes = [mini_repo.scope("py", ["py"], ["python"]), mini_repo.scope("js", ["js"], ["javascript"])]
    lanes = [mini_repo.lane("py", "coveragepy", ["py"]), mini_repo.lane("js", "istanbul", ["js"])]
    return mini_repo.config(scopes, lanes, target=TARGET)


@pytest.fixture(scope="module")
def floored(tmp_path_factory):
    """(driver, coverage's stderr, scored rows by (path, name), the report)."""
    work = tmp_path_factory.mktemp("layouts")
    (work / "py").mkdir()
    for name, text in (("layouts.py", _python_source()), ("drive.py", DRIVER), ("coveragerc", RC)):
        (work / "py" / name).write_text(text, encoding="utf-8", newline="\n")
    report = _coverage_py(work)
    tree = {"crapkit.toml": _config(), "py/layouts.py": _python_source(), "js/shared.js": JS,
            "recorded/py.json": report,
            "recorded/js.json": json.dumps({"js/shared.js": _istanbul_entry("js/shared.js")})}
    driver = mini_repo.build(tmp_path_factory.mktemp("floor") / "repo", tree)
    result = driver.run("coverage", "--export", "scored.tsv")
    assert result.code == 0, result.stderr
    rows = surfaces.read_tsv((driver.root / "scored.tsv").read_text(encoding="utf-8"))[1]
    return driver, result.stderr, rows, json.loads(report)


def _by_path(rows: list[dict], path: str) -> list[dict]:
    return sorted((row for row in rows if row["path"] == path), key=lambda row: int(row["start"]))


def _python_rows(rows: list[dict]) -> dict[str, dict]:
    return {row["long_name"].split("(")[0].strip(): row for row in _by_path(rows, "py/layouts.py")}


def _verdict(row: dict) -> tuple:
    return float(row["cov"]), row["flag"], row["remedy"]


FLOOR = (0.0, "untested", "split-lines")


# --- Python: what coverage.py measures, and the floor ------------------------------------------

# (arms taken, arms, statements run, statements) for the called copy, then the
# idle copy, worked from coverage.py's arc model: a def statement runs at import
# and flows on to the next statement, and a call returns from the line its body
# is on, so a body on the def's own line gives that line two exits
# (https://coverage.readthedocs.io/en/7.16.1/branch.html#how-it-works). A body
# on a multi-line signature's last line is part of the def statement, which
# runs at module level, so the function's region holds nothing.
MEASURED = {
    "own": ((0, 0, 1, 1), (0, 0, 0, 1)),
    "one": ((2, 2, 1, 1), (1, 2, 1, 1)),
    "sig": ((0, 0, 0, 0), (0, 0, 0, 0)),
    "bracket": ((2, 2, 1, 1), (1, 2, 1, 1)),
    "slash": ((2, 2, 1, 1), (1, 2, 1, 1)),
}


def _measure(counts) -> tuple:
    return len(counts.arms_taken), len(counts.arms), len(counts.stmts_run), len(counts.stmts)


@pytest.mark.process
def test_coverage_py_measures_each_layout_as_its_arc_model_says(floored):
    """An idle def-line copy reads half its arms covered from the import alone,
    and a multi-line signature's body reads nothing called or not."""
    regions = {row.name: _measure(row) for row in counts_table.coveragepy_rows(floored[3])}

    assert {name: (regions[f"{name}_called"], regions[f"{name}_idle"]) for name, _ in LAYOUTS} == (
        MEASURED)


@rulings.applies("CO6")
@pytest.mark.process
def test_co6_a_called_one_line_def_floors_although_coverage_py_saw_the_call(floored):
    taken, arms, _, _ = MEASURED["one"][0]
    row = _python_rows(floored[2])["one_called"]

    rulings.pin_ruling("CO6", crapkit=float(row["cov"]), oracle=taken / arms)


@pytest.mark.process
def test_signature_layouts_floor_by_body_line(floored):
    """R48: every layout whose body starts on the signature's last line floors,
    called or not; the own-lines control keeps its measured number."""
    found = {name: _verdict(row) for name, row in _python_rows(floored[2]).items()}

    assert found == {**{f"{name}_{twin}": FLOOR for name in FLOORED for twin in ("called", "idle")},
                     "own_called": (1.0, "measured", "ok"), "own_idle": (0.0, "measured", "add-tests")}


# --- JS: two arrows on one line ------------------------------------------------------------------

@pytest.mark.process
def test_one_called_of_two_on_a_line_reads_zero_for_both(floored):
    """R23: the first arrow on line 1 ran both arms and its sibling never ran;
    no artifact can say which count is whose, so both read the floor."""
    rows = _by_path(floored[2], "js/shared.js")

    assert [(int(row["start"]), _verdict(row)) for row in rows] == [
        (1, FLOOR), (1, FLOOR), (2, (1.0, "measured", "ok")),
        (5, (0.0, "untested", "ok")), (5, (0.0, "untested", "ok"))]


def shared_spans(rows: list[dict]) -> dict[tuple, list[dict]]:
    """(path, start, end) -> the functions declaring it, where more than one does."""
    spans: dict[tuple, list[dict]] = {}
    for row in rows:
        spans.setdefault((row["path"], row["start"], row["end"]), []).append(row)
    return {span: members for span, members in spans.items() if len(members) > 1}


def _over_at_zero(members: list[dict]) -> bool:
    """README.md: CRAP at zero coverage is ccn^2 + ccn; its worst function fails
    the ceiling there."""
    worst = max(int(row["ccn"]) for row in members)
    return worst * worst + worst > TARGET


def _named(stderr: str) -> list[str]:
    """The span locations the note lists, one per indented line."""
    return [line.split()[1] for line in stderr.splitlines() if line.startswith("crapkit:   ")]


@pytest.mark.process
def test_the_run_note_counts_the_measured_shared_spans_and_names_the_over_ones(floored):
    _, stderr, rows, _ = floored
    spans = shared_spans([row for row in rows if row["path"] == "js/shared.js"])
    over = [f"{path}:{start}" for (path, start, _), members in spans.items()
            if _over_at_zero(members)]

    assert f"crapkit: {len(spans)} source line span(s) hold more than one function" in stderr
    assert f"; {len(over)} of them hold a function over its target:" in stderr
    assert _named(stderr) == over


# --- every surface, the preview and the split --------------------------------------------------

def _floored_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if _verdict(row) == FLOOR]


@pytest.mark.process
def test_rescore_previews_what_the_run_scored(floored):
    driver, _, rows, _ = floored
    preview = driver.json("rescore", "py/layouts.py", "js/shared.js")["functions"]

    assert sorted((item["path"], item["start"], item["cov"], item["flag"], item["remedy"])
                  for item in preview) == sorted(
        (row["path"], int(row["start"]), float(row["cov"]), row["flag"], row["remedy"])
        for row in rows)


@pytest.mark.process
def test_the_worklist_reads_every_floored_function_at_the_floor(floored):
    driver, _, rows, _ = floored
    payload = driver.json("worklist", "--top", "100")
    listed = {(item["path"], item["start"], item["function"]): item
              for item in payload["active"] + payload["dormant_top"]}
    floor = [(row["path"], int(row["start"]), row["long_name"]) for row in _floored_rows(rows)]

    assert floor and all((listed[key]["cov"], listed[key]["flag"], listed[key]["remedy"]) == FLOOR
                         for key in floor)


def _split_repo(tmp_path: Path, floored) -> Path:
    """The same repo with every floored layout on its own lines, measured
    again by this interpreter's coverage.py."""
    work = tmp_path / "split"
    (work / "py").mkdir(parents=True)
    source = "\n\n".join(f"def {name}_{twin}(x, y=2):\n    return 1 if x else y\n"
                         for name, _ in LAYOUTS for twin in ("called", "idle"))
    (work / "py" / "layouts.py").write_text(source, encoding="utf-8", newline="\n")
    (work / "py" / "drive.py").write_text(DRIVER, encoding="utf-8", newline="\n")
    (work / "py" / "coveragerc").write_text(RC, encoding="utf-8", newline="\n")
    return work


@pytest.mark.process
def test_splitting_the_definitions_measures_them(floored, tmp_path):
    """The split-lines remedy, carried out: each called copy reads covered and
    each idle copy uncovered, as the README says tests then can."""
    work = _split_repo(tmp_path, floored)
    report = _coverage_py(work)
    shutil.copytree(floored[0].root, tmp_path / "repo")
    driver = drive.Driver(tmp_path / "repo", date_now=repos.EPOCH + 86_400)
    shutil.copyfile(work / "py" / "layouts.py", driver.root / "py" / "layouts.py")
    (driver.root / "recorded" / "py.json").write_bytes(report)
    repos.git(driver.root, "commit", "-qam", "split", date=repos.EPOCH + 60)
    assert driver.run("coverage", "--export", "split.tsv").code == 0
    rows = surfaces.read_tsv((driver.root / "split.tsv").read_text(encoding="utf-8"))[1]

    assert {name: _verdict(row) for name, row in _python_rows(rows).items()} == {
        **{f"{name}_called": (1.0, "measured", "ok") for name, _ in LAYOUTS},
        **{f"{name}_idle": (0.0, "measured", "add-tests") for name, _ in LAYOUTS}}
