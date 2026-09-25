"""The Python shapes crapkit is known to misread, one detector per rulings row.

A differential over thousands of functions compares crapkit with a clean-room
counter only where no open defect applies. Each detector names the rulings
row whose strict-xfail probe pins the defect, and the differential reports how
many functions each one set aside. When a defect is fixed its probe passes,
the strict xfail fails, and this table loses the detector.

Detectors read the ast of a def whose source went through ast.unparse, so a
bracketed expression sits on one line and line-layout defects (rulings row
AO-PY-COG-LAYOUT, pinned by a hand probe) are out of the way. No crapkit import.
"""
from __future__ import annotations

import ast

from accuracy.analysis_oracles.oracles.radon_mccabe import own_nodes

SELF = ("self", "cls")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
COUNTED = (ast.BoolOp, ast.IfExp, *COMPREHENSIONS)
STRING_PREFIXES = frozenset({"f", "b", "r", "u", "rb", "br", "fr", "rf"})
C_KEYWORDS = ("do", "goto", "switch", "catch", "foreach")


def _walked(fn) -> list:
    """Every node on fn's own lines and under them, its parameters included."""
    return [node for item in [*own_nodes(fn), fn.args] for node in ast.walk(item)]


def floor_division(fn) -> bool:
    """AO-PY-FLOORDIV: `//` hides the rest of its line."""
    return any(isinstance(node, ast.FloorDiv) for node in own_nodes(fn))


def _wildcard(case) -> bool:
    pattern = case.pattern
    return isinstance(pattern, ast.MatchAs) and pattern.pattern is None and case.guard is None


def wildcard_case(fn) -> bool:
    """AO-PY-CCN-WILDCARD: an unguarded `case _` counts as a decision."""
    return any(_wildcard(case) for node in own_nodes(fn) if isinstance(node, ast.Match)
               for case in node.cases)


# --- names ------------------------------------------------------------------------------

def _self_call(node, name: str) -> bool:
    """`name(...)`, `self.name(...)` or `cls.name(...)`: a real recursive call."""
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id == name
    owner = getattr(func, "value", None)
    return isinstance(func, ast.Attribute) and func.attr == name and _is_self(owner)


def _is_self(node) -> bool:
    return isinstance(node, ast.Name) and node.id in SELF


def _import_parts(node) -> list:
    """Every dotted part and alias an import statement names."""
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        return []
    parts = [part for alias in node.names for part in (*alias.name.split("."), alias.asname)]
    return parts + str(getattr(node, "module", "")).split(".")


def _spells(node, name: str) -> bool:
    """Whether a node spells the name: a variable, an attribute, a parameter,
    a keyword argument or an imported name."""
    names = [getattr(node, "id", None), getattr(node, "attr", None), getattr(node, "arg", None)]
    return name in names + _import_parts(node)


def _strings_under_prefix_name(fn, nodes: list) -> bool:
    return fn.name in STRING_PREFIXES and any(isinstance(node, (ast.Constant, ast.JoinedStr))
                                              for node in nodes)


def name_as_recursion(fn) -> bool:
    """AO-PY-COG-RECURSION: the function's name spelled anywhere in its body
    other than a call of itself (a variable, a parameter, another object's
    method, an import, a string prefix) reads as recursion."""
    nodes = _walked(fn)
    return bool(_other_spellings(nodes, fn.name)) or _strings_under_prefix_name(fn, nodes)


def _recursive_callees(nodes: list, name: str) -> set:
    return {id(node.func) for node in nodes if _self_call(node, name)}


def _other_spellings(nodes: list, name: str) -> list:
    """The nodes spelling `name` that are not the callee of a real recursive call."""
    calls = _recursive_callees(nodes, name)
    return [node for node in nodes if _spells(node, name) and id(node) not in calls]


def keyword_names(fn) -> bool:
    """AO-PY-COG-KEYWORD-NAMES: a Python name spelled like another language's
    keyword (`do`, `switch`, `catch`, `foreach`, `goto`) reads as that
    structure: +1 and the nesting it sits in, or +1 for goto."""
    return any(_spells(node, word) for node in _walked(fn) for word in C_KEYWORDS)


def self_recursion(fn) -> bool:
    """AO-PY-COG-NESTED-RECURSION (for a def inside a def): a nested def that
    calls itself gets no recursion increment."""
    return any(_self_call(node, fn.name) for node in own_nodes(fn))


# --- operator runs and comprehension levels -----------------------------------------------

def _line_groups(fn) -> list[list]:
    """The counted expression nodes of fn's own lines, grouped by starting line."""
    groups: dict[int, list] = {}
    for node in own_nodes(fn):
        if isinstance(node, COUNTED):
            groups.setdefault(node.lineno, []).append(node)
    return list(groups.values())


_COMMA_COUNTS = {
    ast.Call: lambda node: len(node.args) + len(node.keywords) > 1,
    ast.Tuple: lambda node: bool(node.elts),
    ast.List: lambda node: len(node.elts) > 1,
    ast.Set: lambda node: len(node.elts) > 1,
    ast.Dict: lambda node: len(node.keys) > 1,
    ast.arguments: lambda node: len(node.args) > 1,
}


