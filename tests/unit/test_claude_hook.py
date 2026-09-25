"""The advisory hook's ladder, rung by rung.

The goldens in tests/e2e drive the whole subcommand through a real spawn; these
pin the individual decisions, including the ones a golden can only observe as
silence. Every rung that fails to advance exits 0 and says nothing, so without
these a broken rung and a working one look identical from outside.
"""
import argparse
import io
import json
import os
import time
from pathlib import Path

import pytest

from crapkit.cli.parser import build_parser
from crapkit.cli import main
from crapkit.cli import claude_hook
from crapkit.cli.claude_hook import (_advise, _advisory_lines, _breaches, _command_event,
                                     _edited_files, _edited_path, _fresh, _fresh_python,
                                     _judgeable, _marks_for, _measured, _repo_root,
                                     _sequencing, _status_records, cmd_claude_hook)
from crapkit.merge import FunctionRecord

EVENT = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
         "tool_input": {"file_path": "/repo/calc/grade.py"}}


def record(name: str, ccn: int, start: int = 1, end: int = 16) -> FunctionRecord:
    return FunctionRecord("calc/grade.py", name, start, end, ccn, ccn, ccn, 9, 1, 1)


# --- rung 1: the payload -----------------------------------------------------

def test_a_post_tool_use_edit_names_the_file_it_touched():
    assert _edited_files(EVENT) == ["/repo/calc/grade.py"]


