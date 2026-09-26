"""A UTF-16 source file's changed lines, counted in its text lines (PRD U29).

git summarizes a UTF-16 file as binary, so crapkit asks for its patch with
`--text`, and git then counts a line at every 0A byte. A character whose UTF-16
code unit holds one (上 is U+4E0A, 0A 4E in UTF-16LE) adds a line git sees and
the text does not, and every hunk after it lands one line late. An edit on the
last line of a ccn-8 function then fell outside the function: the pre-commit
gate exited 0 where the same file in UTF-8, or in UTF-16 without such a
character, exits 6. The ranges of such a file are now mapped onto its text
lines, counted the way the scorer counts them (analyze.decode_source).
"""
import codecs

import pytest

from raw_git import checkout, commit, git, repository, stage

from crapkit.cli.parser import main
from crapkit.diffparse import changed_ranges, utf16_line_spans
from crapkit.gitio import SourcePatch, diff_since, staged_diff

CONFIG = b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
TANGLED = ("def tangled(a):\n" + "".join(f"    if a == {i}:\n        a += 1\n" for i in range(7))
           + "    return a\n")
TAIL = "print(tangled(1))\n"
EDITED_RETURN = ("    return a\n", "    return a + 1\n")
EDITED_TAIL = (TAIL, "print(tangled(2))\n")


def _le(text: str) -> bytes:
    return codecs.BOM_UTF16_LE + text.encode("utf-16-le")


def _be(text: str) -> bytes:
    return codecs.BOM_UTF16_BE + text.encode("utf-16-be")


def _utf8(text: str) -> bytes:
    return text.encode()


# id, encoder, line 1, line ending. Line 1 sits above the function, so a line git
# counts there and the text does not moves every hunk below it.
FILES = [
    ("utf16-le-cjk-0a", _le, "# 上", "\n"),
    ("utf16-be-cjk-0a", _be, "# 上", "\n"),
    ("utf16-le-u010a", _le, "# Ċ", "\n"),
    ("utf16-le-u0a0a-two-bytes", _le, "# ਊ", "\n"),
    ("utf16-le-crlf-cjk-0a", _le, "# 上", "\r\n"),
    ("utf16-be-crlf-cjk-0a", _be, "# 上", "\r\n"),
    ("utf16-le-ascii", _le, "# x", "\n"),
    ("utf16-le-emoji-surrogates", _le, "# \U0001f680", "\n"),
    ("utf16-le-crlf-ascii", _le, "# x", "\r\n"),
    ("utf8-cjk", _utf8, "# 上", "\n"),
    ("utf8-crlf-cjk", _utf8, "# 上", "\r\n"),
]
IDS = [row[0] for row in FILES]


def _body(first: str, newline: str, edit: tuple[str, str] | None = None) -> str:
    text = f"{first}\n{TANGLED}{TAIL}"
    if edit:
        text = text.replace(*edit)
    return text.replace("\n", newline)


def _repo(tmp_path, encode, first: str, newline: str):
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/big.py": encode(_body(first, newline))})
    checkout(root)
    return root


# --- the ranges, where they enter: the patch reader ------------------------------------

RETURN_LINE, TAIL_LINE = 17, 18  # text lines of `    return a` and the print below the function


@pytest.mark.parametrize("encode, first, newline", [row[1:] for row in FILES], ids=IDS)
@pytest.mark.parametrize("edit, line", [(EDITED_RETURN, RETURN_LINE), (EDITED_TAIL, TAIL_LINE)],
                         ids=["last-line-of-the-function", "line-below-the-function"])
def test_a_staged_edit_is_ranged_on_its_text_line(tmp_path, encode, first, newline, edit, line):
    root = _repo(tmp_path, encode, first, newline)
    stage(root, b"src/big.py", encode(_body(first, newline, edit)))

    assert changed_ranges(staged_diff(root)) == {"src/big.py": [(line, line)]}


