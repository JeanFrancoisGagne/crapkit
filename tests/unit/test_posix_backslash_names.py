r"""A tracked file whose name holds a backslash: every reader folds it.

git on Linux can track `src/pkg/we\ird.py` as one file. A coverage report, a
JUnit report and the Action's --changed list travel between OSes, and one
written on Windows says `src\pkg\mod.py` for src/pkg/mod.py. The text cannot
tell a separator from a name character, so each of those readers takes the
backslash as a separator on every OS and reads src/pkg/we/ird.py, a file git
does not hold. Such a name is unsupported: its functions find no coverage and
score untested, and `crapkit doctor` names the file so the gap is not silent.

Only a filesystem that allows `\` in a name can hold the file, so the tests
that need it run on POSIX, which the Linux CI jobs are.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hang_guard import HANG_SECONDS
from path_spellings import only_posix

from crapkit import coverage_istanbul, coverage_py
from crapkit.cli import main
from crapkit.cli.verifying import _test_files
from crapkit.config import Lane
from crapkit.junitparse import failed_test_ids
from crapkit.score import score_rows
from crapkit.snapshot import InventoryRow
from crapkit.verify import evaluate

ROOT = Path(__file__).resolve().parents[2]
TRACKED = "src/pkg/we\\ird.py"
FOLDED = "src/pkg/we/ird.py"

SOURCE = """def weird(flag):
    if flag:
        return 1
    return 0
"""

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -c pass"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["src"]
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
                    *args], cwd=root, check=True, capture_output=True, timeout=HANG_SECONDS)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A repo that tracks src/pkg/we\\ird.py, as git on Linux allows."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "we\\ird.py").write_text(SOURCE, encoding="utf-8")
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "a name holding a backslash")
    return root.resolve()


def _coveragepy_report(key: str) -> dict:
    return {"meta": {"branch_coverage": True, "version": "7.16.1"}, "files": {key: {
        "functions": {"weird": {
            "start_line": 1, "executed_lines": [1, 2, 3, 4], "missing_lines": [],
            "summary": {"covered_lines": 4, "num_statements": 4,
                        "num_branches": 2, "covered_branches": 2}}},
        "missing_lines": []}}}


def _istanbul_report(key: str) -> dict:
    return {key: {
        "path": key,
        "fnMap": {"0": {"name": "weird", "decl": {"start": {"line": 1}},
                        "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
        "f": {"0": 2},
        "branchMap": {"0": {"loc": {"start": {"line": 2}},
                            "locations": [{"start": {"line": 2}}, {"start": {"line": 3}}]}},
        "b": {"0": [1, 1]}}}


def _read(adapter, parser: str, root: Path, report: dict) -> dict:
    artifact = root / "cov.json"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    lane = Lane(name="py", command="x", artifact="cov.json", parser=parser, scopes=("src",))
    return adapter.read(lane, root, artifact)[0]


@only_posix
def test_a_coverage_py_key_reads_the_name_with_a_slash(tree):
    assert list(_read(coverage_py, "coveragepy", tree, _coveragepy_report(TRACKED))) == [FOLDED]


@only_posix
@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
def test_an_istanbul_key_reads_the_name_with_a_slash(tree, absolute):
    key = str(tree / "src" / "pkg" / "we\\ird.py") if absolute else TRACKED

    assert list(_read(coverage_istanbul, "istanbul", tree, _istanbul_report(key))) == [FOLDED]


@only_posix
def test_a_junit_classname_reads_the_name_with_a_slash(tree):
    """A failure in the file named by the classname is dirty when src/pkg/we/ird.py
    has edits, and not when git's own name for the tracked file does."""
    report = (f'<testsuite><testcase name="t" classname="{TRACKED}"><failure/></testcase>'
              "</testsuite>")

    failures = failed_test_ids(report)

    def dirty(paths: set[str]) -> list[str]:
        return evaluate(fresh=[], changed_ranges={}, ratchet=[], baseline_failures=set(),
                        fresh_failures=failures, target=6, dirty_paths=paths,
                        test_files=_test_files(tree, failures)).dirty_failures

    assert dirty({FOLDED}) == [f"{TRACKED}::t"]
    assert dirty({TRACKED}) == []


