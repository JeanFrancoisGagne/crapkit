"""Discovery helpers for the start-editing packet: who calls a function, which
tests sit beside a file, and which guide files govern it.

Three standalone questions, answered off the working tree and git's index. Every
answer is sorted and every lookup is batched: one `git grep` per call whatever
the length of the identifier list, one directory listing per directory whatever
the number of glob patterns. Nothing here imports the CLI.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from .errors import GitError

CALLER_CAP = 20
_GUIDES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")
_SUPPORT_PATTERNS = ("conftest.py", "*.test-support.*")
_SUPPORT_DIRS = ("fixtures", "factories")
_EXPORT_STARTS = ("export ", "export{", "import ", "import{", "from ",
                  "module.exports", "exports.")
_DEF_MODIFIERS = (r"(?:@|export\s+|default\s+|public\s+|private\s+|protected\s+"
                  r"|static\s+|async\s+|unsafe\s+|pub(?:\([^)]*\))?\s+)*")


class _Hit(NamedTuple):
    path: str
    line: int
    text: str


def _relative(path) -> PurePosixPath:
    """Repo-relative and posix, whichever slash the caller happened to hold."""
    return PurePosixPath(str(path).replace("\\", "/"))


def _read(root: Path, path: str) -> str:
    """One file's text, or "" when it is gone.

    utf-8 explicitly: a bare open() reads cp1252 on Windows and utf-8 in a
    container, which is one repo answering two ways.
    """
    try:
        return (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _upward(directory: PurePosixPath) -> list[PurePosixPath]:
    """The directory and every parent up to the repo root, nearest first."""
    return list(dict.fromkeys([directory, *directory.parents]))


def _guides_in(root: Path, directory: PurePosixPath) -> list[str]:
    return [str(directory / name) for name in _GUIDES
            if (root / directory / name).is_file()]


def nearest_guides(root, path) -> list[str]:
    """The AGENTS.md, CLAUDE.md and CONTRIBUTING.md files governing `path`.

    Walks up from the file's own directory to the repo root, nearest first, so
    the closest instruction an editor has to obey comes first. Within one
    directory the fixed name order breaks the tie.
    """
    root = Path(root)
    found: list[str] = []
    for directory in _upward(_relative(path).parent):
        found += _guides_in(root, directory)
    return found


# --- the tests and support files beside one source ----------------------------

def _entries(directory: Path) -> list[str]:
    """The file names in one directory, sorted; a directory that is not there is
    an empty answer, not a failure.

    THE batching seam. Every glob pattern is matched against this one list, so a
    directory is read once per call however many patterns ask about it.
    """
    try:
        with os.scandir(directory) as scan:
            return sorted(e.name for e in scan if e.is_file())
    except OSError:
        return []


def _matching(names: list[str], patterns: Iterable[str]) -> list[str]:
    return [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)]


def _under(directory: PurePosixPath, names: list[str]) -> list[str]:
    return [str(directory / n) for n in names]


def _test_patterns(stem: str, suffix: str) -> tuple[str, ...]:
    """The four sibling namings, in the source's own extension: send.ts asks
    about send*.test.ts and send*.spec.ts, parse.py about test_parse*.py and
    parse*_test.py. None of them can match the source itself."""
    return (f"{stem}*.test{suffix}", f"{stem}*.spec{suffix}",
            f"test_{stem}*{suffix}", f"{stem}*_test{suffix}")


def _nested_tests(root: Path, directory: PurePosixPath, stem: str) -> list[str]:
    """__tests__/send* — the sibling directory JS projects keep tests in."""
    nested = directory / "__tests__"
    return _under(nested, _matching(_entries(root / nested), (f"{stem}*",)))


def _support(root: Path, directory: PurePosixPath, own: list[str]) -> list[str]:
    """What a test in this directory leans on: the same-directory support names,
    plus everything one level down in fixtures/ and factories/."""
    found = _under(directory, _matching(own, _SUPPORT_PATTERNS))
    for name in _SUPPORT_DIRS:
        found += _under(directory / name, _entries(root / directory / name))
    return sorted(found)


def related_tests(root, path) -> dict[str, list[str]]:
    """The tests that sit beside `path`, and the support files they lean on.

    Sibling globs on the source stem in the source's own extension, plus the
    __tests__ directory next to it. Both lists are repo-relative and sorted, so
    two runs of a packet agree.
    """
    root = Path(root)
    rel = _relative(path)
    directory = rel.parent
    own = _entries(root / directory)
    tests = _under(directory, _matching(own, _test_patterns(rel.stem, rel.suffix)))
    return {"tests": sorted(tests + _nested_tests(root, directory, rel.stem)),
            "support": _support(root, directory, own)}


# --- who calls one identifier -------------------------------------------------

def _grep_output(root: Path, args: list[str]) -> str:
    """One `git grep`, run for its output.

    Exit 1 is git's way of saying nothing matched, which is an answer rather
    than a failure; anything above that is a broken command.
    """
    try:
        proc = subprocess.run(["git", "grep", *args], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if proc.returncode > 1:
        raise GitError(f"git grep failed in {root}: {proc.stderr.strip()}")
    return proc.stdout


def _flat_paths(scope_paths) -> list[str]:
    """crapkit's config holds scope paths as {scope: (path, ...)}; a caller with
    a plain list of paths means the same thing."""
    if isinstance(scope_paths, Mapping):
        return [p for paths in scope_paths.values() for p in paths]
    return list(scope_paths or ())


def _pathspecs(scope_paths) -> list[str]:
    return sorted(set(_flat_paths(scope_paths)))


def _grep_args(identifiers: list[str], scope_paths) -> list[str]:
    """-F because an identifier is a literal, and one -e per name because fixed
    strings have no alternation. However long the list, this is ONE command."""
    patterns = [arg for name in identifiers for arg in ("-e", name)]
    scoped = _pathspecs(scope_paths)
    limit = ["--", *scoped] if scoped else []
    return ["-n", "-I", "-F", "--full-name", "--no-color", *patterns, *limit]


def _is_test_path(path: str) -> bool:
    """Test file or not, by the rule cli.verifying._is_test_path applies: a
    test/tests/__tests__ directory segment, a test_ prefix, or a .test./.spec.
    infix. Written twice on purpose — this module imports nothing from the CLI —
    and pinned by a test that routes the same paths through both.
    """
    parts = path.lower().split("/")
    return (any(p in ("test", "tests", "__tests__") for p in parts[:-1])
            or parts[-1].startswith("test_")
            or ".test." in parts[-1] or ".spec." in parts[-1])


def _parse_hit(raw: str) -> _Hit | None:
    """`path:line:text`, the one shape `git grep -n` emits."""
    path, _, rest = raw.partition(":")
    number, _, text = rest.partition(":")
    return _Hit(path.replace("\\", "/"), int(number), text) if number.isdigit() else None


def _hits(root: Path, identifiers: list[str], scope_paths) -> list[_Hit]:
    """Every match for every name, out of ONE `git grep`.

    git names no pattern in its output, so which name a line answers is settled
    by the word match below, not by the command. Test files are dropped here: a
    call from a test is not a caller a change has to answer to.
    """
    lines = _grep_output(root, _grep_args(identifiers, scope_paths)).splitlines()
    parsed = [_parse_hit(line) for line in lines]
    return [h for h in parsed if h is not None and not _is_test_path(h.path)]


def _word(identifier: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(identifier) + r"(?![A-Za-z0-9_])")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _def_pattern(identifier: str) -> re.Pattern[str]:
    """A line that DEFINES the name: the keyword forms of every language family
    crapkit reads, plus the two headers that carry no keyword at all.

    `function` sits before `func`, `fun` and `fn` in the alternation so a
    JavaScript definition never matches on a prefix of its own keyword.

    Shell writes `name() {`, which the method-shorthand branch a class body needs
    already claims: over 259 real scripts holding 1,342 definition lines, 1,308
    are spelled that way and 28 use `function`. The last branch is for the
    subshell-bodied `name() (`, whose body opens with a paren the shorthand does
    not end on. It insists on EMPTY parens, so a curried call `f(a)(b)` at the
    start of a line is still a call.
    """
    name = re.escape(identifier)
    return re.compile(r"^\s*" + _DEF_MODIFIERS
                      + r"(?:(?:def|class|function|func|fun|fn|const|let|var)\s+" + name + r"\b"
                      + r"|" + name + r"\s*\([^)]*\)\s*[:{]"
                      + r"|" + name + r"\s*\(\s*\)\s*\()")


def _def_line(lines: list[str], identifier: str) -> int | None:
    pattern = _def_pattern(identifier)
    for number, line in enumerate(lines, start=1):
        if pattern.search(line):
            return number
    return None


def _span_end(lines: list[str], start: int) -> int:
    """The last line of the block opened at `start`: everything up to the next
    non-blank line indented no deeper than the definition.

    Indentation ends a Python body and a braced body alike — the closing brace
    sits at the definition's own indent, and a closing brace holds no
    identifier, so stopping just before it costs nothing.
    """
    opened = _indent(lines[start - 1])
    for i in range(start, len(lines)):
        if lines[i].strip() and _indent(lines[i]) <= opened:
            return i
    return len(lines)


def _defining_span(lines: list[str], identifier: str) -> tuple[int, int] | None:
    """The 1-based line range this file defines the name over, or None when it
    does not define it at all."""
    start = _def_line(lines, identifier)
    return None if start is None else (start, _span_end(lines, start))


def _is_export_line(stripped: str) -> bool:
    return stripped.startswith(_EXPORT_STARTS) or "__all__" in stripped


def _opens_a_list(stripped: str) -> bool:
    """A declaration whose names are on the FOLLOWING lines.

    The bracket is the last thing on the line and no argument list closed just
    before it, which is what separates `export {` and `__all__ = [` from
    `export function send(x) {`. Counting brackets instead swallowed every
    function body under an export line, and every local name in one read as
    exported.
    """
    return (stripped.endswith(("[", "(", "{"))
            and not stripped.rstrip("[({ ").endswith(")"))


def _closes_a_list(stripped: str) -> bool:
    """The closer opens its own line in every formatter that wraps a list."""
    return stripped.startswith(("]", ")", "}"))


def _export_lines(lines: list[str]) -> list[str]:
    """The file's export surface: the export and import statements, plus the
    body of a list one of them opens. Computed once per call and reused by every
    identifier in the batch."""
    out: list[str] = []
    inside = False
    for raw in lines:
        stripped = raw.strip()
        if not inside and not _is_export_line(stripped):
            continue
        out.append(stripped)
        inside = _opens_a_list(stripped) if not inside else not _closes_a_list(stripped)
    return out


def _exported(export_lines: list[str], identifier: str) -> bool:
    word = _word(identifier)
    return any(word.search(line) for line in export_lines)


def _in_span(hit: _Hit, path: str, span: tuple[int, int] | None) -> bool:
    """A definition and its own body are not calls to itself."""
    return span is not None and hit.path == path and span[0] <= hit.line <= span[1]


def _found(hits: list[_Hit], identifier: str, path: str,
           span: tuple[int, int] | None) -> list[dict]:
    word = _word(identifier)
    kept = [h for h in hits if word.search(h.text) and not _in_span(h, path, span)]
    kept.sort(key=lambda h: (h.path, h.line))
    return [{"path": h.path, "line": h.line} for h in kept]


def _answer(hits: list[_Hit], identifier: str, path: str, lines: list[str],
            exports: list[str]) -> dict:
    found = _found(hits, identifier, path, _defining_span(lines, identifier))
    return {"count": len(found), "callers": found[:CALLER_CAP],
            "exported": _exported(exports, identifier)}


def _names(identifier) -> list[str]:
    """The names to ask about, deduplicated in asked-about order.

    A blank one never gets through: `git grep -e ""` matches every line in the
    repo, and thousands of callers is a wrong answer rather than an error.
    """
    raw = [identifier] if isinstance(identifier, str) else list(identifier)
    return list(dict.fromkeys(n for n in raw if n and n.strip()))


def _empty_answer() -> dict:
    return {"count": 0, "callers": [], "exported": False}


def _answers(root: Path, scope_paths, path: str, names: list[str]) -> dict[str, dict]:
    """One grep and one read of the defining file, however many names.

    Nothing to ask about spawns nothing: `git grep` with no -e is a usage error.
    """
    if not names:
        return {}
    lines = _read(root, path).splitlines()
    exports = _export_lines(lines)
    hits = _hits(root, names, scope_paths)
    return {n: _answer(hits, n, path, lines, exports) for n in names}


def callers(root, scope_paths, path, identifier):
    """Who calls `identifier`, which `path` defines.

    ONE `git grep` per call whatever `identifier` is: a str asks about one name
    and returns that name's answer, a list asks about many and returns
    {name: answer}. The list form IS the cache a packet wants — twelve functions
    of one file cost one process and one read of that file, not twelve of each,
    and every name reads the same grep output.

    Neither test files nor the definition's own body count as callers, and each
    name is measured against its OWN span, so a helper called from the function
    above it in the same file is a caller. `count` is the whole number found;
    `callers` stops at 20.
    """
    answers = _answers(Path(root), scope_paths, str(_relative(path)),
                       _names(identifier))
    if not isinstance(identifier, str):
        return answers
    return answers.get(identifier, _empty_answer())
