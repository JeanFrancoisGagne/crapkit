"""Nothing reads or documents the eight keys 0.8.1's verify --json listed its
findings under.

0.9.0 lists every finding once under `findings` and the numbers beside them
under `counts`, and verify --json no longer prints the 0.8.1 per-kind keys. A
reader left on one of those keys reads nothing, so this scan finds every one
that is left.

Python under src/ and tests/: every read of an old key fails unless one of
the exemptions below holds. A read is `x[key]`, `x.get(key)` or `x.pop(key)`,
where the key is the old key's string, a section name it heads
(`"gate_violations.crap"`, `f"gate_violations.{name}"`), a name bound to one
(`KEY = "unread_files"`, or a parameter's default, `key="new_failures"`), a
lookup into a mapping whose values are old keys (`x[JSON_KEYS[name]]`), or a
loop variable over a collection of them (`{k: x[k] for k in VERDICT_KEYS}`). A
collection holds old keys when it holds one of the three no 0.9.0 field
reuses, or old keys alone: the other five also name a findings kind, a counts
key or a table column, so beside other strings they read as those. The
exemptions:

- it is read off an object that is not the verify payload and shares the
  name: `counts`, rescore --gate's `gate` block (`gate.unread_files`), the
  `--json` error object (`x["error"]`, `json_fields()`, or a variable every
  value of which was read off `["error"]`, never a variable named `error`
  alone), the crapkit.toml table `[crapkit]` and brief's `gate_rule`
  (`diff_uncovered_max`);
- it runs only when the payload has no `findings`: a reader that also serves a
  crapkit before 0.9.0, which the accuracy suite's retro replays run, names the
  old key in that branch alone, so dropping the keys changes nothing it reads;
- its file builds payloads the 0.8.1 way on purpose (BUILDER).

JSON: every old key a JSON object holds fails unless the key the object sits
under names one of those other objects (`"counts": {...}`, `"gate": {...}`,
`"error": {...}`, `"gate_rule": {...}`). The scan reads every file under
tests/ but Python, so a verify payload a fixture or golden records, as a .json
file or as a line of a text one, is read like code; records of runs an older
crapkit made keep theirs (RECORDS). It reads the fenced examples on every page
below the same way.

Markdown under docs/, README.md, AGENTS.md and the plugin's skills: every
mention of the three names no 0.9.0 field reuses fails. The other five also
name a findings kind, a `counts` key, the gate block's and the error object's
list, or the crapkit.toml key, so a mention of one fails where the sentence
uses it as a list of verify's: "listed under `overridden`", "`unread_files`
lists", "`diff_uncovered` truncates", "verify's `unread_files`", "the
`overridden` field". A mention the words just before give to the error object
("the error object lists it in `unread_files`") is that object's. A table row
that opens with one of the five fails too, unless its table is the findings
kind table and the name a kind, or lists one of the other objects' keys, which
the line just above the table or the nearest heading names first (`counts`,
`gate_rule`, `[crapkit]`). Dated records (upgrading.md, releases/, specs/,
architecture/) keep theirs, and so does docs/agent-json.md's Errors section,
whose `unread_files` is the error object's.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

from crapkit.verify import FINDING_KINDS

ROOT = Path(__file__).resolve().parents[2]

OLD_KEYS = frozenset({"gate_violations", "ratchet_regressions", "new_failures", "diff_uncovered",
                      "diff_uncovered_count", "diff_uncovered_max", "unread_files", "overridden"})

# The keys no 0.9.0 field reuses.
GONE = ("gate_violations", "ratchet_regressions", "new_failures")

# The objects that are not the verify payload and hold a key of the same name.
# A variable counts as the `error` object only when every value bound to it was
# read off `["error"]` (_Source._holds_error), never by its name alone.
OTHER_OWNERS = frozenset({"counts", "gate", "error", "json_fields"})
OTHER_READS = frozenset({("main", "diff_uncovered_max"), ("rule", "diff_uncovered_max"),
                         ("gate_rule", "diff_uncovered_max"), ("crapkit", "diff_uncovered_max")})

BUILDER = frozenset({
    # Builds payloads the 0.8.1 way to show the Action's comment reads none of these keys.
    "tests/unit/test_action_contract.py",
})

DATED = ("docs/upgrading.md", "docs/releases/", "docs/specs/", "docs/architecture/")


def another_objects(owner: str | None, key: str) -> bool:
    """True when key, read off the object owner names, is that object's own
    and not verify's."""
    return owner in OTHER_OWNERS or (owner, key) in OTHER_READS


