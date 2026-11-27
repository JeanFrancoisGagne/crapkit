"""The lcov adapter, read through coverage_format.lane_format(lane).

Each case writes a small tracefile and reads it the way a lane run does: the
records give per-file line and branch evidence and no function records, a
second record for one SF key merges, and a tracefile crapkit cannot read is
refused naming the record. The bytes, the memory a large tracefile costs, the
keys, the wrong-tree record and the join at score's interface follow.
"""
import codecs
import hashlib
import io
import os
import tracemalloc
from array import array
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.repopath import Unplaced
from crapkit.score import FileEvidence, join_evidence
from crapkit.snapshot import InventoryRow


def _lane(path_prefix: str = "") -> Lane:
    return Lane(name="rust", command="cargo llvm-cov --lcov --output-path lcov.info",
                artifact="lcov.info", parser="lcov", scopes=("src",), path_prefix=path_prefix)


def _write(root: Path, text: str) -> Path:
    artifact = root / "lcov.info"
    artifact.write_bytes(text.encode("utf-8"))
    return artifact


def _read(root: Path, text: str, lane: Lane | None = None, unplaced=None):
    lane = lane or _lane()
    return coverage_format.lane_format(lane).read(lane, root, _write(root, text),
                                                  unplaced=unplaced)


def _refusal(root: Path, text: str) -> str:
    with pytest.raises(ToolError) as refused:
        _read(root, text)
    return str(refused.value)


def _evidence(hit, missed, branches=()) -> FileEvidence:
    return FileEvidence(array("I", hit), array("I", missed), tuple(branches))


# Every record kind lcov 1.x and 2.x write, around one function on lines 1-6.
EVERY_RECORD = """TN:unit
SF:src/a.rs
FN:1,6,f
FNDA:3,f
FNL:0,1,6
FNA:0,3,f
FNF:1
FNH:1
DA:1,3
DA:2,3,5Hd9xJ7a
DA:3,0
DA:5,1
LF:4
LH:3
BRDA:2,0,0,2
BRDA:2,0,1,0
BRDA:5,0,0,-
BRDA:5,0,1,-
BRF:4
BRH:1
end_of_record
"""


def test_every_record_kind_reads_into_line_and_branch_evidence_with_no_function_records(tmp_path):
    per_file, evidence, digest = _read(tmp_path, EVERY_RECORD)

    assert per_file == {"src/a.rs": []}
    assert evidence == {"src/a.rs": _evidence([1, 2, 5], [3], [(2, 2, 1), (5, 2, 0)])}
    assert digest == hashlib.sha256(EVERY_RECORD.encode()).hexdigest()


def test_an_arm_whose_block_never_ran_reads_as_not_taken(tmp_path):
    text = "SF:src/a.rs\nDA:1,1\nBRDA:1,0,0,-\nBRDA:1,0,1,1\nend_of_record\n"

    assert _read(tmp_path, text)[1]["src/a.rs"].branches == ((1, 2, 1),)


def test_lcov1_function_records_and_tn_lines_give_no_evidence(tmp_path):
    text = "TN:\nSF:src/a.c\nFN:4,main\nFNDA:0,main\nDA:4,0\nend_of_record\nTN:other\n"

    assert _read(tmp_path, text)[1] == {"src/a.c": _evidence([], [4])}


def test_a_function_name_with_commas_reads(tmp_path):
    text = "SF:src/a.cpp\nFN:3,7,pair<int, int> make()\nFNDA:1,pair<int, int> make()\nDA:3,1\nend_of_record\n"

    assert _read(tmp_path, text)[1] == {"src/a.cpp": _evidence([3], [])}


