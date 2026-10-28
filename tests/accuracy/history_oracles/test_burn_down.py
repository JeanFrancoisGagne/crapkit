"""The ratchet burn-down and mark age, against the file's history read version by
version and a hand table.

oracles/marks_history_walk.py reads every committed revision of the ratchet
file whole through pygit2 and diffs the mark sets; hand_burn_down.tsv works the
same report out by hand from repos/history_specs.py (BURN, BURN_TIGHTEN).
crapkit is read through `ratchet report --json` and its text. This file imports
no crapkit.

tools/accuracy/pins.toml pins pygit2 for the nightly tier, so every check that
walks a history is nightly and asks for the oracle, as test_renames.py's
pygit2 check does. The hand table, the debt policy, the text report and the
surfaces that print a mark's age run on every push.
"""
from __future__ import annotations

import codecs
import csv
from pathlib import Path
import re

import pytest

from accuracy.kit import drive, repos
from accuracy.history_oracles.oracles import marks_history_walk
from accuracy.history_oracles.repos import history_specs as specs

HERE = Path(__file__).resolve().parent
MARKS = "crapkit-ratchet.tsv"
NOW = specs.EPOCH + 100 * specs.DAY
pytestmark = pytest.mark.process


# Keys of `ratchet report --json` that are not burn-down numbers: `shallow` is a
# fact about the clone, checked on its own below.
NOT_BURN_DOWN = ("schema", "policy_violations", "shallow")


def said(root: Path, *flags: str) -> tuple[int, dict]:
    result = drive.Driver(root, date_now=NOW).run("ratchet", "report", "--json", *flags)
    assert result.code in (0, 1), result.stderr
    payload = result.json()
    return result.code, {key: payload[key] for key in payload if key not in NOT_BURN_DOWN}


def _oldest_text(rows: list[dict]) -> str:
    return "; ".join(f"{row['path']} {row['long_name']} {row['age_days']}" for row in rows)


def _hand(history: str) -> dict[str, str]:
    with (HERE / "hand_burn_down.tsv").open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        return {row["field"]: row["value"] for row in rows if row["history"] == history}


def _as_hand(report: dict, fields) -> dict[str, str]:
    shown = {**report, "oldest": _oldest_text(report["oldest"])}
    return {field: str(shown[field]) for field in fields}


@pytest.fixture
def burned(make_repo):
    built = make_repo(specs.BURN)
    (built.root / MARKS).write_bytes(specs.BURN_WORKING.encode("utf-8"))
    return built


@pytest.mark.nightly
def test_report_matches_the_history_walk(burned, oracle):
    oracle("pygit2")
    _, report = said(burned.root)

    assert report == marks_history_walk.report(burned.root, MARKS)


def test_report_matches_the_hand_table(burned):
    _, report = said(burned.root)

    hand = _hand("BURN")
    assert _as_hand(report, hand) == hand


def test_value_changes_move_mark_age(make_repo):
    """The newest commit only tightens a mark: it is still the newest commit that
    touched the file, so every age is measured back from it (R32)."""
    built = make_repo(specs.BURN_TIGHTEN)

    _, report = said(built.root)

    hand = _hand("BURN_TIGHTEN")
    assert _as_hand(report, hand) == hand


def _depth_one_clone(root: Path, top: Path) -> Path:
    """A depth-1 clone of `root`'s history: git shortens a clone only over a
    transport, so the source is named as a file:// URL."""
    repos.git(top, "clone", "-q", "--depth", "1", root.resolve().as_uri(), "shallow")
    return top / "shallow"


@pytest.mark.parametrize("depth", ["full", "depth-1"])
def test_shallow_is_what_git_says_of_the_clone(burned, tmp_path, depth):
    """docs/agent-json.md, ratchet report --json: `shallow` is true in a shallow
    clone, where every age and repayment counts only the commits it holds."""
    root = _depth_one_clone(burned.root, tmp_path) if depth == "depth-1" else burned.root
    result = drive.Driver(root, date_now=NOW).run("ratchet", "report", "--json")
    assert result.code in (0, 1), result.stderr

    assert result.json()["shallow"] == marks_history_walk.shallow(root) == (depth == "depth-1")


