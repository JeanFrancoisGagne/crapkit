"""Ratchet keys, handles and NAME resolution across twins, callbacks and languages.

docs/ratchet.md (Twins: one name, several functions; Same-line function
identity) and CONTEXT.md (Twin, Handle) define them, restated in
model_verdict.keys and model_verdict.handle:

- a key is (path, long name), the first twin in file order keeping the bare
  long name and later ones taking #2, #3 by (start, occurrence);
- a handle is the bare identifier, NAME#N for every member of a twin group,
  or (anonymous)#N counting a file's anonymous functions from the top;
- a bare twin name selects the worst twin, in brief, explain and the MCP
  history tool alike; a start line names the one function that opens there.

The repository mixes Python twins, a C #ifdef fork, Rust and Go functions
whose long names carry no parenthesis, and TypeScript callbacks two to a
line. Every scope is coverage_optional, so a function's CRAP is its ccn,
counted here by McCabe's rule (one per branch point, plus one). The long
names and positions a check keys are the run's own rows, read with sqlite3;
the rules applied to them, and the numbers, are the docs' and McCabe's.
"""
from __future__ import annotations

import json

import pytest

from accuracy.kit import drive, repos
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

TWINS = ("def dup(x):\n    if x:\n        return 1\n    return 2\n\n\n"
         "def dup(x):\n    if x > 1:\n        return 1\n    if x > 2:\n        return 2\n"
         "    if x > 3:\n        return 3\n    return 4\n\n\ndef dup_other(x):\n    return x\n")
FILES = {
    "py/twins.py": TWINS,
    "web/app.ts": ("export const f = (a: number) => [a].map(x => x > 1 ? 1 : x > 0 ? 2 : 3)"
                   ".filter(y => y > 2 || y < 0);\n\n\n\n"
                   "function g(x: number) {\n  if (x > 1) { return 1; }\n  return 2;\n}\n"),
    "rs/lib.rs": ("fn route(cmd: i32) -> i32 {\n    if cmd > 1 {\n        return 1;\n    }\n    2\n}\n\n"
                  "fn route_all(cmd: i32) -> i32 {\n    cmd\n}\n"),
    "go/main.go": ("package main\n\nfunc Classify(x int) int {\n\tif x > 1 {\n\t\treturn 1\n\t}\n"
                   "\treturn 2\n}\n\nfunc ClassifyAll(x int) int {\n\treturn x\n}\n"),
    "c/shim.c": ("#ifdef WIN\nint f(int x) {\n    if (x > 1) { return 1; }\n    return 2;\n}\n#else\n"
                 "int f(int x) {\n    if (x > 1) { return 1; }\n    if (x > 2) { return 2; }\n"
                 "    return 3;\n}\n#endif\n"),
}
LANGUAGES = {"py": "python", "web": "typescript", "rs": "rust", "go": "go", "c": "cpp"}
# (path, start line) -> McCabe ccn, counted by hand: one per if, ternary, ||
# or &&, plus one. The source column of hand_*.tsv, inline.
MCCABE = {("py/twins.py", 1): 2, ("py/twins.py", 7): 4, ("py/twins.py", 17): 1,
          ("web/app.ts", 5): 2, ("rs/lib.rs", 1): 2, ("rs/lib.rs", 8): 1,
          ("go/main.go", 3): 2, ("go/main.go", 10): 1, ("c/shim.c", 2): 2, ("c/shim.c", 7): 3}
# Line 1 opens three functions, numbered by occurrence: f itself (no branch),
# the map callback (two ternaries) and the filter callback (one ||).
CALLBACKS = {("web/app.ts", 1, 1): 1, ("web/app.ts", 1, 2): 3, ("web/app.ts", 1, 3): 2}


def config(target: int = 1) -> str:
    scopes = "".join(f'[[scope]]\nname = "{name}"\npaths = ["{name}"]\nlanguages = ["{lang}"]\n'
                     "coverage_optional = true\n\n" for name, lang in LANGUAGES.items())
    return f"[crapkit]\ntarget = {target}\n\n{scopes}"


def spec(files: dict) -> repos.Spec:
    return repos.Spec(steps=(repos.Commit(files={"crapkit.toml": config(), **files}, message="seed"),))


def rows(driver: drive.Driver, run: int | None = None) -> list[model.Row]:
    """The run's rows (newest run by default), read with sqlite3."""
    found = driver.store(
        "SELECT i.path, i.long_name, f.start, f.end, f.ccn, f.crap, f.occurrence, f.run_id "
        "FROM functions f JOIN identities i ON i.id = f.identity_id ORDER BY f.run_id, i.path, f.start")
    wanted = run or max(row["run_id"] for row in found)
    return [model.Row(r["path"], r["long_name"], r["start"], model.Fraction(r["crap"]), r["ccn"],
                      r["occurrence"], r["end"]) for r in found if r["run_id"] == wanted]


