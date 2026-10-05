"""coverage_format is the one module that compares parser strings.

A lane's `parser` names its coverage format, and the format's producer: the
coveragepy adapter speaks for coverage.py whether pytest, `make cov` or a
script starts it. Runner facts read the toolchain table, producer facts read
the adapter `coverage_format.lane_format` hands back, so no other module asks
which parser a lane declared. These scans hold that line: a compare of
`parser` against a string, or of a format name against anything, fails outside
coverage_format.py, and every other read of `.parser` or spelling of a format
name is a place that writes the name out, listed below with its reason.
"""
import ast
from pathlib import Path

import pytest

import crapkit

SRC = Path(crapkit.__file__).resolve().parent
OWNER = "coverage_format.py"
FORMAT_NAMES = frozenset({"coveragepy", "istanbul"})

# Each read of a `.parser` attribute outside coverage_format.py: none compares it.
ALLOWED_READS = {
    ("lanes.py", "_run_owned_lane"): "provenance: the run records the parser the lane declared",
    ("packet.py", "lane_record"): "the brief packet echoes the lane as crapkit.toml declares it",
    ("scaffold.py", "_lane_stanza"): "init writes the parser into the config it generates",
    ("scaffold.py", "_live_lanes"): "init leaves out the commented template of each parser a "
                                    "lane it writes already uses; _TEMPLATES is keyed by parser",
}

# Each spelling of a format name outside coverage_format.py: none is compared.
ALLOWED_NAMES = {
    ("config_contract.py", "<module>"): "the config enum a lane's parser is checked against",
    ("scaffold.py", "<module>"): "init's commented templates, one per parser",
    ("scaffold.py", "_pytest_lane"): "init writes parser = coveragepy for the lane it detects",
    ("scaffold.py", "_js_routed_lane"): "init writes parser = istanbul for the lane it detects",
    ("scaffold.py", "_js_root_lane"): "init writes parser = istanbul for the lane it detects",
}


def _strings(node: ast.AST) -> set[str]:
    """The string constants one compare operand spells, a tuple, list or set of
    them included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*map(_strings, node.elts))
    return set()


def _names_parser(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "parser") or (
        isinstance(node, ast.Attribute) and node.attr == "parser")


def _parser_compare(node: ast.Compare) -> bool:
    """`parser` against a string, or a format name against anything."""
    operands = [node.left, *node.comparators]
    spelled = set().union(*map(_strings, operands))
    return bool(spelled & FORMAT_NAMES) or (bool(spelled) and any(map(_names_parser, operands)))


class _Sites(ast.NodeVisitor):
    """Where one module compares, reads or spells a parser, by enclosing function."""

    def __init__(self):
        self.where = ["<module>"]
        self.compares: list[int] = []
        self.reads: set[str] = set()
        self.names: set[str] = set()

    def _inside(self, node):
        self.where.append(node.name)
        self.generic_visit(node)
        self.where.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _inside

    def visit_Compare(self, node):
        if _parser_compare(node):
            self.compares.append(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr == "parser" and isinstance(node.ctx, ast.Load):
            self.reads.add(self.where[-1])
        self.generic_visit(node)

    def visit_Constant(self, node):
        if node.value in FORMAT_NAMES:
            self.names.add(self.where[-1])


def _sites(text: str) -> _Sites:
    sites = _Sites()
    sites.visit(ast.parse(text))
    return sites


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel != OWNER:
            yield rel, _sites(path.read_text(encoding="utf-8"))


def test_no_module_but_coverage_format_compares_a_parser_string():
    found = [f"{rel}:{line}" for rel, sites in _modules() for line in sites.compares]

    assert found == []


def test_every_other_read_of_a_lanes_parser_writes_it_out():
    found = {(rel, where) for rel, sites in _modules() for where in sites.reads}

    assert found == set(ALLOWED_READS)


def test_every_other_spelling_of_a_format_name_writes_it_out():
    found = {(rel, where) for rel, sites in _modules() for where in sites.names}

    assert found == set(ALLOWED_NAMES)


@pytest.mark.parametrize("compare", [
    'lane.parser == "coveragepy"',
    'lane.parser != "coveragepy"',
    'parser in ("istanbul", "lcov")',
    '"cobertura" == spec.parser',
    'fmt == "istanbul"',
    '"coveragepy" in names',
])
def test_the_scan_catches_each_shape_a_parser_test_takes(compare):
    assert _sites(f"def f():\n    return {compare}\n").compares == [2]


@pytest.mark.parametrize("compare", ['parser not in covered', 'lane.parser is None',
                                     'name == "py"'])
def test_the_scan_passes_compares_that_test_no_parser_string(compare):
    assert _sites(f"def f():\n    return {compare}\n").compares == []
