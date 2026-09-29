"""Rescore overlay: fresh complexity over a baseline run's coverage, joined by name.

score.overlay_stale_coverage against README.md:800 through model_score.overlay,
with the CRAP each row gets checked in exact arithmetic.
"""
from __future__ import annotations

from fractions import Fraction
import math

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import exact, rulings
from accuracy.kit.settings import pure
from accuracy.score_model import cases, model_score, production

ULPS = 8
OVERLAY_ROWS = cases.hand("Rescore overlay")


def _baseline_row(path: str, name: str, start: int, end: int, ccn: int, cov: float):
    crap = production.load("score:crap")(ccn, cov)
    return production.scored_row("src", path, name, start, end, ccn, cov, "measured", crap, "ok")


def crapkit_overlay(fresh, baseline, lane_scopes=frozenset({"src"}), target: int = 6):
    return production.call("score:overlay_stale_coverage", list(fresh), list(baseline),
                           lane_scopes=set(lane_scopes), target=target)


def _covs(rows) -> dict[str, tuple[float, str]]:
    return {row.long_name: (row.cov, row.flag) for row in rows}


def _specs(text: str) -> list[list[str]]:
    return [item.split(":") for item in text.split(",")]


def _hand_fresh(path: str, text: str) -> list:
    """name:start:end items; occurrence counts the functions before it on its start line."""
    specs, rows = _specs(text), []
    for number, (name, start, end) in enumerate(specs):
        before = sum(1 for other in specs[:number] if other[1] == start)
        rows.append(production.inventory_row("src", path, f"{name}( )", int(start), int(end), 3,
                                             occurrence=before + 1))
    return rows


def _hand_expected(expected: dict) -> dict[str, tuple[float, str]]:
    return {f"{name}( )": (float(cases.fraction(value.split(":")[0])), value.split(":")[1])
            for name, value in expected.items()}


@pytest.mark.parametrize("given_,expected", [row[1:] for row in OVERLAY_ROWS],
                         ids=[row[0] for row in OVERLAY_ROWS])
def test_overlay_hand_rows(given_, expected):
    """file=PATH baseline=name:start:end:cov,... fresh=name:start:end,..."""
    path = given_["file"]
    baseline = [_baseline_row(path, f"{name}( )", int(start), int(end), 3,
                              float(cases.fraction(cov)))
                for name, start, end, cov in _specs(given_["baseline"])]

    assert _covs(crapkit_overlay(_hand_fresh(path, given_["fresh"]), baseline)) ==         _hand_expected(expected)


def test_renamed_function_gets_no_neighbour_coverage():
    """R04: h, nested in f, is renamed g. A name the baseline never measured
    joins nothing, even inside a function whose own name still joins
    (README.md:800: joined by name)."""
    baseline = [_baseline_row("src/a.ts", "f( )", 1, 20, 4, 0.8),
                _baseline_row("src/a.ts", "h( )", 5, 8, 2, 0.5)]
    fresh = [production.inventory_row("src", "src/a.ts", "f( )", 1, 20, 4),
             production.inventory_row("src", "src/a.ts", "g( )", 5, 8, 2)]

    assert _covs(crapkit_overlay(fresh, baseline)) == {"f( )": (0.8, "measured"),
                                                        "g( )": (0.0, "untested")}


def _shifted(cov_f: float, cov_g: float) -> dict[str, tuple[float, str]]:
    """Nine lines inserted above both functions move f onto g's old span."""
    baseline = [_baseline_row("src/a.ts", "f( )", 1, 9, 4, cov_f),
                _baseline_row("src/a.ts", "g( )", 11, 19, 4, cov_g)]
    fresh = [production.inventory_row("src", "src/a.ts", "f( )", 10, 18, 4),
             production.inventory_row("src", "src/a.ts", "g( )", 20, 28, 4)]
    return _covs(crapkit_overlay(fresh, baseline))


def test_shifted_functions_keep_their_own_numbers():
    """R05: each keeps its own baseline cov, never the one its new span held."""
    assert _shifted(0.75, 0.25) == {"f( )": (0.75, "measured"), "g( )": (0.25, "measured")}


def test_the_overlay_carries_the_baseline_cov_bit_for_bit():
    """2/3 stays the double 2/3, never 0.667: the overlay copies the stored cov."""
    assert _shifted(2 / 3, 0.25) == {"f( )": (2 / 3, "measured"), "g( )": (0.25, "measured")}


