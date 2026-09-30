"""R11: the cognitive reader kept each function's running total under id(fn),
and Python hands a freed object's number to a later object, so a function
could start from a finished function's total and read a higher cognitive
complexity than it has.

    <retro venv python> R11.py WORKTREE

analysis_oracles' determinism check passes at the commit before the fix: the
recycled address hands the same stale total to the same function on every run
of one input, so two cold runs agree and both are wrong. Which function
inherits depends on how the interpreter lays out memory, so a whole file can
read right in one venv and wrong in another.

The probe drives crapkit.lizardcognitive.LizardExtension, which both commits
have, over lizard FunctionInfo objects, below the CLI. `a` holds one `if`; `b`
follows it; `c` comes after a is gone. Whether CPython then gives c a's address
depends on its allocator's pools: Windows did on the first try, the Linux
accuracy image never did in 10,000 tries, so a probe that waited for the reuse
passed the before commit there. This probe does the reuse itself. id() is
unique only among objects alive at the same time, so an id that gives c the
number a had, once a is gone, is one CPython may return. The probe puts that id
in the extension module's globals, where its calls to id() find it before the
builtin. The before commit keys its totals by id(fn), so c starts from a's
total of 1. The fix keys them by the FunctionInfo itself and holds a, so a never
dies and no number is reused. c holds no structure and must read 0.
"""
# source: SonarSource "Cognitive Complexity" (G. Ann Campbell, 2023), B1: only structures add to the score, so a function whose tokens are a line break and `return` reads 0
# source: Python 3 Library Reference, Built-in Functions, id(): "Two objects with non-overlapping lifetimes may have the same id() value."
from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace
import weakref


class PythonReader:
    """What the extension reads from lizard's reader: its type name and the
    function its context is in."""

    def __init__(self) -> None:
        self.context = SimpleNamespace(current_function=None)


class RecycledId:
    """id() as CPython may answer it: `c` gets the number `a` had once `a` is gone."""

    def __init__(self) -> None:
        self.gone = lambda: None
        self.number = 0
        self.c = None

    def watch(self, a) -> None:
        self.gone, self.number = weakref.ref(a), builtins.id(a)

    def __call__(self, obj) -> int:
        if obj is self.c and self.gone() is None:
            return self.number
        return builtins.id(obj)


def _tokens(reader: PythonReader, recycled: RecycledId):
    from lizard import FunctionInfo

    reader.context.current_function = FunctionInfo("a", "m.py")
    recycled.watch(reader.context.current_function)
    yield from ("\n", "    ", "if", "x", ":", "\n", "        ", "return")
    reader.context.current_function = FunctionInfo("b", "m.py")
    yield from ("\n", "    ", "return")
    reader.context.current_function = recycled.c = FunctionInfo("c", "m.py")
    yield from ("\n", "    ", "return")


def main(argv: list[str]) -> int:
    from crapkit import lizardcognitive

    reader, recycled = PythonReader(), RecycledId()
    lizardcognitive.id = recycled
    for _ in lizardcognitive.LizardExtension()(_tokens(reader, recycled), reader):
        pass
    got = recycled.c.cognitive_complexity
    assert got == 0, f"c holds no structure and reads cognitive {got}, the total a left at its address"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