@pytest.mark.parametrize("payload", [
    {},
    {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/repo/a.py"}},
    {"hook_event_name": "Stop", "tool_input": {"file_path": "/repo/a.py"}},
    {"hook_event_name": "PostToolUse", "tool_input": {"notebook_path": "/repo/a.ipynb"}},
    {"hook_event_name": "PostToolUse", "tool_input": None},
    {"hook_event_name": "PostToolUse"},
    {"hook_event_name": "PostToolUse", "tool_input": {"file_path": 17}},
    {"hook_event_name": "preToolUse", "tool_input": {"file_path": "/repo/a.py"}},
])
def test_an_event_protocol_one_does_not_judge_names_no_file(payload: dict):
    assert _edited_files(payload) == []


# --- rung 1, as the other harnesses that run the plugin's hook send it ---------
#
# The plugin's one handler also runs under GitHub Copilot CLI, Cursor (which
# imports Claude Code plugins) and VS Code. Each names the edited file in its
# own vocabulary, captured from the harness at the version named.

# Copilot CLI 1.0.88: Claude's tool names, its own argument names.
COPILOT_EDIT = {"hook_event_name": "PostToolUse", "session_id": "s", "timestamp": "t",
                "cwd": "/repo", "tool_name": "Edit",
                "tool_input": {"path": "/repo/calc/grade.py", "old_str": "a", "new_str": "b"},
                "tool_result": {"result_type": "success"}}
# Cursor 2026.09.23: the event name in camelCase, Edit and Write both as Write.
CURSOR_WRITE = {"hook_event_name": "postToolUse", "conversation_id": "c", "cwd": "/repo",
                "tool_name": "Write",
                "tool_input": {"file_path": "/repo/calc/grade.py", "content": "b"}}
# VS Code 1.139.0: its own tool names, `filePath`, every tool call whatever the matcher.
VSCODE_REPLACE = {"hook_event_name": "PostToolUse", "session_id": "s", "timestamp": "t",
                  "tool_name": "replace_string_in_file",
                  "tool_input": {"filePath": "/repo/calc/grade.py", "oldString": "a",
                                 "newString": "b"}}
PATCH = ("*** Begin Patch\n*** Update File: /repo/calc/grade.py\n@@\n-a\n+b\n"
         "*** Add File: /repo/calc/new.py\n+x = 1\n*** Delete File: /repo/calc/old.py\n"
         "*** Update File: /repo/calc/moved.py\n*** Move to: /repo/calc/renamed.py\n"
         "*** End Patch\n")


@pytest.mark.parametrize("payload", [COPILOT_EDIT, CURSOR_WRITE, VSCODE_REPLACE],
                         ids=["copilot-path", "cursor-postToolUse", "vscode-filePath"])
def test_every_harness_names_the_edited_file_where_the_hook_finds_it(payload):
    """Copilot's `path` and Cursor's camelCase event each read as no event at
    all, so the hook exited 0 on every edit a Copilot or Cursor agent made."""
    assert _edited_files(payload) == ["/repo/calc/grade.py"]


def test_a_vscode_multi_replace_names_each_file_once():
    replacements = [{"filePath": "/repo/calc/grade.py"}, {"filePath": "/repo/calc/grade.py"},
                    {"filePath": "/repo/calc/other.py"}, "not an object"]
    payload = dict(VSCODE_REPLACE, tool_name="multi_replace_string_in_file",
                   tool_input={"explanation": "e", "replacements": replacements})

    assert _edited_files(payload) == ["/repo/calc/grade.py", "/repo/calc/other.py"]


def test_a_vscode_apply_patch_names_every_file_it_leaves_written():
    """A deleted file has nothing left to judge. A moved one names both paths;
    the old one is gone from disk by then and `_measured` drops it."""
    payload = dict(VSCODE_REPLACE, tool_name="apply_patch",
                   tool_input={"input": PATCH, "explanation": "e"})

    assert _edited_files(payload) == ["/repo/calc/grade.py", "/repo/calc/new.py",
                                      "/repo/calc/moved.py", "/repo/calc/renamed.py"]


@pytest.mark.parametrize("tool,tool_input", [
    ("read_file", {"filePath": "{FILE}", "startLine": 1, "endLine": 9}),
    ("list_dir", {"path": "{DIR}"}),
    ("run_in_terminal", {"command": "pytest", "explanation": "e", "isBackground": False}),
    ("apply_patch", {"input": 17}),
    ("multi_replace_string_in_file", {"replacements": "none"}),
])
def test_a_vscode_tool_that_writes_nothing_is_never_judged(tool, tool_input, tmp_path):
    """VS Code runs the plugin's hook on every tool call and ignores its
    `Edit|Write` matcher, so the hook applies it: a read, a listing or a terminal
    command beside a breaching file is neither an edit nor the `Bash` event the
    fallback answers."""
    edited = _breaching_repo(tmp_path)
    pointed = {key: str(value).replace("{FILE}", str(edited)).replace("{DIR}", str(edited.parent))
               if isinstance(value, str) else value for key, value in tool_input.items()}
    payload = dict(VSCODE_REPLACE, tool_name=tool, cwd=str(tmp_path), tool_input=pointed)

    assert (claude_hook._advisory(payload), _command_event(payload)) == ([], False)


def test_the_same_file_under_a_vscode_write_is_judged(tmp_path):
    """Guards the case above: the breaching repo it reads does draw an advisory."""
    edited = _breaching_repo(tmp_path)
    payload = dict(VSCODE_REPLACE, cwd=str(tmp_path), tool_input={"filePath": str(edited)})

    assert claude_hook._advisory(payload)[0].startswith("crapkit advisory: 1 function(s)")


def test_a_file_of_a_type_crapkit_does_not_measure_is_never_judged(tmp_path):
    """The plugin's per-file-type `if` rules skipped these before the spawn;
    with one handler for every edit the hook screens them itself."""
    for name in ("grade.py", "notes.md", "data.json"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    edited = [str(tmp_path / name) for name in ("grade.py", "notes.md", "data.json")]

    assert _measured({}, edited) == [tmp_path / "grade.py"]


def test_every_type_the_language_map_names_is_judged(tmp_path):
    """The screen and the map are one table, so a language added to the map is
    advised on its first edit."""
    from crapkit.universe import LANGUAGE_EXTENSIONS

    extensions = [e for exts in LANGUAGE_EXTENSIONS.values() for e in exts]
    for extension in extensions:
        (tmp_path / f"x{extension}").write_text("", encoding="utf-8")

    judged = _measured({}, [str(tmp_path / f"x{e}") for e in extensions])

    assert [path.suffix for path in judged] == extensions


def test_an_edited_file_gone_from_disk_is_not_judged(tmp_path):
    """A patch that moved a file names its old path too; judging a path that
    no longer exists would raise and silence the files beside it."""
    assert _measured({}, [str(tmp_path / "gone.py")]) == []


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

    assert claude_hook._advise_command(payload) == []


def test_a_cwd_that_is_not_a_directory_names_no_top(tmp_path):
    """The event's cwd can be gone by the time the async hook runs (a command
    that removed its own directory). Silence, not a spawn error."""
    assert claude_hook._repo_top(tmp_path / "gone") is None


def test_a_non_command_event_takes_no_fallback_judgement():
    assert claude_hook._advise_command({"hook_event_name": "PostToolUse",
                                        "tool_input": {}}) == []


@pytest.mark.parametrize("tool", ["Bash", "Shell"])
def test_the_shell_tool_under_each_name_a_harness_sends_is_a_command_event(tool):
    """Claude Code and Copilot CLI send Bash; Cursor maps a Bash matcher onto its Shell tool."""
    assert _command_event(dict(BASH_EVENT, tool_name=tool)) is True


def test_a_command_string_under_another_tool_takes_no_fallback():
    """Codex's apply_patch carries its patch in `command`, and VS Code's terminal
    tool reaches the hook whatever the matcher says. Neither asked for the
    working-tree scan the Bash half is registered for."""
    assert _command_event(dict(BASH_EVENT, tool_name="apply_patch")) is False


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


# --- stdin arrives as UTF-8 whatever the console's code page says --------------

_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"  # ccn 8, over the ceiling of 6
TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n')


def _cp1252_stdin(payload: dict) -> io.TextIOWrapper:
    """What a Windows console hands the hook: Claude Code's UTF-8 bytes, wrapped
    in the locale code page. A TextIOWrapper, because that is what sys.stdin is,
    reconfigure() included."""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252")


def _breaching_repo(tmp_path: Path, toml_bytes: bytes = TOML.encode("utf-8")) -> Path:
    (tmp_path / "crapkit.toml").write_bytes(toml_bytes)
    (tmp_path / "calc").mkdir()
    edited = tmp_path / "calc" / "café.py"
    edited.write_text(BREACH, encoding="utf-8")
    return edited


def _event(edited: Path, root: Path) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(edited)}, "cwd": str(root)}