# --- Python ---------------------------------------------------------------------------

# An old key, alone or as the section name ("gate_violations.crap") a reader
# flattens the payload's rows under.
_OLD_KEY = re.compile(r"^(" + "|".join(sorted(OLD_KEYS)) + r")(?:\.\w+)?$")
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _key_read(node: ast.AST) -> tuple[ast.AST, ast.AST] | None:
    """(the object read, the key expression) when node reads a key off an object."""
    if isinstance(node, ast.Subscript):
        return node.value, node.slice
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop", "setdefault") and node.args):
        return node.func.value, node.args[0]
    return None


def _old_in(value: object) -> frozenset[str]:
    """The old key a string constant names, alone or as a section name."""
    match = _OLD_KEY.match(value) if isinstance(value, str) else None
    return frozenset({match.group(1)}) if match else frozenset()


def _strings(nodes: list[ast.AST]) -> frozenset[str]:
    """The old keys the string constants among nodes name."""
    return frozenset().union(*(_old_in(n.value) for n in nodes if isinstance(n, ast.Constant)))


def _held(nodes: list[ast.AST]) -> frozenset[str]:
    """The old keys a collection holds, when it holds verify's old keys: one no
    0.9.0 field reuses, or old keys alone. The other five also name a findings
    kind, a counts key or a table column, so beside other strings they read as
    those (a tuple of kinds that ends in "diff_uncovered")."""
    strings = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    old = _strings(nodes)
    return old if old & set(GONE) or (strings and all(map(_old_in, strings))) else frozenset()


def _f_section(key: ast.JoinedStr) -> str | None:
    """The section an f-string opens with: `gate_violations` in f"gate_violations.{name}"."""
    head = key.values[0] if key.values else None
    text = head.value if isinstance(head, ast.Constant) else ""
    return text.split(".")[0] if "." in text else None


def _literal(value: ast.AST) -> tuple[list, list]:
    """(what a lookup into a literal collection yields, what a loop over it yields)."""
    if isinstance(value, ast.Call) and value.args:  # frozenset({...}), tuple([...])
        return _literal(value.args[0])
    if isinstance(value, ast.Dict):
        return list(value.values), list(value.keys)
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return [], list(value.elts)
    return [], []


def _assigned(tree: ast.AST) -> dict[int, ast.AST]:
    """The value each plainly assigned name node and each parameter with a
    default takes, by the node's id."""
    values = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            values.update({id(t): node.value for t in targets if isinstance(t, ast.Name)})
        elif isinstance(node, ast.arguments):
            positional = [*node.posonlyargs, *node.args]
            defaulted = [*zip(positional[len(positional) - len(node.defaults):], node.defaults),
                         *zip(node.kwonlyargs, node.kw_defaults)]
            values.update({id(arg): default for arg, default in defaulted})
    return values


def _bound_values(tree: ast.AST) -> dict[str, list]:
    """Each name's bound values; None where a binding has no single value (an
    argument with no default, a loop target, a name unpacked from a tuple)."""
    assigned = _assigned(tree)
    bound: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.setdefault(node.id, []).append(assigned.get(id(node)))
        elif isinstance(node, ast.arg):
            bound.setdefault(node.arg, []).append(assigned.get(id(node)))
    return bound


def _reads_error(value: ast.AST | None) -> bool:
    read = _key_read(value) if value is not None else None
    return read is not None and isinstance(read[1], ast.Constant) and read[1].value == "error"


def _binds(loop: ast.AST, name: str) -> bool:
    return any(isinstance(t, ast.Name) and t.id == name for t in ast.walk(loop.target))


def _loops(node: ast.AST) -> list:
    """The loops a node opens: a comprehension's generators or a for statement."""
    if isinstance(node, _COMPREHENSIONS):
        return node.generators
    return [node] if isinstance(node, ast.For) else []


def _asks_for_findings(test: ast.AST) -> ast.cmpop | None:
    """`in` or `not in` when test asks whether a payload holds `findings`."""
    if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Constant)
            and test.left.value == "findings" and isinstance(test.ops[0], (ast.In, ast.NotIn))):
        return test.ops[0]
    return None


