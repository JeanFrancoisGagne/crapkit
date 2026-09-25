"""tools/accuracy/retro.py: the replay verdict, the digest and the row choice.

The replay rule (the plan's retro section): before counts as red only when the
check fails on an AssertionError, a pin_ruling mismatch included; any other
failure is `not replayable`; the fix must pass. The planted repo below has a
`crapkit` whose `double` answers 3n at its first commit and 2n at its second,
so the expected verdicts are known before anything runs.

The planted replays build two worktrees and two venvs each (about 10 s), so
they run nightly; the verdict, digest and slicing rules run on every push.
"""
from __future__ import annotations

import hashlib
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


@pytest.mark.nightly
@pytest.mark.process
def test_a_planted_bug_reads_red_before_and_passes_after(planted):
    before, fix = retro.replay(_bug(planted, "test_double_doubles"), retro.CURRENT, planted.site)

    assert (before.verdict, before.failure_class, fix.verdict) == ("red", "AssertionError", "pass")
    assert "12" in before.evidence


@pytest.mark.nightly
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


# --- the commands, in-process, with the replay stood in for ---------------------------------------
#
# Each command reads bugs.tsv and ledger.tsv, picks rows and replays them. The
# replay itself (worktrees and venvs) is what the planted repo above checks;
# here it is a stand-in that returns the outcomes a test names, so the row
# choice, the ledger rewrite and the exit codes are checked on every push.

NODE = "tests/accuracy/suite_strength/test_retro_tool.py::test_double_doubles"


def _bug_row(bug_id: str, replay: str = "public", platform: str = "any") -> dict:
    return {"id": bug_id, "fix_commits": "b" * 12, "before_commit": "a" * 12, "packet": "p",
            "test": NODE, "probe": "", "method": "hand", "platform": platform,
            "replay": replay, "calc": "c", "symptom": "s"}


def _ledger_row(bug_id: str, before: str = "red", digest: str = "") -> dict:
    return {"id": bug_id, "test": NODE, "before_commit": "a" * 12, "fix_commit": "b" * 12,
            "lizard": retro.LIZARD, "before": before, "failure_class": "AssertionError",
            "before_evidence": "e", "fix": "pass", "fix_evidence": "f",
            "digest": digest or retro.digest(NODE), "replayed": "2026-09-01", "note": ""}


@pytest.fixture
def tables(tmp_path, monkeypatch):
    """bugs.tsv and ledger.tsv under tmp_path, and a replay that answers from `answers`."""
    bugs, ledger = tmp_path / "bugs.tsv", tmp_path / "ledger.tsv"
    monkeypatch.setattr(retro, "BUGS", bugs)
    monkeypatch.setattr(retro, "LEDGER", ledger)
    answers, replayed = {}, []

    def replay(bug, python, site=None):
        replayed.append(bug.id)
        return answers.get(bug.id, (retro.Outcome("red", "AssertionError", "wrong"),
                                    retro.Outcome("pass", "", "1 item(s) passed")))

    monkeypatch.setattr(retro, "replay", replay)

    def write(bug_rows: list[dict], ledger_rows: list[dict]) -> None:
        retro.write_table(bugs, retro.BUG_COLUMNS, bug_rows)
        retro.write_table(ledger, retro.LEDGER_COLUMNS, ledger_rows)

    return SimpleNamespace(write=write, answers=answers, replayed=replayed, ledger=ledger)


def test_run_record_rewrites_the_replayed_rows_and_keeps_the_rest(tables):
    tables.write([_bug_row("R1"), _bug_row("R2")], [_ledger_row("R2")])

    assert retro.main(["run", "R1", "--record"]) == 0

    rows = {row["id"]: row for row in retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)}
    assert tables.replayed == ["R1"]
    assert (rows["R1"]["before"], rows["R1"]["fix"], rows["R1"]["digest"]) == (
        "red", "pass", retro.digest(NODE))
    assert rows["R2"] == _ledger_row("R2")


def test_run_without_record_leaves_the_ledger_as_it_was(tables):
    tables.write([_bug_row("R1")], [])

    assert retro.main(["run", "all"]) == 0
    assert retro.read_table(tables.ledger, retro.LEDGER_COLUMNS) == []


def test_a_replay_that_contradicts_the_ledger_exits_one_and_says_why(tables, capsys):
    tables.write([_bug_row("R1")], [_ledger_row("R1")])
    tables.answers["R1"] = (retro.Outcome("not replayable", "KeyError", "k"),
                            retro.Outcome("pass"))

    assert retro.main(["run", "R1"]) == 1
    assert "R1: the ledger says red on the before commit, the replay says not replayable" in (
        capsys.readouterr().err)


