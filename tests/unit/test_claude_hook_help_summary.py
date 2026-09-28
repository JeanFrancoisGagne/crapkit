"""`crapkit --help` says when the advisory hook speaks.

The summary said the hook was silent unless a changed function was over its
ceiling. It also speaks, exit 2, when an edit leaves a file it could not
judge: a file no reader can read, a file git cannot say what changed in, a
scoped name that is not UTF-8. And it prints one line, exit 0, when the hook
passes a flag this crapkit does not know. A person reading the command list
learned of none of them.
"""
from __future__ import annotations

import argparse

from crapkit.cli.parser import build_parser


def _summary(name: str) -> str:
    (group,) = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    (action,) = [a for a in group._choices_actions if a.dest == name]
    return " ".join(action.help.split())


def test_the_summary_names_every_case_the_hook_speaks_in():
    summary = _summary("claude-hook")

    assert "silent unless a changed function is over its ceiling" not in summary
    assert "a function over its ceiling" in summary
    assert "a file it could not judge" in summary
    assert "a flag this crapkit does not know" in summary
