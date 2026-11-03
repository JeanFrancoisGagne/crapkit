"""The JaCoCo adapter through coverage_format: what a report reads as.

Recorded reports come from tests/fixtures/coverage/jacoco (Gradle 8.10.2 and
Maven 3.9.9, JaCoCo 0.8.12, over java-repo/). The common promises every adapter
keeps sit in test_coverage_reader_contract.py.
"""
import codecs
import hashlib
import io
import socket
import tracemalloc
from array import array
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.score import FileEvidence, join_evidence
from crapkit.snapshot import InventoryRow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "coverage" / "jacoco"
GRADLE = FIXTURES / "gradle-8.10.2.xml"
MAVEN = FIXTURES / "maven-3.9.9.xml"
SHAPES = "src/main/java/com/acme/Shapes.java"
GREETER = "src/main/java/Greeter.java"
HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><!DOCTYPE report PUBLIC '
        '"-//JACOCO//DTD Report 1.1//EN" "report.dtd"><report name="r">'
        '<sessioninfo id="s" start="0" dump="0"/>')


def _lane(path_prefix: str = "src/main/java") -> Lane:
    return Lane(name="java", command="gradle test jacocoTestReport", artifact="cov.xml",
                parser="jacoco", scopes=("java",), path_prefix=path_prefix)


def _write(root: Path, raw: bytes) -> Path:
    artifact = root / "cov.xml"
    artifact.write_bytes(raw)
    return artifact


def _read(root: Path, raw: bytes, lane: Lane | None = None, unplaced=None):
    lane = lane or _lane()
    return coverage_format.lane_format(lane).read(lane, root, _write(root, raw),
                                                  unplaced=unplaced)


def _report(packages: str) -> bytes:
    return f"{HEAD}{packages}</report>".encode("utf-8")


def _package(name: str, *sourcefiles: str) -> str:
    return f'<package name="{name}">{"".join(sourcefiles)}</package>'


def _sourcefile(name: str, *lines: str) -> str:
    return f'<sourcefile name="{name}">{"".join(lines)}</sourcefile>'


def _line(nr: int, mi: int, ci: int, mb: int = 0, cb: int = 0) -> str:
    return f'<line nr="{nr}" mi="{mi}" ci="{ci}" mb="{mb}" cb="{cb}"/>'


def _refusal(root: Path, raw: bytes) -> str:
    with pytest.raises(ToolError) as refused:
        _read(root, raw)
    return str(refused.value)


def _evidence(hit, missed, branches=()) -> FileEvidence:
    return FileEvidence(array("I", hit), array("I", missed), tuple(branches))


# --- what a report reads as -----------------------------------------------------------------

# The Gradle recording's evidence for Shapes.java, worked from its <line> elements:
# every line with mi + ci > 0 counts, a line with ci > 0 is hit, and each line
# with mb + cb > 0 holds (nr, mb + cb, cb).
SHAPES_EVIDENCE = _evidence(
    [8, 9, 15, 18, 22, 23, 27, 28, 29, 31, 55, 56, 57, 60, 63],
    [6, 11, 16, 35, 36, 38, 39, 41, 42, 43, 44, 45, 46, 48, 61],
    [(8, 2, 1), (15, 4, 1), (35, 4, 0), (38, 4, 0), (41, 2, 0), (43, 2, 0), (45, 2, 0),
     (60, 2, 1)])
GREETER_EVIDENCE = _evidence([], [2, 4, 5, 7], [(4, 2, 0)])


@pytest.mark.parametrize("report", [GRADLE, MAVEN], ids=["gradle", "maven"])
def test_a_recorded_report_reads_each_sourcefile_once_with_no_function_records(tmp_path, report):
    """Shapes$Box shares Shapes.java: its lines come once, from the sourcefile,
    and Gradle's and Maven's runs of one test read the same."""
    raw = report.read_bytes()

    per_file, evidence, digest = _read(tmp_path, raw)

    assert per_file == {SHAPES: [], GREETER: []}
    assert evidence == {SHAPES: SHAPES_EVIDENCE, GREETER: GREETER_EVIDENCE}
    assert digest == hashlib.sha256(raw).hexdigest()


