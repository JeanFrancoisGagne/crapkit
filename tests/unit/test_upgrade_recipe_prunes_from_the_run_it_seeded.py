"""The analysis-11 upgrade recipe prunes from the run it seeded, behind a failed verify too.

On a store a failed verify pins, the upgrade guide sent `--baseline N` to seed
only and then ran a plain `ratchet prune`. A plain prune reads verify's pick,
the pinned run an older crapkit measured, and drops every mark absent from it:
the new-name marks seed had just written went, and the old-name marks stayed
under the new stamp. Passing the same run to prune keeps the marks seed wrote
and drops the ones left under the old names.
"""
import re
from pathlib import Path

import lizard

from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import MARKS, write_run

from crapkit.cli import main
from crapkit.ratchet import stamp_text
from crapkit.score import ScoredRow

ROOT = Path(__file__).resolve().parents[2]
# The tool versions of a run the 0.7.x reader measured.
ANALYSIS_10 = {"analysis_version": "10", "lizard": lizard.version}


def over_ceiling(name: str) -> ScoredRow:
    """One untested ccn-9 function, crap 90, so seed writes a mark for it."""
    return ScoredRow("src", "src/app.ts", name, 1, 9, 9, 9, 9, 9, 1, 0,
                     0.0, "untested", 90.0, "decompose", 0, 1)


def upgrade_store(repo: Path) -> int:
    """Run 1 under analysis 10 with the old name, run 2 a failed verify, run 3 under
    this crapkit with the new name; the marks hold the old name under stamp 10."""
    write_run(repo, [over_ceiling("old( x )")], versions=ANALYSIS_10)
    write_run(repo, [over_ceiling("old( x )")], kind="verify", ok=False)
    fresh = write_run(repo, [over_ceiling("new( x )")])
    (repo / MARKS).write_text(
        f"# {stamp_text(10, lizard.version)}\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
        "src/app.ts\told( x )\t90.0\n", encoding="utf-8", newline="\n")
    return fresh


def marked_names(repo: Path) -> list[str]:
    rows = (repo / MARKS).read_text(encoding="utf-8").splitlines()
    return [line.split("\t")[1] for line in rows if line.startswith("src/")]


def test_seed_and_prune_from_the_named_run_leave_only_the_new_name(repo, capsys):
    fresh = upgrade_store(repo)

    assert main(["ratchet", "seed", "--baseline", str(fresh), "--repo", str(repo)]) == 0
    assert main(["ratchet", "prune", "--baseline", str(fresh), "--repo", str(repo)]) == 0

    assert marked_names(repo) == ["new( x )"]


def test_a_plain_prune_after_the_named_seed_reads_the_pinned_run_instead(repo, capsys):
    fresh = upgrade_store(repo)
    assert main(["ratchet", "seed", "--baseline", str(fresh), "--repo", str(repo)]) == 0
    capsys.readouterr()

    assert main(["ratchet", "prune", "--repo", str(repo)]) == 0

    assert marked_names(repo) == ["old( x )"]
    assert f"(pass `--baseline {fresh}` to read it)" in capsys.readouterr().out


def _pinned_sentence(page: str, anchor: str) -> str:
    """The 600 characters from ANCHOR on, with the page's line breaks read as spaces."""
    text = " ".join((ROOT / page).read_text(encoding="utf-8").split())
    start = text.index(anchor)
    return text[start:start + 600]


def test_the_upgrade_guide_passes_the_named_run_to_prune_as_well():
    text = _pinned_sentence("docs/upgrading.md", "When a failed verify pins the baseline")

    assert re.search(r"`crapkit ratchet prune --baseline N`", text), text


def test_the_changelog_upgrade_bullet_passes_the_named_run_to_prune_as_well():
    text = _pinned_sentence("CHANGELOG.md", "When a failed verify pins the baseline")

    assert re.search(r"`crapkit ratchet prune --baseline N`", text), text
