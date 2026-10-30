"""keys.resolve and keys.pair_moves: which mark judges a function, and which
key that left a file pairs with a key that arrived in it.

The calc is mark identity and move pairing. Three checks:

1. The hand table below, one row per case the ticket names.
2. A property: every marked function of a twin-free file resolves Exact, and
   `key_name` and `split_ordinal` round-trip for every ordinal.
3. An independent pairing model, written from the carry rule's words and docs/ratchet.md's
   key format, that imports no crapkit module. It agrees with `pair_moves` on
   every pair and every unpaired key.

Checks 2 and 3 each run twice. An exhaustive driver walks every case over a
small pool of keys and needs nothing beyond the dev extra, so CI's unit legs
run it. A hypothesis driver generates wider cases; it imports hypothesis
inside the test, so the module still loads where hypothesis is absent (CI's
unit job installs only `.[dev]`) and that driver skips there.

The carry rule: a mark carries to a function only when the function's own key
has no mark, its old key left the file, and exactly one mark in that file
shares its bare name. Twins and anonymous functions never carry.
"""
from __future__ import annotations

import itertools

import pytest

from crapkit import keys
from crapkit.keys import Carry, Exact, Refused, Twin
from crapkit.ratchet import RatchetEntry
from marks_history_repo import NEW, OLD, PATH, REKEYED, SECOND, SEEDED, TWIN, keys_of

OTHER = "calc/other.py"


def at(*names: str, path: str = PATH) -> set[tuple[str, str]]:
    return {(path, name) for name in names}


def key(name: str, path: str = PATH) -> tuple[str, str]:
    return path, name


# (case, the function asked about, the file's functions, the marked keys, the answer)
HAND = [
    ("exact key", "classify( a , b )", at("classify( a , b )"), at("classify( a , b )"),
     Exact(key("classify( a , b )"))),
    ("an unmarked function with no candidate answers its own key", "f( x )", at("f( x )"),
     at("g( y )"), Exact(key("f( x )"))),
    ("twin #2", "classify( a )#2", at("classify( a )", "classify( a )#2"),
     at("classify( a )", "classify( a )#2"), Twin(key("classify( a )#2"), 2)),
    ("an unmarked twin with no candidate answers its own key", "classify( a )#2",
     at("classify( a )", "classify( a )#2"), at("classify( a )"),
     Twin(key("classify( a )#2"), 2)),
    ("carry with one bare-name match", NEW, at(NEW), at(OLD), Carry(key(OLD), key(NEW))),
    ("the old key still in the file: no carry", NEW, at(OLD, NEW), at(OLD), Exact(key(NEW))),
    ("(anonymous) never carries", "(anonymous) ( z )", at("(anonymous) ( z )"),
     at("(anonymous) ( y )"), Exact(key("(anonymous) ( z )"))),
    ("an anonymous twin never carries", "(anonymous)#2", at("(anonymous)", "(anonymous)#2"),
     at("(anonymous)", "(anonymous) ( y )"), Twin(key("(anonymous)#2"), 2)),
    ("a mark in another file never carries", NEW, at(NEW), at(OLD, path=OTHER), Exact(key(NEW))),
    ("Rust and Go spellings carry by the leading token", "route cmd : & Cmd , x : u8",
     at("route cmd : & Cmd , x : u8"), at("route cmd : & Cmd"),
     Carry(key("route cmd : & Cmd"), key("route cmd : & Cmd , x : u8"))),
    ("a C++ overload carries while its sibling keeps its exact mark", "f( int , int )",
     at("f( int , int )", "f( double )"), at("f( int )", "f( double )"),
     Carry(key("f( int )"), key("f( int , int )"))),
    ("the C++ sibling keeps its exact mark", "f( double )",
     at("f( int , int )", "f( double )"), at("f( int )", "f( double )"), Exact(key("f( double )"))),
    ("a case-only change does not carry", "classify( a )", at("classify( a )"),
     at("Classify( a )"), Exact(key("classify( a )"))),
]

