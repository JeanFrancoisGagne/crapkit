"""Brief packet fields and regrowth: the remedy judged against today's ceiling,
the file totals, the regrowth label and history, and the stale flag.

Expected values: README.md's remedy table and agent-json.md:131 (judged
against the ceiling crapkit.toml holds now), agent-json.md:478-483 for
regrowth, exact sums for the file totals, and `git rev-parse HEAD` for stale.
"""
from __future__ import annotations

from fractions import Fraction

from hypothesis import assume, event, given, strategies as st
import pytest

from accuracy.kit import drive, exact, repos, rulings
from accuracy.kit.settings import pure
from accuracy.score_model import cases, cli_repo, model_score, production

REGROWTH_ROWS = cases.hand("Brief packet fields and regrowth")
REMEDIES = ["ok", "add-tests", "split-lines", "decompose"]


def _ints(text: str) -> list[int]:
    return [int(part) for part in text.split(",")]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in REGROWTH_ROWS],
                         ids=[row[0] for row in REGROWTH_ROWS])
def test_regrowth_hand_rows(given_, expected):
    history = [{"run_id": n, "ccn": ccn} for n, ccn in enumerate(_ints(given_["ccns"]), start=1)]
    got = production.load("packet:regrowth")(history)

    assert got["regrown"] == (expected["regrown"] == "1")
    assert got["history"] == [[h["run_id"], h["ccn"]] for h in history]


@given(st.lists(st.integers(1, 12), min_size=1, max_size=10))
@pure
def test_regrowth_label_matches_model(ccns):
    history = [{"run_id": n, "ccn": ccn} for n, ccn in enumerate(ccns, start=1)]

    assert production.load("packet:regrowth")(history)["regrown"] == model_score.regrown(ccns)


# --- the remedy judged against today's ceiling ---------------------------------------------------

def _row(path: str, start: int, end: int, ccn: int, twelfths: int, stored: str, flag="measured",
         name: str = "f( x )", occurrence: int = 1):
    cov = 0.0 if flag != "measured" else twelfths / 12
    crap = production.load("score:crap")(ccn, cov)
    return production.scored_row("src", path, name, start, end, ccn, cov, flag, crap, stored,
                                 occurrence=occurrence)


def crapkit_rejudged(row, ceiling: int, neighbours: list) -> str:
    rows_of = lambda path: [r for r in [row, *neighbours] if r.path == path]  # noqa: E731
    return production.load("packet:rejudged")(row, ceiling, rows_of).remedy


def counted(cov: float) -> Fraction:
    """The coverage a row here stands for: every row is built from a count of
    twelfths, so the fraction nearest its double with a denominator up to 12.
    The README defines cov as covered over total; the double 0.666...6 is 2/3,
    and CRAP(9, 2/3) is 12 exactly."""
    return Fraction(cov).limit_denominator(12)


def _model(row, ceiling: int, neighbours: list) -> str:
    fresh = [model_score.Fresh(r.scope, r.path, r.long_name, r.start, r.end, r.occurrence)
             for r in [row, *neighbours] if r.flag not in ("no-lane", "cc-only")]
    shared = row.flag not in ("no-lane", "cc-only") and model_score.shared_span(fresh[0], fresh)
    return model_score.remedy(row.ccn, exact.crap(row.ccn, counted(row.cov)), ceiling, shared)


@st.composite
def judged_rows(draw):
    """A row as a run stores it under some old ceiling, and today's ceiling. A
    one-line Python def is stored untested at cov 0, the floor README.md#remedy
    gives it, and its stored remedy is the old ceiling's, shared span included."""
    path, start = draw(st.sampled_from(["src/a.py", "src/a.ts"])), draw(st.integers(1, 50))
    end = start if draw(st.booleans()) else start + draw(st.integers(1, 20))
    floored = path.endswith(".py") and start == end
    flag = "untested" if floored else draw(st.sampled_from(["measured", "untested"]))
    row = _row(path, start, end, draw(st.integers(1, 20)), draw(st.integers(0, 12)), "ok", flag)
    stored = _model(row, draw(st.integers(1, 40)), [])
    return row._replace(remedy=stored), draw(st.integers(1, 40))


@given(judged_rows())
@pure
def test_a_rejudged_row_takes_today_s_ceiling(case):
    """agent-json.md:131: judged against the ceiling crapkit.toml holds now.
    Away from an exact CRAP equal to the ceiling (rulings SM-CEILING-EQ)."""
    row, ceiling = case
    at_ceiling = exact.crap(row.ccn, counted(row.cov)) == ceiling
    if at_ceiling:
        event("shape:crap-equals-ceiling")
    assume(not at_ceiling)

    assert crapkit_rejudged(row, ceiling, []) == _model(row, ceiling, [])


