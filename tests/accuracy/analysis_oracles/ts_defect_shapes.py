"""The shapes crapkit reads differently from the tree-sitter counters, one
detector per rulings row, for the brace-language and shell differentials.

A differential compares crapkit with the counters only where no recorded
difference applies. Each Shape names its rulings row (a documented definition
or an open defect whose hand probe pins it), the languages and columns it
covers, and a predicate over one function's tree-sitter node. `row` in the
columns means the function's row itself: its start, its end and whether
crapkit lists it at all.

No crapkit import.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from accuracy.analysis_oracles.oracles import treesitter_counters as counters

ROW = "row"
NESTING = ("nesting",)
COGNITIVE = ("cognitive",)
CCN = ("ccn_std", "ccn_mod")
MOD = ("ccn_mod",)
PARAMS = ("params",)
NLOC = ("nloc",)
EVERY = (ROW, "end", "ccn_std", "ccn_mod", "cognitive", "nesting", "nloc", "params")


@dataclass
class Context:
    """One file: its language, grammar spec, bytes and tree."""
    language: str
    spec: object
    data: bytes
    tree: object
    facts: dict = field(default_factory=dict)

    def text(self, node) -> bytes:
        return self.data[node.start_byte:node.end_byte]


@dataclass(frozen=True)
class Shape:
    ruling: str
    languages: frozenset
    columns: tuple
    holds: object  # (fn, context) -> bool


# --- queries over one function -----------------------------------------------------------------

def own(fn, context: Context):
    return counters.own_nodes(fn, context.spec)


def has_type(fn, context: Context, kinds) -> bool:
    return any(node.type in kinds for node in own(fn, context))


def logical(node, context: Context) -> bool:
    return any(not child.is_named and child.type in context.spec.logical
               for child in node.children)


def has_logical(fn, context: Context) -> bool:
    return any(logical(node, context) for node in own(fn, context))


def _ancestor_types(node, fn) -> list:
    found, node = [], node.parent
    while node is not None and node != fn:
        found.append(node.type)
        node = node.parent
    return found


def nested_loop(fn, context: Context) -> bool:
    loops = context.spec.loops
    return any(node.type in loops and any(kind in loops for kind in _ancestor_types(node, fn))
               for node in own(fn, context))


def switch_cases(fn, context: Context) -> list:
    return [node for node in own(fn, context) if node.type in context.spec.cases]


def has_switch(fn, context: Context) -> bool:
    return has_type(fn, context, context.spec.switches)


NAMES = frozenset({"identifier", "simple_identifier", "command_name", "word"})


def _uses(fn, name, context: Context):
    return (context.text(node) for node in own(fn, context)
            if node.type in NAMES and node != name)


def calls_itself(fn, context: Context) -> bool:
    """fn's name appears again inside it: a call to itself, or a label spelled so."""
    name = counters.name_node(fn)
    if name is None:
        return False
    wanted = context.text(name)
    return any(used == wanted for used in _uses(fn, name, context))


def _previous_sibling_up(node):
    while node is not None and node.prev_sibling is None:
        node = node.parent
    return None if node is None else node.prev_sibling


def _previous_leaf(node):
    node = _previous_sibling_up(node)
    while node is not None and node.child_count:
        node = node.children[-1]
    return node


def _while_loop(node, context: Context) -> bool:
    return node.type in context.spec.loops and node.children[:1] and (
        node.children[0].type == "while")


def while_after_brace(fn, context: Context) -> bool:
    """A while loop whose previous token is a closing brace."""
    whiles = (node for node in own(fn, context) if _while_loop(node, context))
    return any((_previous_leaf(node) or node).type == "}" for node in whiles)


def default_prong(fn, context: Context) -> bool:
    return any(counters.is_default(case, context.data) for case in switch_cases(fn, context))


def _declared_parameters(fn, context: Context) -> list:
    listed = counters._parameter_list(fn)
    kids = [] if listed is None else listed.named_children
    return [kid for kid in kids if kid.type in context.spec.parameters]


def void_function_pointer_param(fn, context: Context) -> bool:
    """One parameter, pointing to a function or block that returns void."""
    kids = _declared_parameters(fn, context)
    text = context.text(kids[0]) if len(kids) == 1 else b""
    return text.startswith(b"void") and b"(" in text


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


# --- Go ---------------------------------------------------------------------------------------

PARAMETER_HOLDERS = frozenset({"parameter_list", "parameter_declaration",
                               "variadic_parameter_declaration"})


def _loose_function_type(node) -> bool:
    """A Go function type outside every parameter list and every result."""
    if node.type != "function_type":
        return False
    parent = node.parent
    while parent is not None:
        if parent.type in PARAMETER_HOLDERS:
            return False
        parent = parent.parent
    holder = node.parent
    return holder is None or holder.child_by_field_name("result") != node


