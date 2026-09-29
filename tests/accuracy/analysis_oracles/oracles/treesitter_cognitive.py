"""Cognitive complexity of a brace-language or shell function, counted over its
tree-sitter tree from G. Ann Campbell, "Cognitive Complexity", SonarSource,
version 1.7 (29 August 2023). Node roles come from treesitter_counters.SPECS;
no lizard reader and no crapkit.

- B1 increments: if, a conditional operator, switch, each loop, each catch
  (+1 and the nesting level); else if and else (+1, no nesting increment);
  each sequence of like binary logical operators (+1); a goto and a break or
  continue that names a label (+1, "Jumps to labels"); direct recursion (+1
  once, "Recursion"). An Objective-C method recurses when it sends its own
  full selector to self.
- Recursion is a call that reaches the function: its bare name, or a member
  call through `self`, `this`, `Self` or, in Rust, the type its impl names
  (`self.walk`, `this->walk`, `R::walk`). A Rust fn in an impl or a trait is
  not reached by its bare name, which is a free function's. In C++ and Java,
  where one name can belong to several functions, the call must pass a
  number of arguments the function takes, and no other function of the name
  in its class may take that number too: the reading sees no types, so such a
  call is either one's.
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
    selector: bytes = b""
    total: int = 0
    recursed: bool = False
    receivers: frozenset = frozenset()  # what a member call reaches it through
    bare: bool = True                    # its bare name reaches it
    arity: tuple | None = None           # (fewest, most) arguments, where names overload
    others: tuple = ()                   # the arities of the name's other overloads
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


def selector(parts, data: bytes) -> bytes:
    """An Objective-C selector: each part, with a colon where an argument follows it."""
    text = b""
    for part in parts:
        after = part.next_sibling
        takes = after is not None and after.type in (":", "method_parameter")
        text += data[part.start_byte:part.end_byte] + (b":" if takes else b"")
    return text


def method_selector(fn, data: bytes) -> bytes:
    if fn.type != "method_definition":
        return b""
    return selector([kid for kid in fn.children if kid.type == "identifier"], data)


def _messages_itself(node, count: Count) -> bool:
    """[self sel...] with the method's own full selector; [super ...] is another method."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None or count.data[receiver.start_byte:receiver.end_byte] != b"self":
        return False
    parts = [kid for index, kid in enumerate(node.children)
             if node.field_name_for_child(index) == "method"]
    return selector(parts, count.data) == count.selector


def _calls_itself(node, count: Count) -> bool:
    if node.type == "message_expression":
        return bool(count.selector) and _messages_itself(node, count)
    callee = _callee(node)
    return callee is not None and _reaches(callee, count) and _fits(node, count)


# A callee that names a member through what holds it: Rust's `self.walk` and
# `R::walk`, C++'s `this->walk`.
MEMBER_CALLEES = frozenset({"field_expression", "scoped_identifier"})
OWNER_FIELDS = ("value", "argument", "path")
MEMBER_FIELDS = ("field", "name")
SELF_RECEIVERS = frozenset({b"self", b"this", b"Self"})


def _text(node, data: bytes) -> bytes:
    return data[node.start_byte:node.end_byte]


def _reaches(callee, count: Count) -> bool:
    if callee.type in MEMBER_CALLEES:
        return _through_receiver(callee, count)
    return count.bare and _text(callee, count.data) == count.name


def _field(node, names: tuple):
    return next((found for found in map(node.child_by_field_name, names) if found is not None),
                None)


def _through_receiver(callee, count: Count) -> bool:
    owner, member = _field(callee, OWNER_FIELDS), _field(callee, MEMBER_FIELDS)
    return (owner is not None and member is not None and _text(member, count.data) == count.name
            and _text(owner, count.data) in count.receivers)


def _fits(call, count: Count) -> bool:
    """A call that passes a number of arguments this function does not take is
    another overload's."""
    if count.arity is None:
        return True
    passed = _passed(call, count)
    return _takes(count.arity, passed) and not any(_takes(other, passed) for other in count.others)


def _passed(call, count: Count) -> int:
    listed = call.child_by_field_name("arguments")
    return 0 if listed is None else sum(
        1 for kid in listed.named_children if kid.type not in count.spec.comments)


def _takes(span: tuple, passed: int) -> bool:
    return span[0] <= passed <= span[1]


def _recursion(node, count: Count) -> int:
    if count.recursed or not count.name or not _calls_itself(node, count):
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


# Per grammar where one name can belong to several functions: the parameter nodes
# a call must fill, those a default lets it leave out, and those taking any number.
ARITY = {
    "tree_sitter_cpp": (frozenset({"parameter_declaration"}),
                        frozenset({"optional_parameter_declaration"}),
                        frozenset({"variadic_parameter_declaration", "..."})),
    "tree_sitter_java": (frozenset({"formal_parameter"}), frozenset(),
                         frozenset({"spread_parameter"})),
}


