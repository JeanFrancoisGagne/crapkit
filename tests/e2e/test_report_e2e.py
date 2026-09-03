"""`crapkit report` against a real consumer repo: real git, a real lane, a real
scored run, and the page that comes out of it.

The unit tests pin the renderer on a recorded payload. What only a live run can
show is the half in between: that collection reaches the store, that the page
lands where the flag says, that the path on stdout is the file that exists, and
that a lane going stale under an edit turns the banner on.
"""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

APP_TS = """export function plain(x: number): number {
  const a = x + 1;
  return a + 2;
}

export function pick(kind: string, mode: number): number {
  let total = 0;
  if (kind === "a") { total += 1; }
  if (kind === "b") { total += 2; }
  if (mode > 1) { total += 3; }
  if (mode > 2) { total += 4; }
  if (mode > 3) { total += 5; }
  if (mode > 4) { total += 6; }
  return total;
}
"""

LANE_SCRIPT = '''"""Stand-in for vitest --coverage: an istanbul artifact for src/app.ts."""
import json
import os

root = os.getcwd()
app = os.path.join(root, "src", "app.ts")
artifact = {
    app: {
        "path": app,
        "fnMap": {
            "0": {"name": "plain", "decl": {"start": {"line": 1}},
                  "loc": {"start": {"line": 1}, "end": {"line": 4}}},
            "1": {"name": "pick", "decl": {"start": {"line": 6}},
                  "loc": {"start": {"line": 6}, "end": {"line": 16}}},
        },
        "f": {"0": 2, "1": 1},
        "branchMap": {
            "0": {"loc": {"start": {"line": 8}},
                  "locations": [{"start": {"line": 8}}, {"start": {"line": 9}}]},
        },
        "b": {"0": [1, 0]},
        "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}},
        "s": {"0": 2},
    }
}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump(artifact, fh)
'''

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "istanbul"\nscopes = ["src"]\n'
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def scored_repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    write(root / "src" / "app.ts", APP_TS)
    write(root / "lane.py", LANE_SCRIPT)
    write(root / "crapkit.toml", TOML)
    write(root / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=root, check=True,
                   capture_output=True, env={**os.environ})
    done = run_cli(root, "coverage", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    return root


def test_report_writes_the_default_page_and_prints_where(scored_repo: Path):
    done = run_cli(scored_repo, "report")

    assert done.returncode == 0, done.stdout + done.stderr
    printed = Path(done.stdout.strip())
    assert printed == (scored_repo / ".crapkit" / "report.html").resolve()
    assert printed.is_file()


def test_the_written_page_carries_the_run_it_was_built_from(scored_repo: Path):
    run_cli(scored_repo, "report")
    page = (scored_repo / ".crapkit" / "report.html").read_text(encoding="utf-8")

    assert page.startswith("<!DOCTYPE html>")
    assert "pick" in page, "the over-target function is missing from the worklist"
    assert "crapkit explain src/app.ts" in page


def test_the_written_page_shows_each_rows_crap_and_coverage(scored_repo: Path):
    """The renderer's unit tests run on a recorded payload; only a live run
    shows the numbers reach the page. `pick` is ccn 7 with one of its two
    branches taken: 7^2 * (1 - 0.5)^3 + 7 = 13.125."""
    done = run_cli(scored_repo, "report", "--out", "page.html")

    assert done.returncode == 0, done.stdout + done.stderr
    page = (scored_repo / "page.html").read_text(encoding="utf-8")
    assert "<th>CRAP</th>" in page and "<th>Cov</th>" in page
    assert '<td class="mono">13.1</td><td class="mono">50%</td>' in page


def test_the_written_page_reaches_no_network(scored_repo: Path):
    run_cli(scored_repo, "report")
    page = (scored_repo / ".crapkit" / "report.html").read_text(encoding="utf-8")

    for needle in ("http://", "https://", "<script", "<img", "@import"):
        assert needle not in page, f"the written page is not self-contained: {needle!r}"


def test_out_puts_the_page_where_the_flag_says(scored_repo: Path):
    done = run_cli(scored_repo, "report", "--out", "build/debt.html")

    assert done.returncode == 0, done.stdout + done.stderr
    assert (scored_repo / "build" / "debt.html").is_file()


def test_an_edit_under_a_lane_turns_the_banner_on(scored_repo: Path):
    """The blackout defect, on the page. An uncommitted edit under the lane's
    scopes makes its artifact stale, which blacks out line-level coverage
    repo-wide. That is the state a tree is normally in when somebody wants a
    report."""
    before = run_cli(scored_repo, "report")
    assert 'class="banner fresh"' in Path(before.stdout.strip()).read_text(encoding="utf-8")

    write(scored_repo / "src" / "app.ts", APP_TS + "\nexport const extra = 1;\n")
    after = run_cli(scored_repo, "report")

    page = Path(after.stdout.strip()).read_text(encoding="utf-8")
    assert 'class="banner stale"' in page
    assert "1 of 1 lanes are stale" in page
    assert "crapkit coverage" in page


def test_an_inventory_only_repo_still_gets_a_page(tmp_path: Path):
    """`trend` lists trusted runs, and an inventory run is not one. The worklist
    ranks off it anyway, so the page has rows and an empty series."""
    root = tmp_path / "inventoried"
    write(root / "src" / "app.ts", APP_TS)
    write(root / "crapkit.toml", TOML)
    write(root / ".gitignore", ".crapkit/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=root, check=True,
                   capture_output=True, env={**os.environ})
    assert run_cli(root, "inventory").returncode == 0

    done = run_cli(root, "report")

    assert done.returncode == 0, done.stdout + done.stderr
    page = Path(done.stdout.strip()).read_text(encoding="utf-8")
    assert "no scored run yet" in page
    assert "pick" in page, "the inventory still ranks complexity"


def test_report_refuses_a_repo_with_no_store(tmp_path: Path):
    root = tmp_path / "bare"
    write(root / "crapkit.toml", TOML)

    done = run_cli(root, "report")

    assert done.returncode == 1
    assert "no snapshot" in done.stderr
