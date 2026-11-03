"""A cc-only scope that gains a coverage lane: the flip, from the README start.

A mixed repo holds a Python scope `py` under `coverage_optional` and a Go
scope `go`, both cc-only, and seeds. Its three Python functions sit at ccn 8,
so each mark is 8.0000. The user then adds a coveragepy lane to `py` and drops
its `coverage_optional`. Measured at 0%, 50% and 100% branch coverage the three
score 72, 16 and 8, so the marks the cc-only seed wrote would fail verify at
exit 7. The marks file's stamp says `py` was cc-only, so doctor warns, verify
refuses at exit 3 and both link the upgrade guide's procedure; the scoped seed
then re-marks `py` as `ratchet.SEED_RAISES` says and leaves `go` alone.

Every step runs in this process, so a case can patch `SEED_RAISES` to the two
answers the shipped table did not take. The lane is a generator that writes the coverage.py
JSON report the three coverage levels need.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from crapkit import ratchet
from crapkit.cli import main
from crapkit.ratchet import ScopeStamp, ScopeStamps, stamp_conflict

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"
EXPORT = ".crapkit/export.tsv"
CCN = 8
# The section a flip refusal links; ratchet.FLIP_PROCEDURE must spell it.
FLIP_PROCEDURE = "docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one"
# Branch coverage of the three functions: 0 of 14, 7 of 14 and 14 of 14.
COVERED = {"none_run": 0, "half_run": 7, "all_run": 14}
BRANCHES = 14


def _function(name: str) -> str:
    """A function of 7 ifs: ccn 8, 14 branches, 15 statements in lines 2 to 16."""
    body = "".join(f"    if {arg}:\n        return {n}\n" for n, arg in enumerate("abcdefg", 1))
    return f"def {name}(a, b, c, d, e, f, g):\n{body}    return 0\n\n"


FLIP_PY = "".join(_function(name) for name in COVERED)
STARTS = {name: 1 + 17 * n for n, name in enumerate(COVERED)}

ROUTE_GO = "package main\n\nfunc Route(code int) string {\n" + "".join(
    f"\tif code == {n} {{\n\t\treturn \"{n}\"\n\t}}\n" for n in range(1, 9)) + "\treturn \"\"\n}\n"
ROUTE_CCN = 9

# The lane's command: coverage.py's JSON report for py/flip.py, each function
# at its share of branches, and one line per run in lane-runs.txt.
MAKE_COV = f'''import json
import os

covered = {COVERED!r}
starts = {STARTS!r}
functions = {{}}
for name, hit in covered.items():
    lines = list(range(starts[name] + 1, starts[name] + 16))
    ran = len(lines) if hit == {BRANCHES} else (8 if hit else 0)
    functions[name] = {{"start_line": starts[name], "executed_lines": lines[:ran],
                        "missing_lines": lines[ran:], "excluded_lines": [],
                        "summary": {{"covered_lines": ran, "num_statements": len(lines),
                                    "num_branches": {BRANCHES}, "covered_branches": hit,
                                    "excluded_lines": 0}}}}
missing = [line for fn in functions.values() for line in fn["missing_lines"]]
report = {{"meta": {{"branch_coverage": True}},
          "files": {{"py/flip.py": {{"functions": functions, "missing_lines": missing}}}}}}
os.makedirs(os.path.join(".crapkit", "cov"), exist_ok=True)
with open(os.path.join(".crapkit", "cov", "py.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, sort_keys=True)
with open("lane-runs.txt", "a", encoding="utf-8") as fh:
    fh.write("ran\\n")
'''

HEAD = "[crapkit]\ntarget = 6\n\n"
GO = '[[scope]]\nname = "go"\npaths = ["go"]\nlanguages = ["go"]\ncoverage_optional = true\n\n'
LANE = ('[[lane]]\nname = "py"\ncommand = "python make_cov.py"\nartifact = ".crapkit/cov/py.json"\n'
        'parser = "coveragepy"\nscopes = ["{scope}"]\n\n[crapkit.scoped_tests]\n{scope} = "python -c pass"\n')


def _py(name: str = "py", optional: bool = False) -> str:
    extra = "coverage_optional = true\n" if optional else ""
    return f'[[scope]]\nname = "{name}"\npaths = ["py"]\nlanguages = ["python"]\n{extra}\n'


CC_ONLY = HEAD + _py(optional=True) + GO
MEASURED = HEAD + _py() + GO + LANE.format(scope="py")
RENAMED = HEAD + _py("app") + GO + LANE.format(scope="app")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                   check=True, capture_output=True, timeout=120)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    """The mixed repo, committed, with the lane's generator left untracked so
    crapkit never scores it."""
    monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON", raising=False)
    root = tmp_path / "mixed"
    (root / "py").mkdir(parents=True)
    (root / "go").mkdir()
    (root / "py" / "flip.py").write_text(FLIP_PY, encoding="utf-8", newline="\n")
    (root / "go" / "route.go").write_text(ROUTE_GO, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".crapkit/\nmake_cov.py\nlane-runs.txt\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    (root / "make_cov.py").write_text(MAKE_COV, encoding="utf-8", newline="\n")
    return root


def cli(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def configure(repo: Path, config: str) -> None:
    (repo / "crapkit.toml").write_text(config, encoding="utf-8", newline="\n")


def start(repo: Path, capsys, config: str = CC_ONLY) -> str:
    """The README start after `init`, on `config`; the marks it seeds, committed."""
    configure(repo, config)
    for step in (["doctor"], ["coverage"], ["worklist"], ["ratchet", "seed"], ["verify"]):
        code, out, err = cli(repo, capsys, *step)
        assert code == 0, (step, code, out, err)
    _commit(repo, "seed")
    return (repo / MARKS).read_text(encoding="utf-8")


def marks(repo: Path, scope_dir: str = "py/") -> dict[str, str]:
    """{function name: mark} for the marks under `scope_dir`."""
    rows = (line.split("\t") for line in (repo / MARKS).read_text(encoding="utf-8").splitlines()
            if line.startswith(scope_dir))
    return {re.split(r"[ (]", name, maxsplit=1)[0]: mark for _, name, mark in rows}


def lines_of(text: str, scope: str, scope_dir: str) -> list[str]:
    """A scope's `#scope` line and its marks, byte for byte."""
    return [line for line in text.splitlines(keepends=True)
            if line.startswith((f"#scope\t{scope}\t", scope_dir))]


