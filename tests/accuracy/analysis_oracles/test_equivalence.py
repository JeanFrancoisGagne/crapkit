"""One shape written in every language reads the same numbers everywhere.

equivalence.tsv holds four shapes (straight, four ifs nested, three loops
nested, seven ifs side by side) and the ccn_std, ccn_mod, cognitive and
nesting each must read, worked from the McCabe text and the Sonar paper.
Every language directory under probes/ holds the shapes in its own syntax
(probes/<lang>/equivalence.*), named by the probe rows whose id starts with
`eq-`. A language that reads a shape another way fails here even when no
other test looks at that language; a difference crapkit records is the
language's rulings row, the same one its hand probe pins.

A Rust match of seven arms and the if/else-if chain doing the same work read
one ccn_std: the arm count of R14.
"""
import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_tables

pytestmark = pytest.mark.process
HERE = analysis_tables.HERE
METRICS = ("ccn_std", "ccn_mod", "cognitive", "nesting")


def _expected() -> dict[tuple[str, str], str]:
    return {(row["shape"], row["metric"]): row["expected"]
            for row in analysis_tables.tsv(analysis_tables.EQUIVALENCE)}


def _shape_probes() -> dict[str, dict[str, analysis_tables.Probe]]:
    """{lang: {shape: the probe row naming its function}}, one metric's row each."""
    found: dict[str, dict] = {}
    for probe in analysis_tables.probes():
        shape = probe.id.removeprefix("eq-").replace("-", "_")
        if probe.id.startswith("eq-") and probe.metric == "ccn_std":
            found.setdefault(probe.lang, {})[shape] = probe
    return found


RULINGS = {(probe.lang, probe.id, probe.metric): probe.ruling for probe in analysis_tables.probes()}


def _ruling(lang: str, shape: str, metric: str) -> str:
    """The rulings row the language's hand probe records for this shape and metric."""
    return RULINGS.get((lang, f"eq-{shape.replace('_', '-')}", metric), "")


SHAPES = sorted({shape for shape, _ in _expected()})
LANGS = sorted(_shape_probes())


def _cases() -> list:
    return [pytest.param(lang, metric, id=f"{lang}-{metric}",
                         marks=[mark for shape in SHAPES
                                for mark in analysis_tables.marks(_ruling(lang, shape, metric))][:1])
            for lang in LANGS for metric in METRICS]


def test_every_language_writes_every_shape():
    assert {lang: sorted(shapes) for lang, shapes in _shape_probes().items()
            if not set(SHAPES) <= set(shapes)} == {}
    assert len(LANGS) >= 15


def _actual(measured, probe, metric: str) -> str:
    rows = analysis_tables.lookup(measured, probe.path, probe.function)
    return str(rows[0][metric]) if len(rows) == 1 else f"{len(rows)} rows"


@pytest.mark.parametrize(("lang", "metric"), _cases())
def test_shape_scores_match_across_languages(lang, metric, probe_inventory):
    expected, shapes = _expected(), _shape_probes()[lang]
    for shape in SHAPES:
        actual = _actual(probe_inventory, shapes[shape], metric)
        analysis_tables.check(actual, expected[(shape, metric)], _ruling(lang, shape, metric))


def _ccn(measured, name: str) -> int:
    (row,) = measured.named("rs/equivalence.rs", name)
    return row["ccn_std"]


def test_match_equals_if_chain(probe_inventory):
    """Seven match arms and an if with six else ifs are eight paths each
    (NIST SP 500-235 sec. 4.1): the `_` arm is the default, like the final else."""
    assert _ccn(probe_inventory, "seven_arms") == _ccn(probe_inventory, "seven_ifs") == 8
