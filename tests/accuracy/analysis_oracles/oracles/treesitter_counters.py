"""Functions, spans, parameter counts, nloc, ccn_std and ccn_mod of the brace
languages and shell, read with tree-sitter 0.26.0 and the grammars pins.toml
names.

A function is what the language reference calls a function or method
declaration; SPECS names each grammar's node types. Its start is the line that
holds its name, its end the last line of its node, its params the entries of
its declared parameter list, and its nloc the lines of its node that hold a
token other than a comment (lizard 1.24.0 README: NLOC counts lines of code
without comments). A function declared inside another counts on its own row
and leaves the enclosing one.

Decision points follow NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1, not
a lizard reader:
- ccn_std = 1 + each if, loop, catch clause, conditional operator, guard,
  short-circuit operator (&&, ||, and, or, orelse, ??) and each case label,
  the default excluded;
- ccn_mod = the same with each switch counted once whatever its cases (lizard
  README option -m).

No crapkit import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib

from tree_sitter import Language, Parser


@dataclass(frozen=True)
class Spec:
    """One grammar's node types, by the role the McCabe and Sonar texts give them."""
    grammar: str
    functions: frozenset          # declarations crapkit lists as rows
    ifs: frozenset                # if, else if (an elif clause is one)
    loops: frozenset
    switches: frozenset
    cases: frozenset              # one per case label; is_default() finds the default
    parameters: frozenset
    catches: frozenset = frozenset()
    ternaries: frozenset = frozenset()
    guards: frozenset = frozenset()       # Swift guard: an if whose body is its else
    coalescing: frozenset = frozenset()   # ??, orelse: a decision, no cognitive
    early_returns: frozenset = frozenset()  # Rust ?, Zig try, let-else: a decision
    chains: frozenset = frozenset()       # Swift a?.b: each `?` under one is a decision
    lambdas: frozenset = frozenset()      # closures crapkit keeps in the enclosing row
    logical: frozenset = frozenset({"&&", "||"})
    comments: frozenset = frozenset({"comment"})
    jumps: frozenset = frozenset()        # goto, and break or continue with a label
    labels: frozenset = frozenset()       # the label node a jump names

    @property
    def decisions(self) -> frozenset:
        return (self.ifs | self.loops | self.catches | self.ternaries | self.guards
                | self.coalescing | self.early_returns) - self.unconditional

    unconditional: frozenset = frozenset()  # a loop with no condition: no decision


def _f(*names: str) -> frozenset:
    return frozenset(names)


C_LOOPS = _f("for_statement", "while_statement", "do_statement")
C_FAMILY = dict(ifs=_f("if_statement"), switches=_f("switch_statement"),
                cases=_f("case_statement"), ternaries=_f("conditional_expression"),
                jumps=_f("goto_statement"))
