"""The crapkit defects the Objective-C differentials found, one detector per
rulings row, in the form ts_defect_shapes takes: RULES lists (rulings id,
languages, columns, predicate over one function's tree-sitter node).

- AO-C-ELVIS-COG (calc-bug analysis-oracles-100): a GNU `a ?: b`, the
  conditional with its middle operand left out (GCC manual sec. 6.8), adds
  nothing to crapkit's cognitive column; the Sonar paper counts every
  conditional operator +1 plus its nesting. crapkit's ccn counts it.
- AO-TRAILING-ATTRIBUTE (calc-bug analysis-oracles-101): an attribute between
  a function's name and its body (`API_AVAILABLE(ios(10))`,
  `__attribute__((noinline))`, `NS_SWIFT_NAME(...)`) takes the row's name. An
  Objective-C method is then named `)` and scores +1 cognitive for recursion,
  and a row whose attribute sits on a later line than its name starts on the
  attribute's line; extra_line() explains that row.
- AO-ND-FOR-AFTER-BRACE (calc-bug analysis-oracles-102): a C-style for (a `;`
  in its header) that holds another structure reads one nesting level short
  once any `{` comes before it in the function: inside an if, a bare block, an
  initializer list, @synchronized or @autoreleasepool. The Objective-C blocks
  showed it first; the same count runs in every brace language that keeps
  lizard's ND column, so the shape covers C, C++, Objective-C, Java and Go.

No crapkit import.
"""
from __future__ import annotations

from accuracy.analysis_oracles.oracles import treesitter_counters as counters

C_FAMILY = ("c", "cpp", "objc")
EVERY = ("row", "end", "ccn_std", "ccn_mod", "cognitive", "nesting", "nloc", "params")
ATTRIBUTES = frozenset({"attribute_specifier", "availability_attribute_specifier"})


def _walk(node):
    """node and every node under it."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def omitted_middle(fn, context) -> bool:
    """AO-C-ELVIS-COG: a conditional expression with no middle operand, `a ?: b`."""
    return any(node.type == "conditional_expression"
               and node.child_by_field_name("consequence") is None
               for node in counters.own_nodes(fn, context.spec))


def _body(fn):
    body = fn.child_by_field_name("body")
    return body or next((kid for kid in fn.children if kid.type == "compound_statement"), None)


def _attribute_between(node, first, last) -> bool:
    return node.type in ATTRIBUTES and first.start_byte < node.start_byte < last.start_byte


def trailing_attributes(fn) -> list:
    """The attribute nodes between fn's name and its body."""
    name, body = counters.name_node(fn), _body(fn)
    if None in (name, body):
        return []
    return [node for node in _walk(fn) if _attribute_between(node, name, body)]


def trailing_attribute(fn, context) -> bool:
    """AO-TRAILING-ATTRIBUTE: fn carries an attribute after its name."""
    return bool(trailing_attributes(fn))


def _attribute_lines(context) -> set:
    lines = set()
    for fn in counters.functions(context.tree, context.spec):
        for node in trailing_attributes(fn):
            lines.update(range(node.start_point[0] + 1, node.end_point[0] + 2))
    return lines


def extra_line(context, start: int) -> bool:
    """A crapkit row that starts on a line a trailing attribute spans
    (AO-TRAILING-ATTRIBUTE), cached per file."""
    if "trailing" not in context.facts:
        context.facts["trailing"] = _attribute_lines(context)
    return start in context.facts["trailing"]


def _header_semicolon(node) -> bool:
    """A for whose header holds a `;`: C, C++, Java, and Go's for clause."""
    parts = [*node.children, *(inner for kid in node.children if kid.type == "for_clause"
                               for inner in kid.children)]
    return any(part.type == ";" for part in parts)


def _structures(context) -> frozenset:
    spec = context.spec
    return spec.ifs | spec.loops | spec.switches | spec.ternaries | spec.catches


def _holds_structure(node, kinds) -> bool:
    return any(inner.type in kinds for inner in _walk(node) if inner != node)


def c_style_fors(fn, context) -> list:
    """The C-style for loops in fn that hold another structure."""
    kinds = _structures(context)
    return [node for node in counters.own_nodes(fn, context.spec)
            if node.type == "for_statement" and _header_semicolon(node)
            and _holds_structure(node, kinds)]


def for_after_brace(fn, context) -> bool:
    """AO-ND-FOR-AFTER-BRACE: such a for starts after a `{` other than the body's first."""
    braces = sorted(node.start_byte for node in _walk(fn) if node.type == "{")
    return any(brace < loop.start_byte for loop in c_style_fors(fn, context)
               for brace in braces[1:])


RULES = [
    ("AO-C-ELVIS-COG", C_FAMILY, ("cognitive",), omitted_middle),
    ("AO-TRAILING-ATTRIBUTE", C_FAMILY, EVERY, trailing_attribute),
    ("AO-ND-FOR-AFTER-BRACE", (*C_FAMILY, "java", "go"), ("nesting",), for_after_brace),
]
