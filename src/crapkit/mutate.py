"""Mutant generation from language tokens and changed source lines.

Corpus-scoped too: `partition_by_corpus` puts the diff's file list through
the predicate scoring uses before a mutant is placed, so a test file never
grows one.

Diff-scoped by design: mutating a whole repo is a research project, mutating
the lines a change touched is a review step. Operators flip comparisons,
boolean connectives, and boolean literals. Language lexers exclude identifiers,
strings, comments and markup while retaining code inside template expressions.

The language decides the table, and two languages have no table at all. Shell
and PowerShell both spell redirection with the same `<` and `>` this module
treats as comparisons, so both are refused rather than mutated — see
`UNMUTABLE` for the measurement.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from itertools import accumulate
import re
from typing import NamedTuple

from .errors import ToolError
from .universe import LANGUAGE_EXTENSIONS, assign_files


class Mutant(NamedTuple):
    path: str
    line: int          # 1-indexed
    original: str      # the whole original line
    mutated: str       # the whole mutated line
    op: str            # "a -> b"


# source token -> mutation targets. Negation flips plus boundary shifts —
# the boundary mutant (> vs >=) is the one an off-by-one test hole misses.
# Word operators retain their historical display spelling in this table.
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

# Keep lexer selection aligned with the source languages the corpus admits.
_LANGUAGE_BY_SUFFIX = {suffix: language for language, suffixes in LANGUAGE_EXTENSIONS.items()
                       for suffix in suffixes}
_LEXEMES = re.compile(r"\w+|===|!==|==|!=|<=|>=|<<=?|>>=?|&&|\|\||->|=>|\.\.<|\.\.\.|[<>]")
_SYNTAX = re.compile(r"\w+|::|->|=>|<=|>=|==|!=|&&|\|\||<<|\.\.<|\.\.\.|[^\s]")
_TYPE_ARGUMENTS = re.compile(r"(?:[\w\s:,.?*\[\]'<>]|&(?!&))+")
_ANGLE_LANGUAGES = {"typescript", "tsx", "vue", "cpp", "rust", "java", "swift", "objectivec"}
_TYPE_CONTEXT = {":", "::", "->", "as", "new", "class", "struct", "interface", "type",
                 "fn", "function", "impl", "typedef", "using", "extends", "implements"}

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

# Why a path in the diff grew no mutants, said beside the refusal reasons above
# because it is the same kind of sentence: a measurement crapkit declined.
OUTSIDE_CORPUS = "outside the scored corpus (scopes, excludes, test files, max_file_bytes)"


def partition_by_corpus(targets: dict, cfg, *, size_of=None) -> tuple[dict, list[str]]:
    """The targets scoring would score, and the paths it would not.

    One predicate, `universe.assign_files`, the call `coverage` makes over
    `git ls-files`: scopes by path and language, the exclude globs, the byte
    ceiling, the test-file cut. A mutant in a test measures nothing (on one
    review run 6 of 9 mutants landed in tests/test_tax.py and the survivor was
    an assertion), so a test file in the diff, or named by `--files`, comes back
    in the outside list instead of growing mutants. Kept targets keep the
    caller's order; the outside list is sorted. `size_of` is injected the way
    `scan_files` takes it, so this stays pure.
    """
    admitted = set().union(*assign_files(sorted(targets), cfg, size_of=size_of).values())
    kept = {rel: lines for rel, lines in targets.items() if rel in admitted}
    return kept, sorted(set(targets) - admitted)


def mutation_language(rel_path: str) -> str:
    """The language whose operator table this path's mutants come from.

    Everything unnamed answers `typescript`, which is what the C-family table is.
    Swift, Go, Vue and Zig spell their operators that way, and C, C++,
    Objective-C and Java are where the spelling came from — none of them needs a
    table of its own, and a table per label would be four copies to drift apart.
    """
    for suffix, language in _LANGUAGE_BY_SUFFIX.items():
        if rel_path.endswith(suffix):
            return language
    return "typescript"


def refusal(language: str) -> str | None:
    """Why crapkit will not mutate this language, or None when it will."""
    return UNMUTABLE.get(language)


def _covered_by_longer(mask: str, at: int, token: str) -> bool:
    for p in _PROTECT:
        if len(p) <= len(token):
            continue
        for start in range(max(0, at - len(p) + 1), at + 1):
            if mask.startswith(p, start):
                return True
    return False


def _line_mutations(line: str, tokens: list, ops: dict) -> list[tuple[str, str]]:
    """Mutate whole code tokens, in the existing operator-table order."""
    out = []
    for source, targets in ops.items():
        for at, token in tokens:
            if token != source or _covered_by_longer(line, at, source):
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
    return _mutants(text, changed_lines, language)


def _code_tokens(text: str, language: str):
    """Pygments ships with lizard and keeps language-specific string/comment state."""
    from pygments.lexers import get_lexer_by_name
    aliases = {"cpp": "c++", "objectivec": "objective-c"}
    lexed = list(get_lexer_by_name(aliases.get(language, language)).get_tokens_unprocessed(text))
    mask, syntax = _code_masks(text, lexed, language)
    protected, ambiguous = _type_angles(syntax, lexed, language)
    tokens = ((match.start(), match.group()) for match in _LEXEMES.finditer(mask)
              if match.start() not in protected)
    return tokens, ambiguous


def _code_masks(text: str, lexed: list, language: str) -> tuple[str, str]:
    mask, syntax = [" "] * len(text), [" "] * len(text)
    for at, kind, value in lexed:
        if _code_kind(kind, language):
            mask[at:at + len(value)] = value
        if _syntax_kind(kind):
            syntax[at:at + len(value)] = value
    return "".join(mask), "".join(syntax)


def _syntax_kind(kind) -> bool:
    from pygments.token import Keyword, Name, Number, Operator, Punctuation

    return any(kind in category for category in (Keyword, Name, Number, Operator, Punctuation))


def _syntax_depths(syntax: str) -> list:
    depth, tokens = 0, []
    for match in _SYNTAX.finditer(syntax):
        word = match.group()
        depth += {"(": 1, "[": 1, "{": 1}.get(word, 0)
        tokens.append((match.start(), word, depth))
        depth -= {")": 1, "]": 1, "}": 1}.get(word, 0)
    return tokens


def _close_angle(stack: list, index: int, tokens: list):
    depth = tokens[index][2]
    while stack and tokens[stack[-1]][2] > depth:
        stack.pop()
    if stack and tokens[stack[-1]][2] == depth:
        return stack.pop(), index
    return None


def _angle_pairs(tokens: list):
    stack = []
    for index, (_, word, depth) in enumerate(tokens):
        if word in (";", "{", "}"):
            _discard_angles(stack, tokens, depth)
        if word == "<":
            stack.append(index)
        elif word == ">":
            pair = _close_angle(stack, index, tokens)
            if pair is not None:
                yield pair


def _discard_angles(stack: list, tokens: list, depth: int) -> None:
    stack[:] = [index for index in stack if tokens[index][2] < depth]


def _type_prefix(tokens: list, start: int, typed: set) -> bool:
    if start and tokens[start - 1][0] in typed:
        return True
    if start >= 2 and tokens[start - 2][1] in _TYPE_CONTEXT:
        return True
    return False


def _angle_kind(syntax: str, tokens: list, start: int, end: int, typed: set, language: str) -> str:
    if _type_prefix(tokens, start, typed):
        return "type"
    inside = syntax[tokens[start][0] + 1:tokens[end][0]]
    if not _TYPE_ARGUMENTS.fullmatch(inside):
        return "comparison"
    if any(at in typed for at, _, _ in tokens[start:end]):
        return "type"
    return "type" if _java_type_tail(tokens, end, language) else "ambiguous"


def _java_type_tail(tokens: list, end: int, language: str) -> bool:
    if language != "java" or end + 1 >= len(tokens):
        return False
    return bool(re.fullmatch(r"\w+", tokens[end + 1][1]))


def _type_angles(syntax: str, lexed: list, language: str) -> tuple[set, set]:
    from pygments.token import Keyword, Name

    protected, ambiguous = set(), set()
    if language not in _ANGLE_LANGUAGES:
        return protected, ambiguous
    typed = {at for at, kind, _ in lexed if kind in Keyword.Type or kind in Name.Builtin}
    tokens = _syntax_depths(syntax)
    for start, end in _angle_pairs(tokens):
        kind = _angle_kind(syntax, tokens, start, end, typed, language)
        _record_angles(tokens, start, end, kind, protected, ambiguous)
    return protected, ambiguous - protected


def _record_angles(tokens: list, start: int, end: int, kind: str, protected: set, ambiguous: set) -> None:
    if kind == "type":
        protected.update(at for at, word, depth in tokens[start:end + 1]
                         if word in ("<", ">") and depth == tokens[start][2])
    if kind == "ambiguous":
        ambiguous.update((tokens[start][0], tokens[end][0]))


def _code_kind(kind, language: str) -> bool:
    from pygments.token import Keyword, Name, Operator, Punctuation

    return kind in Keyword or kind in Name.Builtin or kind in Operator or (language == "go" and kind in Punctuation)


def _tokens_by_line(text: str, language: str, changed: set[int] | None) -> dict:
    starts = list(accumulate((len(line) for line in text.splitlines(keepends=True)), initial=0))
    by_line = defaultdict(list)
    tokens, ambiguous = _code_tokens(text, language)
    for at, token in tokens:
        line = bisect_right(starts, at)
        if changed is None or line in changed:
            if at in ambiguous:
                raise ToolError(f"ambiguous {language} angle syntax on line {line}: "
                                "cannot distinguish type arguments from comparisons")
            by_line[line].append((at - starts[line - 1], token))
    return by_line


def _mutants(text: str, changed_lines: set[int] | None, language: str) -> list[Mutant]:
    ops = {key.strip(): tuple(value.strip() for value in values)
           for key, values in _OPS.get(language, _OPS["typescript"]).items()}
    lines = text.splitlines()
    return [Mutant("", number, lines[number - 1], mutated, op)
            for number, tokens in sorted(_tokens_by_line(text, language, changed_lines).items())
            for mutated, op in _line_mutations(lines[number - 1], tokens, ops)]


def apply_mutant(text: str, mutant: Mutant) -> str:
    lines = text.splitlines(keepends=True)
    eol = "\n" if lines[mutant.line - 1].endswith("\n") else ""
    lines[mutant.line - 1] = mutant.mutated + eol
    return "".join(lines)
