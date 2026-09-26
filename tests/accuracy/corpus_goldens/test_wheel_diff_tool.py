"""tools/accuracy/wheel_diff.py: the moved-row map, its sides and the xplat rule.

- property: the map equals a diff of the two tables written here with the
  csv module, over Hypothesis tables with changed, dropped and added rows.
- metamorphic: swapping base and candidate mirrors every move.
- hand: two wheels zipped from this checkout's crapkit, the candidate with
  one planted change (cognitive complexity gains 1 at ccn 3), run on a
  two-function repo: the planted value is the only move, in both exports.
- hand: a side that handed off failure.json skips with one line and exit 0;
  a PyPI wheel whose bytes miss PyPI's sha256 refuses; the xplat rule's
  ulp cases, worked from math.ulp.
"""
import csv
import io
import json
import math
from pathlib import Path
import time
import zipfile

from hypothesis import given, strategies as st
import pytest

from accuracy.corpus_goldens import releases, wheels
from accuracy.kit import repos
from accuracy.kit.settings import pure

# A two-scope repo: src has no lane, so its rows read no-lane and score
# crap(ccn, 0); lib's lane copies a hand-written coverage.py artifact.
_SUMMARY = {"covered_lines": 2, "num_statements": 2, "missing_lines": 0, "excluded_lines": 0,
            "covered_branches": 0, "num_branches": 0, "missing_branches": 0,
            "num_partial_branches": 0, "percent_covered": 100.0}
_REGION = {"executed_lines": [1, 2], "missing_lines": [], "excluded_lines": [],
           "executed_branches": [], "missing_branches": [], "summary": _SUMMARY}
ARTIFACT = {"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                     "show_contexts": False, "timestamp": "2026-09-24T00:00:00"},
            "files": {"lib/b.py": {**_REGION, "functions": {"h": {**_REGION, "start_line": 1}},
                                   "classes": {}}},
            "totals": _SUMMARY}
TINY = {
    "crapkit.toml": ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
                     'languages = ["python"]\n\n[[scope]]\nname = "lib"\npaths = ["lib"]\n'
                     'languages = ["python"]\n\n'
                     + repos.lane_toml("lib", ".crapkit/cov/b.json", "coveragepy", ["lib"],
                                       "recorded/b.json")),
    "src/a.py": ("def f(x):\n    return x\n\n\ndef g(x):\n    if x > 1:\n        return 1\n"
                 "    if x > 2:\n        return 2\n    return 0\n"),
    "lib/b.py": "def h(x):\n    return x\n",
    "recorded/b.json": json.dumps(ARTIFACT),
}


wheel_diff = releases.wheel_diff()
COLUMNS = ("path", "long_name", "occurrence", "ccn", "crap", "remedy")


# --- the map against a csv diff ------------------------------------------------------

