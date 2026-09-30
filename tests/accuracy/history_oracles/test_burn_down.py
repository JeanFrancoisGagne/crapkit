"""The ratchet burn-down and mark age, against the file's history read version by
version and a hand table.

oracles/marks_history_walk.py reads every committed version of the ratchet
file whole and diffs the mark sets; hand_burn_down.tsv works the same report
out by hand from repos/history_specs.py (BURN, BURN_TIGHTEN). crapkit is read
through `ratchet report --json` and its text. This file imports no crapkit.
"""
from __future__ import annotations

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


def test_report_matches_the_history_walk(burned):
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
    assert report == marks_history_walk.report(built.root, MARKS)


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


def test_the_debt_policy_flags_what_the_docs_say(burned):
    """debt_max_age_months 2 is 60 days at 30 a month; 2 repaid in 30 days is under 3."""
    config = burned.root / "crapkit.toml"
    policy = "[crapkit]\ndebt_max_age_months = 2\nrepayment_min_per_30d = 3\n"
    config.write_bytes(config.read_bytes().replace(b"[crapkit]\n", policy.encode("utf-8")))
    result = drive.Driver(burned.root, date_now=NOW).run("ratchet", "report", "--json",
                                                          "--enforce")

    found = marks_history_walk.report(burned.root, MARKS)
    expected = marks_history_walk.violations(found, 2, 3)
    assert expected == ["age src/a.py fa( x )", "stalled"]
    assert result.code == 1
    assert [_kind(finding) for finding in result.json()["policy_violations"]] == expected


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

    walked = marks_history_walk.report(driver.root, MARKS)
    ages = {(row["path"], row["long_name"]): row["age_days"] for row in walked["oldest"]}
    assert ages[("src/a.py", "fa( x )")] == 40
    assert brief["gate_rule"]["mark_age_days"] == 40
    assert {key: mcp[key] for key in report} == report
