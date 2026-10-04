"""The Action's comment renders one item of every kind verify.FINDING_KINDS lists.

tools/action/comment.py reads verify's `findings` and `counts`: each bullet list
is the items of one kind, and the verdict's phrase is the `rule` label of the
first item that fails with verify's exit. A kind added to the table with no
bullet in the comment would reach a pull request as a count of nothing, or as
nothing at all. This test builds one entry for each row from the Verdict field
the row reads, prints verify's payload for it without the 0.8.1 per-kind keys,
renders the comment, and asks for a bullet that names the item: so a new row
fails here until the comment gives it one.
"""
from __future__ import annotations

import importlib.util
import json
import typing
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit import universe, verify
from crapkit.cli import verifying
from crapkit.verify import FINDING_KINDS, GateViolation, UnreadableName, Verdict

BUILDER = Path(__file__).resolve().parents[2] / "tools" / "action" / "comment.py"
BASELINE = {"id": 1, "commit": "a" * 40}

# A value for each field an entry of any kind carries, by its name. An entry
# type a new row brings with a field missing here fails at construction.
SAMPLE = {"path": "src/m.py", "long_name": "late( v )", "start": 12, "ccn": 9, "cov": 0.25,
          "crap": 52.3, "remedy": "decompose", "key_name": "late( v )",
          "reason": "src/m.py:12: arrow refused", "recorded": 8.0, "fresh_crap": 9.5,
          "scope": "src", "line": 14, "dirty": False}
TEST_ID = "tests/test_m.py::test_late"
# What names an item in the comment: its file, its test id, its line.
NAMING = ("path", "test", "line")


@lru_cache(maxsize=None)
def builder():
    """Loaded by path, the way the Action runs it."""
    spec = importlib.util.spec_from_file_location("crapkit_action_comment_kinds", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry_of(row):
    """One entry of the type the row's Verdict field holds, built from SAMPLE."""
    (held, *_) = typing.get_args(typing.get_type_hints(Verdict)[row.field])
    if held is str:
        return TEST_ID
    return held(**{name: SAMPLE[name] for name in held._fields})


def holding(field: str, entries: list) -> Verdict:
    return verify.settle_verdict(Verdict.passing()._replace(**{field: entries}))


def payload(verdict: Verdict, run_id: int | None = 3) -> dict:
    """verify --json for the verdict, with every 0.8.1 per-kind key dropped,
    the payload once those keys are gone. A breached ceiling of 0 makes each
    uncovered line fail."""
    lines = [(line.path, line.line) for line in verdict.uncovered_violations]
    printed = json.loads(json.dumps(verifying._verify_result(
        verdict, run_id, BASELINE, "b" * 40, {"src/m.py": [(1, 20)]}, lines, 0 if lines else None, 0)))
    for key in [row.json_key for row in FINDING_KINDS] + ["diff_uncovered_count", "diff_uncovered_max"]:
        printed.pop(key, None)
    return printed


def rendered(verdict: Verdict, run_id: int | None = 3) -> list[str]:
    return builder().verdict_line(payload(verdict, run_id), verify.exit_code(verdict)).splitlines()


@pytest.mark.parametrize("row", FINDING_KINDS, ids=[row.kind for row in FINDING_KINDS])
def test_every_kind_s_item_gets_a_bullet_that_names_it(row):
    verdict = holding(row.field, [entry_of(row)])
    (item,) = payload(verdict)["findings"]
    names = [str(item[key]) for key in NAMING if key in item]

    bullets = [line for line in rendered(verdict) if line.startswith("- ")]

    assert names, f"{row.kind}'s item carries none of {NAMING}"
    assert [b for b in bullets if all(name in b for name in names)], (row.kind, names, bullets)


@pytest.mark.parametrize("row", FINDING_KINDS, ids=[row.kind for row in FINDING_KINDS])
def test_a_failing_kind_s_phrase_is_its_rule_label(row):
    verdict = holding(row.field, [entry_of(row)])
    code = verify.exit_code(verdict)

    head = rendered(verdict)[0]

    expected = "**verify passed.**" if code == 0 else f"**verify failed, exit {code}: {row.rule}"
    assert head.startswith(expected), head


# --- the overridden row ------------------------------------------------------------

GRANTED = GateViolation("app/calc.py", "route( a , b , c , d )", 34, 8, 0.1, 54.656, "decompose")
OVERRIDDEN = "- overridden: `app/calc.py:34` `route( a , b , c , d )` ccn 8, cov 10%, crap 54.7 -> decompose"


def test_an_overridden_function_reads_as_the_gate_bullet_would():
    gate = rendered(holding("gate_violations", [GRANTED]))
    granted = rendered(holding("overridden", [GRANTED]))

    assert [line for line in granted if line.startswith("- overridden: ")] == [OVERRIDDEN]
    assert OVERRIDDEN.replace("- overridden: ", "- gate: ") in gate


def test_an_overridden_function_follows_the_pass_line():
    assert rendered(holding("overridden", [GRANTED])) == [
        "**verify passed.** Run 3 against baseline 1, 1 changed file (`src/m.py`).", "", OVERRIDDEN]


def test_an_overridden_function_is_listed_on_a_failing_verdict_too():
    verdict = verify.settle_verdict(Verdict.passing()._replace(overridden=(GRANTED,), new_failures=[TEST_ID]))

    lines = rendered(verdict)

    assert lines[0] == "**verify failed, exit 8: new test failures.**"
    assert [line for line in lines if line.startswith("- ")] == [f"- new test failure: `{TEST_ID}`", OVERRIDDEN]
    assert lines[-1].endswith(": 0 gate violations, 0 ratchet regressions, 1 new test failure, "
                              "0 uncovered changed lines."), lines[-1]


# --- the unreadable_name row -------------------------------------------------------

CLAIMED = UnreadableName("src/caf\udce9.py", "src")


def test_a_claimed_name_is_a_verdict_with_its_own_bullet_and_count():
    """verify stopped before any lane ran, so nothing was measured, and the
    bullet names the file as \\xNN, its scope and the rename."""
    sentence = universe.claimed_text([(CLAIMED.path, CLAIMED.scope)])

    lines = rendered(holding("claimed_names", [CLAIMED]), run_id=None)

    assert "src/caf\\xe9.py is in scope 'src'" in sentence and "(git mv)" in sentence
    assert lines == [
        "**verify failed, exit 3: unreadable name.**", "",
        f"- unreadable name: `src/caf\\xe9.py`: {sentence}", "",
        "Nothing was measured against baseline 1: 1 unreadable name, 0 gate violations, "
        "0 ratchet regressions, 0 new test failures, 0 uncovered changed lines."]


# --- the label comes from the item, not from a table in the comment ----------------

def test_a_kind_the_comment_never_heard_of_still_reads_its_rule():
    """A table of exit codes in comment.py would read `exit 10` here."""
    later = {"ok": False, "run_id": 3, "baseline_run": 1, "changed_files": 1,
             "findings": [{"kind": "crossing", "fails": True, "exit_code": 10, "overridable": False,
                           "dirty": False, "rule": "crossing gate", "path": "src/m.py"}]}

    assert builder().verdict_line(later, 10).startswith("**verify failed, exit 10: crossing gate.**")
