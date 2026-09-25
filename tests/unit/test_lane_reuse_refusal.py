"""The reuse door after a failed attempt, at the lane runner and the fold.

`_run_attempts` refuses the artifact a run did not rewrite (0.4.12). Reuse then
read the same file back on the next command and scored it. The refusal carries
the sha256 of the file the attempt left in place, the fold persists it through
the stamps file and the snapshot store, and reuse refuses the file while it
still holds those bytes. It used to key on the modification time instead, so a
touch, a copy that drops times or a same-bytes rewrite handed the dead lane's
numbers back as a trusted run; a salvage with new bytes is the way back in.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from crapkit.cli.scoring import _collect_lanes
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lane_stamps import file_sha256
from crapkit.lanes import LaneOutcome, read_stamps, run_lane, write_stamps

ISTANBUL = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})
BEFORE = 1_000_000_000
LATER = 2_000_000_000

# A lane command that runs and writes nothing: the failed attempt this file is about.
WRITES_NOTHING = "python -c pass"


def _lane(name: str = "py", artifact: str = "cov.json", command: str = WRITES_NOTHING) -> Lane:
    return Lane(name=name, command=command, artifact=artifact, parser="istanbul", scopes=())


def _plant(root: Path, rel: str, ns: int, text: str = ISTANBUL) -> str:
    """The file, written with modification time `ns`; its sha256."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(ns, ns))
    return file_sha256(path)


def _refused(root: Path, lane: Lane, sha256: str) -> None:
    write_stamps(root, {lane.artifact: {"lane": lane.name, "refused_sha256": sha256}})


# --- run_lane, reuse_artifact=True --------------------------------------------

def test_a_reused_artifact_the_last_attempt_failed_to_write_is_refused(tmp_path):
    lane = _lane()
    _refused(tmp_path, lane, _plant(tmp_path, "cov.json", BEFORE))

    with pytest.raises(ToolError, match="wrote no artifact on its last attempt") as raised:
        run_lane(tmp_path, lane, reuse_artifact=True)

    message = str(raised.value)
    assert "cov.json on disk predates it and is the previous run's" in message
    assert "--reuse-artifacts" in message, "the refusal names the flag that reached it"
    assert str(tmp_path / ".crapkit" / "lane-py.log") in message, "every lane refusal names its log"
    assert raised.value.exit_code == 5


def _touch(path: Path) -> None:
    os.utime(path, ns=(LATER, LATER))


def _copy_without_times(path: Path) -> None:
    """What `cp -r` of a checkout, or a sync client, leaves: the bytes, a new time."""
    copy = path.with_name("copy.json")
    shutil.copyfile(path, copy)
    os.replace(copy, path)
    _touch(path)


def _same_bytes(path: Path) -> None:
    path.write_bytes(path.read_bytes())
    _touch(path)


def _new_bytes(path: Path) -> None:
    """A salvage combined by hand: other bytes, whatever its time says."""
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2),
                    encoding="utf-8")
    os.utime(path, ns=(BEFORE, BEFORE))


LEFTOVER_EVENTS = {"touch": (_touch, True), "copy-without-times": (_copy_without_times, True),
                   "same-bytes-rewrite": (_same_bytes, True), "new-bytes": (_new_bytes, False)}


@pytest.mark.parametrize("event", sorted(LEFTOVER_EVENTS))
def test_only_new_bytes_lift_the_refusal(event, tmp_path):
    """Rewritten from the salvage test that planted the same text at a later
    time and expected reuse: that pinned a touch as a salvage."""
    lane = _lane()
    _refused(tmp_path, lane, _plant(tmp_path, "cov.json", BEFORE))
    act, refused = LEFTOVER_EVENTS[event]
    act(tmp_path / "cov.json")

    if refused:
        with pytest.raises(ToolError, match="wrote no artifact on its last attempt"):
            run_lane(tmp_path, lane, reuse_artifact=True)
        return
    assert run_lane(tmp_path, lane, reuse_artifact=True).provenance["exit_code"] is None


