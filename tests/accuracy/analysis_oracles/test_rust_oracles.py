"""Rust ccn and cognitive against rust-code-analysis and cargo-crap.

Both tools live in the accuracy image (oracles/rust_adapters.py), so this runs
on the Linux nightly cell, over the Rust probe files and the full corpus's
ripgrep member. The join, the set-asides for known crapkit defect shapes and
the transforms follow analysis_tooldiff.

Where a tool counts a construct differently from crapkit's documented
reading, a named transform turns the tool's raw value into crapkit's, or the
function is set aside for that tool, and each such rule is a rulings row
pinned below by a hand case with the tool's raw value and crapkit's.
"""
import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import rust_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
# The tools list every function_item tree-sitter lists; closures get no row anywhere.
LISTED = tooldiff.listed_except(set())


def _rca(column: str):
    def read(root, paths, spans):
        return {key: metrics[column]
                for key, metrics in rust_adapters.rca_metrics(root, paths).items()}
    return read


# --- the transforms and set-asides, each a rulings row ------------------------------------------

def _own(fn, context, kinds) -> list:
    """Nodes of `kinds` that fn holds outside any nested function_item."""
    return [node for node in counters.own_nodes(fn, context.spec) if node.type in kinds]


def _wildcard(arm, data: bytes) -> bool:
    pattern = arm.child_by_field_name("pattern")
    return pattern is not None and data[pattern.start_byte:pattern.end_byte].strip() == b"_"


def wildcard_arms(fn, context) -> int:
    """A `_` arm with no guard: NIST SP 500-235 sec. 4.1 leaves the default out."""
    return sum(_wildcard(arm, context.data) for arm in _own(fn, context, {"match_arm"}))


def loops(fn, context) -> int:
    """A `loop` has no condition, so it is no binary decision."""
    return len(_own(fn, context, {"loop_expression"}))


def closures(fn, context) -> int:
    return len(_own(fn, context, {CLOSURE}))


def closure_decisions(fn, context) -> int:
    return sum(counters.ccn(closure, context.spec, context.data) - 1
               for closure in _own(fn, context, {CLOSURE}))


def guards(fn, context) -> int:
    return sum(arm.child_by_field_name("pattern") is not None and
               arm.child_by_field_name("pattern").child_by_field_name("condition") is not None
               for arm in _own(fn, context, {"match_arm"}))


def _in_test_module(fn, context) -> bool:
    """fn sits in a `#[cfg(test)]` module, whose functions cargo-crap never lists."""
    parent = fn.parent
    while parent is not None:
        if parent.type == "mod_item" and _cfg_test(parent, context.data):
            return True
        parent = parent.parent
    return False


def _cfg_test(module, data: bytes) -> bool:
    before = module.prev_named_sibling
    return before is not None and before.type == "attribute_item" and \
        b"cfg(test)" in data[before.start_byte:before.end_byte].replace(b" ", b"")


def _nested_function(fn, context) -> bool:
    parent = fn.parent
    while parent is not None and parent.type != "function_item":
        parent = parent.parent
    return parent is not None


LOGICAL = (b"&&", b"||")


def _operator(node, data: bytes):
    """A logical binary expression's operator, or None for any other node."""
    if node is None or node.type != "binary_expression":
        return None
    operator = node.child_by_field_name("operator")
    text = data[operator.start_byte:operator.end_byte]
    return text if text in LOGICAL else None


def _bare(node):
    while node is not None and node.type == "parenthesized_expression":
        node = node.named_children[0] if node.named_children else None
    return node


def _operands(node) -> list:
    return [_bare(node.child_by_field_name(side)) for side in ("left", "right")]


def _run(node, data: bytes) -> int:
    """How many like operators the run rooted at node chains."""
    op = _operator(node, data)
    return 1 + sum(_run(kid, data) for kid in _operands(node) if _operator(kid, data) == op)


def _splits_a_run(node, data: bytes) -> bool:
    op = _operator(node, data)
    return op is not None and any(
        _operator(kid, data) not in (None, op) and _run(kid, data) >= 2
        for kid in _operands(node))


