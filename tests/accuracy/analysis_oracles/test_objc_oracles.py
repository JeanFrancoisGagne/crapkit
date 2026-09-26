"""Objective-C and Objective-C++ against OCLint, clang-tidy and the tree-sitter
counters: the probes under probes/objc and probes/objcpp, and the full
corpus's AFNetworking member.

compile_commands() writes one entry per .m file (-x objective-c) and .mm file
(-x objective-c++) with -fblocks, which Apple's compilers turn on by default
and without which every block literal or block type is an error, and
-fobjc-exceptions for @try. Headers are written beside them and read through
their includers only. The accuracy image has no Apple SDK, so a file that
imports Foundation does not compile: every tool skips it, the skip is counted
in the run log, and each test asserts how many files compiled. All six
AFNetworking files import Foundation through their own headers, so none
compiles there; the probes compile without it (a root class, blocks, @try,
@synchronized, @autoreleasepool, for...in) and carry the clang-tool
differential. The clang tests run on the Linux cell, where the image keeps
the tools.

The C-family rules of test_c_family_oracles apply unchanged. The rules below
are where OCLint or clang-tidy read an Objective-C construct differently from
crapkit's documented reading; each is a rulings row pinned by a hand case with
the tool's raw value and crapkit's.

The tree-sitter corpus differential (analysis_tstests) reads AFNetworking as
it is. There tree-sitter-objc cannot parse `AF_API_AVAILABLE(...)` after a
property and turns all of AFURLSessionManager.m into one ERROR node.
expanded() applies the member's own definition of that macro one level
(AFCompatibilityMacros.h:26 and :32 define AF_API_AVAILABLE(...) as
API_AVAILABLE(__VA_ARGS__), and the same for AF_API_UNAVAILABLE), padded with
spaces so every byte keeps its line and column. crapkit reads both spellings
the same, row for row, and tree-sitter parses the expanded file, so its
functions join the differential.
"""
from functools import lru_cache
import json
from pathlib import Path
import re

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles import analysis_treesitter
from accuracy.analysis_oracles import test_c_family_oracles as cfam
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import clang_adapters as adapter
from accuracy.kit import rulings, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.process]
LINUX = pytest.mark.platform("linux")
LISTED = cfam.LISTED
LANGUAGES = {".m": "objective-c", ".mm": "objective-c++"}
STANDARD = {"objective-c": "-std=c11", "objective-c++": "-std=c++17"}
FLAGS = ["-fblocks", "-fobjc-exceptions", *adapter.QUIET]


def _entry(root: Path, path: str) -> dict:
    language = LANGUAGES[Path(path).suffix.lower()]
    file = str(root / path)
    return {"directory": str(root), "file": file,
            "arguments": ["clang", "-x", language, STANDARD[language], *FLAGS, "-c", file]}


def compile_commands(root: Path, paths: list) -> None:
    entries = [_entry(root, path) for path in paths]
    (root / "compile_commands.json").write_text(json.dumps(entries), encoding="utf-8")


def failing(root: Path, paths: tuple) -> set:
    """The files that do not compile, one clang-tidy run each. In one run over many
    files clang-tidy 23.1.2 keeps its error count, so every file after the first
    that fails also reads `Error while processing`. OCLint names the header an
    error sits in, not the file that included it (AFNetworking imports Foundation
    in its headers), so it cannot tell either."""
    return set().union(*(adapter.tidy_cognitive(root, [path]).failed for path in paths))


def _run(root: Path, built: list) -> dict:
    ccn, depth = adapter.oclint(root, built)
    return {"oclint-ccn": ccn, "oclint-depth": depth,
            "tidy-cognitive": adapter.tidy_cognitive(root, built),
            "tidy-nesting": adapter.tidy_nesting(root, built)}


def _nothing() -> dict:
    return {name: adapter.Report() for name in cfam.TOOLS}


@lru_cache(maxsize=16)
def _reports(root: Path, paths: tuple) -> dict:
    """Every tool's report over the files that compile; each report's `failed`
    holds the rest."""
    compile_commands(root, list(paths))
    failed = failing(root, paths)
    built = [path for path in paths if path not in failed]
    reports = _run(root, built) if built else _nothing()
    for report in reports.values():
        report.failed |= failed
    return reports