def test_the_lines_no_test_ran_are_the_counted_lines_with_ci_zero(tmp_path):
    lane = _lane()
    adapter = coverage_format.lane_format(lane)

    missing = adapter.missing(lane, tmp_path, _write(tmp_path, GRADLE.read_bytes()))

    assert missing == {SHAPES: set(SHAPES_EVIDENCE.missed_lines),
                       GREETER: set(GREETER_EVIDENCE.missed_lines)}


def test_jacoco_holds_no_per_line_test_contexts(tmp_path):
    lane = _lane()
    adapter = coverage_format.lane_format(lane)

    assert adapter.contexts(lane, tmp_path, _write(tmp_path, GRADLE.read_bytes()), SHAPES) == {}


def test_a_line_with_no_instruction_is_not_counted(tmp_path):
    raw = _report(_package("p", _sourcefile("A.java", _line(1, 0, 0), _line(2, 0, 1),
                                            _line(3, 1, 0))))

    assert _read(tmp_path, raw, _lane(""))[1] == {"p/A.java": _evidence([2], [3])}


def test_a_branch_line_holds_its_missed_and_covered_branches(tmp_path):
    raw = _report(_package("p", _sourcefile("A.java", _line(4, 0, 3, mb=3, cb=1),
                                            _line(5, 2, 0, mb=2, cb=0))))

    assert _read(tmp_path, raw, _lane(""))[1] == {
        "p/A.java": _evidence([4], [5], [(4, 4, 1), (5, 2, 0)])}


def test_class_method_and_counter_elements_are_not_read(tmp_path):
    """A method names only its first line with bytecode; the counters repeat
    what the lines say. Neither adds a line."""
    noise = ('<class name="p/A" sourcefilename="A.java"><method name="f" desc="()V" line="9">'
             '<counter type="LINE" missed="7" covered="0"/></method>'
             '<counter type="BRANCH" missed="2" covered="0"/></class>')
    raw = _report(f'<package name="p">{noise}{_sourcefile("A.java", _line(1, 0, 1))}'
                  '<counter type="LINE" missed="0" covered="1"/></package>')

    assert _read(tmp_path, raw, _lane(""))[1] == {"p/A.java": _evidence([1], [])}


def test_an_aggregate_report_reads_packages_in_groups_at_any_depth(tmp_path):
    inner = _package("b", _sourcefile("B.java", _line(2, 1, 0)))
    raw = _report('<group name="all"><group name="mod-a">'
                  f'{_package("a", _sourcefile("A.java", _line(1, 0, 1)))}</group>'
                  f'<group name="mod-b"><group name="deeper">{inner}</group></group></group>')

    assert _read(tmp_path, raw, _lane(""))[1] == {"a/A.java": _evidence([1], []),
                                                  "b/B.java": _evidence([], [2])}


def test_one_key_in_two_groups_merges_by_line(tmp_path):
    """One lane over two modules that share a package and a file name."""
    raw = _report('<group name="m1">'
                  f'{_package("p", _sourcefile("A.java", _line(1, 1, 0), _line(2, 0, 1, 1, 1)))}'
                  '</group><group name="m2">'
                  f'{_package("p", _sourcefile("A.java", _line(1, 0, 1), _line(3, 1, 0, 2, 0)))}'
                  "</group>")

    assert _read(tmp_path, raw, _lane(""))[1] == {
        "p/A.java": _evidence([1, 2], [3], [(2, 2, 1), (3, 2, 0)])}


# --- refusals -------------------------------------------------------------------------------

