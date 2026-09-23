"""A packet's handle refusal names the run its rows came from.

brief refuses legacy twins before it builds a packet, and names the run there.
The packet's handle was the last identity read on brief's path that dropped the
run, so a packet that reached it first would print the refusal with no run for
the reader to refresh.
"""
from types import SimpleNamespace

import pytest

from crapkit.cli.queue import _brief_packet
from crapkit.errors import ToolError
from crapkit.score import ScoredRow


def _twin():
    """One of two `g` rows a legacy run stored on line 1 with no position."""
    return ScoredRow("web", "src/a.ts", "g", 1, 1, 9, 9, 9, 1, 0, 0,
                     0.0, "untested", 90.0, "decompose", 0, 0)


class _Loader:
    latest = {"id": 2, "commit": "c0ffee"}
    cfg = SimpleNamespace(ceiling_of=lambda scope: 6)

    def __init__(self, rows):
        self.rows = rows

    def scored_file(self, path):
        return self.rows


def test_the_handle_refusal_names_the_run_the_rows_came_from():
    twins = [_twin(), _twin()]

    with pytest.raises(ToolError, match=r"src/a\.ts: g in run 2;"):
        _brief_packet(_Loader(twins), twins[0])
