"""Marks follow code that moved: the explicit `ratchet move` re-path, the
rename detection prune consults before it calls a function gone, `rekey_marks`,
the whole-key rewrite every writer of a mark's key goes through, and
`ratchet move --function`, which re-keys one mark after a signature edit."""
import argparse
import hashlib
import re
import shlex
from fractions import Fraction
from pathlib import Path

import pytest

from accuracy.kit import exact

from crapkit import keys
from crapkit.analyze import analyze_source
from crapkit.cli import main
from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.ratchet import (MetricStamp, RatchetEntry, dump_ratchet, follow_renames, load_ratchet,
                             move_marks, rekey_marks)
from crapkit.score import ScoredRow


def scored(path, name="hot( )", ccn=8, cov=0.0, scope="src"):
    c = float(exact.crap(ccn, Fraction(cov)))
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_move_repaths_one_exact_file_at_its_recorded_value():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0), RatchetEntry("src/b.ts", "g( )", 12.0)]
    entries, moved = move_marks(prior, "src/a.ts", "lib/a.ts")
    assert moved == 1
    assert entries == [RatchetEntry("lib/a.ts", "f( )", 30.0),
                       RatchetEntry("src/b.ts", "g( )", 12.0)]


def test_move_of_a_directory_prefix_carries_the_whole_subtree():
    prior = [RatchetEntry("src/deep/a.ts", "f( )", 30.0),
             RatchetEntry("src/b.ts", "g( )", 12.0),
             RatchetEntry("other/c.ts", "h( )", 9.0)]
    entries, moved = move_marks(prior, "src/", "lib/")
    assert moved == 2
    assert [e.path for e in entries] == ["lib/b.ts", "lib/deep/a.ts", "other/c.ts"]


def test_a_directory_prefix_tolerates_a_missing_trailing_slash_on_the_target():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0)]
    entries, _ = move_marks(prior, "src/", "lib")
    assert entries == [RatchetEntry("lib/a.ts", "f( )", 30.0)]


def test_an_exact_path_never_matches_a_prefix():
    prior = [RatchetEntry("src/app.ts", "f( )", 30.0)]
    entries, moved = move_marks(prior, "src/app", "lib/app")
    assert moved == 0 and entries == prior


def test_move_reports_zero_when_nothing_matches():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0)]
    entries, moved = move_marks(prior, "src/gone.ts", "lib/gone.ts")
    assert moved == 0 and entries == prior


def test_follow_renames_moves_a_mark_whose_file_git_renamed():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/b.ts")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 1
    assert entries == [RatchetEntry("src/b.ts", "hot( )", 30.0)], "the recorded value travels"


def test_follow_renames_leaves_a_mark_whose_function_is_still_at_its_old_path():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/a.ts"), scored("src/b.ts")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 0 and entries == prior, "a copy is not a move; the mark stays put"


def test_follow_renames_leaves_a_mark_when_the_function_is_absent_at_the_new_path():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/b.ts", name="other( )")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 0 and entries == prior, "the file moved but the function did not survive it"


def test_follow_renames_without_a_rename_map_changes_nothing():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    entries, moved = follow_renames(prior, [], {})
    assert moved == 0 and entries == prior


# --- rekey_marks: one move rewrites a mark's whole key ---------------------------

HEADER = "path\tlong_name\tcrap\n"
MARKS = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
         "src/b.py\tgrade( x )\t31.5000\n"
         "src/c.py\ttotal( n )\t1234.5678\n")
TWINS = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
         "src/b.py\tclassify( a )#2\t31.5000\n")
RANKED = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
          "src/b.py\trank( q )\t31.5000\n"
          "src/c.py\ttotal( n )\t1234.5678\n")


def b(name):
    return "src/b.py", name


