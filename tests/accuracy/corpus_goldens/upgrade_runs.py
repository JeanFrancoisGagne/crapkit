"""A small history written by an earlier crapkit release, then read by the crapkit under test.

The repo is small and written with config keys every release since 0.4.0
reads (target, one scope, one lane that copies a recorded coverage.py
artifact), because an upgrade test must run the old wheel too: the small
corpus uses shapes 0.7.4 refuses. Seven commits, one day apart, edit
src/a.py and src/b.py together six times (coupling needs 5 shared commits),
and the last adds src/c.py, which the artifact does not hold.

write_history() runs, with the release's wheel alone on PYTHONPATH:
coverage (run 1), worklist and coupling (the churn and coupling caches), then
an uncommitted ccn-7 function over the ceiling 6, verify (run 2, which fails
with exit 6), and coverage again (run 3). The README's taint rule makes run 1
the baseline: run 3 was made after a failed verify that no verify cleared.

read() runs the read commands on a copy of that tree, as a given crapkit.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from accuracy.corpus_goldens import releases
from accuracy.kit import drive, repos

DAY = 86_400
FIRST = repos.EPOCH
NOW = FIRST + 8 * DAY
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
          'languages = ["python"]\n\n'
          + repos.lane_toml("py", ".crapkit/cov/py.json", "coveragepy", ["src"], "recorded/py.json"))
A_SOURCE = "def f(x):\n    if x:\n        return 1\n    return 0\n\n\ndef g(y):\n    return y\n"
B_SOURCE = "def h(v):\n    if v > 1:\n        return v\n    return 0\n"
C_SOURCE = "def k(n):\n    return n\n"
LATE = ("\n\ndef late(v):\n    if v == 1:\n        return 1\n    if v == 2:\n        return 2\n"
        "    if v == 3:\n        return 3\n    if v == 4:\n        return 4\n"
        "    if v == 5:\n        return 5\n    if v == 6:\n        return 6\n    return 0\n")
# (file, function, start line, executed lines, missing lines) for the final tree.
MEASURED = (("src/a.py", "f", 1, [2, 3], [4]), ("src/a.py", "g", 7, [], [8]),
            ("src/b.py", "h", 1, [2, 3, 4], []))
BASELINE = 1
WRITES = (("coverage", "--json"), ("worklist", "--json"), ("coupling", "--json"))
AFTER_EDIT = (("verify", "--json"), ("coverage", "--json"))
READS = (("runs", "list", "--json"), ("worklist", "--json"), ("coupling", "--json"),
         ("trend", "--json"))
COMMANDS = tuple(" ".join(argv) for argv in READS)
CACHE_PREFIXES = ("churn", "coupling")


def _summary(executed: list, missing: list) -> dict:
    total = len(executed) + len(missing)
    return {"covered_lines": len(executed), "num_statements": total,
            "missing_lines": len(missing), "excluded_lines": 0, "covered_branches": 0,
            "num_branches": 0, "missing_branches": 0, "num_partial_branches": 0,
            "percent_covered": 100.0 * len(executed) / total}


def _region(executed: list, missing: list) -> dict:
    return {"executed_lines": executed, "missing_lines": missing, "excluded_lines": [],
            "executed_branches": [], "missing_branches": [], "summary": _summary(executed, missing)}


def _lines(path: str, column: int) -> list[int]:
    """Column 3 (executed) or 4 (missing) of every MEASURED entry in `path`."""
    return sorted(line for entry in MEASURED if entry[0] == path for line in entry[column])


def _file(path: str) -> dict:
    functions = {name: {**_region(ran, missed), "start_line": start}
                 for owner, name, start, ran, missed in MEASURED if owner == path}
    return {**_region(_lines(path, 3), _lines(path, 4)), "functions": functions, "classes": {}}


def artifact() -> str:
    """The coverage.py JSON report the lane copies into place."""
    files = {path: _file(path) for path in ("src/a.py", "src/b.py")}
    return json.dumps({"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                                "show_contexts": False, "timestamp": "2026-09-24T00:00:00"},
                       "files": files, "totals": _summary([1], [])})


def _commit(number: int) -> repos.Commit:
    """Commit `number` (1-based) edits a.py and b.py by their trailing comment."""
    mark = f"# edit {number}\n"
    files = {"src/a.py": A_SOURCE + mark, "src/b.py": B_SOURCE + mark}
    if number == 1:
        files.update({"crapkit.toml": CONFIG, "recorded/py.json": artifact()})
    return repos.Commit(files=files, date=FIRST + (number - 1) * DAY, message=f"edit {number}")


def spec() -> repos.Spec:
    last = repos.Commit(files={"src/c.py": C_SOURCE}, date=FIRST + 6 * DAY, message="add c")
    return repos.Spec(steps=tuple(_commit(number) for number in range(1, 7)) + (last,))


@dataclass(frozen=True)
class History:
    version: str
    site: Path
    root: Path
    codes: tuple[tuple[str, int], ...]

    def driver(self, root: Path, site: Path | None = None) -> drive.Driver:
        """crapkit at `root`: the release's wheel when `site` is given, else the one under test."""
        env = {"PYTHONPATH": str(site)} if site else None
        return drive.Driver(root, date_now=NOW, spawn=True, env=env)

    def copy(self, dest: Path) -> Path:
        shutil.copytree(self.root, dest, symlinks=True)
        return dest


def _run_all(driver: drive.Driver, steps: tuple) -> tuple[tuple[str, int], ...]:
    return tuple((" ".join(argv), driver.run(*argv).code) for argv in steps)


def write_history(version: str, work: Path) -> History:
    """The small history above, written by release `version`."""
    site = releases.site(version, work / "site")
    root = repos.build(spec(), work / "repo").root
    old = drive.Driver(root, date_now=NOW, spawn=True, env={"PYTHONPATH": str(site)})
    codes = _run_all(old, WRITES)
    with (root / "src" / "a.py").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(LATE)
    codes += _run_all(old, AFTER_EDIT)
    return History(version, site, root, codes)


def read(driver: drive.Driver) -> dict[str, drive.Result]:
    """Every read command's result, keyed by its argv."""
    return {" ".join(argv): driver.run(*argv) for argv in READS}


def caches(root: Path) -> dict[str, bytes]:
    """The churn and coupling cache files under .crapkit/, by name."""
    return {path.name: path.read_bytes() for path in (root / ".crapkit").iterdir()
            if path.is_file() and path.name.startswith(CACHE_PREFIXES)}


def drop_caches(root: Path) -> None:
    for name in caches(root):
        (root / ".crapkit" / name).unlink()


def version_of(driver: drive.Driver) -> str:
    """The version `crapkit --version` prints: its last word."""
    return driver.run("--version").stdout.split()[-1]
