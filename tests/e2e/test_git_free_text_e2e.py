"""git's free text, fed through every command that reads it, in the bytes a
real history can hold.

git prints an author name, a subject, a body or a patch line as the commit
stored it unless the commit names its encoding, and it re-encodes what a commit
stored as UTF-8 when the repo sets i18n.commitEncoding or
i18n.logOutputEncoding. Each read here used to decode that text as strict
UTF-8, so one such commit ended the command with a UnicodeDecodeError, or with
an AttributeError on Windows where the decode fails in subprocess's reader
thread. gitio now pins i18n.logOutputEncoding=UTF-8 and reads free text through
repotext.lenient: a byte a commit stored that is not UTF-8 reads as U+FFFD,
and a name stored as UTF-8 comes back as stored.

One parametrized test per site, each row a variation the hunt ran (the red
ones and their green controls), driven through the CLI or the MCP server. The
rows no input reaches are listed at the bottom with the reason.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner
from foreign_bytes import (APP, INITIALIZE, LINUX, answered, commit, git, mcp_session, repository,
                           rpc, scored_repo, tool_text)
from name_bytes import NOT_UTF8_NAMES

run_cli = cli_runner(encoding="utf-8", errors="replace")

EDIT = APP.replace(b"return 2\n", b"return 2  # edit\n")
THREE_YEARS = 3 * 365
U_FFFD = chr(0xFFFD)  # what a byte that is not UTF-8 reads as


def _configure(repo: Path, config: dict[str, str]) -> None:
    for key, value in config.items():
        git(repo, "config", key, value)


def _json(res) -> dict:
    answered(res)
    return json.loads(res.stdout)


# --- the churn window: gitio._git_lines under churn_log's walk ---------------
#
# worklist, next-item, brief, coupling and the MCP list_worklist tool all walk
# the 12-month window, and the window's
# `git log --format=%x01%an... --name-only` prints each author name and each
# path as stored. Every row commits src/app.py twice, once as `base` and once
# with the row's bytes, then reads the window through each command.

def _churn_row(row_id, *, config=None, commits=(), base_files=None, base_age=0, churn=(2, 2), mcp=False,
               marks=()):
    return pytest.param(config or {}, list(commits), base_files or {}, base_age, churn, mcp, id=row_id,
                        marks=marks)


CHURN_ROWS = [
    _churn_row("author-invalid-utf8", commits=[dict(author=b"Ren\xe9")], mcp=True),
    _churn_row("author-cp1252-smart-quote", commits=[dict(author=b"O\x92Brien")]),
    _churn_row("path-invalid-utf8-deleted", base_files={b"src/caf\xe9.py": APP},
               commits=[dict(author=b"Ren", deletes=(b"src/caf\xe9.py",))], marks=NOT_UTF8_NAMES),
    _churn_row("tracked-unscored-invalid-path", commits=[dict(author=b"Ren", files={b"docs/caf\xe9.txt": b"x\n"})],
               marks=NOT_UTF8_NAMES),
    # A name stored as UTF-8 that git re-encoded on the way out: two names
    # that differ only where Latin-1 or GBK re-encodes them. Read as U+FFFD
    # they collapse into one author; pinned to UTF-8 they stay two.
    _churn_row("utf8-author-under-log-output-encoding-latin1", config={"i18n.logOutputEncoding": "ISO-8859-1"},
               commits=[dict(author="José".encode()), dict(author="Josè".encode())], churn=(3, 3), mcp=True),
    _churn_row("utf8-author-under-commit-encoding-latin1", config={"i18n.commitEncoding": "ISO-8859-1"},
               commits=[dict(author="José".encode()), dict(author="Josè".encode())], churn=(3, 3)),
    _churn_row("utf8-author-under-log-output-encoding-gbk", config={"i18n.logOutputEncoding": "gbk"},
               commits=[dict(author="王小明".encode()), dict(author="王小红".encode())], churn=(3, 3), mcp=True),
    _churn_row("author-latin1-encoding-header", commits=[dict(author=b"Ren\xe9", encoding=b"ISO-8859-1")]),
    _churn_row("author-valid-accent", commits=[dict(author="René".encode())]),
    _churn_row("author-cjk", commits=[dict(author="渡辺".encode())]),
    _churn_row("author-emoji", commits=[dict(author="dev \U0001f680".encode())]),
    _churn_row("author-utf8-bom", commits=[dict(author=b"\xef\xbb\xbfRene")]),
    _churn_row("committer-invalid-utf8", commits=[dict(committer=b"Ren\xe9")]),
    _churn_row("subject-invalid-utf8", commits=[dict(message=b"caf\xe9 fix", author=b"Ren")]),
    # The window ends at HEAD's commit date: today's commit leaves the two
    # three years back outside it, so only HEAD counts.
    _churn_row("author-invalid-outside-window", base_age=THREE_YEARS,
               commits=[dict(author=b"Ren\xe9", age_days=THREE_YEARS), dict(author=b"Ren")], churn=(1, 1)),
    _churn_row("path-valid-accent-deleted", base_files={"src/café.py".encode(): APP},
               commits=[dict(author=b"Ren", deletes=("src/café.py".encode(),))]),
]


def _churn_repo(root: Path, config, commits, base_files, base_age) -> Path:
    repo = scored_repo(root, author=b"base", age_days=base_age)
    if base_files:
        commit(repo, base_files, author=b"base", age_days=base_age)
    _configure(repo, config)
    for number, fields in enumerate(commits):
        files = fields.pop("files", {})
        commit(repo, {b"src/app.py": APP + b"# edit %d\n" % number, **files}, **fields)
    return repo


def _window_readers(repo: Path) -> dict:
    """What each churn reader answers, the store measured first."""
    answered(run_cli(repo, "coverage"))
    worklist = _json(run_cli(repo, "worklist", "--json"))
    _json(run_cli(repo, "next-item"))
    coupling = _json(run_cli(repo, "coupling", "--json"))
    brief = _json(run_cli(repo, "brief", "src/app.py", "pick", "--json"))
    return {"worklist": worklist, "coupling": coupling, "churn": brief["churn"]}


@pytest.mark.parametrize("config, commits, base_files, base_age, churn, mcp", CHURN_ROWS)
def test_every_churn_reader_reads_the_window_whatever_bytes_it_holds(
        tmp_path, config, commits, base_files, base_age, churn, mcp):
    repo = _churn_repo(tmp_path / "repo", config, commits, base_files, base_age)

    read = _window_readers(repo)

    got = None if read["churn"] is None else (read["churn"]["commits"], read["churn"]["authors"])
    assert got == churn, read["churn"]
    if mcp:
        replies, _ = mcp_session(repo, [INITIALIZE, rpc(2, "tools/call", {"name": "list_worklist",
                                                                          "arguments": {}})], {1, 2})
        assert replies[2]["result"]["isError"] is False, tool_text(replies[2])
        assert json.loads(tool_text(replies[2]))["active"] == read["worklist"]["active"]


@NOT_UTF8_NAMES
def test_a_tracked_name_that_is_not_utf8_is_left_out_of_the_window_and_named(tmp_path):
    repo = _churn_repo(tmp_path / "repo", {}, [dict(author=b"Ren", files={b"docs/caf\xe9.txt": b"x\n"})], {}, 0)
    # A process names each such file once, so this run gets a process of its own.
    measured = run_cli(repo, "coverage", spawn=True)

    answered(measured)
    assert "left out docs/caf\\xe9.txt" in measured.stderr
    answered(run_cli(repo, "worklist", "--json"))


@pytest.mark.parametrize("author", [b"Ren\xe9", b"O\x92Brien", "René".encode()],
                         ids=["invalid-utf8", "cp1252-smart-quote", "valid-accent-control"])
def test_the_range_walk_after_head_moves_reads_a_new_author(tmp_path, author):
    """The window laid down clean, then a commit whose author is the row's
    bytes: the warm read walks only the new range."""
    repo = scored_repo(tmp_path / "repo", author=b"base")
    answered(run_cli(repo, "coverage"))
    assert _json(run_cli(repo, "brief", "src/app.py", "pick", "--json"))["churn"]["commits"] == 1
    commit(repo, {b"src/app.py": EDIT}, author=author)

    churn = _json(run_cli(repo, "brief", "src/app.py", "pick", "--json"))["churn"]

    assert (churn["commits"], churn["authors"]) == (2, 2)


def _pinned_repo(root: Path, author: bytes) -> dict:
    """base and `author` each commit src/app.py at the same two instants."""
    repo = scored_repo(root, author=b"base", age_days=40)
    commit(repo, {b"src/app.py": EDIT}, author=author, age_days=10)
    answered(run_cli(repo, "coverage"))
    return _json(run_cli(repo, "brief", "src/app.py", "pick", "--json"))["churn"]


def test_a_cr_inside_an_author_name_keeps_the_commit_whole(tmp_path):
    """Universal newlines ended a line at the CR in b'Bob\\rX', cutting the
    header off its dates, and the commit's weight doubled with nothing said."""
    with_cr = _pinned_repo(tmp_path / "cr", b"Bob\rX")
    control = _pinned_repo(tmp_path / "plain", b"BobX")

    assert with_cr == control


