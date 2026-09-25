"""The rescore preview equals the next coverage run on a shared span and a one-line def.

README (Commands: rescore) and its remedy table say it once for both: a
function on a line span another one shares, and a Python def whose body starts
on the line its signature ends, scores untested at cov 0, as the coverage run
scores it, so its CRAP is ccn^2 + ccn and its remedy split-lines once that
passes the ceiling (model_verdict.remedy). The preview (`rescore`, `rescore
--gate` and the MCP `check_gate` tool) must say what the next run will say:
a preview that passed a function the next verify fails is the defect.

Each case measures two functions on their own lines, edits them onto one span
(TypeScript arrows, an istanbul lane) or onto their def line (Python, a
coverage.py lane), previews, and runs coverage again.
"""
from __future__ import annotations

from fractions import Fraction
import json

import pytest

from accuracy.kit import drive, exact, repos
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

TARGET = 6

# --- TypeScript: two arrows edited onto one span -------------------------------------------------

# Each arrow holds two conditional expressions: McCabe ccn 3.
ARROW_F = "(a: number) => (a > 1 ? 1 : a > 0 ? 2 : 3)"
ARROW_G = "(b: number) => (b > 2 ? 3 : b > 0 ? 4 : 5)"
APART = f"export const f = {ARROW_F};\nexport const g = {ARROW_G};\n"
SHARED = f"export const f = {ARROW_F}, g = {ARROW_G};\n"


def _position(line: int, column: int) -> dict:
    return {"line": line, "column": column}


def _span(line: int, start: int, end: int) -> dict:
    return {"start": _position(line, start), "end": _position(line, end)}


def _arrow_entries(text: str, arrow: str, line: int, number: int) -> tuple:
    """fnMap, branchMap (two cond-exprs) and statementMap entries for one arrow,
    at the columns it sits on in `text`'s line, as istanbul writes them."""
    start = text.split("\n")[line - 1].index(arrow)
    end = start + len(arrow)
    body = _span(line, start, end)
    fn = {"name": f"(anonymous_{number})", "decl": body, "loc": body, "line": line}
    branch = {"loc": body, "type": "cond-expr", "locations": [body, body], "line": line}
    return fn, [branch, branch], body


def istanbul(path: str, text: str, placed: list[tuple[str, int]]) -> dict:
    """A coverage-final.json for `path` in which every function ran and every
    branch arm was taken."""
    fns, branches, statements = {}, {}, {}
    for number, (arrow, line) in enumerate(placed):
        fn, arms, body = _arrow_entries(text, arrow, line, number)
        fns[str(number)], statements[str(number)] = fn, body
        branches.update({str(len(branches) + offset): arm for offset, arm in enumerate(arms)})
    return {path: {"path": path, "statementMap": statements, "fnMap": fns, "branchMap": branches,
                   "s": dict.fromkeys(statements, 1), "f": dict.fromkeys(fns, 1),
                   "b": {key: [1, 1] for key in branches}}}


TS_CONFIG = (f"[crapkit]\ntarget = {TARGET}\n\n[[scope]]\nname = \"web\"\npaths = [\"web\"]\n"
             "languages = [\"typescript\"]\n\n[exclude]\nglobs = [\"recorded/**\"]\n\n"
             + repos.lane_toml("ts", ".crapkit/cov/ts.json", "istanbul", ["web"],
                               "recorded/ts.json"))


def _ts_files(text: str, placed: list) -> dict:
    return {"web/pair.ts": text,
            "recorded/ts.json": json.dumps(istanbul("web/pair.ts", text, placed))}


def _write(root, files: dict) -> None:
    for path, text in files.items():
        (root / path).write_bytes(text.encode("utf-8"))


def _rows(payload_rows, names) -> dict:
    return {row["function"]: (row["ccn"], row["cov"], row["crap"], row["flag"], row["remedy"])
            for row in payload_rows if row["function"] in names}


