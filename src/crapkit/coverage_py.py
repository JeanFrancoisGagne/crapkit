"""Per-file coverage.py regions, context cleanup, and report completeness rules.

Requires the per-function regions coverage.py writes with their start_line,
which it has done since 7.13.1. Branch data is preferred and not required: the
coverage term falls back to statements, with a warning, so an artifact built by
`pytest --cov --cov-report=json` — the default CI shape, with no --cov-branch —
still scores. Function spans run from start_line, the def statement's line, to
the maximum executed/missing line, the closest thing the report offers to an
end line.

This module is also the coverage.py adapter (coverage_format looks it up from a
lane's `parser`): it walks the report's "files" member through covstream's
framing, keys each relative file with the lane's path_prefix, records each
absolute key with the placing step's reason for the wrong-tree check, and owns
the runner advice a refusal gives.

Under a POSIX locale that is not UTF-8 the lane's own Python names files in that
locale's encoding, so its report keys `pkg/café.py` as `pkg/cafÃ©.py`. The
adapter reads such a key back through the locale's codec when the file it then
names exists and the key as written does not (_speller).
"""
from __future__ import annotations

import codecs
import locale
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream
from .errors import ToolError
from .repopath import Placing, Reported, Unplaced, absolute, file_separators
from .repotext import json_kind, utf8_spelling
from .named import first_few
from .score import FnCoverage, coverage_count

if TYPE_CHECKING:
    from .config import Lane

_NO_BRANCH = "coverage.py report lacks branch data - run the lane with branch coverage on"
# The oldest coverage.py whose report this reader takes: 7.13.1 writes each
# region's start_line. pyproject.toml's py and dev extras pin the same floor,
# and `crapkit doctor` asks each lane's interpreter for its coverage version
# against the same number.
REGIONS_FLOOR = "7.13.1"
COVERAGE_FLOOR = f"coverage>={REGIONS_FLOOR}"
_OLD_COVERAGE = f"needs {COVERAGE_FLOOR}"
# What every refusal of a count or a line coverage.py itself writes tells the
# user to do: the report was edited, merged or truncated after coverage.py
# wrote it, and only a fresh one holds the numbers.
_REGENERATE = "regenerate the report with `coverage json`"


_PAIRS = (("num_branches", "covered_branches"), ("num_statements", "covered_lines"))


def _admit_summary(name: str, summary: object) -> dict:
    """One function's counts, each kind as a total and a covered count or not at
    all. coverage.py writes a summary on every region, and both counts of every
    kind it measured, so a summary that is gone or not an object, a count
    without its partner, or no count of either kind is a report something else
    rewrote; each read as 0 of 0, and a function that ran scored cov 0. A kind
    with neither count is one the report did not measure."""
    if not isinstance(summary, dict):
        raise ValueError(f"{name}: no summary object, so crapkit cannot tell how much of it "
                         f"ran; {_REGENERATE}")
    counts = {}
    for total, covered in _PAIRS:
        counts.update(_admit_pair(name, summary, total, covered))
    if not counts:
        raise ValueError(f"{name}: summary holds neither statement nor branch counts; "
                         f"coverage.py writes one kind or both, so {_REGENERATE}")
    _require_deciding_statements(name, counts)
    counts["excluded_lines"] = coverage_count(summary.get("excluded_lines", 0),
                                              f"{name}: excluded_lines")
    return counts


def _require_deciding_statements(name: str, counts: dict) -> None:
    """With 0 of 0 branches the statement pair decides the function's coverage,
    so a summary without it read as a function that never ran: cov 0 for one
    that ran every line. With branches, the branch pair decides and the missing
    statement pair changes nothing."""
    if "num_statements" not in counts and counts["num_branches"] == 0:
        raise ValueError(f"{name}: summary holds no statement counts and no branch, so "
                         f"crapkit cannot tell how much of it ran; {_REGENERATE}")


def _admit_pair(name: str, summary: dict, total: str, covered: str) -> dict:
    present = [key for key in (total, covered) if key in summary]
    if not present:
        return {}
    _require_partner(name, present, total, covered)
    return _counted_pair(name, summary, total, covered)


