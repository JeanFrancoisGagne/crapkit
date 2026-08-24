"""coverage.py JSON report parser. Pure: report text in, per-file function coverage out.

Requires the per-function regions coverage.py has emitted since 7.6.0 and the
start_line key from 7.13.1; requires branch data (the consuming lane must run
with branch coverage on) so the coverage term measures the same structure the
complexity term counts. Function spans come from start_line and the maximum
executed/missing line, the closest thing the report offers to an end line.
"""
from __future__ import annotations

import json

from .coverage_istanbul import FnCoverage
from .errors import ToolError


def _fn_coverage(name: str, fn: dict) -> FnCoverage:
    summary = fn.get("summary", {})
    lines = list(fn.get("executed_lines", ())) + list(fn.get("missing_lines", ()))
    start = fn.get("start_line") or (min(lines) if lines else 0)
    end = max(lines) if lines else start
    return FnCoverage(name=name, start=start, end=end,
                      invoked=summary.get("covered_lines", 0) > 0,
                      branches_total=summary.get("num_branches", 0),
                      branches_covered=summary.get("covered_branches", 0),
                      statements_total=summary.get("num_statements", 0),
                      statements_covered=summary.get("covered_lines", 0))


def _file_functions(raw_path: str, data: dict) -> list[FnCoverage]:
    functions = data.get("functions")
    if functions is None:
        raise ToolError(
            f"coverage.py report has no function regions for {raw_path!r} — needs coverage >= 7.6")
    # the "" key is the "(no function)" module-level bucket
    fns = [_fn_coverage(name, fn) for name, fn in functions.items() if name]
    return sorted(fns, key=lambda f: f.start)


def parse_coveragepy_missing(text: str, *, path_prefix: str) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    try:
        report = json.loads(text)
        prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        return {prefix + p.replace("\\", "/"): set(data.get("missing_lines", ()))
                for p, data in report.get("files", {}).items()}
    except Exception as exc:
        raise ToolError(f"unparseable coverage.py report: {exc}") from exc


def _line_contexts(raw: dict) -> dict[int, list[str]]:
    out = {}
    for line, contexts in raw.items():
        tests = sorted({c.split("|")[0] for c in contexts if c})
        if tests:
            out[int(line)] = tests
    return out


def parse_coveragepy_contexts(text: str, *, path_prefix: str) -> dict[str, dict[int, list[str]]]:
    """line -> test ids per file, from a report made with --show-contexts and
    dynamic_context = test_function. The empty module-import context is not a test."""
    try:
        report = json.loads(text)
        prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        out = {}
        for p, data in report.get("files", {}).items():
            contexts = _line_contexts(data.get("contexts", {}))
            if contexts:
                out[prefix + p.replace("\\", "/")] = contexts
        return out
    except Exception as exc:
        raise ToolError(f"unparseable coverage.py report: {exc}") from exc


def parse_coveragepy(text: str, *, path_prefix: str) -> dict[str, list[FnCoverage]]:
    try:
        report = json.loads(text)
        if not report.get("meta", {}).get("branch_coverage"):
            raise ToolError("coverage.py report lacks branch data — run the lane with branch coverage on")
        prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        return {prefix + raw_path.replace("\\", "/"): _file_functions(raw_path, data)
                for raw_path, data in report.get("files", {}).items()}
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"unparseable coverage.py report: {exc}") from exc
