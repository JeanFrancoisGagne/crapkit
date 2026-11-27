"""The Cobertura adapter, read through coverage_format.lane_format(lane).

Each case writes a small report, or copies a recorded one, and reads it the
way a lane run does: each class's lines give per-file line and branch
evidence and no function records, classes that name one file merge by line,
and a report crapkit cannot read is refused naming the element. The bytes,
the memory a large report costs, the keys, the wrong-tree refusal and the
join at score's interface follow.
"""
import codecs
import hashlib
import io
import os
import tracemalloc
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.repopath import Unplaced
from crapkit.score import FileEvidence, join_evidence
from crapkit.snapshot import InventoryRow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "coverage" / "cobertura"
RECORDED = Path(__file__).parents[1] / "accuracy" / "coverage_oracles" / "recorded"


def _lane(path_prefix: str = "", scopes=("src",)) -> Lane:
    return Lane(name="cpp", command="gcovr --root . --cobertura cov.xml", artifact="cov.xml",
                parser="cobertura", scopes=scopes, path_prefix=path_prefix)


def _write(root: Path, raw: bytes) -> Path:
    artifact = root / "cov.xml"
    artifact.write_bytes(raw)
    return artifact


def _read(root: Path, raw: bytes, lane: Lane | None = None, unplaced=None):
    lane = lane or _lane()
    return coverage_format.lane_format(lane).read(lane, root, _write(root, raw),
                                                  unplaced=unplaced)


def _report(classes: str, sources: str = "<source>.</source>") -> bytes:
    return (f'<?xml version="1.0" ?>\n<coverage version="test"><sources>{sources}</sources>'
            f'<packages><package name="src"><classes>{classes}</classes></package></packages>'
            "</coverage>\n").encode("utf-8")


def _class(filename: str, *lines: str) -> str:
    return f'<class name="c" filename="{filename}"><methods/><lines>{"".join(lines)}</lines></class>'


def _refusal(root: Path, raw: bytes) -> ToolError:
    with pytest.raises(ToolError) as refused:
        _read(root, raw)
    return refused.value


def _evidence(hit, missed, branches=()) -> FileEvidence:
    return FileEvidence(array("I", hit), array("I", missed), tuple(branches))


# --- what a report reads as -----------------------------------------------------------------

CONTROL = _report(_class(
    "src/a.cpp", '<line number="1" hits="1" branch="false"/>',
    '<line number="2" hits="3" branch="true" condition-coverage="50% (1/2)">'
    '<conditions><condition number="0" type="jump" coverage="50%"/></conditions></line>',
    '<line number="3" hits="1"/>', '<line number="5" hits="0" branch="false"/>'))


def test_lines_and_conditions_read_into_line_and_branch_evidence_with_no_function_records(
        tmp_path):
    per_file, evidence, digest = _read(tmp_path, CONTROL)

    assert per_file == {"src/a.cpp": []}
    assert evidence == {"src/a.cpp": _evidence([1, 2, 3], [5], [(2, 2, 1)])}
    assert digest == hashlib.sha256(CONTROL).hexdigest()


def test_a_branch_line_with_no_condition_coverage_gives_line_evidence_only(tmp_path):
    raw = _report(_class("src/a.cpp", '<line number="2" hits="1" branch="true"/>',
                         '<line number="4" hits="0" branch="true"/>'))

    assert _read(tmp_path, raw)[1] == {"src/a.cpp": _evidence([2], [4])}


def test_a_method_element_is_not_read(tmp_path):
    """The join attributes the lines: a line only a <method> lists gives nothing."""
    raw = _report('<class filename="src/a.cpp"><methods><method name="f" signature="()V">'
                  '<lines><line number="9" hits="1"/></lines></method></methods>'
                  '<lines><line number="1" hits="1"/></lines></class>')

    assert _read(tmp_path, raw)[1] == {"src/a.cpp": _evidence([1], [])}


