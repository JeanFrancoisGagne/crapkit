"""Every list of parser names agrees with the one registry of readers.

`coverage_format._FORMATS` maps each lane `parser` to the adapter that reads
it. Three other places spell the same names out: the lane `parser` enum
admission checks a crapkit.toml against, and the `parser` row of the lane key
table in docs/configuration.md and in docs/lanes.md. A maintainer who adds a
reader edits the registry and the enum; this test then names every place that
still lists the old set. The CLI help names no parser at all, so a new reader
never edits it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from crapkit import config_contract, coverage_format
from crapkit.cli.parser import _help_topics, build_parser
from crapkit.config import load_config_text
from crapkit.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("docs/configuration.md", "docs/lanes.md")
ENUM = "config_contract's lane parser enum"
PARSER_ROW = re.compile(r"^\| `parser` \|(.*)$", re.MULTILINE)


def _doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _row_names(path: str, text: str) -> set[str]:
    """The backticked names in the one `parser` row of the doc's lane key table."""
    rows = PARSER_ROW.findall(text)
    assert len(rows) == 1, f"{path}: expected one `parser` table row, found {len(rows)}"
    return set(re.findall(r"`([^`]+)`", rows[0]))


def _places(docs: dict[str, str]) -> dict[str, set[str]]:
    return {ENUM: set(config_contract.enum_values("lane", "parser")),
            **{f"{path} parser row": _row_names(path, text) for path, text in docs.items()}}


def _gaps(places: dict[str, set[str]]) -> list[str]:
    """One line per name a place lacks or adds beside coverage_format._FORMATS."""
    registry = set(coverage_format._FORMATS)
    lacks = [f"{place} lacks {name!r}, which coverage_format._FORMATS reads"
             for place, names in places.items() for name in sorted(registry - names)]
    adds = [f"{place} names {name!r}, which coverage_format._FORMATS has no reader for"
            for place, names in places.items() for name in sorted(names - registry)]
    return lacks + adds


def _shipped_places() -> dict[str, set[str]]:
    return _places({path: _doc(path) for path in DOCS})


def test_the_enum_and_both_docs_rows_name_the_registry():
    """No gap also proves the enum equals the registry, so one assert names every place."""
    gaps = _gaps(_shipped_places())
    assert not gaps, "\n".join(gaps)


def _failure_of_the_real_test() -> str:
    with pytest.raises(AssertionError) as failed:
        test_the_enum_and_both_docs_rows_name_the_registry()
    return str(failed.value)


def test_a_registry_name_no_other_place_lists_is_named_with_each_place(monkeypatch):
    monkeypatch.setattr(coverage_format, "_FORMATS",
                        {**coverage_format._FORMATS, "no-such-format": object()})

    failure = _failure_of_the_real_test()

    for place in (ENUM, *(f"{path} parser row" for path in DOCS)):
        assert f"{place} lacks 'no-such-format', which coverage_format._FORMATS reads" in failure


@pytest.mark.parametrize("path", DOCS)
@pytest.mark.parametrize("dropped", sorted(coverage_format._FORMATS))
def test_a_docs_row_missing_a_name_is_named(monkeypatch, path, dropped):
    docs = {each: _doc(each) for each in DOCS}
    row = PARSER_ROW.search(docs[path]).group(0)
    docs[path] = docs[path].replace(row, row.replace(f"`{dropped}`", "", 1))
    monkeypatch.setattr(sys.modules[__name__], "_doc", docs.__getitem__)

    failure = _failure_of_the_real_test()

    assert f"{path} parser row lacks {dropped!r}, which coverage_format._FORMATS reads" in failure
    assert ENUM not in failure
    assert [each for each in DOCS if each != path and each in failure] == []


def _help_screens() -> dict[str, str]:
    parser = build_parser()
    topics = _help_topics(parser)
    return {"crapkit": parser.format_help(),
            **{f"crapkit {name}": sub.format_help() for name, sub in topics.items()}}


def test_no_help_screen_names_a_parser():
    names = sorted(coverage_format._FORMATS)
    named = [f"{screen}: {name}" for screen, text in _help_screens().items()
             for name in names if re.search(rf"\b{re.escape(name)}\b", text)]
    assert named == []


_LANE_TOML = ('[[scope]]\nname = "py"\npaths = ["pkg"]\nlanguages = ["python"]\n'
              '[[lane]]\nname = "py"\ncommand = "python -m pytest --cov=pkg"\n'
              'artifact = "cov.json"\nparser = "{parser}"\nscopes = ["py"]\n')


def test_a_parser_with_no_reader_is_refused_at_load():
    """cobertura joins the enum only with its reader (m1-readers-05)."""
    assert load_config_text(_LANE_TOML.format(parser="coveragepy")).lanes[0].parser == "coveragepy"
    with pytest.raises(ConfigError) as raised:
        load_config_text(_LANE_TOML.format(parser="cobertura"))
    assert raised.value.exit_code == 3
    assert "unsupported value 'cobertura'" in str(raised.value)
