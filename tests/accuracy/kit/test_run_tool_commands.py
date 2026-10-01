"""run.py's helper commands: doc-range, xplat, events and oracle-versions."""
import importlib.metadata
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from accuracy.kit import docrange, goldens, oracles, strategies
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


# --- the command line's contract -------------------------------------------------------------
# Each command's usage line and the help it shows, as the module docstring states the
# commands; the options only a test or a packet author passes stay out of the help.

HELP = {
    (): ("usage: run.py [-h] [--tier {push,nightly,weekly,release}] [--shard SHARD] "
         "[--os-sensitive] [--local] [-n WORKERS] [--receipt RECEIPT] Run an accuracy tier.", (
             "--shard SHARD run only the checks modules whose SHARD is this",
             "--os-sensitive run only the checks whose answer can change with the OS "
             "(CI's Windows push job)",
             "--local the releasing machine (release.py's accuracy stage): also run the "
             "checks that read local state, and each check in any cell",
             "-n WORKERS, --workers WORKERS pytest-xdist workers for the pytest session",
             "--receipt RECEIPT where to write the receipt")),
    ("merge",): ("usage: run.py merge [-h] --out OUT receipts [receipts ...]", ()),
    ("kit-goldens",): ("usage: run.py kit-goldens [-h] --declare ID --kind KIND --reason REASON "
                       "[--calcs CALCS] Remeasure the kit's seed corpus, rewrite its goldens "
                       "and declare the change.", ()),
    ("doc-range",): ("usage: run.py doc-range [-h] path start end", ()),
    ("events",): ("usage: run.py events [-h] [--min FLOOR] receipts [receipts ...]", ()),
    ("oracle-versions",): ("usage: run.py oracle-versions [-h] [--tier {push,nightly}]", ()),
}


@pytest.mark.parametrize("command", sorted(HELP))
def test_each_command_shows_its_usage_and_help(command, capsys):
    usage, shown = HELP[command]

    with pytest.raises(SystemExit) as stopped:
        run_tool.main([*command, "--help"])
    text = " ".join(capsys.readouterr().out.split())

    assert stopped.value.code == 0 and text.startswith(usage)
    assert [line for line in shown if line not in text] == []
    assert [hidden for hidden in ("--checks", "--base", "--pins") if hidden in text] == []


@pytest.fixture
def no_regeneration(monkeypatch):
    """kit-goldens must refuse before it remeasures anything."""
    def refuse(base, change):
        raise AssertionError(f"kit-goldens regenerated under {change}")
    monkeypatch.setattr(goldens, "regenerate_seed", refuse)


@pytest.mark.parametrize("argv", [
    ["--tier", "hourly"],
    ["merge", "missing.json"],
    ["kit-goldens", "--kind", "fix", "--reason", "why"],
    ["kit-goldens", "--declare", "T1", "--reason", "why"],
    ["kit-goldens", "--declare", "T1", "--kind", "fix"],
    ["oracle-versions", "--tier", "weekly"],
    ["events"],
])
def test_a_missing_or_unknown_argument_is_a_usage_error(argv, tmp_path, capsys, no_regeneration):
    tail = ["--checks", str(tmp_path), "--receipt", str(tmp_path / "r.json")] if (
        argv[0] == "--tier") else ["--pins", str(_pins(tmp_path))] if argv[0] == "oracle-versions" else []

    with pytest.raises(SystemExit) as stopped:
        run_tool.main(argv + tail)

    assert stopped.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_the_run_command_reads_its_defaults_and_types():
    parse = run_tool._run_parser().parse_args

    assert vars(parse([])) == {"tier": "push", "shard": None, "os_sensitive": False, "local": False,
                               "workers": 0, "receipt": None, "checks": run_tool.CHECKS_DIR}
    assert vars(parse(["--tier", "nightly", "--shard", "one", "--os-sensitive", "--local",
                       "-n", "3", "--receipt", "r.json", "--checks", "c"])) == {
        "tier": "nightly", "shard": "one", "os_sensitive": True, "local": True, "workers": 3,
        "receipt": Path("r.json"), "checks": Path("c")}


def _recorder(seen: list, name: str, answer):
    def record(*args, **named):
        seen.append((name, *args, *([named] if named else [])))
        return answer
    return record


def test_the_run_command_hands_each_argument_on(monkeypatch, tmp_path):
    seen = []
    for name, answer in (("load_checks", ["loaded"]), ("selected", ["chosen"]),
                         ("run_tier", {"outcome": "infra"}), ("_publish", None)):
        monkeypatch.setattr(run_tool, name, _recorder(seen, name, answer))

    code = run_tool.main(["--tier", "nightly", "--shard", "one", "--os-sensitive", "--local",
                          "-n", "2", "--checks", str(tmp_path)])

    assert code == 3
    assert seen == [("load_checks", tmp_path),
                    ("selected", ["loaded"], "nightly", "one", sys.platform, True, {"local": True}),
                    ("run_tier", ["chosen"], "nightly", "one", 2, True, True),
                    ("_publish", {"outcome": "infra"}, run_tool.default_receipt("nightly", "one"))]


