"""code-maat 1.0.4 (Adam Tornhill) over a git2 log written from the numstat walk.
No crapkit.

The log is code-maat's git2 format (parsers/git2.clj: `--<rev>--<YYYY-MM-DD>--
<author>`, then `added<TAB>deleted<TAB>path` lines, a blank line between
commits), written from oracles/git_walk.py commits: the window's commits,
their paths cut to the root, a rename under its new path, the author's name.

- revisions: commits per file (entity, n-revs).
- authors: distinct authors per file (entity, n-authors, n-revs).
- coupling, every threshold opened (min-revs 1, min-shared-revs 1,
  min-coupling 0, max-coupling 100), max-changeset-size at its default 30:
  entity, coupled, degree, average-revs. The degree is int(100 * shared /
  mean(revs of the two)), where the revs count only commits of 30 files or
  fewer: coupling_algos.clj drops a bigger change set before it counts
  anything (ruling H6 for the degree, H7 for the bulk commits).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
from pathlib import Path

import hang_guard
from accuracy.kit import tiers

OPEN = ("--min-revs", "1", "--min-shared-revs", "1", "--min-coupling", "0",
        "--max-coupling", "100")


def _entry(commit) -> str:
    day = datetime.fromtimestamp(commit.at, timezone.utc).strftime("%Y-%m-%d")
    changes = "".join(f"1\t1\t{path}\n" for path in commit.paths)
    return f"--{commit.sha[:12]}--{day}--{commit.author}\n{changes}"


def write_log(commits, path: Path) -> Path:
    """The walk's commits as a git2 log at `path`; a commit with no path is left out."""
    path.write_bytes("\n".join(_entry(c) for c in commits if c.paths).encode("utf-8"))
    return path


def run(log: Path, analysis: str, *options: str) -> list[dict]:
    """One analysis's CSV rows."""
    tiers.require_process("code-maat")
    argv = ["code-maat", "-l", str(log), "-c", "git2", "-a", analysis, *options]
    done = hang_guard.run(argv, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    return list(csv.DictReader(io.StringIO(done.stdout)))


def revisions(log: Path) -> dict[str, int]:
    return {row["entity"]: int(row["n-revs"]) for row in run(log, "revisions")}


def authors(log: Path) -> dict[str, int]:
    return {row["entity"]: int(row["n-authors"]) for row in run(log, "authors")}


def coupling(log: Path) -> dict[tuple[str, str], int]:
    """{sorted pair: degree} with every threshold opened."""
    rows = run(log, "coupling", *OPEN)
    return {tuple(sorted((row["entity"], row["coupled"]))): int(row["degree"]) for row in rows}
