"""Stock lizard's function rows for a list of files, from a process that imports no crapkit.

Run as `python lizard_tripwire.py ROOT FILE...` with FILE relative to ROOT. It
prints one JSON object per function: path, long_name, start, end, nloc,
params, ccn_std (lizard's default count), ccn_mod (the same file under
lizard's `modified` extension, the CLI's -m) and nesting (the `ND`
extension's max_nesting_depth). A file lizard has no reader for prints
nothing.

lizard 1.24.0's CLI fails on `-END` for every brace language ("'NestingStack'
object has no attribute 'set_in_condition'"), so this script asks lizard's
Python API for the same extension lists the CLI builds from `-m` and `-END`.
This is a tripwire, not an oracle: it holds whatever stock lizard answers,
right or wrong, and the test compares crapkit to it only to catch drift in the
languages crapkit leaves to the stock readers.
"""
from __future__ import annotations

import json
import os
import sys

import lizard
import lizard_languages


def _functions(path: str, names: list[str]) -> list:
    return lizard.FileAnalyzer(lizard.get_extensions(names))(path).function_list


def _row(path: str, fn, modified) -> dict:
    return {"path": path, "long_name": fn.long_name, "start": fn.start_line,
            "end": fn.end_line, "nloc": fn.nloc, "params": len(fn.parameters),
            "ccn_std": fn.cyclomatic_complexity, "ccn_mod": modified.cyclomatic_complexity,
            "nesting": getattr(fn, "max_nesting_depth", 0) or 0}


def rows(path: str) -> list[dict]:
    """Stock lizard's rows for one file, standard and modified paired by position.
    Nothing for a suffix no stock reader claims: lizard's API would read it with
    the C reader, where its CLI skips it."""
    if lizard_languages.get_reader_for(path) is None:
        return []
    standard = _functions(path, ["ND"])
    modified = _functions(path, ["ND", "modified"])
    return [_row(path, fn, mod) for fn, mod in zip(standard, modified)]


def main(argv: list[str]) -> int:
    os.chdir(argv[0])
    for path in argv[1:]:
        for row in rows(path):
            print(json.dumps(row, ensure_ascii=True))
    return 1 if "crapkit" in sys.modules else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