def run_of_three(fn, context) -> bool:
    """AO-RCA-RUN-OF-THREE: `(a && b && c) || d` holds a run of three operands next
    to another operator, which rust-code-analysis charges twice."""
    return any(_splits_a_run(node, context.data)
               for node in _own(fn, context, {"binary_expression"}))


def _up(node):
    parent = node.parent
    while parent is not None and parent.type == "parenthesized_expression":
        parent = parent.parent
    return parent


def _root(node, data: bytes) -> bool:
    return _operator(node, data) is not None and _operator(_up(node), data) is None


def _in_condition(node) -> bool:
    parent = _up(node)
    return parent is not None and parent.type in ("if_expression", "while_expression")


def run_after_run(fn, context) -> bool:
    """AO-RCA-RUN-AFTER-RUN: a logical run outside an if or while condition that
    follows another run, which rust-code-analysis can leave uncounted."""
    roots = [node for node in _own(fn, context, {"binary_expression"})
             if _root(node, context.data)]
    return any(not _in_condition(node) for node in roots[1:])


def _negative(count):
    return lambda fn, context: -count(fn, context)


CLOSURE = "closure_expression"
NESTED = tooldiff.holds({"function_item"})
TOOLS = {
    "rust-code-analysis-ccn": Tool(
        "ccn_std", _rca("ccn"),
        transforms={"AO-RCA-WILDCARD-ARM": _negative(wildcard_arms),
                    "AO-RCA-LOOP": _negative(loops), "AO-RCA-CLOSURE-BASE": _negative(closures)},
        set_aside={"AO-RCA-NESTED-FN": NESTED}),
    "rust-code-analysis-cognitive": Tool(
        "cognitive", _rca("cognitive"),
        set_aside={"AO-RCA-NESTED-FN": NESTED, "AO-RCA-NESTED-FN-DEPTH": _nested_function,
                   "AO-RCA-RUN-OF-THREE": run_of_three,
                   "AO-RCA-RUN-AFTER-RUN": run_after_run}),
    "cargo-crap": Tool(
        "ccn_std", lambda root, paths, spans: rust_adapters.cargo_crap(root),
        transforms={"AO-CARGOCRAP-WILDCARD-ARM": _negative(wildcard_arms),
                    "AO-CARGOCRAP-LOOP": _negative(loops), "AO-CARGOCRAP-GUARD": guards,
                    "AO-CARGOCRAP-CLOSURE": closure_decisions},
        set_aside={"AO-CARGOCRAP-CFG-TEST": _in_test_module,
                   "AO-CARGOCRAP-NESTED-FN": _nested_function}),
}
ORACLE = {"rust-code-analysis-ccn": "rust-code-analysis-cli",
          "rust-code-analysis-cognitive": "rust-code-analysis-cli", "cargo-crap": "cargo-crap"}


@pytest.fixture(scope="module")
def rs_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "rust")
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("rs-probes"))


