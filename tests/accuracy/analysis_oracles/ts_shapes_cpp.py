"""The C-family shapes fmt's differentials raised, one detector per rulings row.

ts_defect_shapes takes SHAPES, (ruling id, languages, columns, predicate)
tuples, into its own SHAPES, and extra_row() as its C++ explanation for a
crapkit row at a line where tree-sitter lists no function.

- Crapkit defects, each pinned by a hand probe: AO-CPP-QUALIFIED-RECURSION
  and AO-CPP-CLASS-NAME-RECURSION (calc-bug analysis-oracles-90),
  AO-COG-BRACELESS-BODY (91), AO-C-PARAMS-UNNAMED and AO-C-PARAMS-ARRAY (92),
  AO-CPP-RVALUE-* (93), AO-CPP-LOCAL-CLASS (94), AO-CPP-DECLTYPE-BRACE (95).
- A crapkit definition: AO-CPP-DEFAULTED.
- tree-sitter-cpp reading a construct wrong (AO-TREE-*): what the grammar
  cannot read is kept out of every differential that joins on its function
  list, each pinned by a hand case in test_c_family_oracles.

A predicate takes a function node and the file's ts_defect_shapes.Context.
No crapkit import.
"""
from __future__ import annotations

import re

ROW = "row"
EVERY = (ROW, "end", "ccn_std", "ccn_mod", "cognitive", "nesting", "nloc", "params")
CCN = ("ccn_std", "ccn_mod")
COGNITIVE = ("cognitive",)
NESTING = ("nesting",)
PARAMS = ("params",)
C_LIKE = frozenset({"c", "cpp", "objc"})
CPP = frozenset({"cpp"})


def walk(node) -> list:
    """node and every node under it."""
    found, stack = [], [node]
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(node.children)
    return found


def _inner_declarator(node):
    """The declarator a pointer, reference or parenthesized declarator wraps."""
    return node.child_by_field_name("declarator") or next(
        (child for child in node.named_children if child.type.endswith("declarator")), None)


def _declarator(fn):
    """fn's function_declarator, through pointer, reference and parenthesized declarators."""
    node = fn.child_by_field_name("declarator")
    while node is not None and node.type != "function_declarator":
        node = _inner_declarator(node)
    return node


def _ancestors(node) -> list:
    found, node = [], node.parent
    while node is not None:
        found.append(node.type)
        node = node.parent
    return found


# --- recursion by name (calc-bug analysis-oracles-90) ---------------------------------------------

NAMES = frozenset({"identifier", "field_identifier", "type_identifier", "namespace_identifier"})
SCOPE_BODIES = frozenset({"field_declaration_list", "declaration_list"})


def _scope_name(scope):
    """A scope's bare name: `formatter` for `formatter<T>`."""
    if scope is not None and scope.type == "template_type":
        return scope.child_by_field_name("name")
    return scope


def _name_parts(context, name) -> tuple:
    """(the leading scope's name or None, the function's own name) of a declarator name."""
    if name is None:
        return None, b""
    if name.type != "qualified_identifier":
        return None, context.text(name)
    scope = _scope_name(name.child_by_field_name("scope"))
    _, own = _name_parts(context, name.child_by_field_name("name"))
    return (context.text(scope) if scope is not None else None), own


def _spelled(fn, context, word) -> bool:
    body = fn.child_by_field_name("body")
    return word is not None and body is not None and any(
        node.type in NAMES and context.text(node) == word for node in walk(body))


def _parts(fn, context) -> tuple:
    declarator = _declarator(fn)
    return _name_parts(context, declarator and declarator.child_by_field_name("declarator"))


MEMBER_MARKS = frozenset({"type_qualifier", "ref_qualifier", "virtual_specifier"})


def _member(fn) -> bool:
    """A trailing const, volatile, & or override: only a member function has one
    (fmt's macros can hide the class body around it from tree-sitter)."""
    declarator = _declarator(fn)
    return declarator is not None and any(child.type in MEMBER_MARKS
                                          for child in declarator.children)


def qualified_recursion(fn, context) -> bool:
    """AO-CPP-QUALIFIED-RECURSION: a function in a namespace or class body, named
    with a scope or marked as a member, that spells its own name in its body."""
    scope, own = _parts(fn, context)
    scoped = scope is not None or bool(SCOPE_BODIES & set(_ancestors(fn))) or _member(fn)
    return scoped and _spelled(fn, context, own)