def lane_runs(repo: Path) -> int:
    runs = repo / "lane-runs.txt"
    return len(runs.read_text(encoding="utf-8").splitlines()) if runs.exists() else 0


def export_rows(repo: Path) -> dict[str, dict[str, str]]:
    """`coverage --export`'s rows under py/, by function name."""
    header, *rows = [line.split("\t") for line in (repo / EXPORT).read_text(encoding="utf-8").splitlines()]
    found = (dict(zip(header, row)) for row in rows)
    return {re.split(r"[ (]", row["long_name"], maxsplit=1)[0]: row for row in found
            if row["path"].startswith("py/")}


def answer(monkeypatch, table: str) -> None:
    """The shipped table ("shipped") or one of the two `SEED_RAISES` rows it did not take."""
    if table != "shipped":
        monkeypatch.setattr(ratchet, "SEED_RAISES", dict.fromkeys(ratchet.SEED_RAISES, table == "any-move"))


# --- the rendering, at stamp_conflict ------------------------------------------------

PY13 = {"python": 13}
COV1 = {"coveragepy": 1}
IST1 = {"istanbul": 1}
LIZARD = "1.24.0"


def _refusal(then, now) -> str:
    recorded = ScopeStamps(LIZARD, {"api": ScopeStamp(PY13, then)})
    running = ScopeStamps(LIZARD, {"api": ScopeStamp(PY13, now)})
    return stamp_conflict(recorded, running, frozenset({"api"})).refusal(MARKS)