@pytest.mark.parametrize("path_prefix, sf, key", [
    ("", "src/a.rs", "src/a.rs"),
    ("crates/core", "src/lib.rs", "crates/core/src/lib.rs"),
], ids=["no-prefix", "under-path-prefix"])
def test_the_lines_no_test_ran_are_the_da_lines_with_count_zero(tmp_path, path_prefix, sf, key):
    lane = _lane(path_prefix)
    artifact = _write(tmp_path, EVERY_RECORD.replace("SF:src/a.rs", f"SF:{sf}"))

    assert coverage_format.lane_format(lane).missing(lane, tmp_path, artifact) == {key: {3}}


def test_lcov_holds_no_per_line_test_contexts(tmp_path):
    artifact = _write(tmp_path, EVERY_RECORD)

    assert coverage_format.lane_format(_lane()).contexts(_lane(), tmp_path, artifact,
                                                         "src/a.rs") == {}


# --- one SF key in two records ------------------------------------------------------

def test_one_key_in_two_records_merges_hits_and_arms(tmp_path):
    text = ("SF:src/a.rs\nDA:1,1\nDA:2,0\nDA:3,0\nBRDA:2,0,0,0\nBRDA:2,0,1,1\nend_of_record\n"
            "SF:src/b.rs\nDA:1,0\nend_of_record\n"
            "SF:src/a.rs\nDA:1,0\nDA:2,4\nDA:3,0\nBRDA:2,0,0,1\nBRDA:2,0,1,0\nend_of_record\n")

    evidence = _read(tmp_path, text)[1]

    assert evidence == {"src/a.rs": _evidence([1, 2], [3], [(2, 2, 2)]),
                        "src/b.rs": _evidence([], [1])}


def test_two_spellings_of_one_file_merge_under_its_key(tmp_path):
    text = "SF:./src/a.rs\nDA:1,1\nDA:2,0\nend_of_record\nSF:src/a.rs\nDA:2,1\nend_of_record\n"

    assert _read(tmp_path, text)[1] == {"src/a.rs": _evidence([1, 2], [])}


# --- refusals -------------------------------------------------------------------------

def test_a_record_cut_off_before_end_of_record_is_refused_by_name(tmp_path):
    text = "SF:src/a.rs\nDA:1,1\nend_of_record\nSF:src/b.rs\nDA:1,1\n"

    with pytest.raises(ToolError) as refused:
        _read(tmp_path, text)

    assert refused.value.exit_code == 5
    assert str(refused.value).startswith(
        "lcov artifact lcov.info: the record for SF:src/b.rs has no end_of_record")


def test_a_second_sf_inside_an_open_record_is_refused_as_unclosed(tmp_path):
    text = "SF:src/a.rs\nDA:1,1\nSF:src/b.rs\nDA:1,1\nend_of_record\n"

    assert "the record for SF:src/a.rs has no end_of_record" in _refusal(tmp_path, text)


REFUSED = {
    "dropped-counter": ("DA:3", "SF:src/a.rs: DA:3: a DA record needs a line and a count"),
    "negative-count": ("DA:3,-1", "DA:3,-1: count '-1' is not a nonnegative integer"),
    "nan-count": ("DA:3,nan", "DA:3,nan: count 'nan' is not a nonnegative integer"),
    "line-not-a-number": ("DA:x,1", "DA:x,1: line 'x' is not a nonnegative integer"),
    "infinite-taken": ("BRDA:3,0,0,inf", "BRDA:3,0,0,inf: taken count 'inf' is not"),
    "short-brda": ("BRDA:3,0,1", "BRDA:3,0,1: a BRDA record needs a line, a block"),
    "fn-line": ("FN:a,f", "FN:a,f: line 'a' is not a nonnegative integer"),
    "fn-no-name": ("FN:1", "FN:1: a FN record needs a line and a name"),
    "fnda-count": ("FNDA:x,f", "FNDA:x,f: count 'x' is not a nonnegative integer"),
    "fnl-line": ("FNL:0,x", "FNL:0,x: line 'x' is not a nonnegative integer"),
    "fna-count": ("FNA:0,x,f", "FNA:0,x,f: count 'x' is not a nonnegative integer"),
}


