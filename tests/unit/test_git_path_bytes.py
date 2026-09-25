"""A file git names in bytes that are not UTF-8: a value every reader gets, which
the scope assignment refuses when a scope takes it and leaves out otherwise.

crapkit keys every row, mark and cache entry on a UTF-8 path. A Linux checkout
can hold a Latin-1 name that has no UTF-8 spelling, and Git for Windows keeps
such a name in the index of a clone made from it. gitpaths hands the name on in
its surrogateescape spelling and prints nothing. When a scope's own assignment
(scope path, language extension, exclude) takes it, every command that assigns
files to scopes refuses with exit 3: left out, a scoped file crapkit cannot read
would pass every gate unjudged. Any other such name is left out, and the command
names it once. Repos come from raw_git, which writes names as given; neither a
Windows argv nor `git add` there can.
"""
import os
import sys

import pytest

from raw_git import SOURCE, checkout, commit, git, repository

from crapkit import gitpaths
from crapkit.cli.parser import main
from crapkit.config import load_config_text
from crapkit.diffparse import changed_ranges
from crapkit.errors import ConfigError
from crapkit.gitio import diff_names_since, index_modes, ls_files, renamed_paths, status_names, untracked_files
from crapkit.lane_changes import ChangeReads, visible_paths
from crapkit.universe import assign_files, left_out_lines, scan_files

NOTE = "crapkit: left out src/caf\\xe9.py: git names it in bytes that are not UTF-8"
REFUSAL = "is in scope 'src', but git names it in bytes that are not UTF-8"
CONFIG = b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
SCOPED = load_config_text(CONFIG.decode() + '[exclude]\nglobs = ["**/generated/**"]\n')
TANGLED = (b"def tangled(a, b, c, d):\n"
           + b"".join(b"    if %s:\n        a += 1\n" % v for v in (b"a", b"b", b"c", b"d", b"a > b",
                                                                  b"b > c", b"c > d"))
           + b"    return a\n")
CLAIMED = [
    # id, a name git holds that has no UTF-8 spelling, which scope src takes
    ("tracked-path-invalid-utf8", b"src/caf\xe9.py"),
    ("tracked-path-cp1252-smart-quote", b"src/o\x92brien.py"),
]
UNCLAIMED = [
    ("unscored-path-invalid-utf8", b"docs/r\xe9sum\xe9.txt"),
]
VALID = [
    ("tracked-path-valid-accent", "src/café.py"),
    ("tracked-path-cjk-emoji", "src/日本\U0001f680.py"),
]
# The scope's own assignment decides a claim: its path, its language's
# extension, and the exclusions (test and dot directories, exclude globs).
CLAIM_RULE = [
    ("scope-path-and-language", b"src/caf\xe9.py", True),
    ("git-quotes-it", b'src/q"\xff.py', True),
    ("cp1252-smart-quote", b"src/o\x92brien.py", True),
    ("nested-directory", b"src/deep/caf\xe9.py", True),
    ("byte-in-a-directory-name", b"src/r\xe9/app.py", True),
    ("outside-every-scope-path", b"docs/caf\xe9.py", False),
    ("no-scope-language", b"src/caf\xe9.txt", False),
    ("test-directory", b"src/tests/caf\xe9.py", False),
    ("dot-directory", b"src/.cache/caf\xe9.py", False),
    ("exclude-glob", b"src/generated/caf\xe9.py", False),
    ("repo-root-log", b"caf\xe9.log", False),
]


