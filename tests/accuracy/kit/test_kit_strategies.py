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


def test_an_example_decorated_test_runs_every_shape():
    """One test collects and checks: under xdist a second test that read what
    the first collected could land on another worker and see nothing."""
    seen = []

    @strategies.examples("coverage_pair")
    @given(strategies.coverage_pair())
    @pure
    def collect(pair):
        seen.append(pair)

    collect()

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


def _without_twin(paths: list[str]) -> list[str]:
    """The plain list: every path but a trailing swapcase twin of the first."""
    twinned = len(paths) > 1 and paths[-1] == paths[0].swapcase()
    return paths[:-1] if twinned else paths


def _segments(paths: list[str]) -> list[str]:
    return [part for path in paths for part in path.split("/")]


@given(strategies.fs_paths(case_twins=True))
@pure
def test_filesystem_paths_are_creatable_everywhere_and_a_twin_differs_only_in_case(paths):
    """The twin variant is the plain list plus, when the first path has case, its
    swapcase twin; one test checks both, since 20,000 nested-list draws take minutes."""
    plain = _without_twin(paths)
    assert len({path.casefold() for path in plain}) == len(plain)
    assert len({path.casefold() for path in paths}) in (len(paths), len(paths) - 1)
    for segment in _segments(paths):
        assert not set(segment) & strategies.WINDOWS_INVALID
        assert segment.split(".", 1)[0].upper() not in strategies.RESERVED


def test_draws_are_counted_for_the_run_log():
    before = sum(strategies.EVENTS.values())

    find(strategies.marks(), lambda value: not math.isfinite(value), settings=pure)

    assert sum(strategies.EVENTS.values()) > before