@pytest.mark.parametrize("record, words", list(REFUSED.values()), ids=list(REFUSED))
def test_a_record_in_the_wrong_shape_is_refused_naming_it_and_its_file(tmp_path, record, words):
    text = _refusal(tmp_path, f"SF:src/a.rs\n{record}\nend_of_record\n")

    assert text.startswith("lcov artifact lcov.info: "), text
    assert words in text, text
    assert "regenerate the artifact" in text


def test_a_record_before_any_sf_is_refused(tmp_path):
    text = _refusal(tmp_path, "DA:1,1\nSF:src/a.rs\nend_of_record\n")

    assert "a DA record comes before any SF record" in text


def test_an_sf_record_with_no_path_is_refused(tmp_path):
    assert "an SF record names no file" in _refusal(tmp_path, "SF:\nDA:1,1\nend_of_record\n")


@pytest.mark.parametrize("text", ["", "TN:\n", "\n\n"], ids=["empty", "tn-only", "blank-lines"])
def test_a_tracefile_with_no_sf_record_measured_nothing(tmp_path, text):
    refused = _refusal(tmp_path, text)

    assert "lcov artifact lcov.info is empty (zero SF records)" in refused
    assert "measured nothing" in refused and "rerun the lane" in refused


# --- bytes ------------------------------------------------------------------------------

def test_a_utf8_byte_order_mark_and_crlf_endings_read_past(tmp_path):
    raw = codecs.BOM_UTF8 + EVERY_RECORD.replace("\n", "\r\n").encode()
    artifact = tmp_path / "lcov.info"
    artifact.write_bytes(raw)

    per_file, evidence, digest = coverage_format.lane_format(_lane()).read(_lane(), tmp_path,
                                                                           artifact)

    assert evidence == _read(tmp_path, EVERY_RECORD)[1]
    assert digest == hashlib.sha256(raw).hexdigest()


def test_a_utf16_tracefile_gets_the_decode_refusal_and_its_fix(tmp_path):
    """What `llvm-cov export ... > x.lcov` writes under Windows PowerShell 5.1."""
    artifact = tmp_path / "lcov.info"
    artifact.write_bytes(codecs.BOM_UTF16_LE + EVERY_RECORD.encode("utf-16-le"))

    with pytest.raises(ToolError) as refused:
        coverage_format.lane_format(_lane()).read(_lane(), tmp_path, artifact)

    text = str(refused.value)
    assert "lcov.info is not UTF-8" in text and "UTF-16" in text
    assert "Out-File -Encoding utf8" in text


def test_a_byte_that_is_not_utf8_in_an_sf_path_is_refused_naming_it(tmp_path):
    artifact = tmp_path / "lcov.info"
    artifact.write_bytes(b"SF:src/caf\xe9.rs\nDA:1,1\nend_of_record\n")

    with pytest.raises(ToolError) as refused:
        coverage_format.lane_format(_lane()).read(_lane(), tmp_path, artifact)

    assert "lcov.info is not UTF-8" in str(refused.value)
    assert "byte e9" in str(refused.value)


def test_the_reader_holds_no_decode_literal():
    source = Path(coverage_format.lane_format(_lane()).__file__).read_text(encoding="utf-8")

    for literal in (".decode(", "utf-8", "utf_8", "encoding="):
        assert literal not in source


# --- memory -------------------------------------------------------------------------------

class _Synthetic(io.RawIOBase):
    """`block` read `repeats` times over, made as it is read, so the tracefile
    never sits on the heap or the disk."""

    def __init__(self, block: bytes, repeats: int) -> None:
        self._block, self._left, self._at = block, repeats, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if not self._left:
            return 0
        take = min(len(buffer), len(self._block) - self._at)
        buffer[:take] = self._block[self._at:self._at + take]
        self._at += take
        if self._at == len(self._block):
            self._at, self._left = 0, self._left - 1
        return take


