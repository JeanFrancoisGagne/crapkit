"""The small corpus's synthetic history: 60 commits whose last one holds the small corpus.

tools/accuracy/regenerate.py builds it with kit.repos and writes
history/small.bundle; the history goldens and the store-upgrade check clone
that bundle. The history holds what churn, coupling and renames need to see:

- commits spread over five months, two of them on either side of a month end
  (2025-01-31T23:59:59Z and 2025-02-01T00:00:00Z);
- a rename (`src/py/old_grades.py` becomes `src/py/grades.py`) and a copy
  (`src/py/twins.py` copied to `src/py/twins_copy.py`, later removed);
- a branch merged back with --no-ff;
- a non-ASCII path (`src/py/données.py`) edited more than once;
- one bulk commit touching more than 30 files;
- two files that keep changing together (`src/web/arrows.js` and
  `src/web/templates.ts`), so coupling has a pair to rank;
- a second author on some commits.

Commit dates are fixed and every commit is deterministic, so two builds of
the spec give the same commit ids on every OS.
"""
from __future__ import annotations

from accuracy.kit.repos import Branch, Checkout, Commit, Merge

DAY = 86_400
MONTH_END = 1_738_367_999      # 2025-01-31T23:59:59Z
MONTH_START = MONTH_END + 1    # 2025-02-01T00:00:00Z
SECOND_AUTHOR = ("B Uthor", "b.uthor@example.com")
COUPLED = ("src/web/arrows.js", "src/web/templates.ts")
NON_ASCII = "src/py/données.py"


def _half(data: bytes) -> bytes:
    """The file's lines up to about its middle, cut at a line end so the bytes stay valid."""
    cut = data.rfind(b"\n", 0, max(1, len(data) // 2))
    return data[: cut + 1] if cut >= 0 else data


def _early(files: dict[str, bytes]) -> dict[str, bytes]:
    """An older spelling of each file: its first half, so later commits change it."""
    return {path: _half(data) for path, data in files.items()}


def _pick(files: dict, prefix: str) -> dict:
    return {path: data for path, data in files.items() if path.startswith(prefix)}


def _append(data: bytes, note: bytes) -> bytes:
    return data + note


def _comment(path: str, number: int) -> bytes:
    """A trailing comment line in the file's own comment syntax."""
    marker = b"#" if path.endswith((".py", ".sh", ".bash", ".ps1", ".psm1", ".toml")) else b"//"
    return marker + b" revision " + str(number).encode("ascii") + b"\n"


def _config_steps(files: dict, start: int) -> list:
    base = {path: data for path, data in files.items()
            if path == "crapkit.toml" or path.startswith(("recorded/", "tests/"))}
    return [Commit(files=base, message="config and recordings", date=start)]


def _grades_rename(files: dict, date: int) -> list:
    grades = files["src/py/grades.py"]
    return [
        Commit(files={"src/py/old_grades.py": _early({"g": grades})["g"]},
               message="grades, first cut", date=date),
        Commit(renames={"src/py/old_grades.py": "src/py/grades.py"},
               message="rename old_grades to grades", date=date + DAY),
    ]


def _month_edge(files: dict) -> list:
    data = files[NON_ASCII]
    return [
        Commit(files={NON_ASCII: _early({"d": data})["d"]}, message="donnees, before the month end",
               date=MONTH_END),
        Commit(files={NON_ASCII: _append(_early({"d": data})["d"], _comment(NON_ASCII, 2))},
               message="donnees, after the month end", date=MONTH_START,
               author=SECOND_AUTHOR),
    ]


def _copy_steps(files: dict, date: int) -> list:
    twins = files["src/py/twins.py"]
    return [
        Commit(files={"src/py/twins.py": twins, "src/py/twins_copy.py": twins},
               message="twins, and a copy of it", date=date),
        Commit(files={"src/py/twins_copy.py": None}, message="drop the copy", date=date + DAY),
    ]


def _coupled_steps(files: dict, start: int, count: int) -> list:
    steps = []
    for number in range(count):
        changed = {path: _append(_early({"x": files[path]})["x"], _comment(path, number))
                   for path in COUPLED}
        steps.append(Commit(files=changed, message=f"arrows and templates, round {number}",
                            date=start + number * 2 * DAY,
                            author=SECOND_AUTHOR if number % 3 == 0 else Commit().author))
    return steps


def _branch_steps(files: dict, date: int) -> list:
    web = _pick(files, "src/web/")
    widget = {path: data for path, data in web.items() if path.endswith((".tsx", ".jsx"))}
    return [
        Branch("feature"),
        Commit(files=_early(widget), message="widgets on a branch", date=date),
        Checkout("main"),
        Commit(files={"src/legacy/old.py": files["src/legacy/old.py"]},
               message="legacy on main meanwhile", date=date + DAY),
        Merge("feature", message="merge the widgets", date=date + 2 * DAY),
    ]


def _bulk_step(files: dict, date: int) -> list:
    """One commit that adds every native, script and vendor file at once."""
    bulk = {path: data for path, data in files.items()
            if path.startswith(("src/native/", "src/scripts/", "vendor/"))}
    return [Commit(files=bulk, message="bulk import of the native and vendor trees", date=date)]


def _filler(files: dict, start: int, count: int) -> list:
    """Small edits across the Python scope, one file per commit, round robin."""
    paths = sorted(path for path in _pick(files, "src/py/") if path.endswith(".py")
                   and path not in ("src/py/grades.py", NON_ASCII))
    steps = []
    for number in range(count):
        path = paths[number % len(paths)]
        steps.append(Commit(files={path: _early({"x": files[path]})["x"] + _comment(path, number)},
                            message=f"work on {path}", date=start + number * DAY))
    return steps


def _final(files: dict, date: int) -> list:
    """The last commit writes every file's final bytes, so the tree is the small corpus."""
    return [Commit(files=dict(files), message="the small corpus", date=date)]


def steps(files: dict[str, bytes], start: int, end: int) -> list:
    """60 commits from `start` to `end` (epoch seconds); the last is at `end`."""
    plan = (_config_steps(files, start) + _grades_rename(files, start + DAY)
            + _month_edge(files) + _copy_steps(files, MONTH_START + 3 * DAY)
            + _coupled_steps(files, MONTH_START + 6 * DAY, 12)
            + _branch_steps(files, MONTH_START + 40 * DAY)
            + _bulk_step(files, MONTH_START + 45 * DAY))
    commits = sum(isinstance(step, (Commit, Merge)) for step in plan)
    plan += _filler(files, MONTH_START + 50 * DAY, 60 - commits - 1)
    return plan + _final(files, end)
