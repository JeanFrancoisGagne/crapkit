"""The ratchet file's history walked revision by revision through pygit2, and the
burn-down a reader of the docs computes from it. No crapkit.

Where crapkit reads the file's revisions through `git log` and `git cat-file`,
this walk reads the commit objects, their trees and every revision's blob
whole through pygit2 1.20.1 (libgit2, no git process), and diffs the mark sets
itself. tools/accuracy/pins.toml pins pygit2 for the nightly tier, so a check
that walks a history runs there and asks for the oracle (`oracle("pygit2")`).
pygit2 is imported where the walk starts, as pygit2_renames does, so
read_marks, decode and shallow serve a push-tier or unit test that has no
pygit2.

The walk: from HEAD back along a line of commits (a merge is refused here:
the fixtures hold none). A commit is a revision of the file when its tree and
its parent's hold different bytes under the file's name, or when it renamed
the file. A commit that adds the name while its parent lacks it renamed the
file when libgit2 pairs the two (find_similar, FIND_RENAMES, 50 percent, the
threshold `git diff -M` takes; pygit2_renames.py says the same), and the walk
goes on under the old name from its parent. A revision that deletes the file
reads as no marks.

Each revision is decoded by the rule docs/ratchet.md#how-the-file-is-read
gives for every past revision: UTF-16 when the bytes open with a UTF-16
byte-order mark, else UTF-8 with a UTF-8 byte-order mark dropped, and each
byte that fits neither reads as U+FFFD, so nothing refuses.

The file (docs/ratchet.md#what-a-mark-is): comment lines start with '#', then
a header `path<TAB>long_name<TAB>crap`, then one mark per row, keyed by
(path, key name). A row that needs the portable record encoding is refused
here: the fixtures hold none.

The report (docs/ratchet.md#reporting-the-burn-down, docs/agent-json.md
`ratchet report --json`):
- anchor_ts is the newest commit that touched the file, whatever it changed.
- A mark enters when a commit adds its key; a commit that changes its value
  keeps the entry date; a commit that removes the key repays it, and a later
  re-add enters it again.
- open is the marks on disk; a mark with no commit behind it entered at the
  anchor and is 0 days old.
- age_days is whole days from entry to the anchor (floor).
- uncommitted counts the keys the working tree and the newest commit disagree
  on, a changed value included.
- dropped_last_30d and _90d count repayments within 30 and 90 days of the anchor.
- oldest lists up to 20 open marks, oldest first, then by path and key name.
- The debt policy (docs/ratchet.md#the-debt-policy): a mark older than
  debt_max_age_months counted at 30 days a month; repayment stalled when fewer
  than repayment_min_per_30d marks were repaid in 30 days while debt is open.

shallow() answers the report's `shallow` key apart from the burn-down: whether
the checkout is a shallow clone, which holds only part of the history every
age and repayment counts. git says so itself (`rev-parse
--is-shallow-repository`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import re

from .history_git import text

DAY = 86_400
HEADER = ("path", "long_name", "crap")
RENAME_THRESHOLD = 50
UTF16_MARKS = {b"\xff\xfe": "utf-16-le", b"\xfe\xff": "utf-16-be"}
UTF8_MARK = b"\xef\xbb\xbf"
# surrogateescape turns each byte UTF-8 cannot decode into one lone surrogate
# from U+DC80 to U+DCFF, so each such byte becomes one U+FFFD below.
_ESCAPED = re.compile("[\udc80-\udcff]")


class EncodedRow(ValueError):
    """A row this reader does not decode."""


class Unwalked(ValueError):
    """A history this walk does not follow: a merge."""


def decode(data: bytes) -> str:
    """One revision's text by the docs' rule for a past revision."""
    codec = UTF16_MARKS.get(data[:2])
    if codec is not None:
        return data[2:].decode(codec, "replace")
    body = data[len(UTF8_MARK):] if data.startswith(UTF8_MARK) else data
    return _ESCAPED.sub("\ufffd", body.decode("utf-8", "surrogateescape"))


def read_marks(text: str) -> dict[tuple[str, str], Decimal]:
    """{(path, key name): crap} of one version of the file."""
    rows = [line for line in text.replace("\r\n", "\n").split("\n") if _is_row(line)]
    return dict(_mark(row) for row in rows)


def _is_row(line: str) -> bool:
    """A mark row: not blank, not a comment, not the header."""
    return bool(line) and not line.startswith("#") and tuple(line.split("\t")) != HEADER


def _mark(row: str) -> tuple[tuple[str, str], Decimal]:
    cells = row.split("\t")
    if len(cells) != 3 or any(cell.startswith('"') for cell in cells):
        raise EncodedRow(row)
    return (cells[0], cells[1]), Decimal(cells[2])


def _marks_of(data: bytes | None) -> dict:
    return {} if data is None else read_marks(decode(data))


@dataclass(frozen=True)
class Version:
    at: int
    marks: dict
    path: str = ""


def _repository(root: Path):
    """The pygit2 repository holding `root`, and `root`'s path inside its tree."""
    import pygit2

    repo = pygit2.Repository(pygit2.discover_repository(str(root)))
    prefix = Path(root).resolve().relative_to(Path(repo.workdir).resolve()).as_posix()
    return repo, "" if prefix == "." else prefix + "/"


