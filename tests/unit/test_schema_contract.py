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


def test_the_schema_hint_for_mutation_command_names_the_exit_that_gets_no_verdict():
    """An editor shows this hint beside the key; it must not say every nonzero
    exit kills when mutate reads exit 5 as a suite that ran no test."""
    from crapkit.mutate_pool import MutantVerdict, verdict_of

    hint = _schema()["properties"]["crapkit"]["properties"]["mutation_command"]["description"]

    assert verdict_of(5) is MutantVerdict.NO_VERDICT
    assert hint == "suite run once per mutant; nonzero exit = killed, exit 5 = no verdict"
