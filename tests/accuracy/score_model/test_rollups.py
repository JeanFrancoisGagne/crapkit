"""Run totals and the trend rollup, digest deltas, and the coverage run summary.

Expected values: totals recomputed in exact arithmetic from the rows (the
store read with sqlite3, the exports with kit.surfaces), the README's grade
table, and agent-json.md:920-929 for the summary, through model_score.
"""
from __future__ import annotations

import dataclasses
from fractions import Fraction
import re
import sys
import tomllib

from hypothesis import assume, event, given, strategies as st
import pytest

from accuracy.kit import drive, exact, repos, rulings, surfaces
from accuracy.kit.settings import pure
from accuracy.score_model import cases, cli_repo, model_score, production

TOTALS_ROWS = cases.hand("Run totals and trend rollup")
DIGEST_ROWS = cases.hand("Digest deltas")
SUMMARY_ROWS = cases.hand("Coverage run summary")


def _row(scope: str, name: str, crap: float, ccn: int = 3):
    return production.scored_row(scope, f"src/{scope}.py", name, 1, 9, ccn, 0.5, "measured", crap, "ok")


def model_totals(rows, ceiling_of) -> dict:
    as_model = [model_score.Row(r.scope, r.path, r.long_name, r.start, r.ccn, Fraction(r.crap))
                for r in rows]
    return model_score.totals(as_model, ceiling_of)


def _crapkit_totals(rows, target: int, scope_targets: dict) -> dict:
    got = production.load("digest:totals")(rows, target=target, scope_targets=scope_targets)
    return {"functions": got.functions, "over_target": got.over_target,
            "crap_load": f"{got.crap_load:.2f}"}


def _expected(rows, target: int, scope_targets: dict) -> dict:
    want = model_totals(rows, lambda scope: model_score.ceiling(scope, target, scope_targets))
    return {"functions": want["functions"], "over_target": want["over_target"],
            "crap_load": f"{want['crap_load']:.2f}"}


# --- totals ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("given_,expected", [row[1:] for row in TOTALS_ROWS],
                         ids=[row[0] for row in TOTALS_ROWS])
def test_totals_hand_rows(given_, expected):
    rows = [_row(given_["scope"], f"f{n}( )", float(Fraction(value)))
            for n, value in enumerate(given_["craps"].split(":"))]
    got = production.load("digest:totals")(rows, target=int(given_["ceiling"]))
    grade = production.load("score:grade")(got.over_target, got.functions)

    assert {"functions": str(got.functions), "over_target": str(got.over_target),
            "crap_load": f"{got.crap_load:.2f}", "grade": grade} == expected


def scored_sets():
    one = st.tuples(st.sampled_from(["a", "b", "c"]), st.integers(1, 30), st.integers(0, 12))
    return st.lists(one, min_size=1, max_size=40)


def _rows_of(drawn) -> list:
    crap = production.load("score:crap")
    return [_row(scope, f"f{n}( )", crap(ccn, twelfths / 12), ccn)
            for n, (scope, ccn, twelfths) in enumerate(drawn)]


def _exact_rows(drawn) -> list[model_score.Row]:
    return [model_score.Row(scope, f"src/{scope}.py", f"f{n}( )", 1, ccn,
                            exact.crap(ccn, Fraction(twelfths, 12)))
            for n, (scope, ccn, twelfths) in enumerate(drawn)]


def _load_tie(drawn) -> bool:
    """The exact CRAP load sits on a 2 dp tie (rulings D5.2, SM-LOAD-ORDER)."""
    return sum(r.crap for r in _exact_rows(drawn)) * 200 % 2 == 1


def _at_a_ceiling(drawn, ceiling_of) -> bool:
    """Some row's exact CRAP equals its ceiling (rulings SM-CEILING-EQ-TOTALS)."""
    return any(r.crap == ceiling_of(r.scope) for r in _exact_rows(drawn))