def _shown(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def stage(root, path: bytes, body: bytes) -> None:
    """`body` staged under `path`, bytes as given. Git for Windows refuses a
    `"` in a name it would check out (core.protectNTFS); the index holds one
    all the same once a Linux commit put it there, so the check is off here."""
    blob = git(root, "hash-object", "-w", "--stdin", stdin=body).strip()
    git(root, "-c", "core.protectNTFS=false", "update-index", "--add", "--index-info",
        stdin=b"100644 " + blob + b"\t" + path + b"\n")


# --- the records: `-z` output and diff headers ------------------------------------

def test_a_nul_record_that_is_not_utf8_is_a_value_in_its_place(capsys):
    """Every record keeps its place, so a reader that pairs records
    (`--name-status`) stays in step, and nothing is printed at decode."""
    out = b"a.py\0src/caf\xe9.py\0src/caf\xe9.py\0" + "src/café.py".encode() + b"\0"

    paths = gitpaths.nul_paths(out)

    assert paths == ["a.py", "src/caf\udce9.py", "src/caf\udce9.py", "src/café.py"]
    assert [gitpaths.readable(path) for path in paths] == [True, False, False, True]
    assert paths[1].encode("utf-8", "surrogateescape") == b"src/caf\xe9.py"
    assert capsys.readouterr().err == ""


def test_a_listing_with_every_name_readable_reads_as_itself():
    assert gitpaths.nul_paths("a.py\0src/café.py\0".encode()) == ["a.py", "src/café.py"]


@pytest.mark.parametrize("count", [1, 5, 6, 8])
def test_a_command_names_five_left_out_names_then_counts_the_rest(count):
    names = tuple(f"caf\udce9{i}.py" for i in range(count))

    lines = left_out_lines(names)

    assert lines[:5] == [f"crapkit: left out caf\\xe9{i}.py: git names it in bytes that are not UTF-8, "
                         "and crapkit reads every path as UTF-8; rename it (git mv) to have it read"
                         for i in range(min(count, 5))]
    assert lines[5:] == ([f"crapkit: left out {count - 5} more name(s) that are not UTF-8"]
                         if count > 5 else [])


def test_no_left_out_name_is_no_line():
    assert left_out_lines(()) == []


def test_a_record_with_fields_before_its_path_names_only_the_path(capsys):
    assert gitpaths.split_record(b"100644 abc 0\tsrc/caf\xe9.py", 1) == ("100644 abc 0", "src/caf\udce9.py")
    assert gitpaths.split_record(b"-\t-\tsrc/a\tb.py", 2) == ("-\t-", "src/a\tb.py")
    assert capsys.readouterr().err == ""


# A `+++ ` header's target, read with surrogateescape, and the path it keys.
HEADERS = [
    ("raw-byte", "b/src/caf\udce9.py", "src/caf\udce9.py"),
    ("octal-escape", '"b/src/caf\\351.py"', "src/caf\udce9.py"),
    ("raw-byte-and-quote", '"b/src/caf\udce9\\"q\\".py"', 'src/caf\udce9"q".py'),
    ("quote-and-octal-escape", '"b/src/q\\"\\377.py"', 'src/q"\udcff.py'),
    ("valid-accent", "b/src/café.py", "src/café.py"),
    ("valid-accent-quoted", '"b/src/caf\\303\\251.py"', "src/café.py"),
    ("cjk-emoji", '"b/src/\\346\\227\\245\\360\\237\\232\\200.py"', "src/日\U0001f680.py"),
    ("double-quote", '"b/src/q\\".py"', 'src/q".py'),
    ("tab", '"b/src/a\\tb.py"', "src/a\tb.py"),
    ("newline", '"b/src/a\\nb.py"', "src/a\nb.py"),
]


@pytest.mark.parametrize("target, path", [row[1:] for row in HEADERS], ids=[row[0] for row in HEADERS])
def test_a_diff_header_keys_its_ranges_under_the_bytes_git_named(target, path, capsys):
    """A name that is not UTF-8 keeps its bytes as surrogates, so the scope
    assignment can take it and refuse it; a UTF-8 name reads as itself."""
    assert changed_ranges(f"+++ {target}\n@@ -1 +1 @@\n-a\n+b\n") == {path: [(1, 1)]}
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("target, path", [row[1:] for row in HEADERS], ids=[row[0] for row in HEADERS])
def test_a_history_line_spells_a_name_as_a_diff_header_does(target, path, capsys):
    """unquote_path reads the names in churn and coupling history. It has the
    header's one rule: a name that is not UTF-8 comes back spelled with
    surrogates, where it used to raise."""
    line = target.replace("b/", "", 1)

    assert gitpaths.unquote_path(line) == path
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("path, shown", [
    ("src/caf\udce9.py", "src/caf\\xe9.py"),
    ('src/q"\udcff.py', 'src/q"\\xff.py'),
    ("src/café.py", "src/café.py"),
])
def test_a_message_shows_each_byte_that_is_not_utf8_as_an_escape(path, shown):
    assert gitpaths.shown(path) == shown


# --- the claim: a scope's own assignment decides -----------------------------------

def _listed(raw: bytes):
    """`raw` as a whole-tree listing names it, beside one readable file."""
    return gitpaths.nul_paths(b"src/app.py\0" + raw + b"\0")


def _diff_keys(raw: bytes):
    """`raw` as a diff header keys it: its surrogateescape spelling, in the list."""
    return ["src/app.py", raw.decode("utf-8", "surrogateescape")]


@pytest.mark.parametrize("channel", [_listed, _diff_keys], ids=["listing", "diff-key"])
@pytest.mark.parametrize("raw, claimed", [row[1:] for row in CLAIM_RULE], ids=[row[0] for row in CLAIM_RULE])
def test_a_name_is_refused_only_when_the_scope_assignment_takes_it(channel, raw, claimed):
    files = channel(raw)
    if not claimed:
        universe = scan_files(files, SCOPED)
        assert universe.by_scope == {"src": ["src/app.py"]}
        assert universe.unreadable == (raw.decode("utf-8", "surrogateescape"),)
        return
    with pytest.raises(ConfigError) as refused:
        assign_files(files, SCOPED)
    message = str(refused.value)
    assert message.startswith(f"{_shown(raw)} {REFUSAL}"), message
    assert "rename it (git mv) to a UTF-8 name" in message and "\n" not in message


def test_a_name_no_scope_path_owns_is_not_reported_as_unclaimed_source():
    """`unclaimed` lists source a scope should own. A name crapkit cannot read
    is listed in `unreadable`, which the command names, and not a second time."""
    files = gitpaths.nul_paths(b"src/app.py\0tools/caf\xe9.py\0tools/run.py\0tools/caf\xe9.py\0")

    universe = scan_files(files, SCOPED)

    assert universe.unclaimed == ("tools/run.py",)
    assert universe.unreadable == ("tools/caf\udce9.py",)


def test_a_readable_tree_leaves_nothing_out():
    assert scan_files(["src/app.py", "src/café.py"], SCOPED).unreadable == ()


def test_several_claimed_names_are_one_line_naming_the_first():
    files = gitpaths.nul_paths(b"src/b\xe9.py\0src/a\xe9.py\0src/a\xe9.py\0docs/c\xe9.txt\0")

    with pytest.raises(ConfigError) as refused:
        scan_files(files, SCOPED)

    assert str(refused.value).startswith(f"src/a\\xe9.py (and 1 more) {REFUSAL}")


# --- the readers, over a real repo ------------------------------------------------

def _repo(tmp_path, name: bytes):
    """HEAD holds crapkit.toml, src/app.py and `name`, checked out the way git
    checks it out on this OS."""
    root = repository(tmp_path)
    base = commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE}, age_days=1)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE, name: SOURCE})
    checkout(root)
    return root, base


