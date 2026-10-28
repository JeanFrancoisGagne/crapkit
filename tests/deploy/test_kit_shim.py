"""Two shim launches at once each keep every record they write.

Codex starts `crapkit mcp` twice at the same moment. While every launch
appended to one shared log, a Windows append (a seek to the end, then a write)
let two launches overwrite or cut each other's line: on one Windows runner 9
to 13 of 50 two-launch rounds lost a `start`, a `first_line` or an `exit`, and
a cut line failed the read. The loop holds the shim to keeping each launch's
records whole. It calls only shim.install, shim.launcher_name and shim.starts.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kit import shim

pytestmark = pytest.mark.kit

ROUNDS = 50
# The real launcher's stand-in: the box's Python reads the line the shim forwards and exits 0.
READ_ONE_LINE = "import sys; sys.stdin.readline()"


def launch_pair(box, launcher, round_no: int) -> list[str]:
    """Two shim launches started together, each given one JSON line; the lines sent."""
    sent = [json.dumps({"round": round_no, "launch": launch}) + "\n" for launch in (0, 1)]
    together = threading.Barrier(len(sent))

    def launch(line: str) -> None:
        together.wait()
        box.run([launcher, "-c", READ_ONE_LINE], input=line, expect=0)

    with ThreadPoolExecutor(len(sent)) as pool:
        list(pool.map(launch, sent))
    return sent


def round_problems(launches: list[dict], sent: list[str]) -> list[str]:
    """What one round's launches lack: a launch, a record of one, or a line it was sent."""
    problems = [] if len(launches) == len(sent) else [f"{len(launches)} launches recorded, not {len(sent)}"]
    for launch in launches:
        problems += [f"launch {launch.get('pid')} has no {key}" for key in ("argv", "first_line", "exit")
                     if launch.get(key) is None]
    got = sorted(launch["first_line"] for launch in launches if launch.get("first_line"))
    return problems + ([] if got == sorted(sent) else [f"first lines {got} are not the lines sent {sent}"])


def test_two_shim_launches_at_once_each_keep_every_record(box):
    launcher = shim.install(box, box.toolchain.python("3.12")) / shim.launcher_name()
    red, before = [], 0
    for round_no in range(ROUNDS):
        sent = launch_pair(box, launcher, round_no)
        try:
            launches = shim.starts(box)
        except ValueError as error:
            red.append(f"round {round_no}: a log line json.loads refuses: {error}")
            break
        red += [f"round {round_no}: {problem}" for problem in round_problems(launches[before:], sent)]
        before = len(launches)

    assert not red, f"{len(red)} problem(s) in {ROUNDS} rounds of two launches at once:\n" + "\n".join(red)
