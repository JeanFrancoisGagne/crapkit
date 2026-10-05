"""From `crapkit coverage`, the steps crapkit prints reach a passing verify.

A user who reads no docs runs each command crapkit names as the next step: the
command after `-> next:`, or one in backticks after `run` or `then`. coverage
named worklist, and worklist named nothing, so that user stopped at the risk
map: no mark was signed and no verify ever passed. The chain here runs the
printed commands as printed, one lane that writes a coverage.py artifact, and
checks it ends at the verdict the README calls the first passing state.
"""
import re
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

MAKE_COV = '''import json

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": {}}, fh)
'''

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["calc"]
full_suite = false
"""

GRADE = """def classify(score, late):
    if late and score > 50:
        return "B"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "F"
"""

# The deploy kit's reading, with room for the `python -P -m crapkit` spelling that
# a process started without the console script prints.
PRINTED = re.compile(r'(?:\brun|\bthen) `([^`]+)`|-> next: ((?:(?:"[^"]+"|\S+) -P -m )?crapkit[^\n`]*)')
CRAPKIT = re.compile(r"(?:^|\s)crapkit\s+(.+)$")
LIMIT = 8

run_cli = cli_runner(timeout=180)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "calc").mkdir(parents=True)
    (root / "calc" / "grade.py").write_text(GRADE, encoding="utf-8", newline="\n")
    (root / "make_cov.py").write_text(MAKE_COV, encoding="utf-8", newline="\n")
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "calc")
    return root


def printed_commands(text: str) -> list[str]:
    """The crapkit subcommands a step prints as the next thing to run, in order."""
    steps = [(ran or arrow).strip() for ran, arrow in PRINTED.findall(text)]
    return [found.group(1) for found in map(CRAPKIT.search, steps) if found]


def step(repo: Path, command: str):
    """One printed command as the user pastes it; each must exit 0."""
    result = run_cli(repo, *command.split())
    assert result.returncode == 0, (command, result.stdout, result.stderr)
    return result


def not_yet_run(commands: list[str], ran: list) -> list[str]:
    done = {command for command, _ in ran}
    return [command for command in commands if command not in done]


def follow(repo: Path, first: str) -> list[tuple[str, object]]:
    """Run `first`, then every step the output prints that has not run yet.
    stderr first, the order a terminal shows a note printed before a summary."""
    queue, ran = [first], []
    while queue and len(ran) < LIMIT:
        command = queue.pop(0)
        result = step(repo, command)
        ran.append((command, result))
        queue += not_yet_run(printed_commands(result.stderr + result.stdout), ran)
    return ran


def test_the_printed_steps_from_coverage_end_at_a_passing_verify(repo: Path):
    ran = follow(repo, "coverage")

    assert [command for command, _ in ran] == ["coverage", "worklist", "ratchet seed", "verify"]
    assert ran[-1][1].stdout.startswith("verify OK"), ran[-1][1].stdout
    assert printed_commands(ran[-1][1].stdout) == []
