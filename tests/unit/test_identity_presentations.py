"""Every public function projection must distinguish same-line functions."""
import json

from crapkit.analyze import analyze_source
from crapkit.cli.scoring import _rescore_json
from crapkit.dup import find_twins
from crapkit.packet import file_functions
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows


def callbacks():
    source = "const f = [(x) => { return x + 1; }, (x) => { return x + 2; }];"
    rows = build_inventory_rows({"src": analyze_source("a.ts", source)})
    return score_rows(rows, {}, lane_scopes={"src"})


def test_file_function_projection_keeps_same_line_identity():
    functions = file_functions(callbacks())
    assert [function["occurrence"] for function in functions] == [1, 2]
    assert functions[0] != functions[1]


def test_rescore_json_keeps_same_line_identity(capsys):
    _rescore_json(callbacks(), {"id": 1, "commit": "abc"})
    functions = json.loads(capsys.readouterr().out)["functions"]
    assert [function["occurrence"] for function in functions] == [1, 2]
    assert functions[0] != functions[1]


def test_same_start_nested_function_is_a_contained_twin_not_self():
    source = "function outer() { return function inner() {\n" + "\n".join(
        f"const value{i} = {i};" for i in range(8)) + "\nreturn value7;\n};\n}\n"
    rows = build_inventory_rows({"src": analyze_source("a.ts", source)})
    twins = find_twins(rows[0], rows, {"a.ts": source})
    assert len(twins) == 1
    assert twins[0]["long_name"] == rows[1].long_name
    assert twins[0]["contained"] is True
