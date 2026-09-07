"""Per-file coverage.py regions, context cleanup, and report completeness rules.

Requires the per-function regions coverage.py has emitted since 7.6.0 and the
start_line key from 7.13.1. Branch data is preferred and not required: the
coverage term falls back to statements, with a warning, so an artifact built by
`pytest --cov --cov-report=json` — the default CI shape, with no --cov-branch —
still scores. Function spans come from start_line and the maximum
executed/missing line, the closest thing the report offers to an end line.
"""
from __future__ import annotations

import sys

from .coverage_istanbul import FnCoverage, coverage_count
from .errors import ToolError

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


def _fn_coverage(name: str, fn: dict) -> FnCoverage:
    summary = _admit_summary(name, fn.get("summary", {}))
    lines = list(fn.get("executed_lines", ())) + list(fn.get("missing_lines", ()))
    start = fn.get("start_line") or (min(lines) if lines else 0)
    end = max(lines) if lines else start
    return FnCoverage(name=name, start=start, end=end,
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
