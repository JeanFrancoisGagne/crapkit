"""The Go coverprofile adapter through coverage_format: what a profile reads as.

Recorded profiles come from tests/fixtures/coverage/coverprofile (Go 1.26.8,
over go-repo/). The common promises every adapter keeps sit in
test_coverage_reader_contract.py.
"""
import codecs
import hashlib
import io
import shutil
import tracemalloc
from array import array
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.errors import ConfigError, ToolError
from crapkit.score import FileEvidence, join_evidence
from crapkit.snapshot import InventoryRow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "coverage" / "coverprofile"
REPO = FIXTURES / "go-repo"
SET, COUNT, ATOMIC, COVERPKG = (FIXTURES / f"go-1.26.8-{mode}.out"
                                for mode in ("set", "count", "atomic", "coverpkg"))
SHAPES = "shapes.go"
CALC = "internal/calc/calc.go"
PER_MODULE = ("declare one lane per module with cwd at the module, and test packages (./...), "
              "not files")


def _lane(cwd: str = "", artifact: str = "cov.out") -> Lane:
    return Lane(name="go", command="go test -coverprofile=cov.out ./...", artifact=artifact,
                parser="coverprofile", scopes=("go",), cwd=cwd)


def _module(root: Path, text: str = "module example.com/shapes\n\ngo 1.22\n",
            where: str = "") -> Path:
    (root / where).mkdir(parents=True, exist_ok=True)
    (root / where / "go.mod").write_bytes(text.encode("utf-8"))
    return root


def _write(root: Path, raw: bytes) -> Path:
    artifact = root / "cov.out"
    artifact.write_bytes(raw)
    return artifact


def _read(root: Path, raw: bytes, lane: Lane | None = None):
    lane = lane or _lane()
    return coverage_format.lane_format(lane).read(lane, root, _write(root, raw))


def _profile(*blocks: str, mode: str = "set") -> bytes:
    return "".join(f"{line}\n" for line in (f"mode: {mode}", *blocks)).encode("utf-8")


def _refusal(root: Path, raw: bytes) -> str:
    with pytest.raises(ToolError) as refused:
        _read(root, raw)
    assert refused.value.exit_code == 5
    return str(refused.value)


def _evidence(hit, missed) -> FileEvidence:
    return FileEvidence(array("I", hit), array("I", missed), ())


@pytest.fixture
def repo(tmp_path) -> Path:
    shutil.copytree(REPO, tmp_path, dirs_exist_ok=True)
    return tmp_path


# --- what a recorded profile reads as -----------------------------------------------------

# Worked by hand from go-1.26.8-set.out: every line of a numStmt-positive block
# is hit when a block over it ran. Line 12 sits in the hit `12.2,12.12` and the
# missed `12.12,14.3`, so it is hit; so is 32.
SHAPES_EVIDENCE = _evidence(
    [8, 9, 10, 11, 12, 15, 19, 20, 21, 22, 23, 24, 26, 30, 31, 32, 35, 37, 38, 39, 40, 41, 45,
     46, 47],
    [13, 14, 33, 34, 50, 51, 52, 53, 54])
CALC_EVIDENCE = _evidence([14, 15, 18], [6, 7, 8, 9, 10, 16, 17])


@pytest.mark.parametrize("profile", [SET, COUNT, ATOMIC], ids=["set", "count", "atomic"])
def test_set_count_and_atomic_profiles_of_one_run_read_the_same_evidence(repo, profile):
    raw = profile.read_bytes()

    per_file, evidence, digest = _read(repo, raw)

    assert per_file == {SHAPES: [], CALC: []}
    assert evidence == {SHAPES: SHAPES_EVIDENCE, CALC: CALC_EVIDENCE}
    assert digest == hashlib.sha256(raw).hexdigest()


def test_a_coverpkg_profile_merges_the_blocks_each_test_binary_lists(repo):
    """The shapes binary runs calc.Mul through Area, and the calc binary lists
    shapes.go with every count 0: the merge keeps every hit."""
    evidence = _read(repo, COVERPKG.read_bytes())[1]

    assert evidence[SHAPES] == SHAPES_EVIDENCE
    assert evidence[CALC] == _evidence([6, 7, 10, 14, 15, 18], [8, 9, 16, 17])


def test_the_lines_no_test_ran_are_the_missed_lines(repo):
    lane = _lane()
    adapter = coverage_format.lane_format(lane)

    missing = adapter.missing(lane, repo, _write(repo, SET.read_bytes()))

    assert missing == {SHAPES: set(SHAPES_EVIDENCE.missed_lines),
                       CALC: set(CALC_EVIDENCE.missed_lines)}


