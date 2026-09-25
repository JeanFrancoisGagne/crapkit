"""Artifacts crapkit must read exactly or refuse: framing, digest, counts, and trees.

- Framing: crapkit's streaming reader hands back the members json.loads finds, in
  order, at every window size from 1 to 4096 bytes, and the digest it records is
  hashlib's sha256 of the bytes.
- Refusals: RFC 8259 section 6 has no NaN or Infinity, and docs/lanes.md refuses
  a count that is not a nonnegative integer or a covered count over its total.
- Trees: docs/lanes.md#an-artifact-that-measured-a-different-tree gives three
  verdicts for an artifact that reaches none of its lane's scopes; a model of
  that table decides each drawn artifact, and hand cases run the CLI.
"""
from contextlib import redirect_stderr
import hashlib
import io
import json
import math
import os
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.coverage_oracles import mini_repo, probe_repo, under_test
from accuracy.kit import strategies
from accuracy.kit.settings import pure

TEXT = st.text(st.characters(exclude_categories=("Cs",)), max_size=10)
SCALARS = (st.none() | st.booleans() | st.integers(-10**15, 10**15)
           | st.floats(allow_nan=False, allow_infinity=False) | TEXT)
VALUES = st.recursive(SCALARS, lambda inner: st.lists(inner, max_size=4)
                      | st.dictionaries(TEXT, inner, max_size=4), max_leaves=20)
DOCUMENTS = st.dictionaries(TEXT, VALUES, max_size=6)
LAYOUTS = st.sampled_from([{}, {"indent": 1}, {"indent": 4, "ensure_ascii": False},
                           {"separators": (",", ":")}, {"ensure_ascii": False}])


# Imported once, before any example runs, so no example pays for the import.
COVSTREAM, CONFIG, LANES, ERRORS, COVERAGE_PY, ISTANBUL = (under_test.crapkit(name) for name in (
    "covstream", "config", "lanes", "errors", "coverage_py", "coverage_istanbul"))


def _window(data: bytes, chunk: int):
    return COVSTREAM, COVSTREAM._Window(io.BytesIO(data), chunk)


@given(DOCUMENTS, LAYOUTS, st.integers(1, 4096))
@pure
def test_split_window_hands_back_what_json_loads_reads(document, layout, chunk):
    data = json.dumps(document, **layout).encode("utf-8")
    covstream, window = _window(data, chunk)

    assert list(covstream.split_window(window)) == list(json.loads(data).items())
    assert window.hasher.hexdigest() == hashlib.sha256(data).hexdigest()


def _rebuilt(pairs) -> dict:
    """The report again from the walk's pairs; an empty "files" yields no pair."""
    report: dict = {"files": {}}
    for key, value, kind in pairs:
        if kind == "sub":
            report.setdefault("files", {})[key] = value
        else:
            report[key] = value
    return report


@given(st.dictionaries(TEXT, VALUES, max_size=3), st.dictionaries(TEXT, VALUES, max_size=3),
       st.booleans(), LAYOUTS, st.integers(1, 4096))
@pure
def test_walk_report_hands_back_what_json_loads_reads(files, meta, files_first, layout, chunk):
    report = {"files": files, "meta": meta} if files_first else {"meta": meta, "files": files}
    data = json.dumps(report, **layout).encode("utf-8")
    covstream, window = _window(data, chunk)

    assert _rebuilt(covstream.walk_report(window, "files")) == json.loads(data)


@pytest.mark.parametrize("path", [probe_repo.RECORDED / "coveragepy-7.16.1" / "call.json",
                                  probe_repo.RECORDED / "vitest-v8-5.0.1" / "call.json"],
                         ids=["coveragepy", "istanbul"])
def test_the_recorded_digest_is_the_sha256_of_the_artifact_bytes(path):
    digest = _parse(path, "coveragepy" in str(path))[2]

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def _parse(path: Path, coveragepy: bool):
    if coveragepy:
        return COVERAGE_PY.parse_coveragepy_both_file(path, path_prefix="")
    return ISTANBUL.parse_istanbul_both_file(path, repo_root="")


# --- refusals -------------------------------------------------------------------------------

ISTANBUL_FILE = ('{"a.js": {"path": "a.js", "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}},'
                 ' "loc": {"end": {"line": 3}}}}, "f": {"0": 1}, "statementMap": {"0": {"start":'
                 ' {"line": 2}}}, "s": {"0": COUNT}, "branchMap": {}, "b": {}}}')