def test_one_filename_in_two_classes_merges_by_line(tmp_path):
    """A line is hit when any class hits it; its conditions take the larger
    covered count over the total both report."""
    raw = _report(
        _class("src/a.cpp", '<line number="1" hits="0"/>', '<line number="2" hits="1"/>',
               '<line number="4" hits="1" branch="true" condition-coverage="50% (1/2)"/>'),
        ) .replace(b"</classes>", _class(
            "src/a.cpp", '<line number="1" hits="2"/>', '<line number="3" hits="0"/>',
            '<line number="4" hits="1" branch="true" condition-coverage="25% (0/2)"/>',
            '<line number="6" hits="1" branch="true" condition-coverage="100% (2/2)"/>',
        ).encode() + b"</classes>")

    assert _read(tmp_path, raw)[1] == {"src/a.cpp": _evidence([1, 2, 4, 6], [3],
                                                              [(4, 2, 1), (6, 2, 2)])}


def test_two_classes_reporting_different_condition_totals_for_one_line_are_refused(tmp_path):
    raw = _report(_class("src/a.cpp", '<line number="4" hits="1" branch="true" '
                                      'condition-coverage="50% (1/2)"/>')
                  + _class("src/a.cpp", '<line number="4" hits="1" branch="true" '
                                        'condition-coverage="25% (1/4)"/>'))

    refusal = _refusal(tmp_path, raw)

    assert refusal.exit_code == 5
    assert str(refusal) == ("cobertura artifact cov.xml: src/a.cpp: line 4 reports 2 conditions "
                            "in one class and 4 in another; regenerate the artifact with the "
                            "coverage tool that wrote it")


def test_the_lines_no_test_ran_are_the_lines_with_hits_zero(tmp_path):
    adapter = coverage_format.lane_format(_lane())

    assert adapter.missing(_lane(), tmp_path, _write(tmp_path, CONTROL)) == {"src/a.cpp": {5}}


def test_cobertura_holds_no_per_line_test_contexts(tmp_path):
    adapter = coverage_format.lane_format(_lane())

    assert adapter.contexts(_lane(), tmp_path, _write(tmp_path, CONTROL), "src/a.cpp") == {}


@pytest.mark.parametrize("line, words", [
    ('<line number="3"/>', "line 3 has no hits"),
    ('<line number="3" hits="-1"/>', "line 3: hits '-1' is not a nonnegative integer"),
    ('<line number="3" hits="NaN"/>', "line 3: hits 'NaN' is not a nonnegative integer"),
    ('<line number="3" hits="inf"/>', "line 3: hits 'inf' is not a nonnegative integer"),
    ('<line hits="1"/>', "a line has no number"),
    ('<line number="x" hits="1"/>', "number 'x' is not a nonnegative integer"),
    ('<line number="3" hits="1" branch="yes"/>', "line 3: branch 'yes' is neither true nor false"),
    ('<line number="3" hits="1" branch="true" condition-coverage="half"/>',
     "line 3: condition-coverage 'half' is not a percentage and (covered/total)"),
    ('<line number="3" hits="1" branch="true" condition-coverage="150% (3/2)"/>',
     "line 3: condition-coverage '150% (3/2)' covers more conditions than it has"),
], ids=["dropped-hits", "negative", "nan", "inf", "no-number", "number-not-a-number",
        "branch-not-a-boolean", "condition-not-a-ratio", "covered-over-total"])
def test_a_line_crapkit_cannot_read_is_refused_naming_it_and_its_file(tmp_path, line, words):
    refusal = _refusal(tmp_path, _report(_class("src/a.cpp", line)))

    assert refusal.exit_code == 5
    assert str(refusal) == (f"cobertura artifact cov.xml: src/a.cpp: {words}; regenerate the "
                            "artifact with the coverage tool that wrote it")


def test_a_class_with_no_filename_is_refused(tmp_path):
    refusal = _refusal(tmp_path, _report('<class name="c"><lines/></class>'))

    assert str(refusal) == ("cobertura artifact cov.xml: a class names no filename; regenerate "
                            "the artifact with the coverage tool that wrote it")


@pytest.mark.parametrize("packages", ["<packages/>", "<packages></packages>"])
def test_a_report_with_no_class_measured_nothing(tmp_path, packages):
    raw = f'<?xml version="1.0" ?><coverage><sources/>{packages}</coverage>'.encode()

    refusal = _refusal(tmp_path, raw)

    assert refusal.exit_code == 5
    assert "cov.xml is empty (zero classes) - the coverage run measured nothing" in str(refusal)


# --- the bytes ------------------------------------------------------------------------------

