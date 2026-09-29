"""Records recorded/py.json and recorded/py-junit.xml; see crapkit.toml."""
from src.py.calc import classify, total, valid


def test_classify():
    assert classify(95, False) == "A"
    assert classify(80, True) == "B-"


def test_total():
    assert total([1, -2, 30], 10) == 11


def test_valid():
    assert valid("name")