def class_name_recursion(fn, context) -> bool:
    """AO-CPP-CLASS-NAME-RECURSION: an out-of-class member that spells its class."""
    scope, _ = _parts(fn, context)
    return _spelled(fn, context, scope)


# --- a structure in a braceless body (calc-bug analysis-oracles-91) -------------------------------

BODY_FIELDS = {"if_statement": ("consequence", "alternative"), "for_statement": ("body",),
               "for_range_loop": ("body",), "while_statement": ("body",),
               "do_statement": ("body",), "enhanced_for_statement": ("body",)}
BLOCKS = frozenset({"compound_statement", "block"})
STRUCTURES = frozenset({"if_statement", "for_statement", "for_range_loop", "while_statement",
                        "do_statement", "switch_statement", "conditional_expression",
                        "ternary_expression", "enhanced_for_statement", "try_statement"})


def _else_body(node):
    """An else clause's statement, or a Java alternative; None for an else if."""
    body = node.named_children[-1] if node.type == "else_clause" and node.named_children else node
    return None if body.type == "if_statement" else body


def _bodies(node) -> list:
    bodies = [node.child_by_field_name(field) for field in BODY_FIELDS[node.type]]
    return [_else_body(body) if field == "alternative" else body
            for field, body in zip(BODY_FIELDS[node.type], bodies) if body is not None]


def _braceless(body) -> bool:
    return body is not None and body.type not in BLOCKS and bool(
        STRUCTURES & {node.type for node in walk(body)})


def braceless_body(fn, context) -> bool:
    """AO-COG-BRACELESS-BODY: an if, else or loop body without braces holds a structure."""
    return any(_braceless(body) for holder in walk(fn) if holder.type in BODY_FIELDS
               for body in _bodies(holder))


# --- parameters (calc-bug analysis-oracles-92) ----------------------------------------------------

def _parameters(fn) -> list:
    declarator = _declarator(fn)
    listed = declarator and declarator.child_by_field_name("parameters")
    return [node for node in (listed.named_children if listed else [])
            if node.type == "parameter_declaration"]


def _unnamed(parameter, context) -> bool:
    declarator = parameter.child_by_field_name("declarator")
    named = declarator is not None and any(node.type == "identifier" for node in walk(declarator))
    return not named and context.text(parameter).strip() != b"void"


def unnamed_parameter(fn, context) -> bool:
    """AO-C-PARAMS-UNNAMED: a parameter with no name (a lone `void` is no parameter)."""
    return any(_unnamed(parameter, context) for parameter in _parameters(fn))


def array_parameter(fn, context) -> bool:
    """AO-C-PARAMS-ARRAY: a parameter declared as an array or a reference or pointer to one."""
    return any(node.type in ("array_declarator", "abstract_array_declarator")
               for parameter in _parameters(fn) for node in walk(parameter))


# --- rvalue references (calc-bug analysis-oracles-93 and AO-TREE-RVALUE-LOGICAL) ------------------

REFERENCES = frozenset({"reference_declarator", "abstract_reference_declarator"})


def _rvalues(node) -> list:
    return [found for found in walk(node) if found.type in REFERENCES and
            any(child.type == "&&" for child in found.children)]


def rvalue_anywhere(fn, context) -> bool:
    """An rvalue or forwarding reference in fn, its signature included."""
    return bool(_rvalues(fn))


def rvalue_in_body(fn, context) -> bool:
    """AO-CPP-RVALUE-BODY: an rvalue or forwarding reference inside fn's body."""
    body = fn.child_by_field_name("body")
    return body is not None and bool(_rvalues(body))


# --- which definitions are functions --------------------------------------------------------------

def local_class(fn, context) -> bool:
    """AO-CPP-LOCAL-CLASS: fn defines a class whose body defines a member function."""
    body = fn.child_by_field_name("body")
    return body is not None and any(
        node.type == "field_declaration_list" and any(
            child.type == "function_definition" for child in node.named_children)
        for node in walk(body))