def test_a_non_ascii_path_under_a_cp1252_stdin_still_draws_the_advisory(tmp_path, capsys,
                                                                         monkeypatch):
    """Under code page 437 the advisory for an edit in `calc/café.py` was exit 0
    and silence: stdin decoded the path as `cafÃ©.py`, no such file, catch-all."""
    edited = _breaching_repo(tmp_path)
    monkeypatch.setattr("sys.stdin", _cp1252_stdin(_event(edited, tmp_path)))

    code = main(["claude-hook", "--protocol", "1"])

    err = capsys.readouterr().err
    assert code == 2, err
    assert "calc/café.py" in err, err


def test_a_configuration_saved_with_a_bom_reads_as_the_same_configuration(tmp_path, capsys,
                                                                          monkeypatch):
    """PowerShell's `Out-File -Encoding utf8` writes a BOM; the hook's own
    config read choked on it (`does not parse`), so the advisory went silent in
    every repo whose crapkit.toml came from that shell."""
    edited = _breaching_repo(tmp_path, TOML.encode("utf-8-sig"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_event(edited, tmp_path))))

    code = main(["claude-hook", "--protocol", "1"])

    err = capsys.readouterr().err
    assert code == 2, err
    assert "crapkit advisory" in err, err


# --- where the advisory goes, per harness ---------------------------------------
#
# Exit 2's stderr is text for the model in Claude Code alone. Cursor reads it as
# a deny, VS Code as a blocking error, and Copilot CLI shows it to the user and
# not the model (1.0.88, a stub-model session: of stderr on exit 2, a nested
# hookSpecificOutput, a systemMessage and a top-level additionalContext, only
# the last reached the model request). So those three get exit 0 and one JSON
# object carrying the text under both keys they read.

