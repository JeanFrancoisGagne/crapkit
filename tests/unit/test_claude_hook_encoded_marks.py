"""The advisory honours a mark whatever shape its ratchet line took.

`dump_ratchet` writes a mark as a self-described `@crapkit-record-v1` line when
its path starts with `#` or holds a tab, a line break or a Unicode separator. The
advisory read only lines that start with the raw path and a tab, so it saw no
mark for those files and warned about debt the repo had signed for.

Since 0.9.0 the mark's value decides the advisory's verdict, so the table below
holds the value the marks fetch answers for each shape a line or a file takes.
"""
import codecs
import io
import json
from pathlib import Path

import pytest

from crapkit.analyze import analyze_source
from crapkit.cli import main
from crapkit.cli.claude_hook import _marks_for
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

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
    ("calc", "calc/grade\u2028two.py"),
])
def test_a_mark_written_as_an_encoded_record_silences_the_advisory(scope_path, rel, tmp_path,
                                                                   monkeypatch, capsys):
    marks = dump_ratchet([RatchetEntry(rel, "sprawl( n )", 72.0)], key_version=1,
                         stamp=metric_version())
    assert "@crapkit-record-v1" in marks
    edited = _repo(tmp_path, scope_path, rel, marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert (code, err) == (0, "")


def test_another_files_encoded_mark_never_covers_this_one(tmp_path, monkeypatch, capsys):
    marks = dump_ratchet([RatchetEntry("#calc/grade.py", "sprawl( n )", 72.0)], key_version=1,
                         stamp=metric_version())
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


# --- the value the fetch answers, line shape by line shape --------------------

# repotext's marks-file kind reads each of these as the same text.
ENCODED = {"utf-8": lambda text: text.encode("utf-8"),
           "utf-8-bom": lambda text: codecs.BOM_UTF8 + text.encode("utf-8"),
           "utf-16-bom": lambda text: codecs.BOM_UTF16_LE + text.encode("utf-16-le")}


@pytest.mark.parametrize(("rel", "key", "encoding"), [
    pytest.param("calc/grade.py", "sprawl( n )", "utf-8", id="raw-line"),
    pytest.param("#calc/grade.py", "sprawl( n )", "utf-8", id="record-hash-path"),
    pytest.param("calc/gr\tade.py", "sprawl( n )", "utf-8", id="record-tab-path"),
    pytest.param("calc/grade.py", "sprawl( n )#2", "utf-8", id="second-twin"),
    pytest.param("calc/grade.py", "sprawl( n )", "utf-8-bom", id="utf-8-bom"),
    pytest.param("calc/grade.py", "sprawl( n )", "utf-16-bom", id="utf-16-bom"),
])
def test_the_marks_fetch_answers_each_marks_value_at_four_places(tmp_path, rel, key, encoding):
    """The value, not only the presence: a raw line or an encoded record, a
    `#2` twin beside its first, a BOM in either encoding. A mark under the
    same key on another file is not this file's."""
    neighbour = "sprawl( n )" if key.endswith("#2") else "first( n )"
    marks = dump_ratchet([RatchetEntry(rel, neighbour, 72.0), RatchetEntry(rel, key, 63.6123),
                          RatchetEntry("calc/other.py", key, 1.0)], key_version=1, stamp=metric_version())
    path = tmp_path / "crapkit-ratchet.tsv"
    path.write_bytes(ENCODED[encoding](marks))

    index = _marks_for(path, rel)

    assert ("@crapkit-record-v1" in marks) == (rel != "calc/grade.py")
    assert f"{index.mark((rel, key)):.4f}" == "63.6123"
    assert index.mark(("calc/other.py", key)) is None


# Two arrows on one line: a same-line twin pair, whose ordinals only a key stamp proves.
SAME_LINE = ("values.map((x) => x).filter((x) => { "
             + " ".join(f"if (x > {n}) x++;" for n in range(7)) + " return x; });\n")


@pytest.mark.parametrize(("stamp", "mark"), [("", None), ("# crapkit-keys=1\n", 99.0)],
                         ids=["unproved", "stamped"])
def test_an_unproved_key_identity_pardons_nothing(tmp_path, stamp, mark):
    """A legacy marks file with no key stamp cannot prove which same-line twin
    `(anonymous)#2` meant, so the fetch answers no mark at all, and the gate
    judges the function as one no mark covers."""
    path = tmp_path / "crapkit-ratchet.tsv"
    path.write_text(f"# {metric_version()}\n{stamp}path\tlong_name\tcrap\napp.ts\t(anonymous)#2\t99.0000\n",
                    encoding="utf-8")

    index = _marks_for(path, "app.ts", analyze_source("app.ts", SAME_LINE, note=False))

    assert index.mark(("app.ts", "(anonymous)#2")) == mark


def test_an_unreadable_record_line_leaves_this_files_raw_mark_in_force(tmp_path, monkeypatch,
                                                                       capsys):
    marks = ("path\tlong_name\tcrap\n"
             "@crapkit-record-v9\t[\"calc/grade.py\",\"sprawl( n )\",\"1.0\"]\n"
             "calc/grade.py\tsprawl( n )\t72.0000\n")
    edited = _repo(tmp_path, "calc", "calc/grade.py", marks)

    code, err = _advise(edited, tmp_path, monkeypatch, capsys)

    assert (code, err) == (0, "")
