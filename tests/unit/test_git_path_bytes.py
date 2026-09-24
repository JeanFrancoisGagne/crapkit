"""A file git names in bytes that are not UTF-8 is left out, and named once.

crapkit keys every row, mark and cache entry on a UTF-8 path. A Linux checkout
can hold a Latin-1 name that has no UTF-8 spelling, and Git for Windows keeps
such a name in the index of a clone made from it. One of them anywhere in the
tree, docs/ included, ended init, inventory, doctor, verify and the pre-commit
gate with a UnicodeDecodeError. Every reader now leaves that file out, and
stderr names it with the fix. Repos come from raw_git, which writes names as
given; neither a Windows argv nor `git add` there can.
"""
import os
import sys

import pytest

from raw_git import SOURCE, checkout, commit, repository, stage

from crapkit import gitpaths
from crapkit.cli.parser import main
from crapkit.diffparse import changed_ranges
from crapkit.gitio import diff_names_since, index_modes, ls_files, renamed_paths, status_names, untracked_files
from crapkit.lane_changes import ChangeReads, visible_paths

NOTE = "crapkit: left out src/caf\\xe9.py: git names it in bytes that are not UTF-8"
CONFIG = b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
TANGLED = (b"def tangled(a, b, c, d):\n"
           + b"".join(b"    if %s:\n        a += 1\n" % v for v in (b"a", b"b", b"c", b"d", b"a > b",
                                                                  b"b > c", b"c > d"))
           + b"    return a\n")
NAMES = [
    # id, a name git holds that has no UTF-8 spelling
    ("tracked-path-invalid-utf8", b"src/caf\xe9.py"),
    ("tracked-path-cp1252-smart-quote", b"src/o\x92brien.py"),
    ("unscored-path-invalid-utf8", b"docs/r\xe9sum\xe9.txt"),
]
VALID = [
    ("tracked-path-valid-accent", "src/café.py"),
    ("tracked-path-cjk-emoji", "src/日本\U0001f680.py"),
]


@pytest.fixture(autouse=True)
def fresh_notes(monkeypatch):
    """Each name is named once per process; every test here is a fresh one."""
    monkeypatch.setattr(gitpaths, "_left_out", set(), raising=False)


# --- the records: `-z` output and diff headers ------------------------------------

def test_a_nul_record_that_is_not_utf8_is_left_out_and_named_once(capsys):
    out = b"a.py\0src/caf\xe9.py\0src/caf\xe9.py\0" + "src/café.py".encode() + b"\0"

    assert gitpaths.nul_paths(out) == ["a.py", "src/café.py"]
    assert gitpaths.nul_records(out) == ["a.py", None, None, "src/café.py"]
    err = capsys.readouterr().err
    assert err.count("crapkit: left out") == 1 and NOTE in err
    assert "rename it (git mv) to have it read" in err


def test_past_five_names_one_line_says_there_are_more(capsys):
    gitpaths.nul_paths(b"\0".join(b"caf\xe9%d.py" % i for i in range(8)))

    err = capsys.readouterr().err.splitlines()
    assert len(err) == 6
    assert err[-1] == "crapkit: left out more names that are not UTF-8 than the five above"


def test_a_record_with_fields_before_its_path_names_only_the_path(capsys):
    assert gitpaths.split_record(b"100644 abc 0\tsrc/caf\xe9.py", 1) == ("100644 abc 0", None)
    assert gitpaths.split_record(b"-\t-\tsrc/a\tb.py", 2) == ("-\t-", "src/a\tb.py")
    assert NOTE in capsys.readouterr().err


@pytest.mark.parametrize("target, ranges", [
    ("b/src/caf\udce9.py", {}),
    ('"b/src/caf\\351.py"', {}),
    ('"b/src/caf\udce9\\"q\\".py"', {}),
    ("b/src/café.py", {"src/café.py": [(1, 1)]}),
    ('"b/src/caf\\303\\251.py"', {"src/café.py": [(1, 1)]}),
], ids=["raw-byte", "octal-escape", "raw-byte-and-quote", "valid-accent", "valid-accent-quoted"])
def test_a_diff_header_names_a_path_or_leaves_the_file_out(target, ranges, capsys):
    assert changed_ranges(f"+++ {target}\n@@ -1 +1 @@\n-a\n+b\n") == ranges
    assert ("crapkit: left out src/caf\\xe9" in capsys.readouterr().err) is not bool(ranges)


