"""Free text git prints as stored: bytes that are not UTF-8 read, never crash.

git re-encodes a commit's author and message to UTF-8 only when the commit
names its encoding in a header, and never touches a blob. A commit without
that header (an import from another VCS, a client set to a legacy code page)
comes out as the raw bytes it was written with, and `René` in Latin-1 is
`Ren\xe9`: invalid UTF-8. A repo that sets `i18n.commitEncoding` or
`i18n.logOutputEncoding` gets the opposite problem: git re-encodes a UTF-8
name into that encoding on the way out. crapkit asks every git it starts for
UTF-8 output and reads what is still not UTF-8 as U+FFFD.

Every commit here comes from fast-import (raw_git.commit), which writes the
bytes as given; `git commit` would re-encode them first.
"""
import codecs
import os
import sys

import pytest

from raw_git import SOURCE, commit, git, repository

from crapkit import churn_log
from crapkit.churn import parse_git_log_lines
from crapkit.cli.parser import main
from crapkit.cli.reports import _function_commits
from crapkit.errors import GitError
from crapkit.gitio import config_value, file_log_patches, merge_base, worktree_root
from crapkit.ratchet_report import mark_events, report_from_events

LATIN1 = b"Ren\xe9"
REPLACED = "Ren�"

# --- the churn window: `git log --format=%x01%an%x02%at%x02%ct --name-only` ----

AUTHORS = [
    # id, the commit, the repo's own git config, the author the walk names
    ("author-invalid-utf8", {"author": LATIN1}, {}, REPLACED),
    ("author-cp1252-smart-quote", {"author": b"O\x92Brien"}, {}, "O�Brien"),
    ("author-latin1-encoding-header", {"author": LATIN1, "encoding": b"ISO-8859-1"}, {}, "René"),
    ("author-valid-accent", {"author": "René".encode()}, {}, "René"),
    ("author-cjk", {"author": "王小明".encode()}, {}, "王小明"),
    ("author-emoji", {"author": "Dev \U0001f680".encode()}, {}, "Dev \U0001f680"),
    ("author-utf8-bom", {"author": b"\xef\xbb\xbf" + "René".encode()}, {}, "﻿René"),
    ("committer-invalid-utf8", {"committer": LATIN1}, {}, "a"),
    ("subject-invalid-utf8", {"message": b"caf\xe9 fix\n\n\xff body\n"}, {}, "a"),
    ("valid-author-repo-commit-encoding-latin1", {"author": "José".encode()},
     {"i18n.commitEncoding": "ISO-8859-1"}, "José"),
    ("valid-author-log-output-encoding-latin1", {"author": "José".encode()},
     {"i18n.logOutputEncoding": "ISO-8859-1"}, "José"),
    ("valid-author-log-output-encoding-gbk", {"author": "王小明".encode()},
     {"i18n.logOutputEncoding": "gbk"}, "王小明"),
    ("cr-in-author", {"author": b"Bob\rX"}, {}, "Bob\rX"),
]


@pytest.mark.parametrize("given, config, named", [row[1:] for row in AUTHORS],
                         ids=[row[0] for row in AUTHORS])
def test_the_churn_window_names_an_author_the_way_the_commit_stored_it(tmp_path, given, config, named):
    root = repository(tmp_path)
    for key, value in config.items():
        git(root, "config", key, value)
    commit(root, **given)

    walked = list(churn_log.log_lines(root, 12))

    assert walked[0].startswith(f"\x01{named}\x02"), walked
    assert parse_git_log_lines(walked)["a.py"].commits == 1
    assert list(churn_log.log_lines(root, 12)) == walked  # the laid-down copy says the same


def test_an_author_outside_the_window_never_reaches_the_walk(tmp_path):
    root = repository(tmp_path)
    commit(root, author=LATIN1, age_days=3 * 365)

    assert list(churn_log.log_lines(root, 12)) == []


def test_a_cr_inside_an_author_name_is_one_author_on_every_commit(tmp_path):
    """Universal newlines split `Bob\rX` into two lines, so the header lost its
    dates and each commit weighed as undated."""
    root = repository(tmp_path)
    commit(root, author=b"Bob\rX", files={b"a.py": SOURCE}, age_days=30)
    commit(root, author=b"Bob\rX", files={b"a.py": SOURCE + b"# 2\n"})

    churn = parse_git_log_lines(churn_log.log_lines(root, 12))["a.py"]

    assert (churn.commits, churn.authors) == (2, 1)
    assert churn.weight < 2.0  # dated: the recency weight, not one per commit


