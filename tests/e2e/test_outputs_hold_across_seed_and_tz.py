"""One repo prints the same bytes under any string hash seed and any time zone.

Python salts str hashing per process, and a set or dict built from strings
iterates in that salted order, so any output that walks one unsorted changes
from run to run. Time zones reach output two ways: a local-time timestamp, and
git reading a relative date ("12 months ago") on the local calendar. A hunt
over 46 outputs found two such leaks: the missing-file lines printed in hash
order, and the churn window's cutoff moved with the local calendar. This test
keeps the outputs agents read honest from here on. It builds the same repo and
runs coverage, worklist --json, brief --json, explain --history --json,
inventory --export, runs --json and report under five seeds and five zones,
and wants identical bytes once the stamps crapkit takes from the clock are
masked. Those stamps must still be UTC and read the real clock.

The inventory rows themselves come from lizard's tokenizers and crapkit's own
readers, which could move with the interpreter or its locale. The last test
pins them for a tree of syntax whose tokenizing changed between CPython
releases, so every Python the CI matrix runs, and every hash seed and locale
here, has to print the same rows.

The window's clock is pinned (GIT_TEST_DATE_NOW) to 2028-03-01T03:00Z, where a
12-month window starts 2027-03-01T03:00Z on the UTC calendar. One commit sits
at 2027-03-01T12:00Z: inside that window, and outside the one git counted on
UTC-12's calendar. The rest of the history is dated at 23:30Z or 00:30Z, so a
date printed in local time would land on another day in UTC+14 or UTC-12. Four
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import cli_runner

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace", spawn=True)

NOW = "1835492400"  # 2028-03-01T03:00:00Z
# (PYTHONHASHSEED, TZ); None leaves TZ unset, the machine's own zone. The POSIX
# spellings reach git and the C runtime on every OS; Git for Windows reads the
# IANA name as UTC, so that row bites on Linux.
VARIATIONS = [("0", "UTC0"), ("1", "ABC-14"), ("2", "XYZ+12"), ("3", None),
              ("4", "America/Adak")]

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
    ("bob", "2027-06-15T00:30:00+00:00", {"src/app.ts": ("return 1;", "return 2;")}),
    ("carol", "2027-12-20T23:30:00+00:00", {"src/app.ts": ("return 2;", "return 3;"),
                                           "src/util.ts": ('return "E";', 'return "G";')}),
    ("alice", "2028-02-20T23:30:00+00:00", {"src/util.ts": ('return "G";', 'return "H";')}),
]

# What crapkit stamps from the clock: run times. The zone suffix stays unmasked.
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?")


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


def _outputs(repo: Path, seed: str, zone: str | None) -> tuple[dict[str, str], list[str]]:
    """Every read an agent makes of this repo, as printed with its run stamps
    masked, under one seed and zone; and the stamps themselves."""
    env = {"PYTHONHASHSEED": seed, "TZ": zone, "GIT_TEST_DATE_NOW": NOW}
    calls = {"coverage": ["coverage", "--reuse-artifacts"],
             "worklist": ["worklist", "--json"],
             "brief": ["brief", "src/app.ts", "route", "--json"],
             "explain": ["explain", "src/app.ts", "route", "--history", "--json"],
             "inventory": ["inventory", "--export", ".crapkit/inventory.tsv"],
             "runs": ["runs", "--json"],
             "report": ["report"]}
    printed, stamps = {}, []
    for name, argv in calls.items():
        done = run_cli(repo, *argv, env_extra=env)
        assert done.returncode == 0, (name, done.stdout, done.stderr)
        stamps += STAMP.findall(done.stdout + done.stderr)
        printed[name] = STAMP.sub("<stamp>", done.stdout)
        printed[f"{name} stderr"] = STAMP.sub("<stamp>", done.stderr)
    printed["inventory.tsv"] = (repo / ".crapkit" / "inventory.tsv").read_text(encoding="utf-8")
    page = (repo / ".crapkit" / "report.html").read_text(encoding="utf-8")
    stamps += STAMP.findall(page)
    printed["report.html"] = STAMP.sub("<stamp>", page)
    return printed, stamps


def _off_the_utc_clock(stamps: list[str]) -> list[str]:
    """The stamps more than 15 minutes from UTC now. The masked bytes keep each
    stamp's zone suffix; this catches a local time written without one, which
    sits hours away in UTC+14 or UTC-12."""
    now = datetime.now(timezone.utc)
    return [stamp for stamp in stamps
            if abs(datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc) - now)
            > timedelta(minutes=15)]


def _label(seed: str, zone: str | None) -> str:
    return f"PYTHONHASHSEED={seed} TZ={zone}"


def _names_that_differ(base: dict[str, str], other: dict[str, str]) -> list[str]:
    """The outputs `other` printed otherwise than `base`."""
    return [name for name, text in base.items() if other[name] != text]


def test_every_read_prints_the_same_bytes_under_any_seed_and_zone(tmp_path):
    repo = tmp_path / "repo"
    seen, off_clock = {}, {}
    for seed, zone in VARIATIONS:
        _remove(repo)
        _build(repo)
        seen[_label(seed, zone)], stamps = _outputs(repo, seed, zone)
        off_clock[_label(seed, zone)] = _off_the_utc_clock(stamps)

    base = seen[_label(*VARIATIONS[0])]
    differing = {label: _names_that_differ(base, printed) for label, printed in seen.items()}
    assert differing == {label: [] for label in seen}
    assert off_clock == {label: [] for label in seen}


def test_the_compared_bytes_hold_the_churn_history_and_skipped_files(tmp_path):
    """A mask or a fixture that emptied these would let the comparison above
    pass on anything."""
    repo = tmp_path / "repo"
    _build(repo)
    printed, stamps = _outputs(repo, "0", "UTC0")

    assert printed["runs"].count('"created_at": "<stamp>Z"') == 2, printed["runs"]
    assert "generated <stamp>Z" in printed["report.html"]
    assert len(stamps) >= 4, "the two runs, the run explain's history lists, the report"

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


# --- inventory rows: the same on every interpreter, hash seed and locale -------

# Syntax whose tokenizing moved between CPython releases: nested f-strings with
# a backslash inside (PEP 701, 3.12), type parameters (PEP 695, 3.12), match,
# the walrus, an async comprehension over a backslash continuation, a lambda
# under a decorator, a non-ASCII name, a tab-indented body and CRLF line ends.
PY_SOURCES = {
    "pkg/fstr.py": r"""def fmt(items, width):
    out = []
    for i in items:
        if i:
            out.append(f"{f'{i!r:>{width}}'}-{'x' if i > 2 else 'y'}")
        elif i is None:
            out.append(f"{'\n'.join(str(j) for j in items)}")
    return out