def test_a_refusal_a_0_8_0_stamp_recorded_is_judged_by_its_modification_time(tmp_path):
    """0.8.0 recorded `refused_mtime_ns` and no digest; its rule stands until
    the next run records one."""
    lane = _lane()
    _plant(tmp_path, "cov.json", BEFORE)
    write_stamps(tmp_path, {"cov.json": {"lane": "py", "refused_mtime_ns": BEFORE}})

    with pytest.raises(ToolError, match="wrote no artifact on its last attempt"):
        run_lane(tmp_path, lane, reuse_artifact=True)
    _touch(tmp_path / "cov.json")
    assert run_lane(tmp_path, lane, reuse_artifact=True).provenance["exit_code"] is None


def test_a_stamp_without_a_refusal_reuses_as_before(tmp_path):
    lane = _lane()
    _plant(tmp_path, "cov.json", BEFORE)
    write_stamps(tmp_path, {"cov.json": {"commit": "abc", "lane": "py", "seconds": 1.0}})

    outcome = run_lane(tmp_path, lane, reuse_artifact=True)

    assert outcome.provenance["parser"] == "istanbul"


def test_a_refusal_for_a_file_that_is_gone_is_the_missing_artifact_refusal(tmp_path):
    """The persisted refusal never outranks absence: a cleaned .crapkit/cov/
    still reads as `produced no artifact at`, the sentence the recover skill
    triages on."""
    lane = _lane()
    _refused(tmp_path, lane, "0" * 64)

    with pytest.raises(ToolError, match="produced no artifact at cov.json"):
        run_lane(tmp_path, lane, reuse_artifact=True)


# --- the stamp record that cannot be read ---------------------------------------

UNREADABLE = {
    "cut-short": ('{"cov.json": {"lane": "py", "refused_sha', "it does not parse as JSON"),
    "top-level-list": ('["cov.json"]', "its top level is not an object"),
    "mangled-entry": ('{"cov.json": "garbage"}', "its entry for cov.json is not an object"),
}


@pytest.mark.parametrize("shape", sorted(UNREADABLE))
def test_reuse_refuses_while_the_record_that_would_hold_a_refusal_cannot_be_read(shape, tmp_path):
    """A crash mid-write, a hand edit or a merge left a file that reads as no
    stamp, and reuse scored whatever was on disk."""
    lane = _lane()
    _plant(tmp_path, "cov.json", BEFORE)
    text, why = UNREADABLE[shape]
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / ".crapkit" / "artifacts.json").write_text(text, encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane, reuse_artifact=True)

    message = str(raised.value)
    assert f".crapkit/artifacts.json cannot be read ({why})" in message, message
    assert "coverage --lane py" in message and "delete .crapkit/artifacts.json" in message


# --- _collect_lanes: the refusal the attempt raised is persisted ----------------

def _failed_attempt(root: Path, lane: Lane) -> ToolError:
    """Run the lane and hand back the error it raised, the way `_run_one_lane`
    hands it to the fold."""
    with pytest.raises(ToolError) as raised:
        run_lane(root, lane)
    return raised.value


def _fold_one_failed(root: Path, lane: Lane, error: ToolError) -> None:
    """The fold over a run whose only lane failed. It persists the stamps and
    THEN raises `every lane failed`: a single-lane repo, the commonest shape,
    is exactly where the refusal has to survive the run dying."""
    with pytest.raises(ToolError, match="every lane failed"):
        _collect_lanes(root, [lane], {lane: (None, error)})


def test_a_failed_attempt_records_the_sha256_of_the_artifact_it_left_behind(tmp_path):
    lane = _lane("ui", "ui.json")
    digest = _plant(tmp_path, "ui.json", BEFORE)
    refusal = _failed_attempt(tmp_path, lane)
    assert "wrote no artifact this run" in str(refusal)

    _fold_one_failed(tmp_path, lane, refusal)

    assert read_stamps(tmp_path)["ui.json"] == {"lane": "ui", "refused_sha256": digest}
    assert (tmp_path / "ui.json").stat().st_mtime_ns == BEFORE, "the leftover went back as it was"


