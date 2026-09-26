"""C, C++ and Objective-C ccn, cognitive and nesting against OCLint and clang-tidy.

Both tools live in the accuracy image (oracles/clang_adapters.py), so this
runs on the Linux nightly cell, over the C-family probe files and the full
corpus's cjson and fmt members (fmt's headers compile as C++), each file its
own translation unit in a written compile_commands.json. A file the tools
cannot compile (an Objective-C probe that imports Foundation, fmt's
src/fmt.cc, a C++20 module unit) is skipped for every tool and counted:
OCLint names those files in one process, and clang-tidy's flags must be the
tail of the file list from the first of them (AO-TIDY-STICKY-ERROR). The
join, the set-asides for known crapkit defect shapes and the transforms
follow analysis_tooldiff.

OCLint names a function by the line its declaration starts, clang-tidy and
tree-sitter's function list by its name's line, so OCLint's answers move to
the name's line first. Where a tool counts a construct differently from
crapkit's documented reading, a named transform or set-aside covers it, and
each such rule is a rulings row pinned below by a hand case with the tool's
raw value and crapkit's. So is each place tree-sitter-cpp reads wrong
(ts_shapes_cpp AO-TREE-*), the process count per shard of each adapter, and
retro R15 (706036c), C++ rvalue references against clang-tidy's cognitive.
"""
from pathlib import Path
import re

import pytest

from accuracy.analysis_oracles import (analysis_corpora, analysis_tables, analysis_treesitter,
                                       analysis_tstests, ts_defect_shapes, ts_shapes_cpp)
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import clang_adapters as adapter
from accuracy.analysis_oracles.oracles import go_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LISTED = tooldiff.listed_except(set())
HEADERS = {}  # root -> the language its .h files compile as
REPORTS = {}  # (root, paths) -> each tool's report


def _reports(root: Path, paths: tuple) -> dict:
    """Each tool's report over paths, from a run that already covered them if one did."""
    known = next((reports for (place, done), reports in REPORTS.items()
                  if place == root and set(paths) <= set(done)), None)
    if known is None:
        adapter.compile_commands(root, list(paths), HEADERS.get(root, "c"))
        ccn, depth = adapter.oclint(root, list(paths))
        known = REPORTS[(root, paths)] = {
            "oclint-ccn": ccn, "oclint-depth": depth,
            "tidy-cognitive": adapter.tidy_cognitive(root, list(paths)),
            "tidy-nesting": adapter.tidy_nesting(root, list(paths))}
    return known


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


SEEN = {}  # tool family -> {file bytes: the lines where it reported a compound statement}


def _saw_blocks(family: str, files: dict, report) -> None:
    lines = {}
    for path, line, _ in report.answers:
        lines.setdefault(path, set()).add(line)
    for path, data in files.items():
        SEEN.setdefault(family, {}).setdefault(data, set()).update(lines.get(path, ()))


def not_compiled(family: str):
    """AO-OCLINT-NOT-COMPILED and AO-TIDY-NOT-COMPILED: a function in a preprocessor
    branch the build leaves out reaches no compiler, so the tool reports no block
    at its body's line (DeepNestedBlock at 0 and NestingThreshold 0 see every one)."""
    def unseen(fn, context) -> bool:
        body = fn.child_by_field_name("body")
        seen = SEEN.get(family, {}).get(context.data, set())
        return body is not None and body.start_point[0] + 1 not in seen
    return unseen


def range_for(fn, context) -> int:
    """AO-OCLINT-RANGE-FOR: OCLint counts no range-based for loop; each gets its +1."""
    return len(tooldiff.below(fn, {"for_range_loop"}))


def braceless_goto(fn, context) -> int:
    """AO-TIDY-BRACELESS-GOTO: clang-tidy adds nothing for a goto that is the braceless
    body of an if; each gets its +1 back."""
    return sum(node.parent is not None and node.parent.type == "if_statement"
               for node in tooldiff.below(fn, {"goto_statement"}))