# (shape, the artifact text, what the refusal says); sources: RFC 8259 sections 2 and 6.
REFUSED = [
    ("truncated", ISTANBUL_FILE.replace("COUNT", "1")[:-2], "unparseable"),
    ("trailing", ISTANBUL_FILE.replace("COUNT", "1") + " {}", "unexpected content"),
    ("bom", "\ufeff" + ISTANBUL_FILE.replace("COUNT", "1"), "not a JSON object"),
    ("nan", ISTANBUL_FILE.replace("COUNT", "NaN"), "non-finite"),
    ("infinity", ISTANBUL_FILE.replace("COUNT", "Infinity"), "non-finite"),
    ("minus-infinity", ISTANBUL_FILE.replace("COUNT", "-Infinity"), "non-finite"),
    ("overflow", ISTANBUL_FILE.replace("COUNT", "1e999"), "non-finite"),
]


@pytest.mark.parametrize("shape, text, said", REFUSED, ids=[row[0] for row in REFUSED])
def test_a_malformed_artifact_is_refused(tmp_path, shape, text, said):
    """R27 among them: a non-finite count must never reach a ratio."""
    artifact = tmp_path / "coverage-final.json"
    artifact.write_bytes(text.encode("utf-8"))

    with pytest.raises(ERRORS.ToolError, match=said):
        _parse(artifact, coveragepy=False)


def test_nonfinite_refuses(tmp_path):
    """R27: a NaN in a coverage.py summary is refused as the istanbul one is."""
    region = {"summary": {"num_statements": 2, "covered_lines": float("nan")},
              "executed_lines": [2], "missing_lines": [3], "start_line": 1}
    artifact = tmp_path / "py.json"
    artifact.write_bytes(mini_repo.coveragepy_report({"a.py": {"functions": {"f": region}}}))

    with pytest.raises(ERRORS.ToolError, match="non-finite"):
        _parse(artifact, coveragepy=True)


def _admitted(value) -> bool:
    """docs/lanes.md#what-the-istanbul-parser-reads: a nonnegative integer, an
    integral JSON number such as 1.0 included; never a boolean or a fraction.
    None stands for a count the artifact leaves out."""
    return ADMIT.get(type(value), lambda _: False)(value)


def _integral_float(value: float) -> bool:
    return math.isfinite(value) and value.is_integer() and value >= 0


ADMIT = {type(None): lambda _: True, int: lambda value: value >= 0, float: _integral_float}


def _valid(pair) -> bool:
    if not (_admitted(pair.covered) and _admitted(pair.total)):
        return False
    return (pair.covered or 0) <= (pair.total or 0)


def _summary(pair) -> dict:
    summary = {"num_statements": 2, "covered_lines": 1}
    for key, value in (("covered_branches", pair.covered), ("num_branches", pair.total)):
        if value is not None:
            summary[key] = value
    return summary


def _read(data: bytes, coveragepy: bool):
    """Parse bytes through the reader's own walk, in memory: a file write per
    example costs more on Windows than the pure deadline allows."""
    _, window = _window(data, COVSTREAM.CHUNK)
    with redirect_stderr(io.StringIO()):
        if coveragepy:
            return COVSTREAM._guarded(lambda: COVERAGE_PY._coveragepy_both(window, "", ""), "bad")
        return COVSTREAM._guarded(lambda: ISTANBUL._istanbul_both(window, ""), "bad")


@strategies.examples("coverage_pair")
@given(strategies.coverage_pair())
@pure
def test_impossible_counts_refuse_coveragepy(pair):
    """R28 (coveragepy): a summary count that is not a nonnegative integer, or
    covered over total, refuses; anything else is read as written. R89: a
    region with no branch keys reads zero branches."""
    region = {"summary": _summary(pair), "executed_lines": [2], "missing_lines": [3],
              "start_line": 1}
    data = mini_repo.coveragepy_report({"a.py": {"functions": {"f": region}}})
    try:
        fn = _read(data, coveragepy=True)[0]["a.py"][0]
    except ERRORS.ToolError:
        assert not _valid(pair)
    else:
        assert _valid(pair) and (fn.branches_covered, fn.branches_total) == (
            int(pair.covered or 0), int(pair.total or 0))


@strategies.examples("coverage_pair")
@given(strategies.coverage_pair())
@pure
def test_impossible_counts_refuse_istanbul(pair):
    """R28 (istanbul): a statement count that is not a nonnegative integer refuses."""
    count = json.dumps(pair.covered) if pair.covered is not None else "0"
    try:
        _read(ISTANBUL_FILE.replace("COUNT", count).encode(), coveragepy=False)
    except ERRORS.ToolError:
        assert not _admitted(pair.covered)
    else:
        assert _admitted(pair.covered)


# --- which tree an artifact measured -----------------------------------------------------------

DRIVE = ("C:/", "C:\\", "d:/")


def _escapes(path: str) -> bool:
    """Absolute in either spelling, or climbing out of the tree."""
    return path.startswith(("/", "../")) or path[:3] in DRIVE or path[:3].lower() in DRIVE