@pytest.mark.parametrize("then, now, flip", [
    ("cc-only", COV1, "; api was cc-only and is now measured by its coveragepy lane(s)"),
    ("no-lane", IST1, "; api was no-lane and is now measured by its istanbul lane(s)"),
    (COV1, "cc-only", None),
    (COV1, {"coveragepy": 1, "istanbul": 1}, None),
], ids=["cc-only-to-coveragepy", "no-lane-to-istanbul", "coveragepy-to-cc-only", "a-second-reader"])
def test_the_coverage_kind_renders_the_flip_only_from_no_reader_to_readers(then, now, flip):
    refusal = _refusal(then, now)

    assert "1 scope(s): api (coverage " in refusal and "ratchet seed --scope api`" in refusal
    if flip is None:
        assert "upgrading.md" not in refusal, refusal
    else:
        assert refusal.endswith(f"{flip}, so its marks hold no coverage yet: follow {FLIP_PROCEDURE}"), refusal


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation gone, spaces to dashes."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def test_the_link_names_the_upgrade_guide_section_inside_the_metric_stamp_block():
    assert ratchet.FLIP_PROCEDURE == FLIP_PROCEDURE
    page, anchor = FLIP_PROCEDURE.split("#")
    text = (ROOT / page).read_text(encoding="utf-8")
    block = text[text.index("<!-- 0.9.0:metric-stamp -->"):text.index("<!-- /0.9.0:metric-stamp -->")]
    headings = re.findall(r"^### (.+)$", block, re.M)

    assert "Turning a cc-only scope into a measured one" in headings
    assert anchor in [_anchor(heading) for heading in re.findall(r"^#+ (.+)$", text, re.M)]


# --- doctor --------------------------------------------------------------------------

def _flip_warnings(repo: Path, capsys) -> tuple[int, list[str]]:
    code, out, err = cli(repo, capsys, "doctor", "--json")
    warnings = json.loads(out)["warnings"]
    return code, [w for w in warnings if MARKS in w]


def test_doctor_warns_once_for_a_flipped_scope_and_names_its_seed(repo, capsys):
    start(repo, capsys)
    configure(repo, MEASURED)

    code, found = _flip_warnings(repo, capsys)

    assert code == 0
    assert len(found) == 1, found
    assert "py (coverage cc-only to coveragepy=1)" in found[0]
    assert "ratchet seed --scope py`" in found[0] and FLIP_PROCEDURE in found[0]


def test_doctor_says_nothing_of_the_marks_while_the_scope_keeps_its_mode(repo, capsys):
    start(repo, capsys)

    code, found = _flip_warnings(repo, capsys)

    assert (code, found) == (0, [])


def test_doctor_warns_for_a_single_number_file_whose_scope_has_a_lane_0_8_x_never_read(repo, capsys,
                                                                                      monkeypatch):
    """No reader 0.8.x never read ships yet, and the config schema refuses an
    unknown parser: a stand-in parser registered in the tables the running stamp
    reads, on a config built past the loader, makes the flip a 0.8.x file's
    inferred mode meets. doctor's findings reach `_doctor_report` as built."""
    from crapkit import coverage_format
    from crapkit.cli.admin import NO_PACKAGES, _doctor_marks_stamp, _doctor_report
    from crapkit.config import load_config_text

    start(repo, capsys)
    text = (repo / MARKS).read_text(encoding="utf-8")
    entries, _ = ratchet.read_ratchet(text)
    (repo / MARKS).write_text(ratchet.dump_ratchet(entries, stamp=ratchet.MetricStamp.parse(
                                                       f"crapkit-analysis=13 lizard={ratchet.read_stamp(text).lizard}"),
                                                   key_version=ratchet.read_key_version(text),
                                                   file_name=MARKS), encoding="utf-8", newline="\n")
    monkeypatch.setitem(coverage_format._FORMATS, "lcov", coverage_format._FORMATS["coveragepy"])
    monkeypatch.setitem(coverage_format.READER_VERSIONS, "lcov", 1)
    measured = load_config_text(MEASURED, root=repo)
    lcov = measured._replace(lanes=tuple(lane._replace(parser="lcov") for lane in measured.lanes))
    coveragepy_only = load_config_text(CC_ONLY, root=repo)

    found = _doctor_report(repo, lcov, _doctor_marks_stamp(repo, lcov), NO_PACKAGES)["warnings"]

    assert len(found) == 1 and "py (coverage cc-only to lcov=1)" in found[0], found
    assert "ratchet seed --scope py`" in found[0] and FLIP_PROCEDURE in found[0]
    assert _doctor_marks_stamp(repo, coveragepy_only) == []
    assert _doctor_marks_stamp(repo, measured) == []


