"""The reuse door after a failed attempt, at the lane runner and the fold.

`_run_attempts` refuses the artifact a run did not rewrite (0.4.12). Reuse then
read the same file back on the next command and scored it. The refused
artifact's modification time is now persisted through the stamps file, and
reuse refuses the file while its modification time still equals the refused
one; a salvage rewritten after the failure carries a new one and reuses.
"""
import json
import os
from pathlib import Path

import pytest

from crapkit.cli import _collect_lanes
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import LaneOutcome, before_attempt, read_stamps, run_lane, write_stamps

ISTANBUL = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})
BEFORE = 1_000_000_000
LATER = 2_000_000_000
LATEST = 3_000_000_000


def _lane(name: str = "py", artifact: str = "cov.json") -> Lane:
    return Lane(name=name, command="never runs", artifact=artifact, parser="istanbul", scopes=())


def _plant(root: Path, rel: str, ns: int, text: str = ISTANBUL) -> int:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(ns, ns))
    return path.stat().st_mtime_ns


def _refused(root: Path, lane: Lane, mtime_ns: int) -> None:
    write_stamps(root, {lane.artifact: {"lane": lane.name, "refused_mtime_ns": mtime_ns}})


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


def test_a_salvage_rewritten_after_the_refusal_reuses(tmp_path):
    lane = _lane()
    _refused(tmp_path, lane, _plant(tmp_path, "cov.json", BEFORE))
    _plant(tmp_path, "cov.json", LATER)

    outcome = run_lane(tmp_path, lane, reuse_artifact=True)

    assert outcome.provenance["exit_code"] is None, "a reuse, not a run"


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
    _refused(tmp_path, lane, BEFORE)

    with pytest.raises(ToolError, match="produced no artifact at cov.json"):
        run_lane(tmp_path, lane, reuse_artifact=True)


# --- _collect_lanes: the refusal is persisted through the stamps ---------------

def _attempted(root: Path, lane: Lane, ns: int) -> None:
    """What every attempt does to the lane log: truncates and rewrites it."""
    _plant(root, f".crapkit/lane-{lane.name}.log", ns, text="$ never runs\n(exit 2)\n")


def _fold_one_failed(root: Path, lane: Lane, error: str, before: dict) -> None:
    """The fold over a run whose only lane failed. It persists the stamps and
    THEN raises `every lane failed`: a single-lane repo, the commonest shape,
    is exactly where the refusal has to survive the run dying."""
    with pytest.raises(ToolError, match="every lane failed"):
        _collect_lanes(root, [lane], {lane: (None, error)}, before)


def test_a_failed_attempt_records_the_mtime_of_the_artifact_it_left_behind(tmp_path):
    lane = _lane("ui", "ui.json")
    mtime = _plant(tmp_path, "ui.json", BEFORE)
    before = {lane: before_attempt(tmp_path, lane)}
    _attempted(tmp_path, lane, LATEST)

    _fold_one_failed(tmp_path, lane, "wrote no artifact this run", before)

    assert read_stamps(tmp_path)["ui.json"]["refused_mtime_ns"] == mtime
    assert read_stamps(tmp_path)["ui.json"]["lane"] == "ui"


def test_a_lane_refused_before_its_first_attempt_records_nothing(tmp_path):
    """The container guard refuses a python lane before it runs, and
    `--reuse-artifacts` is the documented way through it: a refusal that made
    no attempt must not close that door. The untouched lane log says no
    attempt happened."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    _attempted(tmp_path, lane, BEFORE)
    before = {lane: before_attempt(tmp_path, lane)}

    _fold_one_failed(tmp_path, lane, "host-only", before)

    assert "refused_mtime_ns" not in read_stamps(tmp_path).get("ui.json", {})


def test_a_failed_lane_that_rewrote_its_artifact_records_nothing(tmp_path):
    """A lane can fail after writing: a junit that says the run did not finish,
    an artifact describing another tree. Those files are this run's, and reuse
    judges them on its own terms."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    before = {lane: before_attempt(tmp_path, lane)}
    _attempted(tmp_path, lane, LATER)
    _plant(tmp_path, "ui.json", LATEST)

    _fold_one_failed(tmp_path, lane, "worker 'gw1' crashed", before)

    assert "refused_mtime_ns" not in read_stamps(tmp_path).get("ui.json", {})