def _under(root: Path, path: str) -> bool:
    if path.startswith("../"):
        return False
    try:
        resolved = os.path.normcase(str(Path(path).resolve()))
    except (OSError, ValueError):
        return False
    base = os.path.normcase(str(root.resolve()))
    return resolved == base or resolved.startswith(base.rstrip(os.sep) + os.sep)


def _reaches(path: str, scope_path: str) -> bool:
    return path == scope_path or path.startswith(scope_path.rstrip("/") + "/")


def verdict(root: Path, keys: list[str], scope_path: str) -> str:
    """docs/lanes.md#an-artifact-that-measured-a-different-tree, as a table."""
    if any(_reaches(key.replace("\\", "/"), scope_path) for key in keys):
        return "admitted"
    return _unreached_verdict(root, [key for key in keys if _escapes(key)])


def _unreached_verdict(root: Path, escaped: list[str]) -> str:
    """No key reaches a scope: an escaping key from another tree refuses, one
    that is this tree spelled absolutely refuses with the other fix, and
    in-tree keys only warn."""
    if any(not _under(root, key) for key in escaped):
        return "different tree"
    return "spelled absolutely" if escaped else "warned"


def _crapkit_verdict(root: Path, keys: list[str], scope_path: str) -> str:
    config, lanes, errors = CONFIG, LANES, ERRORS
    lane = config.Lane("py", "true", ".crapkit/cov/py.json", "coveragepy", ("s",))
    coverage = {key.replace("\\", "/"): [] for key in keys}
    stderr = io.StringIO()
    try:
        with redirect_stderr(stderr):
            lanes._judge_artifact_scope(lane, coverage, {"s": (scope_path,)}, root)
    except errors.ToolError as refusal:
        return "different tree" if "different tree" in str(refusal) else "spelled absolutely"
    return "warned" if stderr.getvalue() else "admitted"


def _keys(root: Path):
    inside = st.sampled_from(["src/a.py", "src/pkg/b.py"])
    missing = st.sampled_from(["tests/test_a.py", "lib/c.py", "srcx/d.py"])
    elsewhere = st.sampled_from(["/other/checkout/src/a.py", "../sibling/a.py", "C:/other/a.py"])
    absolute = st.sampled_from([(root / "src" / "a.py").as_posix(), (root / "lib" / "c.py").as_posix()])
    return st.lists(inside | missing | elsewhere | absolute, min_size=1, max_size=5)


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    """A checkout root, with each verdict asked once up front: the first call
    pays for the reader's lazy imports, which no example should."""
    top = tmp_path_factory.mktemp("admission")
    (top / "src").mkdir()
    for keys in (["src/a.py"], ["lib/c.py"], ["/other/a.py"], [(top / "lib" / "c.py").as_posix()]):
        _crapkit_verdict(top, keys, "src")
    return top


@given(st.data())
@pure
def test_the_admission_verdict_follows_the_documented_table(root, data):
    keys = data.draw(_keys(root))

    assert _crapkit_verdict(root, keys, "src") == verdict(root, keys, "src")


def _case_variants(path: str) -> list[str]:
    return [path, path.replace("/", "\\"), path[:1].swapcase() + path[1:]]


@pytest.mark.platform("win32")
def test_a_windows_root_spelled_any_way_is_the_same_tree(root):
    """docs/lanes.md: the case is folded where the filesystem folds it, so a
    drive letter or separator spelled another way is still this checkout."""
    keys = [(root / "lib" / "c.py").as_posix()]

    assert {_crapkit_verdict(root, [key], "src") for key in
            (variant for key in keys for variant in _case_variants(key))} == {"spelled absolutely"}


# --- the CLI on a lane from another checkout (R82) ----------------------------------------------

SOURCE = "def f(x):\n    if x:\n        return 1\n    return 0\n"


def _region() -> dict:
    return {"executed_lines": [2, 3], "missing_lines": [4], "excluded_lines": [],
            "executed_branches": [[2, 3]], "missing_branches": [[2, 4]], "start_line": 1,
            "summary": {"num_statements": 3, "covered_lines": 2, "num_branches": 2,
                        "covered_branches": 1}}


def _report(key: str) -> bytes:
    return mini_repo.coveragepy_report({key: {"functions": {"f": _region()},
                                              "missing_lines": [4]}})


@pytest.mark.process
@pytest.mark.parametrize("prefix, key, said", [
    ("", "/other/checkout/src/a.py", "describes a different tree"),
    ("backend", "/other/checkout/a.py", "describes a different tree"),
    ("", "ABSOLUTE", "measured this tree and spelled it absolutely"),
], ids=["plain", "path_prefix", "absolute"])
def test_other_checkout_refuses(tmp_path, prefix, key, said):
    """R82: the lane fails, exit 5, and says which verdict (docs/lanes.md table)."""
    top = tmp_path / "repo"
    key = (top / "src" / "a.py").as_posix() if key == "ABSOLUTE" else key
    toml = mini_repo.config([mini_repo.scope("s", ["src"], ["python"])],
                            [mini_repo.lane("py", "coveragepy", ["s"], prefix)])
    driver = mini_repo.build(top, {"crapkit.toml": toml, "src/a.py": SOURCE,
                                   "recorded/py.json": _report(key)})

    result = driver.run("coverage", "--export", "scored.tsv")

    assert (result.code, said in result.stderr) == (5, True), result.stderr