def _stored(driver: drive.Driver, path: str, names) -> dict:
    """What the newest trusted run stored for each name, as brief reads it."""
    scored = [driver.run("brief", path, name, "--json").json()["scored"] for name in names]
    return _rows([{**row, "function": row["long_name"]} for row in scored], names)


def _expected(ccn: int, shares: bool, cov: Fraction) -> tuple:
    cov = Fraction(0) if shares else cov
    crap = exact.crap(ccn, cov)
    return (ccn, float(cov), float(crap), "untested" if shares else "measured",
            model.remedy(ccn, crap, TARGET, shares))


TS_NAMES = ("f ( a )", "g ( b )")


@pytest.fixture
def ts_edited(make_repo):
    """Measured apart, fully covered; then edited onto one span, artifact included."""
    spec = repos.Spec(steps=(repos.Commit(files={"crapkit.toml": TS_CONFIG,
                                                 **_ts_files(APART, [(ARROW_F, 1), (ARROW_G, 2)])},
                                          message="seed"),))
    driver = drive.Driver(make_repo(spec).root)
    assert driver.run("coverage").code == 0
    measured = _stored(driver, "web/pair.ts", TS_NAMES)
    _write(driver.root, _ts_files(SHARED, [(ARROW_F, 1), (ARROW_G, 1)]))
    return driver, measured


@pytest.mark.process
def test_shared_span_preview_equals_next_run(ts_edited):
    driver, measured = ts_edited
    assert measured == {name: _expected(3, False, Fraction(1)) for name in TS_NAMES}
    preview = _rows(driver.run("rescore", "web/pair.ts", "--json").json()["functions"], TS_NAMES)
    assert driver.run("coverage").code == 0
    want = {name: _expected(3, True, Fraction(1)) for name in TS_NAMES}
    assert (preview, _stored(driver, "web/pair.ts", TS_NAMES)) == (want, want)
    assert want["f ( a )"][4] == "split-lines"


@pytest.mark.process
def test_check_gate_answers_as_rescore_gate_on_a_shared_span(ts_edited):
    driver, _ = ts_edited
    cli = driver.run("rescore", "web/pair.ts", "--gate", "--json").json()
    tool = driver.mcp([("check_gate", {"path": "web/pair.ts"})])[0]
    assert json.loads(tool["content"][0]["text"])["gate"] == cli["gate"]
    assert _rows(json.loads(tool["content"][0]["text"])["functions"], TS_NAMES) == \
        {name: _expected(3, True, Fraction(1)) for name in TS_NAMES}


# --- Python: a def edited onto its def line ------------------------------------------------------

WORLD = (vw.World().with_fn("app", vw.Fn("a1", 2, 4)).with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
ONE_LINE = vw.Fn("a1", 2, 4, one_line=True)


@pytest.mark.process
def test_one_line_def_preview_equals_next_run(make_repo):
    """a1 (ccn 3) measured over three lines with every arm run: cov 1, ok.
    Rewritten onto its def line, the preview, the next run and a preview
    after that run all read untested, cov 0, CRAP 12, split-lines."""
    sc = vw.Scenario.build(make_repo, WORLD)
    assert sc.run("coverage").code == 0
    names = ("a1( x )",)
    assert _stored(sc.driver, "src/app.py", names) == {"a1( x )": _expected(3, False, Fraction(1))}
    sc.set(sc.world.with_fn("app", ONE_LINE))
    preview = _rows(sc.run("rescore", "src/app.py", "--json").json()["functions"], names)
    assert sc.run("coverage").code == 0
    after = _rows(sc.run("rescore", "src/app.py", "--json").json()["functions"], names)
    want = {"a1( x )": _expected(ONE_LINE.ccn, True, Fraction(1))}
    assert (preview, _stored(sc.driver, "src/app.py", names), after) == (want, want, want)
    assert want["a1( x )"][2:] == (12.0, "untested", "split-lines")
