"""Same name, one file: every twin gets its own ratchet key.

The reporter's shape, on a repo of 2,768 scored functions: a module holding
several dataclasses, each with its own `__post_init__`. A mark and a gate keyed
on `(path, long_name)`, so one twin owned the key and the others were neither
marked nor gated. Any of them could grow past every ceiling and no gate ever
fired. Three collisions in one repo, twice on `__post_init__` and once on
`size`.

The key now carries an ordinal counted in start order: the first twin keeps the
bare name, the second answers to `name#2`, the third to `name#3`. Bare-is-first
is what makes it free to adopt — every mark recorded before it already reads as
twin #1, so no committed TSV needs rewriting.
"""
import argparse
import json
from pathlib import Path

import pytest

from crapkit.analyze import analyze_source
from crapkit.cli import (_BriefLoader, _ceiling_breaches, _pick_function, _split_marked,
                         _unmarked_breaches)
from crapkit.cli.reports import cmd_explain
from crapkit.config import load_config_text
from crapkit.errors import CrapkitError
from crapkit.hook import Violation
from crapkit.keys import key_name, key_names, key_of, split_ordinal
from crapkit.ratchet import RatchetEntry, merge_ratchets, prune_ratchet, seed_ratchet
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from crapkit.verify import evaluate

POST_INIT = "__post_init__( self )"

# The reporter's file, cut down: two dataclasses, one `__post_init__` each. The
# first is the branchy one, and it is the one the old key lost.
ISO_COST = '''from dataclasses import dataclass


@dataclass
class Cost:
    a: int

    def __post_init__(self):
        if self.a < 0:
            raise ValueError
        if self.a > 9:
            raise ValueError


@dataclass
class Iso:
    b: int

    def __post_init__(self):
        if self.b < 0:
            raise ValueError
'''


def scored(name: str, start: int, crap: float, path: str = "src/iso_cost.py") -> ScoredRow:
    ccn = max(int(crap // 8), 1)
    return ScoredRow("src", path, name, start, start + 4, ccn, ccn, ccn, 9, 1, 1,
                     0.4, "measured", crap, "decompose")


def staged(name: str, start: int, key: str, ccn: int = 9) -> Violation:
    return Violation("src/iso_cost.py", name, start, ccn, key)


# --- the key itself -----------------------------------------------------------

def test_a_name_only_one_function_holds_keys_bare():
    rows = [scored("only( self )", 4, 60.0), scored("other( self )", 20, 12.0)]

    assert set(key_names(rows).values()) == {"only( self )", "other( self )"}


def test_the_reporters_twins_take_one_key_each():
    records = analyze_source("src/iso_cost.py", ISO_COST)

    keys = key_names(records)

    assert [key_of(keys, r)[1] for r in records] == [POST_INIT, f"{POST_INIT}#2"]


def test_the_ordinal_counts_in_start_order_not_arrival_order():
    """Rows reach the key in scope-then-path order, which is not file order. An
    ordinal read off arrival would renumber both twins when a scope is renamed."""
    late, early = scored(POST_INIT, 40, 8.0), scored(POST_INIT, 7, 66.0)

    keys = key_names([late, early])

    assert key_of(keys, early) == ("src/iso_cost.py", POST_INIT)
    assert key_of(keys, late) == ("src/iso_cost.py", f"{POST_INIT}#2")


def test_one_function_scored_under_two_scopes_is_still_one_twin():
    """Two scopes claiming one path score the same span twice. That is one
    function, so it takes one ordinal and not a `#2` nobody can find."""
    row = scored(POST_INIT, 7, 66.0)

    assert list(key_names([row, row._replace(scope="other")]).values()) == [POST_INIT]


def test_two_names_that_open_on_one_line_are_not_each_others_twin():
    """A synthetic row set can give two names the same start. Keying the map on
    the line alone let one overwrite the other and hand a mark to the wrong
    function, which is how a real seed test caught it."""
    hot, fine = scored("hot( )", 1, 72.0), scored("fine( )", 1, 2.0)

    keys = key_names([hot, fine])

    assert (key_of(keys, hot)[1], key_of(keys, fine)[1]) == ("hot( )", "fine( )")


def test_a_name_carrying_a_hash_is_not_a_selector():
    """`#` is a legal character in a long_name. Only a whole-number tail selects."""
    assert split_ordinal("op#( a )") == ("op#( a )", 1)
    assert split_ordinal("f( a )#0") == ("f( a )#0", 1)
    assert split_ordinal(f"{POST_INIT}#3") == (POST_INIT, 3)
    assert key_name(POST_INIT, 1) == POST_INIT


# --- seeding ------------------------------------------------------------------

def test_seed_marks_each_twin_at_its_own_score():
    """One mark for the pair recorded the worse one's debt and silently signed
    for the other at the same number."""
    fresh = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    entries, added, tightened = seed_ratchet([], fresh, target=6)

    assert [(e.long_name, e.crap) for e in entries] == [
        (POST_INIT, 66.0714), (f"{POST_INIT}#2", 30.0)]
    assert (added, tightened) == (2, 0)


def test_a_mark_written_before_the_ordinal_still_covers_the_first_twin():
    """The migration, and the whole reason twin #1 keeps the bare key: an
    existing crapkit-ratchet.tsv needs no rewrite."""
    prior = [RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714)]
    fresh = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    entries, added, tightened = seed_ratchet(prior, fresh, target=6)

    assert (added, tightened) == (1, 0), "only the second twin is new debt"
    assert len(entries) == 2


def test_prune_keeps_both_twins_while_both_are_in_the_file():
    marks = [RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714),
             RatchetEntry("src/iso_cost.py", f"{POST_INIT}#2", 30.0)]
    fresh = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    kept, dropped = prune_ratchet(marks, fresh)

    assert (len(kept), dropped) == (2, 0)


