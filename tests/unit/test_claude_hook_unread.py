"""An edited file no reader could read draws the advisory, naming the file and why.

A file lizard or a crapkit reader refuses scores as zero functions, so the run
goes on. The advisory read those zero records as zero breaches and exited 0,
while a ccn-8 function sat in the same file as the construct the reader
refused. That is one of the gate sites signal-1 missed: the edit is judged
nowhere, so the hook says so with exit 2. An unreadable file the edit left
identical to HEAD stays silent, as it stays unjudged at the commit gate.
"""
from __future__ import annotations

import pytest

from crapkit.cli import claude_hook
from claude_hook_repo import (BREACH, CLEAN, age, bash_event, edit_event, git, hook,
                              measured, write)

# The same function with its parameter list left open: the Python reader
# reaches no body for it, and the file is not scored.
CUT_OFF = BREACH.replace("def sprawl(n):", "def sprawl(n:")

TS_TOML = ('[crapkit]\ntarget = 6\n\n'
           '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
           'coverage_optional = true\n')
# signal-1's probe: one arrow the TypeScript reader refuses beside a ccn-8 function.
TS_PROBE = ("export const actions = {\n"
            "  supportsAction: ({ action }) => new Set<string>([action]).has(action),\n"
            "};\n"
            "export function big(a, b, c, d, e, f, g) {\n"
            + "".join(f"  if ({v}) return {i};\n" for i, v in enumerate("abcdefg", 1))
            + "  return 0;\n}\n")


def _unread_head(rel: str) -> str:
    return (f"crapkit advisory: could not read {rel}, so no function in it was judged "
            "(the edit landed; nothing was blocked)")


def test_a_python_def_cut_off_at_its_signature_is_named(tmp_path, monkeypatch, capsys):
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CUT_OFF)

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[0]) == (2, _unread_head("calc/grade.py"))
    assert err[1].startswith("  calc/grade.py: the Python reader reached no body"), err
    assert err[2] == claude_hook._UNREAD_NEXT


def test_signal_one_probe_a_refused_arrow_beside_a_breach_is_named(tmp_path, monkeypatch,
                                                                    capsys):
    """claude-hook exited 0 on this file while hook-precommit's fix refused it."""
    repo = measured(tmp_path, toml=TS_TOML)
    path = write(repo, "src/a.ts", TS_PROBE)

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[0]) == (2, _unread_head("src/a.ts"))
    assert "src/a.ts:2" in err[1] and "wrap that arrow body in parentheses" in err[1], err


@pytest.mark.parametrize("commit", [True, False], ids=["committed-repo", "unborn-staged"])
def test_an_untracked_or_new_unreadable_file_is_named(commit, tmp_path, monkeypatch, capsys):
    repo = measured(tmp_path, commit=commit)
    path = write(repo, "calc/new.py", CUT_OFF)
    if not commit:
        git(repo, "add", "-A")

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[0]) == (2, _unread_head("calc/new.py"))


def test_an_unreadable_file_the_edit_left_as_head_has_it_is_silent(tmp_path, monkeypatch,
                                                                   capsys):
    """The commit gate never judges a file the change did not touch, readable
    or not, so the advisory does not refuse one either."""
    repo = measured(tmp_path)
    write(repo, "calc/grade.py", CUT_OFF)
    git(repo, "commit", "-qam", "legacy that no reader reads")

    assert hook(monkeypatch, capsys, edit_event(repo / "calc" / "grade.py")) == (0, [])


def test_a_path_the_operating_system_refuses_to_read_is_named(tmp_path, monkeypatch, capsys):
    """A directory spelled like a source file: the read fails with an OS error,
    and the reason says which."""
    repo = measured(tmp_path)
    (repo / "calc" / "odd.py").mkdir()

    code, err = hook(monkeypatch, capsys, edit_event(repo / "calc" / "odd.py"))

    assert (code, err[0]) == (2, _unread_head("calc/odd.py"))
    assert err[1].startswith("  calc/odd.py: "), err


def test_a_bash_write_of_an_unreadable_file_is_named_once(tmp_path, monkeypatch, capsys):
    """The Bash fallback takes the same ladder, and its session memory keeps a
    later touch from naming the same bytes again."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CUT_OFF)

    first = hook(monkeypatch, capsys, bash_event(repo))
    age(path, 60)
    path.touch()
    again = hook(monkeypatch, capsys, bash_event(repo, "touch calc/grade.py"))

    assert (first[0], first[1][0], again) == (2, _unread_head("calc/grade.py"), (0, []))


def test_a_readable_file_still_draws_the_breach_advisory(tmp_path, monkeypatch, capsys):
    """Control: the refusal is for files no reader read, nothing else."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[1]) == (2, "  ccn 8  calc/grade.py:7  sprawl( n )")
