"""The lcov adapter: a tracefile's DA and BRDA records as per-file evidence.

cargo-llvm-cov, `llvm-cov export -format=lcov`, geninfo over gcov, bun, deno and
c8 write lcov. A record opens at `SF:<path>` and closes at `end_of_record`. DA
records give each line's hit count and BRDA records each branch arm's. lcov
keeps no function records crapkit can trust for spans, so this reader hands
score's evidence join per-file line and branch evidence (score.FileEvidence)
and no FnCoverage: FN, FNDA, FNL and FNA records are checked for shape and give
nothing. One SF key in two records merges, a line hit and an arm taken when
either record says so.

Keys: a relative SF key takes the lane's path_prefix, then repopath's reported
entry; an absolute one goes to the placing step, which records the keys it
leaves unplaced for the lane's wrong-tree check. The artifact is read once,
through covstream's HashingReader and its line splitter, which own the digest
and the decode rule; lcov has no producer facts (no data file, no shards).
"""
from __future__ import annotations

import re
from array import array
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, NamedTuple

from . import covstream
from .errors import ToolError
from .repopath import Reported, absolute, file_separators
from .score import FileEvidence

if TYPE_CHECKING:
    from .config import Lane
    from .repopath import Unplaced

_REGENERATE = covstream.REGENERATE
_COUNT = re.compile(r"[0-9]+")
# The largest line number array("I") holds. A line past it, or below 1, sits in
# no span a function or a changed range names, so the evidence leaves it out.
_LAST_LINE = 2 ** 32 - 1
_NOT_TAKEN = "-"


class _Closed(NamedTuple):
    """One key's evidence once its record ends, held compact until the read
    finishes: the line arrays FileEvidence takes as they are, and each arm's
    line, block-and-branch text and taken flag in line order. The arms stay
    one by one so a later record for the key merges arm by arm."""
    hit: array
    missed: array
    arm_lines: array
    arm_names: tuple[str, ...]
    arm_taken: bytes

    def evidence(self) -> FileEvidence:
        return FileEvidence(self.hit, self.missed, _triples(self.arm_lines, self.arm_taken))


class _Record:
    """One SF record's evidence while it is read: each DA line's hit and each
    BRDA arm's taken, an arm keyed by its line and its block-and-branch text."""

    __slots__ = ("key", "written", "lines", "arms")

    def __init__(self, key: str, written: str) -> None:
        self.key = key
        self.written = written
        self.lines: dict[int, bool] = {}
        self.arms: dict[tuple[int, str], bool] = {}

    def absorb(self, earlier: _Closed) -> None:
        """An earlier record for the same key: a line hit and an arm taken when
        either record says so."""
        for line in earlier.hit:
            self.lines[line] = True
        for line in earlier.missed:
            self.lines.setdefault(line, False)
        for arm, taken in zip(zip(earlier.arm_lines, earlier.arm_names), earlier.arm_taken):
            self.arms[arm] = self.arms.get(arm, False) or taken > 0

    def closed(self, names: dict[str, str]) -> _Closed:
        """The compact form, each arm name shared through `names` so a
        tracefile's millions of `0,0` and `0,1` arms hold one string each."""
        return _Closed(*_split_lines(self.lines), *_compact_arms(self.arms, names))


def _split_lines(lines: dict[int, bool]) -> tuple[array, array]:
    """The hit and the missed lines, each sorted."""
    hit, missed = array("I"), array("I")
    for line in filter(_numbered, sorted(lines)):
        (hit if lines[line] else missed).append(line)
    return hit, missed


def _compact_arms(arms: dict[tuple[int, str], bool],
                  names: dict[str, str]) -> tuple[array, tuple[str, ...], bytes]:
    """Each arm's line, shared block-and-branch text and taken flag, in line order."""
    kept = sorted(arm for arm in arms if _numbered(arm[0]))
    return (array("I", (line for line, _ in kept)),
            tuple(names.setdefault(name, name) for _, name in kept),
            bytes(arms[arm] for arm in kept))


def _numbered(line: int) -> bool:
    """A line array("I") holds and a span can own."""
    return 0 < line <= _LAST_LINE


def _triples(lines: array, taken: bytes) -> tuple[tuple[int, int, int], ...]:
    """(line, arms, arms taken) per line that holds a BRDA arm, in line order:
    `lines` is sorted, one entry per arm."""
    per_line: dict[int, list[int]] = {}
    for line, done in zip(lines, taken):
        counts = per_line.setdefault(line, [0, 0])
        counts[0] += 1
        counts[1] += done
    return tuple((line, total, covered) for line, (total, covered) in per_line.items())


class _Refusal(ValueError):
    """A record crapkit cannot read, worded without the artifact's name."""


