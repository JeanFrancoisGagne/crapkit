"""watch: which tracked files a poll names, and what the rescore it starts reads.

Expected values come from the README command table and from os.stat, never
from a crapkit run:
- README "Commands", `watch [--interval SECONDS] [--cycles N]`: it rescores
  tracked files as they change, by mtime polling, and `--cycles N` polls
  exactly N times and exits 0.
- A file changed for a poll when its st_mtime differs from the previous poll's,
  or it appeared or went away (os.stat is the model).
- A file git does not track, or no scope claims, is never named.
- A touch that leaves the bytes alone names the file once and moves no number:
  the rescore it starts prints what `crapkit rescore` printed before the touch.
- An edit's rescore reads the hand value: McCabe counts one `if` as one
  decision, so ccn 2, and a cc-only scope scores crap = ccn (README "Languages").

The loop sleeps between polls. The tests replace time.sleep in-process with a
step that changes the tree, so each poll sees exactly the planned change.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive

ONE_IF = "def {name}(x):\n    if x:\n        return 1\n    return 2\n"
FILES = {"crapkit.toml": analysis_inventory.config(("python",)),
         "a.py": ONE_IF.format(name="a"),
         "b.py": "def b():\n    return 1\n",
         "pkg/c.py": "def c():\n    return 3\n",
         "notes.md": "not a source file\n"}
# Tracked files the one python scope claims: a.py, b.py and pkg/c.py.
CLAIMED = 3
CHANGED = re.compile(r"^--- changed: (.+)$", re.MULTILINE)
BANNER = re.compile(r"^watching (\d+) tracked files .* (\d+) poll\(s\) then stop$", re.MULTILINE)


def _touch(root: Path, path: str) -> None:
    """A new mtime two seconds on, the same bytes."""
    later = os.stat(root / path).st_mtime_ns + 2_000_000_000
    os.utime(root / path, ns=(later, later))


def _write(root: Path, path: str, text: str) -> None:
    (root / path).write_text(text, encoding="utf-8")
    _touch(root, path)


@pytest.fixture
def measured_repo(tmp_path):
    root = analysis_inventory.build(FILES, tmp_path / "repo")
    driver = drive.Driver(root)
    assert driver.run("coverage").code == 0
    return root, driver


def _watch(driver: drive.Driver, monkeypatch, steps: list):
    """`crapkit watch --cycles len(steps)`, where poll i first runs steps[i]."""
    plan = iter(steps)
    slept = []

    def step(seconds):
        slept.append(seconds)
        action = next(plan)
        if action is not None:
            action(driver.root)

    monkeypatch.setattr(time, "sleep", step)
    done = driver.run("watch", "--interval", "0", "--cycles", str(len(steps)))
    monkeypatch.undo()
    return done, slept


def _named(stdout: str) -> list[str]:
    return [path for line in CHANGED.findall(stdout) for path in line.split(", ")]


def _rows(stdout: str, path: str) -> list[str]:
    """The rescore table rows for `path`, as printed."""
    return [line for line in stdout.splitlines() if f" {path}:" in line]


@pytest.mark.process
def test_cycles_polls_exactly_n_times_and_exits_zero(measured_repo, monkeypatch):
    _, driver = measured_repo
    done, slept = _watch(driver, monkeypatch, [None, None, None])
    assert (done.code, len(slept), _named(done.stdout)) == (0, 3, [])
    assert BANNER.search(done.stdout).groups() == (str(CLAIMED), "3")


@pytest.mark.process
def test_a_touch_names_the_file_once_and_moves_no_number(measured_repo, monkeypatch):
    root, driver = measured_repo
    before = driver.run("rescore", "a.py")
    assert before.code == 0, before.stderr
    done, _ = _watch(driver, monkeypatch, [lambda top: _touch(top, "a.py"), None, None])
    assert _named(done.stdout) == ["a.py"]
    assert _rows(done.stdout, "a.py") == _rows(before.stdout, "a.py") != []


@pytest.mark.process
def test_an_edit_rescores_to_the_hand_value(measured_repo, monkeypatch):
    _, driver = measured_repo
    edit = lambda top: _write(top, "b.py", ONE_IF.format(name="b"))  # noqa: E731
    done, _ = _watch(driver, monkeypatch, [edit])
    rows = _rows(done.stdout, "b.py")
    assert _named(done.stdout) == ["b.py"] and len(rows) == 1
    ccn, _cov, crap = rows[0].split()[:3]
    assert (int(ccn), float(crap)) == (2, 2.0)


@pytest.mark.process
def test_untracked_and_unclaimed_files_are_never_named(measured_repo, monkeypatch):
    _, driver = measured_repo
    steps = [lambda top: _write(top, "new.py", "def n():\n    return 1\n"),
             lambda top: _touch(top, "notes.md"),
             lambda top: _touch(top, "new.py")]
    done, _ = _watch(driver, monkeypatch, steps)
    assert (done.code, _named(done.stdout)) == (0, [])


@pytest.mark.process
def test_two_files_in_one_poll_are_named_together_once(measured_repo, monkeypatch):
    _, driver = measured_repo
    both = lambda top: (_touch(top, "a.py"), _touch(top, "pkg/c.py"))  # noqa: E731
    done, _ = _watch(driver, monkeypatch, [both, None])
    assert sorted(_named(done.stdout)) == ["a.py", "pkg/c.py"]
    assert len(CHANGED.findall(done.stdout)) == 1
