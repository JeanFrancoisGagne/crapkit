"""The marks file's git history: every past revision a command reads, one reader.

Every past revision of the marks file reads as `RatchetFile.committed` reads
it, under the past-revision rule: UTF-8 with a byte-order mark dropped,
UTF-16 when a byte-order mark says so, any other byte as U+FFFD, and never a
refusal, since the file those bytes came from may be gone. The history comes
from one `git log -p -U0 --raw` stream (gitio.file_revisions): a revision
carries the marks of the one it changed through the rows its patch took out
and put in, and only a revision the patch cannot carry is read whole from its
blob (_RevisionReader says which). On Windows a whole-blob read of every
revision cost 1.2 to 1.6 s per `ratchet report` on 40,303 marks in 200
revisions, in pipe transport alone. The readers:

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

from collections import Counter
from pathlib import Path
from typing import NamedTuple

from .errors import GitError
from .gitio import (FileRevision, PatchRows, ancestry, blob_at, commit_renames, file_revisions,
                    object_blobs, proven_ancestor, shallow_fix)
from .keys import Key, mark_key, pair_moves
from .ratchet import read_ratchet
from .ratchetfile import RatchetFile
from .repotext import marks_text, plain_utf8


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
    read = _RevisionReader(root, ratchet_file)
    return [read.revision(revision) for revision in _walk(root, ratchet_file, rev_range)]


def marks_at(root: Path, commit: str, ratchet_file: str) -> dict | None:
    """key -> crap as `commit` left the marks file, read as marks_history reads
    a revision; None when that commit held no marks file, or one of only blank
    lines. A past revision never refuses. A commit this clone does not hold
    raises GitError, and in a shallow clone it names the fetch."""
    return _RevisionReader(root, ratchet_file).held(_held(root, commit, ratchet_file))


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
        opened = segment[0]._replace(base=revisions[-1].blob) if revisions else segment[0]
        revisions += [opened, *segment[1:]]
    return revisions


def _renamed_from(root: Path, commit: str, path: str) -> str | None:
    """The path `commit` renamed to `path`, or None when it created it."""
    renames = commit_renames(root, commit)
    return next((old for old, new in renames.items() if new == path), None)


class _Read(NamedTuple):
    """One revision as the reader read it. `blob` names it; None for no file,
    or for bytes read with no id. `marks` is None for no file or one of only
    blank lines. `filled` counts the rows that hold more than blank space, so
    a carry knows when a revision went blank. `once` says the revision lists
    every key once: only then can the next revision carry its marks.
    `changed` holds the keys of the marks a carry took out or put in, against
    the revision it carried from; None for a revision read whole."""
    blob: str | None
    marks: dict | None
    filled: int
    once: bool
    changed: frozenset | None


_NO_FILE = _Read(None, None, 0, True, None)


class _RevisionReader:
    """Revisions read into marks, oldest first.

    A revision gitio read whole decodes through RatchetFile.committed, and
    its rows read by ratchet.read_ratchet's rule. read_ratchet reads each row
    on its own, so a row is read once however many revisions hold it.

    Every other revision carries the marks of the one it changed through its
    patch: the marks of the rows the patch took out go, those of the rows it
    put in come, each row decoded as the whole revision decodes it. A row
    both sides hold, moved or not, comes back out of both, so the rows that
    change are the ones a whole read of each side would say, whatever diff
    algorithm git ran. The first mark under a key answers, so a revision that
    lists a key twice, or follows one that did, is read whole. A byte-order
    mark reads only on line 1, so a patch that takes out line 1 and puts in
    none, or the reverse, moves a row it does not name onto or off line 1,
    and that revision is read whole too. A revision gitio did not read whole
    is read through its blob id then.
    """

    def __init__(self, root: Path, ratchet_file: str) -> None:
        self._root, self._path = root, root / ratchet_file
        self._rows = _Rows()
        self._newest = _NO_FILE

    def revision(self, revision: FileRevision) -> MarksRevision:
        """One commit's revision, read against the revision it changed."""
        if revision.merge:
            before = after = self._side(revision.blob, revision.data)
        else:
            before = self._side(revision.base, revision.before)
            after = self._after(before, revision)
        self._newest = after
        return MarksRevision(revision.commit, revision.time, after.marks, before.marks,
                             _changed(before, after))

    def held(self, held: RatchetFile | None) -> dict | None:
        """key -> crap for one revision as _committed read it; None for none."""
        return self._whole(None, held).marks

    def _side(self, blob: str | None, data: bytes | None) -> _Read:
        """The revision `blob` names: read whole from `data` when gitio read
        it, the one read last when it is that one, else read whole off git."""
        if data is not None:
            return self._whole(blob, _committed(self._path, data))
        if blob is None:
            return _NO_FILE
        if blob == self._newest.blob:
            return self._newest
        return self._whole(blob, _committed(self._path, _object(self._root, blob)))

    def _after(self, before: _Read, revision: FileRevision) -> _Read:
        """The revision a commit left: carried from `before` through its rows
        when it can be, else read whole. A whole read names no keys it
        changed, even when it is the one read last."""
        carried = None if revision.rows is None else self._carried(before, revision.blob, revision.rows)
        return carried or self._side(revision.blob, revision.data)._replace(changed=None)

    def _carried(self, before: _Read, blob: str | None, rows: PatchRows) -> _Read | None:
        """The revision `rows` lead to from `before`. None when `before` lists
        a key twice, the patch moves which row is line 1, or a row that came
        lists a key still held."""
        if not before.once or rows.old_top != rows.new_top:
            return None
        gone, came = _unshared(_texts(rows.gone, rows.old_top), _texts(rows.came, rows.new_top))
        out, into = self._marked(gone), self._marked(came)
        marks = _carry(before.marks or {}, out, into)
        if marks is None:
            return None
        filled = before.filled - _filled(gone) + _filled(came)
        return _Read(blob, marks if filled else None, filled, True, _keys(out + into))

    def _whole(self, blob: str | None, held: RatchetFile | None) -> _Read:
        """One revision read row by row in full, as _committed read it into `held`."""
        if held is None:
            return _Read(blob, None, 0, True, None)
        rows = (held.text or "").split("\n")
        marks = self._marked(rows)
        by_key = dict(reversed(marks))  # reversed: the first mark under a key wins
        return _Read(blob, by_key, _filled(rows), len(by_key) == len(marks), None)

    def _marked(self, rows: list[str]) -> list[tuple]:
        """(key, crap) for each row that holds a mark, in order."""
        return list(filter(None, map(self._rows.__getitem__, rows)))


