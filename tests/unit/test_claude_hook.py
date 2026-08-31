"""The advisory hook's ladder, rung by rung.

The goldens in tests/e2e drive the whole subcommand through a real spawn; these
pin the individual decisions, including the ones a golden can only observe as
silence. Every rung that fails to advance exits 0 and says nothing, so without
these a broken rung and a working one look identical from outside.
"""
import argparse
import io
import os
import time
from pathlib import Path

import pytest

from crapkit.cli import build_parser, main
from crapkit.cli import claude_hook
from crapkit.cli.claude_hook import (_advise, _advisory_lines, _breaches, _command_event,
                                     _edited_file, _edited_path, _fresh, _fresh_python,
                                     _judgeable, _marks_for, _repo_root, _sequencing,
                                     _status_records, cmd_claude_hook)
from crapkit.merge import FunctionRecord

EVENT = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
         "tool_input": {"file_path": "/repo/calc/grade.py"}}


def record(name: str, ccn: int, start: int = 1, end: int = 16) -> FunctionRecord:
    return FunctionRecord("calc/grade.py", name, start, end, ccn, ccn, ccn, 9, 1, 1)


# --- rung 1: the payload -----------------------------------------------------

def test_a_post_tool_use_edit_names_the_file_it_touched():
    assert _edited_file(EVENT) == "/repo/calc/grade.py"


@pytest.mark.parametrize("payload", [
    {},
    {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/repo/a.py"}},
    {"hook_event_name": "Stop", "tool_input": {"file_path": "/repo/a.py"}},
    {"hook_event_name": "PostToolUse", "tool_input": {"notebook_path": "/repo/a.ipynb"}},
    {"hook_event_name": "PostToolUse", "tool_input": None},
    {"hook_event_name": "PostToolUse"},
    {"hook_event_name": "PostToolUse", "tool_input": {"file_path": 17}},
])
def test_an_event_protocol_one_does_not_judge_names_no_file(payload: dict):
    assert _edited_file(payload) == ""


def test_a_relative_file_path_is_read_against_the_events_own_cwd():
    """The only base the payload offers. ${CLAUDE_PROJECT_DIR} is not consulted
    anywhere in this module: it stays at the session root while cwd follows a
    worktree, which resolves a worktree edit to the mainline store."""
    assert _edited_path({"cwd": "/repo"}, "calc/grade.py") == Path("/repo/calc/grade.py")


def test_an_absolute_file_path_is_taken_as_given():
    assert _edited_path({"cwd": "/elsewhere"}, "/repo/calc/grade.py") == Path("/repo/calc/grade.py")


# --- rung 1b: the Bash fallback ----------------------------------------------
#
# A Bash PostToolUse carries `tool_input.command` and never `file_path`, so the
# single-file ladder has nothing to climb. The fallback judges the *.py files
# the working tree changed, scoped by mtime to the ones this command plausibly
# wrote, and capped so the hook's own timeout holds.

BASH_EVENT = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": "/repo",
              "tool_input": {"command": "python - <<'PY'\nPY"}}


def test_a_bash_post_tool_use_is_a_command_event():
    assert _command_event(BASH_EVENT) is True