# (case, the function asked about, the file's functions, the marked keys, every key the refusal names)
REFUSED = [
    ("two bare-name candidates", NEW, at(NEW), at(OLD, "classify( x )"),
     [NEW, OLD, "classify( x )"]),
    ("two unmarked functions share the bare name", NEW, at(NEW, "classify( y )"), at(OLD),
     [NEW, "classify( y )", OLD]),
    ("twins (#2) never carry", TWIN, at(NEW, TWIN), at(NEW, OLD), [TWIN, OLD]),
    ("a twin's mark never carries", NEW, at(NEW), at("classify#2"), [NEW, "classify#2"]),
]


@pytest.mark.parametrize("case, function, functions, marked, answer", HAND, ids=[r[0] for r in HAND])
def test_resolve_answers_the_hand_table(case, function, functions, marked, answer):
    assert keys.resolve(key(function), functions, marked) == answer


@pytest.mark.parametrize("case, function, functions, marked, named", REFUSED, ids=[r[0] for r in REFUSED])
def test_resolve_refuses_an_ambiguous_carry_and_names_it(case, function, functions, marked, named):
    answer = keys.resolve(key(function), functions, marked)

    assert isinstance(answer, Refused)
    assert_names_the_ambiguity(answer.text, named)


def assert_names_the_ambiguity(text: str, named: list[str]) -> None:
    """The file, every candidate key, the explicit form, on one ASCII line."""
    assert text.startswith(f"{PATH}: ")
    for name in named:
        assert f'"{name}"' in text
    assert "ratchet move --function" in text
    assert text.isascii() and "\n" not in text


def test_a_twin_refusal_says_twins_never_carry():
    answer = keys.resolve(key(TWIN), at(NEW, TWIN), at(NEW, OLD))

    assert "twins never carry a mark" in answer.text
    assert f'crapkit ratchet move --function {PATH} "{OLD}" "{TWIN}"' in answer.text


def test_two_candidates_print_one_explicit_move_each():
    answer = keys.resolve(key(NEW), at(NEW), at(OLD, "classify( x )"))

    for old in (OLD, "classify( x )"):
        assert f'crapkit ratchet move --function {PATH} "{old}" "{NEW}"' in answer.text


def test_a_path_with_a_space_prints_as_one_shell_word():
    spaced = "calc dir/grade.py"
    answer = keys.resolve(key(TWIN, spaced), at(NEW, TWIN, path=spaced), at(NEW, OLD, path=spaced))

    assert answer.text.startswith(f"{spaced}: no mark carries to ")
    assert f'crapkit ratchet move --function "{spaced}" "{OLD}" "{TWIN}"' in answer.text


def test_a_carry_reads_the_mark_recorded_under_the_old_key():
    """The signature probe: `classify( a , b )` marked 7.0000, the function now
    `classify( a , b , c = None )`. MarkIndex keeps answering the exact key
    only; the carry names the key whose mark judges the function."""
    index = keys.MarkIndex([RatchetEntry(PATH, OLD, 7.0)])

    answer = keys.resolve(key(NEW), at(NEW), index.keys())

    assert answer == Carry(key(OLD), key(NEW))
    assert (index.mark(key(NEW)), index.mark(answer.old_key)) == (None, 7.0)


def test_resolve_counts_the_function_among_the_file_functions():
    assert keys.resolve(key(NEW), set(), at(OLD)) == Carry(key(OLD), key(NEW))


# --- pair_moves -----------------------------------------------------------------

def test_one_drop_and_one_add_with_one_bare_name_pair():
    assert keys.pair_moves(PATH, at(OLD), at(NEW)) == keys.Moves([(key(OLD), key(NEW))], [], [])


def test_two_adds_with_one_bare_name_give_no_pair_and_a_refusal_naming_both():
    moves = keys.pair_moves(PATH, at(OLD), at(NEW, "classify( y )"))

    assert (moves.pairs, moves.unpaired) == ([], sorted(at(OLD, NEW, "classify( y )")))
    assert len(moves.refused) == 1
    assert_names_the_ambiguity(moves.refused[0].text, [OLD, NEW, "classify( y )"])


def test_a_twin_drop_gives_no_pair():
    moves = keys.pair_moves(PATH, at("classify( a )#2"), at(NEW))

    assert (moves.pairs, moves.unpaired) == ([], sorted(at("classify( a )#2", NEW)))
    assert "twins never carry a mark" in moves.refused[0].text


