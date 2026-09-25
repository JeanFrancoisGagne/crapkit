"""Java ccn, cognitive and nesting against Checkstyle and PMD 7.

Both tools live in the accuracy image (oracles/checkstyle_pmd_adapter.py), so
this runs on the Linux nightly cell, over the Java probe files and the full
corpus's gson member. The join, the set-asides for known crapkit defect shapes
and the transforms follow analysis_tooldiff.

Checkstyle and PMD name a method by its first line (an annotation or
modifier), tree-sitter's function list by its name's line, so every answer is
moved to the name's line first. Where a tool counts a construct differently
from crapkit's documented reading, a named transform or set-aside covers it,
and each such rule is a rulings row pinned below by a hand case with the
tool's raw value and crapkit's.
"""
from functools import lru_cache
from pathlib import Path

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import checkstyle_pmd_adapter as adapter
from accuracy.analysis_oracles.oracles import go_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LISTED = tooldiff.listed_except(set())


def _name_lines(root: Path, paths: tuple) -> dict:
    """{(path, first line of the declaration): its name's line} for every function."""
    return {(path, fn.start_point[0] + 1): counters.start_line(fn)
            for path in paths for fn, _ in LISTED(path, (root / path).read_bytes())}


def _by_name_line(root: Path, paths: tuple, answers: dict) -> dict:
    moved = _name_lines(root, paths)
    return {(path, moved.get((path, line), line)): value for (path, line), value in answers.items()}


@lru_cache(maxsize=8)
def _checkstyle(root: Path, paths: tuple) -> tuple:
    return tuple(adapter.checkstyle(root, list(paths)))


def checkstyle_ccn(root, paths, spans) -> dict:
    found = {(path, line): value for path, line, check, value in _checkstyle(root, tuple(paths))
             if check == "CyclomaticComplexity"}
    return _by_name_line(root, tuple(paths), found)


def checkstyle_depth(root, paths, spans) -> dict:
    """The deepest nested if, for or try Checkstyle reports inside each function."""
    depths = dict.fromkeys(spans, 0)
    for path, line, check, value in _checkstyle(root, tuple(paths)):
        owner = go_adapters.innermost(spans, path, line)
        if check in adapter.DEPTH_CHECKS and owner is not None:
            depths[owner] = max(depths[owner], value)
    return depths


def pmd_cognitive(root, paths, spans) -> dict:
    """PMD's value, or 0 for a function under reportLevel 1."""
    found = _by_name_line(root, tuple(paths), adapter.pmd_cognitive(root, list(paths)))
    return {key: found.get(key, 0) for key in spans}


# --- the transforms and set-asides, each a rulings row ------------------------------------------

KINDS = {"if": {"if_statement"}, "for": {"for_statement", "enhanced_for_statement"},
         "try": {"try_statement", "try_with_resources_statement"}}
UNMEASURED = {"while_statement", "do_statement", "switch_expression", "lambda_expression",
              "class_body", "ternary_expression", "catch_clause", "synchronized_statement"}


def _kinds_held(fn) -> set:
    return {kind for kind, types in KINDS.items() if tooldiff.below(fn, types)}


def mixed_structures(fn, context) -> bool:
    """AO-CHECKSTYLE-ONE-KIND: Checkstyle measures if, for and try depth each on its
    own and no other structure's, so only functions of one kind compare."""
    return len(_kinds_held(fn)) > 1 or bool(tooldiff.below(fn, UNMEASURED))


def depth_base(fn, context) -> int:
    """AO-CHECKSTYLE-DEPTH-BASE: Checkstyle counts the structures around a nested one,
    0 for an if inside no other; the nesting column counts the if itself."""
    return int(bool(_kinds_held(fn)))


def _else_if(node) -> bool:
    parent = node.parent
    return node.type == "if_statement" and parent is not None and \
        parent.type == "if_statement" and parent.child_by_field_name("alternative") == node


STRUCTURES = {"if_statement", "for_statement", "enhanced_for_statement", "while_statement",
              "do_statement", "switch_expression", "catch_clause", "ternary_expression",
              "lambda_expression"}


