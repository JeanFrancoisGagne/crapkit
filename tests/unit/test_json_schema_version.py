"""Every --json payload carries a schema integer, so a wrapper that pinned an
old shape fails loudly on a renamed field instead of reading it as an empty
queue. One version for the whole surface: any removed or retyped field bumps it.
"""
import json

from crapkit.cli import SCHEMA_VERSION, _print_json


def test_print_json_stamps_the_schema_version(capsys):
    _print_json({"answer": 42})
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"answer": 42, "schema": SCHEMA_VERSION}


def test_print_json_keeps_keys_sorted(capsys):
    _print_json({"b": 1, "a": 2})
    out = capsys.readouterr().out
    assert out.index('"a"') < out.index('"b"') < out.index('"schema"')


def test_the_version_is_a_positive_int():
    assert isinstance(SCHEMA_VERSION, int) and SCHEMA_VERSION >= 1
