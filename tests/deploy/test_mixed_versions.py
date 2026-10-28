"""A teammate still on crapkit 0.8.x meets the marks file a 0.9.0 seed writes.

The per-scope form opens with a line that names the file and the release that
reads it, and each `#scope` line holds a tab and a third field that is no
number. 0.8.x reads the first line as a stamp it cannot compare and each
scope line as a mark it cannot parse, so every command that judges or rewrites
the marks refuses once, naming crapkit-ratchet.tsv, and leaves the file's
bytes as they were. Its merge driver refuses too, and git falls back to
a conflict on the file. The read-only commands answer and write nothing.

The repo is a source the old release built (init, coverage, seed, an
override), whose marks the candidate then renders under per-scope stamps
through the writer seed uses. The cell runs with the released 0.8.0 wheel and
again with 0.8.1's.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kit import gitsurf, state
from kit.cells import cell
from kit.state import output

PACKET = "metric-stamp-04"
MARKS = "crapkit-ratchet.tsv"
OLD_RELEASES = ("0.8.0", "0.8.1")
FIRST_LINE = "# crapkit-stamp=2 lizard="
# The candidate's own reader and writer: every scope of the repo's config,
# stamped with the candidate's numbers, through RatchetFile.reseeded, the
# write seed makes.
RENDER = '''
import tomllib
from pathlib import Path

import lizard

from crapkit.analyze import ANALYSIS_VERSIONS
from crapkit.coverage_format import READER_VERSIONS
from crapkit.ratchet import ScopeStamp, ScopeStamps
from crapkit.ratchetfile import RatchetFile

config = tomllib.loads(Path("crapkit.toml").read_text(encoding="utf-8"))
parsers = {}
for lane in config.get("lane", []):
    for scope in lane.get("scopes", []):
        parsers.setdefault(scope, {})[lane["parser"]] = READER_VERSIONS[lane["parser"]]
scopes = {row["name"]: ScopeStamp({language: ANALYSIS_VERSIONS[language] for language in row.get("languages", [])},
                                  "cc-only" if row.get("coverage_optional") else parsers.get(row["name"], "no-lane"))
          for row in config["scope"]}
saved = RatchetFile.read(Path("crapkit-ratchet.tsv"), required=True)
assert saved.publish(saved.reseeded(saved.entries, ScopeStamps(lizard.version, scopes)))
'''


def per_scope_repo(box, templates, candidate, old: str) -> Path:
    """The old release's source with its marks rendered per scope and committed,
    then the old release first on PATH."""
    source = state.build(box, state.source_version(old), cache=templates)
    repo = source.checkout(box)
    writer = state.venv_cli(box, candidate.version, box.root / "candidate-venv")
    box.run([str(writer.bin / "python"), "-c", RENDER], cwd=repo, expect=0,
            note="the candidate renders the marks under per-scope stamps")
    assert (repo / MARKS).read_text(encoding="utf-8").startswith(FIRST_LINE), box.transcript.text()
    state.commit(box, repo, "marks under per-scope stamps, as a 0.9.0 seed writes them")
    state.pip_venv(box, name="old-venv")
    state.pip_install(box, f"crapkit[py]=={old}")
    assert old in box.run(["crapkit", "--version"], expect=0).stdout
    return repo


def refused_once(box, step, before: bytes, repo: Path) -> None:
    """Non-zero, one `crapkit:` refusal line that names the marks file, no
    traceback, and the file's bytes as they were."""
    said = output(step)
    refusals = [line for line in said.splitlines() if line.startswith("crapkit:")]
    assert step.exit != 0, box.transcript.text()
    assert len(refusals) == 1 and MARKS in refusals[0], said
    assert "Traceback" not in said, said
    assert (repo / MARKS).read_bytes() == before, f"{step.argv} rewrote {MARKS}"


def tightened(repo: Path, index: int) -> None:
    """One mark row lowered by one, as a hand-resolved tighten would; every stamp line kept."""
    row = state.mark_rows(repo)[index]
    path, name, value = row.split("\t")
    state.rewrite(repo / MARKS, lambda text: text.replace(row, f"{path}\t{name}\t{float(value) - 1:.4f}"))


def driver_refuses(box, repo: Path) -> None:
    """docs/ratchet.md's driver, wired as the page says, run by the old release
    on two branches that each tightened a mark of the per-scope file."""
    gitsurf.commit_attribute(box, repo)
    gitsurf.configure_driver(box, repo)
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    tightened(repo, 0)
    state.commit(box, repo, "tighten the first mark on feature")
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    tightened(repo, 1)
    state.commit(box, repo, "tighten the second mark on main")
    ours = (repo / MARKS).read_bytes()

    merged = gitsurf.merge(box, repo, "feature")

    said = output(merged)
    driver = [line for line in said.splitlines() if line.startswith("crapkit:")]
    assert merged.exit != 0 and f"Merge conflict in {MARKS}" in said, said
    assert len(driver) == 1 and ".merge_file_" in driver[0], said
    assert "#scope" in driver[0] or "crapkit-stamp=2" in driver[0], said
    assert (repo / MARKS).read_bytes() == ours, "the old driver changed OURS"
    box.run(["git", "merge", "--abort"], cwd=repo, expect=0)


@pytest.mark.parametrize("old", OLD_RELEASES)
@cell("lin-mixed-0.8.0-reads-0.9.0-marks", channel="pip venv", harness="none",
      scenario="mixed versions: 0.8.x verify, seed, prune, move, override and merge driver refuse a per-scope "
               "marks file by name and leave it; brief, explain and worklist answer",
      use_cases="team version skew, ratchet merge", os="linux", image="core", cadence="nightly")
def test_lin_mixed_0_8_x_reads_per_scope_marks(box, templates, candidate, old):
    repo = per_scope_repo(box, templates, candidate, old)
    before = (repo / MARKS).read_bytes()

    for argv in (["verify"], ["ratchet", "seed"], ["ratchet", "prune"],
                 ["ratchet", "move", "calc/grade.py", "calc/grading.py"],
                 ["verify", "--override", state.OVERRIDE_REASON]):
        refused_once(box, box.run(["crapkit", *argv], cwd=repo), before, repo)
    for argv in (["brief", "calc/grade.py", "grade"], ["explain", "calc/grade.py", "grade"], ["worklist"]):
        box.run(["crapkit", *argv], cwd=repo, expect=0)
        assert (repo / MARKS).read_bytes() == before, f"crapkit {' '.join(argv)} wrote {MARKS}"
    driver_refuses(box, repo)
