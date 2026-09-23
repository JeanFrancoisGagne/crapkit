"""The shared publish adapter answers every readback at once, so it waits no real seconds.

`_settled` pauses READBACK_PAUSE seconds between reads of a just-written surface.
The adapter's in-memory surfaces never lag, so a test that exhausts the retries
slept 11 x 5 = 55 seconds for nothing. test_release_tool still pins the pause
itself.
"""
from test_release_guards import repo
from test_release_recovery import publish_adapter
from test_release_tool import release


def test_a_publish_adapter_readback_that_misses_retries_without_sleeping(tmp_path, monkeypatch):
    publish_adapter(repo(tmp_path, bumped=True), monkeypatch)
    waits = []

    assert release._settled(lambda: False, pause=waits.append, attempts=3) is False
    assert waits == [0, 0]
