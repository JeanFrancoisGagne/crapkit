"""The MCP test harness reports a server that exits before reading its input."""
import json
import os
import sys

import pytest

import mcp_stdio

# Larger than any pipe buffer: the write can finish only if the server reads it,
# so a server that exits first always closes the pipe under the write.
BIG = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * 4_000_000}})
QUITS = [sys.executable, "-c", "import sys; print('no such flag', file=sys.stderr); sys.exit(2)"]


def test_a_server_that_exits_before_reading_is_reported_with_its_code_and_stderr(tmp_path):
    with pytest.raises(AssertionError) as missed:
        mcp_stdio.run(QUITS, cwd=tmp_path, frames=BIG, env=dict(os.environ))

    report = str(missed.value)
    assert "never saw a reply from the MCP server before the child exited with code 2" in report
    assert "no such flag" in report.split("--- the child printed ---")[1]