def _without_findings(branch: ast.If, node: ast.AST) -> bool:
    """True when node runs only on a payload with no `findings`."""
    op = _asks_for_findings(branch.test)
    taken = branch.body if isinstance(op, ast.NotIn) else branch.orelse if op else []
    return any(node is inner for statement in taken for inner in ast.walk(statement))


class _Source:
    """One parsed file: what its names hold and where each node sits."""

    def __init__(self, source: str):
        self.tree = ast.parse(source)
        self.parents = {child: parent for parent in ast.walk(self.tree)
                        for child in ast.iter_child_nodes(parent)}
        self._bound = {self.tree: _bound_values(self.tree)}
        literals = {name: _literal(values[0]) for name, values in self._bound[self.tree].items()
                    if len(values) == 1 and values[0] is not None}
        self.lookups = {name: _held(pair[0]) for name, pair in literals.items()}
        self.items = {name: _held(pair[1]) for name, pair in literals.items()}

    def _above(self, node: ast.AST):
        above = self.parents.get(node)
        while above is not None:
            yield above
            above = self.parents.get(above)

    def old_keys(self, key: ast.AST) -> frozenset[str]:
        """The old keys a key expression can hold: its own constant, the
        section an f-string opens with, a lookup into a mapping whose values
        are old keys, a loop variable over a collection of them, or any other
        name bound to one."""
        if isinstance(key, ast.Constant):
            return _old_in(key.value)
        if isinstance(key, ast.JoinedStr):
            return _old_in(_f_section(key))
        if isinstance(key, ast.Subscript) and isinstance(key.value, ast.Name):
            return self.lookups.get(key.value.id, frozenset())
        if not isinstance(key, ast.Name):
            return frozenset()
        iterated = self._iterated(key)
        if iterated is None:
            return _strings(self._values(key))
        if isinstance(iterated, ast.Name):
            return self.items.get(iterated.id, frozenset())
        return _held(_literal(iterated)[1])

    def _iterated(self, name: ast.Name) -> ast.AST | None:
        """What the nearest loop that binds name iterates over."""
        loops = (loop for above in self._above(name) for loop in _loops(above))
        return next((loop.iter for loop in loops if _binds(loop, name.id)), None)

    def owner(self, node: ast.AST) -> str | None:
        """The name of the object a key is read off: the key it sits under, its
        variable, or the method that returned it. A variable reads as `error`
        only when it holds the error object."""
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value
        if isinstance(node, ast.Name):
            return "error" if self._holds_error(node) else node.id if node.id != "error" else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def _values(self, name: ast.Name) -> list:
        """Every value the name's scope binds to it; a name the function does
        not bind resolves in the module."""
        scope = next((above for above in self._above(name) if isinstance(above, _SCOPES)), self.tree)
        if scope not in self._bound:
            self._bound[scope] = _bound_values(scope)
        return self._bound[scope].get(name.id) or self._bound[self.tree].get(name.id, [])

    def _holds_error(self, name: ast.Name) -> bool:
        """True when every value bound to the name was read off `["error"]`."""
        values = self._values(name)
        return bool(values) and all(map(_reads_error, values))

    def off_another_object(self, obj: ast.AST, keys: frozenset[str]) -> bool:
        owner = self.owner(obj)
        return all(another_objects(owner, key) for key in keys)

    def guarded(self, node: ast.AST) -> bool:
        return any(isinstance(above, ast.If) and _without_findings(above, node)
                   for above in self._above(node))


def old_key_reads(source: str) -> list[tuple[int, str, str]]:
    """(line, the object read, the key) for each read of an old key that is
    not off another object and not guarded by the absence of `findings`."""
    parsed = _Source(source)
    found = []
    for node in ast.walk(parsed.tree):
        read = _key_read(node)
        keys = parsed.old_keys(read[1]) if read else frozenset()
        if not keys or parsed.off_another_object(read[0], keys) or parsed.guarded(node):
            continue
        key = read[1].value if isinstance(read[1], ast.Constant) else ast.unparse(read[1])
        found.append((node.lineno, ast.unparse(read[0]), key))
    return sorted(found)


