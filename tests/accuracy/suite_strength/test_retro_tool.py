"""tools/accuracy/retro.py: the replay verdict, the digest and the row choice.

The replay rule (the plan's retro section): before counts as red only when the
check fails on an AssertionError, a pin_ruling mismatch included; any other
failure is `not replayable`; the fix must pass. The planted repo below has a
`crapkit` whose `double` answers 3n at its first commit and 2n at its second,
so the expected verdicts are known before anything runs.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import repos
from accuracy.kit.drive import DriveUnsupported
from accuracy.kit.rulings import RulingDefect
from accuracy.kit.settings import pure

REPO = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_retro_tool",
                                                  REPO / "tools" / "accuracy" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


retro = _load()

# --- the verdict against a model -------------------------------------------------------------

records = st.lists(st.fixed_dictionaries({
    "nodeid": st.sampled_from(["t::a", "t::b", "t::c"]),
    "outcome": st.sampled_from(["passed", "failed"]),
    "exc_type": st.sampled_from(["", "AssertionError", "RulingDefect", "KeyError"]),
    "assertion": st.booleans(),
    "message": st.just("m"),
}), max_size=4)


def _failures(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["outcome"] == "failed"]


def _model_before(rows: list[dict]) -> str:
    """Red on an assertion failure, else not replayable on any failure or on no
    item at all, else green."""
    failed = _failures(rows)
    if any(row["assertion"] for row in failed):
        return "red"
    return "not replayable" if failed or not rows else "green"


@given(records)
@pure
def test_before_is_red_only_on_an_assertion_failure(rows):
    assert retro.classify_before(rows).verdict == _model_before(rows)


@given(records)
@pure
def test_the_fix_passes_only_when_every_item_passed(rows):
    expected = "pass" if rows and all(row["outcome"] == "passed" for row in rows) else "fail"

    assert retro.classify_fix(rows).verdict == expected


def _call(error: BaseException | None, when: str = "call"):
    info = None if error is None else SimpleNamespace(type=type(error), value=error)
    return SimpleNamespace(when=when, excinfo=info)


@pytest.mark.parametrize("error, outcome, assertion", [
    (None, "passed", False),
    (AssertionError("assert 3 == 2"), "failed", True),
    (RulingDefect("R: crapkit still says 9"), "failed", True),
    (DriveUnsupported("crapkit nosuch: usage"), "failed", False),
    (KeyError("tests_total"), "failed", False),
    (ImportError("no module"), "failed", False),
])
def test_an_item_s_record_says_whether_it_failed_on_an_assertion(error, outcome, assertion):
    record = retro._record("t::a", _call(error))

    assert (record["outcome"], record["assertion"]) == (outcome, assertion)


def test_a_skip_or_xfail_is_not_a_failure():
    skipped = type("Skipped", (Exception,), {})

    assert retro._record("t::a", _call(skipped("x")))["outcome"] == "skipped"


def test_a_teardown_error_after_a_passing_call_fails_the_item():
    lines = ['{"nodeid": "t::a", "outcome": "passed", "exc_type": "", "assertion": false, '
             '"message": ""}',
             '{"nodeid": "t::a", "outcome": "failed", "exc_type": "OSError", "assertion": false, '
             '"message": "lock"}']

    assert [row["exc_type"] for row in retro.item_outcomes(lines)] == ["OSError"]


@pytest.mark.parametrize("recorded, before, fix, says", [
    ({"id": "R1"}, "green", "pass", "catches nothing"),
    ({"id": "R1", "before": "red"}, "not replayable", "pass", "the replay says not replayable"),
    ({"id": "R1"}, "red", "fail", "fails on its fix commit"),
    ({"id": "R1", "before": "red"}, "red", "pass", ""),
])
def test_a_replay_that_contradicts_its_ledger_row_says_how(recorded, before, fix, says):
    got = retro.contradiction(recorded, retro.Outcome(before), retro.Outcome(fix, "", "boom"))

    assert (says in got) if says else got == ""


# --- a planted bug, end to end --------------------------------------------------------------------

MAIN = """\
import argparse
import json

parser = argparse.ArgumentParser(prog="crapkit")
sub = parser.add_subparsers(dest="command", required=True)
double = sub.add_parser("double")
double.add_argument("n", type=int)
double.add_argument("--json", action="store_true")
args = parser.parse_args()
print(json.dumps({{"value": args.n * {factor}}}))
"""
CHECK = """\
from accuracy.kit import drive


def test_double_doubles(tmp_path):
    assert drive.Driver(tmp_path, spawn=True).json("double", "4") == {"value": 8}


def test_an_unknown_command(tmp_path):
    drive.Driver(tmp_path, spawn=True).run("halve", "4")