# --- report shapes a lane still scores (R89) -------------------------------------------------------

def _without_branches(region: dict) -> dict:
    summary = {key: value for key, value in region["summary"].items() if "branch" not in key}
    kept = {key: value for key, value in region.items() if "branch" not in key}
    return {**kept, "summary": summary}


def _shape_report(shape: str) -> bytes:
    """no_branch: `pytest --cov` without --cov-branch; no_regions: a second file a
    plugin reporter measured, with no "functions" key."""
    region = _region()
    files = {"src/a.py": {"functions": {"f": region}, "missing_lines": [4]}}
    if shape == "no_branch":
        files["src/a.py"]["functions"]["f"] = _without_branches(region)
        return json.dumps({"meta": {"format": 3, "version": "7.16.1", "branch_coverage": False},
                           "files": files}).encode()
    files["src/page.html"] = {"missing_lines": [], "executed_lines": [1]}
    return mini_repo.coveragepy_report(files)


# (shape, crapkit's cov for f): with no branch data the README falls back to
# statement coverage, 2 of 3 lines; with a regionless file beside it, f keeps its
# branch coverage, 1 of 2 arms (docs/lanes.md#a-file-the-report-carries-no-regions-for).
SHAPES = [("no_branch", 2 / 3), ("no_regions", 0.5)]


@pytest.mark.process
@pytest.mark.parametrize("shape, cov", SHAPES, ids=[shape for shape, _ in SHAPES])
def test_report_shapes(tmp_path, shape, cov):
    """R89: a report carrying less than crapkit asked for still scores the rest."""
    toml = mini_repo.config([mini_repo.scope("s", ["src"], ["python"])],
                            [mini_repo.lane("py", "coveragepy", ["s"])])
    driver = mini_repo.build(tmp_path / "repo", {"crapkit.toml": toml, "src/a.py": SOURCE,
                                                 "recorded/py.json": _shape_report(shape)})

    result = driver.run("coverage", "--export", "scored.tsv")

    assert result.code == 0, result.stderr
    row = (driver.root / "scored.tsv").read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert (row[2].split("(")[0].strip(), float(row[11]), row[12]) == ("f", cov, "measured")


# --- the same refusals through a lane (R27, R28) -----------------------------------------------------

JS_SOURCE = "function f(x) {\n  return x;\n}\n"


def _coveragepy_lane(summary: dict) -> tuple[str, bytes]:
    region = {**_region(), "summary": {**_region()["summary"], **summary}}
    return "coveragepy", mini_repo.coveragepy_report({"src/a.py": {"functions": {"f": region},
                                                                   "missing_lines": [4]}})


def _istanbul_lane(count: str) -> tuple[str, bytes]:
    text = ISTANBUL_FILE.replace('"a.js"', '"src/a.js"').replace("COUNT", count)
    return "istanbul", text.encode()


# (case, lane): RFC 8259 section 6 has no NaN or Infinity; docs/lanes.md refuses a
# count that is not a nonnegative integer and a covered count over its total.
LANE_REFUSALS = [
    ("coveragepy-nan", _coveragepy_lane({"covered_lines": float("nan")})),
    ("coveragepy-covered-over-total", _coveragepy_lane({"covered_branches": 3})),
    ("coveragepy-string", _coveragepy_lane({"num_statements": "3"})),
    ("istanbul-infinity", _istanbul_lane("Infinity")),
    ("istanbul-fraction", _istanbul_lane("1.5")),
    ("istanbul-negative-statement", _istanbul_lane("-1")),
]


@pytest.mark.process
@pytest.mark.parametrize("case, lane", LANE_REFUSALS, ids=[case for case, _ in LANE_REFUSALS])
def test_impossible_counts_refuse_through_a_lane(tmp_path, case, lane):
    """R27 and R28: the lane fails, exit 5, and no row scores off the bad count."""
    parser, artifact = lane
    language, source = ("python", SOURCE) if parser == "coveragepy" else ("javascript", JS_SOURCE)
    suffix = "py" if parser == "coveragepy" else "js"
    toml = mini_repo.config([mini_repo.scope("s", ["src"], [language])],
                            [mini_repo.lane("cov", parser, ["s"])])
    driver = mini_repo.build(tmp_path / "repo", {"crapkit.toml": toml, f"src/a.{suffix}": source,
                                                 "recorded/cov.json": artifact})

    assert driver.run("coverage").code == 5