def test_keys_that_share_no_bare_name_stay_unpaired_with_no_refusal():
    moves = keys.pair_moves(PATH, at(OLD, "(anonymous) ( y )"), at("grade( a )", "(anonymous) ( z )"))

    assert moves == keys.Moves([], sorted(at(OLD, "(anonymous) ( y )", "grade( a )",
                                             "(anonymous) ( z )")), [])


def test_a_key_in_another_file_never_pairs():
    moves = keys.pair_moves(PATH, at(OLD), at(NEW, path=OTHER))

    assert moves == keys.Moves([], sorted(at(OLD) | at(NEW, path=OTHER)), [])


def test_pairs_and_refusals_are_independent_per_bare_name():
    moves = keys.pair_moves(PATH, at(OLD, "grade( a )", "grade( b )"), at(NEW, "grade( c )"))

    assert moves.pairs == [(key(OLD), key(NEW))]
    assert moves.unpaired == sorted(at("grade( a )", "grade( b )", "grade( c )"))
    assert len(moves.refused) == 1
    assert_names_the_ambiguity(moves.refused[0].text, ["grade( a )", "grade( b )", "grade( c )"])


# The mission-4 probe shapes (tests/unit/test_mission4_probes.py), as pairing rows.

def test_the_merge_probe_pairs_ours_rename():
    """merge_ratchets(base {old: 7}, ours {new: 7}, theirs {old: 6}): ours
    dropped the old key and added the new one, which pair."""
    base, ours = at(OLD), at(NEW)

    assert keys.pair_moves(PATH, base - ours, ours - base).pairs == [(key(OLD), key(NEW))]


def test_the_report_probe_pairs_the_rekey_commit_and_not_the_second_classify():
    """The marks history's re-key commit pairs; the commit that adds twin #2
    drops nothing, so its add stays unpaired."""
    seeded, rekeyed, second = keys_of(SEEDED), keys_of(REKEYED), keys_of(SECOND)

    rekey = keys.pair_moves(PATH, seeded - rekeyed, rekeyed - seeded)
    added = keys.pair_moves(PATH, rekeyed - second, second - rekeyed)

    assert rekey == keys.Moves([(key(OLD), key(NEW))], [], [])
    assert added == keys.Moves([], [key(TWIN)], [])


# --- checks 2 and 3: the drivers --------------------------------------------------

def hypothesis_or_skip():
    """hypothesis, its strategies and the settings every driver here runs
    under. No example database, so a run leaves no .hypothesis/ behind."""
    hypothesis = pytest.importorskip("hypothesis")
    return hypothesis, hypothesis.strategies, hypothesis.settings(database=None, deadline=None)


def long_names(st):
    ident = st.from_regex(r"[a-z][a-z_]{0,6}", fullmatch=True)
    params = st.lists(st.sampled_from(["a", "b", "c = None", "x : int"]), max_size=3)
    return st.builds(lambda name, ps: f"{name}( {' , '.join(ps)} )".replace("(  )", "( )"),
                     ident, params)


def cast(pool: list, parts: tuple, *wanted: int) -> set:
    """The pool's members whose part is one of `wanted`."""
    return {member for member, part in zip(pool, parts) if part in wanted}


# --- check 2: a property over twin-free files -------------------------------------

def assert_marked_functions_resolve_exact(functions: set, marked: set, left: set) -> None:
    held, marks = at(*functions), at(*marked) | at(*(left - functions))
    for name in marked:
        assert keys.resolve(key(name), held, marks) == Exact(key(name))


def assert_round_trip(long_name: str, ordinal: int) -> None:
    assert keys.split_ordinal(keys.key_name(long_name, ordinal)) == (long_name, ordinal)


# Each name takes one of four parts in a file: 0 absent, 1 an unmarked
# function, 2 a marked function, 3 a mark whose function left the file.
TWIN_FREE = [OLD, NEW, "classify( x )", "classify( )", "grade( a )", "(anonymous) ( z )"]
ROUND_TRIP = TWIN_FREE + ["(anonymous)", "op#( a )"]


def test_every_marked_function_of_a_twin_free_file_resolves_exact():
    for parts in itertools.product(range(4), repeat=len(TWIN_FREE)):
        assert_marked_functions_resolve_exact(
            cast(TWIN_FREE, parts, 1, 2), cast(TWIN_FREE, parts, 2), cast(TWIN_FREE, parts, 3))