def _section(files: int, checksum: bytes = b"") -> bytes:
    """`files` records of 200 DA lines (three in four hit) and 20 BRDA arms
    (two on every twentieth line)."""
    out = [b"TN:run\n"]
    for index in range(files):
        out.append(b"SF:src/pkg%d/file%d.rs\n" % (index // 500, index))
        out += [b"DA:%d,%d%s\n" % (line, line % 4, checksum) for line in range(1, 201)]
        out += [b"BRDA:%d,0,%d,%d\n" % (line, arm, arm) for line in range(1, 201, 20)
                for arm in (0, 1)]
        out.append(b"LF:200\nLH:150\nBRF:20\nBRH:10\nend_of_record\n")
    return b"".join(out)


def _peak_read(monkeypatch, block: bytes, repeats: int):
    """read() over the synthetic tracefile: its evidence, its digest and the
    tracemalloc peak while it ran."""
    module = coverage_format.lane_format(_lane())
    monkeypatch.setattr(module, "open", lambda path, mode: io.BufferedReader(
        _Synthetic(block, repeats)), raising=False)
    tracemalloc.start()
    try:
        _, evidence, digest = module.read(_lane(), Path("."), Path("lcov.info"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return evidence, digest, peak


def test_a_200_mb_tracefile_reads_under_32_mb_and_hashes_every_byte(monkeypatch):
    """1,000 files whose DA lines carry a long checksum, read over and over to
    200 MB, so each later pass merges into the records the first one closed."""
    block = _section(1_000, checksum=b"," + b"7" * 400)
    repeats = 200_000_000 // len(block) + 1
    digest = hashlib.sha256()
    for _ in range(repeats):
        digest.update(block)

    evidence, sha, peak = _peak_read(monkeypatch, block, repeats)

    assert len(block) * repeats >= 200_000_000
    assert len(evidence) == 1_000
    assert evidence["src/pkg0/file7.rs"] == _evidence(
        [line for line in range(1, 201) if line % 4], range(4, 201, 4),
        [(line, 2, 1) for line in range(1, 201, 20)])
    assert sha == digest.hexdigest()
    assert peak < 32 * 1000 * 1000


# test_score_evidence_join measures evidence_by_path for a 31,459-file tree at
# 200 lines per file at 80.3 MiB, built straight into FileEvidence. A tracefile
# of that tree reads inside the same budget: the reader holds each closed
# record compact and builds no per-line or per-arm object past its record.
EVIDENCE_BUDGET = 80.3 * 2 ** 20


def test_a_31459_file_tracefile_reads_inside_the_evidence_budget(monkeypatch, capsys):
    evidence, _, peak = _peak_read(monkeypatch, _section(31_459), 1)

    with capsys.disabled():
        print(f"\nlcov read of 31,459 files at 200 DA lines and 20 BRDA arms: "
              f"tracemalloc peak {peak / 2 ** 20:.1f} MiB")
    assert len(evidence) == 31_459
    assert peak < EVIDENCE_BUDGET


# --- keys ---------------------------------------------------------------------------------

def _one_key(root: Path, sf: str, path_prefix: str = "", unplaced=None) -> str:
    text = f"SF:{sf}\nDA:1,1\nend_of_record\n"
    (key,) = _read(root, text, _lane(path_prefix), unplaced)[1]
    return key


def test_a_relative_key_takes_path_prefix(tmp_path):
    assert _one_key(tmp_path, "src/a.rs", "crates/core") == "crates/core/src/a.rs"
    assert _one_key(tmp_path, "src/a.rs", "crates/core/") == "crates/core/src/a.rs"


def test_a_dot_slash_key_loses_its_dot(tmp_path):
    assert _one_key(tmp_path, "./src/a.c") == "src/a.c"
    assert _one_key(tmp_path, ".\\src\\a.c") == "src/a.c"


def test_a_key_under_the_checkout_becomes_root_relative(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.rs").write_text("fn f() {}\n")
    root = Path(os.path.realpath(tmp_path))

    assert _one_key(root, f"{root.as_posix()}/src/a.rs") == "src/a.rs"
    assert _one_key(root, f"{root.as_posix()}/src/a.rs", "crates/core") == "src/a.rs"


def test_a_relative_key_in_another_letter_case_takes_the_listed_case(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.rs").write_text("fn f() {}\n")
    folds = (tmp_path / "SRC").exists()

    assert _one_key(tmp_path, "SRC/a.rs") == ("src/a.rs" if folds else "SRC/a.rs")


@pytest.mark.skipif(os.name != "nt", reason="drive letters and \\\\?\\ are Windows spellings")
@pytest.mark.parametrize("spell", [str.lower, lambda s: "\\\\?\\" + s],
                         ids=["lower-case-drive", "extended-length"])
def test_a_windows_spelling_of_the_checkout_becomes_root_relative(tmp_path, spell):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.rs").write_text("fn f() {}\n")
    root = Path(os.path.realpath(tmp_path))

    assert _one_key(root, spell(str(root / "src" / "a.rs"))) == "src/a.rs"


def test_a_link_to_the_checkout_becomes_root_relative(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.rs").write_text("fn f() {}\n")
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot make a directory link here")

    assert _one_key(root, f"{link.as_posix()}/src/a.rs") == "src/a.rs"


@pytest.mark.parametrize("path_prefix", ["", "crates/core"], ids=["no-prefix", "under-path-prefix"])
def test_a_key_from_another_tree_stays_as_written_with_the_placing_reason(tmp_path, path_prefix):
    other = tmp_path / "other"
    (other / "src").mkdir(parents=True)
    root = tmp_path / "repo"
    root.mkdir()
    written = f"{Path(os.path.realpath(other)).as_posix()}/src/a.rs"
    unplaced: dict = {}

    key = _one_key(root, written, path_prefix, unplaced)

    assert key == written
    assert unplaced == {written: Unplaced.ANOTHER_TREE}


def test_the_adapter_carries_its_fix_texts_and_no_producer_facts():
    adapter = coverage_format.lane_format(_lane())

    assert adapter.TAKES_PATH_PREFIX is True
    assert "outside this checkout" in adapter.WRONG_TREE_FIX
    assert adapter.ABSOLUTE_FIX and adapter.UNMEASURED_READING
    assert (adapter.SHARD_GLOB, adapter.COMBINE_RECIPE, adapter.DROPPINGS) == (None, None, ())
    assert adapter.data_file(_lane()) is None


# --- the join at score's interface ----------------------------------------------------------

def _row(name: str, start: int, end: int) -> InventoryRow:
    return InventoryRow(scope="src", path="src/lib.rs", long_name=name, start=start, end=end,
                        ccn_std=2, ccn_mod=2, ccn=2, nloc=end - start + 1, params=0, nesting=0)


RUST = """SF:src/lib.rs
DA:1,1
DA:2,1
DA:3,0
DA:4,1
DA:5,1
DA:8,1
DA:9,0
DA:10,1
BRDA:9,0,0,1
BRDA:9,0,1,0
end_of_record
"""


def test_a_closures_lines_score_the_closure_and_not_the_function_that_holds_it(tmp_path):
    """outer (1-6) holds a closure (3-4); branchy (8-11) has BRDA arms."""
    rows = [_row("outer", 1, 6), _row("outer::{closure}", 3, 4), _row("branchy", 8, 11)]
    (evidence,) = _read(tmp_path, RUST)[1].values()

    joined = {fn.name: fn for fn in join_evidence(rows, evidence)}

    assert (joined["outer::{closure}"].statements_total,
            joined["outer::{closure}"].statements_covered) == (2, 1)
    assert (joined["outer"].statements_total, joined["outer"].statements_covered) == (3, 3)
    assert joined["outer"].branches_total == 0
    assert joined["outer"].coverage == 1.0
    assert (joined["branchy"].branches_total, joined["branchy"].branches_covered) == (2, 1)
    assert joined["branchy"].coverage == 0.5
