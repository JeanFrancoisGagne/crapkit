"""Queue admission, the worklist's ranking and dormant list, and next-item's order
and empty-queue reasons.

Oracles: numpy's percentile (method="lower") for the top-10% weight, the
closed form for the lowest ccn that can be debt, the README's and
agent-json.md's rules through model_score, and the hand rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import html5lib
from hypothesis import given, strategies as st
import numpy
import pytest

from accuracy.kit import drive, exact, repos, rulings
from accuracy.kit.settings import pure
from accuracy.score_model import cases, cli_repo, model_score, production

ADMISSION_ROWS = cases.hand("Queue admission and floors")
RANKING_ROWS = cases.hand("Worklist ranking and dormant list")
NEXT_ROWS = cases.hand("next-item ranking and empty-queue reasons")
CEILING = 6


# --- admission -------------------------------------------------------------------------------

def _hot(text: str) -> float | None:
    return None if text == "none" else float(text)


def _admits(given_: dict) -> bool:
    churn = {"src/a.py": production.file_churn(3, 1, float(given_["weight"]))}
    adm = production.make("worklist:Admission", churn=churn, floor=int(given_["floor"]),
                          hot=_hot(given_["hot"]))
    return adm.admits("src/a.py", int(given_["ccn"]), over_target=given_["over"] == "1")


@pytest.mark.parametrize("given_,expected", [row[1:] for row in ADMISSION_ROWS],
                         ids=[row[0] for row in ADMISSION_ROWS])
def test_admission_hand_rows(given_, expected):
    if "ceiling" in given_:
        got = production.load("worklist:over_target_floor")(int(given_["ceiling"]))
        assert got == int(expected["lowest_debt_ccn"])
    else:
        assert _admits(given_) == (expected["admitted"] == "1")


def test_the_lowest_debt_ccn_is_the_closed_form_for_every_ceiling_to_2000():
    floor = production.load("worklist:over_target_floor")

    assert [c for c in range(1, 2001) if floor(c) != model_score.lowest_debt_ccn(c)] == []


def _numpy_hot(weights: list[float]) -> float | None:
    positive = [weight for weight in weights if weight > 0]
    if len(positive) < 5 or min(positive) == max(positive):
        return None
    return float(numpy.percentile(positive, 90, method="lower"))


def churn_maps():
    weight = st.floats(0.0, 50.0, allow_nan=False)
    return st.lists(weight, min_size=0, max_size=40).map(
        lambda weights: {f"src/f{n}.py": production.file_churn(1, 1, w)
                         for n, w in enumerate(weights)})


def test_numpy_is_the_pinned_oracle(oracle):
    assert oracle("numpy").version == numpy.__version__


@given(churn_maps())
@pure
def test_the_hot_weight_is_numpy_s_lower_90th_percentile(churn):
    threshold = production.load("worklist:admission")(churn, 5).hot

    assert threshold == _numpy_hot([c.weight for c in churn.values()])


def test_hot_promotion_needs_five_files():
    """SM-HOT-MIN: with four weighted files the README's top-10% rule, read with
    numpy's lower percentile, promotes the heaviest file; crapkit promotes none."""
    weights = [0.1, 0.2, 0.3, 0.4]
    churn = {f"src/f{n}.py": production.file_churn(2, 1, w) for n, w in enumerate(weights)}
    adm = production.load("worklist:admission")(churn, 5)
    read = float(numpy.percentile(weights, 90, method="lower"))
    rulings.pin_ruling("SM-HOT-MIN", crapkit=int(adm.admits("src/f3.py", 4, over_target=False)),
                       oracle=int(model_score.admitted(4, 0.4, False, 5, read)))


@given(churn_maps(), st.integers(1, 40), st.integers(1, 60))
@pure
def test_an_over_ceiling_row_is_always_admitted(churn, ccn, floor):
    """R09: a row over its ceiling is queued whatever its ccn and churn."""
    adm = production.load("worklist:admission")(churn, floor)

    assert all(adm.admits(path, ccn, over_target=True) for path in [*churn, "src/none.py"])


