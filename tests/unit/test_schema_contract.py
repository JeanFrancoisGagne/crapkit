"""The published JSON Schema and doctor's known-key sets must never drift apart:
an editor autocompleting from the schema and doctor's typo detection are two
views of one vocabulary."""
import json
from pathlib import Path

from crapkit.doctor import _KNOWN

ROOT = Path(__file__).resolve().parent.parent.parent


def _schema() -> dict:
    return json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))


def test_schema_top_level_matches_doctor():
    assert set(_schema()["properties"]) == _KNOWN[""]


def test_schema_tables_match_doctor():
    props = _schema()["properties"]
    assert set(props["crapkit"]["properties"]) == _KNOWN["crapkit"]
    assert set(props["scope"]["items"]["properties"]) == _KNOWN["scope"]
    assert set(props["lane"]["items"]["properties"]) == _KNOWN["lane"]
    assert set(props["exclude"]["properties"]) == _KNOWN["exclude"]


def test_schema_marks_required_keys():
    props = _schema()["properties"]
    assert set(props["scope"]["items"]["required"]) == {"name", "paths", "languages"}
    assert set(props["lane"]["items"]["required"]) == {"name", "command", "artifact", "parser", "scopes"}
