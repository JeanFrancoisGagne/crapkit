"""docs/agent-json.md describes `brief --json` in the shapes the payload has.

An agent parses the packet by the documented example and tables. Those said
`params` was an array of strings, `attempts` an int, `regrowth.history` a list
of `{run_id, ccn, crap}` objects from trusted runs, `versions` `{crapkit,
lizard}` and `notes` an array; the payload carried `{name, type}` objects, an
array of claims, `[run_id, ccn]` pairs from every stored run, four version keys
and a `{repo, scope}` object. Every assertion below reads a real payload off
`brief --json` and compares the page against it.
"""
import contextlib
import io
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit import packet
from crapkit.cli import main
from hand_scored_repo import make_repo, run, scored, write_run

ROOT = Path(__file__).resolve().parent.parent.parent
# Keys a payload carries only in one branch: the page shows the other one.
CONDITIONAL = {"uncovered_lines_note", "scoped_tests_note"}
_BRACED = re.compile(r"`\{([a-z_]+(?:, [a-z_]+)+)\}`")


@lru_cache(maxsize=None)
def _page() -> str:
    return (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")


def _brief_section() -> str:
    return _page().split("\n## `brief`\n", 1)[1].split("\n## ", 1)[0]


def _subsection(heading: str) -> str:
    return _brief_section().split(f"\n### {heading}\n", 1)[1].split("\n### ", 1)[0]


def _example() -> dict:
    block = _brief_section().split("```json\n", 1)[1].split("```", 1)[0]
    return json.loads(block)


def _table(text: str) -> dict:
    """First-column key -> (second cell, last cell) for every single-key row."""
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        key = re.fullmatch(r"`([a-z_]+)`", cells[0]) if line.startswith("|") else None
        if key:
            rows[key.group(1)] = (cells[1], cells[-1])
    return rows


@pytest.fixture(scope="module")
def payload(tmp_path_factory) -> dict:
    """A real packet: two runs of one function, one released claim on it."""
    root = make_repo(tmp_path_factory.mktemp("brief") / "repo")
    write_run(root, [scored("parse( text , sep )", 1, 20, ccn=9, cov=0.0, crap=90.0,
                            remedy="decompose")])
    write_run(root, [scored("parse( text , sep )", 1, 20, ccn=7, cov=0.0, crap=56.0,
                            remedy="decompose")])
    _cli(root, "next-item", "--claim")
    _cli(root, "claims", "release", "--all")
    return json.loads(_cli(root, "brief", "src/app.py", "parse", "--json"))


def _cli(root: Path, *argv: str) -> str:
    """stdout of one in-process command, for a fixture that outlives capsys."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*argv, "--repo", str(root)])
    assert code == 0, err.getvalue()
    return out.getvalue()


def _shape_gaps(doc, real, where: str) -> list[str]:
    """Where the documented example and the payload disagree in shape. A null
    on either side says nothing about shape, and neither does an empty list."""
    if doc is None or real is None:
        return []
    if isinstance(doc, dict) and isinstance(real, dict):
        return _dict_gaps(doc, real, where)
    if isinstance(doc, list) and isinstance(real, list):
        return _shape_gaps(doc[0], real[0], where + "[]") if doc and real else []
    return [] if _kind(doc) == _kind(real) else [f"{where}: docs {doc!r}, payload {real!r}"]


def _dict_gaps(doc: dict, real: dict, where: str) -> list[str]:
    gaps = [f"{where}.{k}: documented, not in the payload" for k in set(doc) - set(real)]
    gaps += [f"{where}.{k}: in the payload, not documented"
             for k in set(real) - set(doc) - CONDITIONAL]
    for key in sorted(set(doc) & set(real)):
        gaps += _shape_gaps(doc[key], real[key], f"{where}.{key}")
    return gaps


def _kind(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


_TYPES = {"int": int, "float": (int, float), "string": str, "bool": bool,
          "object": dict, "array": list}


def _cell_holds(cell: str, value) -> bool:
    """Does the table's type cell describe this non-null value?"""
    words = cell.replace("**", "").split(" or ")[0].split(" of ")
    if not isinstance(value, _TYPES[words[0]]):
        return False
    if len(words) == 1 or not value:
        return True
    return all(isinstance(v, _TYPES[words[1]]) for v in value)


def test_the_example_packet_has_the_payloads_shape(payload):
    assert _shape_gaps(_example(), payload, "brief") == []


def test_the_example_quotes_the_binds_sentence_every_packet_carries():
    assert _example()["gate_rule"]["binds"] == packet.GATE_BINDS


def test_every_type_the_field_table_names_is_the_payloads_type(payload):
    rows = _table(_subsection("What the session reads"))

    wrong = {key: cell for key, (cell, _) in rows.items()
             if payload.get(key) is not None and not _cell_holds(cell, payload[key])}
    assert wrong == {}


def test_every_braced_key_list_names_the_keys_the_payload_carries(payload):
    rows = {**_table(_subsection("What the session reads")),
            **{f"regrowth.{k}": v for k, v in _table(_subsection(
                "`regrowth`: did this get fixed before?")).items()}}

    wrong = {}
    for key, (_, meaning) in rows.items():
        named = _BRACED.search(meaning)
        value = _lookup(payload, key)
        if named and value:
            real = value if isinstance(value, dict) else value[0]
            documented = set(named.group(1).split(", "))
            if not isinstance(real, dict) or documented != set(real):
                wrong[key] = (sorted(documented), real)
    assert wrong == {}


def _lookup(payload: dict, dotted: str):
    value = payload
    for part in dotted.split("."):
        value = value.get(part)
    return value


def test_regrown_is_documented_as_ccn_falling_and_rising_over_every_run(payload):
    rows = _table(_subsection("`regrowth`: did this get fixed before?"))

    assert payload["regrowth"]["history"] == [[1, 9], [2, 7]], "[run_id, ccn] per run"
    assert "`ccn` fell" in rows["regrown"][1], rows["regrown"]
    assert "every stored run" in rows["history"][1], rows["history"]


def test_a_batch_packet_is_the_brief_payload_without_schema(tmp_path, capsys):
    root = make_repo(tmp_path / "repo")
    write_run(root, [scored("parse( text , sep )", 1, 20, ccn=9, cov=0.0, crap=90.0,
                            remedy="decompose")])
    batch = json.loads(run(root, capsys, "brief", "--batch", "1", "--json")[1])
    single = json.loads(run(root, capsys, "brief", "src/app.py", "parse", "--json")[1])
    rows = _table(_subsection("`--batch N`"))

    assert set(single) - set(batch["packets"][0]) == {"schema"}
    assert "without `schema`" in rows["packets"][1], rows["packets"]
