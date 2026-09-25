"""crapkit's rows follow each field's definition, on a repo worked out by hand.

The repo below holds one probe per clause of a definition in definitions.tsv.
Every expected value in EXPECTED was worked from the definition and the probe's
source before crapkit ran: the branch arcs each call takes, the statements it
reaches, the ceiling its scope sets. coverage.py (the pinned oracle) measures
the Python probes for real; the TypeScript lane replays a hand-written istanbul
file with two empty functions, one called, one not, because only a function
with no statements reaches the invoked-or-not clause. crapkit then scores the
repo once, and each test reads the exported rows and two briefs.

The coverage.py counts are checked against the hand counts as well, so a
change in coverage.py shows up as the oracle moving, not as crapkit moving.
"""
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import sys

import pytest

import hang_guard
from accuracy.definitions import doc_places
from accuracy.kit import drive, repos, rulings, surfaces, tiers

pytestmark = pytest.mark.process

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "core"
paths = ["src/core"]
languages = ["python"]
target = 3

[[scope]]
name = "wide"
paths = ["src/wide"]
languages = ["python"]

[[scope]]
name = "bare"
paths = ["src/bare"]
languages = ["python"]

[[scope]]
name = "gen"
paths = ["src/gen"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "ts"
paths = ["src/ts"]
languages = ["typescript"]

[exclude]
globs = ["tests/**", "recorded/**"]

"""
LANES = (repos.lane_toml("py", ".crapkit/cov/py.json", "coveragepy", ["core", "wide"],
                         "recorded/py.json")
         + "\n" + repos.lane_toml("ts", ".crapkit/cov/ts.json", "istanbul", ["ts"],
                                  "recorded/ts.json"))

SHAPES = '''def half(x):
    if x:
        return 1
    return 2


def both(a, b):
    return a and b


def straight(values):
    total = sum(values)
    mean = total / len(values)
    return mean


def untaken(x):
    if x > 1:
        return x
    return 0


def split_up(a, b, c):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    return 0


def five(a, b, c, d):
    total = 0
    if a:
        total += 1
    if b:
        total += 2
    if c:
        total += 4
    if d:
        total += 8
    return total


def one_line(a, b): return a if a else b
'''

FLAT = "def flat(n):\n" + "".join(
    f"    if n == {i}:\n        return {i}\n" for i in range(7)) + "    return -1\n"

NEST = FLAT + '''

def deep(a, b, c):
    if a:
        if b:
            if c:
                return 1
    return 0


def with_in_if(path, strict):
    if strict:
        with open(path) as handle:
            if handle.readable():
                return handle.read()
    return None
'''

LINES = '''def counted(a, b):
    # a comment line

    total = a + b
    return total
'''

PARAMS = '''def three(a, b, c):
    return a


def none():
    return 0
'''

ONE_BRANCH = '''def {name}(x):
    if x:
        return 1
    return 0
'''

GENERATED = '''def generated(a, b):
    if a:
        return 1
    if b:
        return 2
    return 0
'''

EMPTY_TS = "export function called(): void {}\nexport function idle(): void {}\n"

# The coverage.py run: every probe module is imported, so the artifact speaks
# about it; src/wide/unimported.py is not, so no artifact mentions it.
DRIVER = '''import sys
sys.path.insert(0, ".")
from src.core import shapes
from src.wide import lines, nest, params

shapes.half(True)
shapes.both(False, True)
try:
    shapes.straight([])
except ZeroDivisionError:
    pass
shapes.five(True, True, True, True)
shapes.one_line(1, 2)
'''

FILES = {"crapkit.toml": CONFIG + LANES, "src/core/shapes.py": SHAPES, "src/wide/nest.py": NEST,
         "src/wide/lines.py": LINES, "src/wide/params.py": PARAMS,
         "src/wide/unimported.py": ONE_BRANCH.format(name="lonely"),
         "src/bare/plain.py": ONE_BRANCH.format(name="plain"), "src/gen/gen.py": GENERATED,
         "src/ts/empty.ts": EMPTY_TS, "tests/drive_defs.py": DRIVER}
SPEC = repos.Spec(steps=(repos.Commit(files=FILES, message="definition probes"),))


def _istanbul_function(name: str, line: int) -> dict:
    return {"name": name, "line": line,
            "decl": {"start": {"line": line, "column": 16}, "end": {"line": line, "column": 22}},
            "loc": {"start": {"line": line, "column": 7}, "end": {"line": line, "column": 33}}}


# istanbul's coverage-final.json shape (istanbul-lib-coverage FileCoverage):
# two functions with no statements and no branches, `called` run once.
ISTANBUL = {"src/ts/empty.ts": {
    "path": "src/ts/empty.ts", "statementMap": {}, "branchMap": {}, "s": {}, "b": {},
    "fnMap": {"0": _istanbul_function("called", 1), "1": _istanbul_function("idle", 2)},
    "f": {"0": 1, "1": 0}}}


@dataclass(frozen=True)
class Want:
    ccn: int
    cov: Fraction
    flag: str | None
    ceiling: int
    shared: bool = False


# Worked by hand from the source above and the definitions, before any run:
# - half: `if x` has two arcs, half(True) takes one: 1/2.
# - both: `and` adds 1 to ccn and no arc (D9), so its one statement ran: 1/1.
# - straight: no branch; [] raises on line 3, so 2 of 3 statements ran.
# - untaken, split_up: never called; branches 0 of 2 and 0 of 6.
# - five: each of four `if`s takes its true arc only: 4 of 8.
# - one_line: its body shares the def line, which runs at import, so no call
#   can show; the definition floors it at 0 and asks for split-lines.
# - lonely: no test imports its file, so no artifact names it: untested, 0.
# - plain: its scope has no lane: no-lane, 0. generated: cc-only, 0.
# - called, idle: no statements, so invoked-or-not: 1 and 0.
EXPECTED = {
    ("src/core/shapes.py", "half"): Want(2, Fraction(1, 2), "measured", 3),
    ("src/core/shapes.py", "both"): Want(2, Fraction(1), "measured", 3),
    ("src/core/shapes.py", "straight"): Want(1, Fraction(2, 3), "measured", 3),
    ("src/core/shapes.py", "untaken"): Want(2, Fraction(0), "measured", 3),
    ("src/core/shapes.py", "split_up"): Want(4, Fraction(0), "measured", 3),
    ("src/core/shapes.py", "five"): Want(5, Fraction(1, 2), "measured", 3),
    ("src/core/shapes.py", "one_line"): Want(2, Fraction(0), None, 3, shared=True),
    ("src/wide/unimported.py", "lonely"): Want(2, Fraction(0), "untested", 6),
    ("src/bare/plain.py", "plain"): Want(2, Fraction(0), "no-lane", 6),
    ("src/gen/gen.py", "generated"): Want(3, Fraction(0), "cc-only", 6),
    ("src/ts/empty.ts", "called"): Want(1, Fraction(1), "measured", 6),
    ("src/ts/empty.ts", "idle"): Want(1, Fraction(0), "measured", 6),
}
# The remedy each row must carry, read off README's table by hand.
REMEDIES = {"half": "ok", "both": "ok", "straight": "ok", "untaken": "add-tests",
            "split_up": "decompose", "five": "decompose", "one_line": "split-lines",
            "lonely": "ok", "plain": "ok", "generated": "ok", "called": "ok", "idle": "ok"}
# coverage.py's own summary counts for the probes, by hand: (branches, covered
# branches, statements, covered statements).
COUNTS = {"half": (2, 1, 3, 2), "both": (0, 0, 1, 1), "straight": (0, 0, 3, 2),
          "untaken": (2, 0, 3, 0), "five": (8, 4, 10, 10)}


@dataclass(frozen=True)
class Measured:
    rows: dict
    sarif: dict
    coverage_py: dict
    briefs: dict


def _record_coverage(root, oracle) -> dict:
    """Run the driver under coverage.py with branch measurement, then write the
    JSON report the py lane replays."""
    oracle("coverage")
    env = drive.child_env({"PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_PROCESS_START": None,
                           "COVERAGE_PROCESS_CONFIG": None, "COV_CORE_DATAFILE": None})
    data = f"--data-file={root.parent / 'definitions.coverage'}"
    for argv in (["run", "--branch", data, "tests/drive_defs.py"],
                 ["json", data, "-o", "recorded/py.json"]):
        done = hang_guard.run([sys.executable, "-m", "coverage", *argv], cwd=root, env=env,
                              text=True, encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stdout + done.stderr
    return json.loads((root / "recorded" / "py.json").read_text(encoding="utf-8"))


def _scored(root, out) -> tuple[dict, dict]:
    """The run's exported rows by (path, bare name), and its SARIF document."""
    driver = drive.Driver(root, date_now=repos.EPOCH + 86_400)
    done = driver.run("coverage", "--json", "--export", str(out / "scored.tsv"),
                      "--sarif", str(out / "coverage.sarif"))
    assert done.code == 0, done.stderr
    _, rows = surfaces.read_tsv((out / "scored.tsv").read_text(encoding="utf-8"))
    sarif = json.loads((out / "coverage.sarif").read_text(encoding="utf-8"))
    return {(row["path"], surfaces.bare_name(row["long_name"])): row for row in rows}, sarif


def _briefs(root) -> dict:
    driver = drive.Driver(root, date_now=repos.EPOCH + 86_400)
    asks = {"five": ("src/core/shapes.py", "five"), "deep": ("src/wide/nest.py", "deep"),
            "three": ("src/wide/params.py", "three")}
    return {key: driver.json("brief", path, name) for key, (path, name) in asks.items()}


@pytest.fixture(scope="module")
def measured(repo_templates, tmp_path_factory, oracle) -> Measured:
    tiers.require_process("git, coverage.py and the crapkit CLI")
    work = tmp_path_factory.mktemp("definitions")
    root = repo_templates.copy(SPEC, work / "repo").root
    (root / "recorded").mkdir(exist_ok=True)
    (root / "recorded" / "ts.json").write_text(json.dumps(ISTANBUL), encoding="utf-8")
    coverage_py = _record_coverage(root, oracle)
    rows, sarif = _scored(root, work)
    return Measured(rows, sarif, coverage_py, _briefs(root))


def _row(measured: Measured, key: tuple) -> dict:
    assert key in measured.rows, f"no scored row for {key}: {sorted(measured.rows)}"
    return measured.rows[key]


def _crap(want: Want) -> Fraction:
    """ccn^2 x (1 - cov)^3 + ccn, and ccn on a cc-only row."""
    if want.flag == "cc-only":
        return Fraction(want.ccn)
    return want.ccn ** 2 * (1 - want.cov) ** 3 + want.ccn


def _readme_remedy(want: Want) -> str:
    """README's remedy table, top row first."""
    if want.ccn > want.ceiling:
        return "decompose"
    if _crap(want) <= want.ceiling:
        return "ok"
    return "split-lines" if want.shared else "add-tests"


# --- the oracle agrees with the hand counts -------------------------------------------------

def _summary(coverage_py: dict, name: str) -> tuple:
    """coverage.py's own counts for a shapes.py function; its file keys use the
    OS separator."""
    files = {key.replace("\\", "/"): value for key, value in coverage_py["files"].items()}
    counts = files["src/core/shapes.py"]["functions"][name]["summary"]
    return (counts["num_branches"], counts["covered_branches"], counts["num_statements"],
            counts["covered_lines"])


@pytest.mark.parametrize("name", sorted(COUNTS))
def test_coverage_py_counts_what_the_hand_counts_say(measured, name):
    assert _summary(measured.coverage_py, name) == COUNTS[name]


def test_coverage_py_gives_and_or_no_branch_arc(measured):
    """D9's claim, from the oracle itself: `return a and b` adds 1 to ccn and
    coverage.py measures no branch for it, while `if x` gives two."""
    assert _summary(measured.coverage_py, "both")[0] == 0
    assert _summary(measured.coverage_py, "half")[0] == 2


# --- one test per definition ------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_cov_follows_its_definition(measured, key):
    row = _row(measured, key)

    assert (int(row["ccn"]), float(row["cov"])) == (EXPECTED[key].ccn,
                                                    pytest.approx(float(EXPECTED[key].cov)))


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_crap_follows_its_definition(measured, key):
    got = float(_row(measured, key)["crap"])

    assert math.isclose(got, _crap(EXPECTED[key]), rel_tol=1e-12)


@pytest.mark.parametrize("key", sorted(key for key, want in EXPECTED.items() if want.flag))
def test_flag_follows_its_definition(measured, key):
    assert _row(measured, key)["flag"] == EXPECTED[key].flag


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_remedy_follows_its_definition(measured, key):
    want = REMEDIES[key[1]]

    assert _readme_remedy(EXPECTED[key]) == want
    assert _row(measured, key)["remedy"] == want


@pytest.mark.parametrize("name, depth", [("flat", 1), ("deep", 3), ("with_in_if", 2)])
def test_nesting_follows_its_definition(measured, name, depth):
    """agent-json.md's own examples: seven flat ifs read 1, three-deep reads 3,
    an if inside a with inside an if reads 2 (with adds no level)."""
    assert int(_row(measured, ("src/wide/nest.py", name))["nesting"]) == depth


def test_nloc_follows_its_definition(measured):
    """The def line and two statements: the comment line and the blank line do not count."""
    assert int(_row(measured, ("src/wide/lines.py", "counted"))["nloc"]) == 3


def test_params_follow_their_definition(measured):
    three = _row(measured, ("src/wide/params.py", "three"))
    none = _row(measured, ("src/wide/params.py", "none"))
    brief = measured.briefs["three"]

    assert (int(three["params"]), int(none["params"])) == (3, 0)
    assert [param["name"] for param in brief["params"]] == ["a", "b", "c"]
    assert brief["scored"]["params"] == len(brief["params"])


def test_target_follows_its_definition(measured):
    """core sets target 3; wide sets none and inherits the [crapkit] target 6."""
    five, deep = measured.briefs["five"], measured.briefs["deep"]

    assert (five["target"], five["gate_rule"]["ceiling"]) == (3, 3)
    assert (deep["target"], deep["gate_rule"]["ceiling"]) == (6, 6)


def test_est_uncovered_paths_rounds_half_to_even(measured):
    """D10: five has ccn 5 and cov 1/2, so (1 - cov) x ccn is 2.5; half to even
    gives 2 where half up gives 3. est_splits is ceil(5 / 3) = 2."""
    five = measured.briefs["five"]

    assert (five["est_uncovered_paths"], five["est_splits"]) == (2, 2)


# --- SARIF: what a code-scanning reader sees ------------------------------------------------

# lizard's long names for the probes, as its Python reader spells them.
LONG_NAMES = {"untaken": "untaken( x )", "split_up": "split_up( a , b , c )",
              "five": "five( a , b , c , d )", "one_line": "one_line( a , b )",
              "flat": "flat( n )", "deep": "deep( a , b , c )",
              "with_in_if": "with_in_if( path , strict )"}
NEST_WANT = {"flat": Want(8, Fraction(0), "measured", 6), "deep": Want(4, Fraction(0), "measured", 6),
             "with_in_if": Want(3, Fraction(0), "measured", 6)}
NEST_REMEDIES = {"flat": "decompose", "deep": "add-tests", "with_in_if": "add-tests"}


def _over_target() -> dict:
    """Every probe whose hand CRAP is over its scope's ceiling: the rows the
    over-target rule must report, with the message the definitions predict."""
    wants = {key[1]: want for key, want in EXPECTED.items()} | NEST_WANT
    remedies = REMEDIES | NEST_REMEDIES
    return {name: (f"{LONG_NAMES[name]}: CRAP {float(_crap(want)):.1f} over ceiling "
                   f"{want.ceiling} (ccn {want.ccn}, cov {float(want.cov):.0%}) -> {remedies[name]}")
            for name, want in wants.items() if _crap(want) > want.ceiling}


def test_sarif_over_target_messages_follow_the_definitions(measured):
    """crap over the scope ceiling is what over-target means; the message prints
    crap at one decimal, the ceiling, ccn, cov as a percent of the 0-1 share,
    and the remedy. five: 25 x (1/2)^3 + 5 = 8.125, printed 8.1, cov 50%."""
    [run] = measured.sarif["runs"]
    got = sorted(result["message"]["text"] for result in run["results"]
                 if result["ruleId"] == "crapkit/over-target")

    assert got == sorted(_over_target().values())


def test_the_served_sarif_rules_are_the_ones_sarif_py_declares(measured):
    """The SARIF place is read off sarif.py's syntax tree; the document a run
    writes must carry those same rule texts."""
    [run] = measured.sarif["runs"]
    served = [rule["shortDescription"]["text"] for rule in run["tool"]["driver"]["rules"]]

    assert served == doc_places.sarif_texts()[:len(served)]


# --- a discrepancy the nloc definition found ---------------------------------------------------

@rulings.applies("definitions-1")
def test_nloc_of_a_typed_one_line_ts_function(measured):
    """`export function called(): void {}` sits on line 1 alone, so by the nloc
    definition it holds one line of code; the TypeScript compiler ends its
    FunctionDeclaration on line 1 too. crapkit (like stock lizard 1.24.0) ends
    it on line 2, where `idle` starts, and counts 2."""
    row = _row(measured, ("src/ts/empty.ts", "called"))

    rulings.pin_ruling("definitions-1", crapkit=row["nloc"], oracle=1)
