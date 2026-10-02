"""xdist deals the parts of a split search (settings.split) before any other test.

tools/accuracy/run.py loads this plugin (`-p accuracy.kit.parts_first`) beside
`--dist worksteal`. Its scheduler deals the parts round robin, one to each
worker in turn, then splits the rest evenly as work stealing does, and stealing
balances from there: each worker starts on a part of its own. Under xdist's
default `load` a worker took runs of consecutive tests, and one worker took all
four parts of the history machine. `loadgroup` hands each worker one test
first, but after a worker crashed late in a session it handed the workers
tests that had already run and the session hung.

A plugin and not a conftest hook: every past-bug row's digest covers the
conftest files and what they import, so a conftest edit would make each row
stale.
"""
from __future__ import annotations

import itertools
import re

from xdist.scheduler import WorkStealingScheduling

PART = re.compile(r"\[part\d+\]$")  # the ids settings.split gives its params


class PartsFirst(WorkStealingScheduling):
    """Work stealing whose first deal hands out the parts before the rest."""

    dealt = False

    def check_schedule(self) -> None:
        if not self.dealt and self.collection is not None:
            self.dealt = True
            self._deal()
        super().check_schedule()

    def _deal(self) -> None:
        parts = [index for index in self.pending if PART.search(self.collection[index])]
        for index, node in zip(parts, itertools.cycle(self.nodes)):
            self.pending.remove(index)
            self.node2pending[node].append(index)
            node.send_runtest_some([index])
        for left, node in zip(range(len(self.nodes), 0, -1), self.nodes):
            self._send_tests(node, len(self.pending) // left)


def pytest_xdist_make_scheduler(config, log):
    return PartsFirst(config, log)
