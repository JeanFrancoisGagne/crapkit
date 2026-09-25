"""`--reuse-unchanged` names a declared results file that is gone as missing.

The stamp keeps a digest of each file a lane declares. A file that is not there
has no digest, and the rerun reason used to read that absence as the digest ""
and print `out/junit.xml: bytes differ from its stamp`, the stored rerun_reason
too, for a file that no longer exists. The lane reran either way; only the
sentence was wrong.

The coverage artifact never reaches this check: a missing one is named first,
`no artifact at out/cov.json`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit.cli import main
from crapkit.config import Lane
from crapkit.lanes import lane_reuse_verdict
from crapkit.store import SnapshotStore

PY = sys.executable.replace("\\", "/")

# Writes the coverage artifact and a junit of 20 passing tests, and counts its runs.
MAKE = (
    "import json, os, pathlib\n"
    "root = os.getcwd()\n"
    'with open("runs.txt", "a", encoding="utf-8") as fh:\n'
    '    fh.write("run\\n")\n'
    'app = os.path.join(root, "src", "app.ts")\n'
    'data = {app: {"fnMap": {"0": {"name": "one", "decl": {"start": {"line": 1}},\n'
    '    "loc": {"start": {"line": 1}, "end": {"line": 3}}}}, "f": {"0": 1},\n'
    '    "statementMap": {}, "s": {}, "branchMap": {}, "b": {}}}\n'
    'out = pathlib.Path(root, "out")\n'
    "out.mkdir(exist_ok=True)\n"
    '(out / "cov.json").write_text(json.dumps(data), encoding="utf-8")\n'
    'cases = "".join(\'<testcase classname="t" name="c%d"/>\' % i for i in range(20))\n'
    '(out / "junit.xml").write_text(\'<testsuite name="t" tests="20">\' + cases + "</testsuite>",\n'
    '                               encoding="utf-8")\n'
)

TOML = f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = '"{PY}" make.py'
artifact = "out/cov.json"
results_artifact = "out/junit.xml"
parser = "istanbul"
scopes = ["src"]
"""

LANE = Lane(name="unit", command=f'"{PY}" make.py', artifact="out/cov.json", parser="istanbul",
            scopes=("src",), results_artifact="out/junit.xml")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=repo, capture_output=True, check=True)


def _measured(tmp_path: Path, capsys) -> Path:
    """A committed repo whose one lane ran once and stamped both of its files.

    Called from the test body, not a fixture: pytest rewrites PYTEST_CURRENT_TEST
    between a fixture and the test, and the stamp's proof covers the environment."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function one() {\n  return 1;\n}\n",
                                             encoding="utf-8")
    (tmp_path / "make.py").write_text(MAKE, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\nout/\nruns.txt\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    assert main(["coverage", "--repo", str(tmp_path)]) == 0
    capsys.readouterr()
    return tmp_path


def _delete(path: Path) -> None:
    path.unlink()


def _garble(path: Path) -> None:
    path.write_text("<testsuite><testcase", encoding="utf-8")


def _leave(_path: Path) -> None:
    pass


@pytest.mark.parametrize(("change", "reason", "runs"), [
    pytest.param(_delete, "out/junit.xml: missing", 2, id="missing"),
    pytest.param(_garble, "out/junit.xml: bytes differ from its stamp", 2, id="malformed"),
    pytest.param(_leave, "", 1, id="control"),
])
def test_the_rerun_reason_says_what_happened_to_the_junit(tmp_path, capsys, change, reason, runs):
    measured = _measured(tmp_path, capsys)
    change(measured / "out" / "junit.xml")

    code = main(["coverage", "--reuse-unchanged", "--json", "--repo", str(measured)])
    out = capsys.readouterr()

    lane = json.loads(out.out)["lanes"]["unit"]
    stored = SnapshotStore(measured / ".crapkit" / "crap.sqlite").list_runs()[-1]["lanes"]["unit"]
    assert code == 0, out.err
    assert (lane["rerun_reason"], stored["rerun_reason"]) == (reason, reason), out.err
    assert (f"rerunning: {reason}" in out.err) is bool(reason), out.err
    assert (measured / "runs.txt").read_text(encoding="utf-8").count("run") == runs
    assert stored["tests_total"] == 20, "the rerun counts the suite again; no stand-in 0"


def test_a_results_file_nothing_can_read_is_named_unreadable(tmp_path, capsys):
    measured = _measured(tmp_path, capsys)
    junit = measured / "out" / "junit.xml"
    junit.unlink()
    junit.mkdir()

    reason = lane_reuse_verdict(measured, LANE).reason

    assert reason.startswith("out/junit.xml: unreadable ("), reason


def test_a_missing_coverage_artifact_is_named_before_its_results_file(tmp_path, capsys):
    measured = _measured(tmp_path, capsys)
    (measured / "out" / "cov.json").unlink()
    (measured / "out" / "junit.xml").unlink()

    assert lane_reuse_verdict(measured, LANE).reason == "no artifact at out/cov.json"
