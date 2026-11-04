"""Stamps for tests: a fixture config's or a repo's running per-scope stamp, a
per-scope stamp written out by hand, and a single-number marks file's text.

The values here are literals or come from the config a test builds, so a test
that compares them with `ratchet.stamp_conflict` reads no number from the code
under test except the running tables it patches. A single-number stamp is only
ever the literal line 0.8.x wrote, read back as crapkit reads it.
"""
from __future__ import annotations

from pathlib import Path

from crapkit.config import Config, Lane, Scope, load_config_text
from crapkit.ratchet import (MetricStamp, RatchetEntry, ScopeStamp, ScopeStamps, dump_ratchet,
                             load_ratchet, read_key_version, running_stamp)

LIZARD = "1.24.0"
MARKS = "crapkit-ratchet.tsv"
HEADER = "path\tlong_name\tcrap\n"


def scope(name: str, *languages: str, optional: bool = False) -> Scope:
    """A scope over the directory of its own name."""
    return Scope(name, (name,), languages, coverage_optional=optional)


def lane(name: str, parser: str, *scopes: str) -> Lane:
    return Lane(name, "true", f"{name}.json", parser, scopes)


def config(scopes: list[Scope], lanes: list[Lane]) -> Config:
    return Config(scopes=tuple(scopes), lanes=tuple(lanes))


def running(scopes: list[Scope], lanes: list[Lane]):
    """The stamp a run of this crapkit on that config records."""
    return running_stamp(config(scopes, lanes))


def running_in(repo: Path) -> ScopeStamps:
    """The stamp a run of this crapkit on `repo`'s own crapkit.toml records."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8-sig")
    return running_stamp(load_config_text(text, root=repo))


def restamp(repo: Path) -> None:
    """Rewrite `repo`'s marks file under `running_in(repo)`: the per-scope file
    a test needs when verify must weigh its marks with no inferred mode."""
    text = (repo / MARKS).read_text(encoding="utf-8")
    stamped = dump_ratchet(load_ratchet(text), stamp=running_in(repo),
                           key_version=read_key_version(text), file_name=MARKS)
    (repo / MARKS).write_bytes(stamped.encode("utf-8"))


def marks_in(repo: Path, entries: list[RatchetEntry], key_version: int = 0) -> str:
    """`entries` as the marks file a run of this crapkit on `repo` signs."""
    return dump_ratchet(list(entries), stamp=running_in(repo), key_version=key_version, file_name=MARKS)


def stamp_lines(stamp: MetricStamp, key_version: int = 0, file_name: str = MARKS) -> str:
    """The lines a marks file under `stamp` opens with, up to its header: the
    text a test that writes its rows by hand puts above them."""
    return dump_ratchet([], stamp=stamp, key_version=key_version, file_name=file_name).removesuffix(HEADER)


def scoped(lizard: str = LIZARD, file_name: str | None = None, **scopes: tuple) -> ScopeStamps:
    """A per-scope stamp: each keyword is a scope, its value ({language: N}, mode)."""
    return ScopeStamps(lizard, {name: ScopeStamp(*value) for name, value in scopes.items()}, file_name)


# A per-scope stamp for a test that builds no config, or one whose code under
# test compares no stamp: one scope, python and typescript, cc-only.
FIXED = scoped(src=({"python": 13, "typescript": 13}, "cc-only"))


def shifted(stamp: ScopeStamps, by: int) -> ScopeStamps:
    """`stamp` with every language's analysis number moved by `by`: what a
    newer (`by` > 0) or older crapkit signs on the same config."""
    return ScopeStamps(stamp.lizard, {
        name: ScopeStamp({language: number + by for language, number in line.analysis.items()}, line.coverage)
        for name, line in stamp.scopes.items()})


def single_number(analysis: int, lizard: str = LIZARD) -> MetricStamp:
    """The line 0.8.x stamped its marks with, read back as crapkit reads it."""
    return MetricStamp.parse(f"crapkit-analysis={analysis} lizard={lizard}")


def single_number_text(analysis: int, entries: list[RatchetEntry], lizard: str = LIZARD) -> str:
    """The marks file 0.8.x wrote: one analysis number and no coverage mode."""
    return dump_ratchet(entries, stamp=single_number(analysis, lizard))