def _tsv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _csv_rows(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return {(row["path"], row["long_name"], row["occurrence"]): row for row in reader}


def _presence(key: tuple, old: dict, new: dict) -> tuple:
    return (*key, "row", "present" if key in old else "absent",
            "present" if key in new else "absent")


def _csv_cells(key: tuple, old: dict, new: dict) -> set[tuple]:
    if key not in old or key not in new:
        return {_presence(key, old, new)}
    return {(*key, column, old[key][column], new[key][column]) for column in COLUMNS[3:]
            if old[key][column] != new[key][column]}


def csv_diff(base: str, candidate: str) -> set[tuple]:
    """(path, long_name, occurrence, column, old, new) for every differing cell,
    with a row one side lacks as a `row` present/absent pair."""
    old, new = _csv_rows(base), _csv_rows(candidate)
    return {cell for key in set(old) | set(new) for cell in _csv_cells(key, old, new)}


_ROW = st.fixed_dictionaries({
    "path": st.sampled_from(["src/a.py", "src/b é.py", "lib/c.ts"]),
    "long_name": st.sampled_from(["f( x )", "g( a , b )", "(anonymous)"]),
    "occurrence": st.sampled_from(["1", "2"]),
    "ccn": st.integers(1, 40).map(str),
    "crap": st.floats(1, 2000, allow_nan=False).map(repr),
    "remedy": st.sampled_from(["ok", "add-tests", "decompose", "split-lines"]),
})


def _unique(rows: list[dict]) -> list[dict]:
    return list({(row["path"], row["long_name"], row["occurrence"]): row for row in rows}.values())


def _as_tuples(moves) -> set[tuple]:
    return {(row.path, row.long_name, row.occurrence, row.column, row.old, row.new)
            for row in moves}


@pure
@given(st.lists(_ROW, max_size=12).map(_unique), st.lists(_ROW, max_size=12).map(_unique))
def test_the_map_equals_a_csv_diff(base_rows, candidate_rows):
    base, candidate = _tsv(base_rows), _tsv(candidate_rows)

    assert _as_tuples(wheel_diff.moved("scored.tsv", base, candidate)) == csv_diff(base, candidate)


def _mirror(row) -> tuple:
    return (row.path, row.long_name, row.occurrence, row.column, row.new, row.old)


@pure
@given(st.lists(_ROW, max_size=12).map(_unique), st.lists(_ROW, max_size=12).map(_unique))
def test_swapping_the_sides_mirrors_every_move(base_rows, candidate_rows):
    base, candidate = _tsv(base_rows), _tsv(candidate_rows)
    forward = wheel_diff.moved("scored.tsv", base, candidate)
    backward = wheel_diff.moved("scored.tsv", candidate, base)

    assert {_mirror(row) for row in forward} == _as_tuples(backward)


def test_every_moved_column_names_a_calc_of_the_plan():
    names = set(wheel_diff.CALCS.values()) | {wheel_diff.DISCOVERY}

    assert names == {"File universe and scope ownership", "Function discovery and spans",
                     "ccn_std, ccn_mod and gated ccn", "nloc",
                     "Parameter list (params, packet.params)", "Nesting depth",
                     "Cognitive complexity", "Function coverage ratio", "Coverage flag",
                     "CRAP score", "Remedy label"}


def test_the_declared_calcs_must_equal_the_moved_ones():
    moves = [wheel_diff.Moved("scored.tsv", "src/a.py", "g( x )", "1", "crap", "12.0", "12.5")]

    assert wheel_diff.expectation_problem(moves, "CRAP score") is None
    assert wheel_diff.expectation_problem(moves, None) is None
    assert "declares ['Remedy label']" in wheel_diff.expectation_problem(moves, "Remedy label")
    assert "moved nothing" in wheel_diff.expectation_problem([], "CRAP score")
    assert wheel_diff.expectation_problem([], "") is None


# --- sides ------------------------------------------------------------------------------

def _wheel(dest: Path, planted: bool) -> Path:
    return wheels.zipped(dest, wheels.COGNITIVE_PLANT if planted else None)


def _tiny(tmp_path: Path) -> Path:
    tree = tmp_path / "tiny"
    for name, text in TINY.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(text.encode("utf-8"))
    return tree


@pytest.mark.process
def test_a_planted_change_is_the_only_move(tmp_path):
    tree, exports = _tiny(tmp_path), []
    for side, planted in (("base", False), ("candidate", True)):
        site = wheel_diff.unpack(_wheel(tmp_path / f"{side}.whl", planted), tmp_path / side)
        exports.append(wheel_diff.measure_tree(tree, site, tmp_path / f"{side}-run",
                                               wheel_diff.SMALL_COMMANDS, repos.EPOCH + 86_400))

    moves = wheel_diff.diff_exports(*exports)

    assert [(m.export, m.path, m.long_name, m.column, m.old, m.new, m.calc) for m in moves] == [
        (export, "src/a.py", "g( x )", "cognitive", "2", "3", "Cognitive complexity")
        for export in ("inventory.tsv", "scored.tsv")]


@pytest.mark.process
def test_a_wheel_without_crapkit_refuses(tmp_path):
    empty = tmp_path / "empty.whl"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("other/__init__.py", "")

    with pytest.raises(wheel_diff.WheelDiffError, match="resolved to"):
        wheel_diff.unpack(empty, tmp_path / "site")


def test_a_side_that_handed_off_failure_json_skips_with_one_line(tmp_path, capsys):
    base = tmp_path / "base"
    base.mkdir()
    (base / "failure.json").write_text(json.dumps({"phase": "install", "error": "no wheel"}))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "crapkit-0.0.0-py3-none-any.whl").write_bytes(b"")

    code = wheel_diff.main(["diff", "--base-wheel", str(base), "--candidate-wheel",
                            str(candidate)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "wheel diff skipped: a side handed off failure.json (stopped at install: no wheel)"]


def test_a_hand_off_without_one_wheel_is_an_infra_failure(tmp_path):
    with pytest.raises(wheel_diff.WheelDiffError, match="0 wheels"):
        wheel_diff.hand_off(tmp_path)


def test_a_pypi_wheel_must_hash_to_the_published_sha256(tmp_path, monkeypatch):
    release = {"urls": [{"packagetype": "bdist_wheel", "filename": "crapkit-9.9.9-py3-none-any.whl",
                         "url": "wheel", "digests": {"sha256": "0" * 64}}]}
    answers = {"wheel": b"not the published bytes"}
    monkeypatch.setattr(wheel_diff, "_fetch",
                        lambda url: answers.get(url, json.dumps(release).encode()))

    with pytest.raises(wheel_diff.WheelDiffError, match="does not hash"):
        wheel_diff.download("9.9.9", tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- the xplat rule ---------------------------------------------------------------------

def _next(value: float, steps: int) -> str:
    for _ in range(steps):
        value = math.nextafter(value, math.inf)
    return repr(value)


@pytest.mark.parametrize("left, right, agree", [
    ("13.125", "13.125", True),
    ("13.125", _next(13.125, 1), True),
    ("13.125", _next(13.125, 2), True),
    ("13.125", _next(13.125, 3), False),
    ("0.2500", "0.2501", False),
    ("7", "8", False),
    ("ok", "add-tests", False),
    ("nan", "inf", False),
    ("12", "12.0", False),
])
def test_the_xplat_rule(left, right, agree):
    assert (left == right or wheel_diff.within_ulps(left, right)) is agree


def _receipt(os_name: str, text: str) -> dict:
    return {"os": os_name, "python": "3.12",
            "exports": {"small/scored.tsv": {"sha256": "x", "text": text}}}


def test_xplat_names_the_first_row_and_column_that_differ():
    base = _tsv([{"path": "src/a.py", "long_name": "g( x )", "occurrence": "1", "ccn": "3",
                  "crap": "12.0", "remedy": "add-tests"}])
    near = base.replace("12.0", _next(12.0, 1))
    far = base.replace("\t3\t", "\t4\t")

    assert wheel_diff.xplat([_receipt("linux", base), _receipt("windows", near)]) == []
    assert wheel_diff.xplat([_receipt("linux", base), _receipt("windows", far)]) == [
        "linux-3.12 vs windows-3.12: small/scored.tsv src/a.py 'g( x )' #1 ccn: '3' against '4'"]
    assert wheel_diff.xplat([{"exports": {}}]) == ["no receipt noted a corpus export"]


# --- whole runs -------------------------------------------------------------------------

@pytest.mark.nightly
@pytest.mark.process
def test_one_wheel_against_itself_moves_nothing_on_the_small_corpus(tmp_path, capsys):
    wheel = _wheel(tmp_path / "crapkit-0.0.0-py3-none-any.whl", planted=False)
    started = time.monotonic()

    code = wheel_diff.main(["diff", "--base-wheel", str(wheel), "--candidate-wheel", str(wheel),
                            "--out", str(tmp_path / "out")])

    assert (code, capsys.readouterr().out.splitlines()[0]) == (0, "0 value(s) moved")
    assert time.monotonic() - started <= 90
    assert (tmp_path / "out" / "moved.tsv").read_text(encoding="utf-8").count("\n") == 1
    assert json.loads((tmp_path / "out" / "summary.json").read_text()) == {"moved_rows": 0,
                                                                           "calcs": {}}


@pytest.mark.nightly
@pytest.mark.process
def test_the_full_corpus_diff_reads_every_member(tmp_path):
    corpus = tmp_path / "corpus"
    for member in ("one", "two"):
        (corpus / member / "src").mkdir(parents=True)
        (corpus / member / "crapkit.toml").write_bytes(
            b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            b'languages = ["python"]\ncoverage_optional = true\n')
        (corpus / member / "src" / "a.py").write_bytes(TINY["src/a.py"].encode("utf-8"))
    site = wheel_diff.unpack(_wheel(tmp_path / "w.whl", planted=False), tmp_path / "site")

    exports = wheel_diff.measure_full(corpus, site, tmp_path / "work", repos.EPOCH + 86_400)

    assert sorted(exports) == ["one/inventory.tsv", "two/inventory.tsv"]
    assert wheel_diff.diff_exports(exports, exports) == []


def _files(*kinds: str, yanked: bool = False) -> list[dict]:
    return [{"packagetype": kind, "yanked": yanked} for kind in kinds]


def test_the_releases_are_pypi_s_newest_final_wheels(monkeypatch):
    """0.10.0 sorts above 0.9.1 as a version, not as text; a pre-release, a
    release with only an sdist and a yanked wheel are left out."""
    listing = {"0.9.0": _files("bdist_wheel", "sdist"), "0.9.1": _files("bdist_wheel"),
               "0.10.0": _files("bdist_wheel"), "0.11.0rc1": _files("bdist_wheel"),
               "0.10.1": _files("sdist"), "0.10.2": _files("bdist_wheel", yanked=True),
               "0.8.0": _files("bdist_wheel")}
    asked = []

    def fetch(url):
        asked.append(url)
        return json.dumps({"releases": listing}).encode()
    monkeypatch.setattr(wheel_diff, "_fetch", fetch)

    assert wheel_diff.releases(3) == ["0.10.0", "0.9.1", "0.9.0"]
    assert wheel_diff.releases(2, at_most="0.9.1") == ["0.9.1", "0.9.0"]
    assert asked == ["https://pypi.org/pypi/crapkit/json"] * 2


def test_the_upload_date_is_the_wheel_s(monkeypatch):
    release = {"urls": [{"packagetype": "sdist", "upload_time_iso_8601": "2026-09-22T23:59:00Z"},
                        {"packagetype": "bdist_wheel", "filename": "crapkit-0.9.0-py3-none-any.whl",
                         "upload_time_iso_8601": "2026-09-23T20:04:49.119305Z"}]}
    monkeypatch.setattr(wheel_diff, "_fetch", lambda url: json.dumps(release).encode())

    assert wheel_diff.upload_date("0.9.0") == "2026-09-23"


def test_the_wheelhouse_is_the_one_the_environment_names(monkeypatch, tmp_path):
    monkeypatch.setenv(wheel_diff.WHEELHOUSE_ENV, str(tmp_path))

    assert wheel_diff.default_wheelhouse() == tmp_path


def test_a_failed_release_fetch_is_noted_as_an_infra_miss(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CRAPKIT_ACCURACY_LOG", str(log))

    def fetch(url):
        raise wheel_diff.WheelDiffError(f"fetching {url} failed: offline")
    monkeypatch.setattr(wheel_diff, "_fetch", fetch)

    with pytest.raises(wheel_diff.WheelDiffError):
        releases.last(5)
    notes = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(note["kind"], note["message"]) for note in notes] == [
        ("infra", "fetching https://pypi.org/pypi/crapkit/json failed: offline")]