_AGE = re.compile(r"^mark (.+) in (\S+) is \d+d old")


def _kind(finding: str) -> str:
    match = _AGE.match(finding)
    return f"age {match.group(2)} {match.group(1)}" if match else (
        "stalled" if finding.startswith("repayment stalled") else finding)


# debt_max_age_months 2 is 60 days at 30 a month, and fa entered 75 days before
# the anchor; b and c were repaid in the 30 days before it, and 2 is under 3.
FLAGGED = ["age src/a.py fa( x )", "stalled"]


def test_the_debt_policy_flags_what_the_docs_say(burned):
    config = burned.root / "crapkit.toml"
    policy = "[crapkit]\ndebt_max_age_months = 2\nrepayment_min_per_30d = 3\n"
    config.write_bytes(config.read_bytes().replace(b"[crapkit]\n", policy.encode("utf-8")))
    result = drive.Driver(burned.root, date_now=NOW).run("ratchet", "report", "--json",
                                                          "--enforce")

    assert result.code == 1
    assert [_kind(finding) for finding in result.json()["policy_violations"]] == FLAGGED


@pytest.mark.nightly
def test_the_history_walk_flags_what_the_docs_say(burned, oracle):
    oracle("pygit2")
    found = marks_history_walk.report(burned.root, MARKS)

    assert marks_history_walk.violations(found, 2, 3) == FLAGGED


_HEAD = re.compile(r"(\d+) open mark\(s\), (\d+) repaid \((\d+) in the last 30d, (\d+) in 90d\)")
_ROW = re.compile(r"^\s+(\d+)d  (\S+)  (.+)$", re.M)


@pytest.mark.cross_surface
def test_the_text_report_says_what_the_json_says(burned):
    _, report = said(burned.root)
    text = drive.Driver(burned.root, date_now=NOW).run("ratchet", "report").stdout

    head = tuple(map(int, _HEAD.search(text).groups()))
    assert head == (report["open"], report["dropped_total"], report["dropped_last_30d"],
                    report["dropped_last_90d"])
    assert [(int(age), path, name) for age, path, name in _ROW.findall(text)] == [
        (row["age_days"], row["path"], row["long_name"]) for row in report["oldest"][:10]]


def _tightened(make_repo) -> drive.Driver:
    """Seeded and committed on day 0; fa's mark tightened and committed on day 40."""
    built = make_repo(specs.BURN_SEEDABLE)
    driver = drive.Driver(built.root, date_now=NOW)
    assert driver.run("coverage").code == 0 and driver.run("ratchet", "seed").code == 0
    marks = built.root / MARKS
    _commit(built, marks.read_bytes(), specs.EPOCH)
    _commit(built, marks.read_bytes().replace(b"fa( x )\t5.0000", b"fa( x )\t4.5000"),
            specs.EPOCH + 40 * specs.DAY)
    return driver


def _commit(built: repos.Built, data: bytes, date: int) -> None:
    (built.root / MARKS).write_bytes(data)
    repos.git(built.root, "add", "--", MARKS)
    repos.git(built.root, "commit", "-q", "-m", "marks", date=date)


@pytest.mark.cross_surface
def test_brief_and_mcp_read_the_age_the_report_reads(make_repo):
    driver = _tightened(make_repo)

    report = driver.json("ratchet", "report")
    brief = driver.json("brief", "src/a.py", "fa")
    mcp = driver.mcp([("get_ratchet_report", {})])[0]["structuredContent"]

    assert brief["gate_rule"]["mark_age_days"] == 40
    assert {key: mcp[key] for key in report} == report


@pytest.mark.nightly
def test_the_history_walk_reads_the_age_of_a_mark_seed_wrote(make_repo, oracle):
    """The marks crapkit's own seed wrote: fa's mark keeps its entry date
    through the tightening 40 days later."""
    oracle("pygit2")
    driver = _tightened(make_repo)

    walked = marks_history_walk.report(driver.root, MARKS)

    ages = {(row["path"], row["long_name"]): row["age_days"] for row in walked["oldest"]}
    assert ages[("src/a.py", "fa( x )")] == 40
    assert said(driver.root)[1] == walked


