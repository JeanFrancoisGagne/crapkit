"""C, C++ and Objective-C ccn, cognitive and nesting against OCLint and clang-tidy.

Both tools live in the accuracy image (oracles/clang_adapters.py), so this
runs on the Linux nightly cell, over the C-family probe files and the full
corpus's cjson member, each file its own translation unit in a written
compile_commands.json. A file a tool cannot compile (an Objective-C probe
that imports Foundation) is skipped for every tool and counted. The join,
the set-asides for known crapkit defect shapes and the transforms follow
analysis_tooldiff.

OCLint names a function by the line its declaration starts, clang-tidy and
tree-sitter's function list by its name's line, so OCLint's answers move to
the name's line first. Where a tool counts a construct differently from
crapkit's documented reading, a named transform or set-aside covers it, and
each such rule is a rulings row pinned below by a hand case with the tool's
raw value and crapkit's.
"""
from functools import lru_cache
from pathlib import Path
import re

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import clang_adapters as adapter
from accuracy.analysis_oracles.oracles import go_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LISTED = tooldiff.listed_except(set())
HEADERS = {}  # root -> the language its .h files compile as


@lru_cache(maxsize=16)
def _reports(root: Path, paths: tuple) -> dict:
    adapter.compile_commands(root, list(paths), HEADERS.get(root, "c"))
    ccn, depth = adapter.oclint(root, list(paths))
    return {"oclint-ccn": ccn, "oclint-depth": depth,
            "tidy-cognitive": adapter.tidy_cognitive(root, list(paths)),
            "tidy-nesting": adapter.tidy_nesting(root, list(paths))}


def _starts(root: Path, paths: list) -> dict:
    """{(path, a declaration's first line): its name's line} for every function."""
    moved = {}
    for path in paths:
        for fn, _ in LISTED(path, (root / path).read_bytes()):
            outer = fn.parent if fn.parent is not None and \
                fn.parent.type == "template_declaration" else fn
            moved[(path, outer.start_point[0] + 1)] = counters.start_line(fn)
    return moved


def _per_function(spans: dict, answers: list, floor: int) -> dict:
    """The largest answer inside each function, or floor for one with none."""
    found = dict.fromkeys(spans, floor)
    for path, line, value in answers:
        owner = go_adapters.innermost(spans, path, line)
        if owner is not None:
            found[owner] = max(found[owner], value)
    return found


def oclint_ccn(root, paths, spans) -> dict:
    moved = _starts(root, paths)
    found = {(path, moved.get((path, line), line)): value
             for path, line, value in _reports(root, tuple(paths))["oclint-ccn"].answers}
    return {key: found.get(key, 1) for key in spans}


def oclint_depth(root, paths, spans) -> dict:
    return _per_function(spans, _reports(root, tuple(paths))["oclint-depth"].answers, 0)


def tidy_cognitive(root, paths, spans) -> dict:
    found = {(path, line): value for path, line, value
             in _reports(root, tuple(paths))["tidy-cognitive"].answers}
    return {key: found.get(key, 0) for key in spans}


def tidy_nesting(root, paths, spans) -> dict:
    return _per_function(spans, _reports(root, tuple(paths))["tidy-nesting"].answers, 0)


# --- the transforms and set-asides, each a rulings row ------------------------------------------

def body_block(fn, context) -> int:
    """AO-OCLINT-BODY-BLOCK and AO-TIDY-BODY-BLOCK: both tools count braces, the
    function body's own braces as level 1; the nesting column counts structures."""
    return -1


def _calls_itself(call, name: bytes, data: bytes) -> bool:
    callee = call.child_by_field_name("function")
    return callee is not None and data[callee.start_byte:callee.end_byte] == name


def recursion(fn, context) -> int:
    """AO-TIDY-RECURSION: clang-tidy leaves out the paper's +1 for a function in a
    recursion cycle; a direct call to itself by name gets it back."""
    name = counters.name(fn, context.data).encode()
    return int(any(_calls_itself(call, name, context.data)
                   for call in tooldiff.below(fn, {"call_expression"})))


# Function-like macros of every file measured, by name: both tools read the code
# after the preprocessor, crapkit reads the text as written.
MACROS = {}  # name -> {replacement text}
FALLBACKS = set()  # names defined only when the build has not defined them
DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\([^)]*\)((?:[^\n]*\\\n)*[^\n]*)",
                    re.MULTILINE)
DECISION = re.compile(r"&&|\|\||\?|\b(?:if|for|while|case|catch)\b")
CALLED = re.compile(r"\b(\w+)\s*\(")