def _counted_pair(name: str, summary: dict, total: str, covered: str) -> dict:
    counts = {key: coverage_count(summary[key], f"{name}: {key}") for key in (total, covered)}
    if counts[covered] > counts[total]:
        raise ValueError(f"{name}: {covered} exceeds {total}; {_REGENERATE}")
    return counts


def _require_partner(name: str, present: list[str], total: str, covered: str) -> None:
    if len(present) == 1:
        other = covered if present[0] == total else total
        raise ValueError(f"{name}: {present[0]} without {other}; coverage.py writes both, "
                         f"so {_REGENERATE}")


def _region_start(name: str, fn: dict) -> int:
    """The function's def line, which coverage.py writes as start_line from 7.13.1.

    An older report carries none, and no line inside the region is the def's:
    the body starts below it, and a nested function's def statement sits in its
    encloser's region. Read from the body, a nested function that never ran
    joined its encloser by exact start and scored as half covered, so the
    report is refused instead.
    """
    start = fn.get("start_line")
    if start is None:
        raise ValueError(f"{name}: no start_line; coverage.py writes it on every function "
                         f"from 7.13.1, so install {COVERAGE_FLOOR} and rerun the lane")
    if type(start) is not int or start < 1:
        raise ValueError(f"{name}: start_line must be a line number, got {start!r}; "
                         f"coverage.py writes the def's line there, so {_REGENERATE}")
    return start


def _excluded(summary: dict) -> bool:
    """Whether coverage.py was told to leave the whole function out: a
    `# pragma: no cover` def, or exclude patterns that took every statement,
    leave a region with excluded lines and no statement to measure."""
    return summary.get("num_statements", 0) == 0 and summary["excluded_lines"] > 0


def _region_lines(name: str, fn: dict) -> list[int]:
    """The lines a region holds, its excluded ones included: an excluded
    function keeps no other, and a function whose last lines are excluded
    ends where its source does."""
    return [line for key in ("executed_lines", "missing_lines", "excluded_lines")
            for line in _line_list(name, fn, key)]


def _fn_coverage(name: str, fn: object) -> FnCoverage:
    summary = _admit_summary(name, fn.get("summary") if isinstance(fn, dict) else None)
    start = _region_start(name, fn)
    lines = _region_lines(name, fn)
    end = max(lines) if lines else start
    # A kind the summary lacks reads 0 of 0 below only where it cannot decide:
    # _admit_summary refused every summary whose missing kind would.
    return FnCoverage(name=name, start=start, end=end,
                      invoked=summary.get("covered_lines", 0) > 0,
                      branches_total=summary.get("num_branches", 0),
                      branches_covered=summary.get("covered_branches", 0),
                      statements_total=summary.get("num_statements", 0),
                      statements_covered=summary.get("covered_lines", 0),
                      excluded=_excluded(summary))


def _line_list(name: str, fn: dict, key: str) -> list[int]:
    """A region's or a file's executed or missing lines: a list of line numbers
    when the key is there, as coverage.py writes it. `name` is the function or
    the file the refusal names."""
    lines = fn.get(key, [])
    if not isinstance(lines, list):
        raise ValueError(f"{name}: {key} holds {json_kind(lines)}, not a list of line numbers; "
                         f"{_REGENERATE}")
    _require_entries(name, key, lines, lambda line: type(line) is int, "a line number")
    return lines


def _require_entries(name: str, key: str, entries: list, fits, what: str) -> None:
    """The first entry of a list that `fits` refuses, named by its index. A
    "5" read as a line matched no line, so it was a dead line nobody could see."""
    bad = next((index for index, entry in enumerate(entries) if not fits(entry)), None)
    if bad is not None:
        raise ValueError(f"{name}: {key}[{bad}] holds {_entry_kind(entries[bad])}, not {what}; "
                         f"{_REGENERATE}")


def _entry_kind(value: object) -> str:
    """json_kind, with a float told apart from the integer a line number is."""
    return "a decimal number" if type(value) is float else json_kind(value)


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
    """One file's functions, sorted by start line. Ask `has_regions` first. A
    refusal names the function or the field; `_read_functions` puts the file's
    path in front of it."""
    functions = data["functions"]
    if not isinstance(functions, dict):
        raise ValueError(f"functions holds {json_kind(functions)}, not an object; {_REGENERATE}")
    # the "" key is the "(no function)" module-level bucket
    fns = [_fn_coverage(name, fn) for name, fn in functions.items() if name]
    return sorted(fns, key=lambda f: f.start)