def _texts(rows: tuple[bytes, ...], top: bool) -> list[str]:
    """Each row as RatchetFile.committed decodes it: line 1 (the first row
    when `top`) as the file's start, a byte-order mark dropped, and every
    other row as UTF-8 that keeps a mark as U+FEFF. Any other byte reads as
    U+FFFD. A UTF-16 revision never gets here: gitio hands no rows for a
    patch that holds a NUL, whether git printed it as binary or as text."""
    texts = [plain_utf8(row) for row in rows]
    if top:
        texts[0] = marks_text(rows[0])
    return texts


def _changed(before: _Read, after: _Read) -> frozenset:
    """The keys `before` and `after` may hold different marks under: none for
    one revision, the keys a carry moved, else each key whose mark differs."""
    if after is before:
        return frozenset()
    if after.changed is not None:
        return after.changed
    return _differing(before.marks or {}, after.marks or {})


def _differing(old: dict, new: dict) -> frozenset:
    """Each key one of two revisions read whole holds a mark under that the
    other does not hold the same."""
    return frozenset(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))


def _unshared(gone: list[str], came: list[str]) -> tuple[list[str], list[str]]:
    """The rows only `gone` holds and the rows only `came` holds, a row both
    hold taken out of each once."""
    went, arrived = Counter(gone), Counter(came)
    return list((went - arrived).elements()), list((arrived - went).elements())


def _filled(rows: list[str]) -> int:
    """The rows that hold more than blank space (RatchetFile.blank)."""
    return sum(map(bool, map(str.strip, rows)))


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
    """Newest first, each revision's bytes read only until one holds marks."""
    path = root / ratchet_file
    for revision in reversed(file_revisions(root, ratchet_file, f"{base}..HEAD")):
        held = _committed(path, _left(root, revision))
        if held is not None:
            return revision.commit, held
    held = _held(root, base, ratchet_file)
    return None if held is None else (base, held)


def _left(root: Path, revision: FileRevision) -> bytes | None:
    """The bytes a commit left: gitio's whole read, else its blob read now;
    None where it deleted the file."""
    if revision.data is not None or revision.blob is None:
        return revision.data
    return _object(root, revision.blob)


def _object(root: Path, blob: str) -> bytes:
    """One blob the history named, read by its id."""
    return object_blobs(root, [blob])[blob]


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