def test_the_refusal_outlives_a_deleted_stamps_file(tmp_path):
    """The store keeps a copy, so deleting .crapkit/artifacts.json does not
    hand the leftover back to reuse."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    _fold_one_failed(tmp_path, lane, _failed_attempt(tmp_path, lane))
    (tmp_path / ".crapkit" / "artifacts.json").unlink()

    with pytest.raises(ToolError, match="wrote no artifact on its last attempt"):
        run_lane(tmp_path, lane, reuse_artifact=True)


def test_a_succeeding_run_clears_the_store_s_copy_too(tmp_path):
    """A rerun can write the very bytes the refused leftover held; its stamp
    lifts the refusal in both places."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    _fold_one_failed(tmp_path, lane, _failed_attempt(tmp_path, lane))
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "ui", "seconds": 2.0})
    _collect_lanes(tmp_path, [lane], {lane: (fresh, "")})
    (tmp_path / ".crapkit" / "artifacts.json").unlink()

    assert run_lane(tmp_path, lane, reuse_artifact=True).provenance["exit_code"] is None


def test_a_lane_refused_before_its_first_attempt_records_nothing(tmp_path, monkeypatch):
    """The container guard refuses a python lane before it runs, and
    `--reuse-artifacts` is the documented way through it: a refusal that made
    no attempt must not close that door."""
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    lane = Lane(name="py", command=WRITES_NOTHING, artifact="py.json", parser="coveragepy",
                scopes=())
    _plant(tmp_path, "py.json", BEFORE)
    refusal = _failed_attempt(tmp_path, lane)
    assert "host-only" in str(refusal)

    _fold_one_failed(tmp_path, lane, refusal)

    assert "py.json" not in read_stamps(tmp_path)


def test_a_failed_lane_that_rewrote_its_artifact_records_nothing(tmp_path):
    """A lane can fail after writing: an artifact that does not parse, a junit
    that says the run did not finish. Those files are this run's, and reuse
    judges them on its own terms."""
    lane = _lane("ui", "ui.json", command="python -c \"open('ui.json', 'w').write('{')\"")
    _plant(tmp_path, "ui.json", BEFORE)
    refusal = _failed_attempt(tmp_path, lane)
    assert "wrote no artifact" not in str(refusal)

    _fold_one_failed(tmp_path, lane, refusal)

    assert "ui.json" not in read_stamps(tmp_path)
    assert (tmp_path / "ui.json").read_text(encoding="utf-8") == "{", "the attempt's own file"


def test_the_refusal_keeps_the_commit_and_duration_the_last_success_recorded(tmp_path):
    """The stamp's commit is still the artifact's provenance, since the file
    on disk IS that run's, and the duration still orders parallel starts."""
    lane = _lane("ui", "ui.json")
    write_stamps(tmp_path, {"ui.json": {"commit": "abc", "lane": "ui", "seconds": 12.5,
                                        "refused_mtime_ns": BEFORE}})
    digest = _plant(tmp_path, "ui.json", BEFORE)

    _fold_one_failed(tmp_path, lane, _failed_attempt(tmp_path, lane))

    assert read_stamps(tmp_path)["ui.json"] == {"commit": "abc", "lane": "ui", "seconds": 12.5,
                                                "refused_sha256": digest}


def test_a_refused_lane_beside_a_measured_one_is_recorded_without_ending_the_run(tmp_path):
    lane = _lane("ui", "ui.json")
    digest = _plant(tmp_path, "ui.json", BEFORE)
    other = _lane("unit", "unit.json")
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "unit", "seconds": 1.0})

    _collect_lanes(tmp_path, [other, lane],
                   {other: (fresh, ""), lane: (None, _failed_attempt(tmp_path, lane))})

    assert read_stamps(tmp_path)["ui.json"] == {"lane": "ui", "refused_sha256": digest}
    assert read_stamps(tmp_path)["unit.json"] == {"commit": "def", "lane": "unit", "seconds": 1.0}


def test_a_succeeding_lane_clears_the_refusal(tmp_path):
    lane = _lane("ui", "ui.json")
    _refused(tmp_path, lane, _plant(tmp_path, "ui.json", BEFORE))
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "ui", "seconds": 2.0})

    _collect_lanes(tmp_path, [lane], {lane: (fresh, "")})

    assert read_stamps(tmp_path)["ui.json"] == {"commit": "def", "lane": "ui", "seconds": 2.0}


