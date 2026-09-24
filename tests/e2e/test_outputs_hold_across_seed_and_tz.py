"""One repo prints the same bytes under any string hash seed and any time zone.

Python salts str hashing per process, and a set or dict built from strings
iterates in that salted order, so any output that walks one unsorted changes
from run to run. Time zones reach output two ways: a local-time timestamp, and
git reading a relative date ("12 months ago") on the local calendar. A hunt
over 46 outputs found two such leaks: the missing-file lines printed in hash
order, and the churn window's cutoff moved with the local calendar. This test
keeps the outputs agents read honest from here on. It builds the same repo and
runs coverage, worklist --json, brief --json, explain --history --json,
inventory --export and runs --json under four seeds and four zones, and wants
identical bytes once the run stamps crapkit takes from the clock are masked.

The clock is pinned (GIT_TEST_DATE_NOW) to 2028-03-01T03:00Z, where a
12-month window starts 2027-03-01T03:00Z on the UTC calendar. One commit sits
at 2027-03-01T12:00Z: inside that window, and outside the one git counted on
UTC-12's calendar. The rest of the history is dated at 23:30Z, so a date
printed in local time would land on another day in UTC+14 or UTC-12. Four
tracked files are deleted from the working tree, so coverage and inventory name
them. PYTHONHASHSEED is read only as the interpreter starts, so every call
spawns.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from conftest import cli_runner

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace", spawn=True)

NOW = "1835492400"  # 2028-03-01T03:00:00Z
# (PYTHONHASHSEED, TZ); None leaves TZ unset, the machine's own zone.
VARIATIONS = [("0", "UTC0"), ("1", "ABC-14"), ("2", "XYZ+12"), ("3", None)]

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = "python -c pass"
artifact = "coverage/unit.json"
parser = "istanbul"
scopes = ["src"]
"""

APP_TS = """export function route(n: number): number {
  if (n === 1) { return 10; }
  if (n === 2) { return 20; }
  if (n === 3) { return 30; }
  if (n === 4) { return 40; }
  if (n === 5) { return 50; }
  return 0;
}

export function plain(x: number): number {
  return x + 1;
}
"""

UTIL_TS = """export function grade(n: number): string {
  if (n > 90) { return "A"; }
  if (n > 80) { return "B"; }
  if (n > 70) { return "C"; }
  if (n > 60) { return "D"; }
  return "F";
}
"""

# Deleted from the working tree after the history is built, in this order.
GONE = ["src/gone_delta.ts", "src/gone_alpha.ts", "src/gone_charlie.ts", "src/gone_bravo.ts"]

# (author, commit date, {path: (old line, new line)}). Every edit lands inside a
# function, so `explain --history` lists it, and two authors share paths, so
# author sets, coupling pairs and per-file churn are all built from strings.
HISTORY = [
    ("alice", "2027-03-01T12:00:00+00:00", {"src/app.ts": ("return 0;", "return 1;"),
                                           "src/util.ts": ('return "F";', 'return "E";')}),
    ("bob", "2027-06-15T23:30:00+00:00", {"src/app.ts": ("return 1;", "return 2;")}),
    ("carol", "2027-12-20T23:30:00+00:00", {"src/app.ts": ("return 2;", "return 3;"),
                                           "src/util.ts": ('return "E";', 'return "G";')}),
    ("alice", "2028-02-20T23:30:00+00:00", {"src/util.ts": ('return "G";', 'return "H";')}),
]

# What crapkit stamps from the clock: run and report times.
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def _git(repo: Path, *args: str, date: str = "2026-11-30T23:30:00+00:00",
         author: str = "dana") -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com",
           "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@example.com",
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   cwd=repo, check=True, capture_output=True, env=env)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    _write(path, text.replace(old, new))


