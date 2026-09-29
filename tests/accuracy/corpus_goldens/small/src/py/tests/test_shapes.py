"""A test file inside the scope: the universe drops tests/ directories, and the
scope's {files} template has a test to point at. The recording suite is tests/py."""
from src.py.grades import spread


def test_spread():
    assert spread([2, 5]) == 3