def oclint_ccn(root, paths, spans) -> dict:
    moved = cfam._starts(root, paths)
    found = {(path, moved.get((path, line), line)): value
             for path, line, value in _reports(root, tuple(paths))["oclint-ccn"].answers}
    return {key: found.get(key, 1) for key in spans}


def oclint_depth(root, paths, spans) -> dict:
    return cfam._per_function(spans, _reports(root, tuple(paths))["oclint-depth"].answers, 0)


def tidy_cognitive(root, paths, spans) -> dict:
    found = {(path, line): value for path, line, value
             in _reports(root, tuple(paths))["tidy-cognitive"].answers}
    return {key: found.get(key, 0) for key in spans}


def tidy_nesting(root, paths, spans) -> dict:
    return cfam._per_function(spans, _reports(root, tuple(paths))["tidy-nesting"].answers, 0)


# --- the Objective-C transforms and set-asides, each a rulings row ------------------------------

def elvis(fn, context) -> int:
    """AO-OCLINT-ELVIS: OCLint counts no GNU `a ?: b`; McCabe counts it +1."""
    return sum(node.child_by_field_name("consequence") is None
               for node in tooldiff.below(fn, {"conditional_expression"}))


def holds_elvis(fn, context) -> bool:
    """AO-TIDY-ELVIS: clang-tidy scores no GNU `a ?: b`."""
    return elvis(fn, context) > 0


def for_in(fn, context) -> bool:
    """AO-TIDY-OBJC-FOR-IN: a for...in loop, tree-sitter-objc's for_statement with `in`."""
    return any(any(kid.type == "in" for kid in node.children)
               for node in tooldiff.below(fn, {"for_statement"}))


def objc_method(fn, context) -> bool:
    """AO-TIDY-OBJC-METHOD and AO-TIDY-OBJC-METHOD-SIZE: clang-tidy's function
    checks read C functions only and report no Objective-C method."""
    return fn.type == "method_definition"


def _pool(node) -> bool:
    return node.type == "compound_statement" and bool(node.children) and (
        node.children[0].type == "@autoreleasepool")


def scope_block(fn, context) -> bool:
    """AO-OCLINT-OBJC-SCOPE and AO-TIDY-OBJC-SCOPE: both tools count the braces of
    @synchronized and @autoreleasepool as a level; the Sonar paper opens none."""
    return any(node.type == "synchronized_statement" or _pool(node)
               for node in tooldiff.below(fn, {"synchronized_statement", "compound_statement"}))


def _tool(name: str, read, transforms=None, set_aside=None) -> Tool:
    base = cfam.TOOLS[name]
    return Tool(base.column, read, {**base.transforms, **(transforms or {})},
                {**base.set_aside, **(set_aside or {})})


TOOLS = {
    "oclint-ccn": _tool("oclint-ccn", oclint_ccn, transforms={"AO-OCLINT-ELVIS": elvis}),
    "oclint-depth": _tool("oclint-depth", oclint_depth,
                          set_aside={"AO-OCLINT-OBJC-SCOPE": scope_block}),
    "tidy-cognitive": _tool("tidy-cognitive", tidy_cognitive, set_aside={
        "AO-TIDY-OBJC-METHOD": objc_method,
        "AO-TIDY-OBJC-FOR-IN": for_in,
        "AO-TIDY-OBJC-CATCH": tooldiff.holds({"catch_clause"}),
        "AO-TIDY-ELVIS": holds_elvis}),
    "tidy-nesting": _tool("tidy-nesting", tidy_nesting, set_aside={
        "AO-TIDY-OBJC-METHOD-SIZE": objc_method, "AO-TIDY-OBJC-SCOPE": scope_block}),
}


def _compiled(files: dict, headers: dict, root: Path, name: str) -> tuple:
    """(the files every tool compiled, the files that failed), the failures counted
    in the run log."""
    cfam.learn_macros(files)
    tooldiff.written({**headers, **files}, root)
    reports = _reports(root, tuple(sorted(files)))
    failed = set().union(*(report.failed for report in reports.values()))
    runlog.note("skipped_files", oracle=f"clang tools {name}: files that do not compile",
                count=len(failed))
    return {path: data for path, data in files.items() if path not in failed}, failed


def _sample(files: dict, headers: dict, root: Path, name: str, measure_set) -> tuple:
    compiled, failed = _compiled(files, headers, root, name)
    return (compiled, measure_set(files), root), failed


