"""One NAME rule for `brief` and `explain`: exact first, the fragment second.

The two commands read the same string out of the same payload and disagreed
about it. `brief` matched only the whole long name or the whole bare name, so
`route` picked `route`. `explain` ran a SQL `LIKE '%route%'`, so the same string
returned `route`, `route_chain` and `route_num` and printed three trajectories
for a question about one function.

Exact first fixes the collision without taking the fragment away: a name that
IS a function's name resolves to that function, and a name that is nobody's
still finds everything holding it. A repo whose names nest — `route` inside
`route_chain` — is the common case, not the exotic one.
"""
import pytest

from crapkit import packet
from crapkit.cli import _pick_function
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

PATH = "src/lib.rs"

# Three Rust functions whose names nest. lizard prints Rust parameters with no
# parentheses, so the long name carries the signature after a space.
NAMES = ["route cmd : & Cmd", "route_chain cmds : & [ Cmd ]", "route_num n : u8"]


def row(long_name: str, start: int) -> ScoredRow:
    return ScoredRow("rs", PATH, long_name, start, start + 6, 4, 4, 4,
                     8, 1, 1, 0.0, "unmeasured", 20.0, "test", 0)


ROWS = [row(NAMES[0], 10), row(NAMES[1], 30), row(NAMES[2], 50)]


@pytest.fixture()
def store(tmp_path) -> SnapshotStore:
    st = SnapshotStore(tmp_path / "crap.sqlite")
    st.write_run(commit="c1", tool_versions={}, rows=ROWS)
    return st


# --- the shared rule ---------------------------------------------------------

def test_an_exact_bare_name_beats_the_two_names_that_contain_it():
    assert packet.matching_names(NAMES, "route") == [NAMES[0]]


def test_an_exact_long_name_resolves_to_itself():
    assert packet.matching_names(NAMES, NAMES[1]) == [NAMES[1]]


def test_a_fragment_nobody_owns_still_finds_everything_holding_it():
    """The fallback earns its keep: `rout` is no function's name, and answering
    nothing would make a typo indistinguishable from a missing function."""
    assert packet.matching_names(NAMES, "rout") == NAMES


def test_an_empty_name_matches_nothing_rather_than_everything():
    """`"" in name` is true of every string, so without the guard an empty NAME
    reports the file as ambiguous instead of reporting an unusable name."""
    assert packet.matching_names(NAMES, "") == []


# --- explain, through the store ----------------------------------------------

def test_explain_resolves_an_exact_bare_name_to_one_function(store: SnapshotStore):
    """What a fresh session saw: three trajectories for a question about one."""
    assert store.find_functions(PATH, "route") == [NAMES[0]]


def test_explain_keeps_the_fragment_search_when_nothing_matches_exactly(
        store: SnapshotStore):
    assert store.find_functions(PATH, "route_") == sorted(NAMES[1:])


def test_explain_takes_the_long_name_a_payload_printed(store: SnapshotStore):
    assert store.find_functions(PATH, NAMES[2]) == [NAMES[2]]


def test_a_sql_wildcard_in_a_name_is_a_literal_now(store: SnapshotStore):
    """`LIKE` read `_` as "any character", so `route_num` was a pattern that also
    matched `routeXnum`. Python containment reads it as the character it is."""
    assert store.find_functions(PATH, "route%") == []


# --- brief, through the rows -------------------------------------------------

def test_brief_resolves_the_same_bare_name_to_the_same_function():
    assert _pick_function(PATH, ROWS, "route").long_name == NAMES[0]


def test_brief_now_takes_a_fragment_too_and_reports_the_ambiguity():
    """The rule is shared, so `brief` gained the fallback `explain` always had —
    and an ambiguous fragment is an error listing the candidates, not a guess."""
    with pytest.raises(CrapkitError) as excinfo:
        _pick_function(PATH, ROWS, "rout")

    assert "ambiguous" in str(excinfo.value)
    assert all(name in str(excinfo.value) for name in NAMES)


def test_a_unique_fragment_resolves_in_brief(store: SnapshotStore):
    """The pin on the two commands agreeing: one string, one function, both ways."""
    assert _pick_function(PATH, ROWS, "_chain").long_name == NAMES[1]
    assert store.find_functions(PATH, "_chain") == [NAMES[1]]


def test_a_name_no_function_holds_is_still_an_error(store: SnapshotStore):
    with pytest.raises(CrapkitError):
        _pick_function(PATH, ROWS, "nope")
    assert store.find_functions(PATH, "nope") == []


# --- the start line, which both commands take --------------------------------

def test_a_start_line_resolves_to_the_same_function_in_both_commands(
        store: SnapshotStore):
    """`brief src/lib.rs 30` named `route_chain` and `explain src/lib.rs 30`
    answered "no function matching '30'". The start line is the one handle every
    function has, anonymous ones included, so both commands read it."""
    assert _pick_function(PATH, ROWS, "30").long_name == NAMES[1]
    assert store.find_functions(PATH, "30") == [NAMES[1]]


def test_a_line_no_function_opens_on_resolves_to_nothing(store: SnapshotStore):
    """Inside a function is not the same as opening it: the caller reports the
    miss, so the store answers with an empty list rather than a guess."""
    assert store.find_functions(PATH, "31") == []