def defaulted(fn, context) -> bool:
    """AO-CPP-DEFAULTED: a definition as `= default;` or `= delete;`."""
    return any(child.type in ("default_method_clause", "delete_method_clause")
               for child in fn.children)


CAPITALS = re.compile(rb"^[A-Z][A-Z0-9_]*$")


CLASSES = frozenset({"class_specifier", "struct_specifier", "union_specifier"})


def _class_name(fn, context):
    """The name of the class whose body holds fn, or None."""
    node = fn.parent
    while node is not None and node.type not in CLASSES:
        node = node.parent
    name = node.child_by_field_name("name") if node is not None else None
    return context.text(name) if name is not None else None


def macro_definition(fn, context) -> bool:
    """AO-TREE-MACRO-DEFINITION: a `definition` with no type, named in capitals and not
    after its class (no constructor), is a function-like macro invoked before a block
    (fmt's FMT_CATCH(...) {})."""
    _, own = _parts(fn, context)
    return fn.child_by_field_name("type") is None and bool(CAPITALS.match(own)) and \
        own != _class_name(fn, context)


def no_function_declarator(fn, context) -> bool:
    """AO-TREE-NOT-A-FUNCTION: a `definition` whose declarator has no parameter list (fmt's
    `struct FMT_API pipe { ... }`, the macro read as the struct's name). An Objective-C
    method_definition has no declarator at all and is a function."""
    return fn.type == "function_definition" and _declarator(fn) is None


# --- crapkit rows at lines tree-sitter lists no function on ---------------------------------------

DECLARATIONS = frozenset({"declaration", "field_declaration"})


def _declarations(context) -> list:
    if "cpp-declarations" not in context.facts:
        context.facts["cpp-declarations"] = [node for node in walk(context.tree.root_node)
                                             if node.type in DECLARATIONS]
    return context.facts["cpp-declarations"]


def _declares_function(node) -> bool:
    return any(found.type == "function_declarator" for found in walk(node))


def _braced_return(node) -> bool:
    return any(found.type == "initializer_list" for found in walk(node)
               if "trailing_return_type" in _ancestors(found))


def _starting(context, start: int, test) -> bool:
    return any(node.start_point[0] + 1 == start and _declares_function(node) and test(node)
               for node in _declarations(context))


def decltype_brace_line(context, start: int) -> bool:
    """AO-CPP-DECLTYPE-BRACE: crapkit's row for a declaration whose trailing return type
    holds a braced initializer starts on that declaration's line."""
    return _starting(context, start, _braced_return)


def declaration_error_line(context, start: int) -> bool:
    """AO-TREE-DECLARATION-ERROR: tree-sitter could not read the declaration there, one
    that declares a function (a macro before a constructor with an initializer list)."""
    return _starting(context, start, lambda node: node.has_error)


def extra_row(context, start: int) -> bool:
    return decltype_brace_line(context, start) or declaration_error_line(context, start)


SHAPES = [
    ("AO-CPP-QUALIFIED-RECURSION", CPP, COGNITIVE, qualified_recursion),
    ("AO-CPP-CLASS-NAME-RECURSION", CPP, COGNITIVE, class_name_recursion),
    ("AO-COG-BRACELESS-BODY", C_LIKE | {"java"}, COGNITIVE, braceless_body),
    ("AO-C-PARAMS-UNNAMED", C_LIKE, PARAMS, unnamed_parameter),
    ("AO-C-PARAMS-ARRAY", C_LIKE, PARAMS, array_parameter),
    ("AO-CPP-RVALUE-BODY", CPP, CCN, rvalue_in_body),
    ("AO-CPP-RVALUE-BODY-COG", CPP, COGNITIVE, rvalue_in_body),
    ("AO-CPP-RVALUE-ND", CPP, NESTING, rvalue_anywhere),
    ("AO-CPP-LOCAL-CLASS", CPP, EVERY, local_class),
    ("AO-CPP-DEFAULTED", CPP, (ROW,), defaulted),
    ("AO-TREE-RVALUE-LOGICAL", CPP, CCN + COGNITIVE, rvalue_anywhere),
    ("AO-TREE-MACRO-DEFINITION", C_LIKE, (ROW,), macro_definition),
    ("AO-TREE-NOT-A-FUNCTION", C_LIKE, (ROW,), no_function_declarator),
]
