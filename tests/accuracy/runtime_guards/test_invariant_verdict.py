"""Runtime invariant verdict: crapkit stops on a number past its documented bound, and only then.

The model here is written from the docs, apart from crapkit.invariants:

- CRAP = ccn^2 x (1 - cov)^3 + ccn (README, first section), so CRAP lies in
  [ccn, ccn^2 + ccn], is ccn at cov 1 and ccn^2 + ccn at cov 0;
- the flag table: `untested` and `no-lane` score cov = 0, `cc-only` scores
  crap = ccn (README "Flags: why a coverage number is missing");
- the remedy table: decompose iff ccn > ceiling, ok iff crap <= ceiling, else
  add-tests or split-lines (README "Remedy: what to do about it");
- `ccn` is min(ccn_std, ccn_mod), spans are 1-based inclusive, nloc counts
  lines of the span (docs/agent-json.md, next-item `item` fields).

A row the model accepts must pass crapkit's check; a row it refuses must stop
the check with kind `internal`. Valid rows carry the exact CRAP from kit.exact,
rounded once to a float. crapkit.invariants is loaded at run time, as kit.drive
loads the CLI: it is the code under test, never the source of an expected value.
"""
from __future__ import annotations

from fractions import Fraction
import importlib
import json
import math

from hypothesis import event, given, strategies as st
import pytest

from accuracy.kit import drive, exact, repos, rulings, strategies
from accuracy.kit.settings import pure

FLAGS = ("measured", "untested", "no-lane", "cc-only")
REMEDIES = ("decompose", "split-lines", "add-tests", "ok")
PY = "def f(x):\n    if x:\n        return 1\n    return 2\n"


def guards():
    return importlib.import_module("crapkit.invariants")


def internal_error():
    return importlib.import_module("crapkit.errors").InternalCheckError


# --- the model ----------------------------------------------------------------------------


def _remedies(ccn: int, crap: float, ceiling: int) -> set[str]:
    if ccn > ceiling:
        return {"decompose"}
    if crap <= ceiling:
        return {"ok"}
    return {"add-tests", "split-lines"}


def _pinned_crap(row: dict) -> float | None:
    """The CRAP the docs fix exactly for this row, or None where they fix a range."""
    ccn = row["ccn"]
    if row["flag"] == "cc-only":
        return float(ccn)
    return {1.0: float(ccn), 0.0: float(ccn * ccn + ccn)}.get(row["cov"])


def _ccn_ok(r: dict) -> bool:
    return r["ccn_std"] >= 1 and r["ccn_mod"] >= 1 and r["ccn"] == min(r["ccn_std"], r["ccn_mod"])


def _span_ok(r: dict) -> bool:
    return 1 <= r["start"] <= r["end"] and 0 <= r["nloc"] <= r["end"] - r["start"] + 1


def _score_ok(r: dict) -> bool:
    ccn, crap = r["ccn"], r["crap"]
    return 0.0 <= r["cov"] <= 1.0 and ccn <= crap <= ccn * ccn + ccn and _pinned_crap(r) in (None, crap)


def _flag_ok(r: dict) -> bool:
    return r["flag"] in FLAGS and (r["flag"] not in ("untested", "no-lane") or r["cov"] == 0.0)


def model_accepts(row: dict, ceiling: int) -> bool:
    """Every documented bound at once, over the row's plain fields."""
    return all((_ccn_ok(row), _span_ok(row), _score_ok(row), _flag_ok(row),
                row["remedy"] in _remedies(row["ccn"], row["crap"], ceiling)))


FIELDS = ("scope", "path", "long_name", "start", "end", "ccn_std", "ccn_mod", "ccn", "nloc",
          "params", "nesting", "cov", "flag", "crap", "remedy", "cognitive", "occurrence",
          "inline_body")


def valid_row(ccn: int, covered: int, total: int, flag: str, ceiling: int, twin: bool) -> dict:
    """A row the docs allow: CRAP from kit.exact, the remedy from the README table."""
    cov = Fraction(0) if flag in ("untested", "no-lane", "cc-only") else exact.ratio(covered, total)
    crap = float(ccn) if flag == "cc-only" else float(exact.crap(ccn, cov))
    over = sorted(_remedies(ccn, crap, ceiling))
    remedy = over[-1] if twin else over[0]
    return {"scope": "src", "path": "src/a.py", "long_name": "f( x )", "start": 3, "end": 12,
            "ccn_std": ccn + 1, "ccn_mod": ccn, "ccn": ccn, "nloc": 8, "params": 1, "nesting": 2,
            "cov": float(cov), "flag": flag, "crap": crap, "remedy": remedy, "cognitive": 3,
            "occurrence": 1, "inline_body": 0}