def test_prune_drops_the_twin_that_left_the_file():
    """One of two `__post_init__` deleted renumbers nothing: the survivor is
    twin #1 and `#2` names no function any more."""
    marks = [RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714),
             RatchetEntry("src/iso_cost.py", f"{POST_INIT}#2", 30.0)]

    kept, dropped = prune_ratchet(marks, [scored(POST_INIT, 7, 66.0714)])

    assert ([e.long_name for e in kept], dropped) == ([POST_INIT], 1)


# --- the verdict --------------------------------------------------------------

def test_a_repaid_twin_no_longer_pardons_the_others_regression():
    """The collapse's worst failure. One key held the worse twin's number, so
    repaying that twin left its mark high enough to cover the other twin's
    growth: 66 repaid to 10 while the sibling grew 30 -> 60, and the pair's
    worst was still under the mark. Nothing failed."""
    marks = [RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714),
             RatchetEntry("src/iso_cost.py", f"{POST_INIT}#2", 30.0)]
    fresh = [scored(POST_INIT, 7, 10.0), scored(POST_INIT, 18, 60.0)]

    verdict = evaluate(fresh=fresh, changed_ranges={}, ratchet=marks,
                       baseline_failures=set(), fresh_failures=set(), target=6)

    assert [(r.long_name, r.fresh_crap) for r in verdict.ratchet_regressions] == [
        (f"{POST_INIT}#2", 60.0)]


def test_a_clean_twin_cannot_tighten_its_siblings_mark():
    marks = [RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714)]
    fresh = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 7.0)]

    verdict = evaluate(fresh=fresh, changed_ranges={}, ratchet=marks,
                       baseline_failures=set(), fresh_failures=set(), target=6)

    assert verdict.ratchet_regressions == []


# --- the commit gate ----------------------------------------------------------

def test_the_commit_gate_no_longer_exempts_the_unmarked_twin():
    """The reporter's escape, exactly. A mark on the second `__post_init__`
    read as a mark on the name, so the first one committed at any ccn."""
    violations = [staged(POST_INIT, 7, POST_INIT), staged(POST_INIT, 18, f"{POST_INIT}#2")]

    gated, exempt = _split_marked(
        violations, [RatchetEntry("src/iso_cost.py", f"{POST_INIT}#2", 30.0)])

    assert [v.start for v in gated] == [7]
    assert [v.start for v in exempt] == [18]


def test_a_violation_nobody_keyed_is_judged_under_its_bare_name():
    """A lone function's key IS its long_name, so an unkeyed violation is not a
    special case — it is the ordinary one."""
    gated, exempt = _split_marked(
        [Violation("src/mod.py", "legacy( n )", 4, 9)],
        [RatchetEntry("src/mod.py", "legacy( n )", 63.6)])

    assert (gated, len(exempt)) == ([], 1)


def test_rescore_gate_judges_each_twin_against_its_own_mark():
    """`rescore --gate` compares numbers where the commit gate compares
    existence, and it read the same collapsed key."""
    rows = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 60.0)]
    breaches = _ceiling_breaches(rows, {"src/iso_cost.py": 6}, key_names(rows))

    kept = _unmarked_breaches(breaches, [
        RatchetEntry("src/iso_cost.py", POST_INIT, 66.0714),
        RatchetEntry("src/iso_cost.py", f"{POST_INIT}#2", 30.0)])

    assert [v.start for v in kept] == [18], "the second twin is 30 over its own mark"


def test_the_breach_keys_come_from_the_whole_file_not_the_touched_rows():
    """Only the second twin was touched, so it arrives alone. Counting ordinals
    over the touched rows would call it twin #1 and hand it the bare mark."""
    whole_file = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 60.0)]
    touched = [whole_file[1]]

    breaches = _ceiling_breaches(touched, {"src/iso_cost.py": 6}, key_names(whole_file))

    assert [v.key_name for v in breaches] == [f"{POST_INIT}#2"]


# --- the merge driver ---------------------------------------------------------

