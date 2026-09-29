"""shellmetrics b3bfff2 ccn, read per function.

shellmetrics has bash print each function back (`typeset -f`) and counts one
decision per `then` (if and elif), per `do` (every loop) and per case arm
other than `*)`, on top of 1. It counts no `&&` or `||`.

ccn() takes a directory and the file paths under it, starts one shellmetrics
process for the whole list with --csv, and answers {(path, line): ccn} for
each named function. The rows `<begin>`, `<main>` and `<end>` are file
totals, not functions, and are dropped.

The transform from shellmetrics' count to crapkit's documented one lives in
test_shell_oracles.py as a rulings row. No crapkit import.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import hang_guard

FILE_ROWS = frozenset({"<begin>", "<main>", "<end>"})


def ccn(root: Path, paths: list) -> dict:
    done = hang_guard.run(["shellmetrics", "--csv", "--no-color", *paths], cwd=root, text=True,
                          encoding="utf-8", errors="replace")
    rows = [row for row in csv.DictReader(io.StringIO(done.stdout))
            if row["func"] not in FILE_ROWS]
    assert rows, done.stdout + done.stderr
    return {(row["file"], int(row["lineno"])): int(row["ccn"]) for row in rows}
