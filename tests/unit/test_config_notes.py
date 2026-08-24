"""Notes are the operational traps a repo learned the hard way.

Consumer configs carry them as TOML comments — "the py lane must run from the
repo root", "bump SCHEMA_VERSION with any column" — where the parser drops them
and no payload can quote them. `notes` promotes them to config: one list under
[crapkit] for the repo, one per [[scope]].
"""
import json

import pytest

from crapkit.config import Config, ConfigError, _parse_scopes, load_config_text

REPO_NOTE = "the py lane must run from the repo root; a nested cwd measures nothing"
SRC_NOTE = "store.py owns the sqlite schema; bump SCHEMA_VERSION with any column"

NOTED = f"""
[crapkit]
target = 6
notes = ["{REPO_NOTE}"]

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
notes = ["{SRC_NOTE}"]

[[scope]]
name = "gen"
paths = ["gen"]
languages = ["python"]
"""


def test_repo_notes_load_off_the_crapkit_table():
    assert load_config_text(NOTED).notes == (REPO_NOTE,)


def test_scope_notes_key_only_the_scopes_that_wrote_one():
    assert load_config_text(NOTED).scope_notes == {"src": (SRC_NOTE,)}


def test_a_config_that_declares_none_reads_empty_never_none():
    bare = Config()

    assert (bare.notes, bare.scope_notes) == ((), {})


def test_notes_keep_the_order_they_were_written_in():
    text = NOTED.replace(f'["{REPO_NOTE}"]', '["first", "second", "third"]')

    assert load_config_text(text).notes == ("first", "second", "third")


def test_both_note_fields_go_straight_into_a_json_payload():
    """Their whole reason to exist is being quoted to an agent, and that trip is
    through --json. A mapping json cannot encode would break at the payload, one
    layer away from the config that chose the type."""
    cfg = load_config_text(NOTED)

    assert json.loads(json.dumps({"notes": cfg.notes, "scope_notes": cfg.scope_notes})) == {
        "notes": [REPO_NOTE], "scope_notes": {"src": [SRC_NOTE]}}


def test_a_bare_string_is_rejected_rather_than_split_into_letters():
    text = NOTED.replace(f'["{REPO_NOTE}"]', f'"{REPO_NOTE}"')

    with pytest.raises(ConfigError) as exc:
        load_config_text(text)

    assert "notes" in str(exc.value)


def test_a_scope_note_that_is_not_a_string_is_rejected():
    text = NOTED.replace(f'["{SRC_NOTE}"]', "[3]")

    with pytest.raises(ConfigError) as exc:
        load_config_text(text)

    assert "scope 'src'" in str(exc.value)


def test_scopes_and_their_notes_come_off_one_pass_over_the_rows():
    """One walk of the [[scope]] rows produces both the scopes and their notes.

    A one-shot iterator is the proof: a second pass over it sees nothing, so a
    parser that read the rows twice would return notes for no scope at all.
    """
    rows = iter([{"name": "src", "paths": ["src"], "languages": ["python"], "notes": ["a"]},
                 {"name": "gen", "paths": ["gen"], "languages": ["python"], "notes": ["b"]}])

    scopes, notes = _parse_scopes(rows)

    assert [s.name for s in scopes] == ["src", "gen"]
    assert notes == {"src": ("a",), "gen": ("b",)}
