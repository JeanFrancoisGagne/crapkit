"""Every strategy reaches every shape it names, and each shape's literal carries it."""
from collections import Counter
from contextlib import contextmanager, nullcontext
from fractions import Fraction
import math

from hypothesis import find, given
from hypothesis.errors import NoSuchExample
import pytest

from accuracy.kit import exact, strategies
from accuracy.kit.settings import profile, pure

# The kit's machinery runs inside every packet's push tests; its own
# process-heavy self-tests run nightly, keeping the push budget for packets.
pytestmark = pytest.mark.nightly

STRATEGIES = {"ccn": strategies.ccn, "coverage_pair": strategies.coverage_pair,
              "spans": strategies.spans, "run_kinds": strategies.run_kinds,
              "stamps": strategies.stamps, "marks": strategies.marks,
              "nonfinite_marks": strategies.nonfinite_marks, "path_text": strategies.path_text}
SHAPES = [(name, shape) for name, shapes in strategies.REQUIRED.items() for shape in shapes]
# Strategies whose draws only a test that hands them to crapkit may add to the
# run log. The events floor counts R32 to show crapkit's ratchet reader refused
# the text, so the kit's own searches of nonfinite_marks() leave the log alone.
CRAPKIT_FED = {"nonfinite_marks"}


@contextmanager
def _unlogged():
    """Draws inside reach no crapkit, so the run log's events stay as they were."""
    logged = Counter(strategies.EVENTS)
    try:
        yield
    finally:
        strategies.EVENTS.clear()
        strategies.EVENTS.update(logged)


def _search(name: str, shape: str):
    """find() over the strategy itself, not the literal, for a value with the shape."""
    classify = strategies.CLASSIFIERS[name]
    with _unlogged() if name in CRAPKIT_FED else nullcontext():
        return find(STRATEGIES[name](), lambda value: shape in classify(value), settings=pure)


@pytest.mark.parametrize("name, shape", SHAPES, ids=[f"{n}-{s}" for n, s in SHAPES])
def test_each_required_literal_has_its_shape(name, shape):
    value = strategies.REQUIRED[name][shape]

    assert shape in strategies.CLASSIFIERS[name](value)


@pytest.mark.parametrize("name, shape", SHAPES, ids=[f"{n}-{s}" for n, s in SHAPES])
def test_each_strategy_draws_each_of_its_shapes(name, shape):
    assert shape in strategies.CLASSIFIERS[name](_search(name, shape))


def test_a_search_of_a_crapkit_fed_strategy_logs_nothing():
    """Every draw of nonfinite_marks() is R32: one search for it, shrinking
    included, added about 100 R32 events with no crapkit call, twice the floor."""
    logged = Counter(strategies.EVENTS)

    _search("nonfinite_marks", "R32")

    assert strategies.EVENTS == logged


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


# accuracy.yml's nightly summary runs `run.py events --min 50` on the merged
# Linux 3.12 receipt.
FLOOR = 50
# The ratchet reader test is a process test: on that Linux leg it draws
# nonfinite_marks() this many times a run, not a @pure run's 20,000.
READER_NIGHTLY = profile("process", "nightly", "linux")


def _drawn(name: str, chosen=pure) -> Counter:
    """How many of one run's values carry each shape of a strategy, at `chosen`
    settings. No draw of it reaches crapkit, so the run log's events stay as
    they were."""
    drawn: Counter = Counter()

    @given(STRATEGIES[name]())
    @chosen
    def draw(value):
        drawn.update(strategies.CLASSIFIERS[name](value))

    with _unlogged():
        draw()
    return drawn


def test_one_nightly_run_draws_each_broken_coverage_pair_twice_the_floor():
    """A shape drawn only as a sampled literal reaches a run once (kit.strategies
    says why): drawn from the six literals, a run held R27 twice."""
    drawn = _drawn("coverage_pair")

    short = {shape: drawn[shape] for shape in strategies.REQUIRED["coverage_pair"]
             if drawn[shape] < 2 * FLOOR}
    assert short == {}