def _count(payload: str, tag: str, what: str, value: str) -> int:
    if _COUNT.fullmatch(value) is None:
        raise _Refusal(f"{tag}:{payload}: {what} {value!r} is not a nonnegative integer")
    return int(value)


def _fields(payload: str, tag: str, needs: int, names: str) -> list[str]:
    fields = payload.split(",", needs - 1)
    if len(fields) < needs:
        raise _Refusal(f"{tag}:{payload}: a {tag} record needs {names}")
    return fields


def _da(record: _Record, payload: str) -> None:
    """DA:<line>,<count>[,<checksum>]; the checksum is not read."""
    line, count, *_ = _fields(payload, "DA", 2, "a line and a count")
    count = count.split(",", 1)[0]
    at = _count(payload, "DA", "line", line)
    hit = _count(payload, "DA", "count", count) > 0
    record.lines[at] = record.lines.get(at, False) or hit


def _brda(record: _Record, payload: str) -> None:
    """BRDA:<line>,<block>,<branch>,<taken>; a taken of `-` is an arm whose
    block never ran. lcov 2 may write an expression as the branch, so the
    branch is the text between the block and the last comma."""
    line, rest = _fields(payload, "BRDA", 2, "a line, a block, a branch and a taken count")
    block_branch, _, taken = rest.rpartition(",")
    if "," not in block_branch:
        raise _Refusal(f"BRDA:{payload}: a BRDA record needs a line, a block, a branch and a "
                       "taken count")
    at = _count(payload, "BRDA", "line", line)
    done = taken != _NOT_TAKEN and _count(payload, "BRDA", "taken count", taken) > 0
    arm = (at, block_branch)
    record.arms[arm] = record.arms.get(arm, False) or done


def _fn(record: _Record, payload: str) -> None:
    """FN:<line>,<name> (lcov 1) or FN:<start>,<end>,<name> (lcov 2): a name
    may hold commas, so only the first field is read."""
    start, _ = _fields(payload, "FN", 2, "a line and a name")
    _count(payload, "FN", "line", start)


def _fnda(record: _Record, payload: str) -> None:
    """FNDA:<count>,<name>."""
    count, _ = _fields(payload, "FNDA", 2, "a count and a name")
    _count(payload, "FNDA", "count", count)


def _fnl(record: _Record, payload: str) -> None:
    """FNL:<index>,<start>[,<end>] (lcov 2.2)."""
    _fields(payload, "FNL", 2, "an index and a line")
    for at, value in enumerate(payload.split(",")):
        _count(payload, "FNL", "line" if at else "index", value)


def _fna(record: _Record, payload: str) -> None:
    """FNA:<index>,<count>,<name> (lcov 2.2)."""
    index, count, _ = _fields(payload, "FNA", 3, "an index, a count and a name")
    _count(payload, "FNA", "index", index)
    _count(payload, "FNA", "count", count)


def _summary(record: _Record, payload: str) -> None:
    """FNF, FNH, LF, LH, BRF and BRH sum the record; the reader counts its own."""


_READERS: dict[str, Callable[[_Record, str], None]] = {
    "DA": _da, "BRDA": _brda, "FN": _fn, "FNDA": _fnda, "FNL": _fnl, "FNA": _fna,
    **{tag: _summary for tag in ("FNF", "FNH", "LF", "LH", "BRF", "BRH")},
}


class _Tracefile:
    """The records read so far, merged by key and held compact, and the one
    still open. Only the open record holds a dict per line and per arm, so a
    tracefile's peak stays near the evidence it returns."""

    def __init__(self, key_of: Callable[[str], str]) -> None:
        self._key_of = key_of
        self._names: dict[str, str] = {}
        self.files: dict[str, _Closed] = {}
        self.open: _Record | None = None

    def take(self, line: str) -> None:
        tag, _, payload = line.partition(":")
        if tag == "SF":
            self._start(payload)
        elif tag == "end_of_record":
            self._close()
        elif tag in _READERS:
            self._field(_READERS[tag], self._inside(tag), payload)

    @staticmethod
    def _field(reader: Callable[[_Record, str], None], record: _Record, payload: str) -> None:
        """One record field, a refusal naming the SF key it sits under."""
        try:
            reader(record, payload)
        except _Refusal as refusal:
            raise _Refusal(f"SF:{record.written}: {refusal}") from None

    def _start(self, payload: str) -> None:
        if self.open is not None:
            raise _unclosed(self.open)
        if not payload:
            raise _Refusal("an SF record names no file")
        self.open = _Record(self._key_of(payload), payload)

    def _inside(self, tag: str) -> _Record:
        if self.open is None:
            raise _Refusal(f"a {tag} record comes before any SF record")
        return self.open

    def _close(self) -> None:
        record = self._inside("end_of_record")
        earlier = self.files.get(record.key)
        if earlier is not None:
            record.absorb(earlier)
        self.files[record.key] = record.closed(self._names)
        self.open = None

    def finish(self) -> dict[str, FileEvidence]:
        """Each key's FileEvidence, its compact arms freed as it is built."""
        if self.open is not None:
            raise _unclosed(self.open)
        return {key: self.files.pop(key).evidence() for key in list(self.files)}


