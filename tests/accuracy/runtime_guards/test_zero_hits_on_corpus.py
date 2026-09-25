"""Zero stops on measured corpora: every number crapkit computes from real code meets its bound.

The expected value is zero stops, from the definitions the checks cite: a
measured number past a documented bound is a crapkit bug or a bound the docs
got wrong, never noise, and each one is investigated before it is ruled on.

Push measures the kit's seed corpus (kit/fixtures/seed: Python and TypeScript,
scored from recorded artifacts) through every command that reaches a check,
and a small repo with a dated history, whose commits weigh under 0.5 each.
Nightly runs every member of the full corpus (corpora.py) the same way, and
every member history bundle over its real log.
"""
from __future__ import annotations

import pytest

from accuracy.kit import drive, repos, rulings
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
    listed = driver.json("worklist", "--top", "10")
    return [(entry["path"], entry["handle"]) for entry in listed["active"] + listed["dormant_top"]]


def _answers(driver) -> list:
    done = [driver.run(*argv) for argv in corpora.COMMANDS]
    return done + [driver.run("brief", path, handle, "--json") for path, handle in _handles(driver)]


@pytest.mark.process
def test_every_checked_command_answers_on_the_seed_corpus(seed, tmp_path):
    done = _answers(drive.Driver(seed.private_copy(tmp_path / "seed"), date_now=seed.date_now))
    assert [stop for result in done for stop in corpora.stops(result)] == []
    assert [(result.argv, result.code) for result in done if result.code != 0] == []


def repo_stops(root, name: str, date_now: int | None = None) -> list[str]:
    """Every stop the checked commands print on a repo that holds no run yet."""
    driver = drive.Driver(root, date_now=date_now, spawn=True)
    done = [driver.run(*argv) for argv in corpora.FIRST_RUN] + _answers(driver)
    return [f"{name}: {stop}" for result in done for stop in corpora.stops(result)]


DAY = 86_400
# ccn 9 (eight ifs): over the cc-only target of 6, so every file's row is admitted.
BODY = "\n".join(["def pick(a):"]
                 + [f"    if a == {n}:\n        return {n}" for n in range(8)]
                 + ["    return -1", ""])


def dated_history() -> repos.Spec:
    """Four files, touched four, three, two and one times over 30 days: a log
    with a range, where README "Risk" weighs each commit at most 0.5."""
    first = {f"src/{name}.py": BODY for name in "abcd"}
    first["crapkit.toml"] = corpora.cc_only_config()
    steps = [repos.Commit(first, date=repos.EPOCH)]
    for day, names in ((10, "abc"), (20, "ab"), (30, "a")):
        edits = {f"src/{name}.py": f"{BODY}# day {day}\n" for name in names}
        steps.append(repos.Commit(edits, date=repos.EPOCH + day * DAY))
    return repos.Spec(tuple(steps))


NEWEST = repos.EPOCH + 30 * DAY  # dated_history's newest commit


def _active(root, date_now: int) -> list[dict]:
    return drive.Driver(root, date_now=date_now).json("worklist")["active"]


def _past_half(weights: dict) -> dict:
    """README "Risk": with a range, a file weighs 0 to 0.5 per commit."""
    return {path: w for path, (commits, w) in weights.items() if not 0 <= w <= 0.5 * commits}


@pytest.mark.process
def test_a_dated_history_s_worklist_meets_the_churn_bound(make_repo):
    root = make_repo(dated_history()).root
    assert repo_stops(root, "dated history", NEWEST + DAY) == []
    listed = _active(root, NEWEST + DAY)
    weights = {entry["path"]: (entry["commits"], entry["weight"]) for entry in listed}
    assert sorted(commits for commits, _ in weights.values()) == [1, 2, 3, 4]
    assert _past_half(weights) == {}
    fractional = [w for _, w in weights.values() if not float(w).is_integer()]
    assert fractional, "a log with a range weighs some commit under 1: the check read them"


def _ranked(make_repo, days: int) -> str:
    """How many files the worklist ranks when the tree is first measured `days`
    after its newest commit: a fresh repo, so no churn cache carries over."""
    root = make_repo(dated_history()).root
    driver = drive.Driver(root, date_now=NEWEST + days * DAY)
    for argv in corpora.FIRST_RUN:
        assert driver.run(*argv).code == 0
    return f"{days} days: {len(driver.json('worklist')['active'])} active"


@rulings.applies("RG5")
@pytest.mark.process
def test_rg5_a_fixed_tree_ranks_identically_a_year_later(make_repo):
    """README "Risk": the window anchors on the newest commit, never on the
    wall clock, so a fixed tree ranks identically forever."""
    assert _ranked(make_repo, 1) == "1 days: 4 active"
    rulings.pin_ruling("RG5", crapkit=_ranked(make_repo, 400), oracle="400 days: 4 active")


@pytest.mark.nightly
@pytest.mark.process
def test_zero_stops_on_every_full_corpus_member(tmp_path):
    root = corpora.full_corpus()
    members = corpora.members(root)
    assert members, (f"no full corpus at {root}: set {corpora.CORPUS_ENV}, or restore the "
                     "corpus-<digest> asset tools/accuracy/corpus.py publishes")
    found = [stop for member in members
             for stop in repo_stops(corpora.member_repo(member, tmp_path / member.name),
                                    member.name)]
    assert found == []


@pytest.mark.nightly
@pytest.mark.process
def test_zero_stops_on_every_member_history(tmp_path):
    bundles = corpora.histories(corpora.full_corpus())
    assert bundles, f"no history bundles under {corpora.full_corpus()}: set {corpora.CORPUS_ENV}"
    found = [stop for bundle in bundles
             for stop in repo_stops(corpora.history_repo(bundle, tmp_path / bundle.stem),
                                    bundle.stem)]
    assert found == []