def test_a_shared_span_reads_untested():
    """README.md:800: a function on a span another one shares scores untested."""
    baseline = [_baseline_row("src/a.ts", "f( )", 3, 3, 3, 1.0),
                _baseline_row("src/a.ts", "g( )", 3, 3, 3, 1.0)]
    fresh = [production.inventory_row("src", "src/a.ts", name, 3, 3, 3, occurrence=number)
             for number, name in enumerate(("f( )", "g( )"), start=1)]
    rows = crapkit_overlay(fresh, baseline)

    assert _covs(rows) == {"f( )": (0.0, "untested"), "g( )": (0.0, "untested")}
    assert {row.remedy for row in rows} == {"split-lines"}


def test_twins_take_the_nearest_start():
    """SM-OVERLAY-TWIN: the docs say `joined by name`; crapkit picks the
    same-name baseline row nearest the fresh start."""
    baseline = [_baseline_row("src/a.ts", "h( )", 1, 9, 2, 0.25),
                _baseline_row("src/a.ts", "h( )", 20, 29, 2, 0.75)]
    fresh = [production.inventory_row("src", "src/a.ts", "h( )", 18, 27, 2)]
    (row,) = crapkit_overlay(fresh, baseline)
    want = model_score.overlay(model_score.Fresh("src", "src/a.ts", "h( )", 18, 27), [],
                               {("src/a.ts", "h( )"): [(1, 0.25), (20, 0.75)]}, {"src"})

    rulings.pin_ruling("SM-OVERLAY-TWIN", crapkit="the line-20 twin's cov" if row.cov == 0.75
                       else f"cov {row.cov}", oracle="unspecified")
    assert (row.cov, row.flag) == want


# --- the model and its metamorphic relations ---------------------------------------------------

NAMES = [f"fn{n}( x )" for n in range(6)]


@st.composite
def overlay_cases(draw):
    """One file: baseline functions at their spans with a cov each, and a fresh
    file where some kept their name, some were renamed, and all moved."""
    count = draw(st.integers(1, 6))
    starts = sorted(draw(st.lists(st.integers(1, 40), min_size=count, max_size=count, unique=True)))
    spans = [(start * 10, start * 10 + draw(st.integers(1, 8))) for start in starts]
    covs = [draw(st.integers(0, 12)) / 12 for _ in spans]
    shift = draw(st.integers(-5, 30))
    renamed = draw(st.lists(st.booleans(), min_size=count, max_size=count))
    ccns = draw(st.lists(st.integers(1, 12), min_size=count, max_size=count))
    return spans, covs, shift, renamed, ccns


def _build(case):
    spans, covs, shift, renamed, ccns = case
    baseline = [_baseline_row("src/a.ts", NAMES[i], s, e, ccns[i], covs[i])
                for i, (s, e) in enumerate(spans)]
    fresh = [model_score.Fresh("src", "src/a.ts", f"new{i}( x )" if renamed[i] else NAMES[i],
                               s + shift, e + shift) for i, (s, e) in enumerate(spans)]
    return baseline, fresh


def _inventory(fresh, ccns):
    return [production.inventory_row(f.scope, f.path, f.name, f.start, f.end, ccn)
            for f, ccn in zip(fresh, ccns)]


def _expected(fresh, baseline) -> dict[str, tuple[float, str]]:
    by_name = {}
    for row in baseline:
        by_name.setdefault((row.path, row.long_name), []).append((row.start, row.cov))
    return {f.name: model_score.overlay(f, fresh, by_name, {"src"}) for f in fresh}


@given(overlay_cases())
@pure
def test_overlay_matches_the_join_by_name(case):
    baseline, fresh = _build(case)
    rows = crapkit_overlay(_inventory(fresh, case[4]), baseline)

    assert _covs(rows) == _expected(fresh, baseline)


@given(overlay_cases())
@pure
def test_every_overlay_crap_is_the_exact_formula(case):
    baseline, fresh = _build(case)
    rows = crapkit_overlay(_inventory(fresh, case[4]), baseline)
    far = [row.long_name for row in rows
           if abs(Fraction(row.crap) - exact.crap(row.ccn, Fraction(row.cov)))
           > ULPS * Fraction(math.ulp(row.crap))]

    assert far == []


@given(overlay_cases(), st.integers(1, 50))
@pure
def test_moving_every_function_keeps_every_cov(case, more):
    baseline, fresh = _build(case)
    moved = [model_score.Fresh(f.scope, f.path, f.name, f.start + more, f.end + more) for f in fresh]

    assert _covs(crapkit_overlay(_inventory(moved, case[4]), baseline)) == _covs(
        crapkit_overlay(_inventory(fresh, case[4]), baseline))