def _unclosed(record: _Record) -> _Refusal:
    return _Refusal(f"the record for SF:{record.written} has no end_of_record")


def _measured(evidence: dict[str, FileEvidence], name: str) -> dict[str, FileEvidence]:
    """A tracefile with no SF record scores every function untested if it is
    let through."""
    if not evidence:
        raise ToolError(f"lcov artifact {name} is empty (zero SF records) - the coverage run "
                        "measured nothing; rerun the lane and check that its command runs "
                        "the tests")
    return evidence


def _lines(handle, name: str) -> Iterator[str]:
    """The tracefile's lines that hold a record, lcov's own blank lines and TN
    lines left out: a TN line names a test run, not a record field."""
    for line in covstream.lines(handle, name):
        if line.strip() and not line.startswith("TN:"):
            yield line


def _evidence(path: Path, name: str,
              key_of: Callable[[str], str]) -> tuple[dict[str, FileEvidence], str]:
    """Each SF key's FileEvidence and the sha256 of the artifact's bytes, one
    pass. A record crapkit cannot read is refused naming the artifact."""
    with open(path, "rb") as raw:
        reader = covstream.HashingReader(raw)
        tracefile = _Tracefile(key_of)
        try:
            for line in _lines(reader, name):
                tracefile.take(line)
            evidence = tracefile.finish()
        except _Refusal as refusal:
            raise ToolError(f"lcov artifact {name}: {refusal}; {_REGENERATE}") from None
        return _measured(evidence, name), reader.hexdigest()


def _keyer(root: Path, path_prefix: str) -> tuple[Reported, Callable[[str], str]]:
    """The reported entry and the rule that keys one SF path through it: a
    relative key, less any leading `./`, takes path_prefix first; an absolute
    key goes to the placing step as written, never under the prefix."""
    reported = Reported(root)
    prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""

    def key_of(raw: str) -> str:
        key = file_separators(raw)
        if absolute(key):
            return reported(key)
        while key.startswith("./"):
            key = key[2:]
        return reported(prefix + key)
    return reported, key_of


# --- the adapter a lane reads through ------------------------------------------

WRONG_TREE_FIX = ("These SF paths name files outside this checkout: rerun the lane here "
                  "rather than reusing a tracefile copied in or restored from a CI cache, "
                  "and keep the producer writing paths under this checkout's root")
# Reached only by a path this platform cannot open: the reported entry places
# every other spelling of this checkout before the wrong-tree check reads a key.
ABSOLUTE_FIX = ("The reader places every absolute SF path that resolves under this "
                "checkout, and these could not be opened here: rerun the lane on this "
                "machine rather than reusing a tracefile written somewhere else")
UNMEASURED_READING = ("or the producer writes SF paths relative to a directory these "
                      "scopes do not start at, which path_prefix rebases")
TAKES_PATH_PREFIX = True
# No producer facts: crapkit knows of no data file, shard or dropping an lcov
# producer leaves outside its tracefile, so each one is empty.
SHARD_GLOB = None
COMBINE_RECIPE = None
DROPPINGS = ()


def data_file(lane: Lane) -> None:
    """No data file a second lane could collide on."""
    return None


def read(lane: Lane, root: Path, artifact: Path, *,
         unplaced: dict[str, Unplaced] | None = None
         ) -> tuple[dict[str, list], dict[str, FileEvidence], str]:
    """No function records (an empty list per measured file), each file's
    FileEvidence and the artifact digest, one pass. `unplaced` takes each
    absolute SF key the placing step left unplaced, with its reason."""
    reported, key_of = _keyer(root, lane.path_prefix)
    evidence, digest = _evidence(artifact, lane.artifact, key_of)
    if unplaced is not None:
        unplaced.update(reported.unplaced)
    return {key: [] for key in evidence}, evidence, digest


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The DA lines with count 0, per measured file."""
    evidence, _ = _evidence(artifact, lane.artifact, _keyer(root, lane.path_prefix)[1])
    return {key: set(file.missed_lines) for key, file in evidence.items()}


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """lcov records no per-line test contexts: a TN line names a test run."""
    return {}
