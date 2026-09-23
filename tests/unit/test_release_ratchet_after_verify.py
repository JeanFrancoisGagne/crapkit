"""A release runs the full py lane once, and checks the ratchet against that run.

Stage 1 ran `crapkit coverage` only to feed `ratchet seed` and `ratchet prune`,
and the verify stage then ran the same full lane again. Five release commits in
a row left crapkit-ratchet.tsv unchanged. Stage 1 now measures nothing. After a
passing verify, the verify stage runs seed and prune against that run and stops
the release when verify, seed or prune changes the marks the release commit
carries. A green verify tightens marks itself, so the stage compares against the
marks it read before the verify ran.
"""
import json
import shlex
import shutil
import subprocess

import pytest

from test_release_guards import capture, contracts, git, ledger, repo
from test_release_tool import release

VERSION = "0.5.2"
MARKS = "crapkit-ratchet.tsv"
SAVED = f".crapkit/release-marks-{VERSION}.tsv"
VERIFY = ("verify",)
SEED = ("ratchet", "seed")
PRUNE = ("ratchet", "prune")


def _crapkit(command):
    """The subcommand words of a `python -m crapkit ...` command, else ()."""
    return tuple(command[3:]) if tuple(command[1:3]) == ("-m", "crapkit") else ()


def _receipt(root):
    return json.loads((root / ".crapkit" / "release-receipt.json").read_text(encoding="utf-8"))


def _stage(passing=True, seeded=None, verified=None):
    """Verify records a passing run when `passing` and writes `verified` into the
    marks, the way a green verify tightens them; seed writes `seeded`."""
    writes = {VERIFY: verified, SEED: seeded}

    def effect(command, root):
        words = _crapkit(command)
        if words == VERIFY and passing:
            ledger(root)
        if writes.get(words) is not None:
            (root / MARKS).write_bytes(writes[words])
    return effect


def _carry(root, message):
    """Run the `$ ` lines of the stop message, in order, the way an operator would."""
    for line in message.splitlines():
        if line.startswith("    $ "):
            words = shlex.split(line.removeprefix("    $ "))
            if words[0] == "cp":
                shutil.copyfile(root / words[1], root / words[2])
            else:
                subprocess.run(words, cwd=root, check=True, capture_output=True)


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

    assert [_crapkit(command) for command in commands] == [VERIFY, SEED, PRUNE]
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


def test_marks_a_green_verify_tightens_stop_the_release_and_the_committed_file_returns(
        tmp_path, monkeypatch):
    committed = b"marks the release commit carries\n"
    root = _released_with_marks(tmp_path, monkeypatch, committed)
    capture(monkeypatch, effect=_stage(verified=b"tightened by verify\n"))

    with pytest.raises(release.ReleaseError, match=f"change {MARKS}"):
        release.run("verify", VERSION, root)

    assert (root / MARKS).read_bytes() == committed
    assert git(root, "status", "--porcelain") == ""
    assert "verify_run" not in _receipt(root)
    assert (root / SAVED).read_bytes() == b"tightened by verify\n"


@pytest.mark.parametrize("committed, verified, seeded", [
    (b"marks the release commit carries\n", b"tightened by verify\n", None),
    (None, None, b"first marks\n"),
], ids=["verify-tightens", "seed-creates"])
def test_the_commands_the_stop_prints_carry_the_marks_into_a_verify_that_passes(
        tmp_path, monkeypatch, committed, verified, seeded):
    root = _released_with_marks(tmp_path, monkeypatch, committed)
    capture(monkeypatch, effect=_stage(verified=verified, seeded=seeded))
    with pytest.raises(release.ReleaseError, match=f"change {MARKS}") as stop:
        release.run("verify", VERSION, root)

    _carry(root, str(stop.value))
    contracts(root, monkeypatch)
    capture(monkeypatch, effect=_stage(verified=verified, seeded=seeded))
    release.run("verify", VERSION, root)

    assert _receipt(root)["verify_run"] == 2
    assert (root / MARKS).read_bytes() == (verified or seeded)
    assert git(root, "status", "--porcelain") == ""


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
