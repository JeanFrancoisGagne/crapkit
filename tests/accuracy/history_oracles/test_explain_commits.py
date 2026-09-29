"""explain --history: the commits that touched a function, each with its subject,
body and date, against the commit objects read directly.

oracles/pygit2_log.py reads each commit's message from the raw object (`git
cat-file commit` on push, pygit2 nightly) and cuts it into subject and body
the way git's %s and %b do. Which commits touched f is worked by hand from
repos/history_specs.py: every commit that changed f's lines, newest first,
the one that created them included, and not the commit that changed only g.
crapkit is read through `explain --history --json`. This file imports no crapkit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from accuracy.kit import drive, repos, rulings
from accuracy.history_oracles.oracles import pygit2_log
from accuracy.history_oracles.repos import history_specs as specs

NOW = specs.EPOCH + 30 * specs.DAY
pytestmark = pytest.mark.process


def explained(root: Path) -> drive.Result:
    driver = drive.Driver(root, date_now=NOW)
    assert driver.run("coverage").code == 0
    return driver.run("explain", "src/e.py", "f", "--history", "--json")


def said(root: Path) -> list[tuple]:
    result = explained(root)
    assert result.code == 0, result.stderr
    commits = result.json()["functions"][0]["commits"]
    return [(c["sha"], c["date"], c["subject"], c["body"]) for c in commits]


def _f_commits(built: repos.Built) -> list[str]:
    """Every commit but the one that changed only g, newest first (by hand)."""
    shas = repos.git(built.top, "log", "--format=%H %s").splitlines()
    return [line.split()[0] for line in shas if not line.endswith("Touch g only")]


def _read(messages: list[pygit2_log.Message]) -> list[tuple]:
    return [(m.sha, m.date, m.subject, m.body) for m in messages]


def _match(printed: list[tuple], expected: list[tuple]) -> None:
    assert [row[1:] for row in printed] == [row[1:] for row in expected]
    assert all(full.startswith(short) for (short, *_), (full, *_) in zip(printed, expected))


def test_commit_list_matches_the_object_walk(make_repo):
    built = make_repo(specs.EXPLAIN_PLAIN)

    printed = said(built.root)

    _match(printed, _read(pygit2_log.via_cat_file(built.top, _f_commits(built))))


@pytest.mark.nightly
def test_commit_list_matches_pygit2(make_repo, oracle):
    oracle("pygit2")
    built = make_repo(specs.EXPLAIN_PLAIN)
    shas = _f_commits(built)

    printed = said(built.root)

    expected = _read(pygit2_log.via_pygit2(built.top, shas))
    assert expected == _read(pygit2_log.via_cat_file(built.top, shas))
    _match(printed, expected)


def _records(rows: list[tuple]) -> str:
    return repr([(subject, body) for _, _, subject, body in rows])


def _odd(make_repo, name: str) -> tuple[repos.Built, str]:
    built = make_repo(specs.explain_odd(specs.EXPLAIN_ODD_BODIES[name]))
    oracle = _read(pygit2_log.via_cat_file(built.top, _f_commits(built)))
    return built, _records(oracle)


@rulings.applies("H10")
def test_a_body_line_holding_the_record_marks_keeps_its_commit(make_repo):
    """A body line that is \\x02, or starts with \\x01 and three words."""
    printed = []
    for name in ("stx_line", "soh_line"):
        built, expected = _odd(make_repo, name)
        printed.append((_records(said(built.root)), expected))
    rulings.pin_ruling("H10", crapkit=" | ".join(p for p, _ in printed),
                       oracle=" | ".join(e for _, e in printed))


@rulings.applies("H11")
def test_a_body_line_starting_with_soh_does_not_stop_explain(make_repo):
    built, expected = _odd(make_repo, "soh_word")

    result = explained(built.root)

    crashed = result.stderr.strip().splitlines()[-1:] if result.code else []
    rulings.pin_ruling("H11", crapkit=f"exit {result.code} {' '.join(crashed)}".strip(),
                       oracle="exit 0")


@rulings.applies("H12")
def test_line_separators_in_a_body_come_back_as_committed(make_repo):
    built, expected = _odd(make_repo, "separators")

    rulings.pin_ruling("H12", crapkit=_records(said(built.root)), oracle=expected)
