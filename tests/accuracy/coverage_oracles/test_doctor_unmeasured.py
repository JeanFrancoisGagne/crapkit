"""Doctor's unmeasured-directory finding, against a clean-room model of its stated rule.

The rule, as doctor states it (the WARN line and docs/agent-json.md#doctor,
"warnings": unmeasured directories): a directory where every scored function
outside a coverage_optional scope is flagged untested, while a test file for
that directory exists, is a lane that runs without measuring the code it
covers. The model below spells the matching test out the way the finding's
contract does:

- a test file is named foo.test.ext, foo.spec.ext, test_foo.py or foo_test.ext,
  with an extension a reader parses; it names the stem foo;
- only tests in the language family of the directory's scored files count
  (one family for JavaScript, TypeScript, TSX and Vue);
- the nearest test wins: one in the directory, then the one below it with the
  fewest path components, then a tests/ mirror (the test directory with its
  test, tests, __tests__, spec and specs parts dropped ends the source
  directory's path), then a same-stem test anywhere; ties go alphabetically.

1. A repo with one directory per tier, a family mismatch, a measured directory
   and a coverage_optional one: `doctor --json` warns exactly where the model
   does, naming the model's test (R109: the CLI's production path, not a copy
   of the rule; R110: the nearby test).
2. The pure rule over drawn repos equals the model.
3. MCP check_config carries doctor's warnings; the store's per-path grouping
   equals a grouping of the run's own export (self-diff, not independent).
"""
import json

from hypothesis import given, strategies as st
import pytest

from accuracy.coverage_oracles import mini_repo, under_test
from accuracy.kit import repos, surfaces
from accuracy.kit.settings import pure

DOCTOR = under_test.crapkit("doctor")
# README.md#which-languages-crapkit-reads, the extensions the fixtures use.
FAMILY = {".py": "python", ".go": "go", ".ts": "javascript", ".tsx": "javascript",
          ".js": "javascript", ".vue": "javascript"}
TEST_PARTS = {"test", "tests", "__tests__", "spec", "specs"}


def _dir(path: str) -> str:
    return path.rpartition("/")[0]


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.split("/") if part)


def _family(path: str) -> str | None:
    return next((family for suffix, family in FAMILY.items() if path.endswith(suffix)), None)


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].split(".")[0]


# (does the file name match, the stem it names): the four test-name conventions.
CONVENTIONS = (
    (lambda name, stem: ".test." in name, lambda stem: stem),
    (lambda name, stem: ".spec." in name, lambda stem: stem),
    (lambda name, stem: stem.startswith("test_"), lambda stem: stem[len("test_"):]),
    (lambda name, stem: stem.endswith("_test"), lambda stem: stem[:-len("_test")]),
)


def named_stem(path: str) -> str | None:
    """The stem a test file names, or None when it is not a test file."""
    name, stem = path.rsplit("/", 1)[-1], _stem(path)
    if _family(path) is None:
        return None
    return next((named(stem) for matches, named in CONVENTIONS if matches(name, stem)), None)


def _mirror(test_dir: str, source_dir: str) -> bool:
    kept = tuple(part for part in _parts(test_dir) if part not in TEST_PARTS)
    dropped = len(kept) < len(_parts(test_dir))
    return bool(kept) and dropped and _parts(source_dir)[-len(kept):] == kept


def _below(directory: str, tests: list[str]) -> list[str]:
    inside = [path for path in tests if directory and _dir(path).startswith(directory + "/")]
    return sorted(inside, key=lambda path: (len(_parts(path)), path))[:1]


def _tests(tracked: list[str], families: set) -> list[str]:
    return sorted(path for path in tracked if named_stem(path) and _family(path) in families)


def _tiers(directory: str, stems: set, tests: list[str]) -> tuple[list[str], ...]:
    """The candidates of each tier, nearest tier first."""
    return (_where(tests, lambda path: _dir(path) == directory), _below(directory, tests),
            _where(tests, lambda path: _mirror(_dir(path), directory)),
            _where(tests, lambda path: named_stem(path) in stems))


def _where(tests: list[str], keep) -> list[str]:
    return [path for path in tests if keep(path)]