def loose_function_types(context: Context) -> list:
    """The start bytes of a Go file's loose function types, cached per file."""
    if "loose" not in context.facts:
        context.facts["loose"] = sorted(node.start_byte for node in _walk(context.tree.root_node)
                                        if _loose_function_type(node))
    return context.facts["loose"]


def _next_function_starts(context: Context) -> set:
    """For each loose function type, the start byte of the first function after it."""
    starts = sorted(fn.start_byte for fn in counters.functions(context.tree, context.spec))
    return {next((start for start in starts if start > loose), None)
            for loose in loose_function_types(context)}


def near_loose_function_type(fn, context: Context) -> bool:
    """fn holds a loose function type, or is the first function after one."""
    if "next" not in context.facts:
        context.facts["next"] = _next_function_starts(context)
    holds = any(fn.start_byte <= loose < fn.end_byte for loose in loose_function_types(context))
    return holds or fn.start_byte in context.facts["next"]


def _parameter_nodes(fn, context: Context) -> list:
    listed = counters._parameter_list(fn)
    return [] if listed is None or listed == fn else list(_walk(listed))


def parameter_holds(kinds):
    return lambda fn, context: any(node.type in kinds for node in _parameter_nodes(fn, context))


def if_initializer(fn, context: Context) -> bool:
    return any(node.type == "if_statement" and node.child_by_field_name("initializer") is not None
               for node in own(fn, context))


def _call_with_commas(node) -> bool:
    return node.type == "argument_list" and len(node.named_children) > 1


def _negation(node) -> bool:
    return node.type == "unary_expression" and node.children[:1] and node.children[0].type == "!"


def _negated_calls_with_commas(node) -> bool:
    negated = (inner for inner in _walk(node) if _negation(inner))
    return any(_call_with_commas(args) for inner in negated for args in _walk(inner))


def negated_call_run(fn, context: Context) -> bool:
    """A logical chain holding a negated call with two or more arguments."""
    return any(logical(node, context) and _negated_calls_with_commas(node)
               for node in own(fn, context))


def _top_level(start: int, context: Context) -> bool:
    """Outside every function and every struct field."""
    node = context.tree.root_node.descendant_for_byte_range(start, start + 1)
    while node is not None:
        if node.type == "field_declaration" or node.type in context.spec.functions:
            return False
        node = node.parent
    return True


def _top_loose(context: Context) -> list:
    if "top" not in context.facts:
        context.facts["top"] = [start for start in loose_function_types(context)
                                if _top_level(start, context)]
    return context.facts["top"]


def method_after_loose_function_type(fn, context: Context) -> bool:
    """A method after a package-level loose function type reads its receiver as its
    parameters."""
    top = _top_loose(context)
    return fn.type == "method_declaration" and bool(top) and fn.start_byte > top[0]


# --- every brace language -----------------------------------------------------------------------

def run_over_lines(fn, context: Context) -> bool:
    """A logical sequence whose operators sit on more than one line."""
    return any(logical(node, context) and node.start_point[0] != node.end_point[0]
               and (node.parent is None or not logical(node.parent, context))
               for node in own(fn, context))


def named_like(words):
    """Whether fn holds an identifier spelled as one of `words`."""
    kinds = ("identifier", "field_identifier", "simple_identifier", "type_identifier")
    return lambda fn, context: any(node.type in kinds and context.text(node) in words
                                   for node in own(fn, context))


def _after_else_token(node) -> list:
    kids = list(node.children)
    marks = [index for index, kid in enumerate(kids) if kid.type == "else"]
    return kids[marks[0] + 1:] if marks else []


def _without_else(clause) -> list:
    return [inner for inner in clause.children if inner.type != "else"]


def _in_else_clauses(node) -> list:
    clauses = [kid for kid in node.children if kid.type == "else_clause"]
    return [inner for clause in clauses for inner in _without_else(clause)]


def _after_else(node) -> list:
    """The children of an if node after its else token, and inside its else clause."""
    return _after_else_token(node) + _in_else_clauses(node)


def _else_parts(node, context: Context) -> list:
    """The plain-else bodies of one if node (an else if is not one)."""
    return [part for part in _after_else(node) if part.type not in context.spec.ifs]


def structure_in_else(fn, context: Context) -> bool:
    """An if, loop or switch inside a plain else's body."""
    kinds = context.spec.ifs | context.spec.loops | context.spec.switches
    return any(node.type in context.spec.ifs and any(
        inner.type in kinds for part in _else_parts(node, context) for inner in _walk(part))
               for node in own(fn, context))