GUARD = re.compile(r"#[ \t]*if(?:ndef[ \t]+|[ \t]*!\s*defined\s*\(?\s*)(\w+)")


def learn_macros(files: dict) -> None:
    for data in files.values():
        text = data.decode("utf-8", "replace")
        FALLBACKS.update(GUARD.findall(text))
        for found in DEFINE.finditer(text):
            MACROS.setdefault(found[1], set()).add(found[2])


def _inner(text: str, seen: frozenset) -> list:
    return [expansion(name, seen) for name in CALLED.findall(text)
            if name in MACROS and name not in seen]


def _braces(text: str, inner: list) -> bool:
    return "{" in text or any(braces for _, braces in inner)


def _ambiguous(name: str, counts: list) -> bool:
    return len(MACROS[name]) > 1 or name in FALLBACKS or None in counts


def expansion(name: str, seen: frozenset = frozenset()) -> tuple:
    """(decisions, braces) of one expansion of a macro, the macros it calls
    expanded too; decisions is None when a macro on the way has two definitions
    or is defined under `#ifndef` its own name, a fallback the build may skip."""
    text = min(MACROS[name])
    inner = _inner(text, seen | {name})
    counts = [len(DECISION.findall(text)), *(count for count, _ in inner)]
    return (None if _ambiguous(name, counts) else sum(counts)), _braces(text, inner)


def _callee_names(fn, context) -> list:
    callees = [call.child_by_field_name("function")
               for call in tooldiff.below(fn, {"call_expression"})]
    return [context.text(node).decode() for node in callees if node]


def _expansions(fn, context) -> list:
    """(decisions, braces) of each macro the function invokes."""
    return [expansion(name) for name in _callee_names(fn, context) if name in MACROS]


def macro_decisions(fn, context) -> int:
    """AO-OCLINT-MACRO-DECISIONS: OCLint counts the &&, ||, ?: and branches a
    macro expands to; crapkit counts the invocation as written, a plain call."""
    return -sum(count or 0 for count, _ in _expansions(fn, context))


def macro_conditional(fn, context) -> bool:
    """AO-OCLINT-MACRO-CONDITIONAL: a macro with two #define lines under #if, or
    one under `#ifndef` its own name, expands to what the build's defines pick,
    so its decisions are unknown and the function is set aside for OCLint."""
    return any(count is None for count, _ in _expansions(fn, context))


def macro_blocks(fn, context) -> bool:
    """AO-OCLINT-MACRO-BLOCK and AO-TIDY-MACRO-BLOCK: a macro that expands to
    braces adds compound statements neither tool can tell from written ones."""
    return any(braces for _, braces in _expansions(fn, context))


def macro_cognitive(fn, context) -> bool:
    """AO-TIDY-MACRO-DECISIONS: clang-tidy scores the branches and operators a
    macro expands to, merged into the sequences around the invocation, so a
    function invoking a deciding macro is set aside for cognitive."""
    return any(count != 0 for count, _ in _expansions(fn, context))


BODIES = {"if_statement": "consequence", "for_statement": "body", "while_statement": "body",
          "do_statement": "body", "else_clause": None, "for_range_loop": "body"}


def _unbraced(node) -> bool:
    field = BODIES[node.type]
    body = node.child_by_field_name(field) if field else node.named_children[-1]
    return body is not None and body.type not in {"compound_statement", "if_statement"}


def unbraced(fn, context) -> bool:
    """AO-OCLINT-UNBRACED and AO-TIDY-UNBRACED: a ternary, or an if, else or loop
    whose body has no braces, nests without a compound statement, which is all
    either tool counts, so such functions are set aside."""
    return bool(tooldiff.below(fn, {"conditional_expression"})) or \
        any(map(_unbraced, tooldiff.below(fn, set(BODIES))))


TOOLS = {
    "oclint-ccn": Tool("ccn_std", oclint_ccn,
                       transforms={"AO-OCLINT-MACRO-DECISIONS": macro_decisions},
                       set_aside={"AO-OCLINT-MACRO-CONDITIONAL": macro_conditional}),
    "oclint-depth": Tool("nesting", oclint_depth,
                         transforms={"AO-OCLINT-BODY-BLOCK": body_block},
                         set_aside={"AO-OCLINT-UNBRACED": unbraced,
                                    "AO-OCLINT-MACRO-BLOCK": macro_blocks}),
    "tidy-cognitive": Tool("cognitive", tidy_cognitive,
                           transforms={"AO-TIDY-RECURSION": recursion},
                           set_aside={"AO-TIDY-MACRO-DECISIONS": macro_cognitive}),
    "tidy-nesting": Tool("nesting", tidy_nesting,
                         transforms={"AO-TIDY-BODY-BLOCK": body_block},
                         set_aside={"AO-TIDY-UNBRACED": unbraced,
                                    "AO-TIDY-MACRO-BLOCK": macro_blocks}),
}
ORACLE = {"oclint-ccn": "oclint", "oclint-depth": "oclint", "tidy-cognitive": "clang-tidy",
          "tidy-nesting": "clang-tidy"}
