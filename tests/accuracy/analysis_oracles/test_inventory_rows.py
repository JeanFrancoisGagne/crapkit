"""Inventory rows and the portable record encoding of `inventory --export`.

Expected values come from two places, never from crapkit's record code:
- the hand rows below, worked from the source text (McCabe decision points,
  the Sonar increments, agent-json.md's `name( a , b )` long_name form);
- docs/portable-records.md, which says a row is written as
  `@crapkit-record-v1`, a tab and a JSON array exactly when a field holds a tab,
  LF, CR, VT, FF, U+001C to U+001E, U+0085, U+2028 or U+2029, or the first
  field starts with `#`; every other row keeps its raw tab-separated bytes.

analysis_inventory.read_export is the reader that document describes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from accuracy.analysis_oracles import analysis_inventory as ai

pytestmark = pytest.mark.process

# docs/portable-records.md:37-38, the characters the writer encodes.
ENCODED = "\t\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
HEADER = ("scope\tpath\tlong_name\tstart\tend\tccn_std\tccn_mod\tccn\tnloc\tparams\tnesting"
          "\tcognitive\toccurrence")

CAFE = "def café(x):\n    if x:\n        return 1\n    return 2\n"
# By hand: lines 1-4; one `if` gives McCabe 2 and Sonar 1 at depth 1; four code
# lines; one parameter; the only function of its name in the file.
CAFE_LINE = "all\tit's é/ü.py\tcafé( x )\t1\t4\t2\t2\t2\t4\t1\t1\t1\t1"

# One def per character: a literal inside a default value reaches long_name.
TRIGGERS = {"tab": "\t", "vt": "\x0b", "ff": "\x0c", "fs": "\x1c", "gs": "\x1d", "rs": "\x1e",
            "nel": "\x85", "ls": "\u2028", "ps": "\u2029"}
QUIET = {"us": "\x1f", "e_acute": "é", "quote": "'"}


def _def(name: str, char: str) -> str:
    return f'def {name}(a="{char}"):\n    return a\n'


def _files() -> dict:
    defs = {**TRIGGERS, **QUIET}
    return {"it's é/ü.py": CAFE,
            "chars.py": "".join(_def(name, char) for name, char in defs.items())}


def _raw_lines(work: Path, files: dict, extra: str = "") -> list[str]:
    tree = {"crapkit.toml": ai.config(("python",), extra), **files}
    measured = ai.run_inventory(ai.build(tree, work / "repo"), work / "inventory.tsv", spawn=True)
    assert measured.code == 0, measured.stderr
    return (work / "inventory.tsv").read_bytes().decode("utf-8").split("\n")


@pytest.fixture(scope="module")
def lines(tmp_path_factory) -> list[str]:
    return _raw_lines(tmp_path_factory.mktemp("rows"), _files())


def _named(lines: list[str], name: str) -> tuple[str, list[str]]:
    for line in lines[1:]:
        if line and ai.bare(ai.fields(line)[2]) == name:
            return line, ai.fields(line)
    raise AssertionError(f"no row for {name}")


def test_the_header_names_the_thirteen_columns(lines):
    assert lines[0] == HEADER
    assert lines[-1] == ""


def test_a_plain_row_is_its_raw_utf8_fields(lines):
    assert _named(lines, "café")[0] == CAFE_LINE


@pytest.mark.parametrize("name", sorted(TRIGGERS))
def test_a_field_holding_a_listed_character_is_encoded(lines, name):
    line, fields = _named(lines, name)
    marker, tab, rest = line.partition("\t")
    assert (marker, tab) == (ai.MARKER, "\t")
    assert json.loads(rest) == fields
    assert TRIGGERS[name] in fields[2]
    # The physical row holds none of the characters a splitlines() would cut at.
    assert not any(char in rest for char in ENCODED)
    assert fields[:2] + fields[3:] == ["all", "chars.py", *_span(name), "1", "1", "1", "2", "1",
                                        "0", "0", "1"]


@pytest.mark.parametrize("name", sorted(QUIET))
def test_a_field_without_one_stays_raw(lines, name):
    line, fields = _named(lines, name)
    assert not line.startswith(ai.MARKER)
    assert line == "\t".join(fields)
    assert QUIET[name] in fields[2]


def _span(name: str) -> list[str]:
    start = 1 + 2 * list({**TRIGGERS, **QUIET}).index(name)
    return [str(start), str(start + 1)]


def _needs_a_mark(fields: list[str]) -> bool:
    """A scope starting with # or a field holding a character a line split cuts at."""
    return fields[0].startswith("#") or any(c in field for field in fields for c in ENCODED)


def test_every_row_is_marked_exactly_when_the_document_says(lines):
    for line in filter(None, lines[1:]):
        fields = ai.fields(line)
        assert line.startswith(ai.MARKER + "\t") == _needs_a_mark(fields), line
        assert len(fields) == len(HEADER.split("\t"))


def test_a_scope_name_starting_with_a_hash_marks_the_row(tmp_path):
    toml_scope = ai.config(("python",)).replace('name = "all"', 'name = "#core"')
    tree = {"crapkit.toml": toml_scope, "a.py": "def f():\n    return 1\n"}
    measured = ai.run_inventory(ai.build(tree, tmp_path / "repo"), tmp_path / "inv.tsv",
                                spawn=True)
    assert measured.code == 0, measured.stderr
    line = (tmp_path / "inv.tsv").read_bytes().decode("utf-8").split("\n")[1]
    assert line.startswith(ai.MARKER + "\t")
    assert json.loads(line.partition("\t")[2])[:3] == ["#core", "a.py", "f( )"]


def test_write_order_and_an_unrelated_file_leave_the_bytes_unchanged(lines, tmp_path):
    files = _files()
    reversed_files = dict(reversed(list(files.items())))
    assert _raw_lines(tmp_path / "reversed", reversed_files) == lines
    assert _raw_lines(tmp_path / "noise", {**files, "README.md": "# notes\n"}) == lines


@pytest.mark.platform("linux")
def test_a_path_holding_a_tab_or_a_newline_is_encoded(tmp_path):
    files = {"tab\there.py": "def t():\n    return 1\n", "new\nline.py": "def n():\n    return 1\n"}
    body = [line for line in _raw_lines(tmp_path, files)[1:] if line]
    decoded = sorted(ai.fields(line)[1] for line in body)
    assert decoded == ["new\nline.py", "tab\there.py"]
    assert all(line.startswith(ai.MARKER + "\t") for line in body)
