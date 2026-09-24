"""Every strategy reaches every shape it names, and each shape's literal carries it."""
from fractions import Fraction
import math

from hypothesis import find, given
import pytest

from accuracy.kit import exact, strategies
from accuracy.kit.settings import pure

# The kit's machinery runs inside every packet's push tests; its own
# process-heavy self-tests run nightly, keeping the push budget for packets.
pytestmark = pytest.mark.nightly

STRATEGIES = {"ccn": strategies.ccn, "coverage_pair": strategies.coverage_pair,
              "spans": strategies.spans, "run_kinds": strategies.run_kinds,
              "stamps": strategies.stamps, "marks": strategies.marks,
              "path_text": strategies.path_text}
SHAPES = [(name, shape) for name, shapes in strategies.REQUIRED.items() for shape in shapes]


@pytest.mark.parametrize("name, shape", SHAPES, ids=[f"{n}-{s}" for n, s in SHAPES])
def test_each_required_literal_has_its_shape(name, shape):
    value = strategies.REQUIRED[name][shape]

    assert shape in strategies.CLASSIFIERS[name](value)


@pytest.mark.parametrize("name, shape", SHAPES, ids=[f"{n}-{s}" for n, s in SHAPES])
def test_each_strategy_draws_each_of_its_shapes(name, shape):
    """find() searches the strategy itself, not the literal, for the shape."""
    classify = strategies.CLASSIFIERS[name]

    found = find(STRATEGIES[name](), lambda value: shape in classify(value),
                 settings=pure)

    assert shape in classify(found)


@strategies.examples("coverage_pair")
@given(strategies.coverage_pair())
@pure
def test_an_example_decorated_test_runs_every_shape(pair):
    seen.append(pair)


seen = []


def test_the_literals_ran_first():
    literals = set(map(repr, strategies.REQUIRED["coverage_pair"].values()))

    assert literals <= set(map(repr, seen))


@given(strategies.ccn())
@pure
def test_ccn_stays_between_1_and_5000(value):
    assert 1 <= value <= strategies.CCN_MAX


@given(strategies.crap_case())
@pure
def test_a_crap_case_is_kit_exact(case):
    assert case.crap == exact.crap(case.ccn, Fraction(case.covered, case.total))
    assert case.ccn <= case.crap <= case.ccn * case.ccn + case.ccn


@given(strategies.marks())
@pure
def test_a_tie_is_exactly_half_a_ten_thousandth_past_a_4dp_value(value):
    if strategies._tie(value):
        assert (Fraction(value) * 10_000).denominator == 2


@given(strategies.fs_paths(case_twins=False))
@pure
def test_filesystem_paths_are_creatable_everywhere(paths):
    assert len({path.casefold() for path in paths}) == len(paths)
    for segment in (part for path in paths for part in path.split("/")):
        assert not set(segment) & strategies.WINDOWS_INVALID
        assert segment.split(".", 1)[0].upper() not in strategies.RESERVED


@given(strategies.fs_paths(case_twins=True))
@pure
def test_a_case_twin_differs_only_in_case(paths):
    folded = [path.casefold() for path in paths]
    assert len(set(folded)) in (len(paths), len(paths) - 1)


def test_draws_are_counted_for_the_run_log():
    before = sum(strategies.EVENTS.values())

    find(strategies.marks(), lambda value: not math.isfinite(value), settings=pure)

    assert sum(strategies.EVENTS.values()) > before
