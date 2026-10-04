"""Two mark rules, on purpose, and the line between them.

The commit gate judges staged blobs. A blob has no coverage, so a staged
violation has no exact CRAP, only a range from ccn to its untested CRAP.
`hook-precommit` judges it through the gate module, and in this slot passes
every breach a mark covers (pardoned, marked rise or unproven) with one count
line: the repo signed for this function, and a commit is not the moment to
reopen that.

`rescore --gate` and `verify` both hold a scored row, so they keep the numeric
rule: at or under the recorded mark it is carried debt, past it the mark rose
and the verdict fails. Those two are `_unmarked_breaches`, and this file pins
that the new hook rule did not leak into them.
"""
import json

import pytest

from cli_inproc_repo import (add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)

from crapkit.cli import main
from crapkit.cli.scoring import _ceiling_breaches, _unmarked_breaches
from crapkit.hook import Violation
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow

from test_gate_hook_mapping import hook_split

MARK = RatchetEntry("src/mod.py", "legacy( n )", 63.6)


def staged(path: str, name: str, ccn: int = 8, start: int = 1) -> Violation:
    return Violation(path, name, start, ccn)


def scored(path: str, name: str, ccn: int, crap: float) -> ScoredRow:
    return ScoredRow("src", path, name, 10, 15, ccn, ccn, ccn, 12, 2, 1,
                     0.4, "measured", crap, "decompose")


# --- the hook: a mark's existence ----------------------------------------------

def test_a_marked_function_leaves_the_gated_list():
    gated, carried = hook_split([staged("src/mod.py", "legacy( n )")], [MARK])

    assert (gated, carried) == ([], 1)


def test_an_unmarked_function_stays_gated():
    gated, carried = hook_split([staged("src/mod.py", "fresh( n )")], [MARK])

    assert [v.long_name for v in gated] == ["fresh( n )"]
    assert carried == 0


def test_the_same_name_in_another_file_is_another_function():
    gated, carried = hook_split([staged("src/other.py", "legacy( n )")], [MARK])

    assert [v.path for v in gated] == ["src/other.py"]
    assert carried == 0


def test_no_marks_file_gates_everything():
    gated, carried = hook_split([staged("src/mod.py", "legacy( n )")], [])

    assert ([(v.path, v.long_name, v.ccn) for v in gated], carried) == ([("src/mod.py", "legacy( n )", 8)], 0)


def test_the_gated_order_survives_the_split():
    """The gate prints worst ccn first. Filtering must not reshuffle it."""
    order = [staged("src/mod.py", "worst( n )", ccn=20, start=1),
             staged("src/mod.py", "legacy( n )", ccn=12, start=20),
             staged("src/mod.py", "mild( n )", ccn=7, start=40)]

    gated, carried = hook_split(order, [MARK])

    assert [v.long_name for v in gated] == ["worst( n )", "mild( n )"]
    assert carried == 1


@pytest.mark.parametrize("ccn", [7, 12, 40])
def test_how_far_over_the_ceiling_it_sits_changes_nothing(ccn: int):
    """A mark's existence is the whole rule in this slot: ccn 7 is pardoned by
    the mark, ccn 12 and 40 straddle it (unproven), and all three pass."""
    gated, carried = hook_split([staged("src/mod.py", "legacy( n )", ccn=ccn)], [MARK])

    assert (gated, carried) == ([], 1)


# --- rescore and verify: still the numeric rule -------------------------------

def test_a_scored_breach_past_its_mark_is_still_kept():
    breaches = _ceiling_breaches([scored("src/mod.py", "legacy( n )", 15, 70.0)],
                                 {"src/mod.py": 6})

    assert [b.crap for b in _unmarked_breaches(breaches, [MARK])] == [70.0]


def test_a_scored_breach_at_its_mark_is_still_carried_debt():
    breaches = _ceiling_breaches([scored("src/mod.py", "legacy( n )", 15, 63.6)],
                                 {"src/mod.py": 6})

    assert _unmarked_breaches(breaches, [MARK]) == []


# --- the marks file deleted in the commit under gate ----------------------------
#
# Both rules pardon through the marks file, so a commit that deletes it takes
# every pardon with it. Each gate then judges every changed function, the strict
# direction: a gone file never reads as "this function was signed for".

# check_gate is the MCP tool; it runs `rescore FILE --gate --json`.
_GATE_ARGV = {"rescore --gate": ["rescore", "src/app.ts", "--gate"],
              "hook-precommit": ["hook-precommit"],
              "check_gate": ["rescore", "src/app.ts", "--gate", "--json"]}

# Where each gate names what it refused: rescore's table lists every function,
# so its refusal is the stderr block; the hook prints its refusal on stdout.
_REFUSAL = {
    "rescore --gate": lambda out: out.err,
    "hook-precommit": lambda out: out.out,
    "check_gate": lambda out: str([b["function"] for b in json.loads(out.out)["gate"]["breaches"]]),
}


@pytest.fixture()
def marked(repo, capsys):
    """knotty ( n ) over its ceiling and marked at its crap, then touched inside
    without a change to its complexity."""
    seed_artifacts(repo)
    add_knotty(repo)
    commit_all(repo, "knotty")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    commit_all(repo, "marks")
    app = repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8").replace("{ return 1; }", "{ return 1; } // x"),
                   encoding="utf-8", newline="\n")
    capsys.readouterr()
    return repo


@pytest.mark.parametrize("consumer", list(_GATE_ARGV))
@pytest.mark.parametrize(("marks", "code"), [pytest.param("kept", 0, id="control"),
                                             pytest.param("deleted", 6, id="deleted")])
def test_a_deleted_marks_file_pardons_nothing(marked, capsys, consumer, marks, code):
    if marks == "deleted":
        (marked / "crapkit-ratchet.tsv").unlink()
    git(marked, "add", "-A")

    got = main([*_GATE_ARGV[consumer], "--repo", str(marked)])
    out = capsys.readouterr()

    assert got == code, (out.out, out.err)
    assert code == 0 or "knotty ( n )" in _REFUSAL[consumer](out), (out.out, out.err)