# --- a queue scenario, generated -----------------------------------------------------------------

@dataclass(frozen=True)
class Fn:
    path: str
    name: str
    start: int
    ccn: int
    twelfths: int
    flag: str


@st.composite
def scenarios(draw):
    """Up to 8 files, some without churn, each with up to 4 functions. A file's
    weight stays inside the README's Risk rule, at most 0.5 per commit: crapkit's
    runtime guard stops a worklist whose churn breaks it."""
    count = draw(st.integers(1, 8))
    commits = draw(st.lists(st.integers(0, 6), min_size=count, max_size=count))
    churn = {f"src/f{n}.py": production.file_churn(c, 1, draw(st.integers(1, 500 * c)) / 1000)
             for n, c in enumerate(commits) if c > 0}
    fns = []
    for n in range(count):
        for k in range(draw(st.integers(1, 4))):
            fns.append(Fn(f"src/f{n}.py", f"g{k}( x )", 1 + 20 * k, draw(st.integers(1, 20)),
                          draw(st.integers(0, 12)),
                          draw(st.sampled_from(["measured", "measured", "untested", "no-lane"]))))
    return churn, fns, draw(st.integers(1, 12))


def _cov(fn: Fn) -> float:
    return 0.0 if fn.flag != "measured" else fn.twelfths / 12


def _input_remedy(ccn: int, cov: float) -> str:
    """The remedy an input row carries: the README table on the exact CRAP, so
    building a row reaches no crapkit function a retro replay's version lacks."""
    return model_score.remedy(ccn, exact.crap(ccn, Fraction(cov)), CEILING)


def scored_rows(fns: list[Fn]) -> list:
    crap = production.load("score:crap")
    return [production.scored_row("src", fn.path, fn.name, fn.start, fn.start + 9, fn.ccn,
                                  _cov(fn), fn.flag, crap(fn.ccn, _cov(fn)),
                                  _input_remedy(fn.ccn, _cov(fn)))
            for fn in fns]


def _marks(rows):
    verdicts = {(r.path, r.long_name, r.start, r.occurrence): (r.flag, r.remedy) for r in rows}
    scores = {(r.path, r.long_name, r.start, r.occurrence): (r.crap, r.cov) for r in rows}
    return production.make("worklist:Marks", verdicts=verdicts, scores=scores)


def crapkit_worklist(churn, fns, floor, top=1000):
    rows = scored_rows(fns)
    inventory = [production.inventory_row("src", r.path, r.long_name, r.start, r.end, r.ccn)
                 for r in rows]
    return production.load("worklist:build_worklist")(inventory, churn, floor=floor, top=top,
                                                      marks=_marks(rows))


def _model_admitted(churn, rows, floor) -> list:
    hot = model_score.hot_weight([c.weight for c in churn.values()])
    return [r for r in rows if model_score.admitted(
        r.ccn, churn[r.path].weight if r.path in churn else 0.0, r.remedy != "ok", floor, hot)]


def _entry(row, churn) -> model_score.Entry:
    c = churn.get(row.path)
    return model_score.Entry(row.path, row.start, row.occurrence, row.ccn,
                             c.commits if c else 0, c.weight if c else 0.0)


def _keys(entries) -> list[tuple]:
    return [(e.path, e.start) for e in entries]


@given(scenarios())
@pure
def test_the_worklist_ranks_what_the_docs_admit_in_the_docs_order(scenario):
    churn, fns, floor = scenario
    worklist = crapkit_worklist(churn, fns, floor)
    active, dormant = model_score.split_active(
        [_entry(r, churn) for r in _model_admitted(churn, scored_rows(fns), floor)])

    assert _keys(worklist.active) == _keys(active)
    assert _keys(worklist.dormant) == _keys(dormant)
    assert worklist.active_total == len(active)


@given(scenarios())
@pure
def test_every_risk_is_ccn_times_weight_at_four_places(scenario):
    churn, fns, floor = scenario
    worklist = crapkit_worklist(churn, fns, floor)

    assert [e.risk for e in worklist.active] == [
        float(model_score.risk(e.ccn, e.weight)) for e in worklist.active]