@pytest.mark.parametrize("name", [row[1] for row in CLAIMED + UNCLAIMED],
                         ids=[row[0] for row in CLAIMED + UNCLAIMED])
def test_every_git_file_list_names_a_name_that_is_not_utf8_as_its_bytes(tmp_path, name, capsys):
    """The listings hand the name on for the reader to judge; only the hook
    mode read, which git answers by ASCII hook names, keeps it out."""
    root, base = _repo(tmp_path, name)
    spelled = name.decode("utf-8", "surrogateescape")

    assert sorted(ls_files(root)) == sorted(["crapkit.toml", "src/app.py", spelled])
    assert index_modes(root, "src") == {"src/app.py": "100644"}
    assert diff_names_since(root, base) == [spelled]
    assert capsys.readouterr().err == ""


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
def test_an_untracked_file_named_in_latin1_is_a_change_in_every_status_read(tmp_path):
    """Lane reuse reads these: an untracked Latin-1 file under a lane's inputs
    is a change, as an untracked UTF-8 one is."""
    root, base = _repo(tmp_path, b"src/app2.py")
    (root / os.fsdecode(b"src/caf\xe9.txt")).write_bytes(b"x")

    assert untracked_files(root) == ["src/caf\udce9.txt"]
    assert status_names(root) == ["src/caf\udce9.txt"]
    assert sorted(visible_paths(root, ["src"])) == ["src/app.py", "src/app2.py", "src/caf\udce9.txt"]
    with ChangeReads(root, [base], ["src"]) as reads:
        assert reads.status_names() == ("src/caf\udce9.txt",)
        assert "src/caf\udce9.txt" in reads.changed_since(base)


