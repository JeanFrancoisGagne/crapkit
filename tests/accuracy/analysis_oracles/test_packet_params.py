"""packet.params: the parameter list `brief --json` prints, against ast and the TypeScript compiler.

docs/agent-json.md#item-fields defines `params` as the function's parameters
in declaration order, each {name, type}: `name` as declared, `type` the
annotation as lizard printed it or null when there is none, and
`scored.params` the count of these.

The expected lists come from outside crapkit:
- Python: ast's `arguments` node (Python reference 8.7, "Function
  definitions"): the positional-only parameters, then the positional ones,
  `*name`, the keyword-only ones and `**name`. The bare `/` and `*`
  separators are no parameters. A type is ast.unparse of the annotation.
- TypeScript: the compiler's ParameterDeclaration list (oracles/ts_functions.cjs
  paramList): each name's source text and its type node's source text, with
  a `this` parameter left out (TypeScript handbook, "Declaring this in a
  Function").

Two documented spellings are transforms, each a rulings row with its hand
case below: AO-PKT-TYPE-SPACING (lizard prints a type with a space between
its tokens, so types compare with whitespace removed) and AO-PKT-TS-REST (a
TypeScript rest parameter reads without its dots). Every other difference is
a defect row that its own strict xfail pins.
"""
import ast

import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_tables
from accuracy.analysis_oracles.oracles import node_oracles
from accuracy.kit import drive, oracles
from accuracy.kit.rulings import pin_ruling

pytestmark = pytest.mark.process

PY = """def plain(a, b):
    return a


def defaults(a, b=1, *args, c, d=2, **kw):
    return a


def typed(a: int, b: list[int] = None) -> int:
    return a


def nothing():
    return 1


class Box:
    def put(self, item: str) -> None:
        return None


def posonly(a, /, b, *, c):
    return a


def kwonly(*, key):
    return key


def annotated_star(a: int, *rest: str):
    return a
"""

TS = """export function untyped(a, b) { return a; }
export function withRest(a, ...rest) { return a; }
export function annotated(a: number, b?: string): number { return a; }
export const generic = (a = 1, b: Map<string, number[]>) => a;
export function destructured({ x, y }: { x: number; y: number }) { return x; }
export class Shelf {
  put(this: Shelf, item) { return item; }
}
"""

PY_CLEAN = ("plain", "defaults", "typed", "nothing", "put")
TS_CLEAN = ("untyped",)


# --- the outside lists ---------------------------------------------------------------------

def _annotation(arg) -> str | None:
    return None if arg.annotation is None else ast.unparse(arg.annotation)


def _starred(prefix: str, arg) -> list:
    return [] if arg is None else [(prefix + arg.arg, _annotation(arg))]


def ast_params(fn) -> list[tuple]:
    """(name as declared, annotation text or None) per parameter, in order."""
    args = fn.args
    named = [(arg.arg, _annotation(arg)) for arg in [*args.posonlyargs, *args.args]]
    keyword = [(arg.arg, _annotation(arg)) for arg in args.kwonlyargs]
    return (named + _starred("*", args.vararg) + keyword + _starred("**", args.kwarg))


def ast_lists(source: str) -> dict:
    return {node.name: ast_params(node) for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)}


def compiler_params(fn) -> list[tuple]:
    """(declared text, type text or None) per parameter; a rest name gets its dots."""
    return [(("..." if rest else "") + name, kind) for name, kind, rest in fn.param_list]


# --- crapkit's lists ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def briefs(tmp_path_factory):
    files = {"m.py": PY, "t.ts": TS}
    tree = {"crapkit.toml": analysis_inventory.config(("python", "typescript")), **files}
    root = analysis_inventory.build(tree, tmp_path_factory.mktemp("params") / "repo")
    driver = drive.Driver(root)
    done = driver.run("coverage")
    assert done.code == 0, done.stderr
    return lambda path, name: driver.json("brief", path, name)


@pytest.fixture(scope="module")
def compiled(oracle, tmp_path_factory):
    oracle("typescript")
    work = tmp_path_factory.mktemp("params-ts")
    paths = node_oracles.write({"t.ts": TS}, work)
    return {fn.name: fn for fn in node_oracles.functions(oracles.node_modules("push"), work, paths)}


