"""Cognitive complexity of a brace-language or shell function, counted over its
tree-sitter tree from G. Ann Campbell, "Cognitive Complexity", SonarSource,
version 1.7 (29 August 2023). Node roles come from treesitter_counters.SPECS;
no lizard reader and no crapkit.

- B1 increments: if, a conditional operator, switch, each loop, each catch
  (+1 and the nesting level); else if and else (+1, no nesting increment);
  each sequence of like binary logical operators (+1); a goto and a break or
  continue that names a label (+1, "Jumps to labels"); direct recursion (+1
  once, "Recursion").
- B2 nesting level: the bodies of if, else if, else, switch, loops, catch and
  a conditional operator, and a closure's body, sit one level deeper than the
  structure; a condition sits at the structure's own level.
- "Ignore shorthand": a null-coalescing operator (??, orelse) and an early
  return (Rust's ?, Zig's try) add nothing.
- Section "Sequences of logical operators": a chain is read left to right
  through parentheses, +1 for its first operator and +1 each time it changes.

A Swift guard is an if (+1 and the nesting level) whose else is its body. A
try block adds nothing. A Rust let-else is an if let whose else is its body.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from accuracy.analysis_oracles.oracles import treesitter_counters as counters

BLOCKS = frozenset({"block", "compound_statement", "statements", "block_expression", "do_group",
                    "switch_block", "match_block", "function_body", "labeled_statement"})
ELSE_HOLDERS = frozenset({"else_clause"})
# Nodes a logical sequence runs through: parentheses, and bash's redirect, which the
# grammar hangs on the list before it (`a 2>/dev/null || b`).
PARENS = frozenset({"parenthesized_expression", "condition_clause", "redirected_statement"})


@dataclass
class Count:
    spec: object
    data: bytes
    name: bytes
    total: int = 0
    recursed: bool = False
    deepest: int = 0
    seen: set = field(default_factory=set)


def _structure(node, spec) -> bool:
    kinds = spec.ifs | spec.loops | spec.switches | spec.catches | spec.ternaries | spec.guards
    return node.type in kinds or counters.let_else(node)


def _body(node) -> bool:
    return node.type in BLOCKS or node.type in ELSE_HOLDERS


# --- logical sequences ---------------------------------------------------------------------------

def _run_operator(child, spec) -> bool:
    """A short-circuit operator token that joins a sequence (orelse is shorthand)."""
    return not child.is_named and child.type in spec.logical and child.type != "orelse"


def _operator(node, spec) -> str | None:
    return next((child.type for child in node.children if _run_operator(child, spec)), None)


def _continues(child, spec) -> bool:
    return child.type in PARENS or _operator(child, spec) is not None


def _chain(node, spec, out: list) -> None:
    """The logical operators of one chain, left to right, through parentheses."""
    for child in node.children:
        if _run_operator(child, spec):
            out.append(child.type)
        elif _continues(child, spec):
            _chain(child, spec, out)


def sequences(node, spec) -> int:
    """+1 for a chain's first operator and +1 each time the operator changes."""
    operators: list = []
    _chain(node, spec, operators)
    return sum(1 for index, op in enumerate(operators) if index == 0 or op != operators[index - 1])


def _outside_parens(parent):
    while parent is not None and parent.type in PARENS:
        parent = parent.parent
    return parent


def _chain_top(node, spec) -> bool:
    if _operator(node, spec) is None:
        return False
    parent = _outside_parens(node.parent)
    return parent is None or _operator(parent, spec) is None


# --- jumps and recursion -------------------------------------------------------------------------

# A structure's header sits at its own level; everything else it holds is its body.
HEADER_FIELDS = frozenset({"condition", "initializer", "update", "value", "pattern", "item",
                           "collection", "left", "right", "subject", "type", "name"})
CALLS = frozenset({"call_expression", "method_invocation", "command"})
JUMP_WORDS = frozenset({"break", "continue"})


def _header(node, index: int, child) -> bool:
    return (not child.is_named or child.type in PARENS
            or node.field_name_for_child(index) in HEADER_FIELDS)


def _labeled(node, spec) -> bool:
    return node.children[0].type in JUMP_WORDS and any(
        child.type in spec.labels for child in node.children)


def _jump(node, count: Count) -> int:
    """+1 for a goto, and for a break or continue that names a label."""
    if node.type not in count.spec.jumps or not node.children:
        return 0
    return 1 if node.type == "goto_statement" or _labeled(node, count.spec) else 0