@pytest.mark.parametrize("name, named", [
    (b"src/caf\xe9.py", "src/caf�.py"),
    ("src/café.py".encode(), "src/café.py"),
], ids=["path-invalid-utf8-deleted", "path-valid-accent-deleted"])
def test_a_file_added_and_deleted_inside_the_window_reads_as_a_path(tmp_path, name, named):
    root = repository(tmp_path)
    commit(root, files={name: SOURCE})
    commit(root, files={}, deletes=(name,))

    assert parse_git_log_lines(churn_log.log_lines(root, 12))[named].commits == 2


def test_the_range_walk_after_head_moves_reads_an_author_that_is_not_utf8(tmp_path):
    root = repository(tmp_path)
    base = commit(root)
    list(churn_log.log_lines(root, 12))  # the window laid down clean
    head = commit(root, author=LATIN1, files={b"a.py": SOURCE + b"# 2\n"})

    added = list(churn_log.commits_since(root, base, head))

    assert added[0].startswith(f"\x01{REPLACED}\x02"), added
    assert parse_git_log_lines(churn_log.log_lines(root, 12))["a.py"].commits == 2


# --- a span's history: `git log -L` prints subject, body and patch lines --------

HISTORY = [
    # id, the commit, the repo's own git config, the (subject, body) explain reads
    ("subject-invalid-utf8", {"message": LATIN1 + b" fix\n"}, {}, (f"{REPLACED} fix", "")),
    ("body-invalid-utf8", {"message": b"fix\n\n" + LATIN1 + b" body\n"}, {},
     ("fix", f"{REPLACED} body")),
    ("message-invalid-utf8", {"message": LATIN1 + b" fix\n\n" + LATIN1 + b" body\n"}, {},
     (f"{REPLACED} fix", f"{REPLACED} body")),
    ("patch-cp1252-source-line",
     {"files": {b"a.py": b"def f(x):\n    # " + LATIN1 + b"\n    return x\n"}}, {}, ("edit", "")),
    ("author-invalid-utf8", {"author": LATIN1}, {}, ("edit", "")),
    ("subject-latin1-encoding-header", {"message": b"caf\xe9 fix\n", "encoding": b"ISO-8859-1"},
     {}, ("café fix", "")),
    ("subject-valid-accent", {"message": "café fix\n".encode()}, {}, ("café fix", "")),
    ("subject-cjk-emoji", {"message": "café 修正 \U0001f680\n".encode()}, {},
     ("café 修正 \U0001f680", "")),
    ("subject-crlf", {"message": b"fix\r\n\r\nbody\r\n"}, {}, ("fix", "body")),
    ("patch-utf8-bom-source", {"files": {b"a.py": b"\xef\xbb\xbf" + SOURCE}}, {}, ("edit", "")),
    ("patch-nul-in-source", {"files": {b"a.py": b"def f(x):\n    return x  # \x00\n"}}, {},
     ("edit", "")),
    ("valid-subject-repo-commit-encoding-latin1", {"message": "café fix\n".encode()},
     {"i18n.commitEncoding": "ISO-8859-1"}, ("café fix", "")),
    ("valid-subject-log-output-encoding-latin1", {"message": "café fix\n".encode()},
     {"i18n.logOutputEncoding": "ISO-8859-1"}, ("café fix", "")),
]


@pytest.mark.parametrize("given, config, read", [row[1:] for row in HISTORY],
                         ids=[row[0] for row in HISTORY])
def test_a_span_history_reads_subject_body_and_patch_as_stored(tmp_path, given, config, read):
    root = repository(tmp_path)
    for key, value in config.items():
        git(root, "config", key, value)
    commit(root, **given)

    commits = _function_commits(root, "a.py", 1, 2)

    assert [(c["subject"], c["body"]) for c in commits] == [read]


# --- the marks file's own history: `git log -p --text -- crapkit-ratchet.tsv` ---

