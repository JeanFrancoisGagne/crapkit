"""One verify, one answer per git question — and the dirty set fixed up front.

A verify used to ask `git status` twice: once for attribution at the top of the
command, once inside the lane runner's own GitFacts. On this box that second
spawn cost 25-54ms of a ~320ms command, for a byte-identical verdict.

Sharing one GitFacts is only safe because of when the first answer is taken. A
lane command WRITES into the working tree, so a verdict that re-asked git after
the lanes would blame this run for files its own lanes touched. The second test
pins that: the finding below is in a file the lane dirties while it runs, and it
still has to count as committed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.cli import build_parser, cmd_verify

PY = sys.executable

CLEAN = "def alpha(n):\n    if n > 1:\n        n = n + 1\n    return n\n"
TANGLED = ("def tangled(n):\n"
           + "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
           + "    return n\n")

# The lane: writes a coveragepy artifact for both sources, and (when told to)
# appends a line to a TRACKED file, which is what a real lane does when it
# regenerates a snapshot or a lockfile mid-run.
MAKE_COV = '''import json
import sys

files = {}
for rel in ["src/app.py", "src/debt.py"]:
    with open(rel, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    starts = [i + 1 for i, ln in enumerate(lines) if ln.startswith("def ")]
    fns = {lines[s - 1][4:].split("(")[0]:
           {"start_line": s, "executed_lines": list(range(s, len(lines) + 1)),
            "missing_lines": [],
            "summary": {"covered_lines": 4, "num_statements": 4,
                        "num_branches": 2, "covered_branches": 2}}
           for s in starts}
    files[rel] = {"functions": fns, "missing_lines": []}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
if len(sys.argv) > 1 and sys.argv[1] == "--dirty":
    with open("src/debt.py", "a", encoding="utf-8") as fh:
        fh.write("# the lane wrote this while the verify was running\\n")
'''


def toml(lane_args: str = "") -> str:
    return (
        '[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
        f'[[lane]]\nname = "py"\ncommand = "{PY.replace(chr(92), "/")} make_cov.py{lane_args}"\n'
        'artifact = "cov.json"\nparser = "coveragepy"\nscopes = ["src"]\nfull_suite = false\n'
    )


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "crapkit", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", timeout=300)


def base_repo(tmp_path: Path, lane_args: str = "") -> Path:
    repo = tmp_path / "repo"
    write(repo / "src" / "app.py", CLEAN)
    write(repo / "src" / "debt.py", CLEAN.replace("alpha", "beta"))
    write(repo / "make_cov.py", MAKE_COV)
    write(repo / "crapkit.toml", toml(lane_args))
    write(repo / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    commit(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    return repo


def verify_args(repo: Path, *extra: str):
    return build_parser().parse_args(["verify", "--repo", str(repo), "--json", *extra])


@pytest.fixture()
def counted_status(monkeypatch) -> list:
    """Every `git status` the command runs, still answered by real git."""
    calls: list = []
    real = gitio.status_names

    def counted(root):
        calls.append(root)
        return real(root)

    monkeypatch.setattr(gitio, "status_names", counted)
    return calls


def test_one_git_status_serves_the_whole_verify(tmp_path, counted_status, capsys):
    repo = base_repo(tmp_path)

    assert cmd_verify(verify_args(repo, "--reuse-artifacts")) == 0
    capsys.readouterr()

    assert len(counted_status) == 1, "attribution and the lane runner share one answer"


def test_the_dirty_set_is_the_one_from_before_the_lanes_ran(tmp_path, capsys):
    repo = base_repo(tmp_path, lane_args=" --dirty")
    write(repo / "src" / "debt.py", TANGLED)
    commit(repo, "committed debt")

    assert cmd_verify(verify_args(repo)) == 6
    payload = json.loads(capsys.readouterr().out)

    assert [(v["path"], v["ccn"]) for v in payload["gate_violations"]] == [("src/debt.py", 8)]
    assert payload["committed_findings"] == 1
    assert payload["dirty_findings"] == 0, "the lane dirtied that file AFTER the run started"
    assert git(repo, "status", "--porcelain", "--untracked-files=no"), "the lane really did edit it"
