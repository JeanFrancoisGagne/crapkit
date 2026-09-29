"""keys: the run a legacy refusal names, which twin a bare name picks, and the name forms.

A run stored before crapkit recorded a function's position within its line
(occurrence 0) cannot tell two same-named functions on one line apart; every
resolver refuses those rows and names the stored run they came from. A bare name
picks the worst twin, scores compared at the 4 places a mark holds, and the
first in the file among equals.
"""
from types import SimpleNamespace

import pytest

from crapkit import keys
from crapkit.errors import ToolError

PATH = "src/a.ts"


def fn(start: int, occurrence: int = 1, crap: float | None = None, name: str = "f( )",
       scope: str = "src"):
    return SimpleNamespace(path=PATH, long_name=name, start=start, occurrence=occurrence,
                           crap=crap, scope=scope)


LEGACY = [fn(3, 0), fn(3, 0, scope="web"), fn(3, 0, name="(anonymous)"),
          fn(3, 0, name="(anonymous)", scope="web"), fn(3, 0), fn(3, 0, name="(anonymous)")]
BOTH = f"{PATH}: (anonymous) in run 7; {PATH}: f( ) in run 7"


@pytest.mark.parametrize("ask, named", [
    (lambda rows: keys.key_names(rows, run_id=7), BOTH),
    (lambda rows: keys.anonymous_positions(rows, run_id=7), BOTH),
    (lambda rows: keys.handles(rows, run_id=7), BOTH),
    (lambda rows: keys.select(rows, "3", run_id=7), BOTH),
    (lambda rows: keys.select(rows, "(anonymous)#1", run_id=7), BOTH),
    # a bare name keys only its own twins
    (lambda rows: keys.select(rows, "f( )", run_id=7), f"{PATH}: f( ) in run 7"),
])
def test_every_resolver_names_the_run_its_legacy_twins_came_from(ask, named):
    with pytest.raises(ToolError) as refused:
        ask(LEGACY)

    assert str(refused.value) == (f"ambiguous legacy function identity in {named}; refresh "
                                  "analysis before selecting or comparing these functions")


def test_one_legacy_row_beside_a_positioned_one_is_ambiguous():
    """The occurrence-0 row could be either of the two the positioned run tells apart."""
    rows = [fn(3, 0), fn(3, 1)]

    assert keys.ambiguous_groups(rows, legacy_only=True) == {(PATH, "f( )")}


@pytest.mark.parametrize("twins, picked", [
    # 30.45 is worse than 30.4 at 4 places, though both round to 30
    ([fn(1, crap=30.4), fn(9, crap=30.45)], "f( )#2"),
    # 30.0 and 30.00001 are one score at 4 places: the first in the file wins
    ([fn(1, crap=30.0), fn(9, crap=30.00001)], "f( )"),
    # one line, one score: the first created on the line wins
    ([fn(4, 2, crap=12.0), fn(4, 1, crap=12.0)], "f( )"),
])
def test_a_bare_name_picks_the_worst_twin_and_the_first_among_equals(twins, picked):
    assert keys.select(twins, "f( )") == [("f( )", picked)]


def test_the_ordinal_is_the_digits_after_the_last_hash():
    """A C++ reader can name a function `op#( a )`, so only the last `#` can
    start an ordinal."""
    assert keys.split_ordinal("op#( a )#2") == ("op#( a )", 2)
    assert keys.split_ordinal("op#( a )") == ("op#( a )", 1)


def test_an_anonymous_group_is_read_by_the_path_s_last_suffix():
    assert keys.expression_group("src/a.test.ts", "(anonymous)") is True
    assert keys.expression_group("src/a.ts.py", "(anonymous)") is False


def test_a_row_that_records_no_place_within_its_line_is_a_legacy_row():
    """Occurrence 0 is the legacy mark (_unknown_count): a row object with no
    occurrence field came from before crapkit recorded one."""
    assert keys.position(SimpleNamespace(start=3)) == (3, 0)
    assert keys.ambiguous_groups([SimpleNamespace(path=PATH, long_name="f( )", start=3),
                                  fn(3, 1)], legacy_only=True) == {(PATH, "f( )")}
