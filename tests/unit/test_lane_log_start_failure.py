"""A lane attempt whose command failed to start says so in the lane log.

The timeout and no-progress paths each write their reason under the command's
output. A start failure wrote nothing, so a retried lane's log showed the
first attempt's output with no exit line and no reason before the next
attempt's header. The old launcher at least left "(exit 4294967295)" there.
"""
from crapkit import lanes, procs
from crapkit.config import Lane
from crapkit.errors import ToolError

import pytest

DLL_INIT_FAILED = 0xC0000142
REASON = ("command exited with code 3221225794 (0xC0000142, STATUS_DLL_INIT_FAILED): a process "
          "in it failed to start, so the code is not the command's answer")


def test_each_attempt_that_failed_to_start_leaves_its_reason_in_the_log(tmp_path, monkeypatch):
    def fails_to_start(command, timeout, *, stream, **kwargs):
        stream.write("work ran\n")  # a shell's earlier steps can run and write first
        procs._refuse_failed_start(DLL_INIT_FAILED)  # what run_bounded raises for that exit

    monkeypatch.setattr(lanes, "run_bounded", fails_to_start)
    lane = Lane(name="flaky", command="suite --cov", artifact="cov.json", parser="istanbul",
                scopes=(), retries=1)

    with pytest.raises(ToolError, match="failed to start"):
        lanes.run_lane(tmp_path, lane)

    log = (tmp_path / ".crapkit" / "lane-flaky.log").read_text(encoding="utf-8")
    assert log == (f"$ suite --cov\nwork ran\n\n[crapkit] {REASON}\n"
                   f"\n--- attempt 2 ---\n$ suite --cov\nwork ran\n\n[crapkit] {REASON}\n")