@pytest.fixture(scope="module")
def rs_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "ripgrep", (".rs",))
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("rs-corpus"))


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_rust_probes_match_the_rust_tools(name, rs_probes, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, rs_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_ripgrep_matches_the_rust_tools(name, rs_corpus, oracle):
    oracle(ORACLE[name])
    outcome = tooldiff.differential(name, TOOLS[name], LISTED, rs_corpus)

    assert outcome.problems == []
    assert outcome.compared > 100


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

PICK = ("pub fn pick(k: i32) -> i32 {\n    match k {\n        1 => 10,\n        2 => 20,\n"
        "        3 => 30,\n        _ => 0,\n    }\n}\n")
SPIN = ("pub fn spin(n: i32) -> i32 {\n    let mut i = 0;\n    loop {\n        if i > n {\n"
        "            break;\n        }\n        i += 1;\n    }\n    i\n}\n")
CLOSURE_IF = ("pub fn closure_if(v: Vec<i32>) -> usize {\n    v.iter().filter(|x| {\n"
              "        if **x > 0 {\n            return true;\n        }\n        false\n"
              "    }).count()\n}\n")
GUARD = ("pub fn guard(k: i32, y: bool) -> i32 {\n    match k {\n        1 if y => 10,\n"
         "        2 => 20,\n        3 => 30,\n    }\n}\n")
NESTED_FN = ("pub fn outer(a: i32) -> i32 {\n    fn inner(b: i32) -> i32 {\n        if b > 0 {\n"
             "            return 1;\n        }\n        0\n    }\n    if a > 0 {\n"
             "        return inner(a);\n    }\n    0\n}\n")
CFG_TEST = ("#[cfg(test)]\nmod tests {\n    fn helper(a: i32) -> i32 {\n        if a > 0 {\n"
            "            return 1;\n        }\n        0\n    }\n}\n")
RUN_OF_THREE = ("pub fn run3(a: bool, b: bool, c: bool, d: bool) -> bool {\n"
                "    (a && b && c) || d\n}\n")
RUN_AFTER_RUN = ("pub fn then_and(k: bool, a: bool, b: bool) -> bool {\n    if k && a {\n"
                 "        return true;\n    }\n    a && b\n}\n")
# ruling id -> (tool, source, start line of the function the case judges).
HAND = {
    "AO-RCA-WILDCARD-ARM": ("rust-code-analysis-ccn", PICK, 1),
    "AO-RCA-LOOP": ("rust-code-analysis-ccn", SPIN, 1),
    "AO-RCA-CLOSURE-BASE": ("rust-code-analysis-ccn", CLOSURE_IF, 1),
    "AO-RCA-NESTED-FN": ("rust-code-analysis-ccn", NESTED_FN, 1),
    "AO-RCA-NESTED-FN-DEPTH": ("rust-code-analysis-cognitive", NESTED_FN, 2),
    "AO-RCA-RUN-OF-THREE": ("rust-code-analysis-cognitive", RUN_OF_THREE, 1),
    "AO-RCA-RUN-AFTER-RUN": ("rust-code-analysis-cognitive", RUN_AFTER_RUN, 1),
    "AO-CARGOCRAP-WILDCARD-ARM": ("cargo-crap", PICK, 1),
    "AO-CARGOCRAP-LOOP": ("cargo-crap", SPIN, 1),
    "AO-CARGOCRAP-GUARD": ("cargo-crap", GUARD, 1),
    "AO-CARGOCRAP-CLOSURE": ("cargo-crap", CLOSURE_IF, 1),
    "AO-CARGOCRAP-CFG-TEST": ("cargo-crap", CFG_TEST, 3),
    "AO-CARGOCRAP-NESTED-FN": ("cargo-crap", NESTED_FN, 2),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"{ruling}/src/case.rs": source.encode() for ruling, (_, source, _) in HAND.items()}
    return files, measure_set(files), tmp_path_factory.mktemp("rs-hand")


def _case(ruling_id: str, hand, tool: Tool) -> tuple:
    """(crapkit's value, the tool's raw value or 'absent', fn, context), with the case
    written alone under its own root, since cargo-crap walks a whole directory."""
    files, measured, base = hand
    path, (_, _, start) = f"{ruling_id}/src/case.rs", HAND[ruling_id]
    root = tooldiff.written({path: files[path]}, base / ruling_id)
    raw = tool.read(root, [path], tooldiff.spans({path: files[path]})).get((path, start),
                                                                           "absent")
    crapkit = next(row for row in measured.in_file(path) if row["start"] == start)[tool.column]
    return crapkit, raw, *_function_at(path, files[path], start)


def _function_at(path: str, data: bytes, start: int) -> tuple:
    return next((fn, context) for fn, context in LISTED(path, data)
                if counters.start_line(fn) == start)


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_rust_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name = HAND[ruling_id][0]
    oracle(ORACLE[name])
    tool = TOOLS[name]
    crapkit, raw, fn, context = _case(ruling_id, hand, tool)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_rust_rule_has_a_hand_case():
    assert sorted(set(tooldiff.rules(TOOLS))) == sorted(HAND)