def test_a_coverprofile_holds_no_per_line_test_contexts(repo):
    lane = _lane()
    adapter = coverage_format.lane_format(lane)

    assert adapter.contexts(lane, repo, _write(repo, SET.read_bytes()), SHAPES) == {}


def test_a_block_with_no_statement_moves_nothing(tmp_path):
    raw = _profile("example.com/shapes/a.go:1.1,3.2 1 1", "example.com/shapes/a.go:4.1,6.2 0 0",
                   "example.com/shapes/b.go:1.1,2.2 0 1")

    assert _read(_module(tmp_path), raw)[1] == {"a.go": _evidence([1, 2, 3], [])}


@pytest.mark.parametrize("first, second, hit, missed", [
    ("1 1", "1 0", [1, 2, 3], [4]), ("1 0", "1 1", [3, 4], [1, 2]), ("1 0", "1 0", [], [1, 2, 3, 4])],
    ids=["first-hit", "second-hit", "neither"])
def test_a_line_two_blocks_share_is_hit_when_either_was_hit(tmp_path, first, second, hit, missed):
    """Lines 1-3 and 3-4: line 3 sits in both blocks."""
    raw = _profile(f"example.com/shapes/a.go:1.1,3.5 {first}",
                   f"example.com/shapes/a.go:3.5,4.2 {second}")

    assert _read(_module(tmp_path), raw)[1] == {"a.go": _evidence(hit, missed)}


# --- mode lines ---------------------------------------------------------------------------

def test_a_profile_with_no_mode_line_is_refused_by_name(tmp_path):
    raw = b"example.com/shapes/a.go:1.1,3.2 1 1\n"

    text = _refusal(_module(tmp_path), raw)

    assert "coverprofile artifact cov.out: the first line" in text
    assert "is not a mode line (mode: set, count or atomic)" in text


def test_a_mode_go_does_not_write_is_refused_by_name(tmp_path):
    text = _refusal(_module(tmp_path), _profile("example.com/shapes/a.go:1.1,2.2 1 1",
                                                mode="sets"))

    assert "mode 'sets' is not set, count or atomic" in text


def test_two_mode_lines_of_one_mode_read_as_one_profile(tmp_path):
    raw = _profile("example.com/shapes/a.go:1.1,2.2 1 0") + _profile(
        "example.com/shapes/a.go:1.1,2.2 1 3")

    assert _read(_module(tmp_path), raw)[1] == {"a.go": _evidence([1, 2], [])}


def test_two_mode_lines_of_different_modes_are_refused_by_name(tmp_path):
    raw = _profile("example.com/shapes/a.go:1.1,2.2 1 0") + _profile(
        "example.com/shapes/a.go:1.1,2.2 1 3", mode="count")

    text = _refusal(_module(tmp_path), raw)

    assert "a second mode line says mode: count where the first said mode: set" in text


# --- the conformance promises in this format's fields ---------------------------------------

@pytest.mark.parametrize("block, words", [
    ("example.com/shapes/a.go:1.1,2.2 1", "needs a position range, a statement count and a hit"),
    ("example.com/shapes/a.go:1.1,2.2 1 -1", "hit count '-1' is not a nonnegative integer"),
    ("example.com/shapes/a.go:1.1,2.2 1 NaN", "hit count 'NaN' is not a nonnegative integer"),
    ("example.com/shapes/a.go:1.1,2.2 -1 1", "statement count '-1' is not a nonnegative"),
    ("example.com/shapes/a.go 1 1", "a block needs a file and a position range"),
    ("example.com/shapes/a.go:1.1 1 1", "position range '1.1' is not <line>.<column>,"),
    (":1.1,2.2 1 1", "a block names no file"),
], ids=["dropped-counter", "negative-count", "nan-count", "negative-statements",
        "no-position-range", "half-a-position-range", "no-file"])
def test_a_block_crapkit_cannot_read_is_refused_naming_it(tmp_path, block, words):
    text = _refusal(_module(tmp_path), _profile(block))

    assert words in text and repr(block) in text
    assert "regenerate the artifact" in text


@pytest.mark.parametrize("raw, what", [(b"mode: set\n", "zero blocks"),
                                       (b"", "no mode line and zero blocks")],
                         ids=["mode-line-only", "zero-bytes"])
def test_a_profile_with_no_block_measured_nothing(tmp_path, raw, what):
    text = _refusal(_module(tmp_path), raw)

    assert f"coverprofile artifact cov.out is empty ({what}) - the coverage run measured " \
           "nothing; rerun the lane" in text