def test_run_refuses_ids_with_no_replayable_row(tables, capsys):
    tables.write([_bug_row("R1", replay="open")], [])

    assert retro.main(["run", "R1", "R9"]) == 3
    assert "no replayable bugs.tsv row for R1, R9 here" in capsys.readouterr().err


def test_nightly_replays_stale_rows_and_its_slice_and_never_a_bundle_row(tables):
    rows = [_bug_row("R1"), _bug_row("R2"), _bug_row("R3", replay="bundle"), _bug_row("R4")]
    tables.write(rows, [_ledger_row("R1"), _ledger_row("R2"), _ledger_row("R3"),
                        _ledger_row("R4", digest="0" * 64)])

    assert retro.main(["nightly", "--slice-of", "3", "--day", "0"]) == 0
    # R4's digest moved; slice 0 of the public rows R1, R2, R4 in id order is R1.
    assert sorted(tables.replayed) == ["R1", "R4"]


def test_release_replays_stale_rows_and_every_bundle_row(tables):
    rows = [_bug_row("R1"), _bug_row("R2", replay="bundle"), _bug_row("R3"),
            _bug_row("R4", platform="macos" if not sys.platform.startswith("darwin") else "linux")]
    tables.write(rows, [_ledger_row("R1"), _ledger_row("R2"), _ledger_row("R4")])

    assert retro.main(["release"]) == 0
    # R3 has no ledger row (stale), R2 is a bundle row, R4 runs on another platform.
    assert sorted(tables.replayed) == ["R2", "R3"]


def test_stale_lists_the_rows_to_replay(tables, capsys):
    tables.write([_bug_row("R1"), _bug_row("R2")], [_ledger_row("R1")])

    assert retro.main(["stale"]) == 0
    assert capsys.readouterr().out == f"R2\t{NODE}\n"


def test_digest_prints_the_check_s_digest(capsys):
    assert retro.main(["digest", NODE]) == 0
    assert capsys.readouterr().out == retro.digest(NODE) + "\n"


def test_a_bug_s_replayed_fix_is_its_last_fix_commit():
    row = {**_bug_row("R1"), "fix_commits": "aaaaaaaaaaaa, bbbbbbbbbbbb", "probe": "R1.py"}

    assert retro.bug_of(row) == retro.Bug("R1", NODE, "a" * 12, "b" * 12, "R1.py")
    assert retro.bug_of({**row, "fix_commits": ""}).fix == ""


# --- sync: bugs.tsv follows the check names each packet landed -----------------------------------

def _sync_row(bug_id, packet, test, **fields):
    row = {**_bug_row(bug_id), "packet": packet, "test": test, "calc": f"calc {bug_id}",
           "symptom": f"symptom {bug_id}"}
    return {**row, **fields}


BUGS_BEFORE = [
    _sync_row("R1", "p1", "tests/accuracy/p1/test_a.py::test_proposed"),
    _sync_row("R1", "p2", "tests/accuracy/p2/test_b.py::test_kept", calc="calc R1 in p2"),
    _sync_row("R2", "p1", "tests/accuracy/p1/test_a.py::test_probe", probe="R2.py", method="model"),
    _sync_row("R3", "p3", "tests/accuracy/p3/test_c.py::test_not_landed"),
    _sync_row("R4", "p1", "tests/accuracy/p1/test_a.py::test_open", replay="open"),
]
P1_RETRO = ("id\tfix_commit\ttest\tplatform\n"
            "R1\tbbb\ttests/accuracy/p1/test_a.py::test_confirmed[x]\twindows\n"
            "R2\tbbb\ttests/accuracy/p1/test_a.py::test_probe\t\n"
            "R4\tbbb\ttests/accuracy/p1/test_a.py::test_open_confirmed\t\n")


def _landed(tmp_path: Path, table: str = P1_RETRO) -> Path:
    accuracy = tmp_path / "accuracy"
    (accuracy / "p1").mkdir(parents=True)
    (accuracy / "p1" / "retro.tsv").write_text(table, encoding="utf-8")
    (accuracy / "p3").mkdir()
    return accuracy


def test_sync_replaces_a_landed_packet_s_rows_with_the_pairs_it_lists(tmp_path):
    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path)))

    by_key = {(row["id"], row["test"]): row for row in synced}
    assert sorted(by_key) == [
        ("R1", "tests/accuracy/p1/test_a.py::test_confirmed[x]"),
        ("R1", "tests/accuracy/p2/test_b.py::test_kept"),
        ("R2", "tests/accuracy/p1/test_a.py::test_probe"),
        ("R3", "tests/accuracy/p3/test_c.py::test_not_landed"),
        ("R4", "tests/accuracy/p1/test_a.py::test_open_confirmed")]
    confirmed = by_key[("R1", "tests/accuracy/p1/test_a.py::test_confirmed[x]")]
    assert (confirmed["calc"], confirmed["platform"], confirmed["probe"]) == ("calc R1", "windows", "")
    assert by_key[("R2", "tests/accuracy/p1/test_a.py::test_probe")] == BUGS_BEFORE[2]
    assert by_key[("R1", "tests/accuracy/p2/test_b.py::test_kept")] == BUGS_BEFORE[1]