LOGICAL = frozenset({"&&", "||"})


def _operator(node):
    """A logical binary expression's operator token, or None."""
    token = node.child_by_field_name("operator") if node.type == "binary_expression" else None
    return token.type if token is not None and token.type in LOGICAL else None


def _unwrapped(node):
    while node is not None and node.type == "parenthesized_expression":
        node = node.named_children[0] if node.named_children else None
    return node


def _in_order(node, out: list) -> list:
    node = _unwrapped(node)
    if node is not None and _operator(node):
        _in_order(node.child_by_field_name("left"), out)
        out.append(_operator(node))
        _in_order(node.child_by_field_name("right"), out)
    return out


def _tree_runs(node, above=None) -> int:
    """clang-tidy's count: +1 for each logical operator whose logical parent differs."""
    node = _unwrapped(node)
    own = _operator(node) if node is not None else None
    if own is None:
        return 0
    return int(own != above) + sum(_tree_runs(node.child_by_field_name(side), own)
                                   for side in ("left", "right"))


def _order_runs(top) -> int:
    """The paper's count, the operators read left to right through parentheses."""
    ops = _in_order(top, [])
    return sum(1 for index, op in enumerate(ops) if index == 0 or op != ops[index - 1])


def _top(node) -> bool:
    parent = node.parent
    while parent is not None and parent.type == "parenthesized_expression":
        parent = parent.parent
    return bool(_operator(node)) and (parent is None or not _operator(parent))


def run_order(fn, context) -> int:
    """AO-TIDY-RUN-TREE: clang-tidy's sequence count of each logical expression,
    replaced by the left-to-right one."""
    return sum(_order_runs(top) - _tree_runs(top)
               for top in tooldiff.below(fn, {"binary_expression"}) if _top(top))


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
                       transforms={"AO-OCLINT-MACRO-DECISIONS": macro_decisions,
                                   "AO-OCLINT-RANGE-FOR": range_for},
                       set_aside={"AO-OCLINT-MACRO-CONDITIONAL": macro_conditional,
                                  "AO-OCLINT-NOT-COMPILED": not_compiled("oclint")}),
    "oclint-depth": Tool("nesting", oclint_depth,
                         transforms={"AO-OCLINT-BODY-BLOCK": body_block},
                         set_aside={"AO-OCLINT-UNBRACED": unbraced,
                                    "AO-OCLINT-MACRO-BLOCK": macro_blocks,
                                    "AO-OCLINT-NOT-COMPILED": not_compiled("oclint")}),
    "tidy-cognitive": Tool("cognitive", tidy_cognitive,
                           transforms={"AO-TIDY-RECURSION": recursion,
                                       "AO-TIDY-BRACELESS-GOTO": braceless_goto,
                                       "AO-TIDY-RUN-TREE": run_order},
                           set_aside={"AO-TIDY-MACRO-DECISIONS": macro_cognitive,
                                      "AO-TIDY-NOT-COMPILED": not_compiled("tidy")}),
    "tidy-nesting": Tool("nesting", tidy_nesting,
                         transforms={"AO-TIDY-BODY-BLOCK": body_block},
                         set_aside={"AO-TIDY-UNBRACED": unbraced,
                                    "AO-TIDY-MACRO-BLOCK": macro_blocks,
                                    "AO-TIDY-NOT-COMPILED": not_compiled("tidy")}),
}
ORACLE = {"oclint-ccn": "oclint", "oclint-depth": "oclint", "tidy-cognitive": "clang-tidy",
          "tidy-nesting": "clang-tidy"}
C_FAMILY = ("c", "cpp", "objc")
FAILED = {}  # sample name -> the files neither tool could compile


