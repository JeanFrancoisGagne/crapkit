"""Every mark lookup goes through keys.MarkIndex and keys.rows_by_key.

A module that builds its own dict or set of marks keyed on (path, long_name) is
free to pick its own winner when a marks file lists one key twice: verify and
ratchet each did, the dicts keeping the last mark and the one-key lookup the
first. This guard reads the scanned modules with `ast` and names each private
mark index and each import of `rows_by_key` from verify.

What counts as a private mark index: a dict or set keyed on `(x.path,
x.long_name)` or `mark_key(x)` over a collection of marks, whether a comprehension builds it,
dict(), set() or frozenset() is handed a generator or a list, or a for loop
fills it with `b[key] = ...`, `b.setdefault(key, ...)` or `b.add(key)`. A collection counts as marks unless
it is scored rows: a parameter annotated with ScoredRow, or one of the names
crapkit's modules give a collection that holds no marks. An unknown collection counts as marks, so a
new lookup fails here before anyone decides it is not one.

The guard scans every module under src/crapkit but keys.py, the one module
that owns the lookup. ALLOWED names the three pardon copies left outside keys,
each with the ticket that deletes it. An entry whose function is gone from its
file is inert: the guard neither fails on it nor asks for its removal, so the
tickets that delete those functions leave this file alone.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shutil

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"
OWNER = "keys.py"
# (module under src/crapkit, top-level function) -> the ticket that deletes it.
ALLOWED: dict[tuple[str, str], str] = {
    ("cli/verifying.py", "_split_marked"): "gate-group-08",
    ("cli/scoring.py", "_unmarked_breaches"): "gate-group-07",
    ("cli/claude_hook.py", "_known_marks"): "gate-group-09",
}
# Collections that hold no RatchetEntry rows: scored rows, and the
# TightenRefusal rows `ratchet.unstable_marks` returns.
SCORED_NAMES = frozenset({"rows", "fresh", "scored", "baseline_scored", "refusals"})
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


def _key_names(body: list[ast.AST], owner: str) -> set[str]:
    """The local names a loop body binds to its entry's key: `key = (e.path, e.long_name)`."""
    return {target.id for inner in body if isinstance(inner, ast.Assign) and _mark_key_of(inner.value) == owner
            for target in inner.targets if isinstance(target, ast.Name)}


def _writes_key(stored: ast.AST | None, owner: str, names: set[str]) -> bool:
    """Is `stored` the entry's key, spelled out or through a local name bound to it?"""
    return _mark_key_of(stored) == owner or (isinstance(stored, ast.Name) and stored.id in names)


def _loop_indexes(node: ast.AST, scored: set[str]) -> list[int]:
    """The lines where a for loop over marks writes its entries into a dict or set by (path, long_name)."""
    if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name)) or not _walks_marks(node.iter, scored):
        return []
    owner = node.target.id
    body = [inner for stmt in node.body for inner in ast.walk(stmt)]
    names = _key_names(body, owner)
    return [inner.lineno for inner in body if _writes_key(_stored_key(inner), owner, names)]


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


def _allowed_in(module: str) -> frozenset[str]:
    return frozenset(name for (path, name) in ALLOWED if path == module)


def scan(src: Path) -> list[str]:
    """Every lookup past keys in the package at `src`, as `module:function:line: what`."""
    found = []
    for path in sorted(src.rglob("*.py")):
        module = path.relative_to(src).as_posix()
        if module != OWNER:
            found += [f"{module}:{v}" for v in violations(path.read_text(encoding="utf-8"),
                                                          _allowed_in(module))]
    return found


def test_every_mark_lookup_goes_through_keys():
    found = scan(SRC)

    assert found == [], f"modules look marks up past keys.MarkIndex: {found}"


def test_the_allowlist_holds_the_three_pardon_copies_and_who_deletes_them():
    assert ALLOWED == {
        ("cli/verifying.py", "_split_marked"): "gate-group-08",
        ("cli/scoring.py", "_unmarked_breaches"): "gate-group-07",
        ("cli/claude_hook.py", "_known_marks"): "gate-group-09",
    }
    assert all((SRC / module).is_file() for module, _ in ALLOWED)


def _without(source: str, function: str) -> str:
    """`source` with the top-level `function` cut out, if it is there."""
    node = next((n for n in ast.parse(source).body if getattr(n, "name", None) == function), None)
    if node is None:
        return source
    lines = source.splitlines(keepends=True)
    return "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])


def test_an_allowlist_entry_whose_function_is_gone_is_inert(tmp_path):
    copy = tmp_path / "crapkit"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    listed = copy / "cli" / "verifying.py"
    text = listed.read_text(encoding="utf-8")
    listed.write_text(_without(text, "_split_marked"), encoding="utf-8")

    assert "def _split_marked" not in listed.read_text(encoding="utf-8")
    assert scan(copy) == [], "a deleted pardon copy leaves the guard green"


def test_the_allowlist_excuses_only_its_own_file():
    seeded = "def _split_marked(entries):\n    return {(e.path, e.long_name) for e in entries}\n"

    assert violations(seeded, _allowed_in("cli/verifying.py")) == []
    assert violations(seeded, _allowed_in("cli/queue.py")) == [
        "_split_marked:2: builds its own (path, long_name) mark index"]


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

def via_local_key(prior):
    marks = {}
    for e in prior:
        key = (e.path, e.long_name)
        marks[key] = max(e.crap, marks.get(key, e.crap))
    return marks
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
        "via_local_key:65: builds its own (path, long_name) mark index",
    ]


def test_the_allowlist_excuses_a_named_function():
    assert violations(SEEDED, frozenset({"<module>", "merge", "present", "via_call", "local", "loop_store",
                                         "loop_setdefault", "set_of_list", "dict_of_list", "loop_add",
                                         "through_mark_key", "via_local_key"})) == []


def test_no_module_names_the_deleted_one_key_lookup():
    named = [path.relative_to(SRC).as_posix() for path in sorted(SRC.rglob("*.py"))
             if "mark_for" in path.read_text(encoding="utf-8")]

    assert named == [], "ratchet.mark_for is deleted; read a mark through keys.MarkIndex"


def test_highest_marks_keeps_the_higher_mark_when_a_file_lists_a_key_twice():
    from crapkit.keys import highest_marks
    from crapkit.ratchet import RatchetEntry

    entries = [RatchetEntry("a.py", "run", 12.0), RatchetEntry("a.py", "run#2", 30.0),
               RatchetEntry("a.py", "run", 40.0), RatchetEntry("b.py", "run", 7.5),
               RatchetEntry("b.py", "run", 3.0)]

    assert highest_marks(entries) == {("a.py", "run"): 40.0, ("a.py", "run#2"): 30.0, ("b.py", "run"): 7.5}
    assert highest_marks([]) == {}


def test_the_no_mark_rises_check_reads_the_higher_of_two_lines():
    import pytest

    from crapkit.errors import InternalCheckError
    from crapkit.invariants import check_marks_kept
    from crapkit.ratchet import RatchetEntry

    prior = [RatchetEntry("a.py", "run", 12.0), RatchetEntry("a.py", "run", 40.0)]

    check_marks_kept(prior, [RatchetEntry("a.py", "run", 40.0)], adds=False)
    with pytest.raises(InternalCheckError, match="a mark never rises"):
        check_marks_kept(prior, [RatchetEntry("a.py", "run", 40.5)], adds=False)