def test_sync_copies_another_packet_s_row_when_the_bug_is_new_to_this_one(tmp_path):
    table = "id\ttest\nR3\ttests/accuracy/p1/test_a.py::test_moved\n"

    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path, table)))

    moved = [row for row in synced if row["test"].endswith("test_moved")]
    assert [(row["packet"], row["calc"], row["platform"]) for row in moved] == [
        ("p1", "calc R3", "any")]


def test_sync_refuses_a_bug_bugs_tsv_does_not_know(tmp_path):
    table = "id\ttest\nR9\ttests/accuracy/p1/test_a.py::test_new\n"

    with pytest.raises(retro.RetroError, match="p1/retro.tsv names R9, which bugs.tsv has no row"):
        retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path, table)))


def test_the_synced_ledger_keeps_what_was_replayed_and_waits_on_the_rest(tmp_path):
    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path)))
    recorded = {**_ledger_row("R2"), "test": "tests/accuracy/p1/test_a.py::test_probe"}
    stale = {**_ledger_row("R1"), "test": "tests/accuracy/p1/test_a.py::test_proposed"}
    ledger = {retro.row_key(row): row for row in (recorded, stale)}

    rows = {retro.row_key(row): row for row in retro.synced_ledger(ledger, synced)}

    assert sorted(rows) == sorted(retro.row_key(row) for row in synced)
    assert rows[retro.row_key(recorded)] == recorded
    confirmed = rows[("R1", "tests/accuracy/p1/test_a.py::test_confirmed[x]")]
    assert (confirmed["before"], confirmed["fix"], confirmed["fix_commit"], confirmed["note"]) == (
        "pending", "pending", "b" * 12, retro.PENDING_NOTE)
    assert rows[("R4", "tests/accuracy/p1/test_a.py::test_open_confirmed")]["before"] == "open"


def test_the_sync_command_rewrites_both_tables(tables, tmp_path, monkeypatch, capsys):
    tables.write(BUGS_BEFORE, [])
    monkeypatch.setattr(retro, "ACCURACY", _landed(tmp_path))

    assert retro.main(["sync"]) == 0

    assert len(retro.read_table(retro.BUGS, retro.BUG_COLUMNS)) == 5
    assert len(retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)) == 5
    assert capsys.readouterr().out == "retro: bugs.tsv holds 5 rows (2 new pairs); ledger.tsv follows\n"


def test_sync_leaves_bugs_tsv_as_it_was_when_every_packet_lists_what_it_holds():
    tables = {"p2": [{"id": "R1", "test": "tests/accuracy/p2/test_b.py::test_kept"}]}

    assert retro.synced_bugs(BUGS_BEFORE, tables) == BUGS_BEFORE


# --- what the tools mutation run left alive in the verdict and digest code ---------------------------

def test_a_record_carries_every_field_the_replay_reads():
    assert retro._record("t::a", _call(None)) == {
        "nodeid": "t::a", "outcome": "passed", "exc_type": "", "assertion": False, "message": ""}
    assert retro._record("t::b", _call(KeyError("x" * 400))) == {
        "nodeid": "t::b", "outcome": "failed", "exc_type": "KeyError", "assertion": False,
        "message": "'" + "x" * 299}


def test_an_xfail_is_not_a_failure_either():
    xfailed = type("XFailed", (Exception,), {})

    assert retro._record("t::a", _call(xfailed("x")))["outcome"] == "skipped"


def _line(nodeid: str, outcome: str, exc_type: str = "") -> str:
    return (f'{{"nodeid": "{nodeid}", "outcome": "{outcome}", "exc_type": "{exc_type}", '
            f'"assertion": false, "message": ""}}')


def test_an_item_keeps_its_first_failure_and_a_skipped_item_is_left_out():
    lines = [_line("t::a", "failed", "KeyError"), _line("t::a", "failed", "OSError"),
             _line("t::b", "skipped", "Skipped"), _line("t::c", "passed")]

    assert [(row["nodeid"], row["exc_type"]) for row in retro.item_outcomes(lines)] == [
        ("t::a", "KeyError"), ("t::c", "")]


def _rec(nodeid: str, exc_type: str = "", assertion: bool = False) -> dict:
    outcome = "failed" if exc_type else "passed"
    return {"nodeid": nodeid, "outcome": outcome, "exc_type": exc_type, "assertion": assertion,
            "message": f"m {nodeid}"}


