"""Whole-document JSON decoding, independent of the streaming reader under test."""
import json

from crapkit.coverage_istanbul import FnCoverage, _rel_path, _file_coverage, _dead_lines
from crapkit.coverage_py import _file_functions, _line_contexts, has_regions, judge_branch, judge_regions
from crapkit.errors import ToolError


def _iter_files(text, repo_root):
    for path, data in json.loads(text).items():
        yield _rel_path(path, repo_root), data


def parse_coveragepy_missing(text: str, *, path_prefix: str) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    try:
        report = json.loads(text)
        prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        return {prefix + p.replace("\\", "/"): set(data.get("missing_lines", ()))
                for p, data in report.get("files", {}).items()}
    except Exception as exc:
        raise ToolError(f"unparseable coverage.py report: {exc}") from exc

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

def _scored_files(files: dict, prefix: str) -> dict[str, list[FnCoverage]]:
    return {prefix + raw_path.replace("\\", "/"): _file_functions(data)
            for raw_path, data in files.items() if has_regions(data)}

def _regionless_files(files: dict) -> list[str]:
    return [raw_path for raw_path, data in files.items() if not has_regions(data)]

def parse_coveragepy(text: str, *, path_prefix: str,
                     label: str = "") -> dict[str, list[FnCoverage]]:
    try:
        report = json.loads(text)
        prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        files = report.get("files", {})
        per_file = _scored_files(files, prefix)
        # Regions first: a report with none of them has no statement counts
        # either, so the branch verdict would answer "add --cov-branch" to a
        # coverage too old to emit regions at all, and the rerun changes nothing.
        judge_regions(_regionless_files(files), len(files), label)
        judge_branch(bool(report.get("meta", {}).get("branch_coverage")), per_file, label)
        return per_file
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"unparseable coverage.py report: {exc}") from exc


def parse_istanbul_missing(text: str, *, repo_root: str) -> dict[str, set[int]]:
    """Per measured file, the lines whose statement never ran — the
    diff-coverage ground truth. Files with everything executed map to set()."""
    try:
        missing: dict[str, set[int]] = {}
        for rel_path, cov in _iter_files(text, repo_root):
            missing[rel_path] = _dead_lines(cov)
        return missing
    except Exception as exc:
        raise ToolError(f"unparseable istanbul artifact: {exc}") from exc

def parse_istanbul(text: str, *, repo_root: str) -> dict[str, list[FnCoverage]]:
    try:
        per_file: dict[str, list[FnCoverage]] = {}
        for rel_path, cov in _iter_files(text, repo_root):
            per_file[rel_path] = _file_coverage(cov)
        if not per_file:
            raise ToolError("istanbul artifact is empty (zero files) — the coverage run measured nothing")
        return per_file
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"unparseable istanbul artifact: {exc}") from exc