def nearest_test(directory: str, stems: set, families: set, tracked: list[str]) -> str | None:
    tiers = _tiers(directory, stems, _tests(tracked, families))
    return next((found[0] for found in tiers if found), None)


def model(rows: list[tuple[str, str]], tracked: list[str]) -> list[tuple[str, int, str]]:
    """(directory, functions, test) for every directory the rule warns about;
    `rows` are (path, flag) of the scored functions outside optional scopes."""
    by_dir: dict[str, list[tuple[str, str]]] = {}
    for path, flag in rows:
        by_dir.setdefault(_dir(path), []).append((path, flag))
    found = []
    for directory, members in sorted(by_dir.items()):
        test = _unmeasured_test(directory, members, tracked)
        found += [(directory, len(members), test)] if test else []
    return found


def _unmeasured_test(directory: str, members: list, tracked: list[str]) -> str | None:
    if any(flag != "untested" for _, flag in members):
        return None
    return nearest_test(directory, {_stem(path) for path, _ in members},
                        {_family(path) for path, _ in members}, tracked)


# --- 1. the CLI on one directory per tier ---------------------------------------------------------

SOURCE = "def {name}(x):\n    if x:\n        return 1\n    return 0\n"
FILES = {
    "pkg/a/mod_a.py": SOURCE, "pkg/a/test_mod_a.py": SOURCE,             # a test in the directory
    "pkg/b/mod_b.py": SOURCE, "pkg/b/sub/deep/test_x.py": SOURCE,        # the nearest one below
    "pkg/b/sub/test_y.py": SOURCE,
    "pkg/c/mod_c.py": SOURCE, "tests/c/test_zzz.py": SOURCE,             # a tests/ mirror
    "pkg/d/parser.py": SOURCE, "elsewhere/test_parser.py": SOURCE,       # the same stem anywhere
    "pkg/e/mod_e.py": SOURCE, "pkg/e/mod_e.test.ts": "export const k = 1;\n",  # another family
    "pkg/f/mod_f.py": SOURCE, "pkg/f/test_mod_f.py": SOURCE,             # measured
    "opt/g/mod_g.py": SOURCE, "opt/g/test_mod_g.py": SOURCE,             # coverage_optional
    "pkg/h/mod_h.py": SOURCE,                                            # no test at all
}


def _region(start: int) -> dict:
    return {"start_line": start, "executed_lines": [2, 3], "missing_lines": [4],
            "excluded_lines": [], "executed_branches": [[2, 3]], "missing_branches": [[2, 4]],
            "summary": {"num_statements": 3, "covered_lines": 2, "num_branches": 2,
                        "covered_branches": 1}}


def _tree() -> dict:
    files = {path: text.replace("{name}", "f" + _stem(path).replace(".", "_"))
             for path, text in FILES.items()}
    report = {"pkg/f/mod_f.py": {"functions": {f"f{_stem('pkg/f/mod_f.py')}": _region(1)},
                                 "missing_lines": [4]}}
    config = mini_repo.config(
        [mini_repo.scope("pkg", ["pkg"], ["python", "typescript"]),
         mini_repo.scope("opt", ["opt"], ["python"], optional=True)],
        [mini_repo.lane("py", "coveragepy", ["pkg"])])
    return {"crapkit.toml": config, "recorded/py.json": mini_repo.coveragepy_report(report), **files}


@pytest.fixture(scope="module")
def doctored(tmp_path_factory):
    """(driver, doctor --json, the scored rows, git ls-files)."""
    driver = mini_repo.build(tmp_path_factory.mktemp("doctor") / "repo", _tree())
    assert driver.run("coverage", "--export", "scored.tsv").code == 0
    rows = surfaces.read_tsv((driver.root / "scored.tsv").read_text(encoding="utf-8"))[1]
    tracked = repos.git(driver.root, "ls-files").split()
    return driver, driver.json("doctor"), rows, tracked


def _warning(directory: str, functions: int, test: str) -> str:
    """The WARN line doctor prints for one directory."""
    return (f"{directory}: {functions} function(s) all flagged untested while {test} exists "
            "— tests exist but no lane measures them")


def _expected(rows: list[dict], tracked: list[str]) -> list[str]:
    scored = [(row["path"], row["flag"]) for row in rows if row["flag"] != "cc-only"]
    return [_warning(*finding) for finding in model(scored, tracked)]