@given(scored_sets(), st.integers(1, 30), st.dictionaries(st.sampled_from(["a", "b"]), st.integers(1, 30)))
@pure
def test_totals_match_the_exact_sums(drawn, target, scope_targets):
    """functions, over their ceiling and the CRAP load at 2 dp, against the
    exact CRAP of each row's exact coverage. Away from an exact 2 dp tie of the
    load and from an exact CRAP equal to its ceiling, which rulings rows pin."""
    ceiling_of = lambda scope: model_score.ceiling(scope, target, scope_targets)  # noqa: E731
    edge = _load_tie(drawn) or _at_a_ceiling(drawn, ceiling_of)
    if edge:
        event("shape:crap-load-tie-or-crap-equals-ceiling")
    assume(not edge)
    want = model_score.totals(_exact_rows(drawn), ceiling_of)
    got = _crapkit_totals(_rows_of(drawn), target, scope_targets)

    assert got == {"functions": want["functions"], "over_target": want["over_target"],
                   "crap_load": f"{want['crap_load']:.2f}"}


@given(scored_sets(), st.integers(1, 30))
@pure
def test_per_scope_totals_add_up_to_the_run(drawn, target):
    rows = _rows_of(drawn)
    whole = production.load("digest:totals")(rows, target=target)
    parts = production.load("digest:scope_totals")(rows, target=target).values()

    assert sum(p.functions for p in parts) == whole.functions
    assert sum(p.over_target for p in parts) == whole.over_target


@given(scored_sets(), st.randoms(use_true_random=False))
@pure
def test_row_order_never_moves_the_totals(drawn, rnd):
    """Away from an exact 2 dp tie of the load, which SM-LOAD-ORDER pins."""
    if _load_tie(drawn):
        event("shape:crap-load-tie")
    assume(not _load_tie(drawn))
    rows = _rows_of(drawn)
    shuffled = list(rows)
    rnd.shuffle(shuffled)

    assert _crapkit_totals(shuffled, 6, {}) == _crapkit_totals(rows, 6, {})


# Found by a 20,000-example nightly run on Python 3.11: eleven rows whose exact
# CRAP load is the 2 dp tie 507.625, (ccn, twelfths covered) in this order.
LOAD_TIE_ROWS = [("a", 3, 7), ("a", 4, 7), ("a", 8, 5), ("a", 22, 3), ("a", 25, 8), ("a", 10, 4),
                 ("a", 13, 4), ("a", 25, 8), ("a", 3, 8), ("a", 5, 5), ("a", 10, 4)]
# The builtin sum adds left to right on 3.11; 3.12 made it compensated, which
# gives the correctly rounded sum here. The defect is open on 3.11 only.
LOAD_ORDER = "SM-LOAD-ORDER" if sys.version_info < (3, 12) else "SM-LOAD-ORDER-312"


@rulings.applies(LOAD_ORDER)
def test_the_crap_load_of_one_run_never_follows_its_row_order():
    """The rows in their drawn order and sorted by CRAP print one load: the
    correctly rounded sum of their doubles, 507.62500000000006, at 2 dp."""
    rows = _rows_of(LOAD_TIE_ROWS)
    orders = (rows, sorted(rows, key=lambda row: row.crap))
    printed = sorted({_crapkit_totals(order, 6, {})["crap_load"] for order in orders})
    correctly_rounded = exact.fixed(sum((Fraction(row.crap) for row in rows), Fraction(0)), 2)

    assert sum(r.crap for r in _exact_rows(LOAD_TIE_ROWS)) == Fraction(4061, 8)
    rulings.pin_ruling(LOAD_ORDER, crapkit="|".join(printed), oracle=correctly_rounded)


