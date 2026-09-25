"""Go ccn, cognitive and nesting against gocyclo, gocognit and revive.

The three tools live in the accuracy image (oracles/go_adapters.py), so this
runs on the Linux nightly cell, over the Go probe files and the full corpus's
cobra member. Each function tree-sitter lists is joined to crapkit's row and
the tool's answer by path and start line. A function a known crapkit defect
shape covers is set aside for that column (ts_defect_shapes), as in the
tree-sitter differential.

Where a tool counts a construct differently from crapkit's documented reading,
a named transform turns the tool's raw value into crapkit's, or the function
is set aside for that tool, and each such rule is a rulings row pinned below by
a hand case with the tool's raw value and crapkit's.
"""
import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import go_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LITERAL = "func_literal"
# The tools list every named Go function and no func literal.
LISTED = tooldiff.listed_except({LITERAL})


# --- the transforms and set-asides, each a rulings row ------------------------------------------

def literal_decisions(fn, context) -> int:
    """AO-GOCYCLO-FUNC-LITERAL: gocyclo counts each func literal's decisions in the
    function that holds it; crapkit rows the literal on its own."""
    return sum(counters.ccn(lit, context.spec, context.data) - 1
               for lit in tooldiff.below(fn, {LITERAL}))


def _plain_else(block) -> bool:
    parent = block.parent
    return (block.type == "block" and parent is not None and parent.type == "if_statement"
            and parent.child_by_field_name("alternative") == block)


def _else_if(node) -> bool:
    return node.parent is not None and node.parent.type == "if_statement" and \
        node.parent.child_by_field_name("alternative") == node


def _nesting_weighted(node, spec) -> bool:
    if node.type in spec.ifs:
        return not _else_if(node)
    return node.type in spec.loops | spec.switches


def _else_blocks_above(node, fn) -> int:
    count, parent = 0, node.parent
    while parent is not None and parent != fn:
        count += _plain_else(parent)
        parent = parent.parent
    return count


def else_nesting(fn, context) -> int:
    """AO-GOCOGNIT-ELSE-NESTING: gocognit walks a plain else block without raising
    the nesting level, so each nesting-weighted structure inside one scores one
    less per such else above it than the Sonar paper's B3 gives."""
    return sum(_else_blocks_above(node, fn) for node in counters.own_nodes(fn, context.spec)
               if _nesting_weighted(node, context.spec))


def _has_range(fn, context) -> bool:
    return any(kid.type == "range_clause" for loop in tooldiff.below(fn, {"for_statement"})
               for kid in loop.children)


def _has_else(fn, context) -> bool:
    return any(_plain_else(node) or _else_if(node)
               for node in tooldiff.below(fn, {"block", "if_statement"}))


TOOLS = {
    "gocyclo": Tool("ccn_std", lambda root, paths, spans: go_adapters.gocyclo(root, paths),
                    transforms={"AO-GOCYCLO-FUNC-LITERAL": lambda fn, c: -literal_decisions(fn, c)}),
    "gocognit": Tool("cognitive", lambda root, paths, spans: go_adapters.gocognit(root, paths),
                     transforms={"AO-GOCOGNIT-ELSE-NESTING": else_nesting},
                     set_aside={"AO-GOCOGNIT-FUNC-LITERAL": tooldiff.holds({LITERAL})}),
    "revive": Tool("nesting", go_adapters.revive_depths,
                   set_aside={"AO-REVIVE-ELSE": _has_else, "AO-REVIVE-RANGE": _has_range}),
}


@pytest.fixture(scope="module")
def go_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "go")
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("go-probes"))


@pytest.fixture(scope="module")
def go_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "cobra", (".go",))
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("go-corpus"))


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_go_probes_match_the_go_tools(name, go_probes, oracle):
    oracle(name)
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, go_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_cobra_matches_the_go_tools(name, go_corpus, oracle):
    oracle(name)
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, go_corpus)

    assert outcome.problems == []
    assert outcome.compared > 50


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

LIT = ("package p\n\nfunc F(a []int) int {\n\tg := func(b int) int {\n\t\tif b > 0 {\n"
       "\t\t\treturn 1\n\t\t}\n\t\treturn 0\n\t}\n\tif len(a) > 0 {\n\t\treturn g(a[0])\n"
       "\t}\n\treturn 0\n}\n")
ELSE = ("package p\n\nfunc F(a bool, n int) int {\n\tif a {\n\t\treturn 1\n\t} else {\n"
        "\t\tfor i := 0; i < n; i++ {\n\t\t\tif i > 2 {\n\t\t\t\treturn i\n\t\t\t}\n\t\t}\n"
        "\t}\n\treturn 0\n}\n")
RANGE = ("package p\n\nfunc F(a []int) int {\n\tfor _, x := range a {\n\t\tif x > 0 {\n"
         "\t\t\treturn x\n\t\t}\n\t}\n\treturn 0\n}\n")
# ruling id -> (tool, source); crapkit's value and the tool's are read, the rule applied.
HAND = {
    "AO-GOCYCLO-FUNC-LITERAL": ("gocyclo", LIT),
    "AO-GOCOGNIT-ELSE-NESTING": ("gocognit", ELSE),
    "AO-GOCOGNIT-FUNC-LITERAL": ("gocognit", LIT),
    "AO-REVIVE-ELSE": ("revive", ELSE),
    "AO-REVIVE-RANGE": ("revive", RANGE),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"cases/{ruling}.go": source.encode() for ruling, (_, source) in HAND.items()}
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("go-hand"))


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_go_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _ = HAND[ruling_id]
    oracle(name)
    tool = TOOLS[name]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand, f"cases/{ruling_id}.go", 3)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_go_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted(HAND)
