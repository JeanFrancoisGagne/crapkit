"""The Swift shapes crapkit's reader counts differently from the tree-sitter
counters, found triaging Alamofire: one detector per rulings row.

crapkit reads Swift with lizard 1.24.0's SwiftReader, which reads a function
body with the same state machine as the top of the file. A word that opens a
declaration there (get, set, didSet, protocol, type) opens one in the middle of
an expression, a `,` takes the next token as a declared name, and a `#` starts
a preprocessor line that swallows the rest of its line. The tree-sitter
grammar reads each of these as the expression it is.

RULES lists (rulings id, columns, predicate); ts_defect_shapes turns each into
a Shape for Swift, and adds extra_line to its EXTRA_ROWS. `row` in the columns
means the function's row itself. No crapkit import.
"""
from __future__ import annotations

from bisect import bisect_left

from accuracy.analysis_oracles.oracles import treesitter_counters as counters

CCN = ("ccn_std", "ccn_mod")
COGNITIVE = ("cognitive",)
NESTING = ("nesting",)
PARAMS = ("params",)
EVERY = ("row", "end", "ccn_std", "ccn_mod", "cognitive", "nesting", "nloc", "params")

# Tokens after which the reader takes the next token as a declared name.
TAKES_A_NAME = frozenset({b"let", b"var", b"case", b",", b"func"})
# Declarations whose parameter clause the reader reads as parameters.
SIGNATURES = frozenset({"function_declaration", "init_declaration", "subscript_declaration"})
ACCESSOR_WORDS = frozenset({b"get", b"set", b"willSet", b"didSet", b"deinit", b"subscript"})
LABEL_KEYWORDS = frozenset({b"for", b"while", b"if", b"catch", b"guard", b"case"})
ACCESSORS = frozenset({"getter_specifier", "setter_specifier", "modify_specifier",
                       "willset_clause", "didset_clause", "subscript_declaration"})
ACCESSOR_KEYWORDS = frozenset({"get", "set", "_modify", "willSet", "didSet", "subscript"})


def walk(node):
    """node and every node under it."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _ancestors(node):
    node = node.parent
    while node is not None:
        yield node
        node = node.parent


def _cached(context, key: str, build):
    if key not in context.facts:
        context.facts[key] = build(context)
    return context.facts[key]


def _function_starts(context) -> list:
    return sorted(fn.start_byte for fn in counters.functions(context.tree, context.spec))


def _first_after(starts: list, byte: int):
    return next((start for start in starts if start > byte), None)


def _line(context, byte: int) -> int:
    return context.data.count(b"\n", 0, byte) + 1


# --- the reader's token stream, seen through the tree ------------------------------------------

def _leaves(context) -> tuple:
    """(start bytes, leaves) of every token that is not a comment, in file order."""
    leaves = sorted((node for node in walk(context.tree.root_node)
                     if not node.children and node.type not in context.spec.comments),
                    key=lambda node: node.start_byte)
    return [leaf.start_byte for leaf in leaves], leaves


def previous_token(node, context) -> bytes:
    starts, leaves = _cached(context, "leaves", _leaves)
    index = bisect_left(starts, node.start_byte)
    return context.text(leaves[index - 1]) if index else b""


def next_tokens(node, context, count: int) -> list:
    starts, leaves = _cached(context, "leaves", _leaves)
    index = bisect_left(starts, node.end_byte)
    return [context.text(leaf) for leaf in leaves[index:index + count]]


def _clause(declaration) -> tuple:
    """The bytes of a declaration's parameter clause, from its `(` to its `)`."""
    kinds = [kid.type for kid in declaration.children]
    if "(" not in kinds or ")" not in kinds:
        return (0, 0)
    return (declaration.children[kinds.index("(")].start_byte,
            declaration.children[kinds.index(")")].end_byte)


def _in_string(node) -> bool:
    """node sits in a string literal, which the reader takes as one token."""
    return any("string_literal" in above.type for above in _ancestors(node))


def _hides(above, node) -> bool:
    """`above` is a protocol body (the reader skips it) or a declaration whose
    parameter clause holds node (the reader reads it as parameters)."""
    if above.type == "protocol_declaration":
        return True
    first, last = _clause(above) if above.type in SIGNATURES else (0, 0)
    return first <= node.start_byte < last


def _read_as_declaration(node) -> bool:
    """node sits where the reader does not read statements."""
    return _in_string(node) or any(_hides(above, node) for above in _ancestors(node))


