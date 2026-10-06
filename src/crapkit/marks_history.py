"""The marks file's git history: every past revision a command reads, one reader.

Every past revision of the marks file is read whole, through
`RatchetFile.committed` under the past-revision rule: UTF-8 with a byte-order
mark dropped, UTF-16 when a byte-order mark says so, any other byte as U+FFFD,
and never a refusal, since the file those bytes came from may be gone. No
reader parses patch lines. The readers:

- `ratchet report` and `brief` read mark ages and repayments off
  `marks_history`, the file's revisions parsed into marks, which
  `ratchet_report.mark_events` diffs.
- `ratchet report` on a deleted or emptied marks file reads `held_history`:
  the marks of the newest commit that held any are open, each aged by the
  history up to that commit, and the commit that deleted or emptied the file
  repays none.
- verify judges a deleted or emptied marks file against
  `newest_committed_marks`, the newest marks a commit since the baseline held.
- `marks_at` answers the marks one commit held. It is the one read of a
  commit's marks file: verify's stand-in reads the baseline's marks through
  it, and mission-5 lists the marks added since the baseline against it, so a
  mark the baseline already held is not new debt.
- `moves` answers each mark a revision moved to a new key, the keys it dropped
  and added paired by keys.pair_moves. mission-4 reads it so ratchet report
  keeps a re-keyed mark's age, and mission-5 reads the moves since the
  baseline's commit.

The log walks no renames on its own (gitio.file_revisions says why), so when
the first commit that touched the marks file renamed it with `git mv`, this
module goes on reading the old path's revisions from that commit's parent,
back through every rename git pairs, and the renaming commit reads against the
old file's newest revision. A renamed marks file keeps every age and every
repayment.

gitio answers the git questions; this module decides what they mean for the
marks. A git read that fails raises GitError, and the stand-in walk names what
it was reading, since a history the clone does not hold is not a history that
never held marks.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import GitError
from .gitio import (FileRevision, ancestry, blob_at, commit_renames, file_revisions,
                    proven_ancestor, shallow_fix)
from .keys import Key, mark_key, pair_moves
from .ratchet import read_ratchet
from .ratchetfile import RatchetFile


class MarksRevision(NamedTuple):
    """One commit that touched the marks file, its revision parsed.

    `marks` is key -> crap as the commit left the file, the first mark under
    a key answering (keys.MarkIndex); None when the file was missing or held
    only blank lines. `before` is the same for the revision the commit
    changed. A merge changed nothing git shows, so its `before` is its own
    marks: `git log -p` prints no patch for a merge.

    `changed` holds every key `before` and `marks` may differ on when the
    reader narrowed them (_RevisionReader): any other key holds one mark in
    both. None reads as every key either one holds.
    """
    commit: str
    time: int
    marks: dict | None
    before: dict | None
    changed: frozenset | None = None


class Move(NamedTuple):
    """One mark a commit moved: `old_key` left `path`, `new_key` arrived in
    it, and keys.pair_moves pairs the two. Keys are (path, key name)."""
    commit: str
    time: int
    path: str
    old_key: Key
    new_key: Key


def marks_history(root: Path, ratchet_file: str) -> list[MarksRevision]:
    """The marks file's revisions, oldest first, across every rename git
    pairs. [] when no commit touched it."""
    return _revisions(root, ratchet_file, None)


def _revisions(root: Path, ratchet_file: str, rev_range: str | None) -> list[MarksRevision]:
    """marks_history over `rev_range`, BASE..HEAD; all of HEAD's history for None."""
    read = _RevisionReader(root / ratchet_file)
    return [read.revision(revision) for revision in _walk(root, ratchet_file, rev_range)]


