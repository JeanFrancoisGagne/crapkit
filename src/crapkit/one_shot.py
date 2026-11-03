"""What `crapkit score` reads off its flags, and the in-memory scoring it runs.

config_from_flags writes the crapkit.toml its flags spell and loads it with
config.load_config_text, so a value crapkit.toml would refuse is refused by
the same rule, the message led by the flag that carried it. sniff_format names
a coverage file's format from its first bytes. scored_rows reads the file
through its format's reader and scores the inventory with score.score_rows, the
join a configured lane feeds. Nothing here writes a file.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import coverage_istanbul, coverage_lcov, coverage_py
from .config import Scope, load_config_text
from .coverage_format import _FORMATS, READER_VERSIONS, lane_format
from .errors import ConfigError, ToolError
from .gitio import ls_files, untracked_files
from .named import first_few
from .score import CRAP_PLACES, over_ceiling, score_rows
from .universe import LANGUAGE_EXTENSIONS, owning_scope, scope_matchers

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Config
    from .score import InventoryRow, ScoredRow

LANE = "score"
# Enough of a file to hold an lcov record tag, or a coverage.py report's meta
# and the "files" key after it, or istanbul's first statementMap.
HEAD_BYTES = 65536
_BOM = b"\xef\xbb\xbf"
_ALL_LANGUAGES = tuple(LANGUAGE_EXTENSIONS)

# One rule per reader, tried in this order and keyed by its adapter, so a
# format's name is spelled only in coverage_format's registry
# (tests/unit/test_parser_strings_live_in_one_module.py). A format whose
# reader has not landed has no rule, so its file reads as unrecognized.
_SNIFFS = (
    (coverage_lcov, re.compile(rb"^(?:TN|SF):", re.MULTILINE)),
    (coverage_py, re.compile(rb'\A\s*\{(?=.*"meta"\s*:)(?=.*"files"\s*:)', re.DOTALL)),
    (coverage_istanbul, re.compile(rb'\A\s*\{(?=.*"statementMap"\s*:)', re.DOTALL)),
)
_NAME_OF = {adapter: name for name, adapter in _FORMATS.items()}


def sniff_format(head: bytes) -> str | None:
    """The format a coverage file's first bytes name: `TN:` or `SF:` lines are
    an lcov tracefile, a JSON object with "meta" and "files" a coverage.py
    report, one whose values hold "statementMap" an istanbul report. None for
    anything else."""
    text = head.removeprefix(_BOM)
    return next((_NAME_OF[adapter] for adapter, rule in _SNIFFS if rule.search(text)), None)


def _accepted() -> str:
    return f"--format takes one of: {', '.join(sorted(READER_VERSIONS))}"


def format_of(artifact: Path, named: str | None) -> str:
    """`named` when it is a format crapkit reads, else the one the file's head
    names. Exit 3 (ConfigError) for a missing file, an unknown name or a file
    no rule recognizes, naming --format and the names it takes."""
    head = _head(artifact)
    if named is not None and named not in READER_VERSIONS:
        raise ConfigError(f"--format {named!r} is not a coverage format crapkit reads; {_accepted()}")
    found = named or sniff_format(head)
    if found is None:
        raise ConfigError(f"--coverage {artifact}: crapkit does not recognize this file's format; "
                          f"name it with --format. {_accepted()}")
    return found


def _head(artifact: Path) -> bytes:
    try:
        with open(artifact, "rb") as fh:
            return fh.read(HEAD_BYTES)
    except OSError as exc:
        raise ConfigError(f"--coverage {artifact}: {exc.strerror or exc}") from None


def _quoted(text: str) -> str:
    """A TOML basic string: every character TOML would refuse raw is a \\U escape."""
    return '"' + "".join(_escaped(ch) for ch in text) + '"'


def _escaped(ch: str) -> str:
    return ch if ch.isprintable() and ch not in '"\\' else f"\\U{ord(ch):08X}"


def _array(items) -> str:
    return "[" + ", ".join(map(_quoted, items)) + "]"


def _scope_table(name: str, paths, languages) -> str:
    return f"[[scope]]\nname = {_quoted(name)}\npaths = {_array(paths)}\nlanguages = {_array(languages)}\n"


def _load(flag: str, root: Path, text: str) -> Config:
    try:
        return load_config_text(text, root=root)
    except ConfigError as exc:
        raise ConfigError(f"{flag}: {exc}") from None


def _sources(files, paths) -> list[str]:
    """The files under `paths` in any language crapkit reads."""
    matchers = scope_matchers((Scope("PATH", tuple(paths), _ALL_LANGUAGES),))
    return [path for path in files if owning_scope(path, matchers)]


def _languages(files) -> list[str]:
    return [lang for lang, exts in LANGUAGE_EXTENSIONS.items()
            if any(path.lower().endswith(exts) for path in files)]


def config_from_flags(root: Path, paths, coverage: str, fmt: str) -> Config:
    """One scope per language whose git-tracked files sit under `paths`, one
    lane with no command whose `parser` is `fmt` and whose `artifact` is
    `coverage`, and the default ceiling. A PATH that names no tracked source
    is refused, exit 3, naming the untracked source under it as init does;
    outside a git repository git's refusal is exit 4."""
    # The probe scope is named PATH, so a path crapkit.toml would refuse is
    # refused as `scope 'PATH': path ...`, naming the argument.
    declared = load_config_text(_scope_table("PATH", paths, _ALL_LANGUAGES), root=root).scopes[0].paths
    languages = _languages(_sources(ls_files(root), declared))
    if not languages:
        raise ConfigError(_no_source(root, declared))
    scopes = "".join(_scope_table(lang, declared, (lang,)) for lang in languages)
    lane = (f'[[lane]]\nname = "{LANE}"\ncommand = ""\nartifact = {_quoted(coverage)}\n'
            f"parser = {_quoted(fmt)}\nscopes = {_array(languages)}\n")
    return _load("--coverage", root, scopes + lane)