LATIN1 = ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<coverage><sources><source>.</source>'
          '</sources><packages><package name="s"><classes><class filename="src/café.cpp">'
          '<lines><line number="1" hits="1"/></lines></class></classes></package></packages>'
          '</coverage>\n')


@pytest.mark.parametrize("raw", [
    LATIN1.encode("latin-1"),
    codecs.BOM_UTF16_LE + LATIN1.replace("ISO-8859-1", "UTF-16").encode("utf-16-le"),
    codecs.BOM_UTF8 + LATIN1.replace("ISO-8859-1", "UTF-8").encode("utf-8"),
], ids=["declared-latin-1", "utf-16-bom", "utf-8-bom"])
def test_the_declaration_and_the_byte_order_mark_decide_the_decode(tmp_path, raw):
    """The key is git's UTF-8 path, whatever bytes spelled it in the report."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "café.cpp").write_bytes(b"int f() { return 1; }\n")

    _, evidence, digest = _read(tmp_path, raw)

    assert evidence == {"src/café.cpp": _evidence([1], [])}
    assert digest == hashlib.sha256(raw).hexdigest()


def test_the_reader_holds_no_decode_literal():
    source = Path(coverage_format.lane_format(_lane()).__file__).read_text(encoding="utf-8")

    for literal in (".decode(", "utf-8", "utf_8", "encoding="):
        assert literal not in source


# --- recordings ------------------------------------------------------------------------------

# istanbul's cobertura reporter writes the absolute project root as its one
# <source>; the test puts the checkout there, so the report places as a user's would.
ISTANBUL_ROOT = b"/home/user/repo"


@pytest.mark.parametrize("producer, report, file, branches", [
    ("gcovr-7.2", RECORDED / "gcovr-7.2" / "call.xml", "cpp/shapes.cpp", True),
    ("coverage.py 7.16.1", RECORDED / "coveragepy-reports-7.16.1" / "call.xml", "py/shapes.py",
     True),
    ("gcovr-7.2 stanza", FIXTURES / "gcovr-7.2-stanza.xml", "src/shapes.cpp", True),
    ("nyc 18.0.0", FIXTURES / "nyc-18.0.0-call.xml", "js/shapes.js", True),
], ids=["gcovr", "coverage-xml", "gcovr-stanza", "istanbul-nyc"])
def test_each_recorded_report_reads_hit_and_missed_lines_and_conditions(
        tmp_path, producer, report, file, branches):
    raw = report.read_bytes().replace(ISTANBUL_ROOT, tmp_path.as_posix().encode())
    _, evidence, _ = _read(tmp_path, raw)

    assert file in evidence, producer
    read = evidence[file]
    lines = [line for cls in ET.fromstring(raw).iter("class") if cls.get("filename") == file
             for line in cls.findall("lines/line")]
    assert list(read.hit_lines) == sorted(int(line.get("number")) for line in lines
                                          if line.get("hits") != "0")
    assert list(read.missed_lines) == sorted(int(line.get("number")) for line in lines
                                             if line.get("hits") == "0")
    assert read.hit_lines and read.missed_lines
    assert bool(read.branches) is branches
    assert all(total >= covered for _, total, covered in read.branches)


def test_istanbul_reads_its_class_lines_and_not_its_method_lines(tmp_path):
    """nyc writes each function's first line under <methods> and every line,
    with condition-coverage and no <conditions>, under the class's <lines>."""
    raw = (FIXTURES / "nyc-18.0.0-call.xml").read_bytes()
    _, evidence, _ = _read(tmp_path, raw.replace(ISTANBUL_ROOT, tmp_path.as_posix().encode()))

    read = evidence["js/shapes.js"]
    class_lines = ET.fromstring(raw).findall("packages/package/classes/class/lines/line")
    assert len(read.hit_lines) + len(read.missed_lines) == len(class_lines) < raw.count(b"<line ")
    assert (4, 2, 1) in read.branches


# --- memory -------------------------------------------------------------------------------

class _Synthetic(io.RawIOBase):
    """`head`, then `body` `repeats` times over, then `tail`, made as it is
    read, so the report never sits on the heap or the disk."""

    def __init__(self, head: bytes, body: bytes, repeats: int, tail: bytes) -> None:
        self._parts = iter([head, *([body] * repeats), tail])
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