@given(scenarios(), st.randoms(use_true_random=False))
@pure
def test_input_order_never_changes_the_worklist(scenario, rnd):
    churn, fns, floor = scenario
    shuffled = list(fns)
    rnd.shuffle(shuffled)

    assert _keys(crapkit_worklist(churn, shuffled, floor).active) == _keys(
        crapkit_worklist(churn, fns, floor).active)


def _files_scenario(text: str) -> tuple[dict, list[Fn]]:
    specs = [item.split(":") for item in text.split(",")]
    churn = {f"src/{name}.py": production.file_churn(int(c), 1, float(w))
             for name, _, c, w in specs if int(c) > 0}
    return churn, [Fn(f"src/{name}.py", f"{name}( x )", 1, int(ccn), 12, "measured")
                   for name, ccn, _, _ in specs]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in RANKING_ROWS],
                         ids=[row[0] for row in RANKING_ROWS])
def test_ranking_hand_rows(given_, expected):
    """files=name:ccn:commits:weight,...: one function per file, every one admitted."""
    worklist = crapkit_worklist(*_files_scenario(given_["files"]), floor=1)

    assert ",".join(e.path[4:-3] for e in worklist.active) == expected["order"]
    assert ",".join(e.path[4:-3] for e in worklist.dormant) == expected.get("dormant", "")


def test_equal_risk_ranks_by_ccn_then_commits():
    """SM-RANK-TAIL: risk 4.0 twice (ccn 8 at weight 0.5, ccn 4 at weight 1.0)."""
    churn = {"src/a.py": production.file_churn(2, 1, 1.0), "src/b.py": production.file_churn(2, 1, 0.5)}
    fns = [Fn("src/a.py", "a( x )", 1, 4, 12, "measured"), Fn("src/b.py", "b( x )", 1, 8, 12, "measured")]
    order = [e.path for e in crapkit_worklist(churn, fns, floor=1).active]

    rulings.pin_ruling("SM-RANK-TAIL", crapkit="ccn-8 first" if order[0] == "src/b.py" else "ccn-4 first",
                       oracle="unspecified")


# --- next-item ------------------------------------------------------------------------------------

def crapkit_next(churn, fns, floor):
    adm = production.load("worklist:admission")(churn, floor)
    return production.load("cli.queue:_next_ranked")(scored_rows(fns), adm)


def _model_taken(churn, rows, floor) -> list:
    admitted = set(map(id, _model_admitted(churn, rows, floor)))
    return [model_score.Row(r.scope, r.path, r.long_name, r.start, r.ccn, r.crap) for r in rows
            if model_score.queue_takes(r.flag, r.remedy, r.path in churn, id(r) in admitted)]


def _model_next(churn, rows, floor) -> list:
    order = model_score.next_item_order(_model_taken(churn, rows, floor),
                                        {path: c.commits for path, c in churn.items()})
    return [(row.path, row.start) for row in order]


@given(scenarios())
@pure
def test_next_item_ranks_by_crap_what_the_docs_let_it_take(scenario):
    churn, fns, floor = scenario
    ranked, _ = crapkit_next(churn, fns, floor)

    assert [(r.path, r.start) for r in ranked] == _model_next(churn, scored_rows(fns), floor)


@given(scenarios())
@pure
def test_no_lane_rows_never_outrank_measured_risk(scenario):
    """R05: a no-lane row's cov 0 is a tooling gap: next-item never ranks one,
    and skipped_no_lane counts the ones admission would have taken."""
    churn, fns, floor = scenario
    ranked, skipped = crapkit_next(churn, fns, floor)
    rows = scored_rows(fns)
    admitted = _model_admitted(churn, rows, floor)

    assert [r for r in ranked if r.flag == "no-lane"] == []
    assert skipped == sum(1 for r in admitted if r.flag == "no-lane")