def _commas(node) -> bool:
    """Whether a node's source holds a comma between its parts."""
    check = _COMMA_COUNTS.get(type(node))
    return bool(check and check(node))


def _direct_chain(ops: list) -> bool:
    """Every BoolOp but the outermost is a direct value of another one."""
    values = {id(value) for op in ops for value in op.values}
    return sum(id(op) not in values for op in ops) == 1


def _run_trouble(group: list) -> bool:
    ops = [node for node in group if isinstance(node, ast.BoolOp)]
    if len(ops) > 1 and not _direct_chain(ops):
        return True
    return any(map(_commas, _under(ops)))


def _under(nodes: list) -> list:
    return [inner for node in nodes for inner in ast.walk(node)]


def token_runs(fn) -> bool:
    """AO-PY-COG-RUNS: runs of like operators follow tokens, so a comma between
    them, a negated group or a second expression on the line changes the count."""
    return any(_run_trouble(group) for group in _line_groups(fn))


HEADERS = {ast.If: ("test",), ast.While: ("test",), ast.For: ("iter", "target"),
           ast.AsyncFor: ("iter", "target"), ast.With: ("items",), ast.AsyncWith: ("items",),
           ast.Match: ("subject",)}


def _header_items(node) -> list:
    parts = [getattr(node, name) for name in HEADERS.get(type(node), ())]
    return [item for part in parts for item in (part if isinstance(part, list) else [part])]


def _holds_comprehension(item) -> bool:
    return any(isinstance(inner, COMPREHENSIONS) for inner in ast.walk(item))


def _shares_a_line(group: list) -> bool:
    return len(group) > 1 and any(isinstance(node, COMPREHENSIONS) for node in group)


def comprehension_scope(fn) -> bool:
    """AO-PY-COG-COMP-SCOPE: a comprehension's level stays open to the end of
    its statement: anything counted after it on its line, or in the body of the
    statement whose header holds it, reads nested one level deeper."""
    headers = [item for node in own_nodes(fn) for item in _header_items(node)]
    return (any(map(_shares_a_line, _line_groups(fn)))
            or any(map(_holds_comprehension, headers)))


# --- a while after a brace, and match -------------------------------------------------------

def _after_brace(body: list) -> bool:
    """A while whose previous statement ends in `}` (a dict or set literal)."""
    return any(isinstance(later, ast.While) and ast.unparse(earlier).endswith("}")
               for earlier, later in zip(body, body[1:]))


def _bodies(fn) -> list:
    blocks = [getattr(node, name, None) for node in own_nodes(fn)
              for name in ("body", "orelse", "finalbody")]
    return [fn.body] + [block for block in blocks if isinstance(block, list)]


def while_after_brace(fn) -> bool:
    """AO-COG-WHILE-AFTER-BRACE: a while right after a closing brace reads as a
    do-while's tail and adds nothing."""
    return any(map(_after_brace, _bodies(fn)))


def match_statement(fn) -> bool:
    """D4: a match statement adds nothing to cognitive."""
    return any(isinstance(node, ast.Match) for node in own_nodes(fn))


CCN = {"AO-PY-FLOORDIV": floor_division, "AO-PY-CCN-WILDCARD": wildcard_case}
COGNITIVE = {
    "AO-PY-FLOORDIV-COG": floor_division,
    "AO-PY-COG-RECURSION": name_as_recursion,
    "AO-PY-COG-RUNS-COMMA": token_runs,
    "AO-PY-COG-COMP-SCOPE": comprehension_scope,
    "AO-COG-WHILE-AFTER-BRACE-PY": while_after_brace,
    "AO-PY-COG-KEYWORD-NAMES": keyword_names,
    "D4": match_statement,
}
NESTING = {
    "AO-PY-FLOORDIV-COG": floor_division,
    "AO-PY-NEST-COMP-SCOPE": comprehension_scope,
    "AO-NEST-WHILE-AFTER-BRACE-PY": while_after_brace,
    "AO-PY-NEST-KEYWORD-NAMES": keyword_names,
}
NESTED_COGNITIVE = {"AO-PY-COG-NESTED-RECURSION": self_recursion}


def reasons(fn, table: dict, nested: bool = False) -> list[str]:
    """The ruling ids whose shape this def holds. `nested` says fn sits inside
    another def, where NESTED_COGNITIVE applies too when table is COGNITIVE."""
    extra = NESTED_COGNITIVE if nested and table is COGNITIVE else {}
    return [ruling for ruling, detector in {**table, **extra}.items() if detector(fn)]


def _defs(tree) -> list:
    return [node for node in ast.walk(tree) if isinstance(node, FUNCTIONS)]


def nested_defs(tree) -> set[int]:
    """ids of the defs that sit inside another def."""
    inner = (node for outer in _defs(tree) for node in _defs(outer) if node is not outer)
    return set(map(id, inner))