SPECS = {
    "go": Spec("tree_sitter_go", _f("function_declaration", "method_declaration", "func_literal"),
               _f("if_statement"), _f("for_statement"),
               _f("expression_switch_statement", "type_switch_statement", "select_statement"),
               _f("expression_case", "type_case", "communication_case"),
               _f("parameter_declaration", "variadic_parameter_declaration"),
               jumps=_f("goto_statement", "break_statement", "continue_statement"),
               labels=_f("label_name")),
    "rust": Spec("tree_sitter_rust", _f("function_item"), _f("if_expression"),
                 _f("for_expression", "while_expression", "loop_expression"),
                 _f("match_expression"), _f("match_arm"),
                 _f("parameter", "self_parameter", "variadic_parameter"),
                 early_returns=_f("try_expression"), unconditional=_f("loop_expression"),
                 lambdas=_f("closure_expression"),
                 comments=_f("line_comment", "block_comment"),
                 jumps=_f("break_expression", "continue_expression"), labels=_f("label")),
    "java": Spec("tree_sitter_java", _f("method_declaration", "constructor_declaration",
                                        "compact_constructor_declaration"),
                 _f("if_statement"), _f("for_statement", "enhanced_for_statement",
                                         "while_statement", "do_statement"),
                 _f("switch_expression"), _f("switch_label"),
                 _f("formal_parameter", "spread_parameter"),
                 catches=_f("catch_clause"), ternaries=_f("ternary_expression"),
                 lambdas=_f("lambda_expression"),
                 comments=_f("line_comment", "block_comment"),
                 jumps=_f("break_statement", "continue_statement"), labels=_f("identifier")),
    "c": Spec("tree_sitter_c", _f("function_definition"), loops=C_LOOPS,
              parameters=_f("parameter_declaration", "variadic_parameter"), **C_FAMILY),
    "cpp": Spec("tree_sitter_cpp", _f("function_definition"), loops=C_LOOPS | _f("for_range_loop"),
                parameters=_f("parameter_declaration", "optional_parameter_declaration",
                              "variadic_parameter_declaration", "variadic_parameter"),
                catches=_f("catch_clause"), lambdas=_f("lambda_expression"), **C_FAMILY),
    "objc": Spec("tree_sitter_objc", _f("function_definition", "method_definition"),
                 loops=C_LOOPS | _f("for_in_statement"),
                 parameters=_f("parameter_declaration", "variadic_parameter", "method_parameter"),
                 catches=_f("catch_clause"), lambdas=_f("block_literal"), **C_FAMILY),
    "swift": Spec("tree_sitter_swift", _f("function_declaration", "init_declaration",
                                          "deinit_declaration"),
                  _f("if_statement"), _f("for_statement", "while_statement",
                                         "repeat_while_statement"),
                  _f("switch_statement"), _f("switch_entry"), _f("parameter"),
                  catches=_f("catch_block"), ternaries=_f("ternary_expression"),
                  guards=_f("guard_statement"), coalescing=_f("nil_coalescing_expression"),
                  chains=_f("navigation_expression", "call_expression"),
                  lambdas=_f("lambda_literal"),
                  comments=_f("comment", "multiline_comment"),
                  jumps=_f("control_transfer_statement"), labels=_f("simple_identifier")),
    "zig": Spec("tree_sitter_zig", _f("function_declaration"), _f("if_statement", "if_expression"),
                _f("for_statement", "for_expression", "while_statement", "while_expression"),
                _f("switch_expression"), _f("switch_case"), _f("parameter"),
                catches=_f("catch_expression"), early_returns=_f("try_expression"),
                logical=_f("and", "or", "orelse"),
                jumps=_f("break_expression", "continue_expression"), labels=_f("break_label")),
    "shell": Spec("tree_sitter_bash", _f("function_definition"), _f("if_statement", "elif_clause"),
                  _f("for_statement", "c_style_for_statement", "while_statement"),
                  _f("case_statement"), _f("case_item"), _f(),
                  ternaries=_f("ternary_expression")),
}
SUFFIXES = {".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".cc": "cpp", ".cpp": "cpp",
            ".cxx": "cpp", ".h": "cpp", ".hpp": "cpp", ".m": "objc", ".mm": "objc",
            ".swift": "swift", ".zig": "zig", ".sh": "shell", ".bash": "shell"}
_PARSERS: dict = {}


def language_of(path: str) -> str | None:
    return SUFFIXES.get("." + path.rpartition(".")[2].lower())


def parser(language: str) -> Parser:
    if language not in _PARSERS:
        module = importlib.import_module(SPECS[language].grammar)
        _PARSERS[language] = Parser(Language(module.language()))
    return _PARSERS[language]


def parse(language: str, data: bytes):
    return parser(language).parse(data)


# --- walking a function's own nodes ------------------------------------------------------------

def own_nodes(fn, spec: Spec):
    """Every node under fn that no nested function declaration holds."""
    stack = list(reversed(fn.children))
    while stack:
        node = stack.pop()
        yield node
        if node.type not in spec.functions:
            stack.extend(reversed(node.children))


BODIES = frozenset({"block", "constructor_body", "compound_statement", "function_body",
                    "block_expression"})


def has_body(node) -> bool:
    """A declaration with a body; an interface or abstract method has none."""
    return node.type != "method_declaration" or any(
        child.type in BODIES for child in node.children)


def functions(tree, spec: Spec) -> list:
    """Every function declaration node with a body in the tree, outer before inner."""
    found, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in spec.functions and has_body(node):
            found.append(node)
        stack.extend(reversed(node.children))
    return found


# --- names -------------------------------------------------------------------------------------

DECLARATOR_TYPES = frozenset({"function_declarator", "pointer_declarator", "reference_declarator",
                              "parenthesized_declarator", "attributed_declarator"})
NAME_TYPES = frozenset({"identifier", "field_identifier", "destructor_name", "operator_name",
                        "simple_identifier", "word", "type_identifier"})


def _c_name(node):
    """The name inside a C-family declarator chain."""
    while node is not None and node.type in DECLARATOR_TYPES:
        node = node.child_by_field_name("declarator")
    if node is not None and node.type in ("qualified_identifier", "template_function"):
        return _c_name(node.child_by_field_name("name"))
    return node