def _named(label: str) -> str:
    """Who read the report, when the caller said. The parsers are pure and take
    no lane, so the lane layer passes its own name down for the warnings."""
    return f"{label}: " if label else ""


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
          "coverage term is statement-based for this artifact - add --cov-branch to the "
          "lane command to measure branches", file=sys.stderr)


def _judge_branch_counts(meta_branch: bool, files: _Files, label: str) -> None:
    """The report measures branches when its meta says so, or when any function
    carries branch counts: a report with no meta said its term was
    statement-based while its functions scored on branches."""
    has_branches = meta_branch or files.branch_counted > 0
    judge_branch(has_branches, files.per_file, label)
    if has_branches:
        _refuse_branchless(files.branchless)


def _refuse_branchless(branchless: list[str]) -> None:
    """A function with no branch counts in a report that measures branches.
    coverage.py writes 0 of 0 for a function with no branch, so something else
    rewrote this one; read as statements, its coverage moved with nothing said."""
    if branchless:
        raise ToolError(
            f"coverage.py report measures branches, but {len(branchless)} function(s) carry no "
            f"branch counts ({first_few(sorted(branchless))}), so crapkit cannot tell how many "
            f"of their branches ran; {_REGENERATE}")


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
                        f"{total} file(s) - {_OLD_COVERAGE}")
    print(f"crapkit: {_named(label)}coverage.py report has no function regions for "
          f"{len(regionless)} of {total} file(s) ({first_few(sorted(regionless))}) - those files "
          f"are skipped and the rest of the report is scored", file=sys.stderr)


def _line_contexts(path: str, data: object) -> dict[int, list[str]]:
    """line -> test ids from one file entry's "contexts", an object coverage.py
    writes from a line number to the context names that ran it."""
    raw = _file_entry(path, data).get("contexts", {})
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: contexts holds {json_kind(raw)}, not an object; {_REGENERATE}")
    out = {}
    for line, contexts in raw.items():
        tests = _context_tests(path, line, contexts)
        if tests:
            out[_context_line(path, line)] = tests
    return out


def _context_tests(path: str, line: str, contexts: object) -> list[str]:
    key = f"contexts[{line!r}]"
    if not isinstance(contexts, list):
        raise ValueError(f"{path}: {key} holds {json_kind(contexts)}, not a list of context "
                         f"names; {_REGENERATE}")
    _require_entries(path, key, contexts, lambda name: isinstance(name, str), "a context name")
    return sorted({c.split("|")[0] for c in contexts if c})


def _context_line(path: str, line: str) -> int:
    if not (line.isascii() and line.isdigit()):
        raise ValueError(f"{path}: contexts key {line!r} is not a line number; {_REGENERATE}")
    return int(line)


# --- path keys ---------------------------------------------------------------

def lane_prefix(path_prefix: str) -> str:
    """The lane's `path_prefix` as it is actually glued onto a measured path."""
    return (path_prefix.rstrip("/") + "/") if path_prefix else ""


def measured_key(prefix: str, raw_path: str) -> str:
    """The repository path one report file key names, under a lane_prefix.

    The one spelling of the forward rule. The prefix goes onto a relative key
    only: glued onto `/other/checkout/a.py`, it made `backend//other/...`, a
    key a `backend` scope claimed. An absolute key stays as written, and the
    reader records it with its reason (`read`)."""
    key = file_separators(raw_path)
    return key if absolute(key) else prefix + key


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
        self.branch_counted = 0
        self.branchless: list[str] = []
        self.absolute: list[str] = []

    def add(self, prefix: str, raw_path: str, data: object) -> None:
        self.total += 1
        path = measured_key(prefix, raw_path)
        if absolute(path):
            self.absolute.append(path)
        self.dead[path] = _dead_lines(path, data)
        if not has_regions(data):
            self.regionless.append(raw_path)
            return
        self.per_file[path] = _read_functions(path, data)
        branchless = _branchless(path, data)
        self.branchless += branchless
        self.branch_counted += len(self.per_file[path]) - len(branchless)


def _dead_lines(path: str, data: object) -> set[int]:
    """The lines one file entry says never ran, refused by the file's name
    when the entry or its missing_lines is not the shape coverage.py writes."""
    return set(_line_list(path, _file_entry(path, data), "missing_lines"))