def _blob_id(tree, path: str):
    """The blob id `tree` holds at `path`, or None."""
    try:
        entry = tree[path]
    except KeyError:
        return None
    return entry.id if entry.type_str == "blob" else None


def _renamed_from(repo, parent, commit, path: str) -> str | None:
    """The path libgit2 pairs with `path` as a rename from `parent` to `commit`."""
    import pygit2

    diff = repo.diff(parent, commit)
    diff.find_similar(flags=pygit2.enums.DiffFind.FIND_RENAMES,
                      rename_threshold=RENAME_THRESHOLD)
    renamed = pygit2.enums.DeltaStatus.RENAMED
    return next((delta.old_file.path for delta in diff.deltas
                 if delta.status == renamed and delta.new_file.path == path), None)


def _step(repo, commit, path: str):
    """(the revision `commit` makes of the file at `path` or None, the path the
    walk goes on under from its parent)."""
    if len(commit.parents) > 1:
        raise Unwalked(f"{commit.id} is a merge: this walk follows one line of commits")
    parent = commit.parents[0] if commit.parents else None
    here = _blob_id(commit.tree, path)
    there = None if parent is None else _blob_id(parent.tree, path)
    older = path
    if here is not None and there is None and parent is not None:
        older = _renamed_from(repo, parent, commit, path) or path
        there = _blob_id(parent.tree, older)
    if here == there and older == path:
        return None, older
    data = None if here is None else repo[here].data
    return Version(commit.author.time, _marks_of(data), path), older


def versions(root: Path, name: str) -> list[Version]:
    """Every committed revision of the file, oldest first, back through every
    rename libgit2 pairs; a deletion reads as no marks."""
    repo, prefix = _repository(root)
    if repo.head_is_unborn:
        return []
    found, commit, path = [], repo[repo.head.target], prefix + name
    while commit is not None:
        version, path = _step(repo, commit, path)
        found += [version] if version is not None else []
        commit = commit.parents[0] if commit.parents else None
    return found[::-1]


def _on_disk(path: Path) -> dict:
    return _marks_of(path.read_bytes()) if path.exists() else {}


@dataclass
class _Replay:
    entered: dict = field(default_factory=dict)
    dropped: list = field(default_factory=list)
    last: dict = field(default_factory=dict)
    anchor: int = 0

    def step(self, version: Version) -> None:
        for key in version.marks.keys() - self.last.keys():
            self.entered[key] = version.at
        for key in self.last.keys() - version.marks.keys():
            del self.entered[key]
            self.dropped.append(version.at)
        self.last, self.anchor = version.marks, max(self.anchor, version.at)

    def uncommitted(self, working: dict) -> int:
        keys = self.last.keys() | working.keys()
        return sum(1 for key in keys if self.last.get(key) != working.get(key))

    def within(self, days: int) -> int:
        return sum(1 for stamp in self.dropped if self.anchor - stamp <= days * DAY)


def _oldest(entered: dict, working: dict, anchor: int) -> list[dict]:
    rows = [{"path": path, "long_name": name,
             "age_days": (anchor - entered.get((path, name), anchor)) // DAY}
            for path, name in working]
    return sorted(rows, key=lambda row: (-row["age_days"], row["path"], row["long_name"]))[:20]


def report(root: Path, name: str) -> dict:
    """The burn-down of the file `name` under `root`, read as the docs define it."""
    replay = _Replay()
    for version in versions(root, name):
        replay.step(version)
    working = _on_disk(root / name)
    return {"open": len(working), "dropped_total": len(replay.dropped),
            "anchor_ts": replay.anchor, "uncommitted": replay.uncommitted(working),
            "oldest": _oldest(replay.entered, working, replay.anchor),
            "dropped_last_30d": replay.within(30), "dropped_last_90d": replay.within(90)}


def shallow(root: Path) -> bool:
    """Whether the checkout at `root` is a shallow clone, as git reads it."""
    return text(root, "rev-parse", "--is-shallow-repository") == "true"


def _too_old(found: dict, max_age_months: int | None) -> list[str]:
    if max_age_months is None:
        return []
    return [f"age {row['path']} {row['long_name']}" for row in found["oldest"]
            if row["age_days"] > max_age_months * 30]


def _stalled(found: dict, min_repaid_30d: int | None) -> list[str]:
    if min_repaid_30d is None or not found["open"]:
        return []
    return ["stalled"] if found["dropped_last_30d"] < min_repaid_30d else []


def violations(found: dict, max_age_months: int | None, min_repaid_30d: int | None) -> list[str]:
    """The debt policy's findings: 'age <path> <key>' per mark past its age, then
    'stalled' when repayment falls short while debt is open."""
    return _too_old(found, max_age_months) + _stalled(found, min_repaid_30d)
