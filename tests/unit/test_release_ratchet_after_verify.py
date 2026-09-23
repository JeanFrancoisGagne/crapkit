"""A release runs the full py lane once, and checks the ratchet against that run.

Stage 1 ran `crapkit coverage` only to feed `ratchet seed` and `ratchet prune`,
and the verify stage then ran the same full lane again. Five release commits in
a row left crapkit-ratchet.tsv unchanged. Stage 1 now measures nothing. After a
passing verify, the verify stage runs seed and prune against that run and stops
the release when either would change the marks the release commit carries.
"""
import json

import pytest

from test_release_guards import capture, contracts, git, ledger, repo
from test_release_tool import release

VERSION = "0.5.2"
MARKS = "crapkit-ratchet.tsv"
SEED = ("ratchet", "seed")
PRUNE = ("ratchet", "prune")


def _crapkit(command):
    """The subcommand words of a `python -m crapkit ...` command, else ()."""
    return tuple(command[3:]) if tuple(command[1:3]) == ("-m", "crapkit") else ()


def _receipt(root):
    return json.loads((root / ".crapkit" / "release-receipt.json").read_text(encoding="utf-8"))


def _stage(passing=True, seeded=None):
    """Verify records a passing run when `passing`; seed writes `seeded` into the marks."""
    def effect(command, root):
        words = _crapkit(command)
        if words == ("verify",) and passing:
            ledger(root)
        elif words == SEED and seeded is not None:
            (root / MARKS).write_bytes(seeded)
    return effect


def _released_with_marks(tmp_path, monkeypatch, marks=None):
    root = repo(tmp_path, bumped=True)
    if marks is not None:
        (root / MARKS).write_bytes(marks)
        git(root, "add", MARKS)
        git(root, "commit", "-qm", "marks")
    contracts(root, monkeypatch)
    return root


def test_stage1_runs_no_coverage_lane_and_stages_no_marks(tmp_path, monkeypatch):
    root = repo(tmp_path)
    commands = capture(monkeypatch)

    release.run("stage1", VERSION, root)

    assert [_crapkit(command) for command in commands if _crapkit(command)] == []
    staging = next(command for command in commands if command[:2] == ("git", "add"))
    assert MARKS not in staging


def test_the_verify_stage_runs_seed_then_prune_after_the_verify(tmp_path, monkeypatch):
    root = _released_with_marks(tmp_path, monkeypatch, b"unchanged marks\n")
    commands = capture(monkeypatch, effect=_stage(seeded=b"unchanged marks\n"))

    release.run("verify", VERSION, root)

    assert [_crapkit(command) for command in commands] == [("verify",), SEED, PRUNE]
    assert _receipt(root)["verify_run"] == 1


def test_marks_seed_would_change_stop_the_release_and_are_put_back(tmp_path, monkeypatch):
    root = _released_with_marks(tmp_path, monkeypatch, b"marks the release commit carries\n")
    capture(monkeypatch, effect=_stage(seeded=b"marks seeded from the verify run\n"))

    with pytest.raises(release.ReleaseError, match=f"change {MARKS}"):
        release.run("verify", VERSION, root)

    assert (root / MARKS).read_bytes() == b"marks the release commit carries\n"
    assert git(root, "status", "--porcelain") == ""
    assert "verify_run" not in _receipt(root)
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    assert commands == []


def test_a_marks_file_seed_would_create_stops_the_release_and_is_removed(tmp_path, monkeypatch):
    root = _released_with_marks(tmp_path, monkeypatch)
    capture(monkeypatch, effect=_stage(seeded=b"first marks\n"))

    with pytest.raises(release.ReleaseError, match=f"change {MARKS}"):
        release.run("verify", VERSION, root)

    assert not (root / MARKS).exists()


def test_seed_and_prune_never_run_without_a_new_passing_verify(tmp_path, monkeypatch):
    root = _released_with_marks(tmp_path, monkeypatch)
    commands = capture(monkeypatch, effect=_stage(passing=False))

    with pytest.raises(release.ReleaseError, match="passing full verify"):
        release.run("verify", VERSION, root)

    assert [_crapkit(command) for command in commands] == [("verify",)]


def test_the_plan_prints_the_ratchet_check_in_the_verify_stage():
    verify_stage = [step for step in release.plan(VERSION) if step.stage == "verify"]

    assert [[_crapkit(command) for command in step.commands] for step in verify_stage] == [
        [("verify",)], [SEED, PRUNE]]