"""


@pytest.fixture(scope="module")
def planted(repo_templates, tmp_path_factory):
    """A repo whose first commit plants the bug (3n) and whose second fixes it (2n),
    and a check file outside the accuracy tree that drives it."""
    base = tmp_path_factory.mktemp("planted")
    spec = repos.Spec(steps=(
        repos.Commit(files={"src/crapkit/__init__.py": "", "src/crapkit/__main__.py":
                            MAIN.format(factor=3)}, message="plant"),
        repos.Commit(files={"src/crapkit/__main__.py": MAIN.format(factor=2)}, message="fix")))
    top = repo_templates.copy(spec, base / "repo").top
    shas = repos.git(top, "rev-list", "--reverse", "HEAD").split()
    (base / "checks").mkdir()
    (base / "checks" / "test_planted_double.py").write_text(CHECK, encoding="utf-8")
    site = retro.Site(repo=top, work=base / "work", install="link", packages=())
    return SimpleNamespace(site=site, before=shas[0], fix=shas[1],
                           check=(base / "checks" / "test_planted_double.py").as_posix())


def _bug(planted, test_name: str):
    return retro.Bug("R0", f"{planted.check}::{test_name}", planted.before, planted.fix)


@pytest.mark.process
def test_a_planted_bug_reads_red_before_and_passes_after(planted):
    before, fix = retro.replay(_bug(planted, "test_double_doubles"), retro.CURRENT, planted.site)

    assert (before.verdict, before.failure_class, fix.verdict) == ("red", "AssertionError", "pass")
    assert "12" in before.evidence


@pytest.mark.process
def test_a_usage_error_reads_not_replayable(planted):
    before, _ = retro.replay(_bug(planted, "test_an_unknown_command"), retro.CURRENT, planted.site)

    assert (before.verdict, before.failure_class) == ("not replayable", "DriveUnsupported")


def test_the_replayed_check_never_sees_this_tree_s_crapkit_on_pythonpath(tmp_path, monkeypatch):
    """A spawned old crapkit inherits the check's environment; a PYTHONPATH entry
    holding a crapkit package would shadow the commit's own and turn every before green."""
    (tmp_path / "src" / "crapkit").mkdir(parents=True)
    (tmp_path / "src" / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    monkeypatch.setenv("PYTHONPATH", retro.os.pathsep.join([str(tmp_path / "src"),
                                                             str(tmp_path / "lib")]))

    paths = retro._pytest_env(Path("py"), tmp_path / "o")["PYTHONPATH"].split(retro.os.pathsep)

    assert str(tmp_path / "src") not in paths
    assert paths[-1] == str(tmp_path / "lib")


# --- the digest -----------------------------------------------------------------------------------

TREE = {
    "tests/accuracy/__init__.py": "",
    "tests/accuracy/kit/__init__.py": "",
    "tests/accuracy/kit/helper.py": "VALUE = 1\n",
    "tests/accuracy/kit/other.py": "OTHER = 1\n",
    "tests/accuracy/pkt/__init__.py": "",
    "tests/accuracy/pkt/test_x.py": "from accuracy.kit import helper\n\n\ndef test_x():\n"
                                    "    assert helper.VALUE == 1\n",
    "tests/accuracy/pkt/fixtures/data.txt": "one\n",
    "tests/accuracy/other_pkt/fixtures/data.txt": "two\n",
}
TEST = "tests/accuracy/pkt/test_x.py::test_x"


@pytest.fixture
def small_tree(tmp_path):
    for name, text in TREE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("edited, moves", [
    ("tests/accuracy/kit/helper.py", True),            # imported by the check
    ("tests/accuracy/pkt/fixtures/data.txt", True),    # a data file of its packet
    ("tests/accuracy/pkt/test_x.py", True),            # the check itself
    ("tests/accuracy/kit/other.py", False),            # a kit module it does not import
    ("tests/accuracy/other_pkt/fixtures/data.txt", False),
])
def test_editing_what_the_check_reads_changes_its_digest(small_tree, edited, moves):
    before = retro.digest(TEST, repo=small_tree)
    with (small_tree / edited).open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")

    assert (retro.digest(TEST, repo=small_tree) != before) == moves


def test_the_digest_does_not_depend_on_where_the_tree_sits(small_tree, tmp_path_factory):
    moved = tmp_path_factory.mktemp("moved") / "tree"
    shutil.copytree(small_tree, moved)

    assert retro.digest(TEST, repo=moved) == retro.digest(TEST, repo=small_tree)


# --- choosing rows -----------------------------------------------------------------------------------

def _row(number: int, platform: str = "any", replay: str = "public") -> dict:
    return {"id": f"R{number}", "test": "tests/accuracy/suite_strength/test_retro_tool.py::t",
            "platform": platform, "replay": replay}


@given(st.integers(1, 60), st.integers(1, 9), st.integers(0, 10_000))
@pure
def test_the_nightly_slices_replay_every_row_once_in_a_cycle(count, of, start):
    rows = [_row(number) for number in range(1, count + 1)]
    seen = [row["id"] for day in range(start, start + of) for row in retro.weekly_slice(rows, of, day)]

    assert sorted(seen) == sorted(row["id"] for row in rows)


@pytest.mark.parametrize("row, platform, here", [
    (_row(1), "linux", True),
    (_row(1, "windows"), "linux", False),
    (_row(1, "windows"), "win32", True),
    (_row(98, replay="open"), "linux", False),
    ({**_row(2), "test": "tests/accuracy/no_packet/test_none.py::t"}, "linux", False),
])
def test_a_row_replays_on_its_platform_once_its_check_exists(row, platform, here):
    assert retro.replayable_here(row, platform) == here


def test_all_names_every_replayable_row():
    rows = [_row(1), _row(2), _row(98, replay="open")]

    assert [row["id"] for row in retro.chosen(rows, {"all"})] == ["R1", "R2"]
    assert [row["id"] for row in retro.chosen(rows, {"R2"})] == ["R2"]