def arity(fn, spec, data: bytes) -> tuple | None:
    """(fewest, most) arguments a call to fn passes, where names overload."""
    kinds, listed = ARITY.get(spec.grammar), counters._parameter_list(fn)
    if kinds is None or listed is None:
        return None
    return _span([kid.type for kid in listed.children if _text(kid, data) != b"void"], *kinds)


def _span(types: list, required, optional, variadic) -> tuple:
    fewest = sum(kind in required for kind in types)
    taken = sum(kind in required | optional for kind in types)
    return fewest, float("inf") if variadic & set(types) else taken


# What holds a member function, where a name can overload inside it.
CLASS_BODIES = frozenset({"class_specifier", "struct_specifier", "union_specifier",
                          "class_declaration", "interface_declaration", "enum_declaration",
                          "record_declaration"})
_FILE: dict = {}  # the last file's functions by (class, name); see _index


def overloads(fn, spec, data: bytes) -> tuple:
    """The arities of the other functions of fn's name in its class, where
    names overload. One with the same parameters is fn written again, in
    another preprocessor branch."""
    if ARITY.get(spec.grammar) is None:
        return ()
    holder, name, parameters = _signature(fn, data)
    return tuple(span for start, spelled, span in _index(fn, spec, data).get((holder, name), ())
                 if start != fn.start_byte and spelled != parameters)


def _index(fn, spec, data: bytes) -> dict:
    """Every function of fn's file by (class, name), read once per file."""
    if _FILE.get("data") is not data or _FILE.get("grammar") != spec.grammar:
        root = fn
        while root.parent is not None:
            root = root.parent
        _FILE.update(data=data, grammar=spec.grammar, index=_by_name(root, spec, data))
    return _FILE["index"]


class _Tree:
    def __init__(self, root):
        self.root_node = root


def _by_name(root, spec, data: bytes) -> dict:
    index: dict = {}
    for fn in counters.functions(_Tree(root), spec):
        holder, name, parameters = _signature(fn, data)
        index.setdefault((holder, name), []).append((fn.start_byte, parameters, arity(fn, spec, data)))
    return index


def _signature(fn, data: bytes) -> tuple:
    listed = counters._parameter_list(fn)
    spelled = b"" if listed is None else b" ".join(_text(listed, data).split())
    return _holder(fn, data), counters.name(fn, data), spelled


def _holder(fn, data: bytes) -> bytes:
    """The class fn is a member of, by name: the class around it, or the scope
    a C++ definition outside its class names (`V::starts_with`)."""
    node = fn.parent
    while node is not None and node.type not in CLASS_BODIES:
        node = node.parent
    if node is None:
        return _qualifier(fn, data)
    named = node.child_by_field_name("name")
    return b"" if named is None else _text(named, data)


def _qualifier(fn, data: bytes) -> bytes:
    node = fn.child_by_field_name("declarator")
    while node is not None and node.type in counters.DECLARATOR_TYPES:
        node = node.child_by_field_name("declarator")
    scope = _scope(node)
    return b"" if scope is None else _text(scope, data)


def _scope(declared):
    if declared is None or declared.type != "qualified_identifier":
        return None
    return declared.child_by_field_name("scope")


def impl_of(fn):
    """The Rust impl or trait fn stands directly in, or None."""
    holder = fn.parent
    outer = holder.parent if holder is not None and holder.type == "declaration_list" else None
    return outer if outer is not None and outer.type in ("impl_item", "trait_item") else None


def _type_name(node):
    """`R` in `R`, `W<T>` and `a::R`."""
    while node is not None and node.type in ("generic_type", "scoped_type_identifier"):
        node = node.child_by_field_name("type" if node.type == "generic_type" else "name")
    return node


def _receivers(fn, data: bytes) -> frozenset:
    impl = impl_of(fn)
    typed = None if impl is None else _type_name(
        impl.child_by_field_name("type") or impl.child_by_field_name("name"))
    return SELF_RECEIVERS | (frozenset() if typed is None else {_text(typed, data)})


def measure(fn, spec, data: bytes) -> Count:
    name = counters.name_node(fn)
    count = Count(spec, data, b"" if name is None else data[name.start_byte:name.end_byte],
                  method_selector(fn, data), receivers=_receivers(fn, data),
                  bare=impl_of(fn) is None, arity=arity(fn, spec, data),
                  others=overloads(fn, spec, data))
    for child in (child for child in fn.children if child.type not in PARAMETER_LISTS):
        _visit(child, 0, count)
    return count


def cognitive(fn, spec, data: bytes) -> int:
    return measure(fn, spec, data).total
