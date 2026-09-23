"""The advisory honours a mark whatever shape its ratchet line took.

`dump_ratchet` writes a mark as a self-described `@crapkit-record-v1` line when
its path starts with `#` or holds a tab, a line break or a Unicode separator. The
advisory read only lines that start with the raw path and a tab, so it saw no
mark for those files and warned about debt the repo had signed for.
"""
import io
import json
from pathlib import Path

import pytest

from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet

_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"  # ccn 8, over the ceiling of 6


def _toml(scope_path: str) -> str:
    return ('[crapkit]\ntarget = 6\n\n'
            f'[[scope]]\nname = "calc"\npaths = ["{scope_path}"]\nlanguages = ["python"]\n')


def _repo(tmp_path: Path, scope_path: str, rel: str, marks_text: str) -> Path:
    (tmp_path / "crapkit.toml").write_text(_toml(scope_path), encoding="utf-8")
    edited = tmp_path / rel
    edited.parent.mkdir(parents=True, exist_ok=True)
    edited.write_text(BREACH, encoding="utf-8", newline="\n")
    (tmp_path / "crapkit-ratchet.tsv").write_text(marks_text, encoding="utf-8", newline="\n")
    return edited


def _advise(edited: Path, root: Path, monkeypatch, capsys) -> tuple[int, str]:
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(edited)}, "cwd": str(root)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = main(["claude-hook", "--protocol", "1"])
    return code, capsys.readouterr().err


@pytest.mark.parametrize("scope_path, rel", [
    ("#calc", "#calc/grade.py"),
    ("calc", "calc/grade two.py"),
])
def test_a_mark_written_as_an_encoded_record_silences_the_advisory(scope_path, rel, tmp_path,
                                                                   monkeypatch, capsys):
    marks = dump_ratchet([RatchetEntry(rel, "sprawl( n )", 72.0)], key_version=1)
    assert "@crapkit-record-v1" in marks
    edited = _repo(tmp_path, scope_path, rel, marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert (code, err) == (0, "")


def test_another_files_encoded_mark_never_covers_this_one(tmp_path, monkeypatch, capsys):
    marks = dump_ratchet([RatchetEntry("#calc/grade.py", "sprawl( n )", 72.0)], key_version=1)
    edited = _repo(tmp_path, "calc", "calc/grade.py", marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert code == 2, err
    assert "calc/grade.py:1  sprawl( n )" in err


def test_a_legacy_raw_mark_whose_path_starts_with_a_hash_covers_only_that_path(
        tmp_path, monkeypatch, capsys):
    """Before encoded records, such a mark was written raw. It is a mark, not a
    comment, because it holds a tab, and it names `#calc/grade.py` alone."""
    marks = ("path\tlong_name\tcrap\n"
             "#calc/grade.py\tsprawl( n )\t72.0000\n")
    edited = _repo(tmp_path, "calc", "calc/grade.py", marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert code == 2, err
    assert "crapkit advisory: 1 function(s) over ceiling 6 in calc/grade.py" in err


def test_an_unreadable_record_line_leaves_this_files_raw_mark_in_force(tmp_path, monkeypatch,
                                                                       capsys):
    marks = ("path\tlong_name\tcrap\n"
             "@crapkit-record-v9\t[\"calc/grade.py\",\"sprawl( n )\",\"1.0\"]\n"
             "calc/grade.py\tsprawl( n )\t72.0000\n")
    edited = _repo(tmp_path, "calc", "calc/grade.py", marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert (code, err) == (0, "")