@pytest.fixture(scope="module")
def measured(repo_templates, tmp_path_factory):
    built = repo_templates.copy(spec(FILES), tmp_path_factory.mktemp("names") / "repo")
    driver = drive.Driver(built.root)
    assert driver.run("coverage").code == 0
    assert driver.run("ratchet", "seed").code == 0
    return built, driver


def _marks(built) -> dict:
    return model.parse_marks((built.root / "crapkit-ratchet.tsv").read_bytes().decode("utf-8")).marks


def _mccabe(row: model.Row) -> int:
    return MCCABE.get((row.path, row.start)) or CALLBACKS[(row.path, row.start, row.occurrence)]


# --- ccn by hand, keys by the model -------------------------------------------------------------

@pytest.mark.process
def test_every_function_s_ccn_is_its_mccabe_count(measured):
    _, driver = measured
    got = {(r.path, r.start, r.occurrence): (r.ccn, r.crap) for r in rows(driver)}
    want = {(r.path, r.start, r.occurrence): (_mccabe(r), model.Fraction(_mccabe(r)))
            for r in rows(driver)}
    assert got == want and len(got) == len(MCCABE) + len(CALLBACKS)


@pytest.mark.process
def test_seed_marks_every_twin_under_its_own_key(measured):
    """Every function over the ceiling (1) takes a mark at its CRAP, under the
    key model_verdict.keys gives its long name, start and occurrence."""
    built, driver = measured
    run = rows(driver)
    want = {key: model.mark_value(row.crap) for row, key in model.keys(run).items() if row.crap > 1}
    assert _marks(built) == want


@pytest.mark.process
def test_ifdef_twins_get_distinct_keys(measured):
    """docs/ratchet.md: C gives one name to both arms of an #ifdef fork; the
    first arm keeps the bare key and the second takes #2."""
    built, _ = measured
    marks = {key: value for key, value in _marks(built).items() if key[0] == "c/shim.c"}
    assert marks == {("c/shim.c", "f( int x)"): model.Decimal("2.0000"),
                     ("c/shim.c", "f( int x)#2"): model.Decimal("3.0000")}


@pytest.mark.process
@pytest.mark.parametrize("variant", ["worse-first", "unrelated-above"])
def test_twin_mark_independent_of_row_order(repo_templates, tmp_path, variant):
    """The ordinal is file order. Putting the worse twin first moves the bare
    key to it; a function added above both leaves every ordinal as it was."""
    first, second = TWINS.split("\n\n\ndef dup(x):\n", 1)
    second, rest = ("def dup(x):\n" + second).split("\n\n\ndef dup_other", 1)
    text = {"worse-first": f"{second}\n\n\n{first}\n\n\ndef dup_other{rest}",
            "unrelated-above": f"def above(x):\n    return x\n\n\n{TWINS}"}[variant]
    built = repo_templates.copy(spec({"py/twins.py": text}), tmp_path / "repo")
    driver = drive.Driver(built.root)
    assert driver.run("coverage").code == 0 and driver.run("ratchet", "seed").code == 0

    worst = {"worse-first": ("4.0000", "2.0000"), "unrelated-above": ("2.0000", "4.0000")}[variant]
    assert _marks(built) == {("py/twins.py", "dup( x )"): model.Decimal(worst[0]),
                             ("py/twins.py", "dup( x )#2"): model.Decimal(worst[1])}


# --- handles -------------------------------------------------------------------------------------

@pytest.mark.process
def test_next_item_handles_follow_the_docs(measured):
    """CONTEXT.md, Handle: NAME#N for a twin, (anonymous)#N for a function
    with no name, the bare identifier otherwise; Rust and Go included."""
    _, driver = measured
    run = rows(driver)
    items = driver.run("next-item", "--top", "50").json()["items"]
    got = {(item["path"], item["start"], item["handle"]) for item in items}
    want = {(row.path, row.start, model.handle(row, run)) for row in run if row.crap > 1}
    assert got == want


@pytest.mark.process
@pytest.mark.parametrize("handle, occurrence", [("(anonymous)#1", 2), ("(anonymous)#2", 3)])
def test_every_reader_addresses_both_same_line_callbacks(measured, handle, occurrence):
    """Two anonymous callbacks open on line 1 after f: brief and the MCP brief
    tool each reach either one by its handle, numbered from the top."""
    _, driver = measured
    packet = driver.run("brief", "web/app.ts", handle, "--json").json()
    mcp = driver.mcp([("get_function_brief", {"path": "web/app.ts", "name": handle})])[0]
    scored = packet["scored"]
    assert (scored["start"], scored["occurrence"], scored["ccn"]) == \
        (1, occurrence, CALLBACKS[("web/app.ts", 1, occurrence)])
    assert mcp["structuredContent"]["scored"] == scored