def _python_files() -> list[Path]:
    return sorted(path for top in ("src", "tests") for path in (ROOT / top).rglob("*.py"))


def _parsed_reads(path: Path) -> list[tuple[int, str, str]]:
    """The file's reads; a corpus fixture Python cannot parse holds none."""
    try:
        return old_key_reads(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return []


def test_no_python_outside_the_builder_reads_an_old_verify_key():
    hits = {}
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        reads = [] if rel in BUILDER else _parsed_reads(path)
        if reads:
            hits[rel] = reads
    assert hits == {}, hits


def test_the_scan_flags_a_read_and_passes_the_reads_it_names():
    source = '''
def f(payload, printed, gate, main):
    a = payload["gate_violations"]
    b = printed.get("diff_uncovered_count", 0)
    c = payload.pop("overridden")
    d = payload["counts"]["diff_uncovered_count"] + len(gate["unread_files"])
    e = printed["error"]["unread_files"] + main.get("diff_uncovered_max")
    if "findings" not in payload:
        return payload["new_failures"]
    return [i for i in payload["findings"] if i["kind"] == "diff_uncovered"]
'''
    assert old_key_reads(source) == [(3, "payload", "gate_violations"),
                                     (4, "printed", "diff_uncovered_count"),
                                     (5, "payload", "overridden")]


def test_a_read_guarded_the_other_way_round_is_still_flagged():
    source = '''
def f(payload):
    if "findings" in payload:
        return payload["unread_files"]
    return payload["unread_files"] + payload["findings"]
'''
    assert old_key_reads(source) == [(4, "payload", "unread_files"), (5, "payload", "unread_files")]


def test_a_key_looked_up_in_a_mapping_of_old_keys_is_flagged():
    """The 16 verdict subsets once read `verdict[JSON_KEYS[name]]`. A tuple of
    kinds that ends in "diff_uncovered" is not a collection of old keys."""
    source = '''
JSON_KEYS = {"gate": "gate_violations", "ratchet": "ratchet_regressions"}
FINDINGS = ("gate", "ratchet", "failures", "diff_uncovered")
def f(verdict, row):
    flagged = {name for name in FINDINGS if verdict[JSON_KEYS[name]]}
    return flagged, [row[name] for name in FINDINGS]
'''
    assert old_key_reads(source) == [(5, "verdict", "JSON_KEYS[name]")]


def test_a_key_a_loop_draws_from_a_tuple_of_old_keys_is_flagged():
    source = '''
VERDICT_KEYS = ("ok", "gate_violations", "counts")
def f(payload, printed):
    picked = [payload[k] for k in VERDICT_KEYS]
    for key in ("diff_uncovered_count", "unread_files"):
        picked.append(printed.get(key))
    return picked
'''
    assert old_key_reads(source) == [(4, "payload", "k"), (6, "printed", "key")]


def test_a_section_name_an_old_key_heads_is_flagged():
    source = '''
def f(verdict, unused):
    return verdict[unused]["gate_violations.crap"], verdict[unused]["findings.crap"]
'''
    assert old_key_reads(source) == [(3, "verdict[unused]", "gate_violations.crap")]


def test_a_name_bound_to_an_old_key_is_flagged():
    """A module constant, a local and a parameter's default name the key as
    surely as its string does, and an f-string opens a section like a literal
    one; a parameter with no default, and the gate block's list, pass."""
    source = '''
NEW = "new_failures"
KEY = "unread_files"
def f(p, gate, key="ratchet_regressions", *, kw="overridden"):
    local = "diff_uncovered_count"
    a = p[NEW]
    b = p.get(KEY)
    c = p[key] + p[kw]
    d = p[local] + gate[KEY]
    return p[f"gate_violations.{key}"]
def g(p, key):
    return p[key]
'''
    assert old_key_reads(source) == [(6, "p", "NEW"), (7, "p", "KEY"), (8, "p", "key"),
                                     (8, "p", "kw"), (9, "p", "local"),
                                     (10, "p", "f'gate_violations.{key}'")]


def test_error_passes_only_when_its_value_was_read_off_error():
    """A variable named error that holds verify's payload is the payload."""
    source = '''
def f(printed):
    error = printed["error"]
    return error["unread_files"]
def g(run):
    code, error, err = run()
    return error["unread_files"]
def h(error):
    return error["unread_files"]
def i(printed, refusal=None):
    refusal = printed.get("error")
    return refusal["unread_files"]
'''
    assert old_key_reads(source) == [(7, "error", "unread_files"), (9, "error", "unread_files"),
                                     (12, "refusal", "unread_files")]


# --- JSON ------------------------------------------------------------------------------

# Records of runs an older crapkit made: the retro ledgers keep the failure
# each test printed at the commit before its fix, 0.8.x verify payloads among
# them, so their old keys are evidence, not readers.
RECORDS = ("tests/accuracy/suite_strength/retro/", "tests/accuracy/verdict_model/retro.tsv")

# A JSON string, with the colon that makes it a key, or a bracket. A string
# ends on its own line, so a stray quote in prose costs one line, not the file.
_JSON_TOKEN = re.compile(r'"((?:[^"\\\n]|\\.)*)"(\s*:)?|[{}\[\]]')


def old_json_keys(text: str) -> list[tuple[int, str | None, str]]:
    """(line, the key its object sits under, the key) for each old key a JSON
    object in text holds, unless that object is another one's. An object in an
    array sits under the array's key; one at the top sits under None."""
    found, owners, pending = [], [], None
    for token in _JSON_TOKEN.finditer(text):
        string, is_key, mark = token.group(1), token.group(2), token.group()
        owner = owners[-1] if owners else None
        if is_key and string in OLD_KEYS and not another_objects(owner, string):
            found.append((text.count("\n", 0, token.start()) + 1, owner, string))
        if mark in ("{", "["):
            owners.append(owner if pending is None else pending)
        elif mark in ("}", "]"):
            owners = owners[:-1]
        pending = string if is_key else None
    return found


def _fixture_files() -> list[Path]:
    """Every file under tests/ but Python: fixtures, goldens and recorded runs."""
    return sorted(path for path in (ROOT / "tests").rglob("*")
                  if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts)


def _text(path: Path) -> str:
    """The file's text; a bundle, an image or any other file not in UTF-8 holds none."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def test_no_fixture_or_golden_under_tests_holds_an_old_verify_key():
    hits = {}
    for path in _fixture_files():
        rel = path.relative_to(ROOT).as_posix()
        keys = [] if rel.startswith(RECORDS) else old_json_keys(_text(path))
        if keys:
            hits[rel] = keys
    assert hits == {}, hits


def test_the_json_scan_flags_verify_s_old_keys_and_passes_the_other_objects():
    """A golden pretty-printed or on one line of a text file reads alike; the
    gate block's list, the error object's, counts and brief's ceiling pass."""
    pretty = ('{\n "diff_uncovered_count": 3,\n "findings": [{"kind": "diff_uncovered"}],\n'
              ' "overridden": []\n}')
    line = ('verify --json\n{"counts": {"diff_uncovered_count": 0}, "gate": {"unread_files": []}, '
            '"new_failures": [], "unread_files": []}\n')
    others = ('{"error": {"unread_files": []}, "packets": [{"gate_rule": {"diff_uncovered_max": null}}],'
              ' "verdicts": [{"gate_violations": [{"path": "a.py"}]}]}')

    assert old_json_keys(pretty) == [(2, None, "diff_uncovered_count"), (4, None, "overridden")]
    assert old_json_keys(line) == [(2, None, "new_failures"), (2, None, "unread_files")]
    assert old_json_keys(others) == [(1, "verdicts", "gate_violations")]


def test_the_fixture_scan_reads_the_verify_goldens_and_no_python():
    files = {path.relative_to(ROOT).as_posix() for path in _fixture_files()}

    assert {"tests/fixtures/action_comment/verify.json", "tests/goldens/machine_outputs/verify.txt",
            "tests/accuracy/corpus_goldens/goldens/session/verify.json"} <= files
    assert not [rel for rel in files if rel.endswith(".py")]


# --- docs ------------------------------------------------------------------------------

_GONE = re.compile(r"\b(" + "|".join(GONE) + r")\b")

# The five old keys a 0.9.0 field reuses, and the wording that uses one as a
# list of verify's. Words run across a line break, as wrapped prose does.
SHARED = tuple(sorted(OLD_KEYS - set(GONE)))
_NAME = r"`(" + "|".join(SHARED) + r")`"
_AS_KEY = re.compile("|".join((
    r"\bunder\s+" + _NAME,
    r"\b(?:listed|lists?(?:\s+\w+)?)\s+in\s+" + _NAME,
    r"\bverify's\s+" + _NAME,
    _NAME + r"\s+(?:lists|holds|truncates|carries|key|list|array|field)\b",
    _NAME + r"\s+in\s+`--json`",
)))
_ERROR_OBJECT_BEFORE = re.compile(r"error object\s*$")

# A table row that opens with one of the five, and what names a table's owner.
_ROW = re.compile(r"^\|\s*" + _NAME + r"\s*\|")
_KIND_TABLE = re.compile(r"^\|\s*Kind\s*\|")
_FIRST_NAME = re.compile(r"`\[?(\w+)\]?`")
KINDS = frozenset(row.kind for row in FINDING_KINDS)


def _section(lines: list[str], heading: str) -> range:
    """The lines from heading to the next `## ` heading; none when the page lacks it."""
    if heading not in lines:
        return range(0)
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return range(start, end)


def _error_object_lines(path: str, lines: list[str]) -> set[int]:
    """docs/agent-json.md's Errors section, which documents the error object."""
    return set(_section(lines, "## Errors")) if path == "docs/agent-json.md" else set()


def _shared_key_lines(text: str) -> set[int]:
    """The lines where a sentence uses one of the five shared names as a list
    of verify's, by the line the name sits on; the error object's list passes."""
    lines = set()
    for match in _AS_KEY.finditer(text):
        if _ERROR_OBJECT_BEFORE.search(text, max(0, match.start() - 40), match.start()):
            continue
        name_at = next(match.start(g) for g in range(1, _AS_KEY.groups + 1) if match.group(g))
        lines.add(text.count("\n", 0, name_at))
    return lines


def _example_key_lines(lines: list[str]) -> set[int]:
    """The lines of a fenced example where a JSON object holds an old key of verify's."""
    found, start = set(), None
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("```"):
            continue
        if start is None:
            start = i + 1
            continue
        found |= {start + at - 1 for at, _, _ in old_json_keys("\n".join(lines[start:i]))}
        start = None
    return found


def _table_owner(lines: list[str], row: int) -> str | None:
    """What a table lists: `kind` when its first column is Kind, else the
    first name in backticks on the line just above it or, with none there, on
    the nearest heading above."""
    header = row
    while header > 0 and lines[header - 1].startswith("|"):
        header -= 1
    if _KIND_TABLE.match(lines[header]):
        return "kind"
    above = [line for line in reversed(lines[:header]) if line.strip()]
    heading = next((line for line in above if line.startswith("#")), "")
    named = (_FIRST_NAME.search(line) for line in [*above[:1], heading])
    return next((match.group(1) for match in named if match), None)


def _table_key_lines(lines: list[str]) -> set[int]:
    """The table rows that list one of the five shared names as a key of verify's."""
    found = set()
    for i, line in enumerate(lines):
        match = _ROW.match(line)
        if not match:
            continue
        name, owner = match.group(1), _table_owner(lines, i)
        if not (owner == "kind" and name in KINDS or another_objects(owner, name)):
            found.add(i)
    return found


def gone_key_mentions(path: str, text: str) -> list[tuple[int, str]]:
    """(line number, line) for each line naming a key no 0.9.0 field reuses, or
    using one of the five shared names as a key of verify's: in a sentence, as
    a key a JSON example's object holds, or as a table row."""
    lines = text.split("\n")
    gone = {i for i, line in enumerate(lines) if _GONE.search(line)}
    shared = _shared_key_lines(text) | _example_key_lines(lines) | _table_key_lines(lines)
    shared -= _error_object_lines(path, lines)
    return [(i + 1, lines[i]) for i in sorted(gone | shared)]


def _pages() -> list[str]:
    """Every page a reader of verify --json is sent to, dated records aside:
    docs/, README.md, AGENTS.md and the plugin's skills."""
    pages = [*(ROOT / "docs").rglob("*.md"), ROOT / "README.md", ROOT / "AGENTS.md",
             *(ROOT / "plugin" / "skills").rglob("*.md")]
    found = sorted(page.relative_to(ROOT).as_posix() for page in pages)
    return [rel for rel in found if not rel.startswith(DATED)]


def test_the_scan_reads_readme_agents_and_the_skills_beside_docs():
    pages = _pages()

    assert {"README.md", "AGENTS.md", "plugin/skills/crapkit-recover/SKILL.md",
            "docs/agent-json.md"} <= set(pages)
    assert not [page for page in pages if page.startswith(DATED)]


def test_no_page_outside_the_dated_records_names_a_gone_verify_key():
    hits = {}
    for rel in _pages():
        mentions = gone_key_mentions(rel, (ROOT / rel).read_text(encoding="utf-8"))
        if mentions:
            hits[rel] = mentions
    assert hits == {}, hits


def test_the_docs_scan_flags_the_verify_example_and_table_too():
    """The verify example and the 0.8.1 key table left with the keys, so an
    old key there fails like one anywhere else."""
    page = "\n".join(["## `verify`", "```json", "{", '  "new_failures": [],', "}", "```",
                      "| `gate_violations` | x | 6 |", "a `gate_violations` entry", "## `coverage`",
                      '  "new_failures": [],'])

    assert [line for line, _ in gone_key_mentions("docs/agent-json.md", page)] == [4, 7, 8, 10]


def test_the_docs_scan_flags_a_shared_name_used_as_a_verify_key():
    """The five names a 0.9.0 field reuses fail where a sentence lists by them,
    and pass as a findings kind, a counts key or the error object's list."""
    page = "\n".join([
        "`ratchet_changes` stays `null`, the grant being listed under",
        "`overridden`.",
        "`counts.diff_uncovered_count` is 0. `unread_files` lists each file.",
        "in the shape verify's `unread_files` has; `diff_uncovered` truncates at 50.",
        "An `overridden` item in `findings`; `diff_uncovered` items stop at 50.",
        "`counts.diff_uncovered_count` does not, and `diff_uncovered_max` is the ceiling.",
        "the CLI's error object lists it in `unread_files`, with `gate.unread_files`.",
        "verify --json prints the `diff_uncovered_count` field.",
    ])

    assert [line for line, _ in gone_key_mentions("docs/ratchet.md", page)] == [2, 3, 4, 8]


def test_a_shared_name_as_a_verify_example_s_key_or_a_key_table_s_row_is_flagged():
    """The five fail as a key a verify example holds and as a row of a table
    of verify's keys. Under counts, the gate block or the error object, and in
    the kind table (a kind's row), the counts, gate_rule and [crapkit] tables,
    they pass."""
    page = "\n".join([
        "## `verify`",
        "```json",
        "{",
        '  "counts": {"diff_uncovered_count": 3, "diff_uncovered_max": null},',
        '  "diff_uncovered_count": 3,',
        '  "gate": {"unread_files": []},',
        '  "error": {"unread_files": []},',
        '  "unread_files": []',
        "}",
        "```",
        "| Key | Type | Fires exit |",
        "|---|---|---|",
        "| `unread_files` | list | 6 |",
        "",
        "| Kind | Its own fields | exit |",
        "|---|---|---|",
        "| `diff_uncovered` | `path`, `line` | 9 |",
        "| `unread_files` | `path` | 6 |",
        "",
        "`counts` holds the numbers beside the items.",
        "",
        "| Key | Type | Meaning |",
        "|---|---|---|",
        "| `diff_uncovered_count` | int | every line no test ran |",
        "### `gate_rule`: what the edit is judged by",
        "",
        "Three limits, in one object.",
        "",
        "| Key | Type | Meaning |",
        "|---|---|---|",
        "| `diff_uncovered_max` | int or null | the ceiling |",
        "## `[crapkit]`",
        "",
        "| Key | Type | Default |",
        "|---|---|---|",
        "| `diff_uncovered_max` | int | absent |",
    ])

    assert [line for line, _ in gone_key_mentions("docs/agent-json.md", page)] == [5, 8, 13, 18]


def test_agent_json_errors_section_lists_the_error_objects_unread_files():
    page = "\n".join(["## Errors", "the rest; `unread_files` lists every one:",
                      "## MCP server", "`unread_files` lists every one"])

    assert gone_key_mentions("docs/agent-json.md", page) == [(4, "`unread_files` lists every one")]
    assert len(gone_key_mentions("docs/lanes.md", page)) == 2