@rulings.applies("SM-CEILING-EQ-TOTALS")
def test_a_crap_exactly_at_the_ceiling_is_not_over_target():
    """README.md#grade-and-crap-load counts a function over its ceiling when
    crap > ceiling. CRAP(18, 2/3) is 30 exactly: at target = 30 it is not over,
    in the digest's totals and in a brief's file totals alike."""
    rows = _rows_of([("a", 18, 8)])
    digest = production.load("digest:totals")(rows, target=30).over_target
    packet = production.load("packet:file_totals")(rows, {}, 30)["over_target"]

    over = int(exact.crap(18, Fraction(2, 3)) > 30)

    rulings.pin_ruling("SM-CEILING-EQ-TOTALS", crapkit=f"{digest},{packet}", oracle=f"{over},{over}")


# --- digest ---------------------------------------------------------------------------------------

def _lane_sets(text: str) -> list[frozenset[str]]:
    return [frozenset(part.split(",")) for part in text.split("|")]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in DIGEST_ROWS],
                         ids=[row[0] for row in DIGEST_ROWS])
def test_digest_pair_hand_rows(given_, expected):
    runs = [{"id": n, "lanes": sorted(lanes)} for n, lanes in enumerate(_lane_sets(given_["lanes"]))]
    pair = production.load("digest:latest_comparable_pair")(runs)

    assert ("none" if pair is None else f"{pair[0]['id']},{pair[1]['id']}") == expected["pair"]


@given(st.lists(st.frozensets(st.sampled_from(["a", "b", "c"]), min_size=1), max_size=8))
@pure
def test_only_identical_lane_sets_pair(lane_sets):
    runs = [{"id": n, "lanes": sorted(lanes)} for n, lanes in enumerate(lane_sets)]
    pair = production.load("digest:latest_comparable_pair")(runs)
    want = model_score.comparable_pair(lane_sets)

    assert (None if pair is None else (pair[0]["id"], pair[1]["id"])) == want


def _digest(prev, cur, ceiling: int = 6):
    return production.load("digest:build_digest")(prev, cur, ceiling_of=lambda scope: ceiling)


@given(scored_sets())
@pure
def test_an_unchanged_run_digests_to_silence(drawn):
    rows = _rows_of(drawn)

    assert _digest(rows, list(rows)).quiet


@given(scored_sets(), st.integers(0, 39), st.integers(2, 400))
@pure
def test_a_function_that_moves_breaks_the_silence(drawn, pick, hundredths):
    """A function whose CRAP rises by 0.02 or more is news (README.md:805)."""
    rows = _rows_of(drawn)
    moved = rows[pick % len(rows)]
    cur = [r._replace(crap=r.crap + hundredths / 100) if r is moved else r for r in rows]

    assert not _digest(rows, cur).quiet


def test_digest_is_quiet_under_a_hundredth():
    """SM-DIGEST-001: README.md:805 says silent when nothing changed; one CRAP
    moving 0.00004, with the load, average and share over unchanged at the
    places they print, reads as silence."""
    before = [_row("a", "f( )", 10.0), _row("a", "g( )", 3.0)]
    after = [_row("a", "f( )", 10.00004), _row("a", "g( )", 3.0)]

    rulings.pin_ruling("SM-DIGEST-001", crapkit="quiet" if _digest(before, after).quiet else "speaks",
                       oracle="speaks")


# --- the measured corpus: summary and trend against a cold recompute -------------------------------

def _config(root) -> dict:
    return tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8"))


def _ceiling_of(root):
    cfg = _config(root)
    target = cfg.get("crapkit", {}).get("target", 6)
    own = {scope["name"]: scope.get("target") for scope in cfg.get("scope", [])}
    return lambda scope: model_score.ceiling(scope, target, own)


def _scored(corpus) -> list[dict]:
    return surfaces.read_tsv(corpus.outputs.joinpath("scored.tsv").read_text(encoding="utf-8"))[1]


def _summary_model(rows: list[dict], ceiling_of) -> dict:
    as_model = [model_score.Row(r["scope"], r["path"], r["long_name"], int(r["start"]),
                                int(r["ccn"]), Fraction(float(r["crap"])), r["flag"]) for r in rows]
    counts = model_score.flag_counts(as_model)
    totals = model_score.totals(as_model, ceiling_of)
    return {"functions": totals["functions"], "measured": counts["measured"],
            "untested": counts["untested"], "excluded": counts["excluded"],
            "no_lane": counts["no-lane"], "cc_only": counts["cc-only"],
            "over_target": totals["over_target"], "grade": totals["grade"],
            "crap_load": float(totals["crap_load"])}


