"""Assign tracked files to scopes, apply exclusions, name what fell through. Pure.

The file universe itself comes from `git ls-files` in the shell layer; lizard is
always fed these explicit lists because its own directory walking descends
nested node_modules (measured hang).
"""
from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from typing import NamedTuple

from .config import Config, Scope

LANGUAGE_EXTENSIONS = {
    "typescript": (".ts",),
    "tsx": (".tsx",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "python": (".py",),
    "swift": (".swift",),
    "go": (".go",),
    "rust": (".rs",),
    "shell": (".sh", ".bash"),
    # Exactly lizard's CLikeReader.ext, and deliberately not one suffix more.
    # `.hh`, `.hxx` and `.ipp` are real C++ headers that no lizard reader
    # declares, and lizard answers an undeclared suffix with `get_reader_for(f)
    # or CLikeReader` — a silent fallback, right for those three by luck and
    # wrong for anything else. Every claim here rests on a declared mapping;
    # those three are an opt-in for whoever needs them.
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"),
    "objectivec": (".m", ".mm"),
    "vue": (".vue",),
    "java": (".java",),
    "zig": (".zig",),
    "powershell": (".ps1", ".psm1"),
}

# Test directories are excluded case-insensitively: Swift convention capitalizes Tests/.
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__)(/|$)", re.IGNORECASE)

# So are dot-directories, and for the same reason: `.github/`, `.cursor/` and
# `.specify/` hold helper scripts written in the repo's own languages, and
# `crapkit init` refuses to build a scope out of a directory whose name opens
# with a dot. Without this the two halves of the tool disagreed about whether
# .github/ is code — init left it unscoped, doctor FAILed the config init had
# just written for exactly that. A dot FILE stays in the corpus; only a
# directory component is hidden.
_DOT_DIR = re.compile(r"(^|/)\.[^/]+/")

_NEVER = re.compile(r"(?!x)x").match  # an empty glob list must exclude NOTHING


def _glob_regex(glob: str) -> str:
    """One glob as a regex. A leading `**/` is zero or more directories, so
    `**/dist/**` reaches a repo-root dist/ as well as web/dist/. fnmatch alone
    reads `**` as `*` and demands a directory in front, which is why 0.4.12
    wrote every default glob twice. The rest of the glob is fnmatch as written,
    so a hand-written root form (`dist/**`) keeps matching what it matched."""
    if glob.startswith("**/"):
        return r"(?:.*/)?" + fnmatch.translate(glob[3:])
    return fnmatch.translate(glob)


def exclude_matcher(globs: tuple[str, ...]) -> Callable[[str], re.Match[str] | None]:
    """The exclude globs as ONE compiled alternation, matched against a lowered path.

    `fnmatch.fnmatch` normcases BOTH arguments on every call, and on Windows
    that is an LCMapStringEx syscall per path per glob (measured: 593k calls,
    0.44s on a 31.6k-file repo) for a lowering the caller already did. Compile
    once, match once. Build it OUTSIDE any per-file loop.
    """
    if not globs:
        return _NEVER
    return re.compile("|".join(_glob_regex(g.lower()) for g in globs)).match


def excluded(path: str, match_glob: Callable[[str], re.Match[str] | None]) -> bool:
    return bool(_TEST_DIR.search(path) or _DOT_DIR.search(path) or match_glob(path.lower()))


def _source_extensions(languages: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(e for lang in languages for e in LANGUAGE_EXTENSIONS[lang])


# `str.endswith("")` is true of every path, so the extension arm costs the same
# whether a caller filters by language or not — no per-file branch to skip it.
ANY_EXTENSION = ("",)


class ScopeMatch(NamedTuple):
    """One DECLARED SCOPE PATH's tests, precomputed once per run.

    One entry per path rather than per scope, because depth is a property of the
    path: a scope declaring both `src` and `packages/web/src` claims a file
    under each at a different depth.
    """
    name: str
    path: str
    prefix: str
    extensions: tuple[str, ...]


def _ordered(matches) -> tuple[ScopeMatch, ...]:
    """Deepest declared path first; ties keep declaration order (sort is stable).

    Depth lives in the ORDER so that owning_scope stays a single pass that
    returns on its first hit. Ranking inside the loop would pay for every
    matcher on every path, and this loop runs once per file on a 31.6k-file scan.
    """
    return tuple(sorted(matches, key=lambda m: -len(m.path)))


def scope_matchers(scopes: tuple[Scope, ...]) -> tuple[ScopeMatch, ...]:
    """Matchers for the scored corpus: a scope claims a path only when one of its
    languages claims the extension too."""
    return _ordered(ScopeMatch(s.name, p.rstrip("/"), p.rstrip("/") + "/",
                               _source_extensions(s.languages))
                    for s in scopes for p in s.paths)


def path_matchers(scope_paths: dict[str, tuple[str, ...]]) -> tuple[ScopeMatch, ...]:
    """Matchers for the callers that ask about any file at all, whatever it is.

    Lane staleness counts a fixture, a .md or a config file under a scope, and
    `test-scoped` routes files whose language no scope declares, so both read
    ownership with the extension arm open.
    """
    return _ordered(ScopeMatch(name, p.rstrip("/"), p.rstrip("/") + "/", ANY_EXTENSION)
                    for name, paths in scope_paths.items() for p in paths)


# What a tracked file has to look like to count as a test when init and doctor
# ask whether a scope holds one: under a test directory, or named the way the
# runner conventions name a test (test_x.py, x_test.py, x.test.ts, x.spec.ts,
# and Go's x_test.go). A conftest.py configures tests and is none.
_TEST_NAME = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*\.(test|spec)\.[^/]+)$", re.IGNORECASE)


