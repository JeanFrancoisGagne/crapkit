"""Nesting depth of a brace-language or shell function over its tree-sitter
tree, from G. Ann Campbell, "Cognitive Complexity", SonarSource, version 1.7
(29 August 2023), App. B2: the most nesting structures that enclose any node
of the body. The structures are if (an else if and an else share their if's
level), switch, each loop, catch, a conditional operator, a Swift guard, a
Rust let-else and a closure. A function declared inside another has its own
row and adds nothing here.

This counts ancestors, where treesitter_cognitive walks levels, so the two
are separate readings of the same paper. No lizard reader and no crapkit.
"""
from __future__ import annotations

from accuracy.analysis_oracles.oracles import treesitter_counters as counters


def _follows_else(node) -> bool:
    parent, before = node.parent, node.prev_sibling
    if parent is not None and parent.type == "else_clause":
        return True
    return before is not None and before.type == "else"


def _else_if(node, spec) -> bool:
    """An if that continues an else: it shares the level of the if it follows. Where
    else-if is spelled elif (shell), an if inside an else is a nested if."""
    if "elif_clause" in spec.ifs:
        return node.type == "elif_clause"
    return _follows_else(node)


def opens(node, spec) -> bool:
    """Whether node opens a nesting level for what it holds."""
    if node.type in spec.ifs:
        return not _else_if(node, spec)
    kinds = (spec.loops | spec.switches | spec.catches | spec.ternaries | spec.guards
             | spec.lambdas)
    return node.type in kinds or counters.let_else(node)


def _own_children(node, spec) -> list:
    return [child for child in node.children if child.type not in spec.functions]


def _levels(fn, spec):
    """How many structures enclose or open at each of fn's own nodes."""
    stack = [(child, 0) for child in _own_children(fn, spec)]
    while stack:
        node, level = stack.pop()
        here = level + 1 if opens(node, spec) else level
        yield here
        stack.extend((child, here) for child in _own_children(node, spec))


def depth(fn, spec) -> int:
    return max(_levels(fn, spec), default=0)