@only_posix
def test_the_tracked_name_finds_no_coverage_and_scores_untested(tree):
    """The join is on git's name, and the report's key no longer is it."""
    per_file = _read(coverage_py, "coveragepy", tree, _coveragepy_report(TRACKED))
    row = InventoryRow("src", TRACKED, "weird( flag )", 1, 4, 2, 2, 2, 4, 1, 1)

    (scored,) = score_rows([row], per_file, lane_scopes={"src"})

    assert scored.flag == "untested"


def _comment(tmp_path: Path, flag: str, changed: bytes, rows: dict[str, str]) -> str:
    """The Action's PR comment for a worklist of `rows` (path to function) and a
    changed list framed the way `flag` reads it."""
    listing = tmp_path / "changed"
    listing.write_bytes(changed)
    worklist = tmp_path / "worklist.json"
    worklist.write_text(json.dumps({"active": [
        {"path": path, "function": name, "ccn": 7, "risk": 7, "remedy": "decompose"}
        for path, name in rows.items()]}), encoding="utf-8")
    out = tmp_path / "comment.md"

    done = subprocess.run([sys.executable, str(ROOT / "tools" / "action" / "comment.py"),
                           flag, str(listing), "--worklist", str(worklist),
                           "--top", "10", "--out", str(out)],
                          capture_output=True, timeout=HANG_SECONDS)

    assert done.returncode == 0, done.stderr
    return out.read_text(encoding="utf-8")


def test_the_actions_changed_line_reads_the_name_with_a_slash(tmp_path):
    """The legacy line-framed --changed list folds too, on every OS. A worklist
    row keyed with git's backslash name drops out of the comment."""
    text = _comment(tmp_path, "--changed", (TRACKED + "\n").encode("utf-8"),
                    {FOLDED: "folded( )", TRACKED: "as_git_names_it( )"})

    assert "folded( )" in text and "as_git_names_it( )" not in text


CHANGED_NAMES = {
    "plain": "app/weird.py",
    "space": "src/sp ace/mod.py",
    "non-ascii": "src/\u00fcn\u00ef/mod.py",
    "padded": "src/ pad /mod.py",
}


@pytest.mark.parametrize("flag, frame", [("--changed", "\n"), ("--changed-z", "\0")],
                         ids=["line-framed", "nul-framed"])
@pytest.mark.parametrize("which", CHANGED_NAMES)
def test_the_actions_changed_list_selects_each_name_as_git_wrote_it(tmp_path, which, flag,
                                                                    frame):
    name = CHANGED_NAMES[which]
    text = _comment(tmp_path, flag, (name + frame).encode("utf-8"),
                    {name: "changed_row( )", "unrelated.py": "unrelated_row( )"})

    assert "changed_row( )" in text and "unrelated_row( )" not in text


def test_the_actions_nul_framed_list_keeps_a_backslash_name_exactly(tmp_path):
    """The route the Action takes: git's -z record holds the name as git has it,
    so a POSIX name holding a backslash still selects its row."""
    text = _comment(tmp_path, "--changed-z", (TRACKED + "\0").encode("utf-8"),
                    {TRACKED: "as_git_names_it( )", FOLDED: "folded( )"})

    assert "as_git_names_it( )" in text and "folded( )" not in text


def test_the_action_hands_the_comment_nul_framed_names():
    """The Action writes `git diff --name-only -z` and passes --changed-z, which
    keeps every name exactly, so the legacy --changed fold never reaches it.
    --changed-error carries git's error when the diff failed, not names."""
    steps = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    body = next(step["run"] for step in steps if step.get("name") == "build the comment")
    names = [flag for flag in re.findall(r"--changed[\w-]*", body) if flag != "--changed-error"]

    assert names == ["--changed-z"]


@only_posix
def test_doctor_names_the_tracked_name_as_unsupported(tree, capsys):
    main(["doctor", "--json", "--repo", str(tree)])
    warnings = json.loads(capsys.readouterr().out)["warnings"]

    assert any(TRACKED in line and "does not support" in line for line in warnings), warnings
