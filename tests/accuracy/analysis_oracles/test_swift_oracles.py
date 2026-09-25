"""Swift ccn against SwiftLint's cyclomatic_complexity rule.

SwiftLint lives in the accuracy image (oracles/swiftlint_adapter.py), so this
runs on the Linux nightly cell, over the Swift probe files. Each function
tree-sitter lists is joined to crapkit's row and SwiftLint's answer by path and
start line. A function a known crapkit defect shape covers is set aside for
that column (ts_defect_shapes).

SwiftLint counts from 0, counts a switch's default and leaves out `&&`, `||`
and `?:`. The McCabe text starts at 1, excludes the default and counts a
short-circuit operator; crapkit's Swift reader also counts `?:`, as the
tree-sitter counter does. Each difference is a named transform with its
own rulings row, and the booleans are counted from tree-sitter tokens. A hand
case pins each rule with SwiftLint's raw value and crapkit's.
"""
import pytest

from accuracy.analysis_oracles import analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import swiftlint_adapter
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LISTED = tooldiff.listed_except(set())


def _own(fn, context, kinds) -> list:
    return [node for node in counters.own_nodes(fn, context.spec) if node.type in kinds]


def logical_operators(fn, context) -> int:
    """AO-SWIFTLINT-LOGICAL: each `&&` and `||` the function owns."""
    return sum(counters.logical_operators(node, context.spec)
               for node in counters.own_nodes(fn, context.spec))


def defaults(fn, context) -> int:
    """AO-SWIFTLINT-DEFAULT: SwiftLint counts a switch's default entry."""
    return -sum(counters.is_default(entry, context.data)
                for entry in _own(fn, context, {"switch_entry"}))


TOOLS = {
    "swiftlint": Tool("ccn_std", swiftlint_adapter.complexity, transforms={
        "AO-SWIFTLINT-BASE": lambda fn, context: 1,
        "AO-SWIFTLINT-LOGICAL": logical_operators,
        "AO-SWIFTLINT-TERNARY": lambda fn, c: len(_own(fn, c, {"ternary_expression"})),
        "AO-SWIFTLINT-DEFAULT": defaults,
    }),
}


@pytest.fixture(scope="module")
def swift_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "swift")
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("sw-probes"))


def test_swift_probes_match_swiftlint(swift_probes, oracle):
    oracle("swiftlint")
    outcome = tooldiff.differential("swiftlint", TOOLS["swiftlint"], LISTED, swift_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


# --- each rule's hand case: SwiftLint's raw value and crapkit's -----------------------------------

HAND = {
    "AO-SWIFTLINT-BASE": "func f() -> Int {\n    return 0\n}\n",
    "AO-SWIFTLINT-LOGICAL": ("func f(a: Bool, b: Bool, c: Bool) -> Int {\n    if a && b || c {\n"
                             "        return 1\n    }\n    return 0\n}\n"),
    "AO-SWIFTLINT-TERNARY": "func f(a: Int) -> Int {\n    return a > 0 ? 1 : 2\n}\n",
    "AO-SWIFTLINT-DEFAULT": ("func f(k: Int) -> Int {\n    switch k {\n    case 1:\n        return 10\n"
                             "    case 2:\n        return 20\n    default:\n        return 0\n    }\n}\n"),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"cases/{ruling}.swift": source.encode() for ruling, source in HAND.items()}
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("sw-hand"))


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_swift_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    oracle("swiftlint")
    tool = TOOLS["swiftlint"]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand,
                                                     f"cases/{ruling_id}.swift", 1)

    assert tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_swift_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted(HAND)