def is_test_file(path: str) -> bool:
    """Is this tracked path a test, by directory or by name?"""
    return bool(_TEST_DIR.search(path) or _TEST_NAME.search(path))


def scopes_with_tests(files, scope_paths: dict[str, tuple[str, ...]]) -> frozenset[str]:
    """The scopes whose declared paths hold at least one test file.

    Read with the extension arm open, the way lane staleness and `test-scoped`
    read ownership: a test is claimed by its path, whatever its language. Both
    `init` (which scoped-test form to write) and `doctor` (whether a {files}
    template can collect anything) ask here, so the two cannot disagree about
    one scope.
    """
    matchers = path_matchers(scope_paths)
    paths = [raw.replace("\\", "/") for raw in files]
    owners = (owning_scope(path, matchers) for path in paths if is_test_file(path))
    return frozenset(owner for owner in owners if owner is not None)


def owning_scope(path: str, matchers: tuple[ScopeMatch, ...]) -> str | None:
    """Name of the scope that claims path, or None when no scope does.

    The deepest declared path wins, so a nested scope beats the parent that also
    contains it — the rule README states for `test-scoped`. The scored corpus,
    lane staleness and the brief packet all ask here: when they answered
    separately, `brief` took a function's lane and test command from one scope
    and its ceiling from another.

    A scope matching by path but not by extension is skipped rather than ending
    the search, or two scopes sharing a prefix silently black-hole each other's
    files. The exact arm is what makes a scope that declares a FILE rather than
    a directory own that file.
    """
    for m in matchers:
        if (path == m.path or path.startswith(m.prefix)) and path.endswith(m.extensions):
            return m.name
    return None


class Universe(NamedTuple):
    """Every candidate file's verdict.

    A candidate is a non-excluded path some scope's language claims by
    extension. `unclaimed` is the set that used to vanish here: source in a
    declared language that no scope PATH owns, which then commits with zero
    gating. `oversized` names what the byte ceiling cut, with the sizes, so
    the skip is reported rather than silent.
    """
    by_scope: dict[str, list[str]]
    unclaimed: tuple[str, ...]
    oversized: tuple[tuple[str, int], ...]


def _candidate(path: str, matchers: tuple[ScopeMatch, ...]) -> tuple[str | None, bool]:
    """(owning scope or None, whether any scope's language claims the extension)."""
    owner = owning_scope(path, matchers)
    if owner is not None:
        return owner, True
    return None, any(path.endswith(m.extensions) for m in matchers)


def _candidates(files: list[str], cfg: Config,
                matchers: tuple[ScopeMatch, ...]) -> list[tuple[str, str | None]]:
    match_glob = exclude_matcher(cfg.exclude_globs)
    out = []
    for raw_path in files:
        path = raw_path.replace("\\", "/")
        if excluded(path, match_glob):
            continue
        owner, known_language = _candidate(path, matchers)
        if known_language:
            out.append((path, owner))
    return out


def _oversize(path: str, max_bytes: int | None, size_of) -> int | None:
    """The file's size when the byte ceiling puts it out of reach, else None.

    No limit means no stat: the size lookup is a syscall per file, and a repo
    without max_file_bytes must not pay for a rule it never set.
    """
    if max_bytes is None or size_of is None:
        return None
    size = size_of(path)
    return size if size > max_bytes else None


def _partition(candidates: list[tuple[str, str | None]], scopes: tuple[Scope, ...],
               max_bytes: int | None, size_of):
    assigned: dict[str, list[str]] = {s.name: [] for s in scopes}
    unclaimed, oversized = [], []
    for path, owner in candidates:
        size = _oversize(path, max_bytes, size_of)
        if size is not None:
            oversized.append((path, size))
        elif owner is not None:
            assigned[owner].append(path)
        else:
            unclaimed.append(path)
    return assigned, unclaimed, oversized


def scan_files(files: list[str], cfg: Config, *,
               size_of: Callable[[str], int] | None = None) -> Universe:
    """The whole verdict. `size_of` is injected so this stays pure; the shell
    layer passes a working-tree stat, and callers with no tree pass nothing."""
    assigned, unclaimed, oversized = _partition(
        _candidates(files, cfg, scope_matchers(cfg.scopes)),
        cfg.scopes, cfg.max_file_bytes, size_of)
    return Universe({name: sorted(paths) for name, paths in assigned.items()},
                    tuple(sorted(unclaimed)), tuple(sorted(oversized)))


def assign_files(files: list[str], cfg: Config, *,
                 size_of: Callable[[str], int] | None = None) -> dict[str, list[str]]:
    """Just the per-scope mapping, for callers with no use for the dropped sets."""
    return scan_files(files, cfg, size_of=size_of).by_scope