# --- the walkthrough -------------------------------------------------------------------

def _flip(repo: Path, capsys) -> str:
    """Seed cc-only, add the lane, drop coverage_optional; the marks file before the flip."""
    before = start(repo, capsys)
    assert marks(repo) == dict.fromkeys(COVERED, "8.0000")
    configure(repo, MEASURED)
    return before


def test_verify_refuses_the_flip_before_any_lane_runs(repo, capsys):
    _flip(repo, capsys)
    runs = lane_runs(repo)

    code, out, err = cli(repo, capsys, "verify")

    assert code == 3, (out, err)
    assert lane_runs(repo) == runs
    assert "1 scope(s): py (coverage cc-only to coveragepy=1)" in err, err
    assert "py was cc-only and is now measured by its coveragepy lane(s)" in err
    assert "ratchet seed --scope py`" in err and FLIP_PROCEDURE in err


def _measure(repo: Path, capsys) -> dict[str, dict[str, str]]:
    code, out, err = cli(repo, capsys, "coverage", "--export", EXPORT)
    assert code == 0, (out, err)
    return export_rows(repo)


def test_the_measured_scores_match_the_formula_on_coverage_py_s_own_report(repo, capsys):
    """Check 1: by hand, ccn 8 at 0%, 50% and 100% is 72, 16 and 8. Check 2: the
    formula on each function's branch share in the lane's coverage.py report
    gives the CRAP `coverage --export` measured."""
    _flip(repo, capsys)
    exported = _measure(repo, capsys)
    report = json.loads((repo / ".crapkit/cov/py.json").read_text(encoding="utf-8"))
    functions = report["files"]["py/flip.py"]["functions"]

    assert {name: float(row["crap"]) for name, row in exported.items()} == {
        "none_run": 72.0, "half_run": 16.0, "all_run": 8.0}
    for name, row in exported.items():
        summary = functions[name]["summary"]
        cov = summary["covered_branches"] / summary["num_branches"]
        assert int(row["ccn"]) == CCN
        assert float(row["crap"]) == pytest.approx(CCN ** 2 * (1 - cov) ** 3 + CCN), (name, row)


@pytest.mark.parametrize("table", ["shipped", "any-move"])
def test_the_scoped_seed_raises_the_flipped_marks_and_verify_passes(repo, capsys, monkeypatch, table):
    """The shipped table ("rises on the coverage flip only") and "rises on any stamp move" both
    let the flipped scope's marks take the run's values; `go` keeps its bytes."""
    answer(monkeypatch, table)
    before = _flip(repo, capsys)
    _measure(repo, capsys)

    code, out, err = cli(repo, capsys, "ratchet", "seed", "--scope", "py")

    assert code == 0, (out, err)
    assert marks(repo) == {"none_run": "72.0000", "half_run": "16.0000", "all_run": "8.0000"}
    assert out.startswith(f"{MARKS}: scope py: added 0, tightened 0, raised 2 (first py/flip.py"), out
    after = (repo / MARKS).read_text(encoding="utf-8")
    assert lines_of(after, "go", "go/") == lines_of(before, "go", "go/") != []
    assert marks(repo, "go/") == {"Route": f"{ROUTE_CCN}.0000"}
    assert cli(repo, capsys, "verify")[0] == 0


