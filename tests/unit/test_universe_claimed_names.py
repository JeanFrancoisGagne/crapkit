"""universe.claimed_unreadable: the names a scope takes that crapkit cannot read.

A name git gives in bytes that are not UTF-8 is judged by scope claim. In the
0.8.1 tree scan_files raised on a claimed one from a private helper, so a gate
could only catch the refusal. claimed_unreadable returns them as a partition,
each with the scope that takes it, for a gate adapter to hand to the gate;
scan_files keeps its refusal, word for word, by calling it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from crapkit import gitpaths
from crapkit.config import load_config_text
from crapkit.errors import UnreadableNameError
from crapkit.universe import claimed_unreadable, scan_files

E2E = Path(__file__).resolve().parents[1] / "e2e" / "test_non_utf8_paths_e2e.py"

CONFIG = load_config_text("""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "web"
paths = ["src/web"]
languages = ["typescript"]
coverage_optional = true

[exclude]
globs = ["**/generated/**", "make_cov.py"]
""")

# The sentence the 0.8.1 tree refused a claimed name with, frozen here.
REFUSED_0_8_1 = ("{shown} is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit "
                 "reads every path as UTF-8; a file a scope takes is refused, not left out, so no gate "
                 "passes it unread: rename it (git mv) to a UTF-8 name")


def _listed(*raw: bytes) -> list[str]:
    """The names as git's listing hands them on: surrogateescape spellings."""
    return gitpaths.nul_paths(b"".join(name + b"\0" for name in raw))


def _shown(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def test_a_name_a_scope_takes_comes_back_with_its_scope():
    """The deepest declared path whose language reads the file takes it: web
    reads no Python, so src takes a .py under src/web."""
    files = _listed(b"src/app.py", b"src/web/caf\xe9.ts", b"src/caf\xe9.py", b"src/r\xe9/x.py", b"src/web/r\xe9.py")

    assert claimed_unreadable(files, CONFIG) == [
        ("src/caf\udce9.py", "src"), ("src/r\udce9/x.py", "src"), ("src/web/caf\udce9.ts", "web"),
        ("src/web/r\udce9.py", "src")]


@pytest.mark.parametrize("raw", [
    b"src/generated/caf\xe9.py", b"src/tests/caf\xe9.py", b"src/.cache/caf\xe9.py", b"make_cov\xe9.py",
    b"docs/caf\xe9.py", b"caf\xe9.log", b"src/caf\xe9.txt",
], ids=["exclude-glob", "test-directory", "dot-directory", "unscoped-root", "outside-every-scope", "repo-root",
        "no-scope-reads-txt"])
def test_an_excluded_unclaimed_or_unread_language_name_is_skipped(raw):
    assert claimed_unreadable(_listed(b"src/app.py", raw), CONFIG) == []


def test_a_readable_name_is_never_claimed_however_a_scope_takes_it():
    assert claimed_unreadable(["src/café.py", "src/日本.py", "src/web/a.ts"], CONFIG) == []


def test_a_name_git_lists_twice_comes_back_once():
    files = _listed(b"src/b\xe9.py", b"src/a\xe9.py", b"src/a\xe9.py")

    assert claimed_unreadable(files, CONFIG) == [("src/a\udce9.py", "src"), ("src/b\udce9.py", "src")]


def test_a_name_no_scope_takes_stays_in_the_scans_left_out_partition():
    universe = scan_files(_listed(b"src/app.py", b"docs/caf\xe9.md"), CONFIG)

    assert (universe.by_scope["src"], universe.unreadable) == (["src/app.py"], ("docs/caf\udce9.md",))


def _e2e_rows(name: str) -> list[tuple[str, bytes]]:
    """A list of (id, name) rows test_non_utf8_paths_e2e.py holds, read off its
    source so the two files cannot drift: STAGED_CLAIMED is CLAIMED plus rows."""
    tree = ast.parse(E2E.read_text(encoding="utf-8"))
    values = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
              for target in node.targets if isinstance(target, ast.Name)}
    value = values[name]
    if isinstance(value, ast.BinOp):
        return _e2e_rows(value.left.id) + ast.literal_eval(value.right)
    return ast.literal_eval(value)


CLAIMED_ROWS = [row for name in ("CLAIMED", "STAGED_CLAIMED") for row in _e2e_rows(name)]


@pytest.mark.parametrize("raw", [raw for _, raw in CLAIMED_ROWS], ids=[row_id for row_id, _ in CLAIMED_ROWS])
def test_scan_files_refuses_each_claimed_e2e_row_in_the_0_8_1_sentence(raw):
    with pytest.raises(UnreadableNameError) as refused:
        scan_files(_listed(b"src/app.py", raw), CONFIG)

    assert str(refused.value) == REFUSED_0_8_1.format(shown=_shown(raw))
    assert (refused.value.exit_code, refused.value.names) == (3, (raw.decode("utf-8", "surrogateescape"),))


def test_the_e2e_lists_hold_the_rows_the_ticket_names():
    assert [row_id for row_id, _ in CLAIMED_ROWS] == [
        "unquoted-latin1", "git-quotes-it", "cp1252-smart-quote",
        "unquoted-latin1", "git-quotes-it", "cp1252-smart-quote", "latin1-and-quotes"]


def test_two_claimed_names_are_one_sentence_naming_the_first_and_both_in_names():
    with pytest.raises(UnreadableNameError) as refused:
        scan_files(_listed(b"src/o\x92brien.py", b"src/caf\xe9.py"), CONFIG)

    assert str(refused.value).startswith("src/caf\\xe9.py (and 1 more) is in scope 'src', but git names it")
    assert refused.value.names == ("src/caf\udce9.py", "src/o\udc92brien.py")