def _word(node, context, words) -> bool:
    """node is one of `words` where the reader reads statements, as an expression."""
    named = node.type == "simple_identifier" and context.text(node) in words
    return named and previous_token(node, context) not in TAKES_A_NAME and (
        not _read_as_declaration(node))


def _modifier_set(node) -> bool:
    """The `set` of `private(set)`, `internal(set)` and the like."""
    return node.type == "set" and node.parent is not None and (
        node.parent.type == "visibility_modifier")


# --- AO-SWIFT-KEYWORD-LABEL (calc-bug analysis-oracles-79) --------------------------------------

def _keyword_label(parameter, context) -> bool:
    kids = parameter.children
    return len(kids) > 1 and kids[0].type == "simple_identifier" and (
        context.text(kids[0]) in LABEL_KEYWORDS and kids[1].type != ":")


def keyword_label(fn, context) -> bool:
    """A parameter whose argument label is a keyword the reader counts as a
    structure, followed by its own name: `func value(for name: String)`."""
    return any(kid.type == "parameter" and _keyword_label(kid, context) for kid in fn.children)


# --- AO-SWIFT-OPTIONAL-MARK (calc-bug analysis-oracles-80) --------------------------------------

def _after_close(node, context) -> bool:
    return context.data[node.start_byte - 1:node.start_byte] in (b")", b"]", b">", b"`")


def optional_mark(fn, context) -> bool:
    """A `?` right after `)`, `]` or `>`: an optional type such as `(any Error)?`
    or `[Int]?`, or an optional chain such as `f()?.g`, in fn's signature or body.
    An optional chain after a call also holds AO-SWIFT-OPTIONAL-CHAIN's shape."""
    return any(node.type == "?" and node.parent.type != "ternary_expression"
               and _after_close(node, context) and not _in_string(node)
               for node in counters.own_nodes(fn, context.spec))


# --- AO-SWIFT-OPTIONAL-CHAIN (calc-bug analysis-oracles-162) -----------------------------------

def optional_chain(fn, context) -> bool:
    """An optional chain (`a?.b`, `f()?.g`, `c?()`) in fn: the counters count each `?`
    as a decision; the reader counts none after a name and opens a nesting level after
    `)` or `]`."""
    return any(counters.optional_chains(node, context.spec, context.data)
               for node in counters.own_nodes(fn, context.spec))


# --- AO-SWIFT-ACCESSOR-WORD (calc-bug analysis-oracles-81) --------------------------------------

def _accessor_words(context) -> list:
    """The start bytes of each get, set, willSet, didSet, deinit or subscript the
    reader takes for an accessor or declaration where the code uses it as a name."""
    return [node.start_byte for node in walk(context.tree.root_node)
            if _word(node, context, ACCESSOR_WORDS) or _modifier_set(node)]


def _near(fn, context, key: str, find) -> bool:
    """fn holds one of the bytes `find` lists, or is the first function after one."""
    found = _cached(context, key, find)
    after = _cached(context, key + "-after", lambda c: {
        _first_after(_cached(c, "starts", _function_starts), byte) for byte in found})
    return fn.start_byte in after or any(fn.start_byte <= byte < fn.end_byte for byte in found)


def near_accessor_word(fn, context) -> bool:
    """fn uses an accessor word as a name (`result.get()`, `case .get`,
    `private(set)`), or is the first function after one: the reader opens a
    function there that takes the next `{` as its body."""
    return _near(fn, context, "accessor-words", _accessor_words)


# --- AO-SWIFT-PROTOCOL-WORD (calc-bug analysis-oracles-82) --------------------------------------

def _protocol_words(context) -> list:
    return [node.start_byte for node in walk(context.tree.root_node)
            if _word(node, context, {b"protocol"})]


def after_protocol_word(fn, context) -> bool:
    """fn holds `protocol` as a label or member name (`K(protocol: p)`,
    `self.protocol`), or comes after one: the reader skips a brace block from there
    and loses count of the braces, so no later function gets its own row."""
    words = _cached(context, "protocol-words", _protocol_words)
    return any(byte < fn.end_byte for byte in words)


# --- AO-SWIFT-FAILABLE-INIT (calc-bug analysis-oracles-83) --------------------------------------

def failable_init(fn, context) -> bool:
    """A failable initializer, `init?(...)` or `init!(...)`."""
    kinds = [kid.type for kid in fn.children]
    return fn.type == "init_declaration" and "init" in kinds and (
        kinds[kinds.index("init") + 1:kinds.index("init") + 2] in (["?"], ["!"]))