@pytest.mark.parametrize("line, words", [
    ('<line nr="3" mi="0" mb="0" cb="0"/>', "line 3 has no ci"),
    ('<line nr="3" mi="0" ci="-1" mb="0" cb="0"/>', "line 3: ci '-1' is not a nonnegative"),
    ('<line nr="3" mi="0" ci="1" mb="NaN" cb="0"/>', "line 3: mb 'NaN' is not a nonnegative"),
    ('<line mi="0" ci="1" mb="0" cb="0"/>', "a line has no nr"),
], ids=["dropped-counter", "negative-count", "non-finite", "no-nr"])
def test_a_line_crapkit_cannot_read_is_refused_naming_it_and_its_file(tmp_path, line, words):
    text = _refusal(tmp_path, _report(_package("com/acme", _sourcefile("Foo.java", line))))

    assert "jacoco artifact cov.xml: com/acme/Foo.java: " in text
    assert words in text and "regenerate the artifact" in text


def test_a_sourcefile_or_a_package_with_no_name_is_refused(tmp_path):
    no_file = _refusal(tmp_path, _report('<package name="p"><sourcefile/></package>'))
    no_package = _refusal(tmp_path, _report("<package/>"))

    assert "package 'p': a sourcefile has no name" in no_file
    assert "a package has no name" in no_package


@pytest.mark.parametrize("packages", ["", '<package name="p"/>', '<group name="g"/>'],
                         ids=["no-package", "no-sourcefile", "empty-group"])
def test_a_report_with_no_sourcefile_measured_nothing(tmp_path, packages):
    text = _refusal(tmp_path, _report(packages))

    assert "cov.xml is empty (zero sourcefiles) - the coverage run measured nothing" in text
    assert "rerun the lane" in text


# --- bytes -------------------------------------------------------------------------------------

CONTROL = _report(_package("p", _sourcefile("A.java", _line(1, 0, 1), _line(2, 1, 0))))


@pytest.mark.parametrize("raw", [
    codecs.BOM_UTF8 + CONTROL,
    codecs.BOM_UTF16_LE + CONTROL.decode("utf-8").replace('"UTF-8"', '"UTF-16"').encode(
        "utf-16-le"),
], ids=["utf-8-bom", "utf-16-bom"])
def test_a_byte_order_mark_and_the_declaration_decide_the_decode(tmp_path, raw):
    _, evidence, digest = _read(tmp_path, raw, _lane(""))

    assert evidence == {"p/A.java": _evidence([1], [2])}
    assert digest == hashlib.sha256(raw).hexdigest()


def test_the_reader_holds_no_decode_literal():
    source = Path(coverage_format.lane_format(_lane()).__file__).read_text(encoding="utf-8")

    for literal in (".decode(", "utf-8", "utf_8", "encoding="):
        assert literal not in source


