"""A marks file's history that re-keys one mark, written through raw_git.

Three commits change `crapkit-ratchet.tsv`:

1. SEEDED, 400 days before the newest commit: `calc/grade.py classify( a , b )`
   is marked at 7.0000.
2. REKEYED: the function gained a parameter, and the same 7.0000 now sits under
   `classify( a , b , c = None )`. This is the move `keys.pair_moves` pairs.
3. SECOND: a second `classify` with the same signature joins it as twin #2,
   marked 9.0000. Nothing left the file, so nothing pairs with it.

gate-group-03's mission-4 probes read it, and the marks-history tickets
(gate-group-04a, gate-group-04) extend it with revisions of their own. Every
revision is written as the bytes below, so a reader under test never wrote
the file it reads.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from raw_git import commit, repository

MARKS = "crapkit-ratchet.tsv"
PATH = "calc/grade.py"
OLD = "classify( a , b )"
NEW = "classify( a , b , c = None )"
TWIN = f"{NEW}#2"
STAMP = "# crapkit-analysis=13 lizard=1.24.0"

# Each revision's marks, (key name, mark as the file spells it), in file order.
SEEDED = ((OLD, "7.0000"),)
REKEYED = ((NEW, "7.0000"),)
SECOND = ((NEW, "7.0000"), (TWIN, "9.0000"))

# Days before now each commit is stamped with: the seed is 400 days older than
# the newest commit, which is what the report anchors ages on.
AGES = (400, 30, 0)


class History(NamedTuple):
    """The three commits' shas, oldest first."""
    seeded: str
    rekeyed: str
    second: str


def marks_bytes(marks) -> bytes:
    """One revision of the marks file: the stamp, the key version, the header
    and one row per (key name, mark), every row in PATH."""
    lines = [STAMP, "# crapkit-keys=1", "path\tlong_name\tcrap"]
    lines += [f"{PATH}\t{name}\t{mark}" for name, mark in marks]
    return ("\n".join(lines) + "\n").encode("utf-8")


def keys_of(marks) -> set[tuple[str, str]]:
    """A revision's marked keys, (path, key name) each."""
    return {(PATH, name) for name, _ in marks}


def rekey_history(root: Path) -> History:
    """The three commits on `main` in a new repo at ROOT. Neither the index nor
    the working tree moves; raw_git.checkout does that for a caller who wants it."""
    repository(root)
    shas = [commit(root, message=message.encode(), age_days=age,
                   files={MARKS.encode(): marks_bytes(marks)})
            for message, marks, age in (("seed classify", SEEDED, AGES[0]),
                                        ("re-key classify", REKEYED, AGES[1]),
                                        ("a second classify", SECOND, AGES[2]))]
    return History(*shas)
