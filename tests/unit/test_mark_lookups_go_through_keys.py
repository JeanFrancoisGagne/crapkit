"""Every mark lookup goes through keys.MarkIndex and keys.rows_by_key.

A module that builds its own dict or set of marks keyed on (path, long_name) is
free to pick its own winner when a marks file lists one key twice: verify and
ratchet each did, the dicts keeping the last mark and ratchet.mark_for the
first. This guard reads the scanned modules with `ast` and names each private
mark index and each import of `rows_by_key` from verify.

What counts as a private mark index: a dict or set keyed on `(x.path,
x.long_name)` or `mark_key(x)` over a collection of marks, whether a comprehension builds it,
dict(), set() or frozenset() is handed a generator or a list, or a for loop
fills it with `b[key] = ...`, `b.setdefault(key, ...)` or `b.add(key)`. A collection counts as marks unless
it is scored rows: a parameter annotated with ScoredRow, or one of the names
crapkit's modules give scored rows. An unknown collection counts as marks, so a
new lookup fails here before anyone decides it is not one.

ALLOWED names the (file, function) sites a later ticket deletes. It is empty for
verify.py and ratchet.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"
SCANNED = ("verify.py", "ratchet.py")
ALLOWED: dict[str, frozenset[str]] = {"verify.py": frozenset(), "ratchet.py": frozenset()}
SCORED_NAMES = frozenset({"rows", "fresh", "scored"})
BUILDERS = frozenset({"dict", "set", "frozenset"})
LOOP_WRITES = frozenset({"setdefault", "add"})


def _mark_key_of(node: ast.AST) -> str | None:
    """The variable a `(x.path, x.long_name)` tuple or a `mark_key(x)` call reads,
    or None for any other node."""
    if _calls_mark_key(node):
        return node.args[0].id
    if not (isinstance(node, ast.Tuple) and len(node.elts) == 2):
        return None
    attrs = [e for e in node.elts if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)]
    if len(attrs) != 2 or [a.attr for a in attrs] != ["path", "long_name"]:
        return None
    owner = {a.value.id for a in attrs}
    return owner.pop() if len(owner) == 1 else None


def _calls_mark_key(node: ast.AST) -> bool:
    """Is `node` keys.mark_key on one variable, the key spelled through keys?"""
    if not (isinstance(node, ast.Call) and len(node.args) == 1 and isinstance(node.args[0], ast.Name)):
        return False
    func = node.func
    return (func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)) == "mark_key"


def _keyed_element(comp: ast.AST) -> ast.AST | None:
    """The key a comprehension builds its dict or set on."""
    if isinstance(comp, ast.DictComp):
        return comp.key
    if isinstance(comp, ast.SetComp):
        return comp.elt
    if isinstance(comp, (ast.GeneratorExp, ast.ListComp)):
        # dict() reads (key, value) pairs; set() and frozenset() read the keys.
        pair = comp.elt
        if isinstance(pair, ast.Tuple) and len(pair.elts) == 2 and _mark_key_of(pair) is None:
            return pair.elts[0]
        return pair
    return None


def _builds(node: ast.AST) -> list[ast.AST]:
    """The dict and set comprehensions `node` is, or the generator or list a
    dict(), set() or frozenset() call is handed."""
    if isinstance(node, (ast.DictComp, ast.SetComp)):
        return [node]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILDERS:
        return [arg for arg in node.args if isinstance(arg, (ast.GeneratorExp, ast.ListComp))]
    return []


def _scored(fn: ast.AST) -> set[str]:
    """The parameters of `fn` annotated as scored rows."""
    args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs if hasattr(fn, "args") else []
    return {a.arg for a in args if a.annotation is not None and "ScoredRow" in ast.unparse(a.annotation)}


def _walks_marks(iterable: ast.AST, scored: set[str]) -> bool:
    """Is `iterable` a collection that is not scored rows? Anything but a known name counts."""
    source = iterable.id if isinstance(iterable, ast.Name) else None
    return source not in SCORED_NAMES and source not in scored


def _over_marks(comp: ast.AST, owner: str, scored: set[str]) -> bool:
    """Does the comprehension's `owner` walk a collection that is not scored rows?"""
    for gen in comp.generators:
        if isinstance(gen.target, ast.Name) and gen.target.id == owner:
            return _walks_marks(gen.iter, scored)
    return False


def _comprehension_indexes(node: ast.AST, scored: set[str]) -> list[int]:
    found = []
    for comp in _builds(node):
        owner = _mark_key_of(_keyed_element(comp))
        if owner is not None and _over_marks(comp, owner, scored):
            found.append(comp.lineno)
    return found