def test_a_utf8_byte_order_mark_is_read_past(repo):
    raw = codecs.BOM_UTF8 + SET.read_bytes()

    _, evidence, digest = _read(repo, raw)

    assert evidence == {SHAPES: SHAPES_EVIDENCE, CALC: CALC_EVIDENCE}
    assert digest == hashlib.sha256(raw).hexdigest()


# --- decode -------------------------------------------------------------------------------

def test_a_powershell_utf16_redirect_gets_covstreams_refusal_and_its_fix(repo):
    raw = codecs.BOM_UTF16_LE + SET.read_bytes().decode("ascii").encode("utf-16-le")

    text = _refusal(repo, raw)

    assert "cov.out is not UTF-8" in text and "UTF-16" in text
    assert "write the file with Out-File -Encoding utf8" in text


def test_a_byte_that_is_not_utf8_in_a_path_is_refused_naming_it(repo):
    raw = SET.read_bytes().replace(b"shapes.go", b"shap\xe9s.go", 1)

    text = _refusal(repo, raw)

    assert "cov.out is not UTF-8" in text and "byte e9 at offset" in text


def test_the_reader_holds_no_decode_literal():
    source = Path(coverage_format.lane_format(_lane()).__file__).read_text(encoding="utf-8")

    for literal in (".decode(", "utf-8", "utf_8", "encoding="):
        assert literal not in source


# --- go.mod -------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "module example.com/app // the app\n",
    'module "example.com/app"\n',
    "﻿module example.com/app\n",
    "// a header\n\nmodule example.com/app\n\ngo 1.22\n",
], ids=["comment", "quoted", "byte-order-mark", "leading-comment"])
def test_the_module_line_reads_through_comments_quotes_and_a_byte_order_mark(tmp_path, text):
    raw = _profile("example.com/app/x.go:1.1,2.2 1 1")

    assert set(_read(_module(tmp_path, text), raw)[1]) == {"x.go"}


def test_a_utf16_go_mod_is_refused_as_repotext_refuses_it(tmp_path):
    (tmp_path / "go.mod").write_bytes(codecs.BOM_UTF16_LE + "module example.com/app\n".encode(
        "utf-16-le"))

    with pytest.raises(ConfigError) as refused:
        _read(tmp_path, _profile("example.com/app/x.go:1.1,2.2 1 1"))

    assert "go.mod is not UTF-8" in str(refused.value)


def test_a_go_mod_with_no_module_line_is_refused(tmp_path):
    text = _refusal(_module(tmp_path, "go 1.22\n"), _profile("example.com/app/x.go:1.1,2.2 1 1"))

    assert "go.mod has no module directive" in text


def test_a_lane_whose_cwd_has_no_go_mod_above_it_is_refused_with_the_per_module_fix(tmp_path):
    text = _refusal(tmp_path, _profile("example.com/app/x.go:1.1,2.2 1 1"))

    assert "lane 'go': no go.mod at its cwd '.' or above it in this checkout" in text
    assert PER_MODULE in text


# --- keys ---------------------------------------------------------------------------------

@pytest.mark.parametrize("cwd", ["svc", "svc/cmd"])
def test_an_import_path_keys_under_the_module_directory(tmp_path, cwd):
    _module(tmp_path, "module example.com/app\n", "svc")
    (tmp_path / "svc" / "cmd").mkdir()
    raw = _profile("example.com/app/internal/x.go:1.1,2.2 1 1")

    assert set(_read(tmp_path, raw, _lane(cwd))[1]) == {"svc/internal/x.go"}


def test_a_cgo_generated_file_names_no_file_and_moves_no_score(tmp_path):
    _module(tmp_path, "module example.com/app\n")
    (tmp_path / "x.go").write_text("package app\n")
    raw = _profile("example.com/app/x.go:1.1,2.2 1 1", "example.com/app/_cgo_gotypes.go:1.1,9.2 1 0")
    rows = [InventoryRow(scope="go", path="x.go", long_name="f", start=1, end=2, ccn_std=1,
                         ccn_mod=1, ccn=1, nloc=2, params=0, nesting=0)]

    evidence = _read(tmp_path, raw)[1]
    alone = _read(tmp_path, _profile("example.com/app/x.go:1.1,2.2 1 1"))[1]

    assert set(evidence) == {"x.go", "_cgo_gotypes.go"}
    assert not (tmp_path / "_cgo_gotypes.go").exists()
    assert evidence["x.go"] == alone["x.go"]
    assert [fn.coverage for fn in join_evidence(rows, evidence["x.go"])] == [1.0]