# --- one function's history: explain --history and get_function_history -----
#
# `git log -L` prints each commit's subject and body and the span's patch
# lines as stored. A cp1252 source line is text analyze.decode_source
# admits on purpose.

CP1252_APP = APP.replace(b"    return 0\n", b"    return 0  # caf\xe9\n")


def _history_row(row_id, message, *, source=APP, config=None, author=b"Plain", encoding=None, subject=None):
    fields = dict(message=message, author=author, encoding=encoding)
    return pytest.param(fields, source, config or {}, subject, id=row_id)


HISTORY_ROWS = [
    _history_row("subject-invalid-utf8", b"caf\xe9 subject\n", subject="caf" + U_FFFD + " subject"),
    _history_row("body-invalid-utf8", b"plain subject\n\nbody holds \xff\xfe bytes\n", subject="plain subject"),
    _history_row("patch-cp1252-source-line", b"plain subject\n", source=CP1252_APP, subject="plain subject"),
    _history_row("utf8-subject-under-log-output-encoding-latin1", "café fix\n".encode(),
                 config={"i18n.logOutputEncoding": "ISO-8859-1"}, subject="café fix"),
    _history_row("utf8-subject-under-commit-encoding-latin1", "café fix\n".encode(),
                 config={"i18n.commitEncoding": "ISO-8859-1"}, subject="café fix"),
    _history_row("author-invalid-utf8", b"plain subject\n", author=b"Ren\xe9", subject="plain subject"),
    _history_row("subject-latin1-encoding-header", b"caf\xe9 subject\n", encoding=b"ISO-8859-1",
                 subject="café subject"),
    _history_row("subject-valid-accent", "café subject\n".encode(), subject="café subject"),
    _history_row("subject-cjk-emoji", "李雷 \U0001f600 subject\n".encode(), subject="李雷 \U0001f600 subject"),
    _history_row("subject-crlf", b"crlf subject\r\n\r\nbody\r\n", subject="crlf subject"),
    _history_row("patch-utf8-bom-source", b"plain subject\n", source=b"\xef\xbb\xbf" + APP, subject="plain subject"),
    _history_row("patch-nul-in-source", b"plain subject\n", source=APP + b"# \x00\n", subject="plain subject"),
]