def test_the_merge_driver_still_merges_ordinal_keys():
    """An ordinal is part of the key string, so per-key 3-way semantics are
    untouched: the changed side wins, and both changed takes the lower."""
    base = [RatchetEntry("a.py", POST_INIT, 40.0),
            RatchetEntry("a.py", f"{POST_INIT}#2", 30.0)]
    ours = [RatchetEntry("a.py", POST_INIT, 31.5),
            RatchetEntry("a.py", f"{POST_INIT}#2", 30.0)]
    theirs = [RatchetEntry("a.py", POST_INIT, 40.0),
              RatchetEntry("a.py", f"{POST_INIT}#2", 22.0),
              RatchetEntry("b.py", "bar( y )", 12.0)]

    merged = merge_ratchets(base, ours, theirs)

    assert [(e.long_name, e.crap) for e in merged] == [
        (POST_INIT, 31.5), (f"{POST_INIT}#2", 22.0), ("bar( y )", 12.0)]


# --- addressing a twin by name ------------------------------------------------

def test_a_bare_name_still_resolves_to_the_burn_down_item():
    """Unchanged, and the reason `#N` is an addition rather than a replacement:
    a bare name means the worst twin, which is what the queue ranks."""
    rows = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    assert _pick_function("src/iso_cost.py", rows, POST_INIT).start == 7


def test_a_hash_ordinal_selects_the_twin_the_bare_name_does_not():
    rows = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    assert _pick_function("src/iso_cost.py", rows, "__post_init__#2").start == 18
    assert _pick_function("src/iso_cost.py", rows, "__post_init__#1").start == 7


def test_an_ordinal_past_the_last_twin_says_how_many_there_are():
    rows = [scored(POST_INIT, 7, 66.0714), scored(POST_INIT, 18, 30.0)]

    with pytest.raises(CrapkitError) as err:
        _pick_function("src/iso_cost.py", rows, "__post_init__#5")

    assert "holds 2 function(s)" in str(err.value)


def test_a_name_that_merely_ends_in_a_hash_word_is_not_a_selector():
    rows = [scored("size#px( self )", 41, 60.0)]

    assert _pick_function("src/iso_cost.py", rows, "size#px").start == 41


# --- the mark a packet and a trajectory report --------------------------------

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""

MARKS = ("path\tlong_name\tcrap\n"
         f"src/iso_cost.py\t{POST_INIT}\t66.0714\n"
         f"src/iso_cost.py\t{POST_INIT}#2\t30.0000\n")


@pytest.fixture()
def marked_repo(tmp_path):
    """A repo whose one file holds two `__post_init__`, each with its own mark."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "iso_cost.py").write_text(ISO_COST, encoding="utf-8", newline="\n")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (repo / "crapkit-ratchet.tsv").write_text(MARKS, encoding="utf-8", newline="\n")
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit="a" * 40, tool_versions={},
                             rows=[scored(POST_INIT, 7, 66.0714),
                                   scored(POST_INIT, 18, 30.0)])
    return repo, store, run_id


def _explain_mark(repo, capsys, name: str) -> float:
    args = argparse.Namespace(repo=str(repo), path="src/iso_cost.py", name=name,
                              history=False, tests=False, json=True)
    assert cmd_explain(args) == 0
    (payload,) = json.loads(capsys.readouterr().out)["functions"]
    return payload["ratchet_mark"]


def test_explain_reads_the_first_twins_mark_for_a_bare_name(marked_repo, capsys):
    repo, _, _ = marked_repo

    assert _explain_mark(repo, capsys, "__post_init__") == 66.0714


def test_explain_reads_the_second_twins_mark_for_a_hash_ordinal(marked_repo, capsys):
    """Both twins share one long_name, so without the ordinal every explain of
    this file reported one mark for two different functions."""
    repo, _, _ = marked_repo

    assert _explain_mark(repo, capsys, "__post_init__#2") == 30.0


def test_a_brief_packet_reports_the_mark_on_the_twin_it_picked(marked_repo):
    """`brief` picks the worst twin for a bare name. That twin's key is `#2`
    whenever the worst one is not the first, so the packet has to read the key
    off the row rather than off the name it was asked about."""
    repo, store, run_id = marked_repo
    cfg = load_config_text(TOML)
    loader = _BriefLoader(repo, cfg, store, {"id": run_id, "commit": "a" * 40})
    rows = store.read_scored_file(run_id, "src/iso_cost.py")

    marks = {row.start: loader.mark(row) for row in rows}

    assert marks == {7: 66.0714, 18: 30.0}


# --- the pages promise what the tool prints -----------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.parametrize("page", ["AGENTS.md", "docs/agent-json.md"])
def test_the_documented_out_of_range_twin_message_is_the_one_the_tool_prints(page: str):
    from crapkit.cli import _no_twin_message

    printed = _no_twin_message("calc/iso_cost.py", "__post_init__#5", "__post_init__", 2)

    assert printed == ("no __post_init__#5 in calc/iso_cost.py in the latest scored run"
                       " — it holds 2 function(s) named '__post_init__'")
    assert printed in (_ROOT / page).read_text(encoding="utf-8"), page


@pytest.mark.parametrize("page", ["AGENTS.md", "docs/agent-json.md", "docs/ratchet.md"])
def test_every_page_that_names_the_key_says_the_first_twin_keeps_the_bare_name(page: str):
    """The migration claim. A reader who believes marks need rewriting will
    re-seed a repo and lose every audited override in the process."""
    text = " ".join((_ROOT / page).read_text(encoding="utf-8").lower().split())

    assert "#2" in text and "file order" in text, page