def _failed(reports: dict, paths: list) -> set:
    """OCLint's failed files; each clang-tidy report must flag their tail (AO-TIDY-STICKY-ERROR)."""
    for name in ("tidy-cognitive", "tidy-nesting"):
        adapter.failed_files(paths, reports["oclint-ccn"].failed, reports[name].failed)
    return set(reports["oclint-ccn"].failed)


def _compiled(files: dict, measured, root: Path, header: str, name: str) -> tuple:
    """The sample without the files the tools could not compile, which are
    counted. The tools' reports stay keyed by the files left."""
    HEADERS[root] = header
    learn_macros(files)
    tooldiff.written(files, root)
    paths = sorted(files)
    reports = _reports(root, tuple(paths))
    FAILED[name] = _failed(reports, paths)
    left = tuple(path for path in paths if path not in FAILED[name])
    REPORTS[(root, left)] = reports
    _saw_blocks("oclint", files, reports["oclint-depth"])
    _saw_blocks("tidy", files, reports["tidy-nesting"])
    runlog.note("skipped_files", oracle=f"clang tools {name}: files that do not compile",
                count=len(FAILED[name]))
    return {path: files[path] for path in left}, measured, root


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


@pytest.fixture(scope="module")
def fmt_corpus(full_corpus, measure_set, tmp_path_factory):
    """fmt's sources and headers, the headers compiled as C++."""
    files = analysis_corpora.member_files(full_corpus, "fmt", (".cc", ".h"))
    return _compiled(files, measure_set(files), tmp_path_factory.mktemp("fmt"), "c++", "fmt")


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


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_fmt_matches_the_clang_tools(name, fmt_corpus, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, fmt_corpus)

    assert FAILED["fmt"] == {"src/fmt.cc"}  # a C++20 module unit: skipped and counted
    assert outcome.problems == []
    assert outcome.compared > 100


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
INACTIVE = ("#ifdef NEVER_DEFINED\n"
            "int f(int a) {\n"
            "    if (a) {\n"
            "        return 1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "#endif\n")
RANGE_FOR = ("int f(int (&v)[4]) {\n"
             "    int total = 0;\n"
             "    for (int x : v) {\n"
             "        total += x;\n"
             "    }\n"
             "    return total;\n"
             "}\n")
BRACELESS_GOTO = ("void g(void);\n"
                  "void f(int a) {\n"
                  "    if (a)\n"
                  "        goto out;\n"
                  "    g();\n"
                  "out:\n"
                  "    g();\n"
                  "}\n")
PAREN_RUN = ("void g(void);\n"
             "void f(int a, int b, int c, int d) {\n"
             "    if (a && ((b && c) || d)) {\n"
             "        g();\n"
             "    }\n"
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
    "AO-OCLINT-NOT-COMPILED": ("oclint-ccn", INACTIVE, 2),
    "AO-TIDY-NOT-COMPILED": ("tidy-cognitive", INACTIVE, 2),
    "AO-OCLINT-RANGE-FOR": ("oclint-ccn", RANGE_FOR, 1),
    "AO-TIDY-BRACELESS-GOTO": ("tidy-cognitive", BRACELESS_GOTO, 2),
    "AO-TIDY-RUN-TREE": ("tidy-cognitive", PAREN_RUN, 2),
}
CPP_CASES = {"AO-OCLINT-RANGE-FOR"}  # the hand cases written in C++


def _hand_path(ruling_id: str) -> str:
    return f"{ruling_id}/case.{'cpp' if ruling_id in CPP_CASES else 'c'}"


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {_hand_path(ruling): source.encode() for ruling, (_, source, _) in HAND.items()}
    return _compiled(files, measure_set(files), tmp_path_factory.mktemp("c-hand"), "c", "hand")


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_c_family_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _, start = HAND[ruling_id]
    oracle(ORACLE[name])
    tool = TOOLS[name]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand, _hand_path(ruling_id),
                                                     start)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_c_family_rule_has_a_hand_case():
    assert sorted(set(tooldiff.rules(TOOLS))) == sorted(HAND)


