"""Zero stops on measured corpora: every number crapkit computes from real code meets its bound.

The expected value is zero stops, from the definitions the checks cite: a
measured number past a documented bound is a crapkit bug or a bound the docs
got wrong, never noise, and each one is investigated before it is ruled on.

Push measures the kit's seed corpus (kit/fixtures/seed: Python and TypeScript,
scored from recorded artifacts) through every command that reaches a check.
Nightly runs every member of the full corpus (corpora.py) the same way.
"""
from __future__ import annotations

import pytest

from accuracy.kit import drive
from accuracy.runtime_guards import corpora


def _recorded_stops(run) -> list[str]:
    names = [path.name for path in run.outputs.glob("*.stderr")]
    return [f"{name}: {line}" for name in names
            for line in run.output(name).splitlines() if corpora.STOP in line]


@pytest.mark.process
def test_the_seed_corpus_surfaces_meet_every_bound(seed):
    assert _recorded_stops(seed) == []
    # README "Exit 1 means one of three things": doctor's 1 is a FAIL finding, a verdict.
    allowed = {"doctor.json": 1}
    assert {name: code for name, code in seed.codes.items() if code != allowed.get(name, 0)} == {}


def _handles(driver) -> list[tuple[str, str]]:
    listed = driver.json("worklist", "--top", "50")
    return [(entry["path"], entry["handle"]) for entry in listed["active"] + listed["dormant_top"]]


def _answers(driver) -> list:
    done = [driver.run(*argv) for argv in corpora.COMMANDS]
    return done + [driver.run("brief", path, handle, "--json") for path, handle in _handles(driver)]


@pytest.mark.process
def test_every_checked_command_answers_on_the_seed_corpus(seed, tmp_path):
    done = _answers(drive.Driver(seed.private_copy(tmp_path / "seed"), date_now=seed.date_now))
    assert [stop for result in done for stop in corpora.stops(result)] == []
    assert [(result.argv, result.code) for result in done if result.code != 0] == []


def member_stops(member, work) -> list[str]:
    driver = drive.Driver(corpora.member_repo(member, work), spawn=True)
    done = [driver.run(*argv) for argv in corpora.FIRST_RUN] + _answers(driver)
    return [f"{member.name}: {stop}" for result in done for stop in corpora.stops(result)]


@pytest.mark.nightly
@pytest.mark.process
def test_zero_stops_on_every_full_corpus_member(tmp_path):
    root = corpora.full_corpus()
    members = corpora.members(root)
    assert members, (f"no full corpus at {root}: set {corpora.CORPUS_ENV}, or restore the "
                     "corpus-<digest> asset tools/accuracy/corpus.py publishes")
    found = [stop for member in members for stop in member_stops(member, tmp_path / member.name)]
    assert found == []