@pytest.mark.parametrize("written", ["example.com/other/y.go", "command-line-arguments/y.go"])
def test_an_import_path_outside_the_module_stays_unplaced_and_the_lane_says_why(
        tmp_path, capsys, written):
    _module(tmp_path, "module example.com/app\n")
    raw = _profile("example.com/app/x.go:1.1,2.2 1 1", f"{written}:1.1,2.2 1 1")

    evidence = _read(tmp_path, raw)[1]

    assert set(evidence) == {"x.go", written}
    assert (f"crapkit: lane 'go': 1 of the 2 file(s) in cov.out sit outside module example.com/app "
            f"({written}), so no function scores their lines; {PER_MODULE}") in capsys.readouterr().err


def test_the_scope_check_reading_names_one_lane_per_module_and_packages():
    adapter = coverage_format.lane_format(_lane())

    assert PER_MODULE in adapter.UNMEASURED_READING
    assert "go.mod" in adapter.WRONG_TREE_FIX and "import paths" in adapter.ABSOLUTE_FIX
    assert adapter.TAKES_PATH_PREFIX is False
    assert (adapter.SHARD_GLOB, adapter.COMBINE_RECIPE, adapter.DROPPINGS) == (None, None, ())
    assert adapter.data_file(_lane()) is None


# --- the join at score's interface ----------------------------------------------------------

def _row(name: str, start: int, end: int) -> InventoryRow:
    return InventoryRow(scope="go", path=SHAPES, long_name=name, start=start, end=end,
                        ccn_std=2, ccn_mod=2, ccn=2, nloc=end - start + 1, params=0, nesting=0)


def test_a_func_literals_lines_score_its_own_span_and_every_function_scores_statements(repo):
    """Scaled 30-42 holds the func literal scale on 31-36."""
    rows = [_row("Classify", 8, 16), _row("Scaled", 30, 42), _row("Scaled$1", 31, 36),
            _row("Unused", 50, 55)]
    per_file, evidence, _ = _read(repo, SET.read_bytes())

    joined = {fn.name: fn for fn in join_evidence(rows, evidence[SHAPES])}

    assert per_file[SHAPES] == []
    assert (joined["Scaled$1"].statements_total, joined["Scaled$1"].statements_covered) == (5, 3)
    assert (joined["Scaled"].statements_total, joined["Scaled"].statements_covered) == (6, 6)
    assert (joined["Classify"].statements_total, joined["Classify"].statements_covered) == (8, 6)
    assert joined["Unused"].coverage == 0.0
    for fn in joined.values():
        assert fn.branches_total == 0
        assert fn.coverage == fn.statements_covered / fn.statements_total


# --- memory -------------------------------------------------------------------------------

class _Synthetic(io.RawIOBase):
    """`head`, then `body` `repeats` times over, made as it is read, so the
    profile never sits on the heap or the disk."""

    def __init__(self, head: bytes, body: bytes, repeats: int) -> None:
        self._parts = iter([head, *([body] * repeats)])
        self._now, self._at = b"", 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while self._at == len(self._now):
            self._now, self._at = next(self._parts, None), 0
            if self._now is None:
                self._now = b""
                return 0
        take = min(len(buffer), len(self._now) - self._at)
        buffer[:take] = self._now[self._at:self._at + take]
        self._at += take
        return take


def _packages(files: int) -> bytes:
    """`files` files of 50 four-line blocks (three in four hit), each under a
    long package path, as a -coverpkg run of many binaries repeats them."""
    pad = b"d" * 200
    return b"".join(b"example.com/app/%s/pkg%d/f.go:%d.2,%d.9 2 %d\n" % (
        pad, index, line, line + 3, 1 if (line - 1) % 16 else 0)
        for index in range(files) for line in range(1, 200, 4))


def test_a_200_mb_profile_reads_under_32_mb_and_hashes_every_byte(tmp_path, monkeypatch):
    _module(tmp_path, "module example.com/app\n")
    head = b"mode: set\n"
    body = _packages(1_000)
    repeats = 200_000_000 // len(body) + 1
    digest = hashlib.sha256(head)
    for _ in range(repeats):
        digest.update(body)
    module = coverage_format.lane_format(_lane())
    monkeypatch.setattr(module, "open", lambda path, mode: io.BufferedReader(
        _Synthetic(head, body, repeats)), raising=False)

    tracemalloc.start()
    try:
        _, evidence, sha = module.read(_lane(), tmp_path, Path("cov.out"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(body) * repeats >= 200_000_000
    assert len(evidence) == 1_000
    missed = [line for start in range(1, 200, 16) for line in range(start, start + 4)]
    assert evidence[f"{'d' * 200}/pkg7/f.go"] == _evidence(
        sorted(set(range(1, 201)) - set(missed)), missed)
    assert sha == digest.hexdigest()
    assert peak < 32 * 1000 * 1000
