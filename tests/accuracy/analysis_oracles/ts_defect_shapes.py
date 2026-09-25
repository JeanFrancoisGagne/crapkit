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


def _previous_sibling_up(node, comments):
    """The nearest earlier node that is not a comment, climbing out of blocks."""
    while node is not None:
        before = node.prev_sibling
        while before is not None and before.type in comments:
            before = before.prev_sibling
        if before is not None:
            return before
        node = node.parent
    return None


def _previous_leaf(node, comments):
    node = _previous_sibling_up(node, comments)
    while node is not None and node.child_count:
        node = node.children[-1]
    return node


def _while_loop(node, context: Context) -> bool:
    return node.type in context.spec.loops and node.children[:1] and (
        node.children[0].type == "while")


def while_after_brace(fn, context: Context) -> bool:
    """A while loop whose previous token is a closing brace."""
    whiles = (node for node in own(fn, context) if _while_loop(node, context))
    comments = context.spec.comments
    return any((_previous_leaf(node, comments) or node).type == "}" for node in whiles)


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


# --- Rust -------------------------------------------------------------------------------------

def let_else_in(fn, context: Context) -> bool:
    return any(counters.let_else(node) for node in own(fn, context))


def _empty_closure(node, context: Context) -> bool:
    params = node.child_by_field_name("parameters")
    return node.type == "closure_expression" and params is not None and (
        context.text(params) == b"||")


def empty_closure(fn, context: Context) -> bool:
    return any(_empty_closure(node, context) for node in own(fn, context))


def maybe_bound(fn, context: Context) -> bool:
    """A ?Sized (or any ?Trait) bound in fn's generics or where clause."""
    return any(node.type in ("type_parameters", "where_clause") and b"?" in context.text(node)
               for node in fn.children)


def signature_before(fn, context: Context) -> bool:
    """fn is the first function after a trait method with no body."""
    if "sig" not in context.facts:
        context.facts["sig"] = _after_signatures(context)
    return fn.start_byte in context.facts["sig"]


def _first_after(starts: list, end: int):
    return next((start for start in starts if start > end), None)


def _after_signatures(context: Context) -> set:
    signatures = [node.end_byte for node in _walk(context.tree.root_node)
                  if node.type == "function_signature_item"]
    starts = sorted(fn.start_byte for fn in counters.functions(context.tree, context.spec))
    return {_first_after(starts, end) for end in signatures}


def signature_line(context: Context, start: int) -> bool:
    return any(node.type == "function_signature_item" and node.start_point[0] + 1 == start
               for node in _walk(context.tree.root_node))


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


def _structures(context: Context) -> frozenset:
    return context.spec.ifs | context.spec.loops | context.spec.switches


def _statement(node):
    """The statement a structure stands in (Rust wraps an if in an expression statement)."""
    parent = node.parent
    return parent if parent is not None and parent.type == "expression_statement" else node


def _after_statement(node, kinds) -> bool:
    """A structure with a statement before it in the same block."""
    before = _statement(node).prev_named_sibling
    return node.type in kinds and before is not None and before.type not in kinds


def _nested_after_statement(fn, context: Context) -> list:
    kinds = _structures(context)
    return [node for node in own(fn, context) if _after_statement(node, kinds)
            and any(kind in kinds for kind in _ancestor_types(node, fn))]


def _holds_structure(node, kinds) -> bool:
    return any(inner.type in kinds for inner in _walk(node) if inner != node)


def _semicolon_header(node, kinds) -> bool:
    return any(child.type == ";" for child in node.children) and _holds_structure(node, kinds)


def _semicolon_headers(fn, context: Context) -> list:
    """Structures with a `;` in their header (a C for, a Go if with an initializer)
    that hold another structure."""
    kinds = _structures(context)
    return [node for node in own(fn, context)
            if node.type in kinds and _semicolon_header(node, kinds)]


def statement_then_structure(fn, context: Context) -> bool:
    """AO-ND-SIBLING: an if ends before a structure that follows a statement inside
    another structure, or before a structure with a `;` in its header."""
    ifs = [node.end_byte for node in own(fn, context) if node.type in context.spec.ifs]
    later = _nested_after_statement(fn, context) + _semicolon_headers(fn, context)
    return any(end <= node.start_byte for node in later for end in ifs)


def arm_jump(fn, context: Context) -> bool:
    """A Rust match arm whose value is an unlabeled break or continue."""
    jumps = ("break_expression", "continue_expression")
    return any(node.type == "match_arm" and any(kid.type in jumps for kid in node.named_children)
               for node in own(fn, context))