# Each break moves one field far past its bound, so no rounding tolerance decides it.
BREAKS = {
    "crap-below-ccn": lambda r: {"crap": r["ccn"] - 0.5},
    "crap-above-ccn2+ccn": lambda r: {"crap": (r["ccn"] * r["ccn"] + r["ccn"]) * 1.001 + 1},
    "crap-nan": lambda r: {"crap": math.nan},
    "crap-inf": lambda r: {"crap": math.inf},
    "cov-nan": lambda r: {"cov": math.nan},
    "cov-over-1": lambda r: {"cov": 1.5},
    "cov-negative": lambda r: {"cov": -0.25},
    "flag-unknown": lambda r: {"flag": "partial"},
    "remedy-unknown": lambda r: {"remedy": "rewrite"},
    "remedy-flipped": lambda r: {"remedy": "ok" if r["remedy"] != "ok" else "decompose"},
    "ccn-not-min": lambda r: {"ccn_mod": r["ccn"] + 1, "ccn_std": r["ccn"] + 2},
    "start-zero": lambda r: {"start": 0},
    "untested-covered": lambda r: {"flag": "untested", "cov": 0.5},
}


def as_scored(row: dict):
    return drive.to_crapkit("scored_row", tuple(row[name] for name in FIELDS))


def crapkit_accepts(row: dict, ceiling: int) -> bool:
    try:
        guards().check_rows([as_scored(row)], lambda scope: ceiling)
    except internal_error() as stop:
        assert str(stop).startswith("stopped: an internal check failed.\n  check: ")
        return False
    return True


@st.composite
def cases(draw):
    ccn = draw(strategies.ccn())
    covered, total = draw(strategies.counts())
    flag = draw(st.sampled_from(FLAGS))
    ceiling = draw(st.integers(1, 60))
    row = valid_row(ccn, covered, total, flag, ceiling, draw(st.booleans()))
    broken = draw(st.sampled_from([None, *BREAKS]))
    event(f"break:{broken}")
    return {**row, **(BREAKS[broken](row) if broken else {})}, ceiling


@given(cases())
@pure
def test_crapkit_stops_exactly_where_the_documented_bounds_do(case):
    row, ceiling = case
    assert crapkit_accepts(row, ceiling) == model_accepts(row, ceiling), (row, ceiling)


@given(strategies.crap_case(), st.integers(1, 60))
@pure
def test_no_row_the_formula_produces_is_a_false_alarm(case, ceiling):
    """No false alarm: every (ccn, covered, total) the strategy draws, scored by
    the exact formula, passes, under every flag the formula applies to."""
    for twin in (False, True):
        row = valid_row(case.ccn, case.covered, case.total, "measured", ceiling, twin)
        assert crapkit_accepts(row, ceiling), row


# README examples, by hand: (ccn, cov, crap, flag, remedy, ceiling, accepted, source)
HAND = [
    (4, 0.5, 6.0, "measured", "ok", 6, True, "16 x 0.125 + 4 = 6.0, crap <= ceiling"),
    (4, 0.5, 6.0, "measured", "add-tests", 6, False, "6.0 <= 6 is ok, not add-tests"),
    (7, 1.0, 7.0, "measured", "decompose", 6, True, "README remedy: ccn > ceiling"),
    (7, 1.0, 7.0, "measured", "ok", 6, False, "no coverage clears ccn > ceiling"),
    (14, 0.5, 38.5, "measured", "decompose", 6, True, "README next-item example: 196/8 + 14"),
    (5, 0.0, 30.0, "untested", "add-tests", 6, True, "25 + 5; untested reads cov 0"),
    (5, 0.0, 30.0, "no-lane", "split-lines", 6, True, "split-lines shares add-tests' condition"),
    (7, 0.0, 7.0, "cc-only", "decompose", 6, True, "README flags: cc-only crap = ccn"),
    (7, 0.0, 56.0, "cc-only", "decompose", 6, False, "cc-only never scores ccn^2 + ccn"),
    (3, 0.0, 12.0, "measured", "add-tests", 12, False, "12 <= 12 is ok"),
]


@pytest.mark.parametrize("ccn, cov, crap, flag, remedy, ceiling, accepted, source", HAND)
def test_hand_rows_from_the_readme(ccn, cov, crap, flag, remedy, ceiling, accepted, source):
    row = {**valid_row(ccn, 1, 1, "measured", ceiling, False),
           "cov": cov, "crap": crap, "flag": flag, "remedy": remedy}
    assert model_accepts(row, ceiling) is accepted, source
    assert crapkit_accepts(row, ceiling) is accepted, source


# --- R27, the second check: a non-finite coverage count never becomes a stored CRAP -------

# A coverage.py JSON report whose one function claims NaN covered branches, the
# shape the R27 fix (c24e6a4) refused in the reader. Before it, the NaN reached
# the score and the store.
NAN_REPORT = ('{"meta": {"branch_coverage": true}, "files": {"src/app.py": {"functions": {"f": '
              '{"start_line": 1, "executed_lines": [1, 2, 3], "missing_lines": [4], '
              '"summary": {"covered_lines": 3, "num_statements": 4, "num_branches": 2, '
              '"covered_branches": NaN}}}}}}')
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
          'languages = ["python"]\n\n[exclude]\nglobs = ["recorded/**"]\n\n'
          + repos.lane_toml("py", ".crapkit/cov/py.json", "coveragepy", ["src"], "recorded/py.json"))