@pytest.mark.process
def test_the_coverage_summary_is_the_scored_rows_counted(scored_corpus):
    summary = drive.Result(("coverage",), 0, scored_corpus.output("coverage.json"), "").json()
    want = _summary_model(_scored(scored_corpus), _ceiling_of(scored_corpus.root))

    assert {key: summary[key] for key in want} == want
    flags = ("measured", "untested", "excluded", "no_lane", "cc_only")
    assert sum(summary[key] for key in flags) == summary["functions"]


def _cold_totals(corpus, run_id: int) -> dict:
    stored = drive.Driver(corpus.root).store(
        "SELECT i.scope, f.crap FROM functions f JOIN identities i ON i.id = f.identity_id "
        "WHERE f.run_id = ? AND f.crap IS NOT NULL", (run_id,))
    rows = [model_score.Row(r["scope"], "", "", 0, 0, Fraction(r["crap"])) for r in stored]
    totals = model_score.totals(rows, _ceiling_of(corpus.root))
    return {"functions": totals["functions"], "over_target": totals["over_target"],
            "crap_load": float(totals["crap_load"])}


@pytest.mark.process
def test_every_trend_run_is_its_stored_rows_recomputed(scored_corpus):
    trend = drive.Result(("trend",), 0, scored_corpus.output("trend.json"), "").json()

    assert trend["runs"]
    for run in trend["runs"]:
        assert {key: run[key] for key in ("functions", "over_target", "crap_load")} == _cold_totals(
            scored_corpus, run["run_id"])


@pytest.mark.parametrize("given_,expected", [row[1:] for row in SUMMARY_ROWS],
                         ids=[row[0] for row in SUMMARY_ROWS])
def test_summary_hand_rows(given_, expected):
    rows = [model_score.Row("s", "p", f"f{n}", n, 1, Fraction(1), flag)
            for n, flag in enumerate(given_["flags"].split(","))]
    counts = model_score.flag_counts(rows)

    assert {**{key: str(value) for key, value in counts.items()}, "functions": str(len(rows))} == expected


# --- CLI scenarios over several runs ---------------------------------------------------------------

TWO_SCOPES = cli_repo.Layout(modules=(
    cli_repo.Module("a", "src/a/mod.py", (cli_repo.Fn("hot", 5, 2), cli_repo.Fn("cool", 2, 2))),
    cli_repo.Module("b", "src/b/mod.py", (cli_repo.Fn("wide", 8, 14),))),
    scope_targets={"b": 12})


def _cli(make_repo, layout=TWO_SCOPES):
    built = make_repo(cli_repo.spec(layout))
    return drive.Driver(built.root, date_now=repos.EPOCH + 86_400)


@pytest.mark.nightly
@pytest.mark.process
def test_digest_refuses_mismatched_lane_sets(make_repo):
    """R05: a `--lane a` run leaves scope b unmeasured; the digest never pairs it
    with the full run before it, so no phantom regression prints."""
    cli = _cli(make_repo)
    assert cli.run("coverage").code == 0
    assert cli.run("coverage", "--lane", "a").code == 0
    digest = cli.run("digest")

    assert "regressed" not in digest.stdout, digest.stdout


@pytest.mark.nightly
@pytest.mark.process
def test_trend_after_prune_equals_cold(make_repo):
    """R81: after `runs prune --keep 1`, trend answers for each run it kept as it
    did before the prune, and no cached rollup row outlives its run (read with
    sqlite3): a rollup that survives keeps answering for a run the store no
    longer holds."""
    cli = _cli(make_repo)
    for _ in range(3):
        assert cli.run("coverage").code == 0
    before = _trend_by_run(cli)
    assert cli.run("runs", "prune", "--keep", "1").code == 0
    after = _trend_by_run(cli)
    held = _ids(cli.store("SELECT id AS run_id FROM runs"))

    assert {run_id: before[run_id] for run_id in after} == after
    assert set(after) <= held
    assert _ids(cli.store("SELECT DISTINCT run_id FROM run_rollup")) <= held