@pytest.mark.process
@pytest.mark.parametrize("path, name, long_name", [("rs/lib.rs", "route", "route cmd : i32"),
                                                   ("go/main.go", "Classify", "Classify x int")])
def test_bare_names_resolve_for_rust_and_go(measured, path, name, long_name):
    """agent-json.md: the bare name is the leading token of the long name,
    which Rust and Go print with no parenthesis."""
    _, driver = measured
    assert driver.run("brief", path, name, "--json").json()["function"] == long_name


@pytest.mark.process
@pytest.mark.parametrize("path, name", [("rs/lib.rs", "route"), ("go/main.go", "Classify"),
                                        ("py/twins.py", "dup")])
def test_explain_answers_only_the_named_function(measured, path, name):
    """README explain: an exact bare identifier or long name wins over the
    prefix match, so route explains route and not route_all."""
    _, driver = measured
    functions = driver.run("explain", path, name, "--json").json()["functions"]
    assert len(functions) == 1
    assert model.bare_name(functions[0]["long_name"]) == name


@pytest.mark.process
def test_brief_explain_and_history_pick_the_same_twin(measured):
    """docs/ratchet.md: a bare twin name resolves to the worst twin, and brief,
    explain and get_function_history all pick it (dup at line 7, CRAP 4)."""
    _, driver = measured
    worst = model.worst_twin(rows(driver), "py/twins.py", "dup")
    brief = driver.run("brief", "py/twins.py", "dup", "--json").json()
    explain = driver.run("explain", "py/twins.py", "dup", "--json").json()["functions"][0]
    history = driver.mcp([("get_function_history", {"path": "py/twins.py", "name": "dup"})])[0]
    assert (brief["scored"]["start"], brief["ratchet_mark"]) == (worst.start, 4.0)
    assert (explain["history"][-1]["crap"], explain["ratchet_mark"]) == (4.0, 4.0)
    assert history["structuredContent"]["functions"][0] == explain


@pytest.mark.process
@pytest.mark.parametrize("line, crap", [("1", 2.0), ("7", 4.0)])
def test_explain_by_start_line_selects_that_function(measured, line, crap):
    _, driver = measured
    functions = driver.run("explain", "py/twins.py", line, "--json").json()["functions"]
    assert [f["history"][-1]["crap"] for f in functions] == [crap]


@pytest.mark.process
def test_a_line_two_functions_open_on_is_refused_with_their_handles(measured):
    """No start line names two functions; brief and explain refuse it, exit 1,
    and list the handles that do name them."""
    _, driver = measured
    for command in ("brief", "explain"):
        result = driver.run(command, "web/app.ts", "1")
        assert result.code == 1, (command, result.stdout, result.stderr)
        assert "(anonymous)#1" in result.stderr and "(anonymous)#2" in result.stderr


# --- runs written before same-line order was recorded ----------------------------------------------

def _legacy(tmp_path, measured) -> drive.Driver:
    """A copy whose run lost the same-line order: every occurrence reads 0, as a
    run stored before crapkit recorded it does (CONTEXT.md, Legacy run)."""
    import shutil
    import sqlite3
    built, _ = measured
    top = tmp_path / "legacy"
    shutil.copytree(built.top, top)
    connection = sqlite3.connect(top / ".crapkit" / "crap.sqlite")
    with connection:
        connection.execute("UPDATE functions SET occurrence = 0")
    connection.close()
    return drive.Driver(top)


@pytest.mark.process
def test_worklist_refuses_unordered_same_line_twins(tmp_path, measured):
    """CONTEXT.md, Legacy run: a command that must read the same-line twins
    from it refuses and names the run."""
    result = _legacy(tmp_path, measured).run("worklist", "--json")
    assert result.code == 5, result.stdout + result.stderr
    assert "web/app.ts" in result.stderr


@pytest.mark.process
def test_collision_refuses_only_the_colliding_name(tmp_path, measured):
    """In that legacy run, g opens alone on line 5 of the same file: explain
    answers it, and refuses only the line the two callbacks share."""
    driver = _legacy(tmp_path, measured)
    assert driver.run("explain", "web/app.ts", "g", "--json").code == 0
    assert driver.run("explain", "web/app.ts", "1").code != 0


