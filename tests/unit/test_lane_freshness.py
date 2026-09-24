"""One freshness verdict per lane per command, and the limits its proof names.

lane_freshness.Freshness reads the stamp file once and answers every reader
from it: the `--reuse-artifacts` warning, the dark-line note, the report
banner and `--reuse-unchanged`. The reuse line names what the proof leaves
out, so an edit there (a gitignored file the suite reads, a tool outside the
repository, an inherited variable an inputs lane does not declare) is a
limit the reader was told about rather than a silent stale reuse.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import stale_tree
from stale_tree import REL

INPUTS = ("src", "make_cov.py")


def _reuse_line(root: Path, capsys) -> str:
    from crapkit.cli.scoring import _run_lanes

    cfg = stale_tree.config(root)
    capsys.readouterr()
    _run_lanes(root, cfg.lanes, False, cfg.scope_paths, reuse_unchanged=True)
    return "\n".join(line for line in capsys.readouterr().err.splitlines() if "lane 'unit':" in line)


# --- one stamp read per command -------------------------------------------------

@pytest.fixture()
def stamp_reads(monkeypatch):
    """Every read of .crapkit/artifacts.json, through each module that asks."""
    import crapkit.lane_freshness as lane_freshness
    import crapkit.lane_stamps as lane_stamps
    import crapkit.lanes as lanes

    calls = []
    real = lane_stamps.read

    def counted(root):
        calls.append(root)
        return real(root)

    for module in (lane_stamps, lane_freshness, lanes):
        monkeypatch.setattr(module, "read", counted)
    return calls


def test_a_coverage_run_reads_the_stamps_once_and_once_more_to_merge_its_write(tmp_path, stamp_reads):
    from crapkit.cli.scoring import _run_lanes

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    cfg = stale_tree.config(root)
    stamp_reads.clear()

    _run_lanes(root, cfg.lanes, False, cfg.scope_paths, reuse_unchanged=True)

    assert len(stamp_reads) == 1, "reuse decisions, the run and its stamp read one snapshot"
    _run_lanes(root, cfg.lanes, False, cfg.scope_paths)
    assert len(stamp_reads) == 3, "a run that stamps reads once to judge and once to merge"


def test_the_dark_line_readers_read_the_stamps_once(tmp_path, stamp_reads):
    from crapkit.uncovered import load_uncovered

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stamp_reads.clear()

    lines = load_uncovered(root, stale_tree.config(root))
    lines.note_for(REL)
    lines.in_span(REL, 1, 20)

    assert len(stamp_reads) == 1


# --- what the reuse line says it did not prove ----------------------------------

def _ignored_file_read(root: Path) -> None:
    stale_tree.write(root / "local.settings", "mode=b\n")


def _tool_outside(root: Path) -> None:
    """A tool the lane runs from outside the repository changed."""
    stale_tree.write(root.parent / "tool" / "version.txt", "2\n")


def _inherited_variable(monkeypatch) -> None:
    monkeypatch.setenv("CRAPKIT_FRESHNESS_TOOL", "b")


# (lane kind, what moved outside the proof): every row reuses, and says why it may
LIMITS = {
    "whole-tree/ignored-file-the-lane-reads": ((), _ignored_file_read),
    "whole-tree/tool-outside-tree-changes": ((), _tool_outside),
    "inputs/ignored-file-under-inputs": (INPUTS, lambda root: stale_tree.write(
        root / "src" / "local.settings", "mode=b\n")),
    "inputs/inherited-env-changes": (INPUTS, None),
}
LEAVES_OUT = {(): "its proof leaves out gitignored files and anything outside the repository",
              INPUTS: ("its proof leaves out gitignored files, files outside its inputs and "
                       "inherited environment variables")}


@pytest.mark.parametrize("row", sorted(LIMITS))
def test_a_reuse_names_what_its_proof_leaves_out(row, tmp_path, monkeypatch, capsys):
    """c05, c06, shape-2 ignored-input-edit and h2 ignored-input-changed: the
    change is outside the proof by design, and the line that reuses says so."""
    inputs, act = LIMITS[row]
    monkeypatch.setenv("CRAPKIT_FRESHNESS_TOOL", "a")
    root = stale_tree.measure(stale_tree.build(
        tmp_path / "repo", inputs=inputs, extra={"local.settings": "mode=a\n",
                                                 "src/local.settings": "mode=a\n"},
        ignore=".crapkit/\ncoverage/\nlocal.settings\n"))
    if act is None:
        _inherited_variable(monkeypatch)
    else:
        act(root)

    line = _reuse_line(root, capsys)

    assert "measurement inputs unchanged; reusing without rerun (artifact built at " in line, line
    assert line.endswith(LEAVES_OUT[inputs]), line


def test_a_same_size_edit_under_the_old_time_reuses_as_the_named_limit(tmp_path, capsys):
    """The proof trusts git's stat cache: a same-size edit whose old
    modification time was put back looks untouched to it (docs/lanes.md)."""
    root = stale_tree.EVENTS["same-size-one-tick"].prepare(tmp_path)

    assert "reusing without rerun" in _reuse_line(root, capsys)


# --- a stamp that holds no proof says why ------------------------------------------

@pytest.mark.parametrize("inputs", [(), INPUTS], ids=["whole-tree", "inputs"])
def test_a_stamp_measured_over_uncommitted_changes_names_them(inputs, tmp_path, capsys):
    root = stale_tree.build(tmp_path / "repo", inputs=inputs)
    stale_tree.write(root / "src" / "draft.ts", "export const d = 1;\n")
    stale_tree.measure(root)
    (root / "src" / "draft.ts").unlink()

    line = _reuse_line(root, capsys)

    assert line.endswith("rerunning: its stamp holds no proof: it was measured with 1 "
                         "uncommitted change(s): src/draft.ts"), line


def test_a_stamp_from_before_the_reason_was_recorded_keeps_the_old_sentence(tmp_path, capsys):
    from crapkit.lanes import write_stamps

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stamps = stale_tree.stamp(root)
    stamps["coverage/coverage-final.json"].update(proof="", proof_parts={})
    stamps["coverage/coverage-final.json"].pop("unproved", None)
    (root / ".crapkit" / "artifacts.json").unlink()
    write_stamps(root, stamps)

    assert _reuse_line(root, capsys).endswith(
        "its stamp holds no proof: it was measured with uncommitted changes, or by a crapkit "
        "that recorded none")


# --- the verdict for a stamp that records only a commit -------------------------------

def _legacy(root: Path, commit: str | None = None) -> Path:
    from crapkit.lanes import write_stamps

    stamps = stale_tree.stamp(root)
    entry = stamps["coverage/coverage-final.json"]
    entry.pop("blobs")
    if commit:
        entry["commit"] = commit
    (root / ".crapkit" / "artifacts.json").unlink()
    write_stamps(root, stamps)
    return root


def test_a_legacy_stamp_whose_commit_the_clone_lacks_says_git_cannot_answer(tmp_path):
    """A shallow CI clone with .crapkit/ restored: "not behind HEAD" was false,
    and the commit is simply not here."""
    from crapkit.lane_freshness import Freshness

    root = _legacy(stale_tree.measure(stale_tree.build(tmp_path / "repo")), "f" * 40)
    cfg = stale_tree.config(root)

    with Freshness(root, cfg.lanes, cfg.scope_paths) as fresh:
        why = fresh.lines(cfg.lanes[0])

    assert why == "git cannot say which files in its scopes changed since fffffffffff, which this clone does not hold"


def test_the_warning_and_the_note_give_one_answer_for_a_legacy_stamp(tmp_path, capsys):
    """The warning ran no ancestry check and the note did, so after an amend
    one said nothing and the other called the lane stale."""
    from crapkit.lane_freshness import Freshness
    from crapkit.lanes import _warn_stale_artifact

    root = _legacy(stale_tree.measure(stale_tree.build(tmp_path / "repo")))
    stale_tree.git(root, "commit", "-q", "--amend", "-m", "amended")
    cfg = stale_tree.config(root)
    lane = cfg.lanes[0]

    with Freshness(root, cfg.lanes, cfg.scope_paths) as fresh:
        _warn_stale_artifact(None, lane, cfg.scope_paths, fresh)
        note = fresh.lines(lane)

    assert "which is not behind HEAD" in note
    assert note in capsys.readouterr().err


# --- the library shim ------------------------------------------------------------------

def test_lane_sources_unchanged_still_answers_and_warns_that_it_goes_in_0_9(tmp_path):
    from crapkit.lanes import lane_sources_unchanged

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    cfg = stale_tree.config(root)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fresh = lane_sources_unchanged(root, cfg.lanes[0], cfg.scope_paths)
        stale_tree.write(root / REL, stale_tree.APP_TS + "// edited\n")
        moved = lane_sources_unchanged(root, cfg.lanes[0], cfg.scope_paths)

    assert (fresh, moved) == (True, False)
    assert [w.category for w in caught] == [DeprecationWarning, DeprecationWarning]
    assert "goes in 0.9" in str(caught[0].message)