# --- AO-SWIFT-HASH-LINE (calc-bug analysis-oracles-84) ------------------------------------------

def _hash_constructs(context) -> list:
    """The constructs a `#` starts outside compiler directives (#fileID, #available,
    #selector), with code after them on the same line."""
    return [node.parent for node in walk(context.tree.root_node)
            if node.type == "#" and node.parent is not None and _code_after(node.parent, context)]


def _code_after(construct, context) -> bool:
    end = context.data.find(b"\n", construct.end_byte)
    rest = context.data[construct.end_byte:end if end >= 0 else len(context.data)]
    return rest.split(b"//")[0].strip(b" \t\r),") != b""


def _body_start(fn) -> int:
    body = next((kid for kid in fn.children if kid.type == "function_body"), fn)
    return body.start_byte


def hash_line(fn, context) -> bool:
    """fn holds a `#` construct with code after it on its line, or comes after one in
    another function's signature: the reader drops the rest of that line, so a `{`,
    `}`, `)` or decision there is lost."""
    constructs = _cached(context, "hash", _hash_constructs)
    return any(fn.start_byte <= node.start_byte < fn.end_byte or _in_a_signature(node, fn)
               for node in constructs)


def _in_a_signature(node, fn) -> bool:
    """node sits in the signature of a function that ends before fn starts."""
    owner = next((above for above in _ancestors(node) if above.type in SIGNATURES), None)
    return owner is not None and node.start_byte < _body_start(owner) and (
        owner.end_byte <= fn.start_byte)


# --- AO-SWIFT-TYPE-WORD (calc-bug analysis-oracles-85) ------------------------------------------

def _type_words(context) -> list:
    """Each `type` word the reader takes for a Go-style type declaration, where one
    of the two tokens it swallows after it is a brace (`return type` before `}`)."""
    return [node.start_byte for node in walk(context.tree.root_node)
            if _word(node, context, {b"type"})
            and {b"{", b"}"} & set(next_tokens(node, context, 2))]


def after_type_word(fn, context) -> bool:
    """fn holds such a `type` word, or comes after one: the brace count is lost from
    there."""
    words = _cached(context, "type-words", _type_words)
    return any(byte < fn.end_byte for byte in words)


# --- AO-SWIFT-TYPE-COMMA (calc-bug analysis-oracles-86) -----------------------------------------

def _inner_comma(kid) -> bool:
    return kid.type != "," and any(node.type == "," for node in walk(kid))


def type_comma(fn, context) -> bool:
    """A `,` inside one parameter: a function or tuple type with two members, a
    generic type with two arguments, a default array literal."""
    first, last = _clause(fn)
    return any(first < kid.start_byte < last and _inner_comma(kid) for kid in fn.children)


# --- AO-SWIFT-COMMA-BRACE (calc-bug analysis-oracles-87) ----------------------------------------

def comma_brace(fn, context) -> bool:
    """A closure right after a `,` (`g(1, { x in ... })`): the reader takes its `{`
    as a declared name, so the closure's `}` ends the function."""
    return any(node.type == "{" and previous_token(node, context) == b","
               for node in counters.own_nodes(fn, context.spec))


# --- AO-SWIFT-TRY-ND (calc-bug analysis-oracles-88) ---------------------------------------------

def _plain_try(node, context) -> bool:
    after = context.data[node.end_byte:node.end_byte + 1]
    return node.type == "try_operator" and context.text(node) == b"try" and after not in b"?!"


def plain_try(fn, context) -> bool:
    """A `try` expression (not `try?` or `try!`): the reader opens a nesting level
    for it that no brace closes."""
    return any(_plain_try(node, context) for node in counters.own_nodes(fn, context.spec))


# --- AO-SWIFT-GUARD-ND and AO-SWIFT-GUARD-COG (calc-bug analysis-oracles-89) ---------------------

def has_guard(fn, context) -> bool:
    """A guard statement: its else body is one level deep, which the reader does not count."""
    return any(node.type in context.spec.guards for node in counters.own_nodes(fn, context.spec))


def _structure_above(node, fn, context) -> bool:
    spec = context.spec
    kinds = spec.ifs | spec.loops | spec.switches | spec.catches | spec.guards | spec.lambdas
    return any(above.type in kinds for above in _ancestors(node)
               if above.start_byte > fn.start_byte)