@pytest.mark.parametrize("payload", [
    {},
    {"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}},
    {"hook_event_name": "Stop", "tool_input": {"command": "ls"}},
    {"hook_event_name": "PostToolUse", "tool_input": {"file_path": "/repo/a.py"}},
    {"hook_event_name": "PostToolUse", "tool_input": {"command": 17}},
    {"hook_event_name": "PostToolUse", "tool_input": None},
    {"hook_event_name": "PostToolUse"},
])
def test_an_event_carrying_no_command_string_takes_no_fallback(payload: dict):
    """NotebookEdit and friends stay out of the fallback the same shape-based way
    they stay out of the single-file path: no `command`, nothing to answer for."""
    assert _command_event(payload) is False


def test_status_records_read_one_status_and_one_path_per_record():
    text = " M calc/grade.py\0?? calc/new.py\0"

    assert list(_status_records(text)) == [(" M", "calc/grade.py"),
                                           ("??", "calc/new.py")]


def test_a_rename_records_second_field_is_consumed_not_reread():
    """`--porcelain -z` gives a rename two fields: the new name, then the old.
    Reading one per record would take the old name as the next record's status
    and shift every entry after it."""
    text = "R  calc/new.py\0calc/old.py\0 M calc/grade.py\0"

    assert list(_status_records(text)) == [("R ", "calc/new.py"),
                                           (" M", "calc/grade.py")]


@pytest.mark.parametrize("status,rel,verdict", [
    (" M", "calc/grade.py", True),
    ("??", "calc/new.py", True),
    ("R ", "calc/moved.py", True),
    (" M", "notes.md", False),
    (" D", "calc/gone.py", False),
    ("D ", "calc/gone.py", False),
])
def test_only_a_python_file_still_on_disk_is_judgeable(status, rel, verdict):
    assert _judgeable(status, rel) is verdict


def test_a_just_written_file_is_fresh(tmp_path):
    path = tmp_path / "hot.py"
    path.write_text("def f():\n    pass\n", encoding="utf-8")

    assert _fresh(path, time.time() - 12) is True


def test_a_file_dirty_since_before_the_command_is_not_fresh(tmp_path):
    """The `ls` after an edit: the file is still dirty, but this command did not
    write it, so re-advising it would repeat the advisory on every Bash call."""
    path = tmp_path / "hot.py"
    path.write_text("def f():\n    pass\n", encoding="utf-8")
    stale = time.time() - 3600
    os.utime(path, (stale, stale))

    assert _fresh(path, time.time() - 12) is False


def test_a_path_status_names_but_disk_lacks_is_not_fresh(tmp_path):
    assert _fresh(tmp_path / "gone.py", time.time() - 12) is False


def test_a_command_run_outside_any_git_repo_is_silent(tmp_path):
    """No top to walk from means nothing to judge — the unmeasured-machine case
    the module's silence contract is built around."""
    payload = dict(BASH_EVENT, cwd=str(tmp_path))

    assert claude_hook._advise_command(payload) == 0


def test_a_cwd_that_is_not_a_directory_names_no_top(tmp_path):
    """The event's cwd can be gone by the time the async hook runs (a command
    that removed its own directory). Silence, not a spawn error."""
    assert claude_hook._repo_top(tmp_path / "gone") is None


def test_a_non_command_event_takes_no_fallback_judgement():
    assert claude_hook._advise_command({"hook_event_name": "PostToolUse",
                                        "tool_input": {}}) == 0


def test_the_fallback_caps_the_files_it_judges(monkeypatch, tmp_path):
    """PostToolUse waits this process out, so a huge dirty tree is a stall, not
    a license to judge everything."""
    text = "".join(f"?? f{i}.py\0" for i in range(40))
    monkeypatch.setattr(claude_hook, "_porcelain", lambda top: text)
    monkeypatch.setattr(claude_hook, "_fresh", lambda path, cutoff: True)

    assert len(_fresh_python(tmp_path)) == 25


# --- rungs 2 and 3: the root -------------------------------------------------

def test_the_root_is_the_first_directory_above_the_edit_holding_a_toml(tmp_path):
    (tmp_path / "crapkit.toml").write_text("", encoding="utf-8")
    (tmp_path / "calc").mkdir()

    assert _repo_root(tmp_path / "calc") == tmp_path


def test_a_git_directory_with_no_toml_stops_the_walk(tmp_path):
    """The repo is not measured. 12 of the 14 repos on the machine this was
    measured against, and every non-git directory besides."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "crapkit.toml").write_text("", encoding="utf-8")

    assert _repo_root(tmp_path / "repo") is None


def test_a_git_file_stops_the_walk_the_same_way(tmp_path):
    """A linked worktree carries `.git` as a file. Walking past it would lend the
    worktree its parent checkout's config and its parent's store, with the edited
    file untracked from that root."""
    (tmp_path / "crapkit.toml").write_text("", encoding="utf-8")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert _repo_root(tmp_path / "wt") is None


def test_a_toml_beside_a_git_entry_still_wins(tmp_path):
    """Every measured repo is this shape: the toml and `.git` sit in one
    directory, and the toml is tested first."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "crapkit.toml").write_text("", encoding="utf-8")

    assert _repo_root(tmp_path) == tmp_path


def test_a_path_under_no_repo_at_all_resolves_to_nothing(tmp_path):
    (tmp_path / "loose").mkdir()

    assert _repo_root(tmp_path / "loose") is None


# --- rung 4: the sequencing guard -------------------------------------------

@pytest.mark.parametrize("marker", ["rebase-merge", "rebase-apply", "MERGE_HEAD",
                                    "CHERRY_PICK_HEAD"])
def test_every_sequencing_marker_silences_the_hook(marker: str, tmp_path):
    """lizard parses live conflict markers as two coexisting copies of every
    function, and the changed-range rule inverts against the rebase's temporary
    HEAD: the same function draws opposite verdicts by rebase direction."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / marker).write_text("", encoding="utf-8")

    assert _sequencing(tmp_path) is True


def test_a_settled_checkout_carries_no_marker(tmp_path):
    (tmp_path / ".git").mkdir()

    assert _sequencing(tmp_path) is False


# --- rung 8: the verdict -----------------------------------------------------

def test_only_a_function_the_edit_touched_and_over_the_ceiling_breaches():
    records = [record("sprawl( n )", 8), record("calm( n )", 3, start=20, end=24)]

    assert [r.long_name for r in _breaches(records, [(1, 3)], 6)] == ["sprawl( n )"]


def test_an_over_ceiling_function_the_edit_missed_is_not_a_breach():
    assert _breaches([record("sprawl( n )", 8)], [(40, 41)], 6) == []


def test_an_untracked_file_is_judged_in_full():
    """git diff cannot see a file it never recorded, so `None` ranges are what
    keeps an empty diff from passing every function in it."""
    records = [record("sprawl( n )", 8), record("wide( n )", 7, start=20, end=30)]

    assert [r.long_name for r in _breaches(records, None, 6)] == ["sprawl( n )", "wide( n )"]


def test_breaches_come_out_worst_first():
    records = [record("mild( n )", 7), record("sprawl( n )", 9, start=20, end=30)]

    assert [r.ccn for r in _breaches(records, None, 6)] == [9, 7]


# --- rung 8: the ratchet exemption ------------------------------------------

RATCHET = ("# crapkit-analysis=5 lizard=1.24.0\n"
           "path\tlong_name\tcrap\n"
           "calc/grade.py\tsprawl( n )\t72.0000\n"
           "calc/other.py\twide( n )\t51.0000\n")


def test_a_mark_is_found_by_path_and_function_name(tmp_path):
    marks = tmp_path / "crapkit-ratchet.tsv"
    marks.write_text(RATCHET, encoding="utf-8")

    assert _marks_for(marks, "calc/grade.py") == {"sprawl( n )"}


def test_another_files_mark_never_covers_this_one(tmp_path):
    marks = tmp_path / "crapkit-ratchet.tsv"
    marks.write_text(RATCHET, encoding="utf-8")

    assert "wide( n )" not in _marks_for(marks, "calc/grade.py")


def test_a_repo_with_no_marks_file_carries_no_marks(tmp_path):
    assert _marks_for(tmp_path / "crapkit-ratchet.tsv", "calc/grade.py") == set()


# --- rung 9: the advisory ----------------------------------------------------

def test_the_advisory_says_the_edit_landed_and_points_at_the_commit_gate():
    lines = _advisory_lines("calc/grade.py", [record("sprawl( n )", 8)], 6)

    assert lines == [
        "crapkit advisory: 1 function(s) over ceiling 6 in calc/grade.py "
        "(the edit landed; nothing was blocked)",
        "  ccn 8  calc/grade.py:1  sprawl( n )",
        "the commit gate enforces this; decompose there or mark the debt",
    ]


@pytest.mark.parametrize("banned", ["crapkit gate", "gate:", "decompose before committing"])
def test_the_advisory_never_borrows_the_commit_gates_wording(banned: str):
    """PostToolUse cannot block, and the edit is already on disk. The commit
    gate's own text would tell the agent its landed edit was rejected."""
    text = "\n".join(_advisory_lines("calc/grade.py", [record("sprawl( n )", 8)], 6))

    assert banned not in text


def test_the_advisory_says_outright_that_nothing_was_blocked():
    """The reader is a model that has just been handed a nonzero exit. Without
    this clause it reads the advisory as a refusal and reverts its own edit."""
    head = _advisory_lines("calc/grade.py", [record("sprawl( n )", 8)], 6)[0]

    assert "the edit landed; nothing was blocked" in head


# --- the catch-all -----------------------------------------------------------

class _Exploding(io.StringIO):
    def read(self, *args):
        raise OSError("the harness closed the pipe")


def test_any_internal_exception_leaves_the_edit_alone(monkeypatch):
    """On PostToolUse everything except exit 2 is invisible anyway, and an
    uncaught failure would be exit 1 with a stall nobody can see."""
    monkeypatch.setattr("sys.stdin", _Exploding())

    assert cmd_claude_hook(argparse.Namespace(protocol="1")) == 0


def test_a_payload_that_is_not_an_object_advances_no_rung():
    assert _advise(argparse.Namespace(protocol="1"), io.StringIO("[1, 2, 3]")) == 0


# --- the namespace guard -----------------------------------------------------

def test_the_parser_knows_the_subcommand():
    args = build_parser().parse_args(["claude-hook", "--protocol", "1"])

    assert args.protocol == "1"


def test_the_subcommand_defaults_to_protocol_one():
    assert build_parser().parse_args(["claude-hook"]).protocol == "1"


def test_the_handler_is_owned_by_its_own_module():
    from crapkit.cli import _OWNER

    assert _OWNER["cmd_claude_hook"] == "claude_hook"


@pytest.mark.parametrize("argv", [["claude-anything"], ["claude-hook-v2", "--protocol", "2"],
                                  ["claude-"]])
def test_an_unknown_claude_subcommand_exits_zero_with_no_usage_dump(argv, capsys, monkeypatch):
    """A plugin's hooks.json ships machine-wide and can name a subcommand the
    installed CLI does not have. argparse answers that with exit 2 and a usage
    dump, which on PostToolUse lands in the model's context on every edit."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code = main(argv)

    assert (code, capsys.readouterr()) == (0, ("", ""))


@pytest.mark.parametrize("argv", [["worklist-nope"], ["not-a-command"]])
def test_a_non_claude_typo_is_still_an_argparse_error(argv):
    """The guard covers the claude-* namespace and nothing else: a human typo on
    any other subcommand must still be told about."""
    with pytest.raises(SystemExit) as exit_code:
        main(argv)

    assert exit_code.value.code == 2


def test_every_claude_subcommand_the_parser_defines_is_in_the_guards_own_set():
    """The guard tests argv[1] against a named set rather than the parser, so a
    new claude-* subcommand that nobody adds to it would be silenced instead of
    run."""
    from crapkit.cli.parser import _CLAUDE_SUBCOMMANDS

    subs = [action for action in build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)]
    defined = {name for name in subs[0].choices if name.startswith("claude-")}

    assert defined == set(_CLAUDE_SUBCOMMANDS)