def squeeze(text: str | None) -> str:
    return "null" if text is None else "".join(text.split())


def listed(pairs) -> tuple[str, str]:
    """(names, types) of a list of (name, type), each joined by commas."""
    return (",".join(name for name, _ in pairs), ",".join(squeeze(kind) for _, kind in pairs))


def crapkit_pairs(packet: dict) -> list[tuple]:
    return [(param["name"], param["type"]) for param in packet["params"]]


# --- the hand cases for the two transforms ---------------------------------------------------

def test_squeeze_drops_the_spaces_lizard_puts_between_type_tokens():
    """AO-PKT-TYPE-SPACING's transform: lizard prints `list[int]` as `list [ int ]`."""
    assert squeeze("list [ int ]") == "list[int]"
    assert squeeze(None) == "null"


def test_ast_lists_name_the_parameters_the_reference_declares():
    """Python reference 8.7: `def f(a, /, b, *args, c, **kw)` declares five parameters."""
    assert ast_lists("def f(a, /, b: int, *args, c, **kw): pass\n")["f"] == [
        ("a", None), ("b", "int"), ("*args", None), ("c", None), ("**kw", None)]


# --- lists that match -----------------------------------------------------------------------

@pytest.mark.parametrize("name", PY_CLEAN)
def test_python_params_match_ast(name, briefs):
    packet = briefs("m.py", name)

    assert listed(crapkit_pairs(packet)) == listed(ast_lists(PY)[name])
    assert packet["scored"]["params"] == len(packet["params"])


@pytest.mark.parametrize("name", TS_CLEAN)
def test_typescript_params_match_the_compiler(name, briefs, compiled):
    packet = briefs("t.ts", name)

    assert listed(crapkit_pairs(packet)) == listed(compiler_params(compiled[name]))
    assert packet["scored"]["params"] == len(packet["params"])


# --- rulings rows -----------------------------------------------------------------------------

def _py(name: str):
    return lambda briefs, compiled: (crapkit_pairs(briefs("m.py", name)), ast_lists(PY)[name])


def _ts(name: str):
    return lambda briefs, compiled: (crapkit_pairs(briefs("t.ts", name)),
                                     compiler_params(compiled[name]))


def _names(pairs) -> str:
    return listed(pairs)[0]


def _types(pairs) -> str:
    return listed(pairs)[1]


def _raw_type(index: int):
    return lambda pairs: str(pairs[index][1])


def _rest_names(pairs) -> str:
    return ",".join(name for name, _ in pairs)


# (rulings id, the two lists, the field compared)
CASES = {
    "AO-PKT-TYPE-SPACING": (_py("typed"), _raw_type(1)),
    "AO-PKT-TS-REST": (_ts("withRest"), _rest_names),
    "AO-PKT-PY-SEPARATORS": (_py("posonly"), _names),
    "AO-PKT-PY-BARE-STAR": (_py("kwonly"), _names),
    "AO-PKT-PY-STAR-SPACE": (_py("annotated_star"), _names),
    "AO-PKT-TS-TYPES": (_ts("annotated"), _types),
    "AO-PKT-TS-GENERIC": (_ts("generic"), _types),
    "AO-PKT-TS-DESTRUCTURE-TYPE": (_ts("destructured"), _types),
    "AO-PKT-TS-THIS": (_ts("put"), _names),
}


def _case(ruling_id: str):
    return pytest.param(ruling_id, marks=analysis_tables.marks(ruling_id), id=ruling_id)


@pytest.mark.parametrize("ruling_id", [_case(ruling_id) for ruling_id in CASES])
def test_each_params_difference_pins_its_rulings_row(ruling_id, briefs, compiled):
    lists, field = CASES[ruling_id]
    crapkit, outside = lists(briefs, compiled)

    pin_ruling(ruling_id, crapkit=field(crapkit), oracle=field(outside))


@pytest.mark.parametrize("ruling_id", [_case("AO-TS-PARAMS-THIS")])
def test_the_this_parameter_count_pins_its_rulings_row(ruling_id, briefs, compiled):
    """The params count, not only brief's list: scored.params against the compiler."""
    pin_ruling(ruling_id, crapkit=briefs("t.ts", "put")["scored"]["params"],
               oracle=compiled["put"].params)