def _all(*languages: str) -> frozenset:
    return frozenset(languages)


C_FAMILY = _all("c", "cpp", "objc", "java")
ND_LANGUAGES = _all("c", "cpp", "objc", "java", "go", "rust", "swift", "zig", "shell")
SHAPES = [
    # nesting: lizard's ND column against the Sonar B2 depth
    Shape("AO-N-CONDITION-C", C_FAMILY, NESTING, has_logical),
    Shape("AO-N-LOGICAL-NO-PARENS", _all("go", "rust", "swift", "shell"), NESTING, has_logical),
    Shape("AO-N-CASE-C", C_FAMILY, NESTING, lambda fn, c: bool(switch_cases(fn, c))),
    Shape("AO-N-CASES-STACK", _all("go", "swift"), NESTING,
          lambda fn, c: len(switch_cases(fn, c)) > 1),
    Shape("AO-N-MATCH", _all("rust", "zig"), NESTING, has_switch),
    Shape("AO-N-SH-CASE", _all("shell"), NESTING, has_switch),
    Shape("AO-ND-LOOPS", ND_LANGUAGES, NESTING, nested_loop),
    Shape("AO-SH-NESTING-DEEP", _all("shell"), NESTING,
          lambda fn, c: has_type(fn, c, c.spec.ifs | c.spec.loops)),
    # cognitive
    Shape("AO-COG-RECURSION-BRACE", _all("go", "java", "rust", "shell", "swift", "zig"),
          COGNITIVE, calls_itself),
    Shape("AO-COG-WHILE-AFTER-BRACE", ND_LANGUAGES, COGNITIVE, while_after_brace),
    Shape("AO-COG-RUNS-LINES", ND_LANGUAGES, COGNITIVE, run_over_lines),
    Shape("AO-SWIFT-COG-REPEAT", _all("swift"), COGNITIVE,
          lambda fn, c: has_type(fn, c, {"repeat_while_statement"})),
    Shape("AO-SWIFT-COG-DO", _all("swift"), COGNITIVE,
          lambda fn, c: has_type(fn, c, {"do_statement"})),
    Shape("AO-ZIG-COG-ELSE-PRONG", _all("zig"), COGNITIVE, default_prong),
    # ccn
    Shape("AO-ZIG-ELSE-PRONG", _all("zig"), CCN, default_prong),
    Shape("AO-SH-CASE-DEFAULT", _all("shell"), CCN, default_prong),
    Shape("AO-SH-CASE-MOD", _all("shell"), MOD, has_switch),
    Shape("AO-RS-MATCH-MOD", _all("rust"), MOD, has_switch),
    Shape("AO-N-ELSE", ND_LANGUAGES, NESTING, structure_in_else),
    Shape("AO-ND-DEF", _all("c", "cpp", "objc", "java", "go", "rust", "swift", "zig"), NESTING,
          named_like({b"def", b"foreach", b"try", b"catch"})),
    Shape("AO-COG-KEYWORD-NAMES-BRACE", _all("go", "rust", "zig"), COGNITIVE,
          named_like({b"do", b"foreach", b"catch", b"except", b"while"})),
    # params
    Shape("AO-C-VOID-FNPTR-PARAM", _all("c", "cpp", "objc"), PARAMS, void_function_pointer_param),
    # Go
    Shape("AO-GO-FUNC-TYPE", _all("go"), EVERY, near_loose_function_type),
    Shape("AO-GO-FUNC-TYPE", _all("go"), PARAMS, method_after_loose_function_type),
    Shape("AO-GO-INTERFACE-PARAM", _all("go"), PARAMS,
          parameter_holds({"interface_type", "struct_type"})),
    Shape("AO-GO-FUNC-PARAM", _all("go"), PARAMS, parameter_holds({"function_type"})),
    Shape("AO-GO-ND-INIT", _all("go"), NESTING, if_initializer),
    Shape("AO-COG-RUNS-GO", _all("go"), COGNITIVE, negated_call_run),
]


def reasons(fn, context: Context, columns: tuple) -> list[str]:
    """The rulings ids whose shape fn holds, among those covering `columns`."""
    wanted = set(columns) | {ROW}
    return sorted({shape.ruling for shape in SHAPES
                   if context.language in shape.languages and wanted & set(shape.columns)
                   and shape.holds(fn, context)})


def extra_explained(context: Context, start: int) -> bool:
    """Whether a crapkit row at a line the oracle lists no function on comes from a
    recorded shape (a phantom row that starts at a loose Go function type)."""
    if context.language != "go":
        return False
    lines = {context.data[:loose].count(b"\n") + 1 for loose in loose_function_types(context)}
    return start in lines
