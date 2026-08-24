"""The warm path: what a run does when the cache already answers every file.

On the consumer repo's 14k files a fully-warm inventory spent its time re-deriving things
it already had. Two of those are pinned here:

  re-stamping    every cached row was rebuilt with `_replace(path=...)` even when
                 the path had not moved (0.22 s of a 1.59 s run)
  file identity  every file was opened and read to the end to find out its
                 content had not changed (0.82 s of the same run)

Both are cache mechanics, so the tests check the mechanism (rows reused by
identity, files not opened) and never the clock.
"""
import os
import time
from pathlib import Path

import crapkit.analyze as analyze
from crapkit.analyze import analyze_files, analyze_one, content_hash, fingerprint
from crapkit.merge import FunctionRecord

HOUR_NS = 3600 * 1_000_000_000

SOURCE = ("export function f(a: number) {\n  if (a) { return 1; }\n  return 0;\n}\n"
          "export function g(b: number) {\n  return b;\n}\n")


def _rows(path: str) -> list[FunctionRecord]:
    return [FunctionRecord(path, "f ( a )", 1, 4, 2, 2, 2, 4, 1, 2, 1),
            FunctionRecord(path, "g ( b )", 5, 7, 1, 1, 1, 3, 1, 0, 0)]


def _repo(tmp_path: Path, rel: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SOURCE, encoding="utf-8")
    return tmp_path


def _cache(root: Path, rel: str, stamped_as: str) -> dict:
    return {"fp": fingerprint(),
            "entries": {content_hash(root / rel): _rows(stamped_as)}}


def test_a_cache_hit_whose_path_did_not_move_reuses_its_rows_untouched(tmp_path):
    """Identity, not equality: rebuilding 140k namedtuples to write back the path
    they already carry is the whole cost being removed."""
    root = _repo(tmp_path, "src/a.ts")
    cache = _cache(root, "src/a.ts", "src/a.ts")
    cached = cache["entries"][content_hash(root / "src/a.ts")]

    records, hits, _ = analyze_files(root, ["src/a.ts"], cache=cache)

    assert hits == 1
    assert records["src/a.ts"][0] is cached[0], "a row that did not move was rebuilt anyway"


def test_a_cache_hit_whose_file_moved_is_re_stamped_with_the_new_path(tmp_path):
    """The entry keys on content, so a copy or a rename lands on another file's
    rows. Their old path must not ride into the snapshot."""
    root = _repo(tmp_path, "src/new.ts")
    cache = _cache(root, "src/new.ts", "src/old.ts")

    records, hits, _ = analyze_files(root, ["src/new.ts"], cache=cache)

    assert hits == 1
    assert [r.path for r in records["src/new.ts"]] == ["src/new.ts", "src/new.ts"]


def test_two_paths_sharing_one_entry_each_get_their_own_path(tmp_path):
    """Identical content under two names is common (empty __init__.py, re-export
    shims): 5 entries on the consumer repo are shared by two or more paths."""
    root = _repo(tmp_path, "src/a.ts")
    (root / "src" / "b.ts").write_text(SOURCE, encoding="utf-8")
    cache = _cache(root, "src/a.ts", "src/a.ts")

    records, _, _ = analyze_files(root, ["src/a.ts", "src/b.ts"], cache=cache)

    assert {p: [r.path for r in rows] for p, rows in records.items()} == {
        "src/a.ts": ["src/a.ts", "src/a.ts"],
        "src/b.ts": ["src/b.ts", "src/b.ts"]}


def test_every_row_of_one_analyzed_file_carries_that_files_path(tmp_path):
    """The invariant the first-row check rests on, at the one place rows are
    born: analyze_one stamps every record it emits with the path it was given."""
    root = _repo(tmp_path, "src/a.ts")

    _, records = analyze_one((str(root / "src" / "a.ts"), "src/a.ts"))

    assert len(records) == 2
    assert {r.path for r in records} == {"src/a.ts"}


def test_an_entry_with_no_rows_is_a_hit_and_stays_empty(tmp_path):
    """2,315 of the consumer repo's 14,152 files have no functions at all."""
    root = tmp_path
    (root / "empty.ts").write_text("", encoding="utf-8")
    cache = {"fp": fingerprint(), "entries": {content_hash(root / "empty.ts"): []}}

    records, hits, _ = analyze_files(root, ["empty.ts"], cache=cache)

    assert (hits, records) == (1, {"empty.ts": []})


# --- the stat prefilter -------------------------------------------------------
#
# Reading 14k files end to end to learn that none of them changed cost 0.56 s of
# a 1.59 s warm run. A file whose (mtime_ns, size) is what it was when we hashed
# it keeps that hash without being opened. The cache keys stay content hashes:
# stat decides only whether the hash has to be recomputed.

