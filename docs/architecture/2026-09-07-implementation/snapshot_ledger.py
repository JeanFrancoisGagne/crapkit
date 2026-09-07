"""Preserve one run ledger without overwriting an earlier snapshot."""
from pathlib import Path
import sqlite3
import sys

source, destination = map(Path, sys.argv[1:])
assert source.is_file()
assert not destination.exists()
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
    with sqlite3.connect(destination) as saved:
        original.backup(saved)
print(destination)