@pytest.fixture(scope="module")
def objc_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "objc")
    return _sample(files, {}, tmp_path_factory.mktemp("objc-probes"), "objc probes", measure_set)


@pytest.fixture(scope="module")
def afnetworking(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "afnetworking", (".m",))
    headers = analysis_corpora.member_files(full_corpus, "afnetworking", (".h",))
    return _sample(files, headers, tmp_path_factory.mktemp("afnetworking"), "afnetworking",
                   measure_set)


# constructs.m imports Foundation, and shapes.m names NSArray, BOOL and a superclass;
# the image has none of them. Every other objc and objcpp probe compiles.
PROBES_FAILING = {"objc/constructs.m", "objc/shapes.m"}


@LINUX
def test_objc_probes_compile_without_an_apple_sdk(objc_probes, oracle):
    oracle("oclint")
    (compiled, _, _), failed = objc_probes

    assert failed == PROBES_FAILING
    assert sorted(compiled) == ["objc/defects.m", "objc/equivalence.m", "objc/methods.m",
                                "objcpp/equivalence.mm", "objcpp/methods.mm"]


@LINUX
@pytest.mark.parametrize("name", sorted(TOOLS))
def test_objc_probes_match_the_clang_tools(name, objc_probes, oracle):
    oracle(cfam.ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, objc_probes[0])

    assert outcome.problems == []
    assert outcome.compared > 5


@LINUX
def test_no_afnetworking_file_compiles_without_an_apple_sdk(afnetworking, oracle):
    oracle("oclint")
    (compiled, _, _), failed = afnetworking

    assert (len(compiled), len(failed)) == (0, 6)


@LINUX
@pytest.mark.parametrize("name", sorted(TOOLS))
def test_afnetworking_matches_the_clang_tools_where_it_compiles(name, afnetworking, oracle):
    oracle(cfam.ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, afnetworking[0])

    assert outcome.problems == []
    assert outcome.compared == 0


# --- AFNetworking against tree-sitter, AF_API_AVAILABLE expanded one level ------------------------

AF_API = re.compile(rb"\bAF_(API_(?:UN)?AVAILABLE)\(")


def expanded(data: bytes) -> bytes:
    """AF_API_AVAILABLE( and AF_API_UNAVAILABLE( as AFCompatibilityMacros.h defines
    them, API_AVAILABLE( and API_UNAVAILABLE(, each shifted right by three spaces."""
    return AF_API.sub(lambda found: b"   " + found[1] + b"(", data)


@pytest.fixture(scope="module")
def afnetworking_expanded(full_corpus, measure_set):
    files = analysis_corpora.member_files(full_corpus, "afnetworking", (".m",))
    wider = {path: expanded(data) for path, data in files.items()}
    return (files, measure_set(files)), (wider, measure_set(wider))


COLUMNS = ("path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn", "cognitive",
           "nesting", "nloc", "params")


def _rows(measured) -> list:
    return sorted(tuple(row[column] for column in COLUMNS) for row in measured.rows)


def test_expanding_af_api_availability_changes_no_crapkit_row(afnetworking_expanded):
    (files, measured), (wider, measured_wider) = afnetworking_expanded

    assert [len(data) for data in wider.values()] == [len(data) for data in files.values()]
    assert wider != files
    assert _rows(measured_wider) == _rows(measured)


# The functions tree-sitter still cannot read once the macro is expanded: three hold
# an #if/#else that splits an if statement, and line 63 is a function the grammar's
# error recovery makes of the else-if that line 51's #if cuts off.
UNREADABLE = [("AFNetworking/AFNetworkReachabilityManager.m", 51),
              ("AFNetworking/AFNetworkReachabilityManager.m", 63),
              ("AFNetworking/AFURLSessionManager.m", 108),
              ("AFNetworking/AFURLSessionManager.m", 898)]


def test_the_expansion_leaves_only_preprocessor_splits_unread(afnetworking_expanded):
    _, (wider, _) = afnetworking_expanded
    unread = [(path, fn.start_point[0] + 1) for path, data in sorted(wider.items())
              for fn, _, context in analysis_treesitter.functions(path, data)
              if analysis_treesitter._unreadable(fn, context)]

    assert unread == UNREADABLE


EXPANDED_MIN = {"spans": 250, "ccn": 250, "cognitive": 180, "nesting": 180, "sizes": 80}


