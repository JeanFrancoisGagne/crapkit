"""The lane stamp file, .crapkit/artifacts.json: one read per command, in explicit states.

A lane run records what produced its artifact under the artifact's path: the
commit, the lane, the seconds it took, the reuse `proof` and its parts, the
digests of the files it wrote, the untracked files it left (`byproducts`) and
the git blob id of each file under its scopes (`blobs`). A failed attempt that
left the previous run's file in place records that file's sha256 instead
(`refused_sha256`), and the snapshot store keeps a copy of it, so a deleted or
unreadable stamp file does not hand the dead lane's leftover back to reuse.

Readers used to index the parsed file and check fields with isinstance, and
read an unreadable file, a mangled entry and no entry alike as "no stamp". Here
each artifact is in one of five states, and every question below answers for
each:

- absent: no file, or no entry for the artifact;
- unreadable: the file exists and does not read as a JSON object;
- mangled: the entry for the artifact is not an object;
- legacy: an entry without `blobs` (crapkit 0.8.0 or older, or git gave none);
- recorded: an entry with `blobs`.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

ABSENT, UNREADABLE, MANGLED, LEGACY, RECORDED = (
    "absent", "unreadable", "mangled", "legacy", "recorded")
STAMPS_FILE = ".crapkit/artifacts.json"


class Refusal(NamedTuple):
    """Whether the artifact on disk may be read as a measurement. `kind` is ""
    when it may, "leftover" when it is the file the lane's last attempt failed
    to replace, and "unknown" when the record that would say so cannot be read,
    `why` then saying why."""
    kind: str
    why: str = ""

    def cause(self, artifact: str) -> str:
        """Why the file at `artifact` may not be read as a measurement, in the
        one sentence doctor, the dark-line note and reuse all quote, or "" when
        it may."""
        if self.kind == "leftover":
            return f"its last attempt wrote no artifact, and the {artifact} on disk predates it"
        if self.kind == "unknown":
            return (f"{STAMPS_FILE} cannot be read ({self.why}), so crapkit cannot tell whether "
                    f"the {artifact} on disk is the file a failed attempt left")
        return ""


NOT_REFUSED = Refusal("")
LEFTOVER = Refusal("leftover")


def _path(root: Path) -> Path:
    return root / STAMPS_FILE


class Stamps:
    """One read of the stamp file. Build it with `read`, once per command."""

    def __init__(self, root: Path, raw: dict, unreadable: str = "") -> None:
        self.root = root
        self.raw = raw
        self.unreadable = unreadable
        self._refusals: dict[str, Refusal] = {}
        self._stored: dict[str, str] | None = None

    def state(self, artifact: str) -> str:
        if self.unreadable:
            return UNREADABLE
        if artifact not in self.raw:
            return ABSENT
        return _entry_state(self.raw[artifact])

    def entry(self, artifact: str) -> dict:
        """The artifact's entry, or {} in any state that holds none."""
        entry = self.raw.get(artifact)
        return entry if isinstance(entry, dict) else {}

    def why_unreadable(self, artifact: str) -> str:
        """Why the record for `artifact` cannot be read, or ""."""
        if self.unreadable:
            return self.unreadable
        return f"its entry for {artifact} is not an object" if self.state(artifact) == MANGLED else ""

    def mangled(self) -> list[str]:
        """The keys whose entry is not an object, sorted."""
        return sorted(key for key, entry in self.raw.items() if not isinstance(entry, dict))

    def commit(self, artifact: str) -> str:
        """The commit the entry records, or "" for one that holds only a
        refusal (a lane whose every attempt so far failed) or nothing usable."""
        commit = self.entry(artifact).get("commit")
        return commit if isinstance(commit, str) else ""

    def blobs(self, artifact: str) -> dict | None:
        """The blob ids the entry recorded, or None when it recorded none."""
        blobs = self.entry(artifact).get("blobs")
        return blobs if isinstance(blobs, dict) else None

    def byproducts(self, artifact: str | None = None) -> frozenset[str]:
        """The untracked files one artifact's run wrote, or every stamp's."""
        entries = [self.entry(artifact)] if artifact is not None else self.raw.values()
        return frozenset(name for entry in entries for name in _names_in(entry))

    def seconds(self, lane) -> float | None:
        return recorded_seconds(self.raw, lane)

    def refusal(self, artifact: str) -> Refusal:
        """Whether the file at `artifact` may be read as a measurement, asked
        once per command: the answer hashes the file when a refusal names it."""
        if artifact not in self._refusals:
            self._refusals[artifact] = self._refusal(artifact)
        return self._refusals[artifact]

    def _refusal(self, artifact: str) -> Refusal:
        path = self.root / artifact
        if not path.is_file():
            return NOT_REFUSED
        if self.state(artifact) in (LEGACY, RECORDED):
            return _entry_refusal(self.entry(artifact), path)
        if self._stored_refusals().get(artifact) == file_sha256(path):
            return LEFTOVER
        why = self.why_unreadable(artifact)
        return Refusal("unknown", why) if why else NOT_REFUSED

    def _stored_refusals(self) -> dict[str, str]:
        """The refusals the snapshot store keeps, read once: the record that
        outlives a deleted or unreadable stamp file."""
        if self._stored is None:
            self._stored = _store_refusals(self.root)
        return self._stored


def _entry_state(entry: object) -> str:
    if not isinstance(entry, dict):
        return MANGLED
    return RECORDED if isinstance(entry.get("blobs"), dict) else LEGACY


