"""Per-file coverage.py regions, context cleanup, and report completeness rules.

Requires the per-function regions coverage.py has emitted since 7.6.0 and the
start_line key from 7.13.1. Branch data is preferred and not required: the
coverage term falls back to statements, with a warning, so an artifact built by
`pytest --cov --cov-report=json` — the default CI shape, with no --cov-branch —
still scores. Function spans come from start_line and the maximum
executed/missing line, the closest thing the report offers to an end line.

This module is also the coverage.py adapter (coverage_format looks it up from a
lane's `parser`): it walks the report's "files" member through covstream's
framing, keys each file with the lane's path_prefix, takes that prefix back off
for the wrong-tree check, and owns the runner advice a refusal gives.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream
from .coverage_istanbul import FnCoverage, coverage_count
from .errors import ToolError

if TYPE_CHECKING:
    from .config import Lane

_NO_BRANCH = "coverage.py report lacks branch data — run the lane with branch coverage on"
_OLD_COVERAGE = "needs coverage >= 7.6"
_SAMPLE = 3


def _admit_summary(name: str, summary: dict) -> dict:
    pairs = (("num_branches", "covered_branches"), ("num_statements", "covered_lines"))
    counts = {}
    for total, covered in pairs:
        counts[total] = coverage_count(summary.get(total, 0), f"{name}: {total}")
        counts[covered] = coverage_count(summary.get(covered, 0), f"{name}: {covered}")
        if counts[covered] > counts[total]:
            raise ValueError(f"{name}: {covered} exceeds {total}")
    return counts


class LineRegion(FnCoverage):
    """A coverage.py function region, which counts lines and arcs but no calls.

    A function written on one line shares that line with its own `def`
    statement, which runs when the module is imported, so its region reads as
    run whether a test called it or not: an uncalled `def one(x): return x`
    reads 1 of its 2 branches covered. score.py floors a one-line function
    joined to such a region the way it floors a function on a shared span.
    istanbul counts calls per function, and its regions stay plain FnCoverage.
    """

    __slots__ = ()


def _fn_coverage(name: str, fn: dict) -> LineRegion:
    summary = _admit_summary(name, fn.get("summary", {}))
    lines = list(fn.get("executed_lines", ())) + list(fn.get("missing_lines", ()))
    start = fn.get("start_line") or (min(lines) if lines else 0)
    end = max(lines) if lines else start
    return LineRegion(name=name, start=start, end=end,
                      invoked=summary.get("covered_lines", 0) > 0,
                      branches_total=summary.get("num_branches", 0),
                      branches_covered=summary.get("covered_branches", 0),
                      statements_total=summary.get("num_statements", 0),
                      statements_covered=summary.get("covered_lines", 0))


def has_regions(data: object) -> bool:
    """Whether the report carries function regions for this one file.

    coverage.py writes the "functions" key once per code-region kind the file's
    own reporter declares, so a file measured by a plugin reporter that declares
    none — django or jinja template coverage — loses the key while every .py
    file in the same report keeps it. Public because the streaming reader asks
    the same question one file at a time.
    """
    return isinstance(data, dict) and data.get("functions") is not None


def _file_functions(data: dict) -> list[FnCoverage]:
    """One file's functions, sorted by start line. Ask `has_regions` first: this
    reads an absent "functions" key as an empty one."""
    # the "" key is the "(no function)" module-level bucket
    fns = [_fn_coverage(name, fn) for name, fn in (data.get("functions") or {}).items() if name]
    return sorted(fns, key=lambda f: f.start)


def _named(label: str) -> str:
    """Who read the report, when the caller said. The parsers are pure and take
    no lane, so the lane layer passes its own name down for the warnings."""
    return f"{label}: " if label else ""


def _sample(paths: list[str]) -> str:
    rest = len(paths) - _SAMPLE
    shown = ", ".join(sorted(paths)[:_SAMPLE])
    return f"{shown} and {rest} more" if rest > 0 else shown


def judge_branch(branch: bool, per_file: dict[str, list[FnCoverage]], label: str = "") -> None:
    """No branch data downgrades the coverage term; it does not fail the lane.

    Every function in the model already falls back to statement coverage when it
    holds no branches, and that fallback runs on every normal report, so
    refusing this one blocked arithmetic crapkit performs all day. The refusal
    is kept for the report that carries neither, where there is nothing to
    divide by and every function would come out fully covered.
    """
    if branch:
        return
    if not any(fn.statements_total for fns in per_file.values() for fn in fns):
        raise ToolError(_NO_BRANCH)
    print(f"crapkit: {_named(label)}coverage.py report carries no branch data, so the "
          "coverage term is statement-based for this artifact — add --cov-branch to the "
          "lane command to measure branches", file=sys.stderr)


def judge_regions(regionless: list[str], total: int, label: str = "") -> None:
    """Files with no regions are skipped and named; a report where NO file has
    them is the old-coverage case the refusal was written for.

    Raising for the first bad file threw away every other file in the report,
    and the files that were fine were never mentioned.
    """
    if not regionless:
        return
    if len(regionless) == total:
        raise ToolError(f"coverage.py report has no function regions for any of its "
                        f"{total} file(s) — {_OLD_COVERAGE}")
    print(f"crapkit: {_named(label)}coverage.py report has no function regions for "
          f"{len(regionless)} of {total} file(s) ({_sample(regionless)}) — those files "
          f"are skipped and the rest of the report is scored", file=sys.stderr)


def _line_contexts(raw: dict) -> dict[int, list[str]]:
    out = {}
    for line, contexts in raw.items():
        tests = sorted({c.split("|")[0] for c in contexts if c})
        if tests:
            out[int(line)] = tests
    return out


# --- path keys ---------------------------------------------------------------

def lane_prefix(path_prefix: str) -> str:
    """The lane's `path_prefix` as it is actually glued onto a measured path."""
    return (path_prefix.rstrip("/") + "/") if path_prefix else ""


