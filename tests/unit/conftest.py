"""What every unit test gets, whatever order it runs in.

The unit suite is one process, so a module-level global in src/crapkit is shared
by all 2,282 tests. `crapkit.uncovered` keeps one on purpose: an istanbul lane
decodes its artifact once and hands the dead lines to `_folded` / `_folded_from`
for the next reader to take, which is 100 MB against 205 MB on a 13-lane run.

Nothing in the library empties it except a reader, so a test that runs a lane
and never asks for the dead lines leaves the fold filled for whoever runs next.
48 tests in this suite do exactly that, most of them in
test_cli_scoring_inproc.py and test_cli_verifying_inproc.py. The reader they
break is in test_covstream.py: `_take_folded` drops the WHOLE fold when it
speaks for an artifact the caller did not name, so a stranger's key turns a
handover into two reads of the file and
test_the_walks_fold_answers_once_and_the_next_reader_reads_the_file fails. It
failed that way in one random order and passed in the next, and it passes in
file order only because earlier tests in its own file drain the fold first.
"""
import pytest

from crapkit import uncovered


def _empty_the_fold() -> None:
    uncovered._folded, uncovered._folded_from = {}, set()


@pytest.fixture(autouse=True)
def own_fold():
    """One empty fold per test, cleared on both sides.

    Before, so no test reads a lane another test walked; after, so a test that
    runs a lane and never reads it hands nothing on."""
    _empty_the_fold()
    yield
    _empty_the_fold()
