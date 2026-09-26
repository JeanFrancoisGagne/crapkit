"""The JavaScript-side oracles, run from Python: the TypeScript compiler's
function list (ts_functions.cjs) and ESLint's complexity, max-depth and
sonarjs cognitive-complexity messages (eslint_probe.mjs).

Each call writes the file set under a work directory and starts one node
process for the whole list. The messages are read back into per-function
numbers here; which function a message belongs to comes from the compiler's
spans, so no crapkit output decides it. No crapkit import.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

import hang_guard

HERE = Path(__file__).resolve().parent
NUMBERS = {
    "complexity-classic": re.compile(r"has a complexity of (\d+)"),
    "complexity-modified": re.compile(r"has a complexity of (\d+)"),
    "max-depth": re.compile(r"Blocks are nested too deeply \((\d+)\)"),
    "cognitive": re.compile(r"Cognitive Complexity from (\d+)"),
}


@dataclass(frozen=True)
class Fn:
    path: str
    kind: str
    name: str
    start: int
    column: int
    end: int
    end_column: int
    params: int
    defaults: int
    return_type: bool
    features: frozenset
    param_list: tuple = ()  # ((name text, type text or None, rest), ...)
    shapes: frozenset = frozenset()  # ts_functions.cjs SHAPES the function holds
    pattern_defaults: int = 0  # default values inside destructuring patterns


def write(files: dict, work: Path) -> list[str]:
    """Write {posix path: str or bytes} under work; the relative paths back."""
    for name, content in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return sorted(files)


def _node(argv: list[str], cwd: Path) -> dict:
    done = hang_guard.run(["node", *argv], cwd=cwd, text=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError(f"node {argv[0]} failed ({done.returncode}): {done.stderr[-3000:]}")
    return json.loads(done.stdout)


def listing(node_modules: Path, work: Path, paths: list[str]) -> tuple[list[Fn], dict]:
    """(every function the TypeScript compiler finds in `paths`, relative to
    work; {path: [(shape, line, reach)]} for every misread shape
    ts_functions.cjs finds), from one node process."""
    found = _node([str(HERE / "ts_functions.cjs"), str(node_modules / "typescript"), *paths],
                  work)
    fns = [_fn(path, item) for path in paths for item in found[path]["functions"]]
    return fns, {path: [tuple(mark) for mark in found[path]["marks"]] for path in paths}


def functions(node_modules: Path, work: Path, paths: list[str]) -> list[Fn]:
    """Every function the TypeScript compiler finds in `paths` (relative to work)."""
    return listing(node_modules, work, paths)[0]


def _fn(path: str, item: dict) -> Fn:
    return Fn(path, item["kind"], item["name"], item["start"], item["column"], item["end"],
              item["endColumn"], item["params"], item["defaults"], item["returnType"],
              frozenset(item["features"]),
              tuple(tuple(param) for param in item.get("paramList", ())),
              frozenset(item.get("shapes", ())), item.get("patternDefaults", 0))


def lint(node_modules: Path, work: Path, modes: tuple, paths: list[str]) -> tuple[dict, dict]:
    """({mode: {path: [(line, 0-based column, number)]}}, {path: fatal message})
    for every mode's rule, from one node process over the whole list. A file
    the parser rejects is in the second map and has no messages."""
    found = _node([str(HERE / "eslint_probe.mjs"), str(node_modules), ",".join(modes), *paths],
                  work)
    return {mode: _numbers(found[mode], mode, paths) for mode in modes}, found["fatal"]


def _numbers(found: dict, mode: str, paths: list[str]) -> dict[str, list]:
    pattern = NUMBERS[mode]
    return {path: [(item["line"], item["column"] - 1,
                    int(pattern.search(item["message"]).group(1)))
                   for item in found.get(path, [])] for path in paths}


def messages(node_modules: Path, work: Path, mode: str, paths: list[str]) -> dict[str, list]:
    """{path: [(line, 0-based column, number)]} for the mode's rule; every file
    must parse."""
    found, fatal = lint(node_modules, work, (mode,), paths)
    if fatal:
        raise RuntimeError(f"ESLint cannot parse {fatal}")
    return found[mode]


def _holds(fn: Fn, line: int, column: int) -> bool:
    return (fn.start, fn.column) <= (line, column) <= (fn.end, fn.end_column)


def _next_on_line(fns: list[Fn], line: int, column: int) -> Fn | None:
    after = [fn for fn in fns if fn.start == line and fn.column > column]
    return min(after, key=lambda fn: fn.column, default=None)


def _tightest(fns: list[Fn], line: int, column: int) -> Fn | None:
    holding = [fn for fn in fns if _holds(fn, line, column)]
    return max(holding, key=lambda fn: (fn.start, fn.column), default=None)


def innermost(fns: list[Fn], path: str, line: int, column: int, heads: bool = True) -> Fn | None:
    """The function of `path` whose span holds (line, column) most tightly: the
    one that starts last. ESLint places a function's message at its head, which
    starts at or after the compiler's start (an `export` comes first), or, for
    a function that is a property's value, at the property's key, just before
    the function: then the next function on the line is the one. `heads` is
    False for a block's message (max-depth), which never sits at a head."""
    mine = [fn for fn in fns if fn.path == path]
    inner = _tightest(mine, line, column)
    return _prefer(inner, _next_on_line(mine, line, column) if heads else None, line)


def _prefer(inner: Fn | None, ahead: Fn | None, line: int) -> Fn | None:
    """The function ahead on the line, unless one that starts on the line holds
    the position already."""
    if ahead is None:
        return inner
    return ahead if inner is None or inner.start < line else inner


def per_function(fns: list[Fn], found: dict[str, list], heads: bool = True) -> dict[Fn, int]:
    """Each message charged to the innermost function holding its position, and
    the largest value each function collects."""
    values: dict[Fn, list] = {}
    for path, items in found.items():
        for line, column, number in items:
            values.setdefault(innermost(fns, path, line, column, heads), []).append(number)
    return {fn: max(numbers) for fn, numbers in values.items() if fn is not None}