def test_the_doctype_naming_report_dtd_opens_no_file_and_no_socket(tmp_path, monkeypatch):
    """A report.dtd beside the report declares an entity: reading it would be
    refused, so a clean read proves it was never opened."""
    (tmp_path / "report.dtd").write_text('<!ENTITY boom "x">\n<!ELEMENT report ANY>\n')

    def no_socket(*args, **kwargs):
        raise AssertionError("the reader opened a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    opened: list[str] = []
    module = coverage_format.lane_format(_lane())
    real_open = open
    monkeypatch.setattr(module, "open", lambda path, mode: opened.append(str(path))
                        or real_open(path, mode), raising=False)

    _, evidence, _ = _read(tmp_path, GRADLE.read_bytes())

    assert set(evidence) == {SHAPES, GREETER}
    assert opened == [str(tmp_path / "cov.xml")]


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


def _modules(files: int) -> bytes:
    """`files` packages of one class and one 200-line sourcefile (three in four
    hit, each line with a long ignored attribute), a module group per 100."""
    pad = b' pad="' + b"7" * 300 + b'"'
    out = []
    for index in range(files):
        if index % 100 == 0:
            out.append(b'<group name="m%d">' % (index // 100))
        out.append(b'<package name="pkg%d"><class name="pkg%d/C" sourcefilename="F.java">'
                   b'<method name="f" desc="()V" line="1"><counter type="LINE" missed="1" '
                   b'covered="1"/></method></class><sourcefile name="F.java">' % (index, index))
        out += [b'<line nr="%d" mi="%d" ci="%d" mb="%d" cb="%d"%s/>' % (
            line, 1 if line % 4 == 0 else 0, line % 4, 1 if line % 20 == 1 else 0,
            1 if line % 20 == 1 else 0, pad) for line in range(1, 201)]
        out.append(b"</sourcefile></package>")
        if index % 100 == 99:
            out.append(b"</group>")
    return b"".join(out)


def test_a_200_mb_aggregate_report_reads_under_32_mb_and_hashes_every_byte(monkeypatch):
    head = HEAD.encode("utf-8") + b'<group name="all">'
    tail = b"</group></report>\n"
    body = _modules(1_000)
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
        _, evidence, sha = module.read(_lane(""), Path("."), Path("cov.xml"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(body) * repeats >= 200_000_000
    assert len(evidence) == 1_000
    assert evidence["pkg7/F.java"] == _evidence(
        [line for line in range(1, 201) if line % 4], range(4, 201, 4),
        [(line, 2, 1) for line in range(1, 201, 20)])
    assert sha == digest.hexdigest()
    assert peak < 32 * 1000 * 1000


# --- keys ---------------------------------------------------------------------------------

def _one_key(root: Path, package: str, name: str, path_prefix: str) -> str:
    raw = _report(_package(package, _sourcefile(name, _line(1, 0, 1))))
    (key,) = _read(root, raw, _lane(path_prefix))[1]
    return key


def test_a_key_is_the_package_path_and_the_sourcefile_under_path_prefix(tmp_path):
    assert _one_key(tmp_path, "com/acme", "Foo.java", "svc/src/main/java") == (
        "svc/src/main/java/com/acme/Foo.java")
    assert _one_key(tmp_path, "com/acme", "Foo.java", "svc/src/main/java/") == (
        "svc/src/main/java/com/acme/Foo.java")


def test_a_default_package_sourcefile_keys_alone_under_the_default_stanzas_prefix(tmp_path):
    assert _one_key(tmp_path, "", "Foo.java", "src/main/java") == "src/main/java/Foo.java"
    assert _one_key(tmp_path, "", "Foo.java", "") == "Foo.java"


_MODULES = _report(f'<group name="a">{_package("p", _sourcefile("A.java", _line(1, 0, 1)))}'
                   f'</group><group name="b">{_package("q", _sourcefile("B.java", _line(1, 0, 1)))}'
                   '</group>')
_PER_MODULE = ("for a multi-module build, declare one lane per module with path_prefix at that "
               "module's source root")


def _two_modules(root: Path) -> Path:
    """Modules a and b, each with one Java file under its own src/main/java."""
    for module, package, name in (("a", "p", "A.java"), ("b", "q", "B.java")):
        path = root / module / "src" / "main" / "java" / package / name
        path.parent.mkdir(parents=True)
        path.write_text("class X {}\n")
    return root


def _lane_says(root: Path, capsys, raw: bytes, path_prefix: str, paths: tuple) -> str:
    """What one lane run over the report prints on stderr."""
    from crapkit.lanes import run_lane

    _write(root, raw)
    capsys.readouterr()
    run_lane(root, _lane(path_prefix), reuse_artifact=True, scope_paths={"java": paths})
    return capsys.readouterr().err


@pytest.mark.parametrize("path_prefix, paths, astray", [
    ("a/src/main/java", ("a", "b"), "1 of the 2 sourcefile(s) in cov.xml name no file under "
     "path_prefix 'a/src/main/java' (a/src/main/java/q/B.java)"),
    ("a/src/main/java", (".",), "1 of the 2 sourcefile(s) in cov.xml name no file under "
     "path_prefix 'a/src/main/java' (a/src/main/java/q/B.java)"),
    ("src/main/java", ("a", "b"), "2 of the 2 sourcefile(s) in cov.xml name no file under "
     "path_prefix 'src/main/java' (src/main/java/p/A.java, src/main/java/q/B.java)"),
    ("src/main/java", (".",), "2 of the 2 sourcefile(s) in cov.xml name no file under "
     "path_prefix 'src/main/java' (src/main/java/p/A.java, src/main/java/q/B.java)"),
    ("", ("a", "b"), "2 of the 2 sourcefile(s) in cov.xml name no file in this checkout "
     "(p/A.java, q/B.java)"),
], ids=["module-a-prefix", "module-a-prefix-root-scope", "source-root", "source-root-root-scope",
        "no-prefix"])
def test_one_lane_over_a_multi_module_report_says_to_declare_one_lane_per_module(
        tmp_path, capsys, path_prefix, paths, astray):
    """Whatever path_prefix the lane sets, the keys of the modules it does not
    name stay unplaced and the lane says so, with the one-lane-per-module fix."""
    err = _lane_says(_two_modules(tmp_path), capsys, _MODULES, path_prefix, paths)

    assert f"crapkit: lane 'java': {astray}, so no function scores their lines; {_PER_MODULE}" in err


def test_a_lane_whose_every_key_names_a_file_says_nothing_of_modules(tmp_path, capsys):
    raw = _report(_package("p", _sourcefile("A.java", _line(1, 0, 1))))

    assert _lane_says(_two_modules(tmp_path), capsys, raw, "a/src/main/java", ("a",)) == ""


def test_a_kotlin_sourcefile_no_scope_claims_reads_with_no_error(tmp_path):
    """Bar.kt keys like any sourcefile; no java scope's inventory holds it, so
    its evidence joins no span."""
    raw = _report(_package("com/acme", _sourcefile("Foo.java", _line(1, 0, 1)),
                           _sourcefile("Bar.kt", _line(3, 1, 0))))

    evidence = _read(tmp_path, raw)[1]

    assert set(evidence) == {"src/main/java/com/acme/Foo.java",
                             "src/main/java/com/acme/Bar.kt"}
    rows = [InventoryRow(scope="java", path="src/main/java/com/acme/Foo.java", long_name="f",
                         start=1, end=2, ccn_std=1, ccn_mod=1, ccn=1, nloc=2, params=0,
                         nesting=0)]
    (joined,) = join_evidence(rows, evidence["src/main/java/com/acme/Foo.java"])
    assert joined.coverage == 1.0


def test_the_adapter_carries_its_fix_texts_and_no_producer_facts():
    adapter = coverage_format.lane_format(_lane())

    assert adapter.TAKES_PATH_PREFIX is True
    assert "outside" in adapter.WRONG_TREE_FIX and "path_prefix" in adapter.ABSOLUTE_FIX
    assert (adapter.SHARD_GLOB, adapter.COMBINE_RECIPE, adapter.DROPPINGS) == (None, None, ())
    assert adapter.data_file(_lane()) is None


# --- the join at score's interface ----------------------------------------------------------

def _row(name: str, start: int, end: int) -> InventoryRow:
    return InventoryRow(scope="java", path=SHAPES, long_name=name, start=start, end=end,
                        ccn_std=2, ccn_mod=2, ccn=2, nloc=end - start + 1, params=0, nesting=0)


def test_the_recorded_report_joins_branch_and_line_coverage_and_the_lambdas_lines(tmp_path):
    """Shapes.java's spans: ifElse 7-12 has a branch line, branchless 21-24 has
    lines only, and twice 26-32 holds a lambda on 27-30."""
    rows = [_row("ifElse", 7, 12), _row("branchless", 21, 24), _row("twice", 26, 32),
            _row("twice::lambda", 27, 30)]
    per_file, evidence, _ = _read(tmp_path, GRADLE.read_bytes())

    joined = {fn.name: fn for fn in join_evidence(rows, evidence[SHAPES])}

    assert per_file[SHAPES] == []
    assert (joined["ifElse"].branches_total, joined["ifElse"].branches_covered) == (2, 1)
    assert joined["ifElse"].coverage == 0.5
    assert joined["branchless"].branches_total == 0
    assert (joined["branchless"].statements_total,
            joined["branchless"].statements_covered) == (2, 2)
    assert joined["branchless"].coverage == 1.0
    assert (joined["twice::lambda"].statements_total,
            joined["twice::lambda"].statements_covered) == (3, 3)
    assert (joined["twice"].statements_total, joined["twice"].statements_covered) == (1, 1)
