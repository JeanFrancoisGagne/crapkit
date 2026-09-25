"""The run freshness verdict every payload about a scored run carries.

`stale` is the commit question and `scored_changes` the content question, and
one function answers both for next-item, brief, brief --batch, worklist and the
report page, so no two payloads can disagree about one run. These pin the
verdict itself, the stderr lines the plain worklist prints from it, and what a
run that recorded no content reads as: null, with the refresh beside it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import lane_sources
from crapkit.cli import queue
from crapkit.cli.queue import RunFreshness, _freshness_warnings, run_freshness
from crapkit.errors import GitError
from hand_scored_repo import make_repo, run, scored, write_run

REFRESH = {"refresh": "crapkit coverage --reuse-unchanged"}
LATEST = {"id": 4, "commit": "abc123def4567890"}


def _store(record: dict | None) -> SimpleNamespace:
    return SimpleNamespace(run_sources=lambda run_id: record)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A committed repo holding FILES."""
    return make_repo(tmp_path / "repo", files=files)


# --- the verdict ---------------------------------------------------------------

def test_the_commit_and_the_content_are_two_answers(tmp_path):
    root = _tree(tmp_path, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"})
    record = lane_sources.record(root, ["src/a.py", "src/b.py"])
    (root / "src" / "b.py").write_text("b = 2\n", encoding="utf-8")

    fresh = run_freshness(root, _store(record), LATEST, LATEST["commit"])

    assert fresh == RunFreshness(False, ["src/b.py"])
    assert fresh.fields() == {"stale": False, "scored_changes": 1}


def test_a_moved_head_over_unmoved_content_is_stale_with_nothing_changed(tmp_path):
    root = _tree(tmp_path, {"src/a.py": "a = 1\n"})
    record = lane_sources.record(root, ["src/a.py"])

    fresh = run_freshness(root, _store(record), LATEST, "f" * 40)

    assert fresh.fields() == {"stale": True, "scored_changes": 0}


def test_a_deleted_scored_file_counts_as_changed(tmp_path):
    root = _tree(tmp_path, {"src/a.py": "a = 1\n"})
    record = lane_sources.record(root, ["src/a.py"])
    (root / "src" / "a.py").unlink()

    assert run_freshness(root, _store(record), LATEST, LATEST["commit"]).changed == ["src/a.py"]


def test_a_run_that_recorded_no_content_reads_null(tmp_path):
    assert run_freshness(tmp_path, _store(None), LATEST, LATEST["commit"]).fields() == {
        "stale": False, "scored_changes": None}


def test_a_git_failure_reads_null_and_carries_git_s_error(tmp_path, monkeypatch):
    """A failed read is neither "changed" nor "unchanged": the count is null,
    as for a run that recorded nothing, and the error rides with it."""
    root = _tree(tmp_path, {"src/a.py": "a = 1\n"})
    record = lane_sources.record(root, ["src/a.py"])

    def refuse(*args, **kwargs):
        raise GitError("git diff failed: fatal: index file corrupt")

    monkeypatch.setattr(lane_sources, "moved", refuse)
    fresh = run_freshness(root, _store(record), LATEST, LATEST["commit"])

    assert fresh.fields() == {"stale": False, "scored_changes": None}
    assert fresh.unread == "git diff failed: fatal: index file corrupt"


def test_the_envelope_adds_the_refresh_the_packet_spells():
    from crapkit.packet import commands

    envelope = RunFreshness(True, []).envelope()

    assert envelope == {"stale": True, "scored_changes": 0, "commands": REFRESH}
    assert envelope["commands"]["refresh"] == commands("a.py", False)["refresh"]


# --- the plain worklist's stderr lines -------------------------------------------

@pytest.mark.parametrize("fresh,expected", [
    (RunFreshness(False, []), []),
    (RunFreshness(False, None), []),
    (RunFreshness(True, []), ["warning: snapshot is for abc123def45, HEAD has moved on"]),
    (RunFreshness(False, ["src/a.py"]),
     ["warning: 1 file(s) changed since run 4 scored them: src/a.py"]),
    (RunFreshness(True, ["src/a.py", "src/b.py"]),
     ["warning: snapshot is for abc123def45, HEAD has moved on",
      "warning: 2 file(s) changed since run 4 scored them: src/a.py, src/b.py"]),
], ids=["fresh", "no-record", "head-moved", "one-changed", "both"])
def test_each_way_the_run_went_stale_gets_its_own_line(fresh, expected):
    lines = _freshness_warnings(fresh, LATEST)

    assert [line.split(" — ")[0] for line in lines] == expected
    assert all(line.endswith("coverage`") for line in lines), lines


def test_a_git_failure_gets_a_line_quoting_git_instead_of_silence():
    fresh = RunFreshness(False, None, "git diff failed: fatal: index file corrupt")

    assert _freshness_warnings(fresh, LATEST) == [
        "warning: cannot tell which files changed since run 4 scored them, because git "
        "failed: git diff failed: fatal: index file corrupt"]


# --- every payload, off a run that recorded no content ---------------------------

@pytest.fixture()
def unrecorded(tmp_path: Path) -> Path:
    """A run written straight into the store, as crapkit 0.8.0 wrote every run:
    rows and a commit, no content record."""
    root = make_repo(tmp_path / "repo")
    write_run(root, [scored("parse( text , sep )", 1, 20, ccn=9, cov=0.0, crap=90.0,
                            remedy="decompose")])
    return root


def _payload(root: Path, capsys, *argv: str) -> dict:
    code, out, err = run(root, capsys, *argv)
    assert code == 0, err
    return json.loads(out)


def test_every_payload_says_null_and_names_the_refresh_for_an_unrecorded_run(unrecorded, capsys):
    payloads = {"next-item": _payload(unrecorded, capsys, "next-item"),
                "brief --batch": _payload(unrecorded, capsys, "brief", "--batch", "1"),
                "worklist": _payload(unrecorded, capsys, "worklist", "--json")}
    packet = _payload(unrecorded, capsys, "brief", "src/app.py", "parse", "--json")

    assert {name: (p["scored_changes"], p["commands"]) for name, p in payloads.items()} == \
        dict.fromkeys(payloads, (None, REFRESH))
    assert packet["scored_changes"] is None
    assert packet["commands"]["refresh"] == REFRESH["refresh"]


def test_an_unrecorded_run_prints_no_content_warning(unrecorded, capsys):
    code, _out, err = run(unrecorded, capsys, "worklist")

    assert code == 0, err
    assert "changed since run" not in err


def test_a_batch_reads_the_record_once_for_every_packet(unrecorded, capsys, monkeypatch):
    write_run(unrecorded, [scored("parse( text , sep )", 1, 20, ccn=9, cov=0.0, crap=90.0,
                                  remedy="decompose"),
                           scored("split( text )", 22, 40, ccn=8, cov=0.0, crap=72.0,
                                  remedy="decompose")])
    calls: list = []
    real = queue.run_freshness
    monkeypatch.setattr(queue, "run_freshness", lambda *a: calls.append(a) or real(*a))

    out = _payload(unrecorded, capsys, "brief", "--batch", "2")

    assert len(out["packets"]) == 2
    assert len(calls) == 1, "one verdict per batch, not one per packet"