def marks_at(root: Path, commit: str, ratchet_file: str) -> dict | None:
    """key -> crap as `commit` left the marks file, read as marks_history reads
    a revision; None when that commit held no marks file, or one of only blank
    lines. A past revision never refuses. A commit this clone does not hold
    raises GitError, and in a shallow clone it names the fetch."""
    return _RevisionReader(root / ratchet_file).held(_held(root, commit, ratchet_file))


def moves(root: Path, rev_range: str | None, ratchet_file: str) -> list[Move]:
    """Each mark a revision in `rev_range` (BASE..HEAD; all of HEAD's history
    for None) moved, oldest first. A revision pairs the keys it dropped with
    the keys it added against the revision it changed, the diff mark_events
    reads, so the first revision of a range pairs against what the base held
    when no commit between touched the file, under the name it had there when
    that first revision was a `git mv`. A merge moves nothing. A base
    that is not an ancestor of the range's head raises GitError naming both."""
    if rev_range is not None:
        _check_range(root, rev_range)
    return [move for revision in _revisions(root, ratchet_file, rev_range)
            for move in _moved(revision)]


def _check_range(root: Path, rev_range: str) -> None:
    base, dots, head = rev_range.partition("..")
    head = head or "HEAD"
    if dots and base and not proven_ancestor(root, ancestry(root, base, head)):
        raise GitError(f"cannot read the marks moved in {rev_range}: {base} is not an "
                       f"ancestor of {head}{shallow_fix(root)}")


def _moved(revision: MarksRevision) -> list[Move]:
    """Keys pair only within a path, so each path pairs only its own keys:
    with every key handed to pair_moves once per path, moves over a history
    whose first revision adds 40,303 marks across 37,000 paths took 438 s."""
    before, after = revision.before or {}, revision.marks or {}
    dropped = _by_path(_only_in(before, after, revision.changed))
    added = _by_path(_only_in(after, before, revision.changed))
    return [Move(revision.commit, revision.time, path, old, new)
            for path in sorted(dropped.keys() & added.keys())
            for old, new in pair_moves(path, dropped[path], added[path]).pairs]


def _only_in(side: dict, other: dict, keys: frozenset | None) -> set:
    """The keys `side` holds and `other` does not: among `keys`, or among
    every key `side` holds when `keys` is None (MarksRevision.changed)."""
    return (side.keys() if keys is None else keys & side.keys()) - other.keys()


def _by_path(keys) -> dict[str, list[Key]]:
    grouped: dict[str, list[Key]] = {}
    for key in keys:
        grouped.setdefault(key[0], []).append(key)
    return grouped


def held_history(root: Path, ratchet_file: str) -> list[MarksRevision]:
    """The history a missing or blank marks file reports from: each revision
    up to the newest one that held more than blank lines, and every later one
    as a clock tick that changes no mark and holds what that one held.

    Replayed as written, the commit that deleted or emptied the file dropped
    every mark, the burn-down counted each one as repaid, and `--enforce`
    passed a repayment quota the restored file fails. The newest revision
    that held marks says which are open: a replay cannot say which marks a
    merge kept, so a mark one branch repaid and the other loosened would read
    as open after the merge that kept the repayment.
    """
    history = marks_history(root, ratchet_file)
    last = next((index for index in range(len(history) - 1, -1, -1)
                 if history[index].marks is not None), -1)
    held = history[last].marks if last >= 0 else None
    return history[:last + 1] + [revision._replace(marks=held, before=held)
                                 for revision in history[last + 1:]]


def _walk(root: Path, ratchet_file: str, rev_range: str | None) -> list[FileRevision]:
    """The file's revisions in `rev_range` oldest first, back through every
    rename; each older name reads from the range's base up to the renaming
    commit. An older name no commit in the range touched opens the range: the
    renaming commit reads against what that name held at the commit before it,
    as the first revision of a range reads against what the base held."""
    segments: list[list[FileRevision]] = []
    base = _base_of(rev_range)
    path, rev = ratchet_file, rev_range
    while path is not None:
        segment = file_revisions(root, path, rev)
        if segments and not segment:
            segments[0][0] = _opened(root, segments[0][0], path, base)
        segments.insert(0, segment)
        path = _renamed_from(root, segment[0].commit, path) if segment else None
        rev = f"{base}{segment[0].commit}^" if path is not None else None
    return _stitched(segments)