SHAPES = {
    "one_line_def": (lambda: _row("src/a.py", 7, 7, 3, 0, "ok", "untested"), []),
    "shared_span": (lambda: _row("src/a.ts", 7, 7, 3, 0, "ok", "untested", name="g( x )"),
                    [lambda: _row("src/a.ts", 7, 7, 3, 0, "ok", "untested", name="h( x )")]),
    "plain": (lambda: _row("src/a.py", 7, 15, 3, 0, "ok", "untested"), []),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_packet_rejudge_matches_run_remedy(shape):
    """R49: a row stored `ok` under a ceiling of 30 and rejudged at 6 reads
    split-lines when its lines are shared or it is a one-line Python def
    (README.md#remedy), add-tests otherwise, as the next coverage run would."""
    make, others = SHAPES[shape]
    row, neighbours = make(), [other() for other in others]

    assert crapkit_rejudged(row, 6, neighbours) == _model(row, 6, neighbours)


# --- file totals -------------------------------------------------------------------------------------

def _file_rows(fns: list[tuple[int, int]]) -> list:
    return [_row("src/a.py", 1 + 20 * n, 10 + 20 * n, ccn, twelfths, "ok", name=f"f{n}( x )")
            for n, (ccn, twelfths) in enumerate(fns)]


def _boundary(craps: list[Fraction], ceiling: int) -> bool:
    """An exact CRAP at the ceiling (SM-CEILING-EQ) or an exact 2 dp tie of the load (D5.2)."""
    return ceiling in craps or sum(craps, Fraction(0)) * 200 % 2 == 1


@given(st.lists(st.tuples(st.integers(1, 20), st.integers(0, 12)), min_size=1, max_size=12),
       st.integers(1, 30))
@pure
def test_file_totals_count_and_sum_exactly(fns, ceiling):
    craps = [exact.crap(ccn, Fraction(twelfths, 12)) for ccn, twelfths in fns]
    if _boundary(craps, ceiling):
        event("shape:file-totals-boundary")
    assume(not _boundary(craps, ceiling))
    got = production.load("packet:file_totals")(_file_rows(fns), {}, ceiling)

    assert got["functions"] == len(fns)
    assert got["over_target"] == sum(1 for value in craps if value > ceiling)
    assert f"{got['crap_load']:.2f}" == exact.fixed(sum(craps, Fraction(0)), 2)


# --- CLI: today's ceiling on every surface, the stale flag, regrowth history ---------------------------

LAYOUT = cli_repo.Layout(modules=(
    cli_repo.Module("a", "src/a/mod.py", (cli_repo.Fn("dark", 5, 0),)),
    cli_repo.Module("b", "src/b/mod.py", (cli_repo.Fn("lit", 2, 2),))))


def _cli(make_repo, layout=LAYOUT):
    built = make_repo(cli_repo.spec(layout))
    return built, drive.Driver(built.root, date_now=repos.EPOCH + 86_400)


def _raise_the_ceiling(root, target: int) -> None:
    config = root / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("target = 6", f"target = {target}"),
                      encoding="utf-8")


def _remedy_on(cli, surface: str) -> str:
    if surface == "brief":
        return cli.json("brief", "src/a/mod.py", "dark")["remedy"]
    if surface == "next_item":
        payload = cli.run("next-item").json()
        return payload["item"]["remedy"] if not payload["empty"] else "ok"
    return cli.json("worklist")["active"][0]["remedy"]


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("surface", ["brief", "next_item"])
def test_brief_and_next_item_follow_todays_ceiling(make_repo, surface):
    """R50: `dark` (ccn 5, cov 0, CRAP 30) was scored add-tests at target 6.
    With target 30 in crapkit.toml and no run since, brief and next-item say ok
    (agent-json.md:131)."""
    built, cli = _cli(make_repo)
    assert cli.run("coverage").code == 0
    _raise_the_ceiling(built.root, 30)

    assert _remedy_on(cli, surface) == model_score.remedy(5, exact.crap(5, 0), 30)


@pytest.mark.nightly
@pytest.mark.process
def test_worklist_keeps_the_run_s_stored_verdict(make_repo):
    """R105, SM-WORKLIST-STORED: e9abef6 made the worklist judge today's
    ceiling too; 7eac49c1 took that back, and agent-json.md#worklist says the
    list prints the verdict the run stored while next-item judges today's."""
    built, cli = _cli(make_repo)
    assert cli.run("coverage").code == 0
    _raise_the_ceiling(built.root, 30)

    rulings.pin_ruling("SM-WORKLIST-STORED", crapkit=_remedy_on(cli, "worklist"),
                       oracle=model_score.remedy(5, exact.crap(5, 0), 30))


@pytest.mark.nightly
@pytest.mark.process
def test_stale_flag_follows_head(make_repo):
    """agent-json.md:107: stale is true when the ranked run's commit is not HEAD."""
    built, cli = _cli(make_repo)
    assert cli.run("coverage").code == 0
    before = cli.json("brief", "src/a/mod.py", "dark")["stale"]
    repos.git(built.top, "commit", "-q", "--allow-empty", "-m", "later", date=repos.EPOCH + 60)
    head = repos.git(built.top, "rev-parse", "HEAD").strip()
    after = cli.json("brief", "src/a/mod.py", "dark")

    assert (before, after["stale"]) == (False, True)
    assert cli.run("next-item").json()["commit"] != head


@pytest.mark.nightly
@pytest.mark.process
def test_regrowth_history_counts_every_run_kind(make_repo):
    """agent-json.md:483: one pair for every stored run that scored the
    function, whatever the run's kind: inventory, full and partial runs. No
    past bug: the two commits that pinned this (a15a4a0, e5b07a5) changed tests
    only, and history counted every run kind before them."""
    built, cli = _cli(make_repo)
    for argv in (("inventory",), ("coverage",), ("coverage", "--lane", "a")):
        assert cli.run(*argv).code == 0
    kinds = [row["kind"] for row in cli.store("SELECT kind FROM runs ORDER BY id")]
    history = cli.json("brief", "src/a/mod.py", "dark")["regrowth"]["history"]

    assert [pair[0] for pair in history] == [1, 2, 3] and len(kinds) == 3
