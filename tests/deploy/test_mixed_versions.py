"""A teammate still on crapkit 0.8.x meets the marks file a 0.9.0 seed writes.

The per-scope form opens with a line that names the file and the release that
reads it, and each `#scope` line holds a tab and a third field that is no
number. 0.8.x reads the first line as a stamp it cannot compare and each
scope line as a mark it cannot parse, so every command that judges or rewrites
the marks refuses once, naming crapkit-ratchet.tsv, and leaves the file's
bytes as they were. Its merge driver refuses too, and git falls back to
a conflict on the file. The read-only commands answer and write nothing.

The repo is a source the old release built (init, coverage, seed, an
override), whose marks the candidate then reseeds: an upgraded teammate's
`crapkit coverage`, `crapkit ratchet prune` and `crapkit ratchet seed`, in a
copy of the repo so the old release's store stays as it wrote it. The cell
runs with the released 0.8.0 wheel and again with 0.8.1's.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kit import gitsurf, state
from kit.cells import cell
from kit.state import output

PACKET = "metric-stamp-04"
MARKS = "crapkit-ratchet.tsv"
OLD_RELEASES = ("0.8.0", "0.8.1")
FIRST_LINE = "# crapkit-stamp=2 lizard="


def candidate_seeded(box, source, candidate) -> bytes:
    """The marks file the candidate's seed writes over the old release's marks,
    after the guide's coverage and prune, in its own copy of the source."""
    teammate = source.checkout(box, "upgraded-teammate")
    writer = state.venv_cli(box, candidate.version, box.root / "candidate-venv")
    for step in (["coverage"], ["ratchet", "prune"], ["ratchet", "seed"]):
        writer.run(box, teammate, *step)
    seeded = (teammate / MARKS).read_bytes()
    shutil.rmtree(teammate, ignore_errors=True)
    return seeded


def per_scope_repo(box, templates, candidate, old: str) -> Path:
    """The old release's source with the marks file the candidate's seed wrote
    committed, then the old release first on PATH."""
    source = state.build(box, state.source_version(old), cache=templates)
    repo = source.checkout(box)
    (repo / MARKS).write_bytes(candidate_seeded(box, source, candidate))
    assert (repo / MARKS).read_text(encoding="utf-8").startswith(FIRST_LINE), box.transcript.text()
    assert "\n#scope\t" in (repo / MARKS).read_text(encoding="utf-8"), box.transcript.text()
    state.commit(box, repo, "marks under per-scope stamps, as the 0.9.0 seed wrote them")
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