def _run(payload: dict, capsys, monkeypatch) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = main(["claude-hook", "--protocol", "1"])
    out, err = capsys.readouterr()
    return code, out, err


def _aimed(payload: dict, edited: Path, root: Path) -> dict:
    """One of the recorded payloads, pointed at a breaching file on disk."""
    tool_input = {key: str(edited) if key in ("file_path", "path", "filePath") else value
                  for key, value in payload["tool_input"].items()}
    return dict(payload, tool_input=tool_input, cwd=str(root))


def test_claude_code_hears_the_advisory_through_exit_2_and_stdout_stays_empty(
        tmp_path, capsys, monkeypatch):
    edited = _breaching_repo(tmp_path)

    code, out, err = _run(_aimed(EVENT, edited, tmp_path), capsys, monkeypatch)

    assert (code, out) == (2, "")
    assert err.startswith("crapkit advisory: 1 function(s) over ceiling 6 in calc/café.py"), err


@pytest.mark.parametrize("payload", [COPILOT_EDIT, CURSOR_WRITE, VSCODE_REPLACE],
                         ids=["copilot", "cursor", "vscode"])
def test_every_other_harness_hears_it_as_context_on_exit_0(payload, tmp_path, capsys,
                                                          monkeypatch):
    """Nothing is denied, blocked or left on a terminal the model never reads."""
    edited = _breaching_repo(tmp_path)

    code, out, err = _run(_aimed(payload, edited, tmp_path), capsys, monkeypatch)

    assert (code, err) == (0, "")
    answer = json.loads(out)
    context = answer["additionalContext"]
    assert answer["hookSpecificOutput"] == {"hookEventName": "PostToolUse",
                                            "additionalContext": context}
    assert context.startswith("crapkit advisory: 1 function(s) over ceiling 6"), context
    assert context.endswith("decompose there or mark the debt"), context


def test_copilots_bash_half_hears_it_as_context_too(tmp_path, capsys, monkeypatch):
    """The Bash entry a Copilot user adds to .claude/settings.json sends the
    same `tool_result` key, so its fallback verdict takes the same channel."""
    payload = dict(COPILOT_EDIT, tool_name="Bash", tool_input={"command": "python gen.py"})

    assert claude_hook._reads_context(payload) is True
    assert claude_hook._reads_context(BASH_EVENT) is False


def test_the_json_channel_stays_ascii_whatever_the_console_code_page(tmp_path, capsys,
                                                                    monkeypatch):
    """stdout is the harness's pipe in the console's code page; an escaped
    non-ASCII path survives any of them."""
    edited = _breaching_repo(tmp_path)

    _, out, _ = _run(_aimed(CURSOR_WRITE, edited, tmp_path), capsys, monkeypatch)

    assert out.isascii() and json.dumps("calc/café.py")[1:-1] in out


@pytest.mark.parametrize("payload", [EVENT, COPILOT_EDIT, CURSOR_WRITE, VSCODE_REPLACE],
                         ids=["claude", "copilot", "cursor", "vscode"])
def test_a_clean_edit_prints_nothing_on_either_channel(payload, tmp_path, capsys, monkeypatch):
    edited = _breaching_repo(tmp_path)
    edited.write_text("def calm(n):\n    return n\n", encoding="utf-8")

    assert _run(_aimed(payload, edited, tmp_path), capsys, monkeypatch) == (0, "", "")
