"""Fixtures for the history packet.

Hypothesis reads the constants of every local module the first time a draw
asks for one after sys.modules has grown
(hypothesis.internal.conjecture.providers._get_local_constants), and it counts
that read as input generation. The CLI runs in-process here, so a process test
imports some 150 crapkit modules, and the next property test's first draw spent
1 to 4 s reading them: the too_slow health check failed that test on Windows
and on Linux whenever a process test ran before it in one session. The read
now happens in each test's setup, before Hypothesis starts its clock; with no
new module it is one length check.
"""
from __future__ import annotations

from hypothesis.internal.conjecture import providers
import pytest


@pytest.fixture(autouse=True)
def local_constants_read_in_setup():
    providers._get_local_constants()