def _named_callee(node):
    for field_name in ("function", "name"):
        found = node.child_by_field_name(field_name)
        if found is not None:
            return found
    return node.named_children[0] if node.named_children else None


def _callee(node):
    if node.type not in CALLS or node.child_by_field_name("object") is not None:
        return None
    return _named_callee(node)


def _recursion(node, count: Count) -> int:
    callee = None if count.recursed or not count.name else _callee(node)
    if callee is None or count.data[callee.start_byte:callee.end_byte] != count.name:
        return 0
    count.recursed = True
    return 1


# --- if, else if, else ---------------------------------------------------------------------------

def _else_kids(node, spec) -> list:
    """What follows an else, its payload left out; nothing where else-if is elif."""
    if "elif_clause" in spec.ifs:
        return []
    return [child for child in node.children if child.type not in ("else", "payload")]


def _else_target(node, spec):
    """What an else introduces: the else-if node, or None for a plain else. A Zig
    else's payload (`else |err| if ...`) sits between the else and its if. A language
    that spells else-if as elif (shell) has none: its else holding an if is a plain
    else."""
    kids = _else_kids(node, spec)
    return kids[0] if len(kids) == 1 and kids[0].type in ("if_statement", "if_expression") else None


def _walk_if(node, level: int, count: Count, chained: bool) -> None:
    count.total += 1 if chained else 1 + level
    _enter(level + 1, count)
    after_else = False
    for index, child in enumerate(node.children):
        after_else = _if_child((node, index, child), level, count, after_else)


def _starts_else(child, after_else: bool) -> bool:
    return after_else or child.type in ELSE_HOLDERS or child.type == "elif_clause"


def _if_child(where: tuple, level: int, count: Count, after_else: bool) -> bool:
    node, index, child = where
    if child.type == "else":
        return True
    if _starts_else(child, after_else):
        _else_branch(child, level, count)
        return after_else
    _visit(child, level if _header(node, index, child) else level + 1, count)
    return False


def _else_increment(child) -> int:
    """A plain else pays +1 once: at its block, or at Swift's `{` token."""
    return 1 if (child.is_named and child.type != "statements") or child.type == "{" else 0


def _else_parts(child) -> list:
    inner = child.children if child.type in ELSE_HOLDERS else [child]
    return [part for part in inner if part.type != "else"]


def _else_branch(child, level: int, count: Count) -> None:
    target = child if child.type in count.spec.ifs else _else_target(child, count.spec)
    if target is not None:
        _walk_if(target, level, count, chained=True)
        return
    count.total += _else_increment(child)
    _enter(level + 1, count)
    for part in _else_parts(child):
        _visit(part, level + 1, count)


# --- the walk ------------------------------------------------------------------------------------

def _enter(level: int, count: Count) -> None:
    count.deepest = max(count.deepest, level)


def _whole(node, spec) -> bool:
    return node.type in (spec.switches | spec.catches | spec.ternaries)


def _walk_structure(node, level: int, count: Count) -> None:
    count.total += 1 + level
    _enter(level + 1, count)
    whole = _whole(node, count.spec)
    for index, child in enumerate(node.children):
        header = not whole and _header(node, index, child)
        _visit(child, level if header else level + 1, count)


def _plain_if(node, spec) -> bool:
    return node.type in spec.ifs and node.type != "elif_clause"


def _visit(node, level: int, count: Count) -> None:
    spec = count.spec
    if node.type in spec.functions:
        return
    if _plain_if(node, spec):
        _walk_if(node, level, count, chained=False)
    elif _structure(node, spec):
        _walk_structure(node, level, count)
    else:
        _visit_plain(node, level, count)


def _visit_plain(node, level: int, count: Count) -> None:
    spec = count.spec
    count.total += _jump(node, count) + _recursion(node, count)
    count.total += sequences(node, spec) if _chain_top(node, spec) else 0
    inner = level + 1 if node.type in spec.lambdas else level
    _enter(inner, count)
    for child in node.children:
        _visit(child, inner, count)


PARAMETER_LISTS = frozenset({"parameters", "parameter_list", "formal_parameters"})


def measure(fn, spec, data: bytes) -> Count:
    name = counters.name_node(fn)
    count = Count(spec, data, b"" if name is None else data[name.start_byte:name.end_byte])
    for child in (child for child in fn.children if child.type not in PARAMETER_LISTS):
        _visit(child, 0, count)
    return count


def cognitive(fn, spec, data: bytes) -> int:
    return measure(fn, spec, data).total