def _no_source(root: Path, declared) -> str:
    named = " ".join(declared)
    untracked = untracked_sources(root, declared)
    found = f"; run `git add` first ({len(untracked)} untracked source file(s) found: " \
            f"{first_few(untracked)})" if untracked else ""
    return f"PATH {named}: no tracked source file under it - crapkit scores git-tracked files only{found}"


def untracked_sources(root: Path, declared) -> list[str]:
    """The source files under `declared` that git does not track, sorted:
    the command skips them, and says so."""
    return sorted(_sources(untracked_files(root), declared))


def _refuse_wrong_tree(cfg: Config, coverage: dict, files) -> None:
    """Exit 5 when the file measured no tracked file under PATH: joined, it
    would score every function untested. The reader's own fix closes it."""
    if coverage.keys() & set(files):
        return
    lane = cfg.lanes[0]
    measured = sorted(coverage)
    raise ToolError(f"--coverage {lane.artifact} measured {len(measured)} file(s), none of them a "
                    f"tracked source file under PATH {' '.join(cfg.scopes[0].paths)}"
                    f"{_examples(measured)}. {lane_format(lane).WRONG_TREE_FIX}")


def _examples(measured: list[str]) -> str:
    return f"; it reports paths like {first_few(measured)}" if measured else ""


def scored_rows(root: Path, cfg: Config, files, rows: list[InventoryRow],
                artifact: Path) -> list[ScoredRow]:
    """The inventory scored against the coverage file through the lane's
    reader and score.score_rows, as `crapkit coverage` scores a lane's
    artifact; exit 5 when the file measured none of `files`."""
    lane = cfg.lanes[0]
    coverage, evidence, _ = lane_format(lane).read(lane, root, artifact)
    _refuse_wrong_tree(cfg, coverage, files)
    return score_rows(rows, coverage, lane_scopes=set(lane.scopes), target=cfg.target,
                      scope_targets=cfg.scope_targets,
                      cc_only_scopes=cfg.coverage_optional_scopes,
                      evidence_by_path={path: [found] for path, found in evidence.items()})


def _worst_first(row: ScoredRow) -> tuple:
    return (-round(row.crap, CRAP_PLACES), row.path, row.start, row.long_name)


def over(scored: list[ScoredRow], threshold: int) -> list[ScoredRow]:
    """The rows whose CRAP is above `threshold`, worst first."""
    return sorted((row for row in scored if over_ceiling(row.crap, threshold)), key=_worst_first)


def _row_line(row: ScoredRow) -> str:
    return (f"crap {row.crap:8.2f}  ccn {row.ccn:3d}  cov {row.cov:4.0%}  "
            f"{row.path}:{row.start}  {row.long_name}")


def report_lines(scored: list[ScoredRow], threshold: int, top: int | None) -> list[str]:
    """One line per function over `threshold`, worst first and at most `top`
    of them, then `N functions, M over T`."""
    above = over(scored, threshold)
    shown = above if top is None else above[:top]
    return [*map(_row_line, shown), f"{len(scored)} functions, {len(above)} over {threshold}"]
