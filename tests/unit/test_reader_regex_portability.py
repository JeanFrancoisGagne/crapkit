"""Every reader's patterns match the same on every CPython crapkit installs on.

On CPython 3.11.2, the python3 of Debian 12, which requires-python >=3.11
admits, a negative lookahead or lookbehind that fires inside a possessive loop
keeps the text it looked at (CPython gh-100061): `(?:(?!b)a)*+` matches all of
"aab", so `(?:(?!b)a)*+b` finds nothing there. 3.11.16 answers (0, 2) and
(0, 3). A reader pattern of that shape reads a different token stream on 3.11.2
and raises nothing, so ccn and the function list move without a word.

The suite runs on one CPython, which reads these patterns right, so no count
test can see the fault. This file reads each pattern's parse tree instead. The
spelling that matches right on 3.11.2 is an atomic group around a greedy loop,
`(?>(?:(?!b)a)*)`, which means the same as `*+`.
"""
import importlib
import pkgutil
import re
import re._parser as sre_parse

import pytest

import crapkit

READERS = sorted(m.name for m in pkgutil.iter_modules(crapkit.__path__)
                 if m.name.startswith("lizard"))


def _constants(module):
    """(name, source, flags) for each upper-case constant of MODULE that is a
    pattern, compiled or not yet."""
    for name, value in vars(importlib.import_module("crapkit." + module)).items():
        source = value.pattern if isinstance(value, re.Pattern) else value
        if isinstance(source, str) and name.isupper():
            yield name, source, getattr(value, "flags", 0)


def _subpatterns(argument):
    """The parse trees nested anywhere in one node's argument."""
    if isinstance(argument, sre_parse.SubPattern):
        yield argument
    elif isinstance(argument, (tuple, list)):
        for part in argument:
            yield from _subpatterns(part)


def _nodes(tree, in_possessive=False):
    """(opcode, whether a possessive loop holds it) for every node in TREE."""
    for op, argument in tree:
        yield op, in_possessive
        for sub in _subpatterns(argument):
            yield from _nodes(sub, in_possessive or op is sre_parse.POSSESSIVE_REPEAT)


def _misreads_on_3112(source, flags=0):
    try:
        tree = sre_parse.parse(source, flags)
    except re.error:
        return False
    return (sre_parse.ASSERT_NOT, True) in set(_nodes(tree))


PATTERNS = {f"{module}.{name}": (source, flags)
            for module in READERS for name, source, flags in _constants(module)}


@pytest.mark.parametrize("source, misreads", [
    (r"(?:(?!b)a)*+b", True),
    (r"(?:(?<!x)[^b])*+b", True),
    (r"(?:x|(?:(?!b)a)+)++", True),
    (r"\$\((?:(?>(?:(?!C).)*)C|D)*+\)", True),
    (r"(?>(?:(?!b)a)*)b", False),
    (r"(?:(?!b)a)*b", False),
    (r"(?:(?=a)a)*+b", False),
    (r"[^b]*+(?!b)", False),
    (r"(unbalanced", False),
])
def test_the_scan_finds_a_negative_lookaround_at_any_depth_of_a_possessive_loop(
        source, misreads):
    """The fourth row is the shape 3.11.2 misread in the shell reader: an atomic
    group inside a possessive loop does not shield the lookahead it holds."""
    assert _misreads_on_3112(source) is misreads


def test_the_scan_reaches_the_patterns_the_readers_tokenize_with():
    assert {"lizardshell._TOKEN_ADDITION", "lizardshell._HOLE",
            "lizardpowershell._TOKEN_ADDITION"} <= PATTERNS.keys()


def test_no_reader_pattern_puts_a_negative_lookaround_inside_a_possessive_loop():
    assert sorted(name for name, (source, flags) in PATTERNS.items()
                  if _misreads_on_3112(source, flags)) == []