def test_an_error_text_carrying_no_refusal_records_nothing(tmp_path):
    """The (None, text) shape the scheduling tests fold: an error with no
    attempt behind it has no refusal to persist."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    other = _lane("unit", "unit.json")
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "unit", "seconds": 1.0})

    _collect_lanes(tmp_path, [other, lane], {other: (fresh, ""), lane: (None, "no artifact")})

    assert "ui.json" not in read_stamps(tmp_path)


def test_the_stamps_file_is_replaced_in_one_step(tmp_path, monkeypatch):
    """A crash during a plain write left the file cut short, and with it the
    refusals it held. The text lands in a temporary file first."""
    import crapkit.lane_stamps as lane_stamps

    lane = _lane()
    _refused(tmp_path, lane, "a" * 64)
    before = (tmp_path / ".crapkit" / "artifacts.json").read_text(encoding="utf-8")

    def dies(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(lane_stamps.os, "replace", dies)
    with pytest.raises(OSError):
        _refused(tmp_path, lane, "b" * 64)

    assert (tmp_path / ".crapkit" / "artifacts.json").read_text(encoding="utf-8") == before
    assert [p.name for p in (tmp_path / ".crapkit").iterdir() if p.suffix == ".tmp"] == []


def test_the_lanes_page_and_the_changelog_quote_the_refusal_reuse_prints(tmp_path):
    """The transcript on the lanes page and the changelog sentence are the
    runner's own words; both drift silently otherwise. The two promises that
    `--reuse-artifacts` was untouched by the 0.4.12 rule are gone with them."""
    root = Path(__file__).resolve().parent.parent.parent
    lanes_page = (root / "docs" / "lanes.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    lane = Lane(name="py", command="python -m pytest --cov", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("src",))
    _refused(tmp_path, lane, _plant(tmp_path, lane.artifact, BEFORE))
    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane, reuse_artifact=True)
    printed = str(raised.value).split("; lane log:", 1)[0]

    assert printed.startswith("lane 'py' wrote no artifact on its last attempt")
    assert printed in lanes_page, "the reuse transcript on the lanes page went stale"
    # the changelog wraps its lines, so the quote is compared one space per gap
    unwrapped = " ".join(changelog.split())
    assert printed.split("lane 'py' ", 1)[1] in unwrapped, "the changelog quotes something else"
    for page in (lanes_page, changelog):
        assert "`--reuse-artifacts` is untouched" not in page, "a promise 0.5.0 broke is still made"


def test_two_leftover_files_are_named_in_the_plural(tmp_path):
    """`the cov.json, junit.xml on disk predates it and is the previous run's`
    named two files with a verb for one."""
    lane = Lane(name="py", command=WRITES_NOTHING, artifact="cov.json", parser="istanbul",
                scopes=(), results_artifact="junit.xml")
    _plant(tmp_path, "cov.json", BEFORE)
    _plant(tmp_path, "junit.xml", BEFORE, "<testsuite/>")

    message = str(_failed_attempt(tmp_path, lane))

    assert "the cov.json and junit.xml on disk predate it and are the previous run's" in message


def test_the_lanes_page_quotes_the_leftover_refusal_a_run_prints(tmp_path):
    """The page's lane declares a results file, as every lane init writes does,
    so the transcript names both files the run left."""
    lanes_page = (Path(__file__).resolve().parents[2] / "docs" / "lanes.md").read_text(
        encoding="utf-8")
    lane = Lane(name="py", command="python -c \"import sys; sys.exit(2)\"",
                artifact=".crapkit/cov/py.json", parser="coveragepy", scopes=("src",),
                results_artifact=".crapkit/cov/junit-py.xml")
    _plant(tmp_path, lane.artifact, BEFORE)
    _plant(tmp_path, lane.results_artifact, BEFORE, "<testsuite/>")

    printed = str(_failed_attempt(tmp_path, lane)).split("; lane log:", 1)[0]

    assert printed.startswith("lane 'py' wrote no artifact this run"), printed
    assert f"crapkit: lane 'py' FAILED: {printed}; lane log:" in lanes_page, printed


def test_the_lanes_page_quotes_the_unreadable_record_refusal(tmp_path, monkeypatch):
    import sys

    lanes_page = (Path(__file__).resolve().parents[2] / "docs" / "lanes.md").read_text(
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    lane = Lane(name="py", command="python -m pytest --cov", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("src",))
    _plant(tmp_path, lane.artifact, BEFORE)
    (tmp_path / ".crapkit" / "artifacts.json").write_text("{cut", encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane, reuse_artifact=True)

    assert f"crapkit: lane 'py' FAILED: {raised.value}\n" in lanes_page, str(raised.value)
