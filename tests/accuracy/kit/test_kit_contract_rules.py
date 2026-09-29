"""Each contract rule goes red on a planted file that breaks it."""
from pathlib import Path

from accuracy.kit import calcs
from accuracy.kit import test_kit_contract as contract


def _file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_skip_an_xfail_and_an_importorskip_are_found(tmp_path):
    path = _file(tmp_path, "t.py", "import pytest\nfrom pytest import importorskip\n\n\n"
                 "@pytest.mark.xfail\ndef test_a():\n    pytest.skip('no')\n")

    assert contract._skip_uses(path) == ["importorskip", "skip", "xfail"]
    assert contract._skip_uses(_file(tmp_path, "ok.py", "def test_a():\n    pass\n")) == []


def test_a_per_test_settings_call_is_found(tmp_path):
    path = _file(tmp_path, "t.py", "from hypothesis import settings\n\n\n"
                 "@settings(max_examples=5)\ndef test_a():\n    pass\n")

    assert contract._settings_calls(path) == [4]


def test_an_assume_without_an_event_is_found(tmp_path):
    path = _file(tmp_path, "t.py", "from hypothesis import assume, event\n\n\n"
                 "def test_bare(x):\n    assume(x)\n\n\n"
                 "def test_paired(x):\n    event('R1')\n    assume(x)\n")

    assert [line.split("::")[1] for line in contract._unpaired_assumes(path)] == ["test_bare"]


def test_a_bare_model_import_is_found(tmp_path):
    path = _file(tmp_path, "t.py", "import model_score\nfrom model import crap\n"
                 "from accuracy.score_model import model_score as m\nfrom . import model_x\n")

    assert [line.rsplit(" ", 1)[1] for line in contract._bare_model_imports(path)] == [
        "model_score", "model"]


def test_a_hand_table_without_an_outside_source_is_found(tmp_path):
    missing = _file(tmp_path, "hand_a.tsv", "construct\tccn\nif\t2\n")
    weak = _file(tmp_path, "hand_b.tsv",
                 "construct\tccn\tsource\nif\t2\tcrapkit\nfor\t2\t6.0.1\nwhile\t2\tobserved\n"
                 "and\t2\t\nor\t2\thttps://example.org/mccabe#p3\n")

    assert contract._source_problems(missing) == [f"{missing.as_posix()} has no source column"]
    assert [line.split(": source ")[1] for line in contract._source_problems(weak)] == [
        "'crapkit' is not an outside source", "'6.0.1' is not an outside source",
        "'observed' is not an outside source", "'' is not an outside source"]


def test_an_unhashed_requirement_is_found(tmp_path):
    lock = _file(tmp_path, "r.txt", "# header\nalpha==1.0 \\\n    --hash=sha256:ab\n"
                 "beta==2.0\n    # via gamma\n")

    assert [block[0] for block in contract._requirement_blocks(lock) if len(block) < 2] == [
        "beta==2.0"]


def test_a_crapkit_source_path_in_an_oracle_is_found(tmp_path):
    literal = _file(tmp_path, "o.py", "PATH = 'src/crapkit/score.py'\n")
    comment = _file(tmp_path, "c.py", "# see src/crapkit/score.py\nx = 1\n")
    script = _file(tmp_path, "o.mjs", "import x from '../../src/crapkit/a.js'\n")

    assert contract._names_crapkit_source(literal)
    assert not contract._names_crapkit_source(comment)
    assert contract._names_crapkit_source(script)


def test_a_parametrized_node_id_counts_by_its_base_name():
    collected = {"tests/accuracy/a/test_x.py::test_y[py]", "tests/accuracy/a/test_x.py::test_z"}

    assert contract._is_collected("tests/accuracy/a/test_x.py::test_y", collected)
    assert contract._is_collected("tests/accuracy/a/test_x.py::test_z", collected)
    assert not contract._is_collected("tests/accuracy/a/test_x.py::test_w", collected)


def test_a_calc_function_outside_its_modules_or_missing_is_found():
    row = calcs.Calc("score_model", "CRAP score", "t::x", ("src/crapkit/score.py",),
                     ("src/crapkit/score.py:crap",))

    assert contract._function_problem(row, "src/crapkit/score.py:crap") is None
    assert "lies outside its modules" in contract._function_problem(
        row, "src/crapkit/digest.py:totals")
    assert contract._function_problem(row, "src/crapkit/score.py:no_such") == (
        "CRAP score: no function src/crapkit/score.py:no_such")