def test_the_refusal_keeps_the_commit_and_duration_the_last_success_recorded(tmp_path):
    """The stamp's commit is still the artifact's provenance, since the file
    on disk IS that run's, and the duration still orders parallel starts."""
    lane = _lane("ui", "ui.json")
    write_stamps(tmp_path, {"ui.json": {"commit": "abc", "lane": "ui", "seconds": 12.5}})
    mtime = _plant(tmp_path, "ui.json", BEFORE)
    before = {lane: before_attempt(tmp_path, lane)}
    _attempted(tmp_path, lane, LATEST)

    _fold_one_failed(tmp_path, lane, "wrote no artifact this run", before)

    assert read_stamps(tmp_path)["ui.json"] == {"commit": "abc", "lane": "ui", "seconds": 12.5,
                                                "refused_mtime_ns": mtime}


def test_a_refused_lane_beside_a_measured_one_is_recorded_without_ending_the_run(tmp_path):
    lane = _lane("ui", "ui.json")
    mtime = _plant(tmp_path, "ui.json", BEFORE)
    other = _lane("unit", "unit.json")
    before = {lane: before_attempt(tmp_path, lane), other: before_attempt(tmp_path, other)}
    _attempted(tmp_path, lane, LATEST)
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "unit", "seconds": 1.0})

    _, _, errors, succeeded = _collect_lanes(
        tmp_path, [other, lane], {other: (fresh, ""), lane: (None, "wrote no artifact")}, before)

    assert errors == {"ui": "wrote no artifact"} and [l.name for l in succeeded] == ["unit"]
    assert read_stamps(tmp_path)["ui.json"] == {"lane": "ui", "refused_mtime_ns": mtime}
    assert read_stamps(tmp_path)["unit.json"] == {"commit": "def", "lane": "unit", "seconds": 1.0}


def test_a_succeeding_lane_clears_the_refusal(tmp_path):
    lane = _lane("ui", "ui.json")
    _refused(tmp_path, lane, _plant(tmp_path, "ui.json", BEFORE))
    before = {lane: before_attempt(tmp_path, lane)}
    _attempted(tmp_path, lane, LATER)
    _plant(tmp_path, "ui.json", LATEST)
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "ui", "seconds": 2.0})

    _collect_lanes(tmp_path, [lane], {lane: (fresh, "")}, before)

    assert read_stamps(tmp_path)["ui.json"] == {"commit": "def", "lane": "ui", "seconds": 2.0}


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
    printed = str(raised.value).split("; full log:", 1)[0]

    assert printed.startswith("lane 'py' wrote no artifact on its last attempt")
    assert printed in lanes_page, "the reuse transcript on the lanes page went stale"
    # the changelog wraps its lines, so the quote is compared one space per gap
    unwrapped = " ".join(changelog.split())
    assert printed.split("lane 'py' ", 1)[1] in unwrapped, "the changelog quotes something else"
    for page in (lanes_page, changelog):
        assert "`--reuse-artifacts` is untouched" not in page, "a promise 0.5.0 broke is still made"


def test_without_a_snapshot_the_fold_records_no_refusal(tmp_path):
    """The three-argument call the scheduling tests make: nothing was measured
    before the lanes ran, so nothing is judged."""
    lane = _lane("ui", "ui.json")
    _plant(tmp_path, "ui.json", BEFORE)
    _attempted(tmp_path, lane, LATEST)
    other = _lane("unit", "unit.json")
    fresh = LaneOutcome({}, {"scopes": []}, {"commit": "def", "lane": "unit", "seconds": 1.0})

    _collect_lanes(tmp_path, [other, lane], {other: (fresh, ""), lane: (None, "no artifact")})

    assert "ui.json" not in read_stamps(tmp_path)