def test_the_model_s_keys_and_handles_on_a_written_example():
    """The model against docs/ratchet.md's own twin example and CONTEXT.md's
    handle forms, with no crapkit involved."""
    first = model.Row("calc/iso_cost.py", "__post_init__( self )", 7, model.Fraction(66))
    second = model.Row("calc/iso_cost.py", "__post_init__( self )", 18, model.Fraction(30))
    alone = model.Row("calc/iso_cost.py", "route( a )", 30, model.Fraction(9))
    anon = model.Row("web/a.ts", "(anonymous)", 1, model.Fraction(2), occurrence=2)
    run = [second, alone, first, anon]
    assert model.keys(run) == {first: ("calc/iso_cost.py", "__post_init__( self )"),
                               second: ("calc/iso_cost.py", "__post_init__( self )#2"),
                               alone: ("calc/iso_cost.py", "route( a )"),
                               anon: ("web/a.ts", "(anonymous)")}
    assert [model.handle(row, run) for row in (first, second, alone, anon)] == \
        ["__post_init__#1", "__post_init__#2", "route", "(anonymous)#1"]
    assert model.worst_twin(run, "calc/iso_cost.py", "__post_init__") == first
    assert json.dumps(model.bare_name("route cmd : & Cmd")) == '"route"'


# --- the legacy mark proof: which files a reader proves ---------------------------------------------
#
# docs/ratchet.md, Same-line function identity: a marks file with no
# `# crapkit-keys=1` line keys twins by the old start-only rule. When such a
# legacy marked name has two functions starting on the same line, its entire
# name group needs review, and available historical runs take part in that
# check. A reader of another file's mark has no such group to read. web/b.ts
# holds two callbacks on line 1 (McCabe 2 each: a ternary, an ||); web/a.ts
# holds f (McCabe 2).

def _a_mark(built) -> tuple:
    (key,) = [key for key in _marks(built) if key[0] == "web/a.ts"]
    return key


@pytest.mark.nightly
@pytest.mark.process
def test_legacy_marks_hold_the_seeded_values(make_repo):
    """Seed marked f and both callbacks at their McCabe counts, the callbacks
    under (anonymous) and (anonymous)#2 (docs/ratchet.md, Twins)."""
    built, _ = vw.legacy_group(make_repo)
    assert _marks(built) == {_a_mark(built): model.Decimal("2.0000"),
                             ("web/b.ts", "(anonymous)"): model.Decimal("2.0000"),
                             ("web/b.ts", "(anonymous)#2"): model.Decimal("2.0000")}


@pytest.mark.nightly
@pytest.mark.process
def test_brief_proves_only_its_own_file(make_repo):
    """brief of f reads one mark, f's, and answers it; brief of the second
    callback reads the group run 1 cannot order, and refuses naming its file."""
    built, driver = vw.legacy_group(make_repo)
    answered = driver.run("brief", "web/a.ts", "f", "--json")
    assert answered.code == 0, answered.stdout + answered.stderr
    assert answered.json()["ratchet_mark"] == 2.0
    refused = driver.run("brief", "web/b.ts", "(anonymous)#2", "--json")
    assert refused.code != 0 and "web/b.ts" in refused.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_explain_on_a_dropped_file_proves_the_run_that_holds_it(make_repo):
    """Run 2 dropped web/a.ts. explain resolves f in run 1, the newest trusted
    run that holds the file (agent-json.md), and proves f's file there only."""
    built, driver = vw.legacy_group(make_repo, "rm", "-q", "--", "web/a.ts")
    result = driver.run("explain", "web/a.ts", "f", "--json")
    assert result.code == 0, result.stdout + result.stderr
    (function,) = result.json()["functions"]
    assert ([row["run_id"] for row in function["history"]], function["ratchet_mark"]) == ([1], 2.0)


@pytest.mark.nightly
@pytest.mark.process
def test_a_deleted_file_s_legacy_group_refuses_only_seed_and_prune(make_repo):
    """The tree deleted web/b.ts. brief, explain and worklist read web/a.ts
    and answer. Seed and prune decide what becomes of b.ts's marks, so they
    read its group and refuse, leaving the marks file as it was."""
    built, driver = vw.legacy_group(make_repo, "rm", "-q", "--", "web/b.ts")
    for args in (("brief", "web/a.ts", "f", "--json"), ("explain", "web/a.ts", "f", "--json"),
                 ("worklist", "--json")):
        result = driver.run(*args)
        assert result.code == 0, (args, result.stdout, result.stderr)
    before = (built.root / "crapkit-ratchet.tsv").read_bytes()
    for action in ("seed", "prune"):
        result = driver.run("ratchet", action)
        assert result.code != 0 and "web/b.ts" in result.stderr, (action, result.stderr)
    assert (built.root / "crapkit-ratchet.tsv").read_bytes() == before