BODY_BLOCKS = frozenset({"compound_statement", "block", "block_expression", "statements",
                         "labeled_statement"})
PREPROCESSOR = frozenset({"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else",
                          "preproc_elifdef"})


def _quoted_substitution(node, context: Context) -> bool:
    return node.type == "string" and any(
        inner.type == "command_substitution" and (b"&&" in context.text(inner)
                                                  or b"||" in context.text(inner))
        for inner in _walk(node))


def quoted_substitution(fn, context: Context) -> bool:
    """A && or || inside a double-quoted command substitution."""
    return any(_quoted_substitution(node, context) for node in own(fn, context))


def optional_mark(fn, context: Context) -> bool:
    """A Zig optional type (`?T`) or unwrap (`x.?`) anywhere in fn, its signature too."""
    return any(not node.is_named and node.type in ("?", ".?") for node in _walk(fn))


def payload_else_if(fn, context: Context) -> bool:
    """A Zig `else |err| if ...`."""
    return any(node.type == "else_clause" and any(kid.type == "payload" for kid in node.children)
               for node in own(fn, context))


def _braceless(node, context: Context) -> bool:
    body = [kid for kid in node.named_children if kid.type in BODY_BLOCKS]
    return node.type in context.spec.ifs and not body


def _holders(fn, context: Context) -> list:
    kinds = _structures(context)
    return [node.start_byte for node in own(fn, context)
            if node.type in kinds and _holds_structure(node, kinds)]


def braceless_then_structure(fn, context: Context) -> bool:
    """AO-ND-BRACELESS: a braceless if ends before a structure that holds another."""
    ends = [node.end_byte for node in own(fn, context) if _braceless(node, context)]
    return any(end <= start for end in ends for start in _holders(fn, context))


def _valued_break(node) -> bool:
    """A Zig `break value` with no label."""
    kids = [kid.type for kid in node.named_children]
    return node.type == "break_expression" and bool(kids) and "break_label" not in kids


JUMPS = frozenset({"break_expression", "continue_expression"})


def _jump_prong(node) -> bool:
    return node.type == "switch_case" and any(kid.type in JUMPS for kid in node.named_children)


def prong_jump(fn, context: Context) -> bool:
    """A Zig switch prong whose value is an unlabeled break or continue, or a break
    with a value and no label."""
    return any(_jump_prong(node) or _valued_break(node) for node in own(fn, context))