def measured_key(prefix: str, raw_path: str) -> str:
    """The repository path one report file key names, under a lane_prefix.

    The one spelling of the forward rule. The reader prepends the prefix to
    EVERY key, an absolute one included, which is why as_reported exists."""
    return prefix + raw_path.replace("\\", "/")


def as_reported(lane: Lane, key: str) -> str:
    """One coverage key with this lane's own `path_prefix` taken back off, which
    is the path the runner actually wrote.

    `backend/` + `/other/checkout/a.py` starts with neither `/` nor a drive
    letter, so asked of the key, the wrong-tree check answers no on every lane
    that declares the knob: the monorepo shape the check was written for."""
    prefix = lane_prefix(lane.path_prefix)
    return key[len(prefix):] if prefix and key.startswith(prefix) else key


# --- reading the report --------------------------------------------------------

_BAD_REPORT = "unparseable coverage.py report"


def _meta_has_branch(key: str, value: object) -> bool:
    if key != "meta" or not isinstance(value, dict):
        return False
    return bool(value.get("branch_coverage"))


class _Files:
    """What the walk learned about the report's files, decided at the end.

    Both verdicts wait for the whole walk. Members are not ordered:
    json.dump(sort_keys=True) writes "files" ahead of "meta", so a reader that
    judges at the first file refuses a report whose branch flag it has not read
    yet, and the whole-document parser has always seen meta first.
    """

    def __init__(self) -> None:
        self.per_file: dict[str, list[FnCoverage]] = {}
        self.dead: dict[str, set[int]] = {}
        self.regionless: list[str] = []
        self.total = 0

    def add(self, prefix: str, raw_path: str, data: dict) -> None:
        self.total += 1
        path = measured_key(prefix, raw_path)
        self.dead[path] = set(data.get("missing_lines", ()))
        if not has_regions(data):
            self.regionless.append(raw_path)
            return
        self.per_file[path] = _file_functions(data)


