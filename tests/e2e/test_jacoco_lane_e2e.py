"""A JaCoCo lane end to end, from a report Gradle 8.10.2 recorded.

The fixture under tests/fixtures/coverage/jacoco is a Java repo and the report
JaCoCo 0.8.12 wrote for it through init's gradle stanza (its README names the
command). Each case builds a git repo around the fixture, declares a lane with
parser = "jacoco" and path_prefix = "src/main/java" whose command copies the
report into place, and runs the CLI: coverage exits 0, worklist shows cov and
add-tests, and brief lists the lines with ci 0.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "jacoco"
SOURCES = ("build.gradle", "pom.xml", "src/main/java/Greeter.java",
           "src/main/java/com/acme/Shapes.java", "src/test/java/com/acme/ShapesTest.java")
SHAPES = "src/main/java/com/acme/Shapes.java"

# The lane's command: copy the report the test placed into the artifact path.
_COPY = ("from pathlib import Path\n"
         "out = Path('build/reports/jacoco/test')\n"
         "out.mkdir(parents=True, exist_ok=True)\n"
         "(out / 'jacocoTestReport.xml').write_bytes(Path('report.xml').read_bytes())\n")
# grade's ccn is 8: under a ceiling of 8 no decomposition is owed, so the
# uncovered method's remedy is add-tests.
CONFIG = ("[crapkit]\ntarget = 8\n\n"
          '[[scope]]\nname = "java"\npaths = ["src/main/java"]\nlanguages = ["java"]\n\n'
          '[exclude]\nglobs = ["make_report.py", "src/test/**"]\n\n'
          '[[lane]]\nname = "java-gradle"\ncommand = "python make_report.py"\n'
          'artifact = "build/reports/jacoco/test/jacocoTestReport.xml"\nparser = "jacoco"\n'
          'path_prefix = "src/main/java"\nscopes = ["java"]\n')


@pytest.fixture
def measured(tmp_path):
    """A repo whose JaCoCo lane ran over the recorded Gradle report."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    for rel in SOURCES:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes((FIXTURE / "java-repo" / rel).read_bytes())
    (repo / "report.xml").write_bytes((FIXTURE / "gradle-8.10.2.xml").read_bytes())
    files = {"crapkit.toml": CONFIG, "make_report.py": _COPY, ".gitignore": "build/\n.crapkit/\n"}
    for rel, body in files.items():
        (repo / rel).write_text(body, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "a Java repo and its JaCoCo lane")
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["lane_failures"] == {}
    return repo


def _worklist(repo: Path) -> dict:
    """The functions over the ceiling, by handle."""
    res = run_cli(repo, "worklist", "--json")
    assert res.returncode == 0, res.stderr
    return {row["handle"]: row for row in json.loads(res.stdout)["active"]}


def test_worklist_reads_cov_from_the_report_and_hands_add_tests(measured):
    rows = _worklist(measured)
    (grade,) = [row for handle, row in rows.items() if handle.endswith("grade")]

    assert grade["path"] == SHAPES
    assert (grade["cov"], grade["remedy"], grade["flag"]) == (0.0, "add-tests", "measured")


def test_brief_lists_the_lines_with_ci_zero(measured):
    res = run_cli(measured, "brief", SHAPES, "grade", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["uncovered_lines"] == [35, 36, 38, 39, 41, 42, 43, 44, 45, 46,
                                                         48]