""",
    "pkg/generic.py": """def first[T](xs: list[T], d: T) -> T:
    for x in xs:
        if x:
            return x
    return d


class Box[T]:
    def get(self, k: T) -> T | None:
        if k:
            return k
        return None
""",
    "pkg/matcher.py": """def route(cmd):
    match cmd:
        case ["go", d] if d in "nsew":
            return d
        case {"x": x, **rest} if (n := len(rest)) > 1:
            return n
        case str() | bytes():
            return 0
        case _:
            return None
""",
    "pkg/asyncs.py": r"""import functools


@functools.cache
def deco(f):
    return lambda *a: f(*a) if a else (lambda: None)


async def gather(src):
    total = [x async for x in src if x] \
        or [y for y in range(3) if y % 2]
    if not total:
        return 0
    return sum(total)
""",
    "pkg/unicode_ident.py": "def caf\u00e9(\u00e9t\u00e9, n):\n    if \u00e9t\u00e9 and n:\n"
                            "        return \"\u00e9\"\n    elif n:\n        return \"e\"\n"
                            "    return \"\"\n",
    "pkg/tabs.py": "def tabbed(a, b):\n\tif a:\n\t\treturn b\n\telif b:\n\t\treturn a\n\treturn None\n",
    "pkg/crlf.py": "def crlf(a):\r\n    if a > 1:\r\n        return 1\r\n    elif a < 0:\r\n"
                   "        return -1\r\n    return 0\r\n",
}
PY_TOML = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]
"""
ROWS = [
    ("scope", "path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn", "nloc", "params",
     "nesting", "cognitive", "occurrence"),
    ("pkg", "pkg/asyncs.py", "deco( f )", 5, 6, 2, 2, 2, 2, 1, 0, 1, 1),
    ("pkg", "pkg/asyncs.py", "gather( src )", 9, 14, 7, 7, 7, 6, 1, 2, 10, 1),
    ("pkg", "pkg/crlf.py", "crlf( a )", 1, 6, 3, 3, 3, 6, 1, 1, 2, 1),
    ("pkg", "pkg/fstr.py", "fmt( items , width )", 1, 8, 6, 6, 6, 8, 2, 3, 10, 1),
    ("pkg", "pkg/generic.py", "first( xs : list [ T ] , d : T )", 1, 5, 3, 3, 3, 5, 2, 2, 3, 1),
    ("pkg", "pkg/generic.py", "get( self , k : T )", 9, 12, 2, 2, 2, 4, 2, 1, 1, 1),
    ("pkg", "pkg/matcher.py", "route( cmd )", 1, 10, 7, 4, 4, 10, 1, 0, 2, 1),
    ("pkg", "pkg/tabs.py", "tabbed( a , b )", 1, 6, 3, 3, 3, 6, 2, 1, 2, 1),
    ("pkg", "pkg/unicode_ident.py", "caf\u00e9( \u00e9t\u00e9 , n )", 1, 6, 4, 4, 4, 6, 2, 1, 3, 1),
]
# LANG and PYTHONUTF8 bite on Linux, where the locale sets the interpreter's
# text encoding; on Windows they reach the child and change nothing.
ENVIRONMENTS = {"inherited": {}, "PYTHONHASHSEED=77": {"PYTHONHASHSEED": "77"},
                "LANG=C": {"LANG": "C", "LC_ALL": "C"},
                "LANG=en_US.ISO-8859-1": {"LANG": "en_US.ISO-8859-1",
                                          "LC_ALL": "en_US.ISO-8859-1"},
                "PYTHONUTF8=0": {"PYTHONUTF8": "0"}, "PYTHONUTF8=1": {"PYTHONUTF8": "1"}}


@pytest.fixture(scope="module")
def python_tree(tmp_path_factory) -> Path:
    """The tree above, committed, never measured: each test copies it, so no
    analysis cache carries one run's rows into the next."""
    repo = tmp_path_factory.mktemp("rows") / "repo"
    _write(repo / "crapkit.toml", PY_TOML)
    for rel, text in PY_SOURCES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(text.encode("utf-8"))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.mark.parametrize("env", ENVIRONMENTS.values(), ids=ENVIRONMENTS.keys())
def test_inventory_rows_hold_on_every_interpreter_and_locale(python_tree, tmp_path, env):
    """The rows are pinned, not compared between runs, so each Python in the CI
    matrix is held to the same bytes."""
    repo = tmp_path / "repo"
    shutil.copytree(python_tree, repo)

    done = run_cli(repo, "inventory", "--export", "inventory.tsv", env_extra=env)

    assert done.returncode == 0, done.stderr
    rows = (repo / "inventory.tsv").read_text(encoding="utf-8").splitlines()
    assert rows == ["\t".join(map(str, row)) for row in ROWS]
