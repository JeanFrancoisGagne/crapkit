"""Control: claude-hook stays silent on an unreadable file the edit left unchanged, as in 0.8.0.

test_claude_hook.py pins this row through claude_hook._unjudged and
_unread_advisory, which 0.8.1 introduced, so on 0.8.0 it stops at an
AttributeError. This test replays the row through the whole subcommand: a
TypeScript file no reader can parse, committed, and an Edit event that changed
nothing against HEAD. 0.8.0 exited 0 in silence on every unread file; 0.8.1
advises only when the edit changed one. Both answer this row the same way, so
the test passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import io
import json
import subprocess

from crapkit.cli import main
from test_claude_hook import ARROW, ARROW_TOML, KNOTTY_TS, _event, _unread_repo


def test_an_unreadable_file_the_edit_left_unchanged_draws_no_advisory(tmp_path, capsys,
                                                                      monkeypatch):
    edited = _unread_repo(tmp_path, "src/a.ts", ARROW_TOML, KNOTTY_TS + ARROW)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_event(edited, tmp_path))))

    code = main(["claude-hook", "--protocol", "1"])

    assert (code, capsys.readouterr().err) == (0, "")