@given(scenarios())
@pure
def test_the_reasons_are_the_complement_of_the_queue(scenario):
    """agent-json.md:208-216: every row the queue did not take sits in exactly
    one reasons bucket, and no taken row sits in any."""
    churn, fns, floor = scenario
    rows = scored_rows(fns)
    adm = production.load("worklist:admission")(churn, floor)
    ranked = {(r.path, r.start) for r in crapkit_next(churn, fns, floor)[0]}
    skip = production.load("cli.queue:_skip_reason")
    buckets = [skip(r, adm, []) for r in rows]
    want = [model_score.skip_bucket(r.flag, False, (r.path, r.start) in ranked, r.path in churn)
            for r in rows]

    assert buckets == want


@given(scenarios())
@pure
def test_worklist_and_next_item_take_the_same_rows(scenario):
    """R09: one admission rule. The rows next-item ranks are the worklist's
    admitted rows minus no-lane ones and minus dormant rows at their ceiling."""
    churn, fns, floor = scenario
    worklist = crapkit_worklist(churn, fns, floor)
    listed = {(e.path, e.start) for e in filter(_queue_view, [*worklist.active, *worklist.dormant])}

    assert {(r.path, r.start) for r in crapkit_next(churn, fns, floor)[0]} == listed


def _queue_view(entry) -> bool:
    return entry.flag != "no-lane" and (entry.commits > 0 or entry.remedy != "ok")


def _next_row(name: str, ccn: str, cov: str, flag: str):
    """A scored row at ceiling 6; a row no lane measures scores cov 0."""
    cov_value = float(cases.fraction(cov)) if flag == "measured" else 0.0
    value = production.load("score:crap")(int(ccn), cov_value)
    return production.scored_row("src", f"src/{name}.py", f"{name}( x )", 1, 10, int(ccn),
                                 cov_value, flag, value, _input_remedy(int(ccn), cov_value))


def _next_rows(text: str) -> tuple[dict, list]:
    """name:ccn:cov:commits:flag items, one function per file src/NAME.py."""
    specs = [item.split(":") for item in text.split(",")]
    churn = {f"src/{name}.py": production.file_churn(int(commits), 1, 0.5)
             for name, _, _, commits, _ in specs if int(commits) > 0}
    return churn, [_next_row(name, ccn, cov, flag) for name, ccn, cov, _, flag in specs]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in NEXT_ROWS],
                         ids=[row[0] for row in NEXT_ROWS])
def test_next_item_hand_rows(given_, expected):
    churn, rows = _next_rows(given_["fns"])
    adm = production.load("worklist:admission")(churn, int(given_["floor"]))
    ranked, skipped = production.load("cli.queue:_next_ranked")(rows, adm)
    items = production.load("cli.queue:_actionable")(ranked)
    order = ",".join(row.path[4:-3] for row in items)

    if "ruling" in given_:
        rulings.pin_ruling(given_["ruling"], crapkit=order.split(",")[0], oracle=given_["doc_says"])
    assert (order, skipped) == (expected["order"], int(expected["skipped_no_lane"]))


def test_equal_crap_ranks_by_commits_then_path():
    """SM-NEXT-TAIL: two dark ccn-5 functions (crap 30) in files with 1 and 4 commits."""
    churn = {"src/a.py": production.file_churn(1, 1, 0.5), "src/b.py": production.file_churn(4, 1, 0.5)}
    fns = [Fn("src/a.py", "a( x )", 1, 5, 0, "measured"), Fn("src/b.py", "b( x )", 1, 5, 0, "measured")]
    ranked, _ = crapkit_next(churn, fns, 1)

    rulings.pin_ruling("SM-NEXT-TAIL", crapkit="4-commit file first" if ranked[0].path == "src/b.py"
                       else "1-commit file first", oracle="unspecified")



def test_scores_equal_at_4_places_rank_by_start():
    """agent-json.md:55-57: CRAP(18, 2/3) is exactly 30 but its float reads a hair
    above it, and CRAP(5, 0) reads 30.0. The two tie at 4 places, so in one file
    the function that starts first comes first."""
    fns = [Fn("src/f.py", "late( x )", 61, 18, 8, "measured"),
           Fn("src/f.py", "early( x )", 1, 5, 0, "measured")]
    ranked, _ = crapkit_next({}, fns, 1)

    assert scored_rows(fns)[0].crap != 30.0
    assert [(r.path, r.start) for r in ranked] == [("src/f.py", 1), ("src/f.py", 61)]
    assert _model_next({}, scored_rows(fns), 1) == [("src/f.py", 1), ("src/f.py", 61)]