MARKS = "crapkit-ratchet.tsv"
STAMPED = b"# crapkit-analysis=11 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
MARK = b"src/app.py\tf( x )\t9.0\n"
PAST = [
    # id, the bytes one past revision of the marks file held
    ("history-cp1252-byte", STAMPED + b"# caf\xe9\n" + MARK),
    ("history-cp1252-fn-name", STAMPED + b"src/app.py\tcaf\xe9( n )\t9.0\n"),
    ("history-utf16-powershell-out-file",
     codecs.BOM_UTF16_LE + (STAMPED + MARK).decode().encode("utf-16-le")),
    ("history-utf8-bom", codecs.BOM_UTF8 + STAMPED + MARK),
    ("history-valid-accent-name", STAMPED + "src/app.py\tcafé( n )\t9.0\n".encode()),
    ("history-cjk-emoji-name", STAMPED + "src/日本.py\tf\U0001f680( n )\t9.0\n".encode()),
    ("history-crlf", (STAMPED + MARK).replace(b"\n", b"\r\n")),
    ("history-nul-byte", STAMPED + b"# \x00\n" + MARK),
]


@pytest.mark.parametrize("past", [row[1] for row in PAST], ids=[row[0] for row in PAST])
def test_every_past_revision_of_the_marks_file_reads_into_the_burn_down(tmp_path, past):
    """One revision saved by PowerShell 5.1 or a cp1252 editor stays in history
    after the file is fixed, and its patch lines are not UTF-8."""
    root = repository(tmp_path)
    commit(root, files={MARKS.encode(): past}, age_days=2)
    commit(root, files={MARKS.encode(): STAMPED + MARK})

    report = report_from_events(mark_events(file_log_patches(root, MARKS)))

    assert report["open"] == 1
    assert [row["long_name"] for row in report["oldest"]] == ["f( x )"]


CONFIG = b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _marked_repo(tmp_path, current: bytes):
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, MARKS.encode(): STAMPED + b"# caf\xe9\n" + MARK},
           age_days=2)
    commit(root, files={b"crapkit.toml": CONFIG, MARKS.encode(): current})
    git(root, "reset", "-q", "--hard")
    return root


def test_ratchet_report_reads_a_cp1252_past_revision(tmp_path, capsys):
    root = _marked_repo(tmp_path, STAMPED + MARK)

    assert main(["ratchet", "report", "--repo", str(root)]) == 0
    assert "1 open mark(s)" in capsys.readouterr().out


def test_ratchet_report_refuses_a_cp1252_marks_file_by_name(tmp_path, capsys):
    """The history read used to die first, so the refusal that names the byte
    and the fix never printed."""
    root = _marked_repo(tmp_path, STAMPED + b"src/app.py\tcaf\xe9( n )\t9.0\n")

    assert main(["ratchet", "report", "--repo", str(root)]) == 3
    assert "crapkit-ratchet.tsv is not UTF-8" in capsys.readouterr().err


# --- answers that name a file or a ref: `config`, `rev-parse`, stderr echoes ----

def test_a_hooks_path_that_is_not_utf8_reads_as_its_os_path(tmp_path, capsys):
    """A .git/config saved by an editor in a legacy code page. The value is a
    path, so it keeps the byte the way Python keeps one in an OS path, and
    doctor reads on."""
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/a.py": SOURCE})
    git(root, "reset", "-q", "--hard")
    config = root / ".git" / "config"
    config.write_bytes(config.read_bytes() + b"[core]\n\thooksPath = hooks-caf\xe9\n")

    assert config_value(root, "core.hooksPath") == "hooks-caf\udce9"
    assert main(["doctor", "--repo", str(root)]) in (0, 1)
    assert "Traceback" not in capsys.readouterr().err


def test_a_failing_git_that_echoes_bytes_that_are_not_utf8_raises_git_error(tmp_path):
    """git quotes the ref it could not resolve on stderr, byte for byte. A
    Windows argv cannot carry such a byte, so there the ref is a lone
    surrogate, which git receives as U+FFFD."""
    root = repository(tmp_path)
    commit(root)
    ref = "caf\ud800" if sys.platform == "win32" else os.fsdecode(b"caf\xe9")

    with pytest.raises(GitError, match="merge-base"):
        merge_base(root, ref)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="needs a POSIX directory name whose bytes are not UTF-8")
def test_a_checkout_under_a_directory_named_in_latin1_finds_its_top(tmp_path):
    root = repository(tmp_path / os.fsdecode(b"caf\xe9") / "repo")
    commit(root)

    assert worktree_root(root) == root.resolve()