def _file_entry(path: str, data: object) -> dict:
    """One file's entry under "files", which coverage.py always writes as an
    object."""
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the file entry holds {json_kind(data)}, not an object; "
                         f"{_REGENERATE}")
    return data


def _read_functions(path: str, data: dict) -> list[FnCoverage]:
    """One file's functions, or a refusal that names the file: a function's
    name alone does not say which of a report's files to look in."""
    try:
        return _file_functions(data)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _branchless(path: str, data: dict) -> list[str]:
    """`path: name` for each function whose summary carries no branch counts."""
    return [f"{path}: {name}" for name, fn in data["functions"].items()
            if name and "num_branches" not in fn["summary"]]


def _coveragepy_both(w, prefix: str, label: str,
                     written: list[str] | None = None) -> tuple[dict, dict]:
    """path -> function coverage, salvaging the same way the whole-document
    parser does: a statement-based downgrade with no branch data, and files with
    no regions skipped rather than fatal. `written` takes each absolute key."""
    files, branch = _Files(), False
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "member":
            branch = branch or _meta_has_branch(key, value)
            continue
        files.add(prefix, key, value)
    # Same order as the whole-document reader: regions decide first, so the two
    # cannot answer one report differently.
    judge_regions(files.regionless, files.total, label)
    _judge_branch_counts(branch, files, label)
    if written is not None:
        written.extend(files.absolute)
    return files.per_file, files.dead


def parse_coveragepy_both_file(path: Path | str, *, path_prefix: str,
                               chunk: int = covstream.CHUNK, label: str = "",
                               absolute_keys: list[str] | None = None
                               ) -> tuple[dict, dict, str]:
    """Function coverage, missing lines, and byte digest from one report walk.
    `absolute_keys` takes each absolute key the report wrote, `/` between
    directories and no prefix on it."""
    prefix = lane_prefix(path_prefix)
    (per_file, dead), digest = covstream.read_walk(
        path, lambda w: _coveragepy_both(w, prefix, label, absolute_keys),
        f"{_BAD_REPORT} {path}", chunk)
    return per_file, dead, digest