# --- tree-sitter-cpp's misreadings (ts_shapes_cpp AO-TREE-*): crapkit's value, the counters' ----

RVALUE_TAKE = ("struct Widget {\n"
               "    int v;\n"
               "};\n"
               "void use(Widget& w);\n"
               "void take(Widget&& w) {\n"
               "    use(w);\n"
               "}\n")
MACRO_BLOCK = ("void g();\n"
               "void h();\n"
               "void f() {\n"
               "    FMT_TRY {\n"
               "        g();\n"
               "    }\n"
               "    FMT_CATCH(...) {}\n"
               "    h();\n"
               "}\n")
MACRO_STRUCT = ("struct FMT_API pipe {\n"
                "    int read_end;\n"
                "};\n")
MACRO_CONSTRUCTOR = ("struct buffer {\n"
                     "    FMT_CONSTEXPR buffer(int grow, int sz) noexcept\n"
                     "        : size_(sz), cap_(sz) {\n"
                     "        g();\n"
                     "    }\n"
                     "};\n")
ROW = ts_shapes_cpp.ROW
# ruling id -> (source, the line of the function or row, the column the rule covers)
TREE_HAND = {
    "AO-TREE-RVALUE-LOGICAL": (RVALUE_TAKE, 5, "ccn_std"),
    "AO-TREE-MACRO-DEFINITION": (MACRO_BLOCK, 7, ROW),
    "AO-TREE-NOT-A-FUNCTION": (MACRO_STRUCT, 1, ROW),
    "AO-TREE-DECLARATION-ERROR": (MACRO_CONSTRUCTOR, 2, ROW),
}


@pytest.fixture(scope="module")
def tree_hand(measure_set):
    files = {f"{ruling}/case.cpp": source.encode()
             for ruling, (source, _, _) in TREE_HAND.items()}
    return files, measure_set(files)


def _tree_value(context, start: int, column: str) -> tuple:
    """(the counters' value, the function node or None) at start: a row count for ROW."""
    fn = next((fn for fn in counters.functions(context.tree, context.spec)
               if counters.start_line(fn) == start), None)
    if column == ROW:
        return int(fn is not None), fn
    return analysis_treesitter.values(fn, context.spec, context.data)[column], fn


def _crapkit_value(measured, path: str, start: int, column: str):
    rows = [row for row in measured.in_file(path) if row["start"] == start]
    return len(rows) if column == ROW else rows[0][column]


def _held(ruling_id: str, fn, context, start: int, column: str) -> bool:
    """The rule sets fn aside, or explains crapkit's row where the counters list none."""
    if fn is None:
        return ts_defect_shapes.extra_explained(context, start)
    return ruling_id in analysis_treesitter.reasons(fn, context, (column,))


@pytest.mark.parametrize("ruling_id", sorted(TREE_HAND))
def test_each_treesitter_cpp_rule_is_pinned_by_a_hand_case(ruling_id, tree_hand):
    files, measured = tree_hand
    path, (_, start, column) = f"{ruling_id}/case.cpp", TREE_HAND[ruling_id]
    context = analysis_treesitter.file_context(path, files[path])
    raw, fn = _tree_value(context, start, column)
    crapkit = _crapkit_value(measured, path, start, column)

    assert _held(ruling_id, fn, context, start, column)
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


# The AO-TREE-* shapes, and the rule ts_shapes_cpp.extra_row() applies.
TREE_RULES = {ruling for ruling, *_ in ts_shapes_cpp.SHAPES if ruling.startswith("AO-TREE-")}


def test_every_treesitter_cpp_rule_has_a_hand_case():
    assert sorted(TREE_HAND) == sorted(TREE_RULES | {"AO-TREE-DECLARATION-ERROR"})


# --- processes per shard, and which files failed --------------------------------------------------

def _counted(monkeypatch) -> list:
    started = []
    real = adapter.hang_guard.run

    def run(argv, **kwargs):
        started.append(argv[0])
        return real(argv, **kwargs)
    monkeypatch.setattr(adapter.hang_guard, "run", run)
    return started