def _classes(files: int) -> bytes:
    """`files` classes of 200 lines (three in four hit, each with a long
    ignored attribute) and a two-condition branch on every twentieth line."""
    pad = b' pad="' + b"7" * 300 + b'"'
    out = []
    for index in range(files):
        out.append(b'<class name="c" filename="src/pkg%d/file%d.cpp"><lines>' % (index // 500, index))
        out += [b'<line number="%d" hits="%d"%s%s/>' % (
            line, line % 4, b' branch="true" condition-coverage="50% (1/2)"' if line % 20 == 1
            else b"", pad) for line in range(1, 201)]
        out.append(b"</lines></class>")
    return b"".join(out)


def test_a_200_mb_report_reads_under_32_mb_and_hashes_every_byte(monkeypatch):
    head = _report("").split(b"</classes>")[0]
    tail = b"</classes></package></packages></coverage>\n"
    body = _classes(1_000)
    repeats = 200_000_000 // len(body) + 1
    digest = hashlib.sha256(head)
    for _ in range(repeats):
        digest.update(body)
    digest.update(tail)
    module = coverage_format.lane_format(_lane())
    monkeypatch.setattr(module, "open", lambda path, mode: io.BufferedReader(
        _Synthetic(head, body, repeats, tail)), raising=False)

    tracemalloc.start()
    try:
        _, evidence, sha = module.read(_lane(), Path("."), Path("cov.xml"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(body) * repeats >= 200_000_000
    assert len(evidence) == 1_000
    assert evidence["src/pkg0/file7.cpp"] == _evidence(
        [line for line in range(1, 201) if line % 4], range(4, 201, 4),
        [(line, 2, 1) for line in range(1, 201, 20)])
    assert sha == digest.hexdigest()
    assert peak < 32 * 1000 * 1000


# --- keys ---------------------------------------------------------------------------------

def _one_key(root: Path, sources: str, filename: str = "src/a.cpp", path_prefix: str = "",
             unplaced=None) -> str:
    raw = _report(_class(filename, '<line number="1" hits="1"/>'), sources)
    (key,) = _read(root, raw, _lane(path_prefix), unplaced)[1]
    return key


def _checkout(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.cpp").write_text("int f() { return 1; }\n")
    return Path(os.path.realpath(root))


def test_an_absolute_source_under_the_checkout_keys_root_relative(tmp_path):
    root = _checkout(tmp_path)

    assert _one_key(root, f"<source>{root.as_posix()}</source>") == "src/a.cpp"
    assert _one_key(root, f"<source>{root.as_posix()}/src/</source>", "a.cpp") == "src/a.cpp"


def test_an_absolute_source_through_a_link_to_the_checkout_keys_root_relative(tmp_path):
    root = _checkout(tmp_path / "repo")
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot make a directory link here")

    assert _one_key(root, f"<source>{link.as_posix()}</source>") == "src/a.cpp"


@pytest.mark.skipif(os.name != "nt", reason="drive letters and junctions are Windows spellings")
def test_a_windows_source_in_a_lower_case_drive_or_through_a_junction_keys_root_relative(
        tmp_path):
    import subprocess

    root = _checkout(tmp_path / "repo")
    junction = tmp_path / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(root)], check=True,
                   capture_output=True)

    assert _one_key(root, f"<source>{str(root).lower()}</source>") == "src/a.cpp"
    assert _one_key(root, f"<source>{junction}</source>") == "src/a.cpp"


def test_a_relative_source_takes_path_prefix(tmp_path):
    assert _one_key(tmp_path, "<source>src</source>", "a.cpp", "native") == "native/src/a.cpp"
    assert _one_key(tmp_path, "<source>.</source>", path_prefix="native/") == "native/src/a.cpp"


def test_a_report_with_no_sources_takes_path_prefix_and_the_reported_entry(tmp_path):
    assert _one_key(tmp_path, "", "./src/a.cpp") == "src/a.cpp"
    assert _one_key(tmp_path, "", "src\\a.cpp", "native") == "native/src/a.cpp"


def test_the_first_source_under_which_the_file_exists_keys_it(tmp_path):
    root = _checkout(tmp_path / "repo")
    other = tmp_path / "elsewhere"
    other.mkdir()
    sources = f"<source>{other.as_posix()}</source><source>{root.as_posix()}</source>"
    unplaced: dict = {}

    assert _one_key(root, sources, unplaced=unplaced) == "src/a.cpp"
    assert unplaced == {}


@pytest.mark.parametrize("path_prefix", ["", "native"], ids=["no-prefix", "under-path-prefix"])
def test_a_filename_from_another_checkout_stays_unplaced_with_the_placing_reason(
        tmp_path, path_prefix):
    other = Path(os.path.realpath(tmp_path)) / "other"
    (other / "src").mkdir(parents=True)
    root = tmp_path / "repo"
    root.mkdir()
    unplaced: dict = {}

    key = _one_key(root, f"<source>{other.as_posix()}</source>", path_prefix=path_prefix,
                   unplaced=unplaced)

    assert key == f"{other.as_posix()}/src/a.cpp"
    assert unplaced == {key: Unplaced.ANOTHER_TREE}


@pytest.mark.parametrize("path_prefix", ["", "src"], ids=["no-prefix", "under-path-prefix"])
def test_the_lane_refuses_a_report_from_another_checkout_with_exit_5(tmp_path, path_prefix):
    """The prefix never joins an absolute source, and the run never scores
    every function untested at exit 0."""
    from crapkit.lanes import _judge_artifact_scope

    other = Path(os.path.realpath(tmp_path)) / "other"
    (other / "src").mkdir(parents=True)
    root = _checkout(tmp_path / "repo")
    lane = _lane(path_prefix)
    unplaced: dict = {}
    per_file = _read(root, _report(_class("src/a.cpp", '<line number="1" hits="1"/>'),
                                   f"<source>{other.as_posix()}</source>"), lane, unplaced)[0]

    with pytest.raises(ToolError) as refused:
        _judge_artifact_scope(lane, per_file, {"src": ("src",)}, root, unplaced)

    assert refused.value.exit_code == 5
    assert coverage_format.lane_format(lane).WRONG_TREE_FIX in str(refused.value)
    assert f"{other.as_posix()}/src/a.cpp" in str(refused.value)


def test_the_adapter_carries_its_fix_texts_and_no_producer_facts():
    adapter = coverage_format.lane_format(_lane())

    assert adapter.TAKES_PATH_PREFIX is True
    assert "outside this" in adapter.WRONG_TREE_FIX
    assert adapter.ABSOLUTE_FIX and adapter.UNMEASURED_READING
    assert (adapter.SHARD_GLOB, adapter.COMBINE_RECIPE, adapter.DROPPINGS) == (None, None, ())
    assert adapter.data_file(_lane()) is None


# --- the join at score's interface ----------------------------------------------------------

def _row(name: str, start: int, end: int) -> InventoryRow:
    return InventoryRow(scope="src", path="src/a.cpp", long_name=name, start=start, end=end,
                        ccn_std=2, ccn_mod=2, ccn=2, nloc=end - start + 1, params=0, nesting=0)


CPP = _report(_class(
    "src/a.cpp", '<line number="1" hits="1"/>', '<line number="2" hits="1"/>',
    '<line number="3" hits="0"/>', '<line number="4" hits="1"/>', '<line number="5" hits="1"/>',
    '<line number="8" hits="1"/>',
    '<line number="9" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
    '<line number="10" hits="0"/>'))


def test_a_lambdas_lines_score_the_lambda_and_conditions_score_branch_coverage(tmp_path):
    """outer (1-6) holds a lambda (3-4); branchy (8-11) has a condition line."""
    rows = [_row("outer", 1, 6), _row("outer::lambda", 3, 4), _row("branchy", 8, 11)]
    per_file, by_path, _ = _read(tmp_path, CPP)

    joined = {fn.name: fn for fn in join_evidence(rows, by_path["src/a.cpp"])}

    assert per_file == {"src/a.cpp": []}
    assert (joined["outer::lambda"].statements_total,
            joined["outer::lambda"].statements_covered) == (2, 1)
    assert (joined["outer"].statements_total, joined["outer"].statements_covered) == (3, 3)
    assert joined["outer"].branches_total == 0
    assert joined["outer"].coverage == 1.0
    assert (joined["branchy"].branches_total, joined["branchy"].branches_covered) == (2, 1)
    assert joined["branchy"].coverage == 0.5
