"""Diff coverage: the changed lines no test ran, against the ground truth and diff-cover.

A repo commits a base version of the Python and JS probes, scores it, then
commits the probes as recorded, so the recorded artifacts describe the head.
`crapkit verify --json` lists the changed lines no lane ran (`diff_uncovered`).

- Ground truth: the changed lines that hold a statement the driver never ran
  (ground_truth.tsv, the same hand set test_dark_lines checks), plus every line
  of the functions in a changed file no artifact mentions (README.md#exit-codes,
  exit 9; R62).
- diff-cover 10.6.0 on the same -U0 diff, over coverage.py's Cobertura report
  and istanbul's lcov (nightly): it agrees on every file an artifact measured,
  except a line holding a run and an unrun statement (ruling D6), and it skips a
  changed file no report names (ruling CO5).
- A Hypothesis model of the rule over drawn diffs, dark sets and scored rows:
  every reported line is a changed line, and nothing else is.
"""
import json
from pathlib import Path
import sys

from hypothesis import given, strategies as st
import pytest

import hang_guard
from accuracy.coverage_oracles import counts_table, ground_table, mini_repo, probe_repo, under_test
from accuracy.kit import drive, repos, rulings, tiers
from accuracy.kit.settings import pure

RECORDED = probe_repo.RECORDED
PY_REPORTS = RECORDED / "coveragepy-reports-7.16.1"
JS_RECORDING = RECORDED / "vitest-istanbul-5.0.1"
# (probe, the line edited in the base version). Each edit changes the line's
# text only, so the head's line numbers are the recorded ones.
EDITS = {
    "py/shapes.py": {7: 'return "YES"', 8: 'return "NO"', 28: "count -= 2", 29: "steps += 2",
                     54: "log.append(33)", 100: 'raise NotImplementedError("base")'},
    "js/shapes.js": {5: 'return "YES";', 7: 'return "NO";', 60: "const arrow = (value) => value * 3;",
                     11: 'return value > 0 ? "POS" : "neg";'},
}
# A file no artifact mentions: the head adds it, so every line is changed.
NEW = {"py/new.py": "def fresh(value):\n    if value:\n        return 1\n    return 0\n"}
SCENARIO = {"py/shapes.py": "call", "js/shapes.js": "idle"}
PRODUCER = {"py/shapes.py": "coveragepy-7.16.1", "js/shapes.js": "vitest-istanbul-5.0.1"}
DIFF = ("diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff")
VERIFY = under_test.crapkit("verify")


def _edited(path: str) -> str:
    """The probe with each EDITS line replaced, indentation kept."""
    lines = (probe_repo.PROBES / path).read_text(encoding="utf-8").split("\n")
    for number, text in EDITS[path].items():
        indent = lines[number - 1][:len(lines[number - 1]) - len(lines[number - 1].lstrip())]
        lines[number - 1] = indent + text
    return "\n".join(lines)


def _config() -> str:
    scopes = [mini_repo.scope("py", ["py"], ["python"]), mini_repo.scope("js", ["js"], ["javascript"])]
    lanes = [mini_repo.lane("py", "coveragepy", ["py"]), mini_repo.lane("js", "istanbul", ["js"])]
    return mini_repo.config(scopes, lanes)


def _head() -> dict:
    return {path: (probe_repo.PROBES / path).read_bytes() for path in EDITS} | NEW


def _base_tree() -> dict:
    return {"crapkit.toml": _config(), **{path: _edited(path) for path in EDITS},
            "recorded/py.json": (PY_REPORTS / "call.json").read_bytes(),
            "recorded/js.json": (JS_RECORDING / "idle.json").read_bytes()}


def _commit_head(top: Path) -> None:
    for path, data in _head().items():
        (top / path).write_bytes(data if isinstance(data, bytes) else data.encode())
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "head", date=repos.EPOCH + 60)


@pytest.fixture(scope="module")
def verified(tmp_path_factory):
    """(repo top, verify's JSON) after a baseline run on the base commit."""
    top = tmp_path_factory.mktemp("diffcov") / "repo"
    driver = mini_repo.build(top, _base_tree())
    assert driver.run("coverage").code == 0
    _commit_head(top)
    return top, driver.run("verify", "--json").json()


def _crapkit_lines(payload: dict) -> dict[str, set[int]]:
    assert payload["diff_uncovered_count"] == len(payload["diff_uncovered"])
    found: dict[str, set[int]] = {}
    for item in payload["diff_uncovered"]:
        found.setdefault(item["path"], set()).add(item["line"])
    return found


def hand_lines() -> dict[str, set[int]]:
    """Changed lines holding a statement the driver never ran, per the ground
    truth, and every line of the function in the file no artifact names."""
    found = {path: set(EDITS[path]) & ground_table.dark(PRODUCER[path], SCENARIO[path],
                                                          (path,))[path]
             for path in EDITS}
    return found | {"py/new.py": {1, 2, 3, 4}}


@pytest.mark.process
def test_verify_lists_the_changed_lines_the_ground_truth_leaves_unrun(verified):
    _, payload = verified

    assert _crapkit_lines(payload) == hand_lines()


