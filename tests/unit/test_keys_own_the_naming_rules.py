"""The function-naming rules live in keys.py, beside the twin keys they feed,
and one selector resolver answers a NAME for every command that takes one.

Three resolvers answered the same NAME before. `brief` resolved in cli/queue.py
and answered a bare twin name with the worst twin. `explain` resolved in
store.py and answered it with the first. `claims release` matched through a
copy of the exact-name predicate. store.py and analyze.py imported the packet
formatter to reach the rules, and keys.py spelled `(anonymous)` by hand.
"""
import subprocess
import sys
from collections import namedtuple

import pytest

from crapkit import keys
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow

PATH = "src/iso.py"
POST_INIT = "__post_init__( self )"

# The shape the store reads positions in: where a function is, what it is
# called and what it scored, null on an inventory run.
Span = namedtuple("Span", "scope path long_name start occurrence crap")


def scored(name: str, start: int, crap: float, *, occurrence: int = 1) -> ScoredRow:
    return ScoredRow("src", PATH, name, start, start + 3, 5, 5, 5, 4, 1, 0,
                     0.5, "measured", crap, "decompose", 0, occurrence)


TWINS = [scored(POST_INIT, 7, 30.0), scored(POST_INIT, 18, 66.0)]


# --- where the rules live ------------------------------------------------------

def test_the_store_and_the_analyzer_load_without_the_packet_formatter():
    """Both read the naming rules and nothing else of packet.py, which formats
    the start-editing packet for brief alone."""
    probe = ("import sys, crapkit.store, crapkit.analyze; "
             "print(sorted(m for m in sys.modules if m == 'crapkit.packet'))")

    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, encoding="utf-8", check=True)

    assert out.stdout.strip() == "[]"


def test_the_anonymous_spelling_is_one_constant_the_key_rules_share():
    anonymous = keys.ANONYMOUS + " ( z )"

    assert keys.bare_name(anonymous) == ""
    assert keys.expression_group("web/app.ts", anonymous)
    assert keys.handle_ordinal(keys.ANONYMOUS + "#2") == 2


def test_the_exact_name_predicate_takes_the_long_name_or_its_bare_identifier():
    """What `claims release` matches a saved claim record against."""
    assert keys.named_by("classify( score , late )", "classify")
    assert keys.named_by("classify( score , late )", "classify( score , late )")
    assert not keys.named_by("classify( score , late )", "class")
    assert keys.exact_names(["route cmd : & Cmd", "route_num n : u8"], "route") == [
        "route cmd : & Cmd"]


# --- one resolver --------------------------------------------------------------

def test_a_bare_twin_name_selects_the_worst_twin_wherever_it_sits():
    """The worse `__post_init__` is second in the file, so its key is `#2`."""
    assert keys.select(TWINS, "__post_init__") == [(POST_INIT, f"{POST_INIT}#2")]
    assert keys.select(TWINS, POST_INIT) == [(POST_INIT, f"{POST_INIT}#2")]


def test_the_twin_selector_picks_by_file_order_not_by_score():
    assert keys.select(TWINS, "__post_init__#1") == [(POST_INIT, POST_INIT)]
    assert keys.select(TWINS, "__post_init__#2") == [(POST_INIT, f"{POST_INIT}#2")]


def test_equal_twins_resolve_to_the_first_in_the_file():
    tied = [scored(POST_INIT, 18, 30.0), scored(POST_INIT, 7, 30.0)]

    assert keys.select(tied, "__post_init__") == [(POST_INIT, POST_INIT)]


def test_twins_with_no_score_resolve_to_the_first():
    """An inventory run stores no CRAP, so no twin is worse than another."""
    unscored = [Span("src", PATH, POST_INIT, start, 1, None) for start in (18, 7)]

    assert keys.select(unscored, "__post_init__") == [(POST_INIT, POST_INIT)]


def test_a_start_line_selects_the_function_opening_on_it():
    assert keys.select(TWINS, "18") == [(POST_INIT, f"{POST_INIT}#2")]


def test_a_line_two_functions_open_on_is_refused_with_their_handles():
    rows = [scored("(anonymous)", 1, 12.0, occurrence=1),
            scored("(anonymous) ( x )", 1, 20.0, occurrence=2)]

    with pytest.raises(CrapkitError) as err:
        keys.select(rows, "1")

    assert str(err.value) == ("line 1 in src/iso.py is ambiguous; use a handle: "
                              "(anonymous)#1, (anonymous)#2")


def test_an_anonymous_handle_counts_every_anonymous_function_in_file_order():
    rows = [scored("(anonymous)", 3, 9.0), scored("(anonymous) ( x )", 9, 9.0),
            scored("(anonymous)", 20, 9.0)]

    assert keys.select(rows, "(anonymous)#3") == [("(anonymous)", "(anonymous)#2")]
    assert keys.select(rows, "(anonymous)#2") == [("(anonymous) ( x )", "(anonymous) ( x )")]


def test_every_long_name_a_fragment_matches_comes_back_once():
    rows = [scored("route_chain( a )", 4, 9.0), scored("route_num( b )", 9, 9.0),
            scored("route_num( b )", 30, 12.0)]

    assert keys.select(rows, "rout") == [("route_chain( a )", "route_chain( a )"),
                                         ("route_num( b )", "route_num( b )#2")]


def test_a_name_only_an_older_run_holds_keeps_the_key_its_ordinal_spells():
    """explain matches every name any stored run scored. A function the resolved
    run no longer holds has no twins there to rank."""
    names = ["gone( b )", POST_INIT]

    assert keys.select(TWINS, "gone", names) == [("gone( b )", "gone( b )")]
    assert keys.select(TWINS, "gone#3", names) == [("gone( b )", "gone( b )#3")]


def test_nothing_selected_is_an_empty_list_for_the_caller_to_word():
    assert keys.select(TWINS, "31") == []
    assert keys.select(TWINS, "(anonymous)#1") == []
    assert keys.select(TWINS, "nope") == []
    assert keys.select([], "__post_init__") == []
