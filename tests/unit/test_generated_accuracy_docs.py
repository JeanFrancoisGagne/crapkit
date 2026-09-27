"""docs/accuracy.md's tables and pyproject.toml's mutmut list are written from the accuracy tables.

tools/docs/generate.py reads every tests/accuracy/*/calcs.tsv and rulings.tsv;
test_generated_guidance.py fails while a generated block is out of date.
"""
import importlib.util
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("docs_generate", ROOT / "tools/docs/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = _load()


def _blocks() -> dict[str, str]:
    return {block: value for _, block, value in generate._accuracy_blocks(ROOT)}


def _kit(name: str):
    return generate._accuracy_kit(ROOT, name)


def test_a_cell_escapes_markup_and_pipes():
    assert generate._cell("a | b <T> & c") == r"a \| b &lt;T&gt; &amp; c"
    assert generate._cell("") == "-"


def test_every_calc_and_every_ruling_has_a_line():
    blocks = _blocks()
    calcs = _kit("calcs").load(ROOT / "tests/accuracy")
    rulings = _kit("rulings").load(ROOT / "tests/accuracy")

    assert [row.calc for row in calcs
            if f"| {generate._cell(row.calc)} | `{row.packet}` |" not in blocks["calcs"]] == []
    assert [key for key in rulings if f"\n| {generate._cell(key)} |" not in blocks["rulings"]] == []


def test_the_mutmut_list_is_the_union_of_every_calc_s_modules():
    calcs = _kit("calcs")

    listed = tomllib.loads(_blocks()["mutmut-paths"])["paths_to_mutate"]

    assert listed == calcs.modules(calcs.load(ROOT / "tests/accuracy"))


def test_a_tree_without_the_accuracy_page_gets_no_accuracy_block(tmp_path):
    assert generate._accuracy_blocks(tmp_path) == []


def test_a_toml_block_is_marked_by_toml_comments():
    text = "a = 1\n# generated:x\nb = 2\n# /generated:x\nc = 3\n"

    assert generate._block(text, "x", "b = 3", generate._marks("pyproject.toml")) == (
        "a = 1\n# generated:x\nb = 3\n# /generated:x\nc = 3\n")


def test_one_file_takes_several_blocks(tmp_path):
    (tmp_path / "d.md").write_bytes(b"<!-- generated:a -->\n<!-- /generated:a -->\n"
                                    b"<!-- generated:b -->\n<!-- /generated:b -->\n")

    assert generate._applied(tmp_path, [("d.md", "a", "A"), ("d.md", "b", "B")]) == {
        "d.md": "<!-- generated:a -->\nA\n<!-- /generated:a -->\n"
                "<!-- generated:b -->\nB\n<!-- /generated:b -->\n"}