def _history_repo(root: Path, fields: dict, source: bytes = APP, config: dict | None = None) -> Path:
    """Two commits of src/app.py, the second with the row's fields; an
    inventory run holds the function, which is all explain needs."""
    repo = scored_repo(root, source, message=b"init\n")
    _configure(repo, config or {})
    commit(repo, {b"src/app.py": source.replace(b"return 2\n", b"return 2  # edit\n")}, **fields)
    answered(run_cli(repo, "inventory"))
    return repo


@pytest.mark.parametrize("fields, source, config, subject", HISTORY_ROWS)
def test_explain_history_lists_every_commit_whatever_its_text_holds(tmp_path, fields, source, config, subject):
    repo = _history_repo(tmp_path / "repo", fields, source, config)

    payload = _json(run_cli(repo, "explain", "src/app.py", "pick", "--history", "--json"))

    commits = payload["functions"][0]["commits"]
    assert [c["subject"] for c in commits] == [subject, "init"], commits


def test_get_function_history_over_the_mcp_server_reads_a_latin1_subject(tmp_path):
    repo = _history_repo(tmp_path / "repo", dict(message=b"caf\xe9 subject\n"))

    replies, _ = mcp_session(repo, [INITIALIZE, rpc(2, "tools/call", {
        "name": "get_function_history",
        "arguments": {"path": "src/app.py", "name": "pick", "history": True}})], {1, 2})

    assert replies[2]["result"]["isError"] is False, tool_text(replies[2])
    commits = json.loads(tool_text(replies[2]))["functions"][0]["commits"]
    assert [c["subject"] for c in commits] == ["caf" + U_FFFD + " subject", "init"]


# --- the mutation pool's git output: procs._captured_text --------------------
#
# `git worktree add` and a kept tree's `checkout --force` print `HEAD is now at
# <sha> <subject>`, and `clean -xdff` prints `Removing <name>`; the owned run
# captured both as strict UTF-8, so mutate stopped before any mutant ran.

MUT_APP = b"def f(x):\n    return x > 0\n"
MUT_TEST = b"import sys\nsys.path.insert(0, 'src')\nimport app\nassert app.f(1) and not app.f(0)\n"