@pytest.mark.process
def test_changed_file_absent_from_artifacts_counts(verified):
    """R62, README.md#exit-codes (exit 9): a changed file no lane artifact
    mentions counts every line of its functions."""
    _, payload = verified

    assert _crapkit_lines(payload)["py/new.py"] == {1, 2, 3, 4}


# --- diff-cover on the same diff -----------------------------------------------------------------

def _diff_cover(top: Path, report: Path) -> dict[str, set[int]]:
    tiers.require_process("diff-cover")
    base = repos.git(top, "rev-parse", "HEAD~1").strip()
    (top / "change.diff").write_text(repos.git(top, *DIFF, base), encoding="utf-8")
    argv = [sys.executable, "-m", "diff_cover.diff_cover_tool", str(report), "--diff-file",
            "change.diff", "--format", "json:diff-cover.json"]
    done = hang_guard.run(argv, cwd=top, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr
    stats = json.loads((top / "diff-cover.json").read_text(encoding="utf-8"))["src_stats"]
    return {path: set(entry["violation_lines"]) for path, entry in stats.items()}


def _mixed(path: str) -> set[int]:
    artifact = json.loads((JS_RECORDING / "idle.json").read_bytes())
    return counts_table.mixed_lines(artifact[path])


@pytest.mark.nightly
@pytest.mark.process
def test_diff_cover_agrees_on_the_coverage_py_lane(verified, oracle):
    oracle("diff-cover")
    top, payload = verified
    theirs = _diff_cover(top, PY_REPORTS / "call.xml")

    assert theirs.get("py/shapes.py", set()) == _crapkit_lines(payload)["py/shapes.py"]


@pytest.mark.nightly
@pytest.mark.process
def test_diff_cover_parts_from_the_istanbul_lane_only_on_mixed_lines(verified, oracle):
    """Ruling D6: istanbul's lcov marks a line run when any statement on it ran."""
    oracle("diff-cover")
    top, payload = verified
    theirs = _diff_cover(top, JS_RECORDING / "idle.lcov").get("js/shapes.js", set())
    ours = _crapkit_lines(payload)["js/shapes.js"]

    assert (ours - theirs, theirs - ours) == (set(EDITS["js/shapes.js"]) & _mixed("js/shapes.js"),
                                              set())


@pytest.mark.nightly
@pytest.mark.process
@rulings.applies("CO5")
def test_co5_diff_cover_skips_a_changed_file_no_report_names(verified, oracle):
    oracle("diff-cover")
    top, payload = verified
    theirs = _diff_cover(top, PY_REPORTS / "call.xml")

    rulings.pin_ruling("CO5", crapkit=len(_crapkit_lines(payload)["py/new.py"]),
                       oracle=len(theirs.get("py/new.py", set())))


# --- the rule, over drawn inputs ------------------------------------------------------------------

PATHS = ("a.py", "b.py", "c.ts")
FLAGS = st.sampled_from(["measured", "untested", "no-lane", "cc-only"])


@st.composite
def ranges(draw):
    """Disjoint, ascending hunks, as a -U0 diff gives them."""
    cuts = sorted(draw(st.sets(st.integers(1, 30), min_size=0, max_size=6)))
    return [(start, end) for start, end in zip(cuts[::2], cuts[1::2])]


@st.composite
def row(draw):
    start = draw(st.integers(1, 25))
    return (draw(st.sampled_from(PATHS)), start, start + draw(st.integers(0, 5)), draw(FLAGS))


def _scored(path: str, start: int, end: int, flag: str):
    return drive.to_crapkit("scored_row", ("s", path, "f", start, end, 1, 1, 1, 1, 0, 0, 0.0,
                                           flag, 2.0, "add-tests"))


def diff_model(changed: dict, missing: dict, rows: list) -> set[tuple[str, int]]:
    """README.md#exit-codes (exit 9) with the flag table: a changed line counts
    when an artifact names its file and the line as missing; a changed file no
    artifact names counts every line of its untested functions."""
    dark = _named(missing) | _silent(changed, missing, rows)
    return {(path, line) for path, line in _changed_lines(changed) if (path, line) in dark}


def _named(missing: dict) -> set[tuple[str, int]]:
    return {(path, line) for path, lines in missing.items() for line in lines}


def _silent(changed: dict, missing: dict, rows: list) -> set[tuple[str, int]]:
    """Every line of an untested function in a changed file no artifact names."""
    silent = set(changed) - set(missing)
    counted = [cells for cells in rows if (cells[0] in silent, cells[3]) == (True, "untested")]
    return {(path, line) for path, start, end, _ in counted for line in range(start, end + 1)}


def _changed_lines(changed: dict) -> list[tuple[str, int]]:
    return [(path, line) for path, hunks in changed.items() for start, end in hunks
            for line in range(start, end + 1)]


@given(st.dictionaries(st.sampled_from(PATHS), ranges(), max_size=3),
       st.dictionaries(st.sampled_from(PATHS), st.sets(st.integers(1, 30), max_size=8), max_size=3),
       st.lists(row(), max_size=6))
@pure
def test_diff_uncovered_follows_the_documented_rule(changed, missing, rows):
    found = VERIFY.diff_uncovered(changed, missing, [_scored(*cells) for cells in rows])

    assert len(found) == len(set(found))
    assert set(found) == diff_model(changed, missing, rows)
