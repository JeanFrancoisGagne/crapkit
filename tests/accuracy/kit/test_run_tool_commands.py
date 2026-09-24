"""run.py's helper commands: doc-range, xplat, events and oracle-versions."""
import importlib.metadata
import json
from pathlib import Path

from accuracy.kit import docrange, strategies
from accuracy.kit.test_run_tool import REPO, run_tool


def _receipt(tmp_path: Path, name: str, **fields) -> Path:
    body = {"tier": "nightly", "os": "linux", "python": "3.12", "shard": None, "exports": {},
            "events": {}, **fields}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_doc_range_prints_the_header_line_a_model_cites(capsys):
    assert run_tool.main(["doc-range", "README.md", "1", "3"]) == 0
    assert capsys.readouterr().out.strip() == docrange.header("README.md", 1, 3)


def test_xplat_passes_when_every_cell_exports_the_same_digests(tmp_path, capsys):
    same = {"small/scored.tsv": "ab", "small/inventory.tsv": "cd"}
    paths = [_receipt(tmp_path, "linux", exports=same),
             _receipt(tmp_path, "windows", os="windows", exports=same)]

    assert run_tool.main(["xplat", *map(str, paths)]) == 0
    assert "2 exports agree across 2 receipts" in capsys.readouterr().out


def test_xplat_names_the_first_export_that_differs_and_one_a_cell_lacks(tmp_path, capsys):
    paths = [_receipt(tmp_path, "linux", exports={"a.tsv": "11", "b.tsv": "22"}),
             _receipt(tmp_path, "windows", os="windows", exports={"a.tsv": "11", "b.tsv": "99"}),
             _receipt(tmp_path, "macos", os="macos", python="3.13", exports={"a.tsv": "11"})]

    assert run_tool.main(["xplat", *map(str, paths)]) == 1
    out = capsys.readouterr().out
    assert "b.tsv differs: linux-3.12 22, windows-3.12 99, macos-3.13 <missing>" in out


def test_events_passes_when_every_required_shape_occurred_often_enough(tmp_path):
    names = sorted({name for table in strategies.REQUIRED.values() for name in table})
    half = {name: 30 for name in names}

    paths = [_receipt(tmp_path, "one", events=half), _receipt(tmp_path, "two", events=half)]

    assert run_tool.main(["events", "--min", "50", *map(str, paths)]) == 0


def test_events_names_each_shape_below_the_floor(tmp_path, capsys):
    names = sorted({name for table in strategies.REQUIRED.values() for name in table})
    counts = {name: 50 for name in names[1:]}
    counts[names[1]] = 49

    receipt = _receipt(tmp_path, "r", events=counts)

    assert run_tool.main(["events", "--min", "50", str(receipt)]) == 1
    err = capsys.readouterr().err
    assert f"{names[0]} occurred 0 times" in err and f"{names[1]} occurred 49 times" in err


def _pins(tmp_path: Path, extra: str = "") -> Path:
    version = importlib.metadata.version("hypothesis")
    text = (f'[oracle.hypothesis]\nkind = "python"\ntier = "push"\nversion = "{version}"\n'
            f'url = "https://example.invalid/h"\nsha256 = "{"0" * 64}"\n'
            f'version_line = "{version}"\npackage = "hypothesis"\n' + extra)
    path = tmp_path / "pins.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_oracle_versions_passes_when_every_oracle_is_its_pin(tmp_path, capsys):
    assert run_tool.main(["oracle-versions", "--pins", str(_pins(tmp_path))]) == 0
    assert "hypothesis" in capsys.readouterr().out


def test_oracle_versions_names_a_drifted_and_a_missing_oracle(tmp_path, capsys):
    extra = ('\n[oracle.radon]\nkind = "python"\ntier = "push"\nversion = "0.0.1"\n'
             'url = "https://example.invalid/r"\nsha256 = "' + "0" * 64 + '"\n'
             'version_line = "0.0.1"\npackage = "radon"\n'
             '\n[oracle.nothing]\nkind = "binary"\ntier = "nightly"\nversion = "1"\n'
             'url = "https://example.invalid/n"\nsha256 = "' + "0" * 64 + '"\n'
             'version_line = "1"\ncommand = ["crapkit-no-such-binary", "--version"]\n')

    assert run_tool.main(["oracle-versions", "--pins", str(_pins(tmp_path, extra))]) == 1
    err = capsys.readouterr().err
    assert "oracle radon is 6.0.1, pins.toml says 0.0.1" in err
    assert "oracle nothing 1 is not on PATH" in err


def test_the_repo_pins_file_is_the_default():
    assert run_tool.PINS == REPO / "tools" / "accuracy" / "pins.toml"