# (case, the marks file, the moves, the marks file after them, the count)
REKEY = [
    ("one move keeps every value string and re-sorts", MARKS,
     [(("src/c.py", "total( n )"), ("src/a.py", "total( n , m )"))],
     HEADER + "src/a.py\ttotal( n , m )\t1234.5678\n"
              "src/b.py\tclassify( a )\t7.0000\n"
              "src/b.py\tgrade( x )\t31.5000\n", 1),
    ("an empty move set changes nothing", MARKS, [], MARKS, 0),
    ("a move whose old key holds no mark counts 0", MARKS,
     [(b("classify( z )"), b("classify( z , y )"))], MARKS, 0),
    ("a twin key moves like any other key", TWINS,
     [(b("classify( a )#2"), b("classify( a , b )"))],
     HEADER + "src/b.py\tclassify( a )\t7.0000\n"
              "src/b.py\tclassify( a , b )\t31.5000\n", 1),
    ("an unmarked move sorted first takes no key from a marked move", MARKS,
     [(b("alpha( q )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))], RANKED, 1),
    ("an unmarked move sorted last takes no key from a marked move", MARKS,
     [(b("zeta( q )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))], RANKED, 1),
]


@pytest.mark.parametrize("case, text, moves, after, count", REKEY, ids=[r[0] for r in REKEY])
def test_rekey_marks_answers_the_table(case, text, moves, after, count):
    entries, moved = rekey_marks(load_ratchet(text), moves)

    assert moved == count
    assert dump_ratchet(entries, stamp=MetricStamp.NONE) == after


def test_rekey_marks_refuses_a_move_onto_a_marked_key_and_names_both():
    with pytest.raises(ValueError) as refused:
        rekey_marks(load_ratchet(MARKS), [(b("classify( a )"), b("grade( x )"))])

    assert "src/b.py classify( a )" in str(refused.value)
    assert "src/b.py grade( x )" in str(refused.value)


def test_rekey_marks_refuses_two_moves_onto_one_key():
    moves = [(b("classify( a )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))]

    with pytest.raises(ValueError, match=r"another mark moves to src/b\.py rank\( q \)$"):
        rekey_marks(load_ratchet(MARKS), moves)


# --- ratchet move --function: one mark re-keyed after a signature edit ------------

MARKS_FILE = "crapkit-ratchet.tsv"
GRADE = "calc/grade.py"
CONFIG = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n'
STAMPS = "# crapkit-analysis=13 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
BEFORE = "def classify(a, b):\n    return a\n\n\ndef grade(x):\n    return x\n"
AFTER = BEFORE.replace("classify(a, b)", "classify(a, b, c=None)")
TWINS_OF = ("import os\n\nif os.name:\n    def classify({sig}):\n        return a\nelse:\n"
            "    def classify({sig}):\n        return b\n")
OTHERS = f"{GRADE}\tgrade( x )\t31.5000\ncalc/total.py\ttotal( n )\t1234.5678\n"


def function_repo(root: Path, source: str, rows: str, path: str = GRADE) -> Path:
    """A repo with no .crapkit directory: crapkit.toml, PATH holding SOURCE,
    and a marks file holding ROWS under both stamp lines."""
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (root / "calc").mkdir()
    (root / path).write_text(source, encoding="utf-8")
    (root / MARKS_FILE).write_bytes((STAMPS + rows).encode("utf-8"))
    return root


def move_function(root: Path, path: str, old: str, new: str) -> int:
    return cmd_ratchet(argparse.Namespace(action="move", repo=str(root), files=[], baseline=None,
                                          function=[path, old, new]))


def sha(root: Path) -> str:
    return hashlib.sha256((root / MARKS_FILE).read_bytes()).hexdigest()


def rows_of(root: Path) -> list[str]:
    return (root / MARKS_FILE).read_text(encoding="utf-8").splitlines()[3:]


def values(lines: list[str]) -> list[str]:
    return [line.split("\t")[2] for line in lines]


@pytest.mark.parametrize("value", ["7.0000", "31.5000", "1234.5678"])
def test_a_function_move_rekeys_one_mark_and_moving_it_back_restores_the_bytes(tmp_path, capsys,
                                                                                value):
    root = function_repo(tmp_path, AFTER, f"{GRADE}\tclassify( a , b )\t{value}\n" + OTHERS)
    before, lines = (root / MARKS_FILE).read_bytes(), rows_of(root)

    assert move_function(root, GRADE, "classify( a , b )", "classify") == 0

    assert capsys.readouterr().out == (f"{MARKS_FILE}: re-keyed 1 mark in calc/grade.py from "
                                       "classify( a , b ) to classify( a , b , c = None )\n")
    assert (root / MARKS_FILE).read_bytes() == before.replace(b"\tclassify( a , b )\t",
                                                              b"\tclassify( a , b , c = None )\t")
    assert values(rows_of(root)) == values(lines), "every value field keeps its bytes"

    (root / GRADE).write_text(BEFORE, encoding="utf-8")
    assert move_function(root, GRADE, "classify( a , b , c = None )", "classify") == 0
    assert hashlib.sha256((root / MARKS_FILE).read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()


def test_a_function_move_parses_path_once_and_opens_no_store(tmp_path, monkeypatch):
    from crapkit import analyze, store

    root = function_repo(tmp_path, AFTER, f"{GRADE}\tclassify( a , b )\t7.0000\n")
    parsed = []
    lizard_read = analyze._Analyzer.analyze_source_code

    def counted(self, filename, code):
        parsed.append(filename)
        return lizard_read(self, filename, code)

    def no_store(*args, **kwargs):
        raise AssertionError("ratchet move --function opened the store")

    monkeypatch.setattr(analyze._Analyzer, "analyze_source_code", counted)
    monkeypatch.setattr(store.SnapshotStore, "__init__", no_store)

    assert move_function(root, GRADE, "classify( a , b )", "classify") == 0
    assert parsed == [GRADE]
    assert rows_of(root) == [f"{GRADE}\tclassify( a , b , c = None )\t7.0000"]
    assert not (root / ".crapkit" / "crap.sqlite").exists()


def test_a_twin_ordinal_rekeys_the_second_twin_and_leaves_the_first(tmp_path):
    root = function_repo(tmp_path, TWINS_OF.format(sig="a, b"),
                         f"{GRADE}\tclassify( a )\t7.0000\n{GRADE}\tclassify( a )#2\t31.5000\n")

    assert move_function(root, GRADE, "classify( a )#2", "classify#2") == 0

    assert rows_of(root) == [f"{GRADE}\tclassify( a )\t7.0000",
                             f"{GRADE}\tclassify( a , b )#2\t31.5000"]


def test_the_command_a_twin_refusal_prints_moves_the_mark(tmp_path):
    """keys' Refused sentence for a twin names `ratchet move --function` as the
    way out; run the command it prints and the mark moves."""
    source = TWINS_OF.format(sig="a, b, c=None")
    root = function_repo(tmp_path, source, f"{GRADE}\tclassify( a , b )\t9.0000\n"
                                           f"{GRADE}\tclassify( a , b , c = None )\t7.0000\n")
    functions = {(GRADE, name) for name in keys.key_names(analyze_source(GRADE, source)).values()}
    marked = {(GRADE, "classify( a , b )"), (GRADE, "classify( a , b , c = None )")}
    refused = keys.resolve((GRADE, "classify( a , b , c = None )#2"), functions, marked)
    [command] = re.findall(r"crapkit ratchet move --function .*$", refused.text)

    assert main([*shlex.split(command)[1:], "--repo", str(root)]) == 0

    assert rows_of(root) == [f"{GRADE}\tclassify( a , b , c = None )\t7.0000",
                             f"{GRADE}\tclassify( a , b , c = None )#2\t9.0000"]


def test_every_command_a_refusal_for_two_marked_twins_prints_moves_its_mark(tmp_path):
    """Both twins held a mark and both gained `c=None`: keys' refusal prints
    one command per (old mark, new twin) pair, and each one, run as printed,
    moves its OLD mark onto its NEW twin, the first twin's whole key included."""
    source = TWINS_OF.format(sig="a, b, c=None")
    marks = {"classify( a , b )": "7.0000", "classify( a , b )#2": "31.5000"}
    functions = {(GRADE, name) for name in keys.key_names(analyze_source(GRADE, source)).values()}
    refused = keys.resolve((GRADE, "classify( a , b , c = None )"), functions,
                           {(GRADE, name) for name in marks})
    commands = refused.text.split("the explicit form: ", 1)[1].split(" or ")
    assert len(commands) == 4, refused.text

    for n, command in enumerate(commands):
        (tmp_path / str(n)).mkdir()
        root = function_repo(tmp_path / str(n), source,
                             "".join(f"{GRADE}\t{name}\t{value}\n" for name, value in marks.items()))
        *_, old, new = shlex.split(command)

        assert main([*shlex.split(command)[1:], "--repo", str(root)]) == 0, command

        moved = {name: value for name, value in marks.items() if name != old} | {new: marks[old]}
        assert dict(line.split("\t")[1:] for line in rows_of(root)) == moved, command


TWIN_MARKS = f"{GRADE}\tclassify( a )\t7.0000\n{GRADE}\tclassify( a )#2\t31.5000\n"
# (case, the twins' parameters, OLD, NEW, the marks after): OLD and NEW each
# name one twin while both twins hold a mark.
TWIN_MOVES = [
    ("OLD is a function name with #N", ("a", "a, b"), "classify#2", "classify( a , b )",
     [f"{GRADE}\tclassify( a )\t7.0000", f"{GRADE}\tclassify( a , b )\t31.5000"]),
    ("OLD is the first twin's whole key", ("a, b", "a"), "classify( a )", "classify( a , b )",
     [f"{GRADE}\tclassify( a )#2\t31.5000", f"{GRADE}\tclassify( a , b )\t7.0000"]),
    ("NEW is the second twin's start line", ("a, b", "a, b"), "classify( a )#2", "7",
     [f"{GRADE}\tclassify( a )\t7.0000", f"{GRADE}\tclassify( a , b )#2\t31.5000"]),
]


@pytest.mark.parametrize("case, sigs, old, new, after", TWIN_MOVES, ids=[r[0] for r in TWIN_MOVES])
def test_old_and_new_each_name_one_twin_while_both_hold_marks(tmp_path, case, sigs, old, new, after):
    source = ("import os\n\nif os.name:\n    def classify({}):\n        return a\nelse:\n"
              "    def classify({}):\n        return b\n").format(*sigs)
    root = function_repo(tmp_path, source, TWIN_MARKS)

    assert move_function(root, GRADE, old, new) == 0

    assert rows_of(root) == after


ONE_MARK =f"{GRADE}\tclassify( a , b )\t7.0000\n"
# (case, the source, the marks, PATH OLD NEW, what the refusal says: PATH, the
# names it lists and the fix)
REFUSALS = [
    ("no mark named OLD", AFTER, ONE_MARK, [GRADE, "classify( z )", "classify"],
     ["no mark named classify( z ) in calc/grade.py", '"classify( a , b )"', "name one of them as OLD"]),
    ("no mark in PATH", AFTER, f"calc/total.py\ttotal( n )\t7.0000\n", [GRADE, "classify( a , b )", "classify"],
     ["no mark named classify( a , b ) in calc/grade.py", "calc/grade.py holds no mark"]),
    ("OLD names several marks", AFTER, f"{GRADE}\tclassify( a )\t5.0000\n" + ONE_MARK,
     [GRADE, "classify", "classify"],
     ["classify names 2 marks in calc/grade.py", '"classify( a )", "classify( a , b )"',
      "name one of them as OLD"]),
    ("NEW names no function", AFTER, ONE_MARK, [GRADE, "classify( a , b )", "nothing"],
     ["nothing names no function in calc/grade.py", "classify, grade", "name one of them as NEW"]),
    ("NEW is a bare name twins share", TWINS_OF.format(sig="a, b"), f"{GRADE}\tclassify( a )\t7.0000\n",
     [GRADE, "classify( a )", "classify"],
     ["classify names 2 functions in calc/grade.py", "classify#1, classify#2", "name one of them as NEW"]),
    ("NEW is a line several functions open on", "const pair = [() => 1, () => 2];\n",
     "calc/app.js\tpair( )\t7.0000\n", ["calc/app.js", "pair( )", "1"],
     ["line 1 in calc/app.js is ambiguous", "use a handle: (anonymous)#1, (anonymous)#2"]),
    ("NEW's key holds a mark", AFTER, ONE_MARK + f"{GRADE}\tclassify( a , b , c = None )\t9.0000\n",
     [GRADE, "classify( a , b )", "classify"],
     ["calc/grade.py classify( a , b )", "calc/grade.py classify( a , b , c = None ) already holds a mark",
      "crapkit ratchet prune"]),
    ("PATH ends in /", AFTER, ONE_MARK, ["calc/", "classify( a , b )", "classify"],
     ["calc/ ends in '/'", "name the file"]),
    ("PATH is not in the working tree", AFTER, f"calc/gone.py\tclassify( a , b )\t7.0000\n",
     ["calc/gone.py", "classify( a , b )", "classify"],
     ["cannot read calc/gone.py", "NEW names a function in the working tree's calc/gone.py"]),
]


@pytest.mark.parametrize("case, source, marks, spec, said", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_each_refusal_exits_3_names_path_and_its_fix_and_leaves_the_file(tmp_path, capsys, case,
                                                                         source, marks, spec, said):
    root = function_repo(tmp_path, source, marks, "calc/app.js" if spec[0] == "calc/app.js" else GRADE)
    before = sha(root)

    assert main(["ratchet", "move", "--function", *spec, "--repo", str(root)]) == 3

    err = capsys.readouterr().err
    assert err.startswith("crapkit: ratchet move --function: ") and err.count("\n") == 1, err
    assert [words for words in said if words not in err] == [], err
    assert sha(root) == before


def test_the_function_form_takes_no_path_pair_beside_it(tmp_path, capsys):
    root = function_repo(tmp_path, AFTER, ONE_MARK)

    assert main(["ratchet", "move", "a.py", "b.py", "--function", GRADE, "classify( a , b )",
                 "classify", "--repo", str(root)]) == 3
    assert "takes OLD NEW or --function PATH OLD NEW, not both" in capsys.readouterr().err


def test_one_path_names_both_forms_of_move(tmp_path, capsys):
    root = function_repo(tmp_path, AFTER, ONE_MARK)

    assert main(["ratchet", "move", "a.py", "--repo", str(root)]) == 3
    assert ("ratchet move takes exactly two paths, OLD NEW, or --function PATH OLD NEW to re-key "
            "one function's mark") in capsys.readouterr().err


def test_only_move_takes_the_function_form(tmp_path, capsys):
    root = function_repo(tmp_path, AFTER, ONE_MARK)

    assert main(["ratchet", "seed", "--function", GRADE, "classify( a , b )", "classify",
                 "--repo", str(root)]) == 3
    assert "ratchet seed re-keys no mark, so it takes no --function" in capsys.readouterr().err
