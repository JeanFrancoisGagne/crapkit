"""next-item's item carries the fields docs/agent-json.md shows for it.

The page lists next-item among the payloads that carry `occurrence`, and its
example and field table show it, but the item never had the key, and neither
did get_next_item's output schema. Every assertion reads a real payload.
"""
import json
from pathlib import Path

from crapkit import mcp_server
from hand_scored_repo import make_repo, run, scored, write_run

ROOT = Path(__file__).resolve().parent.parent.parent


def _documented_item() -> dict:
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    section = page.split("\n## `next-item`\n", 1)[1].split("\n## ", 1)[0]
    return json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])["item"]


def _item(tmp_path, capsys) -> dict:
    root = make_repo(tmp_path / "repo")
    write_run(root, [scored("parse( text , sep )", 1, 20, ccn=9, cov=0.0, crap=90.0,
                            remedy="decompose", occurrence=2)])
    code, out, err = run(root, capsys, "next-item")
    assert code == 0, err
    return json.loads(out)["item"]


def test_the_item_carries_its_occurrence(tmp_path, capsys):
    assert _item(tmp_path, capsys)["occurrence"] == 2


def test_the_item_carries_every_key_the_documented_example_shows(tmp_path, capsys):
    """The example shows a span an artifact answered for, so it has no note."""
    item = _item(tmp_path, capsys)

    assert set(item) - {"uncovered_lines_note"} == set(_documented_item())


def test_get_next_item_declares_occurrence_in_its_item_schema():
    (entry,) = [t for t in mcp_server.tool_listing() if t["name"] == "get_next_item"]
    properties = entry["outputSchema"]["properties"]

    for packet in (properties["item"], properties["items"]["items"]):
        assert packet["properties"]["occurrence"]["type"] == "integer"