def nested_guard(fn, context) -> bool:
    """A guard inside another structure or a closure: the reader adds 1 for it with no
    nesting increment, and reads the structures after it one level too shallow."""
    return any(node.type in context.spec.guards and _structure_above(node, fn, context)
               for node in counters.own_nodes(fn, context.spec))


# --- AO-TSCOG-SWIFT-ELSE-COMMENT (oracle bug in the tree-sitter cognitive counter) ---------------

def _comment_after_else(node, context) -> bool:
    kinds = [kid.type for kid in node.children]
    after = kinds[kinds.index("else") + 1:] if "else" in kinds else []
    return any(kind in context.spec.comments for kind in after)


def else_comment(fn, context) -> bool:
    """A comment right inside a plain else's braces: the Swift grammar hangs it on
    the if statement, and the tree-sitter cognitive counter counts it as one more else."""
    return any(node.type in context.spec.ifs and _comment_after_else(node, context)
               for node in counters.own_nodes(fn, context.spec))


# --- AO-SWIFT-DIRECTIVE-LOGICAL (oracle bug in the tree-sitter counters) -------------------------

def directive_logical(fn, context) -> bool:
    """A compiler directive whose condition holds && or || (`#if canImport(A) && B`):
    the tree-sitter counters count those operators as runtime decisions."""
    return any(node.type == "directive" and counters.logical_operators(node, context.spec)
               for node in counters.own_nodes(fn, context.spec))


RULES = [
    ("AO-SWIFT-KEYWORD-LABEL", CCN, keyword_label),
    ("AO-SWIFT-KEYWORD-LABEL-COG", COGNITIVE, keyword_label),
    ("AO-SWIFT-KEYWORD-LABEL-ND", NESTING, keyword_label),
    ("AO-SWIFT-OPTIONAL-MARK", CCN, optional_mark),
    ("AO-SWIFT-OPTIONAL-MARK-COG", COGNITIVE, optional_mark),
    ("AO-SWIFT-OPTIONAL-MARK-ND", NESTING, optional_mark),
    ("AO-SWIFT-OPTIONAL-CHAIN", CCN, optional_chain),
    ("AO-SWIFT-OPTIONAL-CHAIN-ND", NESTING, optional_chain),
    ("AO-SWIFT-ACCESSOR-WORD", EVERY, near_accessor_word),
    ("AO-SWIFT-PROTOCOL-WORD", EVERY, after_protocol_word),
    ("AO-SWIFT-FAILABLE-INIT", EVERY, failable_init),
    ("AO-SWIFT-HASH-LINE", EVERY, hash_line),
    ("AO-SWIFT-TYPE-WORD", EVERY, after_type_word),
    ("AO-SWIFT-TYPE-COMMA", PARAMS, type_comma),
    ("AO-SWIFT-COMMA-BRACE", EVERY, comma_brace),
    ("AO-SWIFT-TRY-ND", NESTING, plain_try),
    ("AO-SWIFT-GUARD-ND", NESTING, has_guard),
    ("AO-SWIFT-GUARD-COG", COGNITIVE, nested_guard),
    ("AO-SWIFT-DIRECTIVE-LOGICAL", CCN, directive_logical),
    ("AO-SWIFT-DIRECTIVE-LOGICAL-COG", COGNITIVE, directive_logical),
    ("AO-TSCOG-SWIFT-ELSE-COMMENT", COGNITIVE, else_comment),
]


# --- rows crapkit lists where tree-sitter lists no function ------------------------------------

def _accessor_line(node, context):
    """The line of an accessor or subscript keyword (AO-SWIFT-ACCESSOR-ROWS)."""
    if node.type in ACCESSOR_KEYWORDS and node.parent is not None and (
            node.parent.type in ACCESSORS):
        return _line(context, node.start_byte)
    return None


def _extra_lines(context) -> set:
    accessors = {_accessor_line(node, context) for node in walk(context.tree.root_node)}
    phantoms = {_line(context, byte) for byte in _cached(context, "accessor-words",
                                                         _accessor_words)}
    return (accessors | phantoms) - {None}


def extra_line(context, start: int) -> bool:
    """A crapkit row at a line tree-sitter lists no function on that a recorded shape
    explains: an accessor block or a subscript (AO-SWIFT-ACCESSOR-ROWS, a definition),
    or a phantom accessor word (AO-SWIFT-ACCESSOR-WORD)."""
    return start in _cached(context, "extra-lines", _extra_lines)