def test_under_marks_never_rise_verify_names_the_functions_not_fully_covered(repo, capsys, monkeypatch):
    """Check 3: the exit-7 set, computed from the export against the marks
    without verify, is the functions not fully covered; after a seed that
    keeps the marks it is what verify names."""
    answer(monkeypatch, "never")
    before = _flip(repo, capsys)
    exported = _measure(repo, capsys)
    marked = marks(repo)
    expected = {name for name, row in exported.items() if float(row["crap"]) > float(marked[name])}

    assert expected == {name for name, hit in COVERED.items() if hit < BRANCHES} == {"none_run", "half_run"}
    assert cli(repo, capsys, "ratchet", "seed", "--scope", "py")[0] == 0
    assert marks(repo) == dict.fromkeys(COVERED, "8.0000")
    after = (repo / MARKS).read_text(encoding="utf-8")
    assert lines_of(after, "go", "go/") == lines_of(before, "go", "go/")

    code, out, err = cli(repo, capsys, "verify")

    assert code == 7, (out, err)
    named = {name for name in COVERED if f"{name}(" in out + err or f"{name} (" in out + err}
    assert named == expected, (out, err)


# --- the variations --------------------------------------------------------------------

def test_no_lane_to_measured_is_the_flip_too(repo, capsys):
    """A scope with no lane and no coverage_optional scores cov 0, so its marks
    are 72; coverage refuses a repo with no lane at all, so the file is the one
    the cc-only start seeded, restamped `no-lane` at those marks. The flip's
    seed lowers the two that are covered."""
    start(repo, capsys)
    text = (repo / MARKS).read_text(encoding="utf-8")
    no_lane = re.sub(r"^(#scope\tpy\t.*coverage )cc-only$", r"\g<1>no-lane", text, flags=re.M)
    no_lane = re.sub(r"^(py/[^\t]*\t[^\t]*\t)8\.0000$", r"\g<1>72.0000", no_lane, flags=re.M)
    assert no_lane.count("coverage no-lane") == 1
    (repo / MARKS).write_text(no_lane, encoding="utf-8", newline="\n")
    assert marks(repo) == dict.fromkeys(COVERED, "72.0000")
    configure(repo, MEASURED)

    code, out, err = cli(repo, capsys, "verify")
    assert code == 3 and "py was no-lane and is now measured by its coveragepy lane(s)" in err, err
    _measure(repo, capsys)
    assert cli(repo, capsys, "ratchet", "seed", "--scope", "py")[0] == 0

    assert marks(repo) == {"none_run": "72.0000", "half_run": "16.0000", "all_run": "8.0000"}
    assert cli(repo, capsys, "verify")[0] == 0


def test_measured_to_cc_only_is_no_flip_and_the_seed_tightens_to_ccn(repo, capsys):
    start(repo, capsys, MEASURED)
    assert marks(repo) == {"none_run": "72.0000", "half_run": "16.0000", "all_run": "8.0000"}
    configure(repo, CC_ONLY)

    code, out, err = cli(repo, capsys, "verify")
    assert code == 3 and "py (coverage coveragepy=1 to cc-only)" in err, err
    assert "upgrading.md" not in err
    _measure(repo, capsys)
    assert cli(repo, capsys, "ratchet", "seed", "--scope", "py")[0] == 0

    assert marks(repo) == dict.fromkeys(COVERED, "8.0000")
    assert cli(repo, capsys, "verify")[0] == 0


@pytest.mark.parametrize("table, code_after", [("shipped", 7), ("any-move", 0)])
def test_a_scope_renamed_in_the_commit_that_adds_its_lane_reads_as_unstamped(repo, capsys, monkeypatch,
                                                                             table, code_after):
    """Under the shipped table the seed keeps the old marks and verify names the functions not
    fully covered; with any stamp move raising, one `seed --scope app` repairs it.
    The procedure renames and adds the lane in two commits for this reason."""
    answer(monkeypatch, table)
    start(repo, capsys)
    configure(repo, RENAMED)

    code, out, err = cli(repo, capsys, "verify")
    assert code == 3 and "app (no stamp recorded for this scope)" in err, err
    assert "upgrading.md" not in err
    _measure(repo, capsys)
    assert cli(repo, capsys, "ratchet", "seed", "--scope", "app")[0] == 0

    code, out, err = cli(repo, capsys, "verify")
    assert code == code_after, (out, err)
    if code_after == 7:
        assert marks(repo) == dict.fromkeys(COVERED, "8.0000")
        assert "none_run" in out + err and "half_run" in out + err and "all_run" not in out + err
    else:
        assert marks(repo) == {"none_run": "72.0000", "half_run": "16.0000", "all_run": "8.0000"}
