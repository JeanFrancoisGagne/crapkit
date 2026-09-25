"""Changed line ranges, read through the two gates that act on them.

`hook-precommit` judges the functions a staged diff changed and `rescore
--gate` the ones the working tree changed since HEAD; each names every judged
function here, because all five functions in the file are over the ceiling.
The expected set comes from unidiff reading the same `git diff -U0` text
(oracles/unidiff_ranges.py) over the function spans the file's text puts
down, and from hand_gated.tsv. This file imports no crapkit.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from accuracy.kit import drive, repos, rulings
from accuracy.history_oracles import history_hand
from accuracy.history_oracles.oracles import unidiff_ranges
from accuracy.history_oracles.oracles.history_git import text as git_text
from accuracy.history_oracles.repos import history_specs as specs

pytestmark = pytest.mark.process
_GATE_LINE = re.compile(r"src/m\.py:(\d+)\s+(f\d)\(")


def spans(text: str) -> dict[str, tuple[int, int]]:
    """{function: (def line, its last `return` line)} in the file's text."""
    lines, found, name = text.split("\n"), {}, None
    for number, line in enumerate(lines, start=1):
        if line.startswith("def "):
            name, start = line[4:line.index("(")], number
        elif line.startswith("    return '"):
            found[name] = (start, number)
    return found


def _overlaps(span: tuple[int, int], hunks: list[tuple[int, int]]) -> bool:
    return any(first <= span[1] and span[0] <= last for first, last in hunks)


def _meeting(root: Path, hunks: list[tuple[int, int]]) -> set[str]:
    """The functions of src/m.py whose span meets one of `hunks`."""
    text = (root / "src" / "m.py").read_bytes().decode("utf-8")
    return {name for name, span in spans(text).items() if _overlaps(span, hunks)}


def _diff(root: Path, *diff_args: str) -> str:
    return git_text(root, "-c", "core.quotePath=false", "diff", "-U0", "--no-renames",
                    *diff_args) + "\n"


def expected(root: Path, *diff_args: str) -> set[str]:
    """The functions whose span meets a hunk unidiff reads in `git diff -U0`."""
    return _meeting(root, unidiff_ranges.ranges(_diff(root, *diff_args)).get("src/m.py", []))


def _edited(make_repo, edit: str):
    built = make_repo(specs.GATED)
    driver = drive.Driver(built.root, date_now=specs.GATED_NOW)
    (built.root / "src" / "m.py").write_bytes(specs.GATED_EDITS[edit].encode("utf-8"))
    return built, driver, history_hand.gated(edit)


def hook_judged(built: repos.Built, driver: drive.Driver) -> set[str]:
    repos.git(built.top, "add", "--", "src/m.py")
    result = driver.run("hook-precommit")
    assert result.code in (0, 6), result.stderr
    return {name for _, name in _GATE_LINE.findall(result.stderr + result.stdout)}


def rescore_judged(driver: drive.Driver) -> set[str]:
    assert driver.run("coverage").code == 0
    result = driver.run("rescore", "src/m.py", "--gate", "--json")
    gate = result.json()["gate"]
    names = {breach["function"].split("(")[0] for breach in gate["breaches"]}
    assert gate["judged"] == len(names)
    return names


@pytest.mark.parametrize("edit", ["mixed", "plus_plus"])
def test_touched_functions_match_unidiff(make_repo, edit):
    built, driver, by_hand = _edited(make_repo, edit)
    unstaged = expected(built.root)

    from_rescore = rescore_judged(driver)
    from_hook = hook_judged(built, driver)

    assert from_rescore == unstaged == by_hand
    assert from_hook == expected(built.root, "--cached") == by_hand


def test_content_lines_starting_with_plus_plus(make_repo):
    """An added line whose text starts with '++ ' reads '+++ ' in a -U0 diff; it
    must not open a phantom file and hide f3's hunk from the gate (R03)."""
    built, driver, by_hand = _edited(make_repo, "plus_plus")

    assert hook_judged(built, driver) == by_hand == {"f1", "f3"}


def _literal_judged(root: Path) -> set[str]:
    """The functions a hunk's added lines meet, with no touch point for a deletion."""
    hunks = [hunk for patched in unidiff_ranges.files(_diff(root, "--cached")) for hunk in patched]
    return _meeting(root, [pair for pair in map(unidiff_ranges.added, hunks) if pair])


@rulings.applies("H9")
def test_a_pure_deletion_marks_the_line_before_it(make_repo):
    built, driver, by_hand = _edited(make_repo, "deletion_only")

    judged = hook_judged(built, driver)

    assert judged == by_hand
    rulings.pin_ruling("H9", crapkit=",".join(sorted(judged)) or "none",
                       oracle=",".join(sorted(_literal_judged(built.root))) or "none")
