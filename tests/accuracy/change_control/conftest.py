"""Every change-control test starts from cold tool caches.

The tool caches compiled globs, corpus members and ESLint answers for the life of a
process. A test that found them warm would never run the code that fills them, and
under mutmut, whose children fork from a process that already ran the suite once,
no mutant of that code could be killed.
"""
import pytest

from accuracy.change_control import cc_seeds


@pytest.fixture(autouse=True)
def _cold_tool_caches():
    cc_seeds.tool().clear_caches()