def _coveragepy_missing(w, prefix: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "sub":
            path = measured_key(prefix, key)
            out[path] = _dead_lines(path, value)
    return out


def parse_coveragepy_missing_file(path: Path | str, *, path_prefix: str,
                                  chunk: int = covstream.CHUNK) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    prefix = lane_prefix(path_prefix)
    missing, _ = covstream.read_walk(
        path, lambda w: _coveragepy_missing(w, prefix), f"{_BAD_REPORT} {path}", chunk)
    return missing


def _coveragepy_contexts(w, prefix: str, source_path: str, spell: Callable[[str], str]) -> dict:
    selected = {}
    for key, value, kind in covstream.walk_report(w, "files"):
        if kind == "sub" and spell(measured_key(prefix, key)) == source_path:
            selected = _line_contexts(source_path, value)
    return selected


def _as_written(key: str) -> str:
    return key


def parse_coveragepy_contexts_file(path: Path | str, *, path_prefix: str,
                                   source_path: str, chunk: int = covstream.CHUNK,
                                   spell: Callable[[str], str] = _as_written
                                   ) -> dict[int, list[str]]:
    """One repository path's line contexts, after validating the whole report.
    `spell` turns a measured key into git's spelling before the compare."""
    prefix = lane_prefix(path_prefix)
    selected, _ = covstream.read_walk(
        path, lambda w: _coveragepy_contexts(w, prefix, source_path, spell),
        f"{_BAD_REPORT} {path}", chunk)
    return selected


# --- the adapter a lane reads through ------------------------------------------
#
# The walk takes path_prefix and takes no repo root, so a refusal is about the
# environment the lane binds to or a report copied in from elsewhere. path_prefix
# only prepends, so it rebases neither another tree's path nor this tree's path
# spelled absolutely: that one is the runner's own switch. The adapter then
# spells each root-relative key as git does (_speller).

WRONG_TREE_FIX = ("Point the lane at this checkout's own environment (a bare "
                  "`python -m pytest` binds to whichever venv the shell has active - run "
                  "it through the project's manager, `uv run python -m pytest ...`), or "
                  "rerun the lane here rather than reusing a report copied from another "
                  "checkout; path_prefix only prepends, so it cannot rebase these paths")
ABSOLUTE_FIX = ("Make the runner write relative paths: `relative_files = true` "
                "under `[tool.coverage.run]` in pyproject.toml, or "
                "`[run] relative_files = true` in .coveragerc, then rerun the lane")
UNMEASURED_READING = "or the runner reports paths this lane needs path_prefix to rebase"
TAKES_PATH_PREFIX = True


def _speller(root: Path) -> Callable[[str], str]:
    """A measured key as git spells the file. A root-relative key takes the
    letter case its directories list (repopath's reported entry): coverage.py on
    macOS keys a file in the case the import system handed it, and `PKG/mod.py`
    named no tracked file. An absolute key stays as written, because
    relative_files is the runner's own switch and the wrong-tree check names it.
    A key the lane's child wrote in a locale that is not UTF-8 first reads back
    as the UTF-8 name it spells (_utf8_speller)."""
    relative = Reported(root).relative
    in_utf8 = _utf8_speller(root)

    def spell(key: str) -> str:
        key = in_utf8(key)
        return key if absolute(key) else relative(key)
    return spell


def _respelled(per_key: dict, spell: Callable[[str], str]) -> dict:
    return {spell(key): value for key, value in per_key.items()}


def read(lane: Lane, root: Path, artifact: Path, *,
         unplaced: dict[str, Unplaced] | None = None) -> tuple[dict, dict, str]:
    """The lane's function coverage, dead lines and artifact digest, one walk.
    `unplaced` takes each absolute key, under the measured key it reads as,
    with the placing step's reason: KEPT_ABSOLUTE for one in this checkout,
    because this reader never rebases an absolute key (relative_files is the
    runner's own switch). The placing step is asked once a folder, and not at
    all for a report whose keys are all relative."""
    written: list[str] = []
    per_file, dead, digest = parse_coveragepy_both_file(
        artifact, path_prefix=lane.path_prefix, label=f"lane {lane.name!r}",
        absolute_keys=written)
    spell = _speller(root)
    if unplaced is not None:
        unplaced.update(_reasons(root, written, spell))
    return _respelled(per_file, spell), _respelled(dead, spell), digest


def _reasons(root: Path, written: list[str], spell: Callable[[str], str]) -> dict:
    placing = Placing(root)
    return {spell(key): _kept(placing.placed(key)) for key in written}


def _kept(placed: str | Unplaced) -> Unplaced:
    """The reason an absolute key stays as written: the placing step's, or
    KEPT_ABSOLUTE when it lands in this checkout."""
    return placed if isinstance(placed, Unplaced) else Unplaced.KEPT_ABSOLUTE


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines coverage.py reports as never run, per measured file."""
    return _respelled(parse_coveragepy_missing_file(artifact, path_prefix=lane.path_prefix),
                      _speller(root))


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """line -> test ids for one repository path, under the key the lane's child
    wrote for it in git's letter case or in a locale that is not UTF-8."""
    return parse_coveragepy_contexts_file(artifact, path_prefix=lane.path_prefix,
                                          source_path=source_path, spell=_speller(root))


def _child_codec() -> str | None:
    """The codec a lane's own Python names files in, when that is not UTF-8: a
    POSIX locale such as en_US.ISO-8859-1, which the child keeps while crapkit
    restarts itself in UTF-8 mode. Windows and macOS name files in UTF-8, and
    Python reads the C locale as UTF-8."""
    if os.name != "posix" or sys.platform == "darwin":
        return None
    codec = codecs.lookup(locale.getencoding()).name
    return None if codec in ("utf-8", "ascii") else codec


def _utf8_speller(root: Path) -> Callable[[str], str]:
    """A report key, written in the child's locale, read back as the repository
    path it names; as written when the child names files in UTF-8."""
    codec = _child_codec()
    if codec is None:
        return _as_written
    return lambda key: _respelled_key(root, key, codec)


def _respelled_key(root: Path, key: str, codec: str) -> str:
    """`key` as the UTF-8 name its bytes spell, when that file exists and the
    key as written names none; a file really named `cafÃ©.py` keeps its key."""
    if key.isascii() or (root / key).exists():
        return key
    named = utf8_spelling(key, codec)
    return named if named and (root / named).exists() else key