def _opened(root: Path, renaming: FileRevision, old_path: str, base: str) -> FileRevision:
    """The renaming commit that opens a range, read against the old name's
    bytes at its parent. With no base the walk read every older revision, so
    finding none means the clone holds no parent to read (a shallow clone's
    boundary): the commit reads as the file created there."""
    if not base:
        return renaming
    return renaming._replace(before=_blob(root, f"{renaming.commit}^", old_path))


def _base_of(rev_range: str | None) -> str:
    """`BASE..` for a range that names a base; "" for none."""
    base, dots, _ = (rev_range or "").partition("..")
    return f"{base}.." if dots and base else ""


def _stitched(segments: list[list[FileRevision]]) -> list[FileRevision]:
    """The segments, oldest first, as one history. Each later segment opens
    with the commit that renamed the file, whose log reads the file as
    created there; it reads instead against the old file's newest revision,
    so a pure rename changes no mark and moves only the clock. A renaming
    commit with no older revision keeps the bytes _walk opened it with."""
    revisions = list(segments[0])
    for segment in segments[1:]:
        old = revisions[-1].data if revisions else segment[0].before
        revisions += [segment[0]._replace(before=old), *segment[1:]]
    return revisions


def _renamed_from(root: Path, commit: str, path: str) -> str | None:
    """The path `commit` renamed to `path`, or None when it created it."""
    renames = commit_renames(root, commit)
    return next((old for old, new in renames.items() if new == path), None)


class _Read(NamedTuple):
    """One revision as the reader read it. `text` and `marks` are None for no
    file or one of only blank lines. `once` says the revision lists every key
    once: only then can the next revision carry its marks. `changed` holds
    the keys of the marks a carry took out or put in; None for a revision
    read in full."""
    data: bytes | None
    text: str | None
    marks: dict | None
    once: bool
    changed: frozenset | None


class _RevisionReader:
    """Revisions read into marks, oldest first.

    A revision decodes whole through RatchetFile.committed, and its rows read
    by ratchet.read_ratchet's rule. read_ratchet reads each row on its own, so
    a row is read once however many revisions hold it.

    A history of a large marks file changes a few rows per commit. A revision
    read against the one it changed starts from that one's marks and reads
    only the rows the two texts do not share (_changed_rows), so it costs the
    rows it changed, not the rows the file holds: ratchet report took 3.1 s
    on 40,303 marks in 200 revisions when each revision built its marks anew.
    The first mark under a key answers, so a revision that lists a key twice,
    or follows one that did, is read row by row in full, and so is one whose
    base is not the revision read last.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rows = _Rows()
        self._newest = _Read(None, None, None, True, None)

    def revision(self, revision: FileRevision) -> MarksRevision:
        """One commit's revision, read against the revision it changed."""
        before = self._at(revision.data if revision.merge else revision.before)
        after = before if revision.merge else self._after(before, revision.data)
        self._newest = after
        return MarksRevision(revision.commit, revision.time, after.marks, before.marks,
                             frozenset() if after is before else after.changed)

    def held(self, held: RatchetFile | None) -> dict | None:
        """key -> crap for one revision as _committed read it; None for none."""
        return self._whole(None, held).marks

    def _at(self, data: bytes | None) -> _Read:
        """The revision `data` holds: the one read last when it is that one,
        else read in full."""
        return self._newest if data == self._newest.data else self._whole(
            data, _committed(self._path, data))

    def _after(self, before: _Read, data: bytes | None) -> _Read:
        """`data` read against `before`: carried from it when it can be, else
        read in full."""
        held = _committed(self._path, data)
        return self._carried(before, data, held) or self._whole(data, held)

    def _carried(self, before: _Read, data: bytes | None, held: RatchetFile | None) -> _Read | None:
        """`held` read from `before`: the marks of the rows only `before`
        holds go, those of the rows only `held` holds come. None when either
        holds no text, `before` lists a key twice, the texts do not line up
        again (_changed_rows), or a row that came lists a key already held."""
        rows = _changed_rows(before.text, held.text) if _carries(before, held) else None
        if rows is None:
            return None
        gone, came = map(self._marked, rows)
        marks = _carry(before.marks, gone, came)
        return None if marks is None else _Read(data, held.text, marks, True, _keys(gone + came))

    def _whole(self, data: bytes | None, held: RatchetFile | None) -> _Read:
        """`data` read row by row in full, as _committed read it into `held`."""
        if held is None:
            return _Read(data, None, None, True, None)
        marks = self._marked((held.text or "").split("\n"))
        by_key = dict(reversed(marks))  # reversed: the first mark under a key wins
        return _Read(data, held.text, by_key, len(by_key) == len(marks), None)

    def _marked(self, rows: list[str]) -> list[tuple]:
        """(key, crap) for each row that holds a mark, in order."""
        return list(filter(None, map(self._rows.__getitem__, rows)))