def struct_return(fn, context: Context) -> bool:
    """A Zig function whose return type is an anonymous struct."""
    return any(child.type == "struct_declaration" for child in fn.children)


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
    Shape("AO-ND-SIBLING", ND_LANGUAGES, NESTING, statement_then_structure),
    Shape("AO-ND-BRACELESS", ND_LANGUAGES, NESTING, braceless_then_structure),
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
    Shape("AO-COG-KEYWORD-NAMES-BRACE", _all("go", "rust"), COGNITIVE,
          named_like({b"do", b"foreach", b"catch", b"except", b"while", b"and", b"or"})),
    Shape("AO-COG-KEYWORD-NAMES-BRACE", _all("zig"), COGNITIVE,
          named_like({b"do", b"foreach", b"except"})),
    Shape("AO-N-CLOSURE", ND_LANGUAGES, NESTING,
          lambda fn, c: has_type(fn, c, c.spec.lambdas)),
    Shape("AO-LOOP-NO-CONDITION", _all("go", "c", "cpp", "objc", "java"), CCN,
          lambda fn, c: any(counters._conditionless_for(node) for node in own(fn, c))),
    # params
    Shape("AO-C-PREPROC", _all("c", "cpp", "objc"), CCN,
          lambda fn, c: has_type(fn, c, PREPROCESSOR)),
    Shape("AO-C-PREPROC-COG", _all("c", "cpp", "objc"), COGNITIVE + NESTING,
          lambda fn, c: has_type(fn, c, PREPROCESSOR)),
    Shape("AO-C-PREPROC-NLOC", _all("c", "cpp", "objc"), NLOC,
          lambda fn, c: has_type(fn, c, PREPROCESSOR)),
    Shape("AO-SH-QUOTED-SUBST", _all("shell"), CCN + COGNITIVE, quoted_substitution),
    Shape("AO-SH-HEREDOC-NLOC", _all("shell"), NLOC,
          lambda fn, c: has_type(fn, c, {"heredoc_body", "heredoc_redirect"})),
    Shape("AO-ZIG-STRUCT-RETURN", _all("zig"), EVERY, struct_return),
    Shape("AO-ZIG-FN-PARAM", _all("zig"), PARAMS,
          parameter_holds({"function_signature", "function_type"})),
    Shape("AO-ZIG-TRY-ND", _all("zig"), NESTING,
          lambda fn, c: has_type(fn, c, {"try_expression", "catch_expression"})),
    Shape("AO-ZIG-OPTIONAL", _all("zig"), COGNITIVE + NESTING, optional_mark),
    Shape("AO-ZIG-PAYLOAD-ELSE", _all("zig"), COGNITIVE, payload_else_if),
    Shape("AO-ZIG-ARM-JUMP", _all("zig"), COGNITIVE, prong_jump),
    Shape("AO-ZIG-SWITCH-MOD", _all("zig"), MOD, has_switch),
    Shape("AO-C-VOID-FNPTR-PARAM", _all("c", "cpp", "objc"), PARAMS, void_function_pointer_param),
    # Go
    Shape("AO-GO-FUNC-TYPE", _all("go"), EVERY, near_loose_function_type),
    Shape("AO-GO-FUNC-TYPE", _all("go"), PARAMS, method_after_loose_function_type),
    Shape("AO-GO-INTERFACE-PARAM", _all("go"), PARAMS,
          parameter_holds({"interface_type", "struct_type"})),
    Shape("AO-GO-FUNC-PARAM", _all("go"), PARAMS, parameter_holds({"function_type"})),
    Shape("AO-GO-ND-INIT", _all("go"), NESTING, if_initializer),
    Shape("AO-COG-RUNS-GO", _all("go"), COGNITIVE, negated_call_run),
    # Rust
    Shape("AO-RS-TRY-COG", _all("rust"), COGNITIVE, lambda fn, c: has_type(fn, c, {"try_expression"})),
    Shape("AO-RS-TRY-ND", _all("rust"), NESTING, lambda fn, c: has_type(fn, c, {"try_expression"})),
    Shape("AO-RS-LETELSE", _all("rust"), CCN, let_else_in),
    Shape("AO-RS-LETELSE-ND", _all("rust"), NESTING, let_else_in),
    Shape("AO-RS-EMPTY-CLOSURE", _all("rust"), CCN + NESTING, empty_closure),
    Shape("AO-RS-EMPTY-CLOSURE-COG", _all("rust"), COGNITIVE, empty_closure),
    Shape("AO-RS-MAYBE-BOUND", _all("rust"), CCN + COGNITIVE + NESTING, maybe_bound),
    Shape("AO-RS-TUPLE-PARAM", _all("rust"), PARAMS, parameter_holds({"tuple_type"})),
    Shape("AO-RS-TRAIT-SIG", _all("rust"), EVERY, signature_before),
    Shape("AO-RS-LOOP-COG", _all("rust"), COGNITIVE,
          lambda fn, c: has_type(fn, c, {"loop_expression"})),
    Shape("AO-RS-LOOP-ND", _all("rust"), NESTING,
          lambda fn, c: has_type(fn, c, {"loop_expression"})),
    Shape("AO-COG-GUARD", _all("rust"), COGNITIVE,
          lambda fn, c: any(counters.match_guard(node) for node in own(fn, c))),
    Shape("AO-RS-LETELSE-COG", _all("rust"), COGNITIVE, let_else_in),
    Shape("AO-RS-ARM-JUMP", _all("rust"), COGNITIVE, arm_jump),
    Shape("AO-RS-WHERE", _all("rust"), CCN,
          lambda fn, c: any(node.type == "where_clause" for node in fn.children)),
]


def reasons(fn, context: Context, columns: tuple) -> list[str]:
    """The rulings ids whose shape fn holds, among those covering `columns`."""
    wanted = set(columns) | {ROW}
    return sorted({shape.ruling for shape in SHAPES
                   if context.language in shape.languages and wanted & set(shape.columns)
                   and shape.holds(fn, context)})


def extra_explained(context: Context, start: int) -> bool:
    """Whether a crapkit row at a line the oracle lists no function on comes from a
    recorded shape: a phantom row that starts at a loose Go function type, or at a Rust
    trait method with no body."""
    explain = EXTRA_ROWS.get(context.language)
    return explain is not None and explain(context, start)


def _loose_line(context: Context, start: int) -> bool:
    lines = {context.data[:loose].count(b"\n") + 1 for loose in loose_function_types(context)}
    return start in lines


EXTRA_ROWS = {"go": _loose_line, "rust": signature_line}
