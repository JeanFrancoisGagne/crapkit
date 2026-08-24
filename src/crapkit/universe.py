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
}

# Test directories are excluded case-insensitively: Swift convention capitalizes Tests/.
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__)(/|$)", re.IGNORECASE)

_NEVER = re.compile(r"(?!x)x").match  # an empty glob list must exclude NOTHING


def exclude_matcher(globs: tuple[str, ...]) -> Callable[[str], re.Match[str] | None]:
    """The exclude globs as ONE compiled alternation, matched against a lowered path.

    `fnmatch.fnmatch` normcases BOTH arguments on every call, and on Windows
    that is an LCMapStringEx syscall per path per glob (measured: 593k calls,
    0.44s on a 31.6k-file repo) for a lowering the caller already did. Compile
    once, match once. Build it OUTSIDE any per-file loop.
    """
    if not globs:
        return _NEVER
    return re.compile("|".join(fnmatch.translate(g.lower()) for g in globs)).match


def excluded(path: str, match_glob: Callable[[str], re.Match[str] | None]) -> bool:
    return bool(_TEST_DIR.search(path) or match_glob(path.lower()))


def _source_extensions(languages: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(e for lang in languages for e in LANGUAGE_EXTENSIONS[lang])


class _ScopeMatch(NamedTuple):
    """One scope's prefix and extension tests, precomputed once per run."""
    name: str
    exact: frozenset[str]
    prefixes: tuple[str, ...]
    extensions: tuple[str, ...]


def _scope_matchers(scopes: tuple[Scope, ...]) -> tuple[_ScopeMatch, ...]:
    return tuple(
        _ScopeMatch(s.name, frozenset(s.paths),
                    tuple(p.rstrip("/") + "/" for p in s.paths),
                    _source_extensions(s.languages))
        for s in scopes
    )


def _owning_scope(path: str, matchers: tuple[_ScopeMatch, ...]) -> str | None:
    """Name of the scope that claims path, or None when no scope does."""
    # First scope whose path prefix AND language extensions both match wins;
    # a prefix-only match must not stop the search or shared-prefix scopes
    # silently black-hole each other's files.
    for m in matchers:
        if path in m.exact or path.startswith(m.prefixes):
            if path.endswith(m.extensions):
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


def _candidate(path: str, matchers: tuple[_ScopeMatch, ...]) -> tuple[str | None, bool]:
    """(owning scope or None, whether any scope's language claims the extension)."""
    owner = _owning_scope(path, matchers)
    if owner is not None:
        return owner, True
    return None, any(path.endswith(m.extensions) for m in matchers)


def _candidates(files: list[str], cfg: Config,
                matchers: tuple[_ScopeMatch, ...]) -> list[tuple[str, str | None]]:
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
        _candidates(files, cfg, _scope_matchers(cfg.scopes)),
        cfg.scopes, cfg.max_file_bytes, size_of)
    return Universe({name: sorted(paths) for name, paths in assigned.items()},
                    tuple(sorted(unclaimed)), tuple(sorted(oversized)))


def assign_files(files: list[str], cfg: Config, *,
                 size_of: Callable[[str], int] | None = None) -> dict[str, list[str]]:
    """Just the per-scope mapping, for callers with no use for the dropped sets."""
    return scan_files(files, cfg, size_of=size_of).by_scope