def _carries(before: _Read, held: RatchetFile | None) -> bool:
    return held is not None and before.text is not None and before.once


def _carry(marks: dict, gone: list[tuple], came: list[tuple]) -> dict | None:
    """`marks` without the marks that went and with the marks that came. Each
    key the marks list is listed once, so a mark that went names its key; a
    mark that came under a key still held is a key listed twice, whose first
    mark only a read in full knows: None."""
    carried = marks.copy()
    for key, _ in gone:
        del carried[key]
    for key, crap in came:
        if key in carried:
            return None
        carried[key] = crap
    return carried


def _keys(marked: list[tuple]) -> frozenset:
    return frozenset(key for key, _ in marked)


# Characters two revisions are compared in while they agree, and the reach of
# the search for the row where they agree again after a difference.
_BLOCK = 4096
# Rows of the older text tried, one after another, as the place the two agree again.
_RESYNC = 8
# A carry gives up once the texts differ in more than one stretch per this
# many characters of the older text. On 40,303 marks a carry through one
# stretch per 1,024 characters took 15 ms and a read in full 28 ms; through
# one per 512 the carry took 29 ms.
_CARRIED = 1024


def _changed_rows(old: str, new: str) -> tuple[list[str], list[str]] | None:
    """The rows only `old` holds and the rows only `new` holds, or None when,
    past a difference, none of the next _RESYNC rows of `old` turns up in
    `new` within _BLOCK characters, or when the texts differ in more than one
    stretch per _CARRIED characters of `old`.

    The stretches both texts hold are compared a block at a time and never
    split into rows. A row both hold may still come back on both sides, which
    carries its mark out and back in unchanged.
    """
    gone: list[str] = []
    came: list[str] = []
    i = j = 0
    for _ in range(len(old) // _CARRIED + 1):
        i, j = _agreed(old, new, i, j)
        if i == len(old) or j == len(new):
            return gone + old[i:].split("\n"), came + new[j:].split("\n")
        found = _realigned(old, new, i, j)
        if found is None:
            return None
        gone += old[i:found[0]].split("\n")
        came += new[j:found[1]].split("\n")
        i, j = found
    return None


def _agreed(old: str, new: str, i: int, j: int) -> tuple[int, int]:
    """From the row starts (i, j), the row starts where `old` and `new` first
    hold different rows, or the ends of both when they hold the same rest."""
    while i + _BLOCK <= len(old) and new.startswith(old[i:i + _BLOCK], j):
        i, j = i + _BLOCK, j + _BLOCK
    rest, other = old[i:i + _BLOCK], new[j:j + _BLOCK]
    if rest == other:  # shorter than a block: both end here
        return len(old), len(new)
    start = old.rfind("\n", 0, i + _shared(rest, other)) + 1
    return start, start + j - i


def _shared(a: str, b: str) -> int:
    """How many characters `a` and `b`, two strings that differ, share before
    they do. Bisected: a[:low] equals b[:low], a[:high] does not equal b[:high]."""
    low, high = 0, min(len(a), len(b)) + 1
    while high - low > 1:
        mid = (low + high) // 2
        low, high = (mid, high) if a[low:mid] == b[low:mid] else (low, mid)
    return low


def _realigned(old: str, new: str, i: int, j: int) -> tuple[int, int] | None:
    """Past the differing rows at the row starts (i, j), the row starts where
    `old` and `new` hold one row again: the first of the next _RESYNC rows of
    `old` that `new` holds as a row within _BLOCK characters of j. The ends of
    both when `old` runs out of rows that end in a newline first."""
    p = i
    for _ in range(_RESYNC):
        end = old.find("\n", p) + 1
        if not end:
            return len(old), len(new)
        q = _row_at(new, old[p:end], j)
        if q is not None:
            return p, q
        p = end
    return None


def _row_at(text: str, row: str, start: int) -> int | None:
    """Where `row`, its newline included, is a whole row of `text`, from the
    row start `start` to _BLOCK characters past it; None when it is not."""
    stop = start + _BLOCK + len(row)
    at = text.find(row, start, stop)
    while at > start and text[at - 1] != "\n":  # inside a longer row: look on
        at = text.find(row, at + 1, stop)
    return None if at < 0 else at


class _Rows(dict):
    """One row's (key, crap), or None for a row that holds no mark, read once."""

    def __missing__(self, row: str):
        entries = read_ratchet(row)[0]
        self[row] = (mark_key(entries[0]), entries[0].crap) if entries else None
        return self[row]


def newest_committed_marks(root: Path, base: str, ratchet_file: str):
    """(commit, marks) for the newest commit from `base` to HEAD whose marks file
    holds more than blank lines, or None when none does. A history this clone
    cannot read raises GitError naming what it was for, and in a shallow clone
    the fetch that brings the rest in (gitio.file_revisions)."""
    try:
        return _newest_marks(root, base, ratchet_file)
    except GitError as exc:
        raise GitError(f"cannot read the history of {ratchet_file} since the baseline "
                       f"{base[:11]} to stand in for the missing marks: {exc}") from exc


def _newest_marks(root: Path, base: str, ratchet_file: str):
    path = root / ratchet_file
    for revision in reversed(file_revisions(root, ratchet_file, f"{base}..HEAD")):
        held = _committed(path, revision.data)
        if held is not None:
            return revision.commit, held
    held = _held(root, base, ratchet_file)
    return None if held is None else (base, held)


def _held(root: Path, commit: str, ratchet_file: str) -> RatchetFile | None:
    """The marks file as `commit` left it (_committed): the one read of a
    commit's marks, which marks_at parses and verify's stand-in judges. A
    commit this clone does not hold raises GitError naming the fetch."""
    return _committed(root / ratchet_file, _blob(root, commit, ratchet_file))


def _blob(root: Path, commit: str, rel_path: str) -> bytes | None:
    """gitio.blob_at, and a commit this clone does not hold names the fetch."""
    try:
        return blob_at(root, commit, rel_path)
    except GitError as exc:
        raise GitError(f"{exc}{shallow_fix(root)}") from exc


def _committed(path: Path, data: bytes | None) -> RatchetFile | None:
    """The marks one revision held, or None when it held none or only blank
    lines. Blank reads as it does for the file on disk (RatchetFile.blank), so
    a byte-order mark and blank lines hold no marks."""
    held = None if data is None else RatchetFile.committed(path, data)
    return None if held is None or held.blank else held