def _stored_key(node: ast.AST) -> ast.AST | None:
    """The key a loop body writes: `b[key] = ...`, `b.setdefault(key, ...)` or `b.add(key)`."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        return node.slice
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        return node.args[0] if node.func.attr in LOOP_WRITES else None
    return None


def _loop_indexes(node: ast.AST, scored: set[str]) -> list[int]:
    """The lines where a for loop over marks writes its entries into a dict or set by (path, long_name)."""
    if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name)) or not _walks_marks(node.iter, scored):
        return []
    body = [inner for stmt in node.body for inner in ast.walk(stmt)]
    return [inner.lineno for inner in body if _mark_key_of(_stored_key(inner)) == node.target.id]


def _private_indexes(fn: ast.AST) -> list[int]:
    scored = _scored(fn)
    found = []
    for node in ast.walk(fn):
        found += _comprehension_indexes(node, scored) + _loop_indexes(node, scored)
    return sorted(found)


def _verify_imports(fn: ast.AST) -> list[int]:
    return [node.lineno for node in ast.walk(fn)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "verify"
            and any(alias.name == "rows_by_key" for alias in node.names)]


def _sites(tree: ast.Module) -> list[tuple[str, int, str]]:
    """(enclosing top-level name, line, what) for each lookup that bypasses keys."""
    found = []
    for top in tree.body:
        name = getattr(top, "name", "<module>")
        found += [(name, line, "builds its own (path, long_name) mark index") for line in _private_indexes(top)]
        found += [(name, line, "imports rows_by_key from verify") for line in _verify_imports(top)]
    return found


def violations(source: str, allowed: frozenset[str] = frozenset()) -> list[str]:
    tree = ast.parse(source)
    return [f"{name}:{line}: {what}" for name, line, what in _sites(tree) if name not in allowed]


@pytest.mark.parametrize("module", SCANNED)
def test_every_mark_lookup_goes_through_keys(module):
    found = violations((SRC / module).read_text(encoding="utf-8"), ALLOWED[module])

    assert found == [], f"{module} looks marks up past keys.MarkIndex: {found}"


def test_the_allowlist_is_empty_for_verify_and_ratchet():
    assert ALLOWED["verify.py"] == ALLOWED["ratchet.py"] == frozenset()


SEEDED = '''
from .verify import rows_by_key

def merge(base: list[RatchetEntry]):
    return {(e.path, e.long_name): e.crap for e in base}

def present(prior):
    return {(e.path, e.long_name) for e in prior}

def via_call(prior):
    return dict(((e.path, e.long_name), e) for e in prior)

def local():
    from .verify import rows_by_key

def groups(rows):
    return {(row.path, row.long_name) for row in rows}

def scored_param(found: list[ScoredRow]):
    return {(r.path, r.long_name) for r in found}

def through_keys(prior):
    return MarkIndex(prior)

def loop_store(base):
    b = {}
    for e in base:
        b[(e.path, e.long_name)] = e.crap
    return b

def loop_setdefault(entries):
    b = {}
    for e in entries:
        b.setdefault((e.path, e.long_name), e)
    return b

def set_of_list(prior):
    return set([(e.path, e.long_name) for e in prior])

def dict_of_list(base):
    return dict([((e.path, e.long_name), e.crap) for e in base])

def loop_add(prior):
    seen = set()
    for e in prior:
        seen.add((e.path, e.long_name))
    return seen

def loop_over_rows(rows):
    b = {}
    for row in rows:
        b[(row.path, row.long_name)] = row
    return b

def through_mark_key(prior):
    b = {}
    for e in prior:
        b.setdefault(mark_key(e), e)
    return {keys.mark_key(e): e.crap for e in prior}
'''


def test_the_guard_names_each_seeded_lookup_and_passes_scored_rows():
    assert violations(SEEDED) == [
        "<module>:2: imports rows_by_key from verify",
        "merge:5: builds its own (path, long_name) mark index",
        "present:8: builds its own (path, long_name) mark index",
        "via_call:11: builds its own (path, long_name) mark index",
        "local:14: imports rows_by_key from verify",
        "loop_store:28: builds its own (path, long_name) mark index",
        "loop_setdefault:34: builds its own (path, long_name) mark index",
        "set_of_list:38: builds its own (path, long_name) mark index",
        "dict_of_list:41: builds its own (path, long_name) mark index",
        "loop_add:46: builds its own (path, long_name) mark index",
        "through_mark_key:58: builds its own (path, long_name) mark index",
        "through_mark_key:59: builds its own (path, long_name) mark index",
    ]


def test_the_allowlist_excuses_a_named_function():
    assert violations(SEEDED, frozenset({"<module>", "merge", "present", "via_call", "local", "loop_store",
                                         "loop_setdefault", "set_of_list", "dict_of_list", "loop_add",
                                         "through_mark_key"})) == []