# --- the commands ------------------------------------------------------------------

@pytest.mark.parametrize("name", [row[1] for row in CLAIMED], ids=[row[0] for row in CLAIMED])
@pytest.mark.parametrize("command", [["inventory"], ["doctor"]], ids=["inventory", "doctor"])
def test_a_command_refuses_a_name_a_scope_takes(tmp_path, name, command, capsys):
    root, _ = _repo(tmp_path, name)

    code = main([*command, "--repo", str(root)])

    err = capsys.readouterr().err
    assert code == 3, err
    assert f"crapkit: {_shown(name)} {REFUSAL}" in err and "Traceback" not in err
    assert "crapkit: left out " not in err, err


@pytest.mark.parametrize("name", [row[1] for row in UNCLAIMED], ids=[row[0] for row in UNCLAIMED])
@pytest.mark.parametrize("command", [["inventory"], ["doctor"]], ids=["inventory", "doctor"])
def test_a_command_names_the_file_it_leaves_out_and_goes_on(tmp_path, name, command, capsys):
    root, _ = _repo(tmp_path, name)

    code = main([*command, "--repo", str(root)])

    err = capsys.readouterr().err
    assert code in ((0,) if command == ["inventory"] else (0, 1)), err
    assert err.count("crapkit: left out ") == 1 and REFUSAL not in err and "Traceback" not in err


def test_init_names_the_file_it_leaves_out_and_the_next_command_refuses_it(tmp_path, capsys):
    """No scope exists before init writes one, so init warns and writes the
    config; the first command that assigns files under it refuses."""
    root = repository(tmp_path)
    commit(root, files={b"src/app.py": SOURCE, b"src/caf\xe9.py": SOURCE})
    checkout(root)

    assert main(["init", "--repo", str(root)]) == 0
    assert capsys.readouterr().err.count(NOTE) == 1
    assert (root / "crapkit.toml").is_file()
    assert main(["inventory", "--repo", str(root)]) == 3
    err = capsys.readouterr().err
    assert f"crapkit: src/caf\\xe9.py {REFUSAL}" in err and "crapkit: left out " not in err


@pytest.mark.parametrize("name, code", [
    (b"src/caf\xe9.py", 3),
    (b'src/q"\xff.py', 3),
    (b"docs/caf\xe9.txt", 0),
    ("src/café.py".encode(), 6),
    ('src/q"é.py'.encode(), 6),
], ids=["staged-scored-invalid-path", "staged-scored-quoted-invalid-path", "staged-unscored-invalid-path",
        "staged-valid-accent-path", "staged-quoted-valid-path"])
def test_the_precommit_gate_names_a_staged_file_it_cannot_read(tmp_path, name, code, capsys):
    """A ccn-8 function under a name that is not UTF-8 cannot be keyed. In a
    scope the gate refuses the commit and names the rename, in one line; out
    of every scope it leaves the file out and says so, in one line; the same
    function under a UTF-8 name is refused for its ccn."""
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE})
    checkout(root)
    stage(root, name, TANGLED)

    assert main(["hook-precommit", "--repo", str(root)]) == code
    err = capsys.readouterr().err
    assert err.count("crapkit: left out ") == (1 if code == 0 else 0), err
    assert err.count(f"crapkit: {_shown(name)} {REFUSAL}") == (1 if code == 3 else 0), err