def _objc_method_name(fn):
    return next((child for child in fn.children if child.type == "identifier"), None)


def name_node(fn):
    """The node that names a function, or None for one with no name."""
    if fn.type == "function_definition" and fn.child_by_field_name("declarator") is not None:
        return _c_name(fn.child_by_field_name("declarator"))
    if fn.type == "method_definition":
        return _objc_method_name(fn)
    named = fn.child_by_field_name("name")
    return named if named is not None else _first_name(fn)


def _first_name(fn):
    return next((child for child in fn.children if child.type in NAME_TYPES), None)


def name(fn, data: bytes) -> str:
    node = name_node(fn)
    if node is None:
        return {"init_declaration": "init", "deinit_declaration": "deinit"}.get(fn.type, "")
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def start_line(fn) -> int:
    node = name_node(fn)
    return (node if node is not None else fn).start_point[0] + 1


def end_line(fn) -> int:
    return fn.end_point[0] + 1


# --- params ------------------------------------------------------------------------------------

def _c_parameter_list(fn):
    declarator = fn.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    return None if declarator is None else declarator.child_by_field_name("parameters")


def _parameter_list(fn):
    """The node holding fn's declared parameters (fn itself for Swift), or None."""
    if fn.type == "function_definition":
        return _c_parameter_list(fn)
    listed = fn.child_by_field_name("parameters")
    if listed is not None:
        return listed
    return next((child for child in fn.children if child.type in PARAMETER_LISTS), fn)


PARAMETER_LISTS = frozenset({"parameters", "parameter_list", "formal_parameters"})


def _go_names(node) -> int:
    names = [child for child in node.children if child.type == "identifier"]
    return max(len(names), 1)


def _is_void(node, data: bytes) -> bool:
    return node.type == "parameter_declaration" and data[node.start_byte:node.end_byte] == b"void"


def _param_weight(node, spec: Spec, data: bytes) -> int:
    if node.type not in spec.parameters or _is_void(node, data):
        return 0
    return _go_names(node) if node.type == "parameter_declaration" and spec is SPECS["go"] else 1


def params(fn, spec: Spec, data: bytes) -> int:
    listed = _parameter_list(fn)
    if listed is None:
        return 0
    return sum(_param_weight(child, spec, data) for child in listed.children)


# --- nloc --------------------------------------------------------------------------------------

def _code_leaf(node, spec: Spec) -> bool:
    return node.type not in spec.comments


def _lines(node) -> range:
    return range(node.start_point[0], node.end_point[0] + 1)


# A string literal is one token: code on every line it spans.
STRINGS = frozenset({"string", "raw_string", "string_literal", "raw_string_literal",
                     "interpreted_string_literal", "line_string_literal",
                     "multi_line_string_literal", "text_block"})


def _opaque(node, spec: Spec) -> bool:
    return node.child_count == 0 or node.type in STRINGS


def _code_leaves(fn, spec: Spec):
    """fn's own tokens (a string literal whole), a comment's inner tokens (Rust's // and
    doc markers) left out."""
    stack = list(reversed(fn.children))
    while stack:
        node = stack.pop()
        if _opaque(node, spec):
            yield node
        elif node.type not in spec.functions and node.type not in spec.comments:
            stack.extend(reversed(node.children))


def _code_lines(fn, spec: Spec):
    for node in _code_leaves(fn, spec):
        if _code_leaf(node, spec):
            yield from _lines(node)


def raw_nloc(fn, spec: Spec) -> set:
    """The lines from fn's name on that hold one of its own tokens other than a comment."""
    first = start_line(fn) - 1
    return {line for line in _code_lines(fn, spec) if line >= first}


def _nested(fn, spec: Spec) -> list:
    return [node for node in own_nodes(fn, spec) if node.type in spec.functions]


def closing_lines(fn, spec: Spec) -> set:
    """AO-NLOC-CLOSE-LINE: the line a nested function ends on is that function's."""
    return {node.end_point[0] for node in _nested(fn, spec)}


def opening_lines(fn, spec: Spec) -> set:
    """AO-NLOC-OPEN-LINE: a nested function's header, from its first token (an
    annotation, a modifier) to the line of its name, counts for the enclosing function
    too."""
    return {line for node in _nested(fn, spec)
            for line in range(node.start_point[0], start_line(node))}


def nloc(fn, spec: Spec, transform: bool = True) -> int:
    lines = raw_nloc(fn, spec)
    if transform:
        lines = (lines | opening_lines(fn, spec)) - closing_lines(fn, spec)
    return len(lines)