def test_key_name_and_split_ordinal_round_trip():
    for long_name, ordinal in itertools.product(ROUND_TRIP, [*range(1, 65), 999, 10_000]):
        assert_round_trip(long_name, ordinal)


def test_hypothesis_every_marked_function_of_a_twin_free_file_resolves_exact():
    hypothesis, st, quiet = hypothesis_or_skip()
    names = long_names(st)

    @quiet
    @hypothesis.given(functions=st.sets(names, min_size=1, max_size=8),
                      left=st.sets(names, max_size=4), data=st.data())
    def check(functions, left, data):
        marked = data.draw(st.sets(st.sampled_from(sorted(functions)), min_size=1))
        assert_marked_functions_resolve_exact(functions, marked, left)

    check()


def test_hypothesis_key_name_and_split_ordinal_round_trip():
    hypothesis, st, quiet = hypothesis_or_skip()

    @quiet
    @hypothesis.given(long_name=st.one_of(long_names(st), st.sampled_from(ROUND_TRIP)),
                      ordinal=st.integers(min_value=1, max_value=10_000))
    def check(long_name, ordinal):
        assert_round_trip(long_name, ordinal)

    check()


# --- check 3: an independent pairing model ----------------------------------------

def model_bare(name: str) -> str:
    """docs/ratchet.md's key: the long name, then `#N` for the Nth twin. The
    bare name is the long name up to its first `(`."""
    return model_split(name)[0].split("(")[0]


def model_split(name: str) -> tuple[str, int]:
    head, sep, tail = name.rpartition("#")
    return (head, int(tail)) if sep and tail.isdigit() and int(tail) > 1 else (name, 1)


def model_pairs(path: str, dropped: set, added: set) -> tuple[set, set]:
    """The carry rule: a key that left the file pairs with a key that arrived in it when
    they share a bare name, exactly one of each in the file holds it, and
    neither is a twin or anonymous. Everything else stays unpaired."""
    gone = [d for d in dropped if d[0] == path]
    came = [a for a in added if a[0] == path]
    pairs = set()
    for old in gone:
        bare = model_bare(old[1])
        rivals = [d for d in gone if model_bare(d[1]) == bare]
        takers = [a for a in came if model_bare(a[1]) == bare]
        if bare and len(rivals) == 1 and len(takers) == 1 and not twin(old) and not twin(takers[0]):
            pairs.add((old, takers[0]))
    paired = {k for pair in pairs for k in pair}
    return pairs, (dropped | added) - paired


def twin(key: tuple[str, str]) -> bool:
    return model_split(key[1])[1] > 1


def assert_pairing_agrees(dropped: set, added: set) -> None:
    moves = keys.pair_moves(PATH, dropped, added)

    assert (set(moves.pairs), set(moves.unpaired)) == model_pairs(PATH, dropped, added)
    assert len(moves.unpaired) == len(set(moves.unpaired))


# Each key takes one of three parts: 0 untouched, 1 dropped, 2 added. The pool
# holds two candidates and a twin under one bare name, a twin of the new key, a
# second bare name, both anonymous spellings and a key in another file.
PAIRING = [key(OLD), key(NEW), key("classify( x )"), key("classify#2"), key(TWIN),
           key("grade( a )"), key("(anonymous)"), key("(anonymous) ( z )"), key(NEW, OTHER)]


def test_pair_moves_agrees_with_the_independent_model():
    for parts in itertools.product(range(3), repeat=len(PAIRING)):
        assert_pairing_agrees(cast(PAIRING, parts, 1), cast(PAIRING, parts, 2))


def test_hypothesis_pair_moves_agrees_with_the_independent_model():
    hypothesis, st, quiet = hypothesis_or_skip()
    names = st.one_of(long_names(st),
                      st.builds(lambda name, n: f"{name}#{n}", long_names(st), st.integers(2, 4)),
                      st.sampled_from(["(anonymous)", "(anonymous) ( z )", "(anonymous)#2"]))
    keyed = st.tuples(st.sampled_from([PATH, PATH, PATH, OTHER]), names)

    @hypothesis.settings(quiet, max_examples=300)
    @hypothesis.given(dropped=st.sets(keyed, max_size=6), added=st.sets(keyed, max_size=6))
    def check(dropped, added):
        assert_pairing_agrees(dropped, added - dropped)

    check()
