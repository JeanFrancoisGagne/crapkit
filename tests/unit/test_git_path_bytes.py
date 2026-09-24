"""A file git names in bytes that are not UTF-8: left out and named, or refused
when a scope takes it.

crapkit keys every row, mark and cache entry on a UTF-8 path. A Linux checkout
can hold a Latin-1 name that has no UTF-8 spelling, and Git for Windows keeps
such a name in the index of a clone made from it. Every reader leaves that file
out and names it on stderr with the fix. When a scope's own assignment (scope
path, language extension, exclude) takes the name's surrogateescape spelling,
every command that assigns files to scopes refuses with exit 3 instead: left
out, a scoped file crapkit cannot read would pass every gate unjudged. Repos
come from raw_git, which writes names as given; neither a Windows argv nor
`git add` there can.
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
from crapkit.universe import assign_files, scan_files

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


@pytest.fixture(autouse=True)
def fresh_notes(monkeypatch):
    """Each name is named once per command; every test here is a fresh one."""
    monkeypatch.setattr(gitpaths, "_left_out", set(), raising=False)


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

def test_a_nul_record_that_is_not_utf8_is_left_out_and_named_once(capsys):
    out = b"a.py\0src/caf\xe9.py\0src/caf\xe9.py\0" + "src/café.py".encode() + b"\0"

    paths = gitpaths.nul_paths(out)
    assert paths == ["a.py", "src/café.py"]
    assert paths.left_out == (b"src/caf\xe9.py", b"src/caf\xe9.py")
    assert gitpaths.nul_records(out) == ["a.py", None, None, "src/café.py"]
    err = capsys.readouterr().err
    assert err.count("crapkit: left out") == 1 and NOTE in err
    assert "rename it (git mv) to have it read" in err


def test_a_listing_with_every_name_readable_leaves_nothing_out():
    paths = gitpaths.nul_paths("a.py\0src/café.py\0".encode())

    assert paths == ["a.py", "src/café.py"] and paths.left_out == ()


def test_past_five_names_one_line_says_there_are_more(capsys):
    gitpaths.nul_paths(b"\0".join(b"caf\xe9%d.py" % i for i in range(8)))

    err = capsys.readouterr().err.splitlines()
    assert len(err) == 6
    assert err[-1] == "crapkit: left out more names that are not UTF-8 than the five above"


def test_a_new_command_names_again_what_an_earlier_one_named(capsys):
    """One process can run several commands (the test suite's in-process
    runner does): each names its own left-out files."""
    gitpaths.nul_paths(b"src/caf\xe9.py\0")
    gitpaths.reset_left_out()
    gitpaths.nul_paths(b"src/caf\xe9.py\0")

    assert capsys.readouterr().err.count(NOTE) == 2


def test_a_record_with_fields_before_its_path_names_only_the_path(capsys):
    assert gitpaths.split_record(b"100644 abc 0\tsrc/caf\xe9.py", 1) == ("100644 abc 0", None)
    assert gitpaths.split_record(b"-\t-\tsrc/a\tb.py", 2) == ("-\t-", "src/a\tb.py")
    assert NOTE in capsys.readouterr().err


@pytest.mark.parametrize("target, path", [
    ("b/src/caf\udce9.py", "src/caf\udce9.py"),
    ('"b/src/caf\\351.py"', "src/caf\udce9.py"),
    ('"b/src/caf\udce9\\"q\\".py"', 'src/caf\udce9"q".py'),
    ('"b/src/q\\"\\377.py"', 'src/q"\udcff.py'),
    ("b/src/café.py", "src/café.py"),
    ('"b/src/caf\\303\\251.py"', "src/café.py"),
    ('"b/src/\\346\\227\\245\\360\\237\\232\\200.py"', "src/日\U0001f680.py"),
    ('"b/src/q\\".py"', 'src/q".py'),
    ('"b/src/a\\tb.py"', "src/a\tb.py"),
    ('"b/src/a\\nb.py"', "src/a\nb.py"),
], ids=["raw-byte", "octal-escape", "raw-byte-and-quote", "quote-and-octal-escape", "valid-accent",
        "valid-accent-quoted", "cjk-emoji", "double-quote", "tab", "newline"])
def test_a_diff_header_keys_its_ranges_under_the_bytes_git_named(target, path, capsys):
    """A name that is not UTF-8 keeps its bytes as surrogates, so the scope
    assignment can take it and refuse it; a UTF-8 name reads as itself."""
    assert changed_ranges(f"+++ {target}\n@@ -1 +1 @@\n-a\n+b\n") == {path: [(1, 1)]}
    assert ("crapkit: left out src/" in capsys.readouterr().err) is not gitpaths.readable(path)


@pytest.mark.parametrize("path, shown", [
    ("src/caf\udce9.py", "src/caf\\xe9.py"),
    ('src/q"\udcff.py', 'src/q"\\xff.py'),
    ("src/café.py", "src/café.py"),
])
def test_a_message_shows_each_byte_that_is_not_utf8_as_an_escape(path, shown):
    assert gitpaths.shown(path) == shown


# --- the claim: a scope's own assignment decides -----------------------------------

def _listed(raw: bytes):
    """`raw` as a whole-tree listing leaves it out, beside one readable file."""
    return gitpaths.nul_paths(b"src/app.py\0" + raw + b"\0")


def _diff_keys(raw: bytes):
    """`raw` as a diff header keys it: its surrogateescape spelling, in the list."""
    return ["src/app.py", raw.decode("utf-8", "surrogateescape")]


@pytest.mark.parametrize("channel", [_listed, _diff_keys], ids=["listing", "diff-key"])
@pytest.mark.parametrize("raw, claimed", [row[1:] for row in CLAIM_RULE], ids=[row[0] for row in CLAIM_RULE])
def test_a_name_is_refused_only_when_the_scope_assignment_takes_it(channel, raw, claimed):
    files = channel(raw)
    if not claimed:
        assert scan_files(files, SCOPED).by_scope == {"src": ["src/app.py"]}
        return
    with pytest.raises(ConfigError) as refused:
        assign_files(files, SCOPED)
    message = str(refused.value)
    assert message.startswith(f"{_shown(raw)} {REFUSAL}"), message
    assert "rename it (git mv) to a UTF-8 name" in message and "\n" not in message


def test_a_name_no_scope_path_owns_is_not_reported_as_unclaimed_source():
    """`unclaimed` lists source a scope should own. A name crapkit cannot read
    is already named on stderr, so it is not listed a second time there."""
    files = gitpaths.nul_paths(b"src/app.py\0tools/caf\xe9.py\0tools/run.py\0")

    assert scan_files(files, SCOPED).unclaimed == ("tools/run.py",)


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
def test_every_git_file_list_leaves_out_a_name_that_is_not_utf8(tmp_path, name, capsys):
    root, base = _repo(tmp_path, name)

    tracked = ls_files(root)
    assert sorted(tracked) == ["crapkit.toml", "src/app.py"] and tracked.left_out == (name,)
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

@pytest.mark.parametrize("name", [row[1] for row in CLAIMED], ids=[row[0] for row in CLAIMED])
@pytest.mark.parametrize("command", [["inventory"], ["doctor"]], ids=["inventory", "doctor"])
def test_a_command_refuses_a_name_a_scope_takes(tmp_path, name, command, capsys):
    root, _ = _repo(tmp_path, name)

    code = main([*command, "--repo", str(root)])

    err = capsys.readouterr().err
    assert code == 3, err
    assert f"crapkit: {_shown(name)} {REFUSAL}" in err and "Traceback" not in err


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
    assert NOTE in capsys.readouterr().err
    assert (root / "crapkit.toml").is_file()
    gitpaths.reset_left_out()
    assert main(["inventory", "--repo", str(root)]) == 3
    assert f"crapkit: src/caf\\xe9.py {REFUSAL}" in capsys.readouterr().err


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
    scope the gate refuses the commit and names the rename; out of every scope
    it leaves the file out and says so; the same function under a UTF-8 name
    is refused for its ccn."""
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": SOURCE})
    checkout(root)
    stage(root, name, TANGLED)

    assert main(["hook-precommit", "--repo", str(root)]) == code
    err = capsys.readouterr().err
    assert ("crapkit: left out " in err) is (code != 6), err
    assert (f"crapkit: {_shown(name)} {REFUSAL}" in err) is (code == 3), err