# --- ccn ---------------------------------------------------------------------------------------

def _text(node, data: bytes) -> bytes:
    return data[node.start_byte:node.end_byte]


DEFAULT_PATTERNS = (b"_", b"*")


def is_default(case, data: bytes) -> bool:
    """A default arm: `default`, `else =>`, a `_` match arm or a `*)` shell arm."""
    first = case.children[0] if case.children else None
    if first is None:
        return False
    if first.type in ("default", "default_keyword", "else"):
        return True
    return _text(first, data).strip() in DEFAULT_PATTERNS and case.type in ("match_arm",
                                                                             "case_item")


def logical_operators(node, spec: Spec) -> int:
    """The short-circuit operator tokens directly under node."""
    return sum(1 for child in node.children if not child.is_named and child.type in spec.logical)


def match_guard(node) -> bool:
    """A Rust match arm's `if` guard: one more decision on its arm."""
    return node.type == "match_pattern" and any(child.type == "if" for child in node.children)


def _conditionless_for(node) -> bool:
    """A Go `for { ... }` or a C `for (;;)`: a loop with no condition decides nothing."""
    if node.type != "for_statement":
        return False
    kinds = [child.type for child in node.children]
    c_style = {"(", ";"} <= set(kinds) and node.child_by_field_name("condition") is None
    return kinds == ["for", "block"] or c_style


def let_else(node) -> bool:
    """A Rust `let PATTERN = VALUE else { ... }`: a decision, like the if let it spells."""
    return node.type == "let_declaration" and any(child.type == "else" for child in node.children)


def _case(node, data: bytes, mod: bool) -> int:
    """A case label: +1 in ccn_std unless it is the default; ccn_mod counts the switch."""
    return 0 if mod or is_default(node, data) else 1


def _switch_or_operators(node, spec: Spec, mod: bool) -> int:
    if mod and node.type in spec.switches:
        return 1
    return logical_operators(node, spec)


# The members that name an optional type itself, not a member of its value: its metatype
# (`Int?.self`) and Optional's own case and initializers (`Empty?.none`, `Int?.some(1)`).
TYPE_MEMBERS = frozenset({b".self", b".Type", b".Protocol", b".none", b".some", b".init"})


def _marks_a_type(mark, data: bytes) -> bool:
    """A `?` followed by a member in TYPE_MEMBERS: it marks the optional type itself."""
    after = mark.next_sibling
    return after is not None and data[after.start_byte:after.end_byte] in TYPE_MEMBERS


def optional_chains(node, spec: Spec, data: bytes) -> int:
    """The `?` of an optional chain directly under node (Swift `a?.b`, `f()?.g`, `c?()`,
    `d?[0]`): one short-circuit decision each (NIST SP 500-235 sec. 4.1), as `?.` is in
    TypeScript. A `?` before `.self`, `.Type`, `.Protocol`, `.none`, `.some` or `.init`
    marks a type and is none: `Empty?.none` is the case none of Optional<Empty>."""
    if node.type not in spec.chains:
        return 0
    return sum(1 for child in node.children
               if child.type == "?" and not _marks_a_type(child, data))


def _one_decision(node, spec: Spec) -> bool:
    if _conditionless_for(node):
        return False
    return node.type in spec.decisions or let_else(node) or match_guard(node)


def _decisions(node, spec: Spec, data: bytes, mod: bool) -> int:
    if _one_decision(node, spec):
        return 1
    if node.type in spec.cases:
        return _case(node, data, mod)
    return _switch_or_operators(node, spec, mod) + optional_chains(node, spec, data)


def ccn(fn, spec: Spec, data: bytes, mod: bool = False) -> int:
    """ccn_std, or ccn_mod when mod is set."""
    return 1 + sum(_decisions(node, spec, data, mod) for node in own_nodes(fn, spec))


# --- one row per function ----------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    name: str
    start: int
    end: int
    params: int
    nloc: int
    ccn_std: int
    ccn_mod: int
    has_error: bool


def rows(path: str, data: bytes) -> list[Row]:
    """Every function of one file, or [] for a suffix no spec reads."""
    language = language_of(path)
    if language is None:
        return []
    spec, tree = SPECS[language], parse(language, data)
    return [_row(fn, spec, data) for fn in functions(tree, spec)]


def _row(fn, spec: Spec, data: bytes) -> Row:
    return Row(name(fn, data), start_line(fn), end_line(fn), params(fn, spec, data),
               nloc(fn, spec), ccn(fn, spec, data), ccn(fn, spec, data, mod=True),
               fn.has_error)
