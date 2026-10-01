"""The verdict model orders the rerun reasons the way docs/lanes.md lists them.

tests/accuracy/verdict_model/model_verdict.py restates crapkit's verdict from its
docs alone, and its REUSE_ORDER is the order in which docs/lanes.md's sentence "A
rerun names the first condition that failed: ..." gives the conditions. The page
dropped "an artifact built at a commit that is no longer behind HEAD", a reason
`--reuse-unchanged` never gives, and the model kept it as "not behind" under a
comment saying the docs still listed it.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "tests" / "accuracy" / "verdict_model" / "model_verdict.py"
LANES = ROOT / "docs" / "lanes.md"

# The words docs/lanes.md's rerun sentence gives each condition, in the page's
# order, keyed by the model's name for the condition.
WORDS = {
    "no artifact": "`no artifact at PATH`",
    "wrote none": "a last attempt that wrote none",
    "no proof": "`its stamp holds no proof`",
    "uncommitted": "none), uncommitted changes,",
    "head": "`HEAD is X and its artifact was built at Y`",
    "crapkit.toml": "`crapkit.toml changed`",
    "lane table": "`its lane table changed`",
    "crapkit version": "`the crapkit version changed`",
    "environment": "`N environment variable(s) changed: NAME`",
    "inputs": "changes under a lane's `inputs` since its commit",
    "inputs unread": "`nothing proves its inputs unchanged`",
    "commit absent": "a stamp commit this clone does not hold",
    "bytes": "`PATH: bytes differ from its stamp`",
}


def _reruns() -> str:
    """The rerun sentence of docs/lanes.md, Reusing artifacts, joined as prose."""
    text = " ".join(LANES.read_text(encoding="utf-8").split())
    return (text.split("A rerun names the first condition that failed:", 1)[1]
            .split("`crapkit.toml` is compared with CRLF", 1)[0])


def _assigned(name: str) -> ast.expr:
    """The expression model_verdict.py assigns to a module-level `name`."""
    tree = ast.parse(MODEL.read_text(encoding="utf-8"))
    return next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and [getattr(target, "id", None) for target in node.targets] == [name])


def test_the_verdict_model_orders_the_conditions_the_lanes_page_lists():
    reruns = _reruns()
    places = {name: reruns.find(words) for name, words in WORDS.items()}

    assert [name for name, place in places.items() if place < 0] == []
    assert sorted(places, key=places.get) == list(WORDS)
    assert ast.literal_eval(_assigned("REUSE_ORDER")) == tuple(WORDS)


def test_the_verdict_model_counts_no_condition_the_lanes_page_dropped():
    named = {node.value for name in ("WITH_INPUTS", "WHOLE_TREE")
             for node in ast.walk(_assigned(name))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    assert named - set(WORDS) == set()