# --- the report against the walk at every commit ------------------------------
# Check 1: at each commit, checked out in turn, `ratchet report --json` (the
# report crapkit computes from mark_events over the file's revisions) equals
# the walk's, on BURN, BURN_TIGHTEN and a history that saves the file every way
# docs/ratchet.md#how-the-file-is-read names: a UTF-8 revision, a cp1252 byte in
# one mark's name, a PowerShell 5.1 UTF-16 LE resave (byte-order mark, CRLF)
# that also adds a mark, a `git mv` of the file, a re-key, a commit that
# tightens one value and repays one mark, and one that adds that mark again.
# tests/unit/test_marks_history.py builds the same marks history and asserts
# its events by hand. Before the git mv the file is old-marks.tsv, which
# crapkit.toml names; the walk then reads it under that name.

OLD = "old-marks.tsv"
CAFE = "caf�( n )"
BASE = (("a( n )", "20.0"), ("b( n )", "20.0"), ("c( n )", "20.0"))


def _rows(*marked: tuple[str, str]) -> bytes:
    return specs.marks_file(*(("src/a.py", name, crap) for name, crap in marked)).encode("utf-8")


def _naming(marks_file: str) -> str:
    return specs.config().replace("[crapkit]\n", f'[crapkit]\nratchet_file = "{marks_file}"\n', 1)


WITH_CAFE = _rows(*BASE) + b"src/a.py\tcaf\xe9( n )\t5.0\n"
UTF16 = codecs.BOM_UTF16_LE + (WITH_CAFE.decode("utf-8", "replace") + "src/a.py\td( n )\t12.0\n"
                               ).replace("\n", "\r\n").encode("utf-16-le")
REKEYED = (("a( n )", "20.0"), ("b( n )#2", "20.0"), ("c( n )", "20.0"), (CAFE, "5.0"),
           ("d( n )", "12.0"))
TIGHTENED = (("a( n )", "10.0"), ("b( n )#2", "20.0"), (CAFE, "5.0"), ("d( n )", "12.0"))


def _day(n: int) -> int:
    return specs.EPOCH + n * specs.DAY


SAVED_EVERY_WAY = repos.Spec(steps=(
    repos.Commit(files={"crapkit.toml": _naming(OLD), OLD: _rows(*BASE)}, date=_day(0)),
    repos.Commit(files={OLD: WITH_CAFE}, date=_day(20), message="a cp1252 byte"),
    repos.Commit(files={OLD: UTF16}, date=_day(40), message="a UTF-16 LE resave"),
    repos.Commit(renames={OLD: MARKS}, files={"crapkit.toml": specs.config()}, date=_day(60),
                 message="git mv"),
    repos.Commit(files={MARKS: _rows(*REKEYED)}, date=_day(70), message="re-key b"),
    repos.Commit(files={MARKS: _rows(*TIGHTENED)}, date=_day(80), message="tighten a, repay c"),
    repos.Commit(files={MARKS: _rows(*TIGHTENED, ("c( n )", "20.0"))}, date=_day(90),
                 message="c again"),
))


def _walked_at_every_commit(root: Path, names: list[str]) -> int:
    """Check 1 at each commit, checked out in turn, of the file `names` gives
    for that commit. Answers the commits checked."""
    shas = repos.git(root, "rev-list", "--reverse", "HEAD").split()
    for sha, name in zip(shas, names, strict=True):
        repos.git(root, "checkout", "-q", sha)
        assert said(root)[1] == marks_history_walk.report(root, name), sha
    return len(shas)


@pytest.mark.nightly
@pytest.mark.parametrize("spec, names", [
    pytest.param(specs.BURN, [MARKS] * 6, id="BURN"),
    pytest.param(specs.BURN_TIGHTEN, [MARKS] * 3, id="BURN_TIGHTEN"),
    pytest.param(SAVED_EVERY_WAY, [OLD] * 3 + [MARKS] * 4, id="saved-every-way"),
])
def test_report_matches_the_history_walk_at_every_commit(make_repo, oracle, spec, names):
    oracle("pygit2")
    built = make_repo(spec)

    assert _walked_at_every_commit(built.root, names) == len(names)