def test_one_nightly_run_draws_each_mark_shape_twice_the_floor():
    drawn = _drawn("marks")

    short = {shape: drawn[shape] for shape in strategies.REQUIRED["marks"]
             if drawn[shape] < 2 * FLOOR}
    assert short == {}


def test_one_nightly_run_of_the_reader_test_draws_r32_twice_the_floor():
    """Drawn from three literals, R32 reached a run three times: nightly
    36980041697's Linux 3.12 receipt counted it 12 times in all."""
    drawn = _drawn("nonfinite_marks", READER_NIGHTLY)

    assert drawn["R32"] >= 2 * FLOOR


def test_marks_draws_no_r32():
    """The events floor counts R32 to show crapkit's ratchet reader refused text
    that is no finite number. marks() feeds only kit self-tests, which call no
    crapkit, so an R32 it drew would count toward the floor with the reader
    test deleted."""
    assert _drawn("marks")["R32"] == 0


def test_a_measured_run_adds_nothing_to_the_run_log():
    """The events floor reads the counts the run log hands run.py. _drawn
    only measures a strategy, so its 20,000 nightly draws stay out of them."""
    logged = Counter(strategies.EVENTS)

    _drawn("ccn")

    assert strategies.EVENTS == logged


@pytest.mark.parametrize("shape", sorted(strategies._PAIR_FAMILIES))
def test_each_broken_family_draws_only_pairs_of_its_shape(shape):
    with pytest.raises(NoSuchExample):
        find(strategies._PAIR_FAMILIES[shape],
             lambda pair: shape not in strategies._pair_shapes(pair), settings=pure)


def test_every_nonfinite_mark_reads_as_no_finite_number():
    """Every spelling in the family, read in a loop: a search would check only
    some of them and add each draw to the run log's R32 events."""
    finite = [text for text in set(strategies._NONFINITE) if math.isfinite(float(text))]

    assert finite == []


def _kind(text: str) -> str:
    body = text.strip().lstrip("+-").lower()
    return body if body in ("nan", "inf", "infinity") else "overflow"


def test_each_kind_of_nonfinite_text_is_about_a_quarter_of_the_family():
    """Listed once each, infinity's 256 case mixes made 73% of the family and
    nan's 8 made 2%: a 200-draw run drew nan 0 to 7 times."""
    kinds = Counter(map(_kind, strategies._NONFINITE))

    shares = {kind: round(count / len(strategies._NONFINITE), 2) for kind, count in kinds.items()}
    assert set(shares) == set(strategies._KINDS)
    assert {kind: share for kind, share in shares.items() if not 0.2 <= share <= 0.3} == {}


def test_the_family_lists_nan_first():
    """A failing draw shrinks toward the head of the list sampled_from reads,
    so a failure that every spelling meets reports the plainest one."""
    assert strategies._NONFINITE[0] == "nan"


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
        assert segment.rstrip(" .") == segment


# Microsoft's "Naming Files, Paths, and Namespaces" reserves CON, PRN, AUX, NUL,
# COM0 to COM9, LPT0 to LPT9 and COM or LPT with a superscript 1, 2 or 3.
@pytest.mark.parametrize("segment, safe", [
    ("CON", "CON_"), ("nul", "nul_"), ("COM0", "COM0_"), ("lpt³", "lpt³_"),
    ("Com9", "Com9_"), ("console", "console"), ("COM10", "COM10"), ("LPT⁴", "LPT⁴"),
])
def test_a_windows_device_name_gets_a_suffix_and_nothing_else_changes(segment, safe):
    assert strategies._unreserved(segment) == safe


def test_draws_are_counted_for_the_run_log():
    before = sum(strategies.EVENTS.values())

    find(strategies.marks(), strategies._tie, settings=pure)

    assert sum(strategies.EVENTS.values()) > before
