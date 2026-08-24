"""A rejected config key has to name the keys that would have been accepted.

`unknown key crapkit.churn_windo_months` alone leaves the reader guessing which
of five tables the key belonged to and how the real one is spelled, with no key
list anywhere in the tool. The table travels with the finding so the message can
carry that list.
"""
from crapkit.cli import _unknown_key_text
from crapkit.doctor import _KNOWN, UnknownKey, table_label, unknown_key_findings, valid_keys


def test_a_finding_carries_the_table_the_key_was_checked_against():
    assert unknown_key_findings({"crapkit": {"churn_windo_months": 3}}) == [
        UnknownKey("crapkit.churn_windo_months", "crapkit")]


def test_every_table_reports_its_own_name():
    raw = {"targett": 6, "crapkit": {"nope": 1}, "exclude": {"glob": []},
           "scope": [{"name": "src", "tarrget": 5}], "lane": [{"name": "js", "artifacts": "x"}]}

    assert [(u.path, u.table) for u in unknown_key_findings(raw)] == [
        ("targett", ""), ("crapkit.nope", "crapkit"), ("exclude.glob", "exclude"),
        ("scope 'src'.tarrget", "scope"), ("lane 'js'.artifacts", "lane")]


def test_the_dotted_path_still_names_the_scope_the_key_sat_in():
    raw = {"crapkit": {"nope": 1}, "scope": [{"name": "src", "tarrget": 5}]}

    assert [u.path for u in unknown_key_findings(raw)] == ["crapkit.nope", "scope 'src'.tarrget"]


def test_the_message_spells_out_every_key_the_table_accepts():
    text = _unknown_key_text(UnknownKey("crapkit.churn_windo_months", "crapkit"))

    assert text.startswith("unknown key crapkit.churn_windo_months")
    assert "[crapkit] accepts these keys: " in text
    assert all(key in text for key in _KNOWN["crapkit"])


def test_an_array_of_tables_is_named_with_the_brackets_it_is_written_with():
    assert table_label("scope") == "[[scope]]"
    assert table_label("lane") == "[[lane]]"
    assert table_label("exclude") == "[exclude]"
    assert table_label("") == "crapkit.toml"


def test_a_stray_top_level_key_is_told_which_tables_exist():
    text = _unknown_key_text(UnknownKey("targett", ""))

    assert "crapkit.toml accepts these tables: crapkit, exclude, lane, scope" in text


def test_the_accepted_keys_are_sorted_so_the_message_never_moves():
    assert valid_keys("exclude") == ("globs", "max_file_bytes")
    assert valid_keys("scope") == ("coverage_optional", "languages", "name", "notes",
                                   "paths", "target")
