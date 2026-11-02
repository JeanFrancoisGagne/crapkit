"""A store pinned behind a failed verify, the state issue #75 reported.

Run 1 is a coverage run written before crapkit recorded same-line positions: two
anonymous functions share line 1 of src/app.ts and neither carries its place on
that line. Run 2 is a verify that failed. Run 3 is a coverage run this crapkit
measured, positions and all. `pick_baseline` stays on run 1 until a verify
passes, and every row-keyed read of run 1 refuses its twins.

Built on the in-process template repo (`cli_inproc_repo`), so `main([...])` drives
every command and `--reuse-artifacts` stands in for the lanes.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import lizard

from cli_inproc_repo import git, seed_artifacts

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main
from crapkit.config import load_config_text
from crapkit.ratchet import running_stamp, stamp_text
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

MARKS = "crapkit-ratchet.tsv"
MEASURED = {"analysis_version": str(ANALYSIS_VERSION), "lizard": lizard.version}
# The stamp an older crapkit signed its marks with; this one refuses to compare them.
STALE = stamp_text(7, lizard.version)
# The tool versions of a run that older crapkit measured: seed signs it with STALE.
OLDER = {"analysis_version": "7", "lizard": lizard.version}


def twin(occurrence: int) -> ScoredRow:
    """An anonymous function on line 1; occurrence 0 is a row with no recorded place."""
    return ScoredRow("src", "src/app.ts", "(anonymous)", 1, 1, 1, 1, 1, 1, 0, 0,
                     1.0, "measured", 2.0, "ok", 0, occurrence)


def store_of(repo: Path) -> SnapshotStore:
    (repo / ".crapkit").mkdir(exist_ok=True)
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite")


def scope_stamps(repo: Path, analysis: int | None = None) -> dict:
    """The `scope_stamps` a run on the repo's config records: this crapkit's, or
    `analysis` for every language when an older crapkit measured the run."""
    config = load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8"), root=repo)
    return {name: {"analysis": {language: analysis or number for language, number in stamp.analysis.items()},
                   "coverage": stamp.coverage}
            for name, stamp in running_stamp(config).scopes.items()}


def run_versions(repo: Path, versions: dict | None = None) -> dict:
    """MEASURED, or `versions`, with the scope stamps such a run records."""
    analysis = None if versions is None else int(versions["analysis_version"])
    return {**(versions or MEASURED), "scope_stamps": scope_stamps(repo, analysis)}


def write_run(repo: Path, rows: list, *, kind: str = "coverage", ok: bool | None = None,
              versions: dict | None = None) -> int:
    """One stored run at HEAD; `ok` sets a verify's verdict, `versions` the metric it
    was measured under (this crapkit's by default), each scope stamped with it."""
    store = store_of(repo)
    with closing(store._conn):
        run_id = store.write_run(commit=git(repo, "rev-parse", "HEAD").strip(),
                                 tool_versions=run_versions(repo, versions), rows=rows,
                                 lanes={"unit": {}, "ui": {}}, kind=kind)
        if ok is not None:
            store.set_verdict_ok(run_id, ok, findings=0 if ok else 1)
    return run_id


def legacy_run(repo: Path) -> int:
    return write_run(repo, [twin(0), twin(0)])


def failed_verify(repo: Path) -> int:
    return write_run(repo, [twin(1), twin(2)], kind="verify", ok=False)


def fresh_run(repo: Path) -> int:
    """A coverage run this crapkit measures, off the canned lane artifacts."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    store = store_of(repo)
    with closing(store._conn):
        return store.list_runs()[-1]["id"]


def pinned(repo: Path) -> tuple[int, int, int]:
    """(legacy run, failed verify, fresh run), oldest first."""
    return legacy_run(repo), failed_verify(repo), fresh_run(repo)


def stale_marks(repo: Path) -> None:
    """A header-only marks file an older crapkit stamped."""
    (repo / MARKS).write_text(f"# {STALE}\n# crapkit-keys=1\npath\tlong_name\tcrap\n",
                              encoding="utf-8", newline="\n")