@pytest.mark.parametrize("records, outcome", [
    ([_rec("t::a"), _rec("t::b")], retro.Outcome("green", "", "2 item(s) passed")),
    ([_rec("t::a", "KeyError"), _rec("t::b", "AssertionError", True)],
     retro.Outcome("red", "AssertionError", "m t::b")),
    ([_rec("t::a", "KeyError")], retro.Outcome("not replayable", "KeyError", "m t::a")),
    ([], retro.Outcome("not replayable", "NotCollected", "the check collected no test")),
])
def test_before_names_the_record_that_decided_it(records, outcome):
    assert retro.classify_before(records) == outcome


@pytest.mark.parametrize("records, outcome", [
    ([_rec("t::a")], retro.Outcome("pass", "", "1 item(s) passed")),
    ([_rec("t::a"), _rec("t::b", "OSError")], retro.Outcome("fail", "OSError", "m t::b")),
    ([], retro.Outcome("fail", "NotCollected", "the check collected no test")),
])
def test_the_fix_names_the_record_that_decided_it(records, outcome):
    assert retro.classify_fix(records) == outcome


def test_a_failing_fix_quotes_two_hundred_characters_of_its_evidence():
    got = retro.contradiction({"id": "R1"}, retro.Outcome("red"), retro.Outcome("fail", "E", "e" * 300))

    assert got == "R1: the check fails on its fix commit: " + "e" * 200


# The digest, worked out here from its definition: sha256 over each file's
# repo path, a NUL and the sha256 of its bytes, in path order; 16 hex digits.

def _expected_digest(tree: Path, relative: list[str]) -> str:
    hashed = hashlib.sha256()
    for name in sorted(relative):
        hashed.update(name.encode() + b"\0")
        hashed.update(hashlib.sha256((tree / name).read_bytes()).digest())
    return hashed.hexdigest()[:16]


READS = ["tests/accuracy/__init__.py", "tests/accuracy/kit/__init__.py",
         "tests/accuracy/kit/helper.py", "tests/accuracy/pkt/__init__.py",
         "tests/accuracy/pkt/fixtures/data.txt", "tests/accuracy/pkt/test_x.py"]


def test_the_digest_is_sha256_over_each_file_s_path_and_bytes(small_tree):
    assert retro.digest(TEST, repo=small_tree) == _expected_digest(small_tree, READS)


def test_the_check_s_files_are_its_closure_and_its_packet_s_data(small_tree):
    files = retro.check_files(TEST, repo=small_tree)

    assert [path.relative_to(small_tree.resolve()).as_posix() for path in files] == sorted(READS)


def test_a_probe_is_one_more_file_and_editing_it_moves_the_digest(small_tree):
    probe = small_tree / "tests/accuracy/suite_strength/retro/probes/R0.py"
    probe.parent.mkdir(parents=True)
    probe.write_text("VALUE = 1\n", encoding="utf-8")
    with_probe = READS + ["tests/accuracy/suite_strength/retro/probes/R0.py"]

    assert retro.digest(TEST, "R0.py", repo=small_tree) == _expected_digest(small_tree, with_probe)
    probe.write_text("VALUE = 2\n", encoding="utf-8")
    assert retro.digest(TEST, "R0.py", repo=small_tree) == _expected_digest(small_tree, with_probe)


TOOL_TREE = {
    "tests/accuracy/pkt/test_t.py": "import toolmod\nfrom helpers import shared\n\n\n"
                                    "def test_t():\n    assert toolmod.X == shared.Y\n",
    "tools/accuracy/toolmod.py": "X = 1\n",
    "tools/helpers/__init__.py": "",
    "tools/helpers/shared.py": "Y = 1\n",
    "tests/accuracy/pkt/README.md": "notes\n",
    "tests/accuracy/pkt/cases/fixtures/deep.txt": "three\n",
}


@pytest.mark.parametrize("edited, moves", [
    ("tools/accuracy/toolmod.py", True),                  # imported through tools/accuracy
    ("tools/helpers/shared.py", True),                    # imported through tools/
    ("tests/accuracy/pkt/cases/fixtures/deep.txt", True),  # a data dir below the packet's top
    ("tests/accuracy/pkt/README.md", False),              # a packet file in no data dir
])
def test_the_closure_reaches_through_both_tool_roots(small_tree, edited, moves):
    for name, text in TOOL_TREE.items():
        (small_tree / name).parent.mkdir(parents=True, exist_ok=True)
        (small_tree / name).write_text(text, encoding="utf-8")
    test = "tests/accuracy/pkt/test_t.py::test_t"
    before = retro.digest(test, repo=small_tree)
    with (small_tree / edited).open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")

    assert (retro.digest(test, repo=small_tree) != before) == moves