# --- the readers, over a real repo ------------------------------------------------

def _repo(tmp_path, name: bytes):
    """HEAD holds crapkit.toml, src/app.py and `name`, checked out the way git
    checks it out on this OS."""
    root = repository(tmp_path)
    base = commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE}, age_days=1)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE, name: SOURCE})
    checkout(root)
    return root, base


@pytest.mark.parametrize("name", [row[1] for row in NAMES], ids=[row[0] for row in NAMES])
def test_every_git_file_list_leaves_out_a_name_that_is_not_utf8(tmp_path, name, capsys):
    root, base = _repo(tmp_path, name)

    assert sorted(ls_files(root)) == ["crapkit.toml", "src/app.py"]
    assert index_modes(root, "src") == {"src/app.py": "100644"}
    assert diff_names_since(root, base) == []
    assert all("\udce9" not in path for path in status_names(root))
    assert "crapkit: left out " in capsys.readouterr().err


@pytest.mark.parametrize("name", [row[1] for row in VALID], ids=[row[0] for row in VALID])
def test_a_valid_non_ascii_name_is_listed_as_git_spells_it(tmp_path, name):
    root, base = _repo(tmp_path, name.encode())

    assert sorted(ls_files(root)) == ["crapkit.toml", "src/app.py", name]
    assert diff_names_since(root, base) == [name]


def test_a_rename_away_from_a_name_that_is_not_utf8_pairs_with_nothing(tmp_path):
    root, _ = _repo(tmp_path, b"src/caf\xe9.py")
    before = commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE, b"src/caf\xe9.py": SOURCE})
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE, b"src/cafe.py": SOURCE},
           deletes=(b"src/caf\xe9.py",))

    assert renamed_paths(root, before) == {}


@pytest.mark.skipif(sys.platform == "win32",
                    reason="needs a POSIX file system that stores a name whose bytes are not UTF-8")
def test_an_untracked_file_named_in_latin1_is_left_out_of_every_status_read(tmp_path):
    root, base = _repo(tmp_path, b"src/app2.py")
    (root / os.fsdecode(b"src/caf\xe9.txt")).write_bytes(b"x")

    assert untracked_files(root) == []
    assert status_names(root) == []
    assert visible_paths(root, ["src"]) == ("src/app.py", "src/app2.py")
    with ChangeReads(root, [base], ["src"]) as reads:
        assert reads.status_names() == ()


# --- the commands ------------------------------------------------------------------

@pytest.mark.parametrize("name", [row[1] for row in NAMES], ids=[row[0] for row in NAMES])
@pytest.mark.parametrize("command", [["inventory"], ["doctor"]], ids=["inventory", "doctor"])
def test_a_command_names_the_file_it_leaves_out_and_goes_on(tmp_path, name, command, capsys):
    root, _ = _repo(tmp_path, name)

    code = main([*command, "--repo", str(root)])

    err = capsys.readouterr().err
    assert code in ((0,) if command == ["inventory"] else (0, 1)), err
    assert "crapkit: left out " in err and "Traceback" not in err


def test_init_names_the_file_it_leaves_out_and_writes_the_config(tmp_path, capsys):
    root = repository(tmp_path)
    commit(root, files={b"src/app.py": SOURCE, b"src/caf\xe9.py": SOURCE})
    checkout(root)

    assert main(["init", "--repo", str(root)]) == 0
    assert NOTE in capsys.readouterr().err
    assert (root / "crapkit.toml").is_file()


@pytest.mark.parametrize("name, code", [
    (b"src/caf\xe9.py", 0),
    (b"docs/caf\xe9.txt", 0),
    ("src/café.py".encode(), 6),
], ids=["staged-scored-invalid-path", "staged-unscored-invalid-path", "staged-valid-accent-path"])
def test_the_precommit_gate_names_a_staged_file_it_cannot_read(tmp_path, name, code, capsys):
    """A ccn-8 function under a name that is not UTF-8 cannot be keyed, so the
    gate leaves the file out and says so; the same function under a UTF-8
    name is refused."""
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE})
    checkout(root)
    stage(root, name, TANGLED)

    assert main(["hook-precommit", "--repo", str(root)]) == code
    err = capsys.readouterr().err
    assert ("crapkit: left out " in err) is (code == 0), err
