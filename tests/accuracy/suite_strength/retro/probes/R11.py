"""R11: the cognitive reader kept each function's running total under id(fn),
and Python hands a freed object's address to a later object of its size, so a
function could start from a finished function's total and read a higher
cognitive complexity than it has.

    <retro venv python> R11.py WORKTREE

analysis_oracles' determinism check passes at the commit before the fix: the
recycled address hands the same stale total to the same function on every run
of one input, so two cold runs agree and both are wrong. Which function
inherits depends on how the interpreter lays out memory, so a whole file can
read right in one venv and wrong in another. This probe makes the reuse happen
on purpose, below the CLI: it drives crapkit.lizardcognitive.LizardExtension,
which both commits have, over lizard FunctionInfo objects. `a` holds one `if`;
`b` follows it, which frees `a` where nothing else holds it; new functions are
then made until one lands at a's address (at most 10,000; the fix holds `a`,
so there it never frees), and that one, `c`, holds no structure. c must read 0.
"""
# source: SonarSource "Cognitive Complexity" (G. Ann Campbell, 2023), B1: only structures add to the score, so a function whose tokens are a line break and `return` reads 0
from __future__ import annotations

import sys
from types import SimpleNamespace

TRIES = 10_000


class PythonReader:
    """What the extension reads from lizard's reader: its type name and the
    function its context is in."""

    def __init__(self) -> None:
        self.context = SimpleNamespace(current_function=None)


def _at_address(address: int, kept: list):
    """A new FunctionInfo, the first made at `address` if one is within TRIES."""
    from lizard import FunctionInfo

    for _ in range(TRIES):
        made = FunctionInfo("c", "m.py")
        if id(made) == address:
            return made
        kept.append(made)
    return made


def _tokens(reader: PythonReader, seen: dict):
    from lizard import FunctionInfo

    reader.context.current_function = FunctionInfo("a", "m.py")
    address = id(reader.context.current_function)
    yield from ("\n", "    ", "if", "x", ":", "\n", "        ", "return")
    reader.context.current_function = FunctionInfo("b", "m.py")
    yield from ("\n", "    ", "return")
    reader.context.current_function = seen["c"] = _at_address(address, seen.setdefault("kept", []))
    yield from ("\n", "    ", "return")


def main(argv: list[str]) -> int:
    from crapkit.lizardcognitive import LizardExtension

    reader, seen = PythonReader(), {}
    for _ in LizardExtension()(_tokens(reader, seen), reader):
        pass
    got = seen["c"].cognitive_complexity
    assert got == 0, f"c holds no structure and reads cognitive {got}, the total a left at its address"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