ALPHA = "export function aa(x: number) { return x; }\n"
BETA = "export function bb(x: number) { return x; }\n"  # same byte length as ALPHA


def _settle(path: Path, when_ns: int) -> None:
    os.utime(path, ns=(when_ns, when_ns))


def _names(records: dict, rel: str) -> list[str]:
    return [r.long_name.split("(")[0].strip() for r in records[rel]]


def _write(root: Path, rel: str, text: str, mtime_ns: int) -> Path:
    path = root / rel
    path.write_text(text, encoding="utf-8")
    _settle(path, mtime_ns)
    return path


def test_a_file_whose_stat_has_not_moved_keeps_its_hash_without_being_read(tmp_path, monkeypatch):
    """The mechanism, not the clock: the second run cannot hash anything, and
    still answers."""
    settled = time.time_ns() - HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, settled)
    _, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})

    def refuse(path):
        raise AssertionError(f"an unchanged file was read and hashed: {path}")

    monkeypatch.setattr(analyze, "content_hash", refuse)
    records, hits, _ = analyze_files(tmp_path, ["a.ts"], cache=cache)

    assert (hits, _names(records, "a.ts")) == (1, ["aa"])


def test_a_changed_file_is_rehashed_and_reanalyzed(tmp_path):
    settled = time.time_ns() - HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, settled)
    _, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})

    _write(tmp_path, "a.ts", BETA, settled + 1000)
    records, hits, _ = analyze_files(tmp_path, ["a.ts"], cache=cache)

    assert (hits, _names(records, "a.ts")) == (0, ["bb"])


def test_a_file_whose_mtime_cannot_be_trusted_yet_is_never_stamped(tmp_path):
    """A stamp is only worth something once the file has held still. Windows
    moves a file time on a ~15 ms clock tick, so two writes inside one tick can
    share an mtime; a file written seconds ago, or one whose mtime is in the
    future, is not evidence and gets no stamp. Here the mtime is ahead of the
    clock, so the same-length rewrite underneath it is still caught."""
    ahead = time.time_ns() + HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, ahead)
    _, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})

    _write(tmp_path, "a.ts", BETA, ahead)  # identical stat, different content
    records, hits, _ = analyze_files(tmp_path, ["a.ts"], cache=cache)

    assert (hits, _names(records, "a.ts")) == (0, ["bb"])


def test_a_forced_stat_collision_serves_the_stale_hash_until_the_next_change(tmp_path):
    """The honest limit of the prefilter, written down.

    Rewriting a settled file with content of the same length AND putting its old
    mtime back is invisible to stat, so the stale record is served. Reaching it
    takes a deliberate os.utime backwards: a plain write moves the mtime, and a
    write within the mtime tick of the last one cannot be stamped in the first
    place (the test above). The next change with a real mtime recovers, so the
    error cannot outlive it.
    """
    settled = time.time_ns() - HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, settled)
    _, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})

    _write(tmp_path, "a.ts", BETA, settled)  # mtime dragged back to match
    stale, _, cache = analyze_files(tmp_path, ["a.ts"], cache=cache)
    assert _names(stale, "a.ts") == ["aa"], "documented: stat cannot see this edit"

    _write(tmp_path, "a.ts", BETA, settled + 1000)
    recovered, _, _ = analyze_files(tmp_path, ["a.ts"], cache=cache)

    assert _names(recovered, "a.ts") == ["bb"]


def test_the_stat_index_lives_under_the_crapkit_store(tmp_path):
    settled = time.time_ns() - HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, settled)

    analyze_files(tmp_path, ["a.ts"], cache={})

    written = sorted(p.name for p in (tmp_path / ".crapkit").iterdir())
    assert written == ["stat-stamps.json"]


def test_a_corrupt_stat_index_reads_as_no_index(tmp_path):
    settled = time.time_ns() - HOUR_NS
    _write(tmp_path, "a.ts", ALPHA, settled)
    _, _, cache = analyze_files(tmp_path, ["a.ts"], cache={})
    (tmp_path / ".crapkit" / "stat-stamps.json").write_text("{tru", encoding="utf-8")

    records, hits, _ = analyze_files(tmp_path, ["a.ts"], cache=cache)

    assert (hits, _names(records, "a.ts")) == (1, ["aa"])


def test_a_missing_file_still_raises_when_it_is_handed_to_the_analyzer(tmp_path):
    """The caller filters absent paths; a file that vanishes mid-run must not
    turn into a silent empty result."""
    import pytest

    with pytest.raises(OSError):
        analyze_files(tmp_path, ["gone.ts"], cache={})
