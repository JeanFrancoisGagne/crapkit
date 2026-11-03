"""`crapkit score`: CRAP per function from a coverage file CI already wrote,
with no crapkit.toml, no lane run and no store. The flags build the Config
(one_shot.config_from_flags); analysis and the join run in memory, and the
command writes nothing under the repo, so it needs no `init` first."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import one_shot
from ..gitio import ls_files
from ..named import first_few
from ..repopath import typed_path
from ..snapshot import build_inventory_rows
from ._shared import _analysis_tools, _on_its_drive, _say_left_out, _scan
from .scoring import _present_on_disk, _records_by_scope, _tracked_files

# The exit `rescore --gate` and every other "a function is over its limit"
# verdict uses.
OVER_LIMIT = 6


def _root(repo: str | None) -> Path:
    """`--repo`, else the working directory: score reads no crapkit.toml, so
    it walks up to none."""
    return _on_its_drive(typed_path(repo or "."))


def _artifact(root: Path, coverage: str) -> Path:
    path = typed_path(coverage)
    return path if path.is_absolute() else root / path


def _say_untracked(untracked: list[str]) -> None:
    if untracked:
        print(f"crapkit: skipped {len(untracked)} untracked source file(s) under PATH - crapkit "
              f"scores git-tracked files only; run `git add` to score them: "
              f"{first_few(untracked)}", file=sys.stderr)


def _inventory(root: Path, cfg) -> tuple[list[str], list]:
    """The tracked files under PATH and their inventory rows. analyze_jobs
    reads each file and writes nothing; analyze_files would save its stat
    index under <root>/.crapkit/."""
    _analysis_tools()  # an absent lizard is a ToolError, exit 5
    from ..analyze import analyze_jobs

    universe = _scan(root, ls_files(root), cfg)
    _say_left_out(universe.unreadable)
    files = _present_on_disk(root, _tracked_files(universe.by_scope))
    records = analyze_jobs([(str(root / rel), rel) for rel in files])
    return files, build_inventory_rows(_records_by_scope(universe.by_scope, records))


def cmd_score(args: argparse.Namespace) -> int:
    root = _root(args.repo)
    artifact = _artifact(root, args.coverage)
    cfg = one_shot.config_from_flags(root, args.paths, args.coverage,
                                     one_shot.format_of(artifact, args.format))
    _say_untracked(one_shot.untracked_sources(root, cfg.scopes[0].paths))
    files, rows = _inventory(root, cfg)
    scored = one_shot.scored_rows(root, cfg, files, rows, artifact)
    limit = cfg.target if args.fail_above is None else args.fail_above
    for line in one_shot.report_lines(scored, limit, args.top):
        print(line)
    failing = args.fail_above is not None and one_shot.over(scored, limit)
    return OVER_LIMIT if failing else 0