# --- one run for both views (CLI) -----------------------------------------------------------------

TWO_SCOPES = cli_repo.Layout(modules=(
    cli_repo.Module("a", "src/a/mod.py", (cli_repo.Fn("hot", 5, 2), cli_repo.Fn("cool", 2, 2))),
    cli_repo.Module("b", "src/b/mod.py", (cli_repo.Fn("wide", 8, 7),))))


@pytest.mark.nightly
@pytest.mark.process
def test_worklist_and_next_item_read_the_same_run(make_repo):
    """R79: after a full run and a partial one (`--lane a`), both views read the
    trusted run: a partial run is never the one ranked (README.md#the-trusted-baseline)."""
    built = make_repo(cli_repo.spec(TWO_SCOPES))
    cli = drive.Driver(built.root, date_now=repos.EPOCH + 86_400)
    assert cli.run("coverage").code == 0
    assert cli.run("coverage", "--lane", "a").code == 0

    assert cli.json("worklist")["run_id"] == 1
    assert cli.run("next-item").json()["run_id"] == 1


def _cli(make_repo, layout):
    built = make_repo(cli_repo.spec(layout))
    return drive.Driver(built.root, date_now=repos.EPOCH + 86_400)


def _starts_to_crap(entries) -> dict[int, float]:
    return {entry["start"]: entry["crap"] for entry in entries}


@pytest.mark.nightly
@pytest.mark.process
def test_an_over_ceiling_row_under_the_floor_is_listed_and_handed_out(make_repo):
    """R09: with worklist_floor = 5, `small` (ccn 3, no branch taken: CRAP 9 * 1
    + 3 = 12) is over the ceiling of 6, so both views take it
    (docs/agent-json.md:210: an over-target row is queued whatever its ccn);
    `tidy` (ccn 2, both branches taken: CRAP 2) is under the floor and the
    ceiling, so neither view takes it."""
    layout = cli_repo.Layout(floor=5, modules=(cli_repo.Module("a", "src/a/mod.py", (
        cli_repo.Fn("small", 3, 0), cli_repo.Fn("tidy", 2, 2))),))
    cli = _cli(make_repo, layout)
    assert cli.run("coverage").code == 0

    assert [e["function"] for e in cli.json("worklist")["active"]] == ["small( x )"]
    assert cli.run("next-item").json()["item"]["function"] == "small( x )"


@pytest.mark.nightly
@pytest.mark.process
def test_next_item_never_hands_out_a_no_lane_row(make_repo):
    """R05: scope c has no lane, so `dark` (ccn 9, CRAP 90 at cov 0) is a wiring
    gap: next-item hands out `lit` (ccn 4, no branch taken: CRAP 20) and counts
    `dark` in skipped_no_lane (README.md:796)."""
    layout = cli_repo.Layout(no_lane=("c",), modules=(
        cli_repo.Module("a", "src/a/mod.py", (cli_repo.Fn("lit", 4, 0),)),
        cli_repo.Module("c", "src/c/mod.py", (cli_repo.Fn("dark", 9, 0),))))
    cli = _cli(make_repo, layout)
    assert cli.run("coverage").code == 0
    payload = cli.run("next-item").json()

    assert (payload["item"]["function"], payload["skipped_no_lane"]) == ("lit( x )", 1)