def _coveragepy_both(w, prefix: str, label: str) -> tuple[dict, dict]:
    """path -> function coverage, salvaging the same way the whole-document
    parser does: a statement-based downgrade with no branch data, and files with
    no regions skipped rather than fatal."""
    files, branch = _Files(), False
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "member":
            branch = branch or _meta_has_branch(key, value)
            continue
        files.add(prefix, key, value)
    # Same order as the whole-document reader: regions decide first, so the two
    # cannot answer one report differently.
    judge_regions(files.regionless, files.total, label)
    judge_branch(branch, files.per_file, label)
    return files.per_file, files.dead


def parse_coveragepy_both_file(path: Path | str, *, path_prefix: str,
                               chunk: int = covstream.CHUNK, label: str = ""
                               ) -> tuple[dict, dict, str]:
    """Function coverage, missing lines, and byte digest from one report walk."""
    prefix = lane_prefix(path_prefix)
    (per_file, dead), digest = covstream.read_walk(
        path, lambda w: _coveragepy_both(w, prefix, label), f"{_BAD_REPORT} {path}", chunk)
    return per_file, dead, digest


def _coveragepy_missing(w, prefix: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "sub":
            out[measured_key(prefix, key)] = set(value.get("missing_lines", ()))
    return out


def parse_coveragepy_missing_file(path: Path | str, *, path_prefix: str,
                                  chunk: int = covstream.CHUNK) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    prefix = lane_prefix(path_prefix)
    missing, _ = covstream.read_walk(
        path, lambda w: _coveragepy_missing(w, prefix), _BAD_REPORT, chunk)
    return missing


def _coveragepy_contexts(w, prefix: str, source_path: str) -> dict:
    selected = {}
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "sub" and measured_key(prefix, key) == source_path:
            selected = _line_contexts(value.get("contexts", {}))
    return selected


def parse_coveragepy_contexts_file(path: Path | str, *, path_prefix: str,
                                   source_path: str, chunk: int = covstream.CHUNK
                                   ) -> dict[int, list[str]]:
    """One repository path's line contexts, after validating the whole report."""
    prefix = lane_prefix(path_prefix)
    selected, _ = covstream.read_walk(
        path, lambda w: _coveragepy_contexts(w, prefix, source_path), _BAD_REPORT, chunk)
    return selected


# --- the adapter a lane reads through ------------------------------------------
#
# The reader takes path_prefix and takes no repo root, so a refusal is about the
# environment the lane binds to, and the prefix is a real knob. A path this tree
# spelled absolutely is the runner's own switch: path_prefix only prepends.

WRONG_TREE_FIX = ("Point the lane at this checkout's own environment (a bare "
                  "`python -m pytest` binds to whichever venv the shell has active — run "
                  "it through the project's manager, `uv run python -m pytest ...`), or "
                  "set path_prefix when the runner reports paths relative to a subdirectory")
ABSOLUTE_FIX = ("Make the runner write relative paths: `relative_files = true` "
                "under `[tool.coverage.run]` in pyproject.toml, or "
                "`[run] relative_files = true` in .coveragerc, then rerun the lane")
UNMEASURED_READING = "or the runner reports paths this lane needs path_prefix to rebase"


def read(lane: Lane, root: Path, artifact: Path) -> tuple[dict, dict, str]:
    """The lane's function coverage, dead lines and artifact digest, one walk."""
    return parse_coveragepy_both_file(artifact, path_prefix=lane.path_prefix,
                                      label=f"lane {lane.name!r}")


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines coverage.py reports as never run, per measured file."""
    return parse_coveragepy_missing_file(artifact, path_prefix=lane.path_prefix)


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """line -> test ids for one repository path."""
    return parse_coveragepy_contexts_file(artifact, path_prefix=lane.path_prefix,
                                          source_path=source_path)