def _trend_by_run(cli) -> dict[int, dict]:
    return {run["run_id"]: run for run in cli.json("trend")["runs"]}


def _ids(rows: list[dict]) -> set[int]:
    return {row["run_id"] for row in rows}


@pytest.mark.nightly
@pytest.mark.process
def test_digest_trend_and_summary_agree_per_scope_ceiling(make_repo):
    """R96: scope b sets target = 12, so its fully covered ccn-8 function (CRAP
    8) is under its ceiling though over the repo's 6, while scope a's ccn-5 at
    cov 1/4 (CRAP 15.546875) is over 6: every summary counts each row against
    its own scope's ceiling."""
    cli = _cli(make_repo)
    summary = cli.json("coverage")
    trend = cli.json("trend")["runs"][-1]
    want = {"a": 1, "b": 0}

    assert {scope: block["over_target"] for scope, block in summary["by_scope"].items()} == want
    assert {scope: block["over_target"] for scope, block in trend["by_scope"].items()} == want
    assert summary["over_target"] == trend["over_target"] == 1


GROWN = cli_repo.Layout(modules=(
    TWO_SCOPES.modules[0],
    cli_repo.Module("b", "src/b/mod.py", (cli_repo.Fn("wide", 8, 14), cli_repo.Fn("tangled", 8, 0)))),
    scope_targets={"b": 12})
DIGEST_OVER = re.compile(r"over (?:target|ceiling) (\d+) -> (\d+)")


@pytest.mark.nightly
@pytest.mark.process
def test_the_digest_counts_each_scope_against_its_own_ceiling(make_repo):
    """R96: between two full runs scope b gains `tangled` (ccn 8, no branch
    taken: CRAP 72, over b's ceiling of 12). Counted per scope, over target
    goes 1 -> 2 (a's `hot` at CRAP 15.55 both times; b's `wide` at CRAP 8 is
    under 12), and the digest's line says what trend says."""
    cli = _cli(make_repo)
    assert cli.run("coverage").code == 0
    for path, text in cli_repo.files(GROWN).items():
        (cli.root / path).write_bytes(text.encode("utf-8"))
    assert cli.run("coverage").code == 0
    trend = [run["over_target"] for run in cli.json("trend")["runs"]][-2:]
    said = DIGEST_OVER.search(cli.run("digest").stdout)

    assert trend == [1, 2]
    assert said is not None and [int(said[1]), int(said[2])] == trend


def _summary_without_run_identity(cli) -> dict:
    """The coverage summary less the fields that name the run, not its numbers."""
    summary = cli.json("coverage")
    return {key: value for key, value in summary.items() if key not in ("commit", "db")}


def _scored_rows(cli) -> dict:
    return {key: {k: v for k, v in row.items() if k not in ("run_id", "id")}
            for key, row in surfaces.from_store(cli.root / drive.STORE, 1).items()}


@pytest.mark.nightly
@pytest.mark.process
def test_parallel_lanes_score_what_serial_lanes_score(make_repo):
    """max_parallel_lanes = 3 runs the three lanes at once and 1 runs them in
    turn: the summary and every stored row come out the same."""
    serial = cli_repo.driver(make_repo, dataclasses.replace(cli_repo.SURFACES, parallel_lanes=1))
    parallel = cli_repo.driver(make_repo, dataclasses.replace(cli_repo.SURFACES, parallel_lanes=3))

    assert _summary_without_run_identity(serial) == _summary_without_run_identity(parallel)
    assert _scored_rows(serial) == _scored_rows(parallel)