def _mutation_repo(root: Path, subject: bytes, *, workers: int, encoding=None, config=None) -> Path:
    toml = (b'[crapkit]\ntarget = 6\nmutation_command = "python t.py"\nmutation_workers = %d\n\n'
            b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n' % workers)
    repository(root)
    _configure(root, config or {})
    commit(root, {b"src/app.py": MUT_APP, b"t.py": MUT_TEST, b"crapkit.toml": toml,
                  b".gitignore": b".crapkit/\n"}, message=subject, encoding=encoding)
    return root


def _mutated(repo: Path) -> dict:
    out = _json(run_cli(repo, "mutate", "--files", "src/app.py", "--json"))
    assert (repo / "src" / "app.py").read_bytes() == MUT_APP
    return out


MUTATE_ROWS = [
    pytest.param(b"caf\xe9 setup\n", 2, None, {}, id="fresh-pool-subject-invalid-utf8-workers2"),
    pytest.param(b"caf\xe9 setup\n", 1, None, {}, id="subject-invalid-utf8-workers1"),
    pytest.param("café fix\n".encode(), 2, None, {"i18n.commitEncoding": "ISO-8859-1"},
                 id="utf8-subject-under-commit-encoding-latin1"),
    pytest.param("café fix\n".encode(), 2, None, {"i18n.logOutputEncoding": "ISO-8859-1"},
                 id="utf8-subject-under-log-output-encoding-latin1"),
    pytest.param(b"caf\xe9 setup\n", 2, b"ISO-8859-1", {}, id="subject-latin1-encoding-header"),
    pytest.param("café setup\n".encode(), 2, None, {}, id="subject-valid-accent"),
    pytest.param("修正 \U0001f680\n".encode(), 2, None, {}, id="subject-cjk-emoji"),
]


@pytest.mark.parametrize("subject, workers, encoding, config", MUTATE_ROWS)
def test_mutate_builds_its_pool_at_a_head_whatever_its_subject_holds(tmp_path, subject, workers,
                                                                      encoding, config):
    repo = _mutation_repo(tmp_path / "repo", subject, workers=workers, encoding=encoding, config=config)

    out = _mutated(repo)

    assert (out["mutants"], out["killed"]) == (2, 2)


def _kept_pool(root: Path) -> Path:
    repo = _mutation_repo(root, b"ascii setup\n", workers=2)
    assert _mutated(repo)["killed"] == 2
    assert sorted(p.name for p in (repo / ".crapkit" / "mutate-pool").glob("w*")) == ["w0", "w1"]
    return repo


def test_a_kept_pool_resets_to_a_head_whose_subject_is_not_utf8(tmp_path):
    repo = _kept_pool(tmp_path / "repo")
    commit(repo, {b"t.py": MUT_TEST + b"# again\n"}, message=b"caf\xe9 again\n")

    out = _mutated(repo)

    assert (out["mutants"], out["killed"]) == (2, 2)


@pytest.mark.skipif(not LINUX, reason="a file name that is not UTF-8 exists only on a POSIX filesystem")
def test_a_kept_pool_cleans_a_file_whose_name_is_not_utf8(tmp_path):
    """`clean -xdff` names the leftover it removes, on Linux."""
    repo = _kept_pool(tmp_path / "repo")
    for tree in (repo / ".crapkit" / "mutate-pool").glob("w*"):
        (tree / os.fsdecode(b"caf\xe9.o")).write_bytes(b"x")

    assert _mutated(repo)["killed"] == 2


# --- a git config value: gitio.config_value under doctor ----------------------
#
# doctor reads core.hooksPath and `rev-parse --git-path hooks/pre-commit`; a
# .git/config written in a legacy code page hands both back in bytes that
# are not UTF-8.

CONFIG_ROWS = [
    pytest.param(b"[core]\n\thooksPath = hooks-caf\xe9\n", id="hookspath-invalid-utf8"),
    pytest.param("[core]\n\thooksPath = hooks-café\n".encode(), id="hookspath-valid-accent"),
    pytest.param("[core]\n\thooksPath = hooks-渡辺\n".encode(), id="hookspath-cjk"),
    pytest.param(b"[core]\n\thooksPath = hooks-ascii\n", id="hookspath-ascii"),
    pytest.param(b"[user]\n\tname = Ren\xe9\n", id="user-name-invalid-utf8-unread"),
]


@pytest.mark.parametrize("config", CONFIG_ROWS)
def test_doctor_reads_a_git_config_value_in_any_bytes(tmp_path, config):
    control = scored_repo(tmp_path / "control")
    repo = scored_repo(tmp_path / "repo")
    with open(repo / ".git" / "config", "ab") as handle:
        handle.write(config)

    res, expected = run_cli(repo, "doctor"), run_cli(control, "doctor")

    answered(res, expected.returncode)


# --- rows no input reaches ------------------------------------------------------
#
# An LF or a NUL inside an author name: an LF ends the
# author line of a commit object and git stops a name at a NUL, so no walk
# reads either. A CR does reach the walk, in
# test_a_cr_inside_an_author_name_keeps_the_commit_whole.
# On win32, a leftover pool file named in Latin-1: NTFS
# stores every name as UTF-16, so `clean` names each file in UTF-8 there.
# The unowned worktree commands: mutate
# passes the measurement owner to every pool command, and the unowned path
# reads git's answer through gitio._spawn, which never raises on a byte.