def _istanbul(repo: Path) -> None:
    """The unit lane's artifact: route half its branches, plain all of them."""
    source = str((repo / "src" / "app.ts").resolve())
    functions = {"route": (1, 8, [1, 0]), "plain": (10, 12, [1, 1])}
    fn_map, hits, branch_map, branches = {}, {}, {}, {}
    for i, (name, (start, end, taken)) in enumerate(functions.items()):
        fn_map[str(i)] = {"name": name, "decl": {"start": {"line": start}},
                          "loc": {"start": {"line": start}, "end": {"line": end}}}
        hits[str(i)] = 1
        branch_map[str(i)] = {"loc": {"start": {"line": start}},
                              "locations": [{"start": {"line": start}}] * 2}
        branches[str(i)] = taken
    payload = {source: {"path": source, "fnMap": fn_map, "f": hits, "branchMap": branch_map,
                        "b": branches, "statementMap": {}, "s": {}}}
    _write(repo / "coverage" / "unit.json", json.dumps(payload))


def _build(repo: Path) -> None:
    """The same commits, byte for byte and so SHA for SHA, on every call."""
    _write(repo / "crapkit.toml", TOML)
    _write(repo / ".gitignore", ".crapkit/\ncoverage/\n")
    _write(repo / "src" / "app.ts", APP_TS)
    _write(repo / "src" / "util.ts", UTIL_TS)
    for gone in GONE:
        _write(repo / gone, "export const x = 1;\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    for author, date, edits in HISTORY:
        for path, (old, new) in edits.items():
            _edit(repo / path, old, new)
        _git(repo, "commit", "-qam", f"{author} edits", date=date, author=author)
    for gone in GONE:
        (repo / gone).unlink()
    _istanbul(repo)


def _remove(path: Path) -> None:
    """rmtree that also clears git's read-only object files on Windows."""
    def writable(func, target, _info):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if path.exists():
        shutil.rmtree(path, onerror=writable)


def _outputs(repo: Path, seed: str, zone: str | None) -> dict[str, str]:
    """Every read an agent makes of this repo, as printed, under one seed and zone."""
    env = {"PYTHONHASHSEED": seed, "TZ": zone, "GIT_TEST_DATE_NOW": NOW}
    calls = {"coverage": ["coverage", "--reuse-artifacts"],
             "worklist": ["worklist", "--json"],
             "brief": ["brief", "src/app.ts", "route", "--json"],
             "explain": ["explain", "src/app.ts", "route", "--history", "--json"],
             "inventory": ["inventory", "--export", ".crapkit/inventory.tsv"],
             "runs": ["runs", "--json"]}
    printed = {}
    for name, argv in calls.items():
        done = run_cli(repo, *argv, env_extra=env)
        assert done.returncode == 0, (name, done.stdout, done.stderr)
        printed[name] = STAMP.sub("<stamp>", done.stdout)
        printed[f"{name} stderr"] = STAMP.sub("<stamp>", done.stderr)
    printed["inventory.tsv"] = (repo / ".crapkit" / "inventory.tsv").read_text(encoding="utf-8")
    return printed


def test_every_read_prints_the_same_bytes_under_any_seed_and_zone(tmp_path):
    repo = tmp_path / "repo"
    seen = {}
    for seed, zone in VARIATIONS:
        _remove(repo)
        _build(repo)
        seen[(seed, zone)] = _outputs(repo, seed, zone)

    first, *others = VARIATIONS
    differing = {f"PYTHONHASHSEED={seed} TZ={zone}": [name for name, text in seen[first].items()
                                                      if seen[(seed, zone)][name] != text]
                 for seed, zone in others}
    assert differing == {variation: [] for variation in differing}


def test_the_compared_bytes_hold_the_churn_history_and_skipped_files(tmp_path):
    """A mask or a fixture that emptied these would let the comparison above
    pass on anything."""
    repo = tmp_path / "repo"
    _build(repo)
    printed = _outputs(repo, "0", "UTC0")

    churn = json.loads(printed["brief"])["churn"]
    assert (churn["commits"], churn["authors"]) == (3, 3), "the edge commit is in the window"
    worklist = json.loads(printed["worklist"])
    assert sorted(row["function"].split(" ")[0] for row in worklist["active"]) == ["grade", "route"]
    (history,) = json.loads(printed["explain"])["functions"]
    assert [c["date"] for c in history["commits"]] == \
        ["2027-12-20", "2027-06-15", "2027-03-01", "2026-11-30"]
    skipped = [line.rpartition(" ")[2] for line in printed["inventory stderr"].splitlines()
               if "missing from working tree" in line]
    assert skipped == sorted(GONE)