def _names_in(entry: object) -> list[str]:
    listed = entry.get("byproducts") if isinstance(entry, dict) else None
    return [name for name in listed if isinstance(name, str)] if isinstance(listed, list) else []


def _entry_refusal(entry: dict, path: Path) -> Refusal:
    """The refusal the entry records against the file on disk. A refusal keyed
    on the file's sha256 holds until other bytes are there: a touch, a copy
    that drops times, or a same-bytes rewrite leaves it standing. One crapkit
    0.8.0 recorded holds only a modification time, and is judged by it."""
    if "refused_sha256" in entry:
        return LEFTOVER if entry["refused_sha256"] == file_sha256(path) else NOT_REFUSED
    refused = entry.get("refused_mtime_ns")
    return LEFTOVER if refused is not None and refused == _mtime_ns(path) else NOT_REFUSED


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def file_sha256(path: Path) -> str:
    """The file's sha256, or "" when it cannot be read."""
    try:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    except OSError:
        return ""


def read(root: Path) -> Stamps:
    """The stamp file as it is now. A missing file is no stamps; one that does
    not parse, or whose top level is not an object, is unreadable and says so."""
    path = _path(root)
    if not path.is_file():
        return Stamps(root, {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Stamps(root, {}, "it does not parse as JSON")
    if not isinstance(data, dict):
        return Stamps(root, {}, "its top level is not an object")
    return Stamps(root, data)


def read_stamps(root: Path) -> dict:
    """The parsed file as a mapping, {} when it is missing or unreadable: the
    view doctor's duration and commit columns read."""
    return read(root).raw


def stamp_for(stamps: dict, artifact: str) -> dict:
    """One artifact's entry from `read_stamps`, or {} when there is none."""
    entry = stamps.get(artifact)
    return entry if isinstance(entry, dict) else {}


def _recorded_seconds(entry: object) -> float | None:
    """The duration one stamp recorded, or None when it has none to give."""
    value = entry.get("seconds") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _is_lane(entry: object, name: str) -> bool:
    return isinstance(entry, dict) and entry.get("lane") == name


def _named_seconds(stamps: dict, name: str) -> float | None:
    """The longest run any stamp recorded under this lane's NAME, or None when
    no stamp names it.

    Stamps are filed under the artifact PATH, so moving an artifact orphans its
    duration and the lane sorts as never-measured: the consumer repo renamed 12 of them
    and 11 of its 14 lanes read as zero seconds, which makes longest-first
    scheduling do nothing at all. Every stamp already names the lane that wrote
    it, so the name finds the record the path lost. Longest wins because the
    schedule only ever needs an upper bound on how long the lane can run.

    Durations only. Reuse and staleness still key on the exact artifact path:
    judging a NEW artifact's freshness by an OLD one's commit would hand back a
    stale coverage number, and a start order can never do that.
    """
    named = (_recorded_seconds(entry) for entry in stamps.values() if _is_lane(entry, name))
    return max((seconds for seconds in named if seconds is not None), default=None)


def recorded_seconds(stamps: dict, lane) -> float | None:
    """How long this lane took the last time it actually ran, or None when no
    stamp records it. The declared artifact is the exact record; the lane name
    is the fallback that survives a rename. The start order and doctor --tune
    both read this, so the two cannot disagree about a renamed artifact."""
    exact = _recorded_seconds(stamps.get(lane.artifact))
    return exact if exact is not None else _named_seconds(stamps, lane.name)


def refusal_entry(stamps: Stamps, lane, sha256: str) -> dict[str, dict]:
    """The entry that records the lane's artifact as the file its last attempt
    failed to replace, keyed by its sha256. The previous entry's fields ride
    along: its commit is still where that file was built, and its duration
    still orders parallel starts."""
    kept = {key: value for key, value in stamps.entry(lane.artifact).items()
            if key != "refused_mtime_ns"}
    return {lane.artifact: {**kept, "lane": lane.name, "refused_sha256": sha256}}


def write(root: Path, entries: dict[str, dict]) -> None:
    """Merge this run's entries into the stamp file in one atomic write, and
    mirror its refusals into the snapshot store.

    Dying before this point loses stamps but never fabricates one, so the worst
    a crash costs is a rerun of lanes that could have been reused. The text goes
    to a temporary file that replaces the old one in one step: a crash during a
    plain write left the file cut short, and with it the refusals it held.
    """
    fresh = {artifact: entry for artifact, entry in entries.items() if entry}
    if not fresh:
        return
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace_text(path, json.dumps({**read(root).raw, **fresh}, sort_keys=True, indent=1))
    _mirror_refusals(root, fresh)


def _replace_text(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _store_path(root: Path) -> Path:
    return root / ".crapkit" / "crap.sqlite"


def _mirror_refusals(root: Path, fresh: dict[str, dict]) -> None:
    """Record each new refusal in the store and drop the store's refusal of
    every artifact a run just measured. A store that does not exist yet is
    created only to hold a refusal."""
    refused = _refusals_in(fresh)
    if not refused and not _store_path(root).is_file():
        return
    from .store import SnapshotStore

    with closing(SnapshotStore(_store_path(root))) as store:
        store.clear_refusals([artifact for artifact in fresh if artifact not in refused])
        store.record_refusals(refused)


def _refusals_in(entries: dict[str, dict]) -> dict[str, str]:
    return {artifact: entry["refused_sha256"] for artifact, entry in entries.items()
            if "refused_sha256" in entry}


def _store_refusals(root: Path) -> dict[str, str]:
    if not _store_path(root).is_file():
        return {}
    from .store import SnapshotStore

    with closing(SnapshotStore(_store_path(root))) as store:
        return store.lane_refusals()