@pytest.mark.parametrize("encode, first, newline", [row[1:] for row in FILES], ids=IDS)
def test_a_working_tree_edit_is_ranged_on_its_text_line(tmp_path, encode, first, newline):
    """verify and coverage read `git diff <commit>`, and claude-hook reads
    `git diff HEAD -- <file>`: the new side is the working tree."""
    root = _repo(tmp_path, encode, first, newline)
    (root / "src" / "big.py").write_bytes(encode(_body(first, newline, EDITED_RETURN)))

    assert changed_ranges(diff_since(root, "HEAD")) == {"src/big.py": [(RETURN_LINE, RETURN_LINE)]}
    read = SourcePatch(root, "HEAD", paths=("src/big.py",))
    try:
        assert changed_ranges(read.result()) == {"src/big.py": [(RETURN_LINE, RETURN_LINE)]}
    finally:
        read.close()


@pytest.mark.parametrize("encode", [_le, _be], ids=["utf16-le", "utf16-be"])
def test_an_added_line_holding_the_character_is_one_text_line(tmp_path, encode):
    root = _repo(tmp_path, encode, "# x", "\n")
    stage(root, b"src/big.py", encode(_body("# x", "\n", ("    return a\n", "    return a  # 上\n"))))

    assert changed_ranges(staged_diff(root)) == {"src/big.py": [(RETURN_LINE, RETURN_LINE)]}


@pytest.mark.parametrize("encode", [_le, _be], ids=["utf16-le", "utf16-be"])
def test_a_new_utf16_file_is_ranged_over_every_text_line(tmp_path, encode):
    root = _repo(tmp_path, encode, "# x", "\n")
    stage(root, b"src/new.py", encode(_body("# 上", "\n")))

    assert changed_ranges(staged_diff(root))["src/new.py"] == [(1, TAIL_LINE)]


def test_a_staged_deletion_of_a_utf16_file_has_no_ranges(tmp_path):
    root = _repo(tmp_path, _le, "# 上", "\n")
    (root / "src" / "big.py").unlink()
    git(root, "rm", "-q", "--cached", "src/big.py")

    assert changed_ranges(staged_diff(root)) == {}


# --- the gate ----------------------------------------------------------------------------

@pytest.mark.parametrize("encode, first, newline", [row[1:] for row in FILES], ids=IDS)
def test_the_gate_refuses_an_edit_on_the_last_line_of_a_ccn8_function(tmp_path, capsys, encode, first,
                                                                       newline):
    root = _repo(tmp_path, encode, first, newline)
    stage(root, b"src/big.py", encode(_body(first, newline, EDITED_RETURN)))

    assert main(["hook-precommit", "--repo", str(root)]) == 6
    assert "tangled( a )" in capsys.readouterr().out


@pytest.mark.parametrize("encode, first, newline", [row[1:] for row in FILES], ids=IDS)
def test_the_gate_passes_an_edit_below_the_function(tmp_path, encode, first, newline):
    root = _repo(tmp_path, encode, first, newline)
    stage(root, b"src/big.py", encode(_body(first, newline, EDITED_TAIL)))

    assert main(["hook-precommit", "--repo", str(root)]) == 0


# --- the map from git's lines to text lines ----------------------------------------------

SPANS = [
    # id, file bytes, (first, last) text line of each line git counts
    ("le-ascii", _le("a\nb\n"), [(1, 1), (2, 2), (2, 2)]),
    ("le-cjk-splits-line-1", _le("上\nb\n"), [(1, 1), (1, 1), (2, 2), (2, 2)]),
    ("be-cjk-splits-line-1", _be("上\nb\n"), [(1, 1), (1, 1), (2, 2)]),
    ("le-empty-line", _le("a\n\nb"), [(1, 1), (2, 2), (3, 3)]),
    ("le-crlf", _le("a\r\nb\r\n"), [(1, 1), (2, 2), (2, 2)]),
    ("le-lone-cr-is-a-text-line", _le("a\rb\n"), [(1, 2), (2, 2)]),
    ("le-no-final-newline", _le("a\nb"), [(1, 1), (2, 2)]),
    ("le-bom-only", codecs.BOM_UTF16_LE, [(1, 1)]),
    ("le-odd-trailing-byte", _le("a\n") + b"x", [(1, 1), (2, 2)]),
]


@pytest.mark.parametrize("raw, spans", [row[1:] for row in SPANS], ids=[row[0] for row in SPANS])
def test_each_line_git_counts_maps_onto_the_text_lines_it_covers(raw, spans):
    assert utf16_line_spans(raw) == spans
