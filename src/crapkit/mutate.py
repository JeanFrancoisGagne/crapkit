"""Line-based mutant generation. Pure: text + changed lines in, mutants out.

Diff-scoped by design: mutating a whole repo is a research project, mutating
the lines a change touched is a review step. Operators flip comparisons,
boolean connectives, and boolean literals — the mutants that catch a test
suite asserting nothing. String literals are masked and comment lines skipped:
a survivor in dead text would erode trust in the survivor list.
"""
from __future__ import annotations

import re
from typing import NamedTuple


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
    ops = _OPS.get(language, _OPS["typescript"])
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