def test_no_arguments_run_the_push_tier_and_none_read_the_command_line(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(run_tool, "_run_main", _recorder(seen, "run", 5))
    monkeypatch.setattr(run_tool.sys, "argv", ["run.py", "doc-range", "README.md", "1", "3"])

    assert run_tool.main([]) == 5 and seen == [("run", [])]
    assert run_tool.main() == 0
    assert capsys.readouterr().out.strip() == docrange.header("README.md", 1, 3)


def test_kit_goldens_declares_the_change_it_is_given(monkeypatch, tmp_path, capsys):
    asked = []
    monkeypatch.setattr(goldens, "regenerate_seed",
                        lambda base, change: asked.append((base, change))
                        or [Path("b.json"), Path("c.tsv")])
    monkeypatch.setattr(run_tool.time, "gmtime",
                        lambda *args: time.struct_time((2001, 2, 3, 4, 5, 6, 5, 34, 0)))

    first = run_tool.main(["kit-goldens", "--declare", "T9", "--kind", "fix", "--reason", "moved"])
    second = run_tool.main(["kit-goldens", "--declare", "T8", "--kind", "none", "--reason", "r",
                            "--calcs", "nloc", "--base", str(tmp_path)])

    assert (first, second) == (0, 0)
    assert asked == [
        (run_tool.REPO, {"id": "T9", "date": "2001-02-03", "kind": "fix", "calcs": "",
                         "reason": "moved"}),
        (tmp_path, {"id": "T8", "date": "2001-02-03", "kind": "none", "calcs": "nloc",
                    "reason": "r"})]
    assert capsys.readouterr().out.splitlines() == [
        "relocked b.json under T9", "relocked c.tsv under T9",
        "relocked b.json under T8", "relocked c.tsv under T8"]


def test_events_asks_for_50_of_each_shape_unless_told(tmp_path):
    names = sorted({name for table in strategies.REQUIRED.values() for name in table})
    enough = _receipt(tmp_path, "enough", events={name: 50 for name in names})
    one_short = _receipt(tmp_path, "short", events={name: 49 for name in names})

    assert run_tool.main(["events", str(enough)]) == 0
    assert run_tool.main(["events", str(one_short)]) == 1
    assert run_tool.main(["events", "--min", "49", str(one_short)]) == 0
    assert run_tool.main(["events", "--min", "1", str(_receipt(tmp_path, "none", events={}))]) == 1


def test_oracle_versions_reads_the_repo_pins_unless_told(monkeypatch):
    read = []
    monkeypatch.setattr(oracles, "load_pins", lambda path: read.append(path) or {})

    assert run_tool.main(["oracle-versions"]) == 0
    assert read == [run_tool.PINS]


# --- each command's exact lines ------------------------------------------------------------------

def test_xplat_prints_one_line_per_export_that_differs_with_each_cell_s_shard(tmp_path, capsys):
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"os": "macos", "python": "3.13"}), encoding="utf-8")
    paths = [_receipt(tmp_path, "linux", shard="one", exports={"a.tsv": "1", "b.tsv": "2"}),
             _receipt(tmp_path, "windows", os="windows", shard="one",
                      exports={"a.tsv": "9", "b.tsv": "8"}), bare]

    assert run_tool.main(["xplat", *map(str, paths)]) == 1
    assert capsys.readouterr().out == (
        "xplat: a.tsv differs: linux-3.12-one 1, windows-3.12-one 9, macos-3.13 <missing>\n"
        "xplat: b.tsv differs: linux-3.12-one 2, windows-3.12-one 8, macos-3.13 <missing>\n")


def test_events_says_each_rare_shape_on_its_own_prefixed_line(tmp_path, capsys):
    names = sorted({name for table in strategies.REQUIRED.values() for name in table})
    receipt = _receipt(tmp_path, "r", events={name: 50 for name in names[2:]})

    assert run_tool.main(["events", "--min", "50", str(receipt)]) == 1
    assert capsys.readouterr().err == "".join(
        f"events: {name} occurred 0 times, fewer than 50\n" for name in names[:2])


def test_oracle_versions_prints_a_line_per_oracle_and_prefixes_each_problem(tmp_path, capsys):
    extra = ('\n[oracle.nothing]\nkind = "binary"\ntier = "nightly"\nversion = "1"\n'
             'url = "https://example.invalid/n"\nsha256 = "' + "0" * 64 + '"\n'
             'version_line = "1"\ncommand = ["crapkit-no-such-binary", "--version"]\n'
             '\n[oracle.other]\nkind = "binary"\ntier = "nightly"\nversion = "1"\n'
             'url = "https://example.invalid/o"\nsha256 = "' + "0" * 64 + '"\n'
             'version_line = "1"\ncommand = ["crapkit-no-such-binary-2", "--version"]\n')

    assert run_tool.main(["oracle-versions", "--pins", str(_pins(tmp_path, extra))]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines() == [f"hypothesis: {importlib.metadata.version('hypothesis')}",
                                "nothing: missing", "other: missing"]
    assert [line.split(": ", 1)[0] for line in err.splitlines()] == ["oracle-versions"] * 2


def _pin(kind: str, tier: str) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, tier=tier)


def test_push_reads_only_push_pins_and_no_tier_reads_a_producer():
    pins = {"a": _pin("python", "push"), "b": _pin("binary", "nightly"),
            "c": _pin("producer", "push")}

    assert (run_tool._checked_pins(pins, "push"), run_tool._checked_pins(pins, "nightly")) == (
        ["a"], ["a", "b"])
