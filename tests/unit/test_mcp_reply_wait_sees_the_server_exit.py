"""A wait for an MCP reply ends when the server exits, and says how it exited.

Before, the reply wait watched only the message queue: a server that died before
replying held the test for the whole hang bound, and the failure named no exit
code.
"""
import pytest

from hang_guard import exited
from test_071_mcp_lifecycle import Client


def test_a_reply_wait_on_a_server_that_exited_names_its_exit_code(tmp_path):
    client = Client(tmp_path)
    try:
        client.process.stdin.close()
        exited(client.process, log=tmp_path / 'server-errors')

        with pytest.raises(AssertionError, match='a reply from the server before the child exited'):
            client.receive()
    finally:
        client.close()