BROKEN = "int broken(int a) {\n    return undeclared_name;\n}\n"
SHARD = {"a_broken.c": BROKEN, "b_nested.c": NESTED_IF, "c_fact.c": FACTORIAL}


@pytest.fixture
def shard(tmp_path):
    tooldiff.written({path: source.encode() for path, source in SHARD.items()}, tmp_path)
    adapter.compile_commands(tmp_path, sorted(SHARD), "c")
    return tmp_path, sorted(SHARD)


def test_each_clang_adapter_starts_its_pinned_process_count(shard, monkeypatch, oracle):
    oracle("clang-tidy")
    oracle("oclint")
    root, paths = shard
    started = _counted(monkeypatch)
    counts = {}
    for name, run in (("oclint", adapter.oclint), ("tidy_cognitive", adapter.tidy_cognitive),
                      ("tidy_nesting", adapter.tidy_nesting)):
        run(root, paths)
        counts[name], started[:] = len(started), []

    # the deepest block in the shard is an if's, level 2 counting the body: 1 + 2 sweeps
    assert counts == {"oclint": 1, "tidy_cognitive": 1, "tidy_nesting": 3}
    rulings.pin_ruling("AO-TIDY-NESTING-SWEEP", crapkit=counts["oclint"],
                       oracle=counts["tidy_nesting"])


def test_clang_tidy_flags_every_file_after_a_failed_one(shard, measure_set, oracle):
    oracle("clang-tidy")
    oracle("oclint")
    root, paths = shard
    tidy, (ccn, _) = adapter.tidy_cognitive(root, paths), adapter.oclint(root, paths)
    measured = measure_set({path: source.encode() for path, source in SHARD.items()})

    assert tidy.failed == set(paths)  # a_broken.c and every file after it
    assert adapter.failed_files(paths, ccn.failed, tidy.failed) == {"a_broken.c"}
    answer = next(value for path, line, value in tidy.answers if path == "b_nested.c")
    rulings.pin_ruling("AO-TIDY-STICKY-ERROR", oracle=answer,
                       crapkit=measured.in_file("b_nested.c")[0]["cognitive"])


# --- retro R15 (706036c): C++ rvalue references against clang-tidy's cognitive --------------------

R15_FILE = "cpp/rvalue.cpp"
# A scope naming the probe's directory, which crapkit before 2026-09-05 also reads (a
# root scope owned no file then); the language is the one 706036c admitted.
R15_CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "cpp"\npaths = ["cpp"]\n'
              'languages = ["cpp"]\ncoverage_optional = true\n')
R15_FUNCTIONS = ("take", "pass")  # their && sits in the signature, what 706036c fixed


@pytest.fixture(scope="module")
def r15_sample(measure_set, tmp_path_factory):
    source = analysis_tables.probe_files()[R15_FILE]
    measured = measure_set({R15_FILE: source, "crapkit.toml": R15_CONFIG.encode()})
    root = tmp_path_factory.mktemp("r15")
    tooldiff.written({R15_FILE: source}, root)
    adapter.compile_commands(root, [R15_FILE], "c++")
    return measured, adapter.tidy_cognitive(root, [R15_FILE])


def _by_name(measured) -> dict:
    return {row["long_name"].split("(")[0].strip(): row for row in measured.in_file(R15_FILE)}


def test_cpp_rvalue_refs_match_clang_tidy(r15_sample, oracle):
    oracle("clang-tidy")
    measured, tidy = r15_sample
    found = {line: value for _, line, value in tidy.answers}
    rows = _by_name(measured)
    crapkit = {name: rows[name]["cognitive"] for name in R15_FUNCTIONS}

    assert tidy.failed == set()
    assert crapkit == {name: found.get(rows[name]["start"], 0) for name in R15_FUNCTIONS}
