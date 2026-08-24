"""`notes` joins the known-key vocabulary.

doctor is where typos die, so a key the loader now reads has to stop being
reported as one — and `valid_keys`, which every rejection message quotes, has to
answer the same question for the fiftieth unknown key without re-sorting the set.
"""
from crapkit.doctor import unknown_key_findings, valid_keys

NOTED = {
    "crapkit": {"target": 6, "notes": ["run the lane from the repo root"]},
    "scope": [{"name": "src", "paths": ["src"], "languages": ["python"],
               "notes": ["store.py owns the sqlite schema"]}],
}


def test_repo_and_scope_notes_are_recognized_keys():
    assert unknown_key_findings(NOTED) == []


def test_a_typo_beside_notes_is_still_caught():
    typo = {"crapkit": {"note": ["singular"]}, "scope": NOTED["scope"]}

    assert [u.path for u in unknown_key_findings(typo)] == ["crapkit.note"]


def test_notes_is_offered_in_both_tables_that_accept_it():
    assert "notes" in valid_keys("crapkit")
    assert "notes" in valid_keys("scope")


def test_valid_keys_hands_back_the_same_tuple_every_call():
    """One unknown key per line, each line quoting the whole table vocabulary:
    a config with twenty typos in [crapkit] sorted that set twenty times."""
    assert valid_keys("crapkit") is valid_keys("crapkit")
    assert valid_keys("lane") is valid_keys("lane")


def test_the_cached_tuple_is_still_sorted_so_a_message_never_moves():
    assert valid_keys("exclude") == tuple(sorted(valid_keys("exclude")))
