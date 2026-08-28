"""Line-based mutant generation. Pure: text + changed lines in, mutants out.

Diff-scoped by design: mutating a whole repo is a research project, mutating
the lines a change touched is a review step. Operators flip comparisons,
boolean connectives, and boolean literals — the mutants that catch a test
suite asserting nothing. String literals are masked and comment lines skipped:
a survivor in dead text would erode trust in the survivor list.

The language decides the table, and two languages have no table at all. Shell
and PowerShell both spell redirection with the same `<` and `>` this module
treats as comparisons, so both are refused rather than mutated — see
`UNMUTABLE` for the measurement.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from .errors import ToolError


class Mutant(NamedTuple):
    path: str
    line: int          # 1-indexed
    original: str      # the whole original line
    mutated: str       # the whole mutated line
    op: str            # "a -> b"


# source token -> mutation targets. Negation flips plus boundary shifts —
# the boundary mutant (> vs >=) is the one an off-by-one test hole misses.
# Word-ish operators bind on spaces to dodge identifiers like Sand/oreo.
_OPS = {
    "python": {"==": ("!=",), "!=": ("==",), "<=": ("<", ">"), ">=": (">", "<"),
               "<": ("<=", ">="), ">": (">=", "<="),
               " and ": (" or ",), " or ": (" and ",), "True": ("False",), "False": ("True",)},
    "typescript": {"===": ("!==",), "!==": ("===",), "==": ("!=",), "!=": ("==",),
                   "<=": ("<", ">"), ">=": (">", "<"), "<": ("<=", ">="), ">": (">=", "<="),
                   "&&": ("||",), "||": ("&&",), "true": ("false",), "false": ("true",)},
}
# a short token matching INSIDE one of these is not that operator (== in ===,
# > in => arrows, < in <=, < in a Swift half-open range): skip the occurrence
# entirely. `0..<b` mutated to `0..<=b` does not compile, so the mutant dies on
# the compiler and reads as killed by a test that never ran.
_PROTECT = ("===", "!==", "==", "!=", "<=", ">=", "=>", "->", "..<", "...")
_COMMENT_PREFIXES = ("#", "//", "/*", "*")
_STRING_RE = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")

# The suffixes that pick something other than the C-family table. Rust is named
# here rather than left to the default because it is a decision: it spells `==`,
# `!=`, `<`, `>`, `&&`, `||`, `true` and `false` exactly as C does, and its `..`
# and `..=` ranges contain none of those tokens, so they need no `_PROTECT` entry
# the way Swift's `0..<n` did (measured on a range-heavy function: zero mutants).
_LANGUAGE_BY_SUFFIX = {".py": "python", ".rs": "rust", ".sh": "shell", ".bash": "shell",
                       ".ps1": "powershell", ".psm1": "powershell"}

# Languages this module refuses, with the reason a user reads. Shell's `<` and
# `>` are redirections: on a 9-line function 8 of 11 mutants flipped one
# (`echo "$n" > out.txt` became `>= out.txt`, `cat a >> log` became `>=>`), which
# is either a syntax error the runner scores as a kill or a write to a file
# named `=`. Shell's real comparisons are `-eq`/`-gt`/`-lt` and this table
# carries none of them, so refusing costs nothing that ever worked.
UNMUTABLE = {
    "shell": "'<' and '>' are redirections in shell, not comparisons, and its "
             "comparisons ('-eq', '-gt', '-lt') are no part of crapkit's operator table",
    "powershell": "'<' and '>' are redirections in PowerShell, not comparisons, and its "
                  "comparisons ('-eq', '-gt', '-lt') are no part of crapkit's operator "
                  "table; '-and'/'-or' are its boolean connectives and are not either",
}


def mutation_language(rel_path: str) -> str:
    """The language whose operator table this path's mutants come from.

    Everything unnamed answers `typescript`, which is what the C-family table is:
    Swift, Go and Kotlin all spell their operators that way.
    """
    for suffix, language in _LANGUAGE_BY_SUFFIX.items():
        if rel_path.endswith(suffix):
            return language
    return "typescript"


def refusal(language: str) -> str | None:
    """Why crapkit will not mutate this language, or None when it will."""
    return UNMUTABLE.get(language)


def _masked(line: str) -> str:
    """String literal contents blanked, length preserved, so operator offsets
    found on the mask apply to the real line."""
    return _STRING_RE.sub(lambda m: " " * len(m.group()), line)


def _occurrences(mask: str, needle: str) -> list[int]:
    out, at = [], mask.find(needle)
    while at != -1:
        out.append(at)
        at = mask.find(needle, at + 1)
    return out


def _covered_by_longer(mask: str, at: int, token: str) -> bool:
    for p in _PROTECT:
        if len(p) <= len(token):
            continue
        for start in range(max(0, at - len(p) + 1), at + 1):
            if mask.startswith(p, start):
                return True
    return False


def _line_mutations(line: str, ops: dict) -> list[tuple[str, str]]:
    """(mutated_line, op_label) for every operator occurrence outside strings."""
    mask = _masked(line)
    out = []
    for source, targets in ops.items():
        for at in _occurrences(mask, source):
            if _covered_by_longer(mask, at, source):
                continue
            for target in targets:
                out.append((line[:at] + target + line[at + len(source):],
                            f"{source.strip()} -> {target.strip()}"))
    return out


def file_mutants(text: str, changed_lines: set[int] | None, language: str) -> list[Mutant]:
    """Every mutant for this text, or a ToolError for a language in `UNMUTABLE`.

    Loud, not empty: a wrong mutant comes back as a survivor or a kill, and both
    read as a measurement. The caller decides whether to skip the file or stop.
    """
    reason = refusal(language)
    if reason:
        raise ToolError(f"crapkit does not mutate {language}: {reason}")
    return _mutants(text, changed_lines, _OPS.get(language, _OPS["typescript"]))


def _mutants(text: str, changed_lines: set[int] | None, ops: dict) -> list[Mutant]:
    mutants = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if changed_lines is not None and line_no not in changed_lines:
            continue
        if line.strip().startswith(_COMMENT_PREFIXES):
            continue
        for mutated, op in _line_mutations(line, ops):
            mutants.append(Mutant("", line_no, line, mutated, op))
    return mutants


def apply_mutant(text: str, mutant: Mutant) -> str:
    lines = text.splitlines(keepends=True)
    eol = "\n" if lines[mutant.line - 1].endswith("\n") else ""
    lines[mutant.line - 1] = mutant.mutated + eol
    return "".join(lines)
