"""doctor over a .crapkit/artifacts.json whose entry for a lane is not an object.

lanes.read_stamps promises that a hand-mangled file reads as no stamps, and the
lane scheduler copes with one. doctor --json and doctor --tune read the entry
with .get() and died with AttributeError, and check_config spawns doctor --json,
so the MCP tool died with them. The entry reads as no stamp now, and doctor
names it in a WARN so the reader can find the line to delete.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit import mcp_server
from crapkit.cli import main

MANGLED = {"coverage/unit.json": "garbage",
           "coverage/ui.json": {"commit": "abc", "lane": "ui", "seconds": 4.0}}


def _mangle(root) -> None:
    stamps = root / ".crapkit" / "artifacts.json"
    stamps.parent.mkdir(parents=True, exist_ok=True)
    stamps.write_text(json.dumps(MANGLED), encoding="utf-8")


def _run(argv: list[str], root, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(root)])
    out = capsys.readouterr()
    return code, out.out, out.err


def _named(texts: list[str]) -> list[str]:
    return [text for text in texts if "'coverage/unit.json'" in text and "not an object" in text]


def test_doctor_json_reads_the_entry_as_no_stamp_and_warns_about_it(repo, capsys):
    _mangle(repo)

    code, out, _ = _run(["doctor", "--json"], repo, capsys)

    payload = json.loads(out)
    assert code == 0, payload["problems"]
    lanes = {lane["name"]: lane for lane in payload["lanes"]}
    assert (lanes["unit"]["commit"], lanes["unit"]["seconds"]) == (None, None)
    assert (lanes["ui"]["commit"], lanes["ui"]["seconds"]) == ("abc", 4.0)
    assert len(_named(payload["warnings"])) == 1, payload["warnings"]


def test_plain_doctor_prints_the_same_warn(repo, capsys):
    _mangle(repo)

    code, out, _ = _run(["doctor"], repo, capsys)

    assert code == 0, out
    assert len(_named([line for line in out.splitlines() if line.startswith("WARN ")])) == 1, out


def test_doctor_tune_costs_the_lanes_it_can_read(repo, capsys):
    _mangle(repo)

    code, out, err = _run(["doctor", "--tune"], repo, capsys)

    assert code == 0, err
    assert out.splitlines()[-1].startswith("# lane cost: 4.0s serial -> ~4.0s"), out
    assert len(_named(err.splitlines())) == 1, err


def test_a_well_formed_file_adds_no_warn(repo, capsys):
    _mangle(repo)
    (repo / ".crapkit" / "artifacts.json").write_text(
        json.dumps({"coverage/ui.json": MANGLED["coverage/ui.json"]}), encoding="utf-8")

    _, out, _ = _run(["doctor", "--json"], repo, capsys)

    assert [w for w in json.loads(out)["warnings"] if "not an object" in w] == []


def _warns_about(key: str, warnings: list[str]) -> list[str]:
    return [text for text in warnings if f"{key!r}" in text and "not an object" in text]


def test_the_warn_names_the_lane_whose_run_replaces_the_entry(repo, capsys):
    _mangle(repo)

    _, out, _ = _run(["doctor", "--json"], repo, capsys)

    [warn] = _warns_about("coverage/unit.json", json.loads(out)["warnings"])
    assert warn.endswith("lane 'unit' replaces it on its next successful run, "
                         "or delete the entry"), warn


def test_an_entry_no_declared_lane_writes_is_one_to_delete(repo, capsys):
    """write_stamps merges this run's stamps over the file, so a key no lane
    declares (an old artifact path, a typo) is never rewritten, and a WARN
    promising the next run would replace it repeated on every doctor run."""
    stamps = repo / ".crapkit" / "artifacts.json"
    stamps.parent.mkdir(parents=True, exist_ok=True)
    stamps.write_text(json.dumps({"old/gone.json": "garbage"}), encoding="utf-8")

    _, out, _ = _run(["doctor", "--json"], repo, capsys)

    [warn] = _warns_about("old/gone.json", json.loads(out)["warnings"])
    assert warn.endswith("no declared lane writes this key, so delete the entry"), warn


def test_check_config_answers_over_the_entry(repo):
    _mangle(repo)

    result = mcp_server._call_tool(repo, "check_config", {})

    assert result["isError"] is False, result["content"][0]["text"][-800:]
    assert len(_named(result["structuredContent"]["warnings"])) == 1