def _unmeasured(warnings: list[str]) -> list[str]:
    return [line for line in warnings if "all flagged untested" in line]


@pytest.mark.process
def test_rule_model_vs_production_path(doctored):
    """R109: doctor --json, through the store and the CLI, warns where the model does."""
    _, payload, rows, tracked = doctored

    assert _unmeasured(payload["warnings"]) == _expected(rows, tracked)


@pytest.mark.process
def test_nearby_test_matches_model(doctored):
    """R110: each tier names its own test, a TypeScript test never answers for a
    Python directory, and a measured or coverage_optional directory is quiet."""
    _, payload, _, _ = doctored
    named = {line.split(":")[0]: line.split(" while ")[1].split(" exists")[0]
             for line in _unmeasured(payload["warnings"])}

    assert {key: named.get(key) for key in ("pkg/a", "pkg/b", "pkg/c", "pkg/d", "pkg/e",
                                            "pkg/f", "opt/g", "pkg/h")} == {
        "pkg/a": "pkg/a/test_mod_a.py", "pkg/b": "pkg/b/sub/test_y.py",
        "pkg/c": "tests/c/test_zzz.py", "pkg/d": "elsewhere/test_parser.py",
        "pkg/e": None, "pkg/f": None, "opt/g": None, "pkg/h": None}


@pytest.mark.process
def test_check_config_carries_doctor_s_warnings(doctored):
    driver, payload, _, _ = doctored
    result = driver.mcp([("check_config", {})])[0]
    answer = result.get("structuredContent") or json.loads(result["content"][0]["text"])

    assert answer["warnings"] == payload["warnings"]


@pytest.mark.process
def test_the_store_grouping_equals_the_export_grouping(doctored):
    """Self-diff: store.count_by_path against the run's own export, grouped here."""
    driver, _, rows, _ = doctored
    store = under_test.crapkit("store").SnapshotStore(driver.root / ".crapkit" / "crap.sqlite")
    run_id = max(run["id"] for run in store.list_runs())
    found = store.count_by_path(run_id, flag="untested", skip_scopes=frozenset({"opt"}))

    assert [tuple(item) for item in found] == _grouped(rows)


def _grouped(rows: list[dict]) -> list[tuple[str, int, int]]:
    """(path, functions, functions not untested) per path outside the optional scope."""
    grouped: dict[str, list[int]] = {}
    for row in (row for row in rows if row["flag"] != "cc-only"):
        counts = grouped.setdefault(row["path"], [0, 0])
        counts[0] += 1
        counts[1] += row["flag"] != "untested"
    return sorted((path, *counts) for path, counts in grouped.items())


# --- 2. the pure rule over drawn repos ----------------------------------------------------------

DIRS = ["", "src", "src/api", "src/api/v1", "core", "tests", "tests/api", "src/api/tests", "web"]
SOURCES = ["parser.py", "api.py", "handler.ts", "util.go"]
TESTS = ["test_parser.py", "parser_test.py", "handler.test.ts", "handler.spec.ts", "util_test.go",
         "_mermaid_test.md", "test_api.py"]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


@given(st.dictionaries(st.tuples(st.sampled_from(DIRS), st.sampled_from(SOURCES)),
                       st.tuples(st.integers(1, 3), st.integers(0, 1)), min_size=1, max_size=6),
       st.sets(st.tuples(st.sampled_from(DIRS), st.sampled_from(TESTS)), max_size=6))
@pure
def test_the_rule_equals_the_model(scored, tests):
    counts = [(_join(*key), functions, others) for key, (functions, others) in sorted(scored.items())]
    tracked = sorted({_join(*key) for key in tests} | {path for path, _, _ in counts})
    found = DOCTOR.unmeasured_directories(counts, tracked)

    assert [tuple(item) for item in found] == model(_flags(counts), tracked)


def _flags(counts: list[tuple[str, int, int]]) -> list[tuple[str, str]]:
    """One (path, flag) per function: the first `others` measured, the rest untested."""
    return [(path, "measured" if index < others else "untested")
            for path, functions, others in counts for index in range(functions)]