def _nests_after(link) -> bool:
    """A structure in the branch after an else if, other than the chain's next link."""
    after = link.child_by_field_name("alternative")
    return after is not None and any(not _else_if(node) for node in
                                     [after, *tooldiff.below(after, STRUCTURES)]
                                     if node.type in STRUCTURES)


def else_if_nesting(fn, context) -> bool:
    """AO-PMD-ELSE-IF-NESTING: PMD nests each else if inside the one before it, so a
    structure in a later branch scores one more than the Sonar paper's flat chain."""
    return any(_nests_after(link) for link in tooldiff.below(fn, {"if_statement"})
               if _else_if(link))


TOOLS = {
    "checkstyle-ccn": Tool("ccn_std", checkstyle_ccn),
    "checkstyle-depth": Tool("nesting", checkstyle_depth,
                             transforms={"AO-CHECKSTYLE-DEPTH-BASE": depth_base},
                             set_aside={"AO-CHECKSTYLE-ONE-KIND": mixed_structures}),
    "pmd-cognitive": Tool("cognitive", pmd_cognitive,
                          set_aside={"AO-PMD-INNER-CLASS": tooldiff.holds({"class_body"}),
                                     "AO-PMD-ELSE-IF-NESTING": else_if_nesting}),
}
ORACLE = {"checkstyle-ccn": "checkstyle", "checkstyle-depth": "checkstyle",
          "pmd-cognitive": "pmd"}


@pytest.fixture(scope="module")
def java_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "java")
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("j-probes"))


@pytest.fixture(scope="module")
def java_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "gson", (".java",))
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("j-corpus"))


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_java_probes_match_the_java_tools(name, java_probes, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, java_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_gson_matches_the_java_tools(name, java_corpus, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, java_corpus)

    assert outcome.problems == []
    assert outcome.compared > 100


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

NESTED_IF = ("class Case {\n"
                "  int f(boolean a, boolean b) {\n"
                "    if (a) {\n"
                "      if (b) {\n"
                "        return 1;\n"
                "      }\n"
                "    }\n"
                "    return 0;\n"
                "  }\n" "}\n")
IF_IN_WHILE = ("class Case {\n"
                  "  int f(int n) {\n"
                  "    while (n > 0) {\n"
                  "      if (n == 3) {\n"
                  "        return 1;\n"
                  "      }\n"
                  "      n--;\n"
                  "    }\n"
                  "    return 0;\n"
                  "  }\n" "}\n")
INNER_CLASS = ("class Case {\n"
                  "  Runnable f(boolean a) {\n"
                  "    return new Runnable() {\n"
                  "      public void run() {\n"
                  "        int x = a ? 1 : 2;\n"
                  "      }\n"
                  "    };\n"
                  "  }\n" "}\n")
ELSE_IF = ("class Case {\n"
              "  int f(boolean a, boolean b, boolean c) {\n"
              "    if (a) {\n"
              "      return 1;\n"
              "    } else if (b) {\n"
              "      return 2;\n"
              "    } else {\n"
              "      if (c) {\n"
              "        return 3;\n"
              "      }\n"
              "    }\n"
              "    return 0;\n"
              "  }\n" "}\n")
# ruling id -> (tool, source); the case's method starts on line 2.
HAND = {
    "AO-CHECKSTYLE-DEPTH-BASE": ("checkstyle-depth", NESTED_IF),
    "AO-CHECKSTYLE-ONE-KIND": ("checkstyle-depth", IF_IN_WHILE),
    "AO-PMD-INNER-CLASS": ("pmd-cognitive", INNER_CLASS),
    "AO-PMD-ELSE-IF-NESTING": ("pmd-cognitive", ELSE_IF),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"{ruling}/Case.java": source.encode() for ruling, (_, source) in HAND.items()}
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("j-hand"))


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_java_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _ = HAND[ruling_id]
    oracle(ORACLE[name])
    tool = TOOLS[name]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand,
                                                     f"{ruling_id}/Case.java", 2)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_java_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted(HAND)
