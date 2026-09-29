"""The digest's lines and totals, read exactly: which moves it names, in what order, how many.

README "digest": the totals line, then the largest regressions, the functions new
over their ceiling and the improvements of functions that were over it, five of
each, with equal moves (compared at 4 places) listed by path.
"""
from crapkit.digest import Totals, build_digest, scope_totals, totals_from_counts
from crapkit.score import ScoredRow

CEILINGS = {"src": 6, "lib": 12}


def ceiling_of(scope: str) -> int:
    """Every scope these rows use, and no other: a scope read wrong is a KeyError."""
    return CEILINGS[scope]


def row(path: str, crap: float, scope: str = "src", name: str = "f( )") -> ScoredRow:
    return ScoredRow(scope, path, name, 1, 9, 3, 3, 3, 5, 1, 1, 0.5, "measured", crap, "add-tests")


def lines(prev: list, cur: list, **kwargs) -> list[str]:
    return build_digest(prev, cur, ceiling_of=ceiling_of, **kwargs).lines


def test_the_totals_round_the_average_to_4_places_and_the_share_to_2():
    assert totals_from_counts(3, 1, 10.0) == Totals(3, 1, 10.0, 3.3333, 33.33)
    assert totals_from_counts(0, 0, 0.0) == Totals(0, 0, 0.0, 0.0, 0.0)
    assert type(totals_from_counts(3, 1, 10.0).pct_over) is float


def test_each_scope_is_counted_against_its_own_ceiling():
    rows = [row("src/a.py", 10.0, scope="lib"), row("src/b.py", 10.0)]

    counted = scope_totals(rows, target=6, scope_targets={"lib": 12})

    assert {scope: t.over_target for scope, t in counted.items()} == {"lib": 0, "src": 1}


def test_regressions_list_the_largest_rise_first_and_ties_by_path():
    """Rises of 1.45 and 1.4 differ at 4 places and tie at 0; 0.5 is still a rise."""
    prev = [row(f"src/{name}.py", 10.0) for name in "abcd"]
    cur = [row("src/a.py", 11.4), row("src/b.py", 11.45), row("src/c.py", 10.5),
           row("src/d.py", 11.4)]

    assert lines(prev, cur)[1:] == [
        "regressed +1.4: src/b.py f( ) (crap 11.4)", "regressed +1.4: src/a.py f( ) (crap 11.4)",
        "regressed +1.4: src/d.py f( ) (crap 11.4)", "regressed +0.5: src/c.py f( ) (crap 10.5)"]


def test_new_functions_list_the_highest_crap_first_and_ties_by_path():
    """30.45 and 30.4 differ at 4 places and tie at 0; 29.999999999999996 and 30.0
    are one CRAP at 4 places. The earlier run scored scope src, so these read
    "new", not "newly scored"."""
    cur = [row("src/e.py", 30.4), row("src/d.py", 30.45), row("src/c.py", 30.0),
           row("src/b.py", 29.999999999999996), row("src/a.py", 40.0)]

    assert lines([row("src/z.py", 1.0)], cur)[1:] == [
        "new over ceiling: src/a.py f( ) (crap 40.0)",
        "new over ceiling: src/d.py f( ) (crap 30.4)",
        "new over ceiling: src/e.py f( ) (crap 30.4)",
        "new over ceiling: src/b.py f( ) (crap 30.0)",
        "new over ceiling: src/c.py f( ) (crap 30.0)"]


def test_improvements_count_only_what_was_over_its_scope_s_ceiling():
    prev = [row("src/a.py", 10.0), row("src/b.py", 10.0, scope="lib"), row("src/c.py", 8.0)]
    cur = [row("src/a.py", 9.5), row("src/b.py", 9.5, scope="lib"), row("src/c.py", 7.5)]

    assert lines(prev, cur)[1:] == ["improved -0.5: src/a.py f( ) (crap 9.5)",
                                    "improved -0.5: src/c.py f( ) (crap 7.5)"]


def test_each_list_stops_at_five_unless_told():
    prev = [row(f"src/r{n}.py", 10.0) for n in range(6)] + [
        row(f"src/i{n}.py", 20.0) for n in range(6)]
    cur = [row(f"src/r{n}.py", 11.0) for n in range(6)] + [
        row(f"src/i{n}.py", 19.0) for n in range(6)] + [row(f"src/n{n}.py", 30.0) for n in range(6)]

    five = lines(prev, cur)
    two = lines(prev, cur, top=2)

    assert [line.split(":")[0] for line in five[1:]] == (
        ["regressed +1.0"] * 5 + ["new over ceiling"] * 5 + ["improved -1.0"] * 5)
    assert len(two) == 1 + 3 * 2


def test_an_improvement_alone_speaks_when_the_totals_do_not_move():
    """a improves while a function under the ceiling replaces one of equal CRAP:
    the counts and the load stay put, and only the improvement is news."""
    prev = [row("src/a.py", 10.0), row("src/gone.py", 2.0)]
    cur = [row("src/a.py", 9.0), row("src/new.py", 3.0)]

    assert lines(prev, cur) == [
        "CRAP load 12.0 -> 12.0; over ceiling 1 -> 1; functions 2 -> 2",
        "improved -1.0: src/a.py f( ) (crap 9.0)"]
