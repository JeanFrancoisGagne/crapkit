"""clang-tidy (LLVM 23.1.2) and OCLint 26.02 over a written compile_commands.json.

compile_commands() writes one entry per file, so every file, header or not,
is its own translation unit and each tool reports a function once, in the
file that defines it. The language follows the suffix (`.h` takes the
sample's header language), `-I include` is added when the root has one, and
implicit function declarations stay warnings, so a C probe that calls an
undeclared helper still compiles.

tidy_cognitive() runs one clang-tidy process over the file list with
readability-function-cognitive-complexity at Threshold 0, which prints
`<file>:<line>:<col>: warning: function '<name>' has cognitive complexity of N`
on the function name's line for every function scoring 1 or more.

tidy_nesting() reads readability-function-size's NestingThreshold. At
threshold t it notes `nesting level <t+1> starts here` at each compound
statement t+1 braces deep, the function body being level 1. It prints no
maximum, so the sweep raises t over the files that still report until none
does, and every note comes back with its level.

oclint() runs one OCLint process with HighCyclomaticComplexity at 1 and
DeepNestedBlock at 0. It prints `<file>:<line>:<col>: high cyclomatic
complexity [size|P2] Cyclomatic Complexity Number N exceeds limit of 1` on
the line the declaration starts (a template line, a storage class), and
`deep nested block [size|P3] Block depth of N exceeds limit of 0` on every
compound statement, N being the most braces deep the statement reaches,
itself included.

A file a tool cannot compile comes back in `failed`. clang-tidy names it in
`Error while processing <file>.`. OCLint lists each error under the file it
sits in, a header too, so every listed file is compiled alone to learn which
failed, and the total is checked against the count of files its summary says
it analyzed. Answers carry
paths relative to root. No crapkit import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

import hang_guard

LANGUAGES = {".c": "c", ".cc": "c++", ".cpp": "c++", ".cxx": "c++", ".hpp": "c++",
             ".m": "objective-c", ".mm": "objective-c++"}
STANDARD = {"c": "-std=c11", "c++": "-std=c++17", "objective-c": "-std=c11",
            "objective-c++": "-std=c++17"}
QUIET = ["-Wno-everything", "-Wno-implicit-function-declaration"]
DIAGNOSTIC = re.compile(r"^(/[^:]+|[^/:][^:]*):(\d+):(\d+): (warning|note|error)?:? ?(.*)$")
COGNITIVE = re.compile(r"has cognitive complexity of (\d+) \(threshold")
LEVEL = re.compile(r"^nesting level (\d+) starts here")
TIDY_FAILED = re.compile(r"^Error while processing (.+)\.$", re.MULTILINE)
OCLINT_CCN = re.compile(r"Cyclomatic Complexity Number (\d+) exceeds")
OCLINT_DEPTH = re.compile(r"Block depth of (\d+) exceeds")
OCLINT_FILES = re.compile(r"Summary: TotalFiles=(\d+) ")
UNLIMITED = ["-max-priority-1=1000000", "-max-priority-2=1000000", "-max-priority-3=1000000"]


@dataclass
class Report:
    answers: list = field(default_factory=list)  # (path, line, number)
    failed: set = field(default_factory=set)  # paths the tool could not compile


def _relative(root: Path, path: str):
    """path relative to root, or None for a file outside it (a system header)."""
    place, base = (root / path).resolve(), root.resolve()
    return place.relative_to(base).as_posix() if place.is_relative_to(base) else None


def compile_commands(root: Path, paths: list, header: str) -> None:
    include = ["-I", str(root / "include")] if (root / "include").is_dir() else []
    entries = []
    for path in paths:
        language = LANGUAGES.get(Path(path).suffix.lower(), header)
        entries.append({"directory": str(root), "file": str(root / path),
                        "arguments": ["clang", "-x", language, STANDARD[language], *include,
                                      *QUIET, "-c", str(root / path)]})
    (root / "compile_commands.json").write_text(json.dumps(entries), encoding="utf-8")


def _matches(text: str, pattern) -> list:
    """(path, line, number) of each diagnostic line whose message matches."""
    lines = filter(None, map(DIAGNOSTIC.match, text.splitlines()))
    return [(m[1], int(m[2]), int(hit[1])) for m in lines for hit in [pattern.search(m[5])]
            if hit]


def _found(root: Path, text: str, pattern) -> list:
    """(path, line, number) for every matching diagnostic in a file under root."""
    placed = [(_relative(root, path), line, number)
              for path, line, number in _matches(text, pattern)]
    return [hit for hit in placed if hit[0]]


def _tidy(root: Path, paths: list, checks: str, options: str, pattern) -> Report:
    done = hang_guard.run(["clang-tidy", "-p", str(root), f"--checks=-*,{checks}",
                           f"--config={{CheckOptions: {{{options}}}}}",
                           *[str(root / path) for path in paths]],
                          cwd=root, text=True, encoding="utf-8", errors="replace")
    said = done.stdout + done.stderr
    failed = {_relative(root, path) for path in TIDY_FAILED.findall(said)}
    return Report([hit for hit in _found(root, said, pattern) if hit[0] not in failed], failed)


def tidy_cognitive(root: Path, paths: list) -> Report:
    return _tidy(root, paths, "readability-function-cognitive-complexity",
                 "readability-function-cognitive-complexity.Threshold: 0", COGNITIVE)


def tidy_nesting(root: Path, paths: list) -> Report:
    """Every note `nesting level N starts here`, as (path, line, N), N from 1 up."""
    report, threshold, left = Report(), 0, list(paths)
    while left:
        step = _tidy(root, left, "readability-function-size",
                     f"readability-function-size.NestingThreshold: {threshold}", LEVEL)
        report.answers += step.answers
        report.failed |= step.failed
        left = sorted({path for path, _, _ in step.answers})
        threshold += 1
    return report


def _oclint_run(root: Path, paths: list):
    return hang_guard.run(["oclint", "-p", str(root), *UNLIMITED,
                           "-rule", "HighCyclomaticComplexity", "-rc", "CYCLOMATIC_COMPLEXITY=1",
                           "-rule", "DeepNestedBlock", "-rc", "NESTED_BLOCK_DEPTH=0",
                           *[str(root / path) for path in paths]],
                          cwd=root, text=True, encoding="utf-8", errors="replace")


def _analyzed(done) -> int:
    assert done.returncode in (0, 5, 6), done.stdout[-2000:] + done.stderr
    return int(OCLINT_FILES.search(done.stdout)[1])


def _oclint_failed(root: Path, paths: list, done) -> set:
    """The files OCLint could not compile. Its `Compiler Errors:` list names the
    file each error sits in, a header too, so each listed file that is also a
    translation unit is compiled alone to learn whether it is the one that failed,
    and the total is checked against the summary's count of files analyzed."""
    errors = done.stdout.partition("Compiler Errors:")[2].partition("OCLint Report")[0]
    named = {_relative(root, m[1]) for m in map(DIAGNOSTIC.match, errors.splitlines()) if m}
    failed = {path for path in sorted(named & set(paths))
              if _analyzed(_oclint_run(root, [path])) == 0}
    assert len(paths) - _analyzed(done) == len(failed), (failed, errors[-2000:])
    return failed


def oclint(root: Path, paths: list) -> tuple:
    """(cyclomatic complexity report, block depth report)."""
    done = _oclint_run(root, paths)
    failed = _oclint_failed(root, paths, done)
    report = done.stdout.partition("OCLint Report")[2]
    return (Report(_found(root, report, OCLINT_CCN), failed),
            Report(_found(root, report, OCLINT_DEPTH), failed))