@pytest.mark.parametrize("group", sorted(EXPANDED_MIN))
def test_expanded_afnetworking_matches_the_treesitter_counters(group, afnetworking_expanded):
    _, (wider, measured) = afnetworking_expanded

    outcome = analysis_tstests.check(wider, measured, group, "afnetworking expanded")
    assert outcome.compared > EXPANDED_MIN[group]


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

ROOT_CLASS = ("__attribute__((objc_root_class))\n"
              "@interface K\n"
              "@end\n"
              "@implementation K\n")
METHOD_IF = ROOT_CLASS + ("- (int)f:(int)a {\n"
                          "    if (a) {\n"
                          "        return 1;\n"
                          "    }\n"
                          "    return 0;\n"
                          "}\n"
                          "@end\n")
ELVIS = ("int f(int a, int b) {\n"
         "    return a ?: b;\n"
         "}\n")
FOR_IN = ("int f(id items) {\n"
          "    for (id item in items) {\n"
          "        if (item) {\n"
          "            return 1;\n"
          "        }\n"
          "    }\n"
          "    return 0;\n"
          "}\n")
CATCH = ("int use(int k);\n"
         "int f(id x) {\n"
         "    @try {\n"
         "        use(1);\n"
         "    } @catch (id e) {\n"
         "        return 1;\n"
         "    }\n"
         "    return 0;\n"
         "}\n")
SYNCHRONIZED = ("int f(id x) {\n"
                "    @synchronized (x) {\n"
                "        if (x) {\n"
                "            return 1;\n"
                "        }\n"
                "    }\n"
                "    return 0;\n"
                "}\n")
# ruling id -> (tool, source, the line its function starts on)
HAND = {
    "AO-OCLINT-ELVIS": ("oclint-ccn", ELVIS, 1),
    "AO-TIDY-ELVIS": ("tidy-cognitive", ELVIS, 1),
    "AO-TIDY-OBJC-METHOD": ("tidy-cognitive", METHOD_IF, 5),
    "AO-TIDY-OBJC-METHOD-SIZE": ("tidy-nesting", METHOD_IF, 5),
    "AO-TIDY-OBJC-FOR-IN": ("tidy-cognitive", FOR_IN, 1),
    "AO-TIDY-OBJC-CATCH": ("tidy-cognitive", CATCH, 2),
    "AO-OCLINT-OBJC-SCOPE": ("oclint-depth", SYNCHRONIZED, 1),
    "AO-TIDY-OBJC-SCOPE": ("tidy-nesting", SYNCHRONIZED, 1),
}
# crapkit reads the ?: hand case 0 until calc-bug analysis-oracles-100 is fixed: the
# defect's row and the value the Sonar paper gives it, worked by hand (B1, +1).
HELD = {"AO-TIDY-ELVIS": ("AO-C-ELVIS-COG", 1)}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"{ruling}/case.m": source.encode() for ruling, (_, source, _) in HAND.items()}
    sample, failed = _sample(files, {}, tmp_path_factory.mktemp("objc-hand"), "objc hand",
                             measure_set)
    assert failed == set()
    return sample


@LINUX
@pytest.mark.parametrize("ruling_id", [
    pytest.param(ruling, marks=analysis_tables.marks(HELD.get(ruling, ("",))[0]))
    for ruling in sorted(HAND)])
def test_each_objc_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _, start = HAND[ruling_id]
    oracle(cfam.ORACLE[name])
    tool = TOOLS[name]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand,
                                                     f"{ruling_id}/case.m", start)
    if ruling_id in HELD:
        rulings.pin_ruling(HELD[ruling_id][0], crapkit=crapkit, oracle=HELD[ruling_id][1])

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


@LINUX
def test_clang_tidy_scores_no_elvis(hand, oracle):
    """Sonar v1.7 App. B1 worked by hand: `return a ?: b` holds one conditional
    operator at nesting 0, so cognitive is 1. clang-tidy reads 0 whatever crapkit
    says, which is why AO-TIDY-ELVIS sets such functions aside."""
    oracle("clang-tidy")
    _, raw, _, _ = tooldiff.hand_values(TOOLS["tidy-cognitive"], LISTED, hand,
                                        "AO-TIDY-ELVIS/case.m", 1)

    assert raw == 0


def test_every_objc_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted({*cfam.HAND, *HAND})
