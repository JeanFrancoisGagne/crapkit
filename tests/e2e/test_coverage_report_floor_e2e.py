"""End-to-end: a coverage.py report is read from the start_line coverage 7.13.1 writes.

coverage 7.6 to 7.13.0 write function regions without start_line, and no line
inside a region is its def line. Read from the body, a nested function that
never ran joined its encloser and scored as half covered. The lane now fails at
exit 5 naming the artifact, the file, the first such function and the coverage
to install, and the extras install that coverage.

Real git, real CLI. One lane runs pytest-cov under the coverage this suite has
installed, which the dev extra pins at 7.13.1 or newer; the other copies that
run's report with start_line taken out, the shape 7.12.0 writes.
"""
import json
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit.score import parse_scored_tsv

run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace")

NESTED = ("def outer(x):\n    def inner(y):\n        if y > 0:\n            return y + 1\n"
          "        return y - 1\n    if x:\n        return inner(x)\n    return 0\n")
TEST = "from pkg.nested import outer\n\n\ndef test_outer():\n    assert outer(0) == 0\n"
STRIP = ("import json\n"
         "report = json.load(open('cov.json', encoding='utf-8'))\n"
         "for data in report['files'].values():\n"
         "    for region in (data.get('functions') or {}).values():\n"
         "        region.pop('start_line', None)\n"
         "json.dump(report, open('cov.json', 'w', encoding='utf-8'))\n")
PYTEST = ("python -m pytest tests -n 0 -p no:cacheprovider -p no:randomly --cov=pkg "
          "--cov-branch --cov-report=json:cov.json -q")

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[[lane]]
name = "py"
command = "{command}"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["pkg"]
full_suite = false
env = {{ COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }}
"""


def _repo(tmp_path: Path, command: str) -> Path:
    repo = tmp_path / "repo"
    files = {"pkg/__init__.py": "", "pkg/nested.py": NESTED, "tests/test_nested.py": TEST,
             "strip_start_line.py": STRIP, "crapkit.toml": CONFIG.format(command=command),
             ".gitignore": ".crapkit/\ncov.json\n.coverage\n__pycache__/\n"}
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    return repo


def test_the_installed_coverage_writes_start_line_and_the_nested_function_scores_its_own(
        tmp_path):
    repo = _repo(tmp_path, PYTEST)

    res = run_cli(repo, "coverage", "--export", "scored.tsv")

    assert res.returncode == 0, res.stderr
    report = json.loads((repo / "cov.json").read_text(encoding="utf-8"))
    (regions,) = [data["functions"] for key, data in report["files"].items()
                  if key.replace("\\", "/") == "pkg/nested.py"]
    assert {name: type(region.get("start_line")) for name, region in regions.items()} == {
        "": int, "outer": int, "outer.inner": int}, report["meta"]["version"]
    scored = parse_scored_tsv((repo / "scored.tsv").read_text(encoding="utf-8"))
    assert {row.long_name: row.cov for row in scored} == {"outer( x )": 0.5,
                                                          "outer.inner( y )": 0.0}


def test_a_report_without_start_line_fails_the_lane_naming_the_coverage_to_install(tmp_path):
    repo = _repo(tmp_path, PYTEST + " && python strip_start_line.py")

    res = run_cli(repo, "coverage")

    assert res.returncode == 5, res.stderr
    (failed,) = [line for line in res.stderr.splitlines() if "FAILED" in line]
    assert failed.startswith("crapkit: lane 'py' FAILED: unparseable coverage.py report "), failed
    assert failed.endswith(
        "cov.json: pkg/nested.py: outer: no start_line; coverage.py writes it on every "
        "function from 7.13.1, so install coverage>=7.13.1 and rerun the lane"), failed


@pytest.mark.parametrize("start_line", [None, "1"], ids=["null", "a-string"])
def test_a_start_line_that_is_not_a_line_number_fails_the_lane(tmp_path, start_line):
    fill = ("import json\n"
            "report = json.load(open('cov.json', encoding='utf-8'))\n"
            "for data in report['files'].values():\n"
            "    for region in (data.get('functions') or {}).values():\n"
            f"        region['start_line'] = {start_line!r}\n"
            "json.dump(report, open('cov.json', 'w', encoding='utf-8'))\n")
    repo = _repo(tmp_path, PYTEST + " && python fill_start_line.py")
    (repo / "fill_start_line.py").write_text(fill, encoding="utf-8", newline="\n")
    git_commit_all(repo, "fill")

    res = run_cli(repo, "coverage")

    assert res.returncode == 5, res.stderr
    assert "pkg/nested.py: outer: " in res.stderr and "start_line" in res.stderr, res.stderr