def _stored_crap(driver) -> list[float]:
    if not (driver.root / drive.STORE).is_file():
        return []
    return [row["crap"] for row in driver.store("SELECT crap FROM functions WHERE crap IS NOT NULL")]


@pytest.mark.process
def test_r27_a_nonfinite_coverage_count_never_becomes_a_stored_crap(make_repo):
    built = make_repo(repos.Spec(steps=(repos.Commit(files={
        "crapkit.toml": CONFIG, "src/app.py": PY, "recorded/py.json": NAN_REPORT}),)))
    driver = drive.Driver(built.root)
    done = driver.run("coverage")
    stored = _stored_crap(driver)
    assert all(math.isfinite(crap) for crap in stored), (done.code, stored, done.stderr)
    assert done.code != 0, "a report crapkit cannot score is a refusal, not a clean run"


# --- rulings: where crapkit and the plan's bounds part, and why ----------------------------

# RG1. The arrow opens on line 2 and its body `x;` sits on line 3, so the span
# is 2-3; the two lines hold code, so nloc is 2. Stock lizard 1.24.0 reads g@2-3.
ARROW = "function f() { return 1; }\nconst g = (x) =>\n  x;\n"
ARROW_BY_HAND = "lines 2-3 nloc 2"
TS_CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
             'languages = ["typescript"]\ncoverage_optional = true\n')


def _exported(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]


@rulings.applies("RG1")
@pytest.mark.process
def test_rg1_a_typescript_arrow_s_span_reaches_its_body(make_repo, tmp_path):
    built = make_repo(repos.Spec(steps=(repos.Commit(files={
        "crapkit.toml": TS_CONFIG, "src/app.ts": ARROW}),)))
    done = drive.Driver(built.root).run("inventory", "--export", str(tmp_path / "inv.tsv"))
    assert done.code == 0, done.stderr
    (arrow,) = [row for row in _exported(tmp_path / "inv.tsv") if row["long_name"].startswith("g")]
    measured = f"lines {arrow['start']}-{arrow['end']} nloc {arrow['nloc']}"
    rulings.pin_ruling("RG1", crapkit=measured, oracle=ARROW_BY_HAND)


def _stamp_line(text: str) -> str:
    first = text.splitlines()[0]
    return "stamp line" if first.startswith("# crapkit-analysis=") else "no stamp line"


@rulings.applies("RG2")
@pytest.mark.process
def test_rg2_prune_keeps_a_file_with_no_stamp_unstamped(seed, tmp_path):
    """docs/ratchet.md, the metric stamp: prune, move and the merge driver keep
    the recorded stamp, and a file written before stamping records none."""
    root = seed.private_copy(tmp_path / "repo")
    marks = root / "crapkit-ratchet.tsv"
    marks.write_text("path\tlong_name\tcrap\ngone.py\tlost( )\t9.0000\n", encoding="utf-8",
                     newline="\n")
    done = drive.Driver(root, date_now=seed.date_now).run("ratchet", "prune")
    assert done.code == 0 and "pruned 1" in done.stdout, done.stdout + done.stderr
    rulings.pin_ruling("RG2", crapkit=_stamp_line(marks.read_text(encoding="utf-8")),
                       oracle="stamp line")


def _hook_answer(driver, payload: str) -> str:
    done = driver.run("claude-hook", "--protocol", "1", stdin=payload)
    return f"exit {done.code}" + (" silent" if not (done.stdout or done.stderr) else "")


@rulings.applies("RG3")
@pytest.mark.process
def test_rg3_claude_hook_turns_a_stop_into_its_silent_exit(seed, tmp_path, monkeypatch):
    """README, claude-hook: any internal failure exits 0 in silence."""
    root = seed.private_copy(tmp_path / "repo")
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(8))
    source = root / "src" / "py" / "calc.py"
    source.write_text(source.read_text(encoding="utf-8") + f"\n\ndef wild(n):\n{body}    return n\n",
                      encoding="utf-8", newline="\n")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "cwd": str(root), "tool_input": {"file_path": str(source)}})
    driver = drive.Driver(root, date_now=seed.date_now)
    assert _hook_answer(driver, payload) == "exit 2", "the unpatched hook advises on wild"
    hook = importlib.import_module("crapkit.cli.claude_hook")
    monkeypatch.setattr(hook, "_breaches", lambda records, ranges, ceiling: records)
    rulings.pin_ruling("RG3", crapkit=_hook_answer(driver, payload), oracle="exit 5")


# RG4. The row below counts 11 lines of code in the 10 lines 3-12. The docs'
# span and nloc allow no such row, and the guard lets it through until RG1's
# reader fix lands: that bound would stop every multi-line TypeScript arrow.
PAST_SPAN = {**valid_row(4, 1, 2, "measured", 6, False), "nloc": 11}


@rulings.applies("RG4")
def test_rg4_the_guard_holds_nloc_to_the_span():
    answer = "accepted" if crapkit_accepts(PAST_SPAN, 6) else "stopped"
    rulings.pin_ruling("RG4", crapkit=answer, oracle="accepted" if model_accepts(PAST_SPAN, 6)
                       else "stopped")