@pytest.mark.nightly
@pytest.mark.process
def test_each_twin_s_worklist_row_prints_its_own_score(make_repo):
    """R95: two `twin` defs in one file. coverage.py keys its functions by name,
    so the report holds one entry, for the later def (every branch of ccn 7
    taken: CRAP 7); the earlier def has none and scores cov 0, CRAP 7^2 + 7 = 56.
    Each worklist row prints its own CRAP, never its twin's."""
    layout = cli_repo.Layout(modules=(cli_repo.Module("a", "src/a/mod.py", (
        cli_repo.Fn("twin", 7, 0), cli_repo.Fn("twin", 7, 12))),))
    cli = _cli(make_repo, layout)
    assert cli.run("coverage").code == 0
    craps = sorted(_starts_to_crap(cli.json("worklist")["active"]).items())

    assert [crap for _, crap in craps] == [
        float(exact.crap(7, 0)), float(exact.crap(7, Fraction(12, 12)))]


def test_worklist_twin_equals_brief_twin():
    """R95: two functions a file gives one name each keep their own CRAP and
    cov on their worklist rows, the numbers the run scored for that row."""
    fns = [Fn("src/a.py", "h( x )", 1, 6, 0, "measured"), Fn("src/a.py", "h( x )", 21, 6, 12, "measured")]
    churn = {"src/a.py": production.file_churn(2, 1, 0.5)}
    rows = {r.start: (r.crap, r.cov) for r in scored_rows(fns)}
    listed = {e.start: (e.crap, e.cov) for e in crapkit_worklist(churn, fns, floor=1).active}

    assert listed == rows == {1: (42.0, 0.0), 21: (6.0, 1.0)}


# --- the same order on every surface (CLI) -----------------------------------------------------

XHTML = "{http://www.w3.org/1999/xhtml}"


def _html_order(text: str) -> list[tuple[str, int]]:
    """The report's worklist rows (tr.wl) as (path, start), top to bottom."""
    rows = html5lib.parse(text).iter(f"{XHTML}tr")
    return [_location(row) for row in rows if row.get("class") == "wl"]


def _location(row) -> tuple[str, int]:
    """A worklist row's `path:start` cell (div.loc)."""
    loc = next(div.text for div in row.iter(f"{XHTML}div") if div.get("class") == "loc")
    path, _, line = loc.rpartition(":")
    return path, int(line)


def _surfaces_entries() -> list[model_score.Entry]:
    """One commit, so every file weighs 1.0 and has one commit (agent-json.md:707)."""
    return [model_score.Entry(row.path, row.start, 1, row.ccn, 1, 1.0)
            for row in cli_repo.expected(cli_repo.SURFACES)]


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.cross_surface
def test_the_worklist_order_reads_alike_on_json_mcp_and_the_report(make_repo, tmp_path):
    cli = cli_repo.driver(make_repo, cli_repo.SURFACES)
    assert cli.run("coverage").code == 0
    listed = [(e["path"], e["start"]) for e in cli.json("worklist")["active"]]
    (mcp,) = cli.mcp([("list_worklist", {})])
    assert cli.run("report", "--out", str(tmp_path / "report.html")).code == 0
    page = _html_order((tmp_path / "report.html").read_text(encoding="utf-8"))

    assert listed == _keys(model_score.split_active(_surfaces_entries())[0])
    assert [(e["path"], e["start"]) for e in mcp["structuredContent"]["active"]] == listed == page


def _model_queue() -> list[tuple[str, int]]:
    rows = [model_score.Row(r.scope, r.path, r.name, r.start, r.ccn, exact.crap(r.ccn, r.cov))
            for r in cli_repo.expected(cli_repo.SURFACES)
            if model_score.remedy(r.ccn, exact.crap(r.ccn, r.cov), r.ceiling) != "ok"]
    return [(row.path, row.start) for row in model_score.next_item_order(rows, {})]


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.cross_surface
def test_brief_batch_takes_next_item_s_order(make_repo):
    """agent-json.md:599: `brief --batch N` packets come in next-item order,
    crap descending: deep (CRAP 103.2), tangled (39.0), hot (15.5)."""
    cli = cli_repo.driver(make_repo, cli_repo.SURFACES)
    assert cli.run("coverage").code == 0
    items = [(i["path"], i["start"]) for i in cli.run("next-item", "--top", "3").json()["items"]]
    packets = [(p["path"], p["scored"]["start"]) for p in cli.json("brief", "--batch", "3")["packets"]]

    assert items == packets == _model_queue()