C_FAMILY = ("c", "cpp", "objc")


def _compiled(files: dict, measured, root: Path, header: str, name: str) -> tuple:
    """The sample without the files a tool could not compile, which are counted."""
    HEADERS[root] = header
    learn_macros(files)
    tooldiff.written(files, root)
    reports = _reports(root, tuple(sorted(files)))
    failed = set().union(*(report.failed for report in reports.values()))
    runlog.note("skipped_files", oracle=f"clang tools {name}: files that do not compile",
                count=len(failed))
    return {path: data for path, data in files.items() if path not in failed}, measured, root


@pytest.fixture(scope="module")
def c_probes(measure_set, tmp_path_factory):
    probes = analysis_tables.probe_files()
    files = {path: data for language in C_FAMILY
             for path, data in analysis_tstests.of_language(probes, language).items()}
    return _compiled(files, measure_set(files), tmp_path_factory.mktemp("c-probes"), "c", "probes")


@pytest.fixture(scope="module")
def c_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "cjson", (".c", ".h"))
    return _compiled(files, measure_set(files), tmp_path_factory.mktemp("cjson"), "c", "cjson")


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_c_family_probes_match_the_clang_tools(name, c_probes, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, c_probes)

    assert outcome.problems == []
    assert outcome.compared > 10


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_cjson_matches_the_clang_tools(name, c_corpus, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, c_corpus)

    assert outcome.problems == []
    assert outcome.compared > 50


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

NESTED_IF = ("int f(int a) {\n"
             "    if (a) {\n"
             "        return 1;\n"
             "    }\n"
             "    return 0;\n"
             "}\n")
FACTORIAL = ("int fact(int n) {\n"
             "    if (n <= 1) {\n"
             "        return 1;\n"
             "    }\n"
             "    return n * fact(n - 1);\n"
             "}\n")
TERNARY = ("int f(int a) {\n"
           "    return a > 0 ? 1 : 2;\n"
           "}\n")
BOTH = ("#define BOTH(a, b) ((a) && (b))\n"
        "int f(int a, int b) {\n"
        "    if (BOTH(a, b)) {\n"
        "        return 1;\n"
        "    }\n"
        "    return 0;\n"
        "}\n")
FALLBACK = "#ifndef EITHER\n" + BOTH.replace("BOTH", "EITHER").replace(
    "\nint", "\n#endif\nint", 1)
TWICE = ("#define TWICE(x) do { x; x; } while (0)\n"
         "void f(int a) {\n"
         "    TWICE(a++);\n"
         "}\n")
# ruling id -> (tool, source, the line its function starts on)
HAND = {
    "AO-OCLINT-BODY-BLOCK": ("oclint-depth", NESTED_IF, 1),
    "AO-TIDY-BODY-BLOCK": ("tidy-nesting", NESTED_IF, 1),
    "AO-TIDY-RECURSION": ("tidy-cognitive", FACTORIAL, 1),
    "AO-OCLINT-UNBRACED": ("oclint-depth", TERNARY, 1),
    "AO-TIDY-UNBRACED": ("tidy-nesting", TERNARY, 1),
    "AO-OCLINT-MACRO-DECISIONS": ("oclint-ccn", BOTH, 2),
    "AO-OCLINT-MACRO-CONDITIONAL": ("oclint-ccn", FALLBACK, 4),
    "AO-TIDY-MACRO-DECISIONS": ("tidy-cognitive", BOTH, 2),
    "AO-OCLINT-MACRO-BLOCK": ("oclint-depth", TWICE, 2),
    "AO-TIDY-MACRO-BLOCK": ("tidy-nesting", TWICE, 2),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"{ruling}/case.c": source.encode() for ruling, (_, source, _) in HAND.items()}
    return _compiled(files, measure_set(files), tmp_path_factory.mktemp("c-hand"), "c", "hand")


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_c_family_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _, start = HAND[ruling_id]
    oracle(ORACLE[name])
    tool = TOOLS[name]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand,
                                                     f"{ruling_id}/case.c", start)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_c_family_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted(HAND)
