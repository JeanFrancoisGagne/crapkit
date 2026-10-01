"""tools/accuracy/retro.py: the replay verdict, the digest and the row choice.

The replay rule (the plan's retro section): before counts as red only when the
check fails on an AssertionError, a pin_ruling mismatch included; any other
failure is `not replayable`; the fix must pass. The planted repo below has a
`crapkit` whose `double` answers 3n at its first commit and 2n at its second,
so the expected verdicts are known before anything runs.

The planted replays build two worktrees and two venvs each (about 10 s), the
bundle fetch runs about fifteen git commands and the probe and command tests
start a Python child each, so they run nightly; the verdict, digest and slicing
rules and the plumbing, with git and uv stood in for, run on every push.
"""
from __future__ import annotations

from dataclasses import replace
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import runpy
import shutil
import sys
import sysconfig
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


def _reported(tmp_path, monkeypatch, error: BaseException | None, **report) -> list[dict]:
    """The records the plugin writes for one call phase that raised `error` and whose
    final pytest report carries `report`'s fields."""
    outcomes = tmp_path / "outcomes.jsonl"
    monkeypatch.setenv(retro.OUTCOMES_ENV, str(outcomes))
    retro.pytest_runtest_makereport(SimpleNamespace(nodeid="t::a"), _call(error))
    retro.pytest_runtest_logreport(SimpleNamespace(nodeid="t::a", when="call", **report))
    return retro.item_outcomes(outcomes.read_text(encoding="utf-8").splitlines())


def test_an_open_defect_s_strict_xfail_is_not_a_failure(tmp_path, monkeypatch):
    """A check pins an open defect with a strict xfail (rulings.applies). The defect's
    RulingDefect reaches makereport before pytest turns it into an xfail, so the replay
    reads pytest's own report: an item that failed as declared is no failure."""
    defect = RulingDefect("D1c: crapkit still says 0")

    assert _reported(tmp_path, monkeypatch, defect, wasxfail="D1c is an open defect") == []


def test_a_failure_pytest_reports_as_a_failure_keeps_its_class(tmp_path, monkeypatch):
    records = _reported(tmp_path, monkeypatch, RulingDefect("D1c: crapkit still says 0"))

    assert [(row["outcome"], row["exc_type"], row["assertion"]) for row in records] == [
        ("failed", "RulingDefect", True)]


def test_a_phase_makereport_never_saw_writes_nothing(tmp_path, monkeypatch):
    """xdist's controller logs the reports its workers made; the worker wrote them."""
    outcomes = tmp_path / "outcomes.jsonl"
    monkeypatch.setenv(retro.OUTCOMES_ENV, str(outcomes))

    retro.pytest_runtest_logreport(SimpleNamespace(nodeid="t::b", when="call"))

    assert not outcomes.exists()


def _marked(*pythons: tuple) -> SimpleNamespace:
    """An item with one python marker per (major, minor) in `pythons`."""
    marks = [SimpleNamespace(args=version) for version in pythons]
    return SimpleNamespace(iter_markers=lambda name: marks if name == "python" else [])


def _collected(monkeypatch, items: list, replay: bool) -> tuple[list, list]:
    """What the plugin leaves collected and what it reports deselected."""
    if replay:
        monkeypatch.setenv(retro.OUTCOMES_ENV, "outcomes.jsonl")
    else:
        monkeypatch.delenv(retro.OUTCOMES_ENV, raising=False)
    deselected = []
    hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    config = SimpleNamespace(hook=hook)
    retro.pytest_collection_modifyitems(config, items)
    return items, deselected


def test_a_replay_drops_an_item_marked_for_a_newer_python(monkeypatch):
    """The replay runs with CRAPKIT_ACCURACY_COLLECT_ALL, which keeps every item, so a
    shape marked python(3, 14) ran on 3.12 and failed on a KeyError: no evidence about
    the commit, yet it failed R43's fix. The replay drops such an item instead."""
    plain, old, new = _marked(), _marked((3, 0)), _marked((3, 0), (99, 0))

    assert _collected(monkeypatch, [plain, old, new], replay=True) == ([plain, old], [new])


def test_outside_a_replay_the_plugin_drops_nothing(monkeypatch):
    new = _marked((99, 0))

    assert _collected(monkeypatch, [new], replay=False) == ([new], [])


XFAIL_CHECK = """\
import pytest


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="an open defect")
def test_open_defect():
    assert 1 == 2


@pytest.mark.python(99, 0)
def test_too_new_for_this_python():
    assert 1 == 4


def test_right():
    assert 1 == 1


def test_wrong():
    assert 1 == 3
"""


@pytest.mark.nightly
@pytest.mark.process
def test_real_pytest_records_a_declared_xfail_as_no_failure(tmp_path):
    """The two hooks against pytest itself: the stand-ins above assume where pytest
    reports an xfail."""
    check = tmp_path / "test_xfail_check.py"
    check.write_text(XFAIL_CHECK, encoding="utf-8")
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.touch()
    env = {**retro.os.environ, retro.OUTCOMES_ENV: str(outcomes), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(REPO / "tools" / "accuracy")}
    argv = [sys.executable, "-m", "pytest", str(check), "-q", "-p", "no:cacheprovider",
            "-p", "no:randomly", "-p", "retro", "--rootdir", str(tmp_path)]

    retro._run(argv, cwd=tmp_path, env=env)

    records = retro.item_outcomes(outcomes.read_text(encoding="utf-8").splitlines())
    assert sorted((row["nodeid"].rpartition("::")[2], row["outcome"]) for row in records) == [
        ("test_right", "passed"), ("test_wrong", "failed")]


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
CALC = "def double(n):\n    return n * {factor}\n"
CHECK = """\
import importlib

from accuracy.kit import drive


def test_double_doubles(tmp_path):
    assert drive.Driver(tmp_path, spawn=True).json("double", "4") == {"value": 8}


def test_an_unknown_command(tmp_path):
    drive.Driver(tmp_path, spawn=True).run("halve", "4")


def test_double_doubles_in_process():
    assert importlib.import_module("crapkit.calc").double(4) == 8
"""


@pytest.fixture(scope="module")
def planted(repo_templates, tmp_path_factory):
    """A repo whose first commit plants the bug (3n) and whose second fixes it (2n),
    and a check file outside the accuracy tree that drives it."""
    base = tmp_path_factory.mktemp("planted")
    spec = repos.Spec(steps=(
        repos.Commit(files={"src/crapkit/__init__.py": "", "src/crapkit/__main__.py":
                            MAIN.format(factor=3), "src/crapkit/calc.py": CALC.format(factor=3)},
                     message="plant"),
        repos.Commit(files={"src/crapkit/__main__.py": MAIN.format(factor=2),
                            "src/crapkit/calc.py": CALC.format(factor=2)}, message="fix")))
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


@pytest.mark.nightly
@pytest.mark.process
def test_a_check_that_imports_crapkit_in_process_reads_the_commit_s(planted):
    """A check may call crapkit in its own process (the score-model packet's production
    seam does): the replay hands it the commit's crapkit, not the one beside pytest."""
    bug = _bug(planted, "test_double_doubles_in_process")

    before, fix = retro.replay(bug, retro.CURRENT, planted.site)

    assert (before.verdict, before.failure_class, fix.verdict) == ("red", "AssertionError", "pass")


def _answering(monkeypatch, returncode: int, stdout: str = "", stderr: str = "") -> dict:
    seen = {}

    def run(argv, cwd=retro.REPO, env=None):
        seen.update(argv=argv, env=env)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(retro, "_run", run)
    return seen


def test_the_commit_s_crapkit_root_is_asked_without_this_tree_s(tmp_path, monkeypatch):
    (tmp_path / "src" / "crapkit").mkdir(parents=True)
    (tmp_path / "src" / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", retro.os.pathsep.join([str(tmp_path / "src"), "lib"]))
    seen = _answering(monkeypatch, 0, "/old/src\n")

    assert retro.crapkit_root(Path("py")) == "/old/src"
    assert seen["argv"] == [Path("py"), "-c", retro.ROOT_CODE]
    assert seen["env"]["PYTHONPATH"] == "lib"
    assert seen["env"]["PATH"] == retro.os.environ["PATH"]


def test_a_venv_that_cannot_import_crapkit_is_refused(monkeypatch):
    _answering(monkeypatch, 1, stderr="ModuleNotFoundError: crapkit\n")

    with pytest.raises(retro.RetroError, match="py cannot import crapkit: ModuleNotFoundError"):
        retro.crapkit_root(Path("py"))


def _site_packages(tmp_path: Path) -> Path:
    """A wheel venv's site-packages: crapkit, its dist-info, and the runner beside them."""
    site = tmp_path / "venv" / "Lib" / "site-packages"
    for name in ("crapkit/__init__.py", "crapkit/cli/__init__.py", "crapkit-0.8.0.dist-info/METADATA",
                 "pytest_cov/__init__.py", "lizard.py"):
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_bytes(b"# " + name.encode() + b"\n")
    return site


def test_a_wheel_s_crapkit_reaches_the_check_without_the_rest_of_site_packages(tmp_path, monkeypatch):
    """A bare venv the check starts must not import the replay venv's pytest_cov
    through PYTHONPATH: the check gets crapkit alone, copied once per venv."""
    site = _site_packages(tmp_path)
    monkeypatch.setattr(retro, "crapkit_root", lambda interpreter: str(site))

    root = Path(retro.import_root(Path("py")))
    (site / "crapkit" / "__init__.py").write_bytes(b"# changed after the copy\n")

    assert root == site.parent / retro.ALONE
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) == [
        "crapkit-0.8.0.dist-info/METADATA", "crapkit/__init__.py", "crapkit/cli/__init__.py"]
    assert Path(retro.import_root(Path("py"))) == root
    assert (root / "crapkit" / "__init__.py").read_bytes() == b"# crapkit/__init__.py\n"


def test_a_crapkit_outside_site_packages_is_taken_where_it_is(monkeypatch):
    """A link install imports crapkit from the commit's src/, which holds nothing else."""
    monkeypatch.setattr(retro, "crapkit_root", lambda interpreter: "/wt/abc/src")

    assert retro.import_root(Path("py")) == str(Path("/wt/abc/src"))


def test_the_commit_s_crapkit_leads_the_check_s_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "lib"))

    env = retro._pytest_env(Path("py"), tmp_path / "o", "/old/src")

    assert env["PYTHONPATH"].split(retro.os.pathsep) == [
        "/old/src", str(retro.REPO / "tests"), str(retro.REPO / "tools" / "accuracy"),
        str(tmp_path / "lib")]


CUT = "CRAPKIT_ACCURACY_LANGUAGES=python,typescript CRAPKIT_ACCURACY_ROOT_PATHS=entries"


def test_a_row_s_env_reaches_the_replayed_check(tmp_path):
    """A packet replays an old commit with its set cut to the languages that commit
    read, and the root scope named by the tree's top-level entries: the row says so."""
    env = retro._pytest_env(Path("py"), tmp_path / "o", "/old/src", CUT)

    assert (env["CRAPKIT_ACCURACY_LANGUAGES"], env["CRAPKIT_ACCURACY_ROOT_PATHS"]) == (
        "python,typescript", "entries")
    assert env[retro.PYTHON_ENV] == "py"


def test_the_replayed_check_learns_the_commit_s_checkout(tmp_path, monkeypatch):
    """A wheel carries src/ only: a check that runs the commit's action.yml finds it
    through the checkout the replay names, and a run with no checkout names none."""
    monkeypatch.delenv(retro.CHECKOUT_ENV, raising=False)

    named = retro._pytest_env(Path("py"), tmp_path / "o", "/old/src", "", tmp_path / "wt")
    unnamed = retro._pytest_env(Path("py"), tmp_path / "o", "/old/src")

    assert (named[retro.CHECKOUT_ENV], retro.CHECKOUT_ENV in unnamed) == (str(tmp_path / "wt"), False)


@pytest.mark.parametrize("cell, says", [
    ("CRAPKIT_ACCURACY_PYTHON=/usr/bin/python3", "CRAPKIT_ACCURACY_PYTHON"),
    ("CRAPKIT_ACCURACY_LANGUAGES", "CRAPKIT_ACCURACY_LANGUAGES"),
    ("PATH=/bin", "PATH"),
])
def test_a_row_s_env_names_only_a_replay_switch(tmp_path, cell, says):
    """The replay's own variables and anything outside the accuracy switches stay the
    replay's: a row cannot point the check at another crapkit."""
    with pytest.raises(retro.RetroError, match=f"env names {says}"):
        retro._pytest_env(Path("py"), tmp_path / "o", "", cell)


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


def test_a_row_s_env_moves_its_digest_and_no_env_leaves_it_as_it_was(small_tree):
    """A replay under another cut is another replay; a row with no env keeps the
    digest every recorded row already carries."""
    plain = retro.digest(TEST, repo=small_tree)

    assert retro.digest(TEST, repo=small_tree, env="") == plain
    assert retro.digest(TEST, repo=small_tree, env=CUT) != plain


def test_the_digest_does_not_depend_on_where_the_tree_sits(small_tree, tmp_path_factory):
    moved = tmp_path_factory.mktemp("moved") / "tree"
    shutil.copytree(small_tree, moved)

    assert retro.digest(TEST, repo=moved) == retro.digest(TEST, repo=small_tree)


@pytest.mark.parametrize("generated", [
    "tests/accuracy/pkt/fixtures/__pycache__/data.cpython-312.pyc",
    "tests/accuracy/pkt/fixtures/__pycache__/data.cpython-312.pyc.64312",
])
def test_bytecode_beside_a_data_file_leaves_the_digest_alone(small_tree, generated):
    """Python writes __pycache__ beside a module a check imports from a data folder,
    and leaves a .pyc.<pid> while it writes one. No checkout holds either, so two
    worktrees of one commit gave the check two digests, and a row recorded in one
    read stale in the other."""
    before = retro.digest(TEST, repo=small_tree)
    (small_tree / generated).parent.mkdir(parents=True, exist_ok=True)
    (small_tree / generated).write_bytes(b"\x00bytecode")

    assert retro.digest(TEST, repo=small_tree) == before


# --- choosing rows -----------------------------------------------------------------------------------

# A check this file holds: a row replays only when its test function exists.
NODE = "tests/accuracy/suite_strength/test_retro_tool.py::test_digest_prints_the_check_s_digest"


def _row(number: int, platform: str = "any", replay: str = "public") -> dict:
    return {"id": f"R{number}", "test": NODE,
            "platform": platform, "replay": replay}


@pytest.mark.parametrize("row, platform, here", [
    (_row(1), "linux", True),
    (_row(1, "windows"), "linux", False),
    (_row(1, "windows"), "win32", True),
    (_row(98, replay="open"), "linux", False),
    ({**_row(2), "test": "tests/accuracy/no_packet/test_none.py::t"}, "linux", False),
    ({**_row(3), "test": "tests/accuracy/suite_strength/test_retro_tool.py::test_not_written_yet"},
     "linux", False),
    ({**_row(4), "test": f"{NODE}[case]"}, "linux", True),
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



def _bug_row(bug_id: str, replay: str = "public", platform: str = "any") -> dict:
    return {"id": bug_id, "fix_commits": "b" * 12, "before_commit": "a" * 12, "packet": "p",
            "test": NODE, "probe": "", "env": "", "method": "hand", "platform": platform,
            "replay": replay, "calc": "c", "symptom": "s"}


def _ledger_row(bug_id: str, before: str = "red", digest: str = "") -> dict:
    return {"id": bug_id, "test": NODE, "before_commit": "a" * 12, "fix_commit": "b" * 12,
            "lizard": retro.LIZARD, "before": before, "failure_class": "AssertionError",
            "before_evidence": "e", "fix": "pass", "fix_evidence": "f",
            "digest": digest or retro.digest(NODE), "replayed": "2026-09-01", "note": ""}


def test_a_table_write_that_fails_halfway_leaves_the_old_table(tmp_path, monkeypatch):
    """A full disk once emptied ledger.tsv mid-write and lost a replay run's records."""
    ledger = tmp_path / "ledger.tsv"
    retro.write_table(ledger, retro.LEDGER_COLUMNS, [_ledger_row("R1")])
    kept = ledger.read_bytes()
    real = Path.write_bytes

    def full_disk(path, data):
        real(path, data[:10])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", full_disk)
    with pytest.raises(OSError):
        retro.write_table(ledger, retro.LEDGER_COLUMNS, [_ledger_row("R1"), _ledger_row("R2")])

    assert ledger.read_bytes() == kept
    assert sorted(path.name for path in tmp_path.iterdir()) == ["ledger.tsv"]


@pytest.mark.parametrize("evidence, cell", [
    (r"--export C:\Users\ann\AppData\Local\Temp\pytest-of-ann\pytest-82559\defs\x.json",
     r"--export <tmp>\defs\x.json"),
    (r"root='E:\\scratch\\tmp\\pytest-of-ann\\pytest-105\\repo'", r"root='<tmp>\\repo'"),
    ("lane 'js' FAILED: /tmp/pytest-of-ann/pytest-3/t0/cov.json",
     "lane 'js' FAILED: <tmp>/t0/cov.json"),
    (r'File "C:\Users\ann\uv\python\lib\runpy.py", line 88',
     r'File "<home>\uv\python\lib\runpy.py", line 88'),
    ("in /home/ann/src/a.py\nand\t/Users/ann/b.py", "in <home>/src/a.py and <home>/b.py"),
    ("assert 3 == 0 in src/users/a.py", "assert 3 == 0 in src/users/a.py"),
    (r"re-baseline with `D:\scratch\retro\a815caa38248-venv-3.12-wheel\Scripts\python.exe -m",
     r"re-baseline with `<work>\a815caa38248-venv-3.12-wheel\Scripts\python.exe -m"),
    (r"CRAP...D:\\scratch\\retro\\20f00e137133\\js\\shared.js",
     r"CRAP...<work>\\20f00e137133\\js\\shared.js"),
    ('File "/tmp/retro/33ba48079aa6/src/crapkit/cli.py", line 3',
     'File "<work>/33ba48079aa6/src/crapkit/cli.py", line 3'),
    (r'File "C:\Users\ann\crapkit\.crapkit\accuracy\retro\33ba48079aa6\src\a.py"',
     r'File "<work>\33ba48079aa6\src\a.py"'),
    ("commit 33ba48079aa6 is not in the bundle", "commit 33ba48079aa6 is not in the bundle"),
])
def test_a_table_cell_names_no_local_path(evidence, cell):
    """A replay's evidence quotes the temp and home paths it ran under; the committed
    tables name them by role, so no user or machine reaches the public ledger."""
    assert retro._cell(evidence) == cell


@pytest.fixture
def tables(tmp_path, monkeypatch):
    """bugs.tsv and ledger.tsv under tmp_path, a replay that answers from `answers`,
    the verdict cache under tmp_path, and an env key whose image tag `env` names,
    with git and node versions that start no process."""
    bugs, ledger = tmp_path / "bugs.tsv", tmp_path / "ledger.tsv"
    monkeypatch.setattr(retro, "BUGS", bugs)
    monkeypatch.setattr(retro, "LEDGER", ledger)
    monkeypatch.setenv(retro.VERDICTS_ENV, str(tmp_path / "verdicts"))
    monkeypatch.setenv("CRAPKIT_ACCURACY_TIER", "push")
    for name in retro.RUNNER_IMAGE:
        monkeypatch.delenv(name, raising=False)
    env = {"image": "0a1b2c3d4e5f"}
    monkeypatch.setattr(retro, "_image_tag", lambda: env["image"])
    monkeypatch.setattr(retro, "_tool_version", lambda name: f"{name} version 1")
    answers, replayed = {}, []

    def replay(bug, python, site=None):
        replayed.append(bug.id)
        return answers.get(bug.id, (retro.Outcome("red", "AssertionError", "wrong"),
                                    retro.Outcome("pass", "", "1 item(s) passed")))

    monkeypatch.setattr(retro, "replay", replay)

    def write(bug_rows: list[dict], ledger_rows: list[dict]) -> None:
        retro.write_table(bugs, retro.BUG_COLUMNS, bug_rows)
        retro.write_table(ledger, retro.LEDGER_COLUMNS, ledger_rows)

    return SimpleNamespace(write=write, answers=answers, replayed=replayed, ledger=ledger, env=env,
                           verdicts=tmp_path / "verdicts")


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


def test_nightly_replays_every_public_row_with_no_verdict_for_its_key_and_never_a_bundle_row(tables):
    rows = [_bug_row("R1"), _bug_row("R2"), _bug_row("R3", replay="bundle"), _bug_row("R4")]
    tables.write(rows, [_ledger_row("R1"), _ledger_row("R2"), _ledger_row("R3"),
                        _ledger_row("R4", digest="0" * 64)])

    assert retro.main(["nightly"]) == 0
    assert sorted(tables.replayed) == ["R1", "R2", "R4"]


def test_a_nightly_with_every_verdict_kept_replays_nothing(tables):
    """The weekly slice re-judged a seventh of 178 unchanged rows every night, about
    22 container-minutes; a kept verdict for the same row digest and env key is that
    replay's answer already."""
    tables.write([_bug_row("R1"), _bug_row("R2")], [_ledger_row("R1"), _ledger_row("R2")])
    assert retro.main(["nightly"]) == 0
    tables.replayed.clear()

    assert retro.main(["nightly"]) == 0
    assert tables.replayed == []


@pytest.mark.parametrize("moved", [
    {"before_commit": "c" * 12},
    {"fix_commits": "b" * 12 + "," + "d" * 12},
    {"env": "CRAPKIT_ACCURACY_LANGUAGES=python"},
])
def test_a_nightly_replays_exactly_the_row_whose_inputs_moved(tables, moved):
    ledger = [_ledger_row("R1"), _ledger_row("R2")]
    tables.write([_bug_row("R1"), _bug_row("R2")], ledger)
    retro.main(["nightly"])
    tables.replayed.clear()
    tables.write([_bug_row("R1"), {**_bug_row("R2"), **moved}], ledger)

    retro.main(["nightly"])

    assert tables.replayed == ["R2"]


def test_a_kept_verdict_judges_its_row_as_the_fresh_replay_did(tables, capsys):
    """A nightly that reuses a contradicting verdict still exits 1 with the same
    words: a red night stays red until the row or its ledger changes."""
    tables.write([_bug_row("R1"), _bug_row("R2")], [_ledger_row("R1"), _ledger_row("R2")])
    tables.answers["R1"] = (retro.Outcome("not replayable", "KeyError", "k"), retro.Outcome("pass"))
    assert retro.main(["nightly"]) == 1
    fresh = capsys.readouterr()
    tables.replayed.clear()

    assert retro.main(["nightly"]) == 1

    kept = capsys.readouterr()
    assert tables.replayed == []
    assert kept.err == fresh.err != ""
    today = datetime.date.today().isoformat()
    assert kept.out == fresh.out.replace(", fix pass\n", f", fix pass (kept from {today})\n") != fresh.out


def _moves_the_image(tables, monkeypatch):
    tables.env["image"] = "ffffffffffff"
    return []


def _moves_the_tier(tables, monkeypatch):
    monkeypatch.setenv("CRAPKIT_ACCURACY_TIER", "release")
    return []


def _moves_the_runner_image(tables, monkeypatch):
    monkeypatch.setenv("ImageOS", "win25")
    monkeypatch.setenv("ImageVersion", "20261001.1")
    return []


def _moves_lizard(tables, monkeypatch):
    monkeypatch.setattr(retro, "LIZARD", "1.25.0")
    return []


def _moves_the_runner(tables, monkeypatch):
    monkeypatch.setattr(retro, "RUNNER", (*retro.RUNNER[:-1], "coverage==7.17.0"))
    return []


def _moves_node(tables, monkeypatch):
    monkeypatch.setattr(retro, "_tool_version", lambda name: f"{name} version 2")
    return []


def _moves_the_python(tables, monkeypatch):
    return ["--python", "3.99"]


@pytest.mark.parametrize("move", [_moves_the_image, _moves_the_tier, _moves_the_runner_image,
                                  _moves_lizard, _moves_the_runner, _moves_node, _moves_the_python])
def test_a_moved_env_key_replays_every_row_of_that_platform(tables, monkeypatch, move):
    tables.write([_bug_row("R1"), _bug_row("R2")], [_ledger_row("R1"), _ledger_row("R2")])
    retro.main(["nightly"])
    tables.replayed.clear()

    retro.main([*move(tables, monkeypatch), "nightly"])

    assert sorted(tables.replayed) == ["R1", "R2"]


def test_a_push_tier_verdict_does_not_serve_a_release_lookup(tables, monkeypatch):
    """kit/settings.py sizes Hypothesis at 200 examples at push and 5,000 at release:
    a nightly verdict never answers for the release tier."""
    tables.write([_bug_row("R1")], [_ledger_row("R1", digest="0" * 16)])
    retro.main(["nightly"])
    monkeypatch.setenv("CRAPKIT_ACCURACY_TIER", "release")
    tables.replayed.clear()

    assert retro.main(["release"]) == 0
    assert tables.replayed == ["R1"]
    tables.replayed.clear()
    assert retro.main(["release"]) == 0
    assert tables.replayed == []


def test_run_replays_a_row_with_a_kept_verdict_and_keeps_the_new_one(tables):
    tables.write([_bug_row("R1")], [])
    retro.main(["run", "R1"])
    tables.answers["R1"] = (retro.Outcome("red", "AssertionError", "other"), retro.Outcome("pass", "", "p"))

    retro.main(["run", "R1"])
    retro.main(["nightly"])

    assert tables.replayed == ["R1", "R1"]
    kept = retro.kept_verdict(retro.verdict_path(_bug_row("R1"), retro.env_key(retro.CURRENT)))
    assert kept == (tables.answers["R1"][0], tables.answers["R1"][1], datetime.date.today().isoformat())


def test_a_replay_keeps_its_verdict_under_the_env_key_the_row_id_and_the_row_digest(tables):
    tables.write([_bug_row("R1")], [])

    retro.main(["run", "R1"])

    key = retro.env_key(retro.CURRENT)
    path = tables.verdicts / key / "R1" / f"{retro.row_digest(_bug_row('R1'))}.json"
    assert retro.verdict_path(_bug_row("R1"), key) == path
    assert json.loads(path.read_bytes()) == {
        "id": "R1", "test": NODE, "before_commit": "a" * 12, "fix_commit": "b" * 12,
        "before": {"verdict": "red", "failure_class": "AssertionError", "evidence": "wrong"},
        "fix": {"verdict": "pass", "failure_class": "", "evidence": "1 item(s) passed"},
        "replayed": datetime.date.today().isoformat()}


def test_with_no_verdict_cache_named_the_verdicts_sit_beside_the_work_directory(monkeypatch):
    monkeypatch.delenv(retro.VERDICTS_ENV, raising=False)

    assert retro.verdicts_dir() == REPO / ".crapkit" / "accuracy" / "retro-verdicts"


def test_a_row_with_no_kept_verdict_reads_none(tmp_path):
    assert retro.kept_verdict(tmp_path / "R1" / "0.json") is None


@pytest.mark.parametrize("moved", [
    {"id": "R2"}, {"test": f"{NODE}[case]"}, {"before_commit": "c" * 12},
    {"fix_commits": "d" * 12}, {"env": "CRAPKIT_ACCURACY_LANGUAGES=python"}, {"probe": "R97.py"},
])
def test_the_row_digest_moves_with_each_part_of_the_row_s_identity(moved):
    row = _bug_row("R1")

    assert retro.row_digest(row) == retro.row_digest(dict(row))
    assert retro.row_digest({**row, **moved}) != retro.row_digest(row)


def test_the_row_digest_covers_the_check_s_files(monkeypatch):
    row = _bug_row("R1")
    before = retro.row_digest(row)
    monkeypatch.setattr(retro, "digest", lambda test, probe="", repo=retro.REPO, env="": "moved")

    assert retro.row_digest(row) != before


def test_the_env_key_names_the_os_python_and_tier_and_hashes_every_part(tables):
    parts = retro.env_parts("3.12")
    blob = json.dumps(parts, sort_keys=True).encode()

    assert retro.env_key("3.12") == f"{sys.platform}-3.12-push-{hashlib.sha256(blob).hexdigest()[:12]}"


@pytest.mark.parametrize("windows", [True, False])
def test_the_env_parts_are_every_input_a_verdict_depends_on_past_the_row(tables, monkeypatch, windows):
    monkeypatch.setattr(retro, "WINDOWS", windows)
    host = platform.platform() if windows else ""

    assert retro.env_parts("3.12") == {
        "os": sys.platform, "image": "0a1b2c3d4e5f", "host": host, "lizard": retro.LIZARD,
        "runner": list(retro.RUNNER), "python": "3.12", "checks_python": platform.python_version(),
        "git": "git version 1", "node": "node version 1", "tier": "push"}
    monkeypatch.setenv("ImageOS", "win22")
    monkeypatch.setenv("ImageVersion", "20260928.1")
    assert retro.env_parts("3.12")["host"] == "win22 20260928.1"


def test_the_image_tag_in_the_env_key_is_the_one_run_py_names():
    run_tool = runpy.run_path(str(REPO / "tools" / "accuracy" / "run.py"))

    assert retro._image_tag() == run_tool["image_tag"]()


def test_a_tool_s_version_is_what_it_prints_and_a_missing_tool_has_none(monkeypatch):
    asked = []
    monkeypatch.setattr(retro.shutil, "which", lambda name: None if name == "node" else f"/bin/{name}")
    monkeypatch.setattr(retro, "_run", lambda argv, cwd=None, env=None: asked.append(argv)
                        or SimpleNamespace(returncode=0, stdout="git version 2.43.0\n", stderr=""))

    assert (retro._tool_version("git"), retro._tool_version("node")) == ("git version 2.43.0", "")
    assert asked == [["/bin/git", "--version"]]


def test_a_tool_that_fails_to_say_its_version_has_none(monkeypatch):
    monkeypatch.setattr(retro.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(retro, "_run", lambda argv, cwd=None, env=None:
                        SimpleNamespace(returncode=1, stdout="1.0\n", stderr="no"))

    assert retro._tool_version("git") == ""


def test_adopt_writes_kept_verdicts_into_the_ledger_as_a_fresh_record_would(tables, capsys):
    pending = [retro._waiting(_bug_row("R1")), retro._waiting(_bug_row("R2"))]
    tables.write([_bug_row("R1")], pending)
    retro.main(["nightly"])
    tables.write([_bug_row("R1"), _bug_row("R2")], pending)
    tables.replayed.clear()
    capsys.readouterr()

    assert retro.main(["adopt"]) == 0

    assert tables.replayed == []
    adopted = retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)
    today = datetime.date.today().isoformat()
    assert capsys.readouterr().out == (
        f"retro: R1 {NODE}: before red, fix pass (kept from {today})\n"
        f"retro: adopted 1 row(s) from {retro.env_key(retro.CURRENT)}; 1 row(s) have no verdict there\n")
    assert adopted[1] == pending[1]
    tables.write([_bug_row("R1"), _bug_row("R2")], pending)
    retro.main(["run", "R1", "--record"])
    assert adopted == retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)


def test_adopt_reads_the_env_key_it_is_named(tables, capsys):
    tables.write([_bug_row("R1")], [retro._waiting(_bug_row("R1"))])
    tables.env["image"] = "ffffffffffff"
    retro.main(["nightly"])
    elsewhere = retro.env_key(retro.CURRENT)
    tables.env["image"] = "0a1b2c3d4e5f"

    assert retro.main(["adopt"]) == 0
    assert retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)[0]["before"] == "pending"
    assert retro.main(["adopt", "R1", "--env-key", elsewhere]) == 0
    assert retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)[0]["before"] == "red"
    assert tables.replayed == ["R1"]


def test_adopt_keeps_a_contradicting_verdict_pending_and_exits_one(tables, capsys):
    tables.write([_bug_row("R1")], [_ledger_row("R1")])
    tables.answers["R1"] = (retro.Outcome("green", "", "1 item(s) passed"), retro.Outcome("pass"))
    retro.main(["nightly"])
    capsys.readouterr()

    assert retro.main(["adopt"]) == 1

    (row,) = retro.read_table(tables.ledger, retro.LEDGER_COLUMNS)
    assert (row["before"], row["fix"]) == ("pending", "pending")
    assert "R1: the check passes on its before commit, so it catches nothing" in capsys.readouterr().err


THIS_OS = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def test_a_platform_only_nightly_replays_this_os_rows_with_no_verdict(tables):
    """The Windows cell replays the Windows rows; the Linux job, which replays
    every `any` row, is not run twice."""
    rows = [_bug_row("R1"), _bug_row("R2", platform=THIS_OS), _bug_row("R3", platform=THIS_OS)]
    tables.write(rows, [_ledger_row(bug) for bug in ("R1", "R2", "R3")])

    assert retro.main(["nightly", "--platform-only"]) == 0
    assert sorted(tables.replayed) == ["R2", "R3"]
    tables.replayed.clear()
    assert retro.main(["nightly", "--platform-only"]) == 0
    assert tables.replayed == []


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
    assert retro.bug_of({**row, "env": CUT}).env == CUT
    assert retro.bug_of({**row, "fix_commits": ""}).fix == ""
    assert retro.bug_of({**row, "fix_commits": "aaaa,bbbb"}).fix == "bbbb"


# --- sync: bugs.tsv follows the check names each packet landed -----------------------------------

def _sync_row(bug_id, packet, test, **fields):
    row = {**_bug_row(bug_id), "packet": packet, "test": test, "calc": f"calc {bug_id}",
           "symptom": f"symptom {bug_id}"}
    return {**row, **fields}


BUGS_BEFORE = [
    _sync_row("R1", "p1", "tests/accuracy/p1/test_a.py::test_proposed"),
    _sync_row("R1", "p2", "tests/accuracy/p2/test_b.py::test_kept", calc="calc R1 in p2"),
    _sync_row("R2", "p1", "tests/accuracy/p1/test_a.py::test_probe", probe="R2.py", method="model",
              env=CUT),
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
    assert [(row["packet"], row["calc"], row["platform"], row["probe"], row["env"])
            for row in moved] == [("p1", "calc R3", "any", "", "")]


def test_a_packet_s_platform_all_syncs_as_any_and_replays_everywhere(tmp_path):
    table = "id\ttest\tplatform\nR3\ttests/accuracy/p1/test_a.py::test_everywhere\tall\n"

    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path, table)))

    row = next(row for row in synced if row["test"].endswith("test_everywhere"))
    assert row["platform"] == "any"
    assert retro.PLATFORMS[row["platform"]] == ""


def test_a_pair_listed_once_per_fix_commit_syncs_as_one_row(tmp_path):
    """A packet may list a bug's check once for each of its fix commits; the ledger keys
    on (id, test), so bugs.tsv holds the pair once, with the first listing's platform."""
    table = ("id\tfix_commit\ttest\tplatform\n"
             "R3\taaa\ttests/accuracy/p1/test_a.py::test_both\twindows\n"
             "R3\tbbb\ttests/accuracy/p1/test_a.py::test_both\tlinux\n")

    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path, table)))

    both = [row for row in synced if row["test"].endswith("test_both")]
    assert [(row["id"], row["platform"]) for row in both] == [("R3", "windows")]


def test_sync_carries_the_env_a_packet_lists(tmp_path):
    """A packet's retro.tsv may carry an env column; a packet without one leaves the
    env bugs.tsv holds for the pair (R2's above)."""
    table = ("id\ttest\tenv\n"
             "R3\ttests/accuracy/p1/test_a.py::test_cut\tCRAPKIT_ACCURACY_LANGUAGES=python\n")

    synced = retro.synced_bugs(BUGS_BEFORE, retro.landed(_landed(tmp_path, table)))

    cut = next(row for row in synced if row["test"].endswith("test_cut"))
    assert cut["env"] == "CRAPKIT_ACCURACY_LANGUAGES=python"


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


def test_sync_keeps_the_proposed_row_of_a_bug_no_landed_packet_lists():
    """A triaged calc fix keeps a bugs.tsv row, pending, until some packet lands a check
    for it: a packet that leaves the bug out of its retro.tsv does not erase it."""
    tables = {"p1": [{"id": "R1", "test": "tests/accuracy/p1/test_a.py::test_confirmed"}],
              "p2": [{"id": "R2", "test": "tests/accuracy/p2/test_b.py::test_moved_here"}]}

    synced = retro.synced_bugs(BUGS_BEFORE, tables)

    assert sorted((row["id"], row["packet"], row["test"]) for row in synced) == [
        ("R1", "p1", "tests/accuracy/p1/test_a.py::test_confirmed"),
        ("R2", "p2", "tests/accuracy/p2/test_b.py::test_moved_here"),
        ("R3", "p3", "tests/accuracy/p3/test_c.py::test_not_landed"),
        ("R4", "p1", "tests/accuracy/p1/test_a.py::test_open")]


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


def test_the_digest_orders_paths_with_their_case_on_every_platform(small_tree):
    """Windows orders paths without case and Linux with it, so a check whose packet
    data held probes/swift/Accessors.swift beside probes/swift/probes.tsv had one
    digest after a Windows replay and another on Linux: the Linux retro job read
    every such row as stale and replayed it each night."""
    mixed = ["tests/accuracy/pkt/fixtures/Beta.txt", "tests/accuracy/pkt/fixtures/alpha.txt"]
    for name in mixed:
        (small_tree / name).write_text("x\n", encoding="utf-8")

    assert retro.digest(TEST, repo=small_tree) == _expected_digest(small_tree, READS + mixed)


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


# --- the replay's plumbing, in-process ----------------------------------------------------------------
#
# The planted repo above drives worktrees, venvs and the pytest child end to
# end, nightly. These tests pin each piece on its own, with the commands it
# hands git and uv recorded. The few that start a Python child run nightly too;
# the tools mutation run counts every tier, so each still kills its mutants.

def _commands(monkeypatch) -> list[tuple[list[str], Path | None]]:
    """Stand in for _checked: record each (argv, cwd) and answer with no output."""
    seen = []

    def checked(argv, cwd=None):
        seen.append(([str(part) for part in argv], cwd))
        return ""

    monkeypatch.setattr(retro, "_checked", checked)
    return seen


@pytest.mark.nightly
def test_a_run_passes_each_argument_as_text_and_decodes_what_the_child_wrote(tmp_path):
    code = ("import sys; sys.stdout.buffer.write(repr(sys.argv[1:]).encode() + b'\\xff');"
            " sys.stderr.buffer.write(b'e\\xff'); sys.exit(4)")

    done = retro._run([Path(sys.executable), "-c", code, Path("a")], cwd=tmp_path)

    assert (done.returncode, done.stdout, done.stderr) == (4, "['a']�", "e�")


@pytest.mark.nightly
def test_a_run_runs_in_its_folder_with_its_environment(tmp_path):
    code = "import os, sys; sys.stdout.write(os.getcwd() + '|' + os.environ['RETRO_PROBE'])"

    done = retro._run([sys.executable, "-c", code], cwd=tmp_path,
                      env={**retro.os.environ, "RETRO_PROBE": "kept"})

    assert done.stdout == f"{tmp_path}|kept"


@pytest.mark.nightly
def test_a_checked_command_answers_its_output(tmp_path):
    assert retro._checked([sys.executable, "-c", "import sys; sys.stdout.write('ok')"], tmp_path) == "ok"


@pytest.mark.nightly
def test_a_failed_command_names_its_argv_and_the_last_400_characters_it_said(tmp_path):
    argv = [Path(sys.executable), "-c", "import sys; sys.stderr.write('d' + 'e' * 500 + '  '); sys.exit(2)"]

    with pytest.raises(retro.RetroError) as refused:
        retro._checked(argv, tmp_path)

    assert str(refused.value) == f"{' '.join(map(str, argv))}: " + "e" * 400


@pytest.mark.parametrize("code, held", [(0, True), (128, False)])
def test_a_commit_is_here_when_git_finds_a_commit_object_by_it(tmp_path, monkeypatch, code, held):
    asked = []
    monkeypatch.setattr(retro, "_run", lambda argv, cwd=None, env=None: asked.append((argv, cwd))
                        or SimpleNamespace(returncode=code))

    assert retro.have_commit("abc", tmp_path) is held
    assert asked == [(["git", "cat-file", "-e", "abc^{commit}"], tmp_path)]


def test_a_missing_commit_is_fetched_from_the_bundle_into_refs_of_its_own(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)
    asked = []
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: asked.append((sha, repo)) or True)
    monkeypatch.setenv(retro.BUNDLE_ENV, "history.bundle")

    assert retro.fetch_bundle("abc", tmp_path) is None

    assert seen == [(["git", "fetch", "-q", "history.bundle", "+refs/*:refs/retro-bundle/*"], tmp_path)]
    assert asked == [("abc", tmp_path)]


def test_a_commit_the_bundle_does_not_hold_either_is_refused(tmp_path, monkeypatch):
    _commands(monkeypatch)
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: False)
    monkeypatch.setenv(retro.BUNDLE_ENV, "history.bundle")

    with pytest.raises(retro.RetroError) as refused:
        retro.fetch_bundle("abc", tmp_path)

    assert str(refused.value) == "abc is in neither this clone nor history.bundle"


@pytest.mark.parametrize("bundle", [None, ""])
def test_a_missing_commit_with_no_bundle_names_the_variable_to_set(tmp_path, monkeypatch, bundle):
    seen = _commands(monkeypatch)
    monkeypatch.delenv(retro.BUNDLE_ENV, raising=False)
    if bundle is not None:
        monkeypatch.setenv(retro.BUNDLE_ENV, bundle)

    with pytest.raises(retro.RetroError) as refused:
        retro.fetch_bundle("abc", tmp_path)

    assert str(refused.value) == f"abc is not in this clone; set {retro.BUNDLE_ENV} to the history bundle"
    assert seen == []


SHA = "0123456789abcdef" * 2 + "01234567"


def test_a_worktree_is_added_detached_at_the_commit_under_its_short_sha(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: True)
    monkeypatch.setattr(retro, "fetch_bundle", lambda sha, repo: pytest.fail("fetched"))
    site = retro.Site(repo=tmp_path / "repo", work=tmp_path / "a" / "work")

    tree = retro.worktree(SHA, site)

    assert tree == tmp_path / "a" / "work" / "0123456789ab"
    assert site.work.is_dir()
    assert seen == [(["git", "worktree", "add", "-f", "--detach", str(tree), SHA], tmp_path / "repo")]


def test_a_commit_missing_from_the_clone_is_fetched_before_its_worktree_is_added(tmp_path, monkeypatch):
    _commands(monkeypatch)
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: False)
    fetched = []
    monkeypatch.setattr(retro, "fetch_bundle", lambda sha, repo: fetched.append((sha, repo)))

    retro.worktree(SHA, retro.Site(repo=tmp_path / "repo", work=tmp_path / "work"))

    assert fetched == [(SHA, tmp_path / "repo")]


def _cached_tree(work: Path) -> Path:
    """A worktree folder a cache restored, its venv and another commit's venv beside it."""
    tree = work / "0123456789ab"
    for folder in (tree, work / "0123456789ab-venv-3.12-wheel", work / "fedcba987654-venv-3.12-wheel"):
        folder.mkdir(parents=True)
        (folder / "kept").write_bytes(b"")
    (tree / ".git").write_bytes(b"gitdir: /src/.git/worktrees/0123456789ab\n")
    return tree


def _git_answers(monkeypatch, head: SimpleNamespace) -> list[list[str]]:
    """Stand in for _run: `git worktree repair` succeeds and rev-parse answers `head`."""
    asked = []

    def run(argv, cwd=None, env=None):
        asked.append([str(part) for part in argv])
        return head if "rev-parse" in argv else SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(retro, "_run", run)
    return asked


def test_a_cached_worktree_git_puts_at_its_commit_is_reused_with_its_venv(tmp_path, monkeypatch):
    """A restored CRAPKIT_RETRO_WORK saves the worktree and venv builds (85 s a row
    cold in the image, 10 s warm), but only for a tree git says is at its commit."""
    seen = _commands(monkeypatch)
    tree = _cached_tree(tmp_path / "work")
    asked = _git_answers(monkeypatch, SimpleNamespace(returncode=0, stdout=SHA + "\n", stderr=""))

    assert retro.worktree(SHA, retro.Site(repo=tmp_path, work=tmp_path / "work")) == tree

    assert asked == [["git", "worktree", "repair", str(tree)], ["git", "-C", str(tree), "rev-parse", "HEAD"]]
    assert seen == []
    assert (tree / "kept").is_file() and (tree.parent / "0123456789ab-venv-3.12-wheel" / "kept").is_file()


@pytest.mark.parametrize("head", [
    SimpleNamespace(returncode=128, stdout="", stderr="fatal: not a git repository"),  # dangling gitdir
    SimpleNamespace(returncode=0, stdout="f" * 40 + "\n", stderr=""),                # another commit
])
def test_a_cached_worktree_that_is_dangling_or_elsewhere_is_rebuilt_with_its_venvs(tmp_path, monkeypatch,
                                                                                   head):
    """A fresh clone holds no .git/worktrees entry for a restored tree, so git cannot
    say which commit it holds: the tree and every venv built from it go, and the
    tree is added again. Another commit's venv stays."""
    seen = _commands(monkeypatch)
    tree = _cached_tree(tmp_path / "work")
    _git_answers(monkeypatch, head)
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: True)

    assert retro.worktree(SHA, retro.Site(repo=tmp_path, work=tmp_path / "work")) == tree

    assert seen == [(["git", "worktree", "add", "-f", "--detach", str(tree), SHA], tmp_path)]
    assert not tree.exists() and not (tree.parent / "0123456789ab-venv-3.12-wheel").exists()
    assert (tree.parent / "fedcba987654-venv-3.12-wheel" / "kept").is_file()


def test_a_venv_left_without_its_worktree_is_removed_before_the_tree_is_added(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)
    venv = tmp_path / "0123456789ab-venv-3.12-wheel"
    venv.mkdir()
    monkeypatch.setattr(retro, "have_commit", lambda sha, repo: True)

    retro.worktree(SHA, retro.Site(repo=tmp_path / "repo", work=tmp_path))

    assert not venv.exists()
    assert len(seen) == 1


@pytest.mark.parametrize("windows, expected", [(True, "Scripts/python.exe"), (False, "bin/python")])
def test_a_venv_s_interpreter_sits_where_the_os_puts_it(tmp_path, monkeypatch, windows, expected):
    monkeypatch.setattr(retro, "WINDOWS", windows)

    assert retro.venv_python(tmp_path) == tmp_path / expected


def test_another_python_s_venv_is_made_by_uv(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)

    retro._create_venv(tmp_path / "v", "3.99")

    assert seen == [(["uv", "venv", "-q", "--python", "3.99", str(tmp_path / "v")], None)]


def test_this_python_s_venv_is_made_by_the_standard_library_with_no_pip(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)

    retro._create_venv(tmp_path / "v", retro.CURRENT)

    interpreter = retro.venv_python(tmp_path / "v")
    assert seen == []
    assert interpreter.is_file() and interpreter.is_symlink() is not retro.WINDOWS
    assert not list((tmp_path / "v").rglob("pip"))


@pytest.mark.parametrize("how, tail", [("editable", ["-e", "TREE"]), ("wheel", ["TREE"])])
def test_a_commit_goes_in_editable_or_as_a_wheel_without_its_dependencies(tmp_path, monkeypatch, how, tail):
    seen = _commands(monkeypatch)

    retro._install(Path("py"), tmp_path, how)

    assert seen == [(["uv", "pip", "install", "-q", "--python", "py", "--no-deps",
                      *[str(tmp_path) if part == "TREE" else part for part in tail]], None)]


def test_a_linked_commit_is_one_pth_line_naming_its_src(tmp_path, monkeypatch):
    seen = _commands(monkeypatch)
    monkeypatch.setattr(retro, "_purelib", lambda interpreter: tmp_path)

    retro._install(Path("py"), tmp_path / "tree", "link")

    assert (tmp_path / "retro-src.pth").read_bytes() == (str(tmp_path / "tree" / "src") + "\n").encode()
    assert seen == []


@pytest.mark.nightly
def test_a_venv_s_purelib_is_what_its_interpreter_says():
    assert retro._purelib(Path(sys.executable)) == Path(sysconfig.get_path("purelib"))


def test_packages_go_in_by_uv_and_no_packages_start_nothing(monkeypatch):
    seen = _commands(monkeypatch)

    retro._packages(Path("py"), [])
    retro._packages(Path("py"), ["pytest==9.1.1", "coverage==7.16.1"])

    assert seen == [(["uv", "pip", "install", "-q", "--python", "py", "pytest==9.1.1",
                      "coverage==7.16.1"], None)]


def test_a_venv_is_built_beside_its_tree_with_the_site_s_packages_and_the_extras(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(retro, "_create_venv", lambda venv, python: calls.append(("venv", venv, python)))
    monkeypatch.setattr(retro, "_install", lambda interpreter, tree, how: calls.append(
        ("install", interpreter, tree, how)))
    monkeypatch.setattr(retro, "_packages", lambda interpreter, packages: calls.append(
        ("packages", interpreter, packages)))
    site = retro.Site(repo=tmp_path, work=tmp_path, install="editable", packages=("a==1",))

    interpreter = retro.build_venv(tmp_path / "abc", "3.12", site, ("b==2",))

    venv = tmp_path / "abc-venv-3.12-editable"
    assert interpreter == retro.venv_python(venv)
    assert calls == [("venv", venv, "3.12"), ("install", interpreter, tmp_path / "abc", "editable"),
                     ("packages", interpreter, ["a==1", "b==2"])]


def test_a_venv_already_built_is_reused(tmp_path, monkeypatch):
    venv = tmp_path / "abc-venv-3.12-wheel"
    retro.venv_python(venv).parent.mkdir(parents=True)
    retro.venv_python(venv).write_bytes(b"")
    (venv / retro.BUILT).write_bytes("".join(f"{package}\n" for package in retro.Site().packages).encode())
    monkeypatch.setattr(retro, "_create_venv", lambda *args: pytest.fail("built again"))

    assert retro.build_venv(tmp_path / "abc", "3.12", retro.Site(work=tmp_path)) == retro.venv_python(venv)


def _pinned(name: str) -> str:
    """name==version as tools/accuracy/requirements-push.txt pins it."""
    text = (REPO / "tools" / "accuracy" / "requirements-push.txt").read_bytes().decode()
    (line,) = [line for line in text.splitlines() if line.startswith(f"{name}==")]
    return line.split()[0]


def test_a_replay_venv_holds_the_test_runner_the_accuracy_suite_pins():
    """Checks run crapkit in-process in the dev venv, where doctor's coverage probe and
    test-scoped's pytest find their runner; a replay venv must hold the same runner, or
    those checks fail on both commits for a reason no bug explains."""
    assert retro.Site().packages == (f"lizard=={retro.LIZARD}", _pinned("pytest"), _pinned("pytest-cov"),
                                      _pinned("coverage"))


def test_a_venv_built_with_other_packages_is_built_again(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(retro, "_create_venv", lambda venv, python: built.append(venv) or
                        venv.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(retro, "_install", lambda interpreter, tree, how: None)
    monkeypatch.setattr(retro, "_packages", lambda interpreter, packages: None)
    old, new = retro.Site(work=tmp_path, packages=("lizard==1",)), retro.Site(work=tmp_path)

    retro.build_venv(tmp_path / "abc", "3.12", old)
    retro.build_venv(tmp_path / "abc", "3.12", old)
    retro.build_venv(tmp_path / "abc", "3.12", new)

    assert len(built) == 2


def test_a_venv_whose_install_failed_is_built_again(tmp_path, monkeypatch):
    """A replay killed or refused mid-install leaves an interpreter with no
    crapkit or no lizard in it; the next replay must not take that venv as built."""
    built = []

    def create(venv, python):
        assert not venv.exists(), "the half-built venv was not removed first"
        retro.venv_python(venv).parent.mkdir(parents=True)
        retro.venv_python(venv).write_bytes(b"")
        built.append(venv)

    def install_once(interpreter, tree, how):
        if len(built) == 1:
            raise retro.RetroError("uv pip install: interrupted")

    monkeypatch.setattr(retro, "_create_venv", create)
    monkeypatch.setattr(retro, "_install", install_once)
    monkeypatch.setattr(retro, "_packages", lambda interpreter, packages: None)
    site = retro.Site(work=tmp_path)

    with pytest.raises(retro.RetroError):
        retro.build_venv(tmp_path / "abc", "3.12", site)
    interpreter = retro.build_venv(tmp_path / "abc", "3.12", site)

    assert len(built) == 2
    assert (interpreter.parents[1] / retro.BUILT).is_file()


# --- the pytest child ------------------------------------------------------------------------------

def test_the_child_s_environment_names_the_venv_the_outcomes_and_this_tree(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setenv("RETRO_PROBE", "kept")

    env = retro._pytest_env(Path("venv-python"), Path("out.jsonl"))

    assert {key: env[key] for key in (retro.PYTHON_ENV, retro.OUTCOMES_ENV, retro.COLLECT_ALL_ENV,
                                      "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "RETRO_PROBE")} == {
        retro.PYTHON_ENV: "venv-python", retro.OUTCOMES_ENV: "out.jsonl",
        retro.COLLECT_ALL_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1", "RETRO_PROBE": "kept",
        "PYTHONPATH": retro.os.pathsep.join([str(REPO / "tests"), str(REPO / "tools" / "accuracy")])}


def test_a_check_in_this_tree_runs_from_its_root_and_one_elsewhere_from_its_folder(tmp_path):
    outside = tmp_path / "checks" / "test_c.py"

    assert retro._rootdir("tests/accuracy/suite_strength/test_retro_tool.py::t[a/b]") == REPO
    assert retro._rootdir(".") == REPO
    assert retro._rootdir(f"{outside.as_posix()}::t[a/b]") == outside.parent.resolve()


def _child(monkeypatch, written: bytes, code: int = 1) -> dict:
    """Stand in for the pytest child: record what it was handed, write `written` as
    its outcomes and end with `code`."""
    seen = {}

    def run(argv, cwd=None, env=None):
        outcomes = Path(env[retro.OUTCOMES_ENV])
        seen.update(argv=[str(part) for part in argv], cwd=cwd, env=env, outcomes=outcomes,
                    touched=outcomes.is_file())
        with open(outcomes, "ab") as handle:
            handle.write(written)
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(retro, "_run", run)
    monkeypatch.setattr(retro, "crapkit_root", lambda interpreter: f"root-of-{interpreter}")
    return seen


def test_a_replayed_node_runs_pytest_with_the_plugin_and_reads_what_it_wrote(monkeypatch):
    seen = _child(monkeypatch, b'{"nodeid": "t::a", "outcome": "failed", "exc_type": "KeyError", '
                               b'"assertion": false, "message": "k \xc3\xa9"}\r\n')

    records = retro.replay_node(NODE, Path("venv-python"))

    assert seen["argv"] == [sys.executable, "-m", "pytest", NODE, "-q", "-p", "no:cacheprovider",
                            "-p", "no:randomly", "-p", "retro", "--rootdir", str(REPO)]
    assert (seen["cwd"], seen["env"][retro.PYTHON_ENV]) == (None, "venv-python")
    assert seen["env"]["PYTHONPATH"].split(retro.os.pathsep)[0] == "root-of-venv-python"
    assert seen["outcomes"].name == "outcomes.jsonl" and seen["touched"]
    assert seen["outcomes"].parent.name.startswith("crapkit-retro-")
    assert not seen["outcomes"].exists()
    assert records == [{"nodeid": "t::a", "outcome": "failed", "exc_type": "KeyError",
                        "assertion": False, "message": "k é"}]


def test_a_child_that_collected_nothing_leaves_no_record(monkeypatch):
    _child(monkeypatch, b"", code=5)

    assert retro.replay_node(NODE, Path("venv-python")) == []


PASSED_SETUP = b'{"nodeid": "t::a", "outcome": "passed", "exc_type": "", "assertion": false, "message": ""}\n'


@pytest.mark.parametrize("code, ending", [(1, "exit 1"), (-11, "signal 11"), (2, "exit 2")])
def test_a_child_that_ended_before_it_reported_every_item_fails_the_replay(monkeypatch, code,
                                                                        ending):
    """A crapkit call stuck in C code ends the child with exit 1 mid-item
    (tests/e2e/cli_in_process.py), and a crash ends it by a signal: the item's
    passing setup was its only record, and the replay read as a pass."""
    _child(monkeypatch, PASSED_SETUP, code)

    records = retro.replay_node(NODE, Path("venv-python"))

    assert retro.classify_fix(records).verdict == "fail"
    assert retro.classify_before(records).verdict == "not replayable"
    assert f"pytest ended with {ending} before it reported every item" in (
        retro.classify_fix(records).evidence)


class _Report:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_the_plugin_appends_each_call_and_each_failing_phase(tmp_path, monkeypatch):
    out = tmp_path / "out.jsonl"
    out.write_bytes(b"kept\n")
    monkeypatch.setenv(retro.OUTCOMES_ENV, str(out))

    for nodeid, call in (("t::setup", _call(None, "setup")), ("t::a", _call(None)),
                         ("t::é", _call(OSError("lock"), "teardown"))):
        assert retro.pytest_runtest_makereport(_Report(nodeid), call) is None
        retro.pytest_runtest_logreport(SimpleNamespace(nodeid=nodeid, when=call.when))

    kept, *lines = out.read_bytes().decode().split("\n")
    assert kept == "kept" and lines[-1] == ""
    assert [json.loads(line) for line in lines[:-1]] == [
        retro._record("t::a", _call(None)), retro._record("t::é", _call(OSError("lock")))]


def test_the_plugin_writes_nothing_outside_a_replay(tmp_path, monkeypatch):
    monkeypatch.delenv(retro.OUTCOMES_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    assert retro.pytest_runtest_makereport(_Report("t::a"), _call(None)) is None
    retro.pytest_runtest_logreport(SimpleNamespace(nodeid="t::a", when="call"))
    assert list(tmp_path.iterdir()) == []


# --- the probe ---------------------------------------------------------------------------------------

def _file(tmp_path: Path, body: str, name: str = "RX.py") -> Path:
    path = tmp_path / name
    path.write_bytes(body.encode())
    return path


def test_a_probe_s_header_names_its_packages_and_its_install(tmp_path):
    header = _file(tmp_path, "# requires: a==1 b@file:///w/b.whl\n# install: editable \n"
                             "# requires: c==3\nx = 1\n")
    late = _file(tmp_path, "x = 1\n" * 20 + "# requires: late==1\n# install: editable\n", "late.py")
    wheel = _file(tmp_path, "#  requires: no==1\n# install: wheel\n", "wheel.py")

    assert retro.probe_header(header) == (["a==1", "b@file:///w/b.whl", "c==3"], True)
    assert retro.probe_header(late) == ([], False)
    assert retro.probe_header(wheel) == ([], False)


LONG = "x" * 400


@pytest.mark.nightly
@pytest.mark.parametrize("body, record", [
    ("import sys\nassert sys.argv[1]\n",
     {"outcome": "passed", "exc_type": "", "assertion": False, "message": ""}),
    ("assert 1 == 2, 'wrong:  value'\n",
     {"outcome": "failed", "exc_type": "AssertionError", "assertion": True,
      "message": "AssertionError: wrong: value"}),
    (f"assert False, '{LONG}'\n",
     {"outcome": "failed", "exc_type": "AssertionError", "assertion": True,
      "message": ("AssertionError: " + LONG)[:300]}),
    ("import http.client\nraise http.client.InvalidURL('a.b: c')\n",
     {"outcome": "failed", "exc_type": "InvalidURL", "assertion": False,
      "message": "http.client.InvalidURL: a.b: c"}),
    ("import sys\nsys.stderr.write('one\\ntwo\\n')\nsys.exit(3)\n",
     {"outcome": "failed", "exc_type": "two", "assertion": False, "message": "two"}),
    ("import sys\nsys.exit(3)\n",
     {"outcome": "failed", "exc_type": "(no output)", "assertion": False, "message": "(no output)"}),
], ids=["passes", "assertion", "long", "dotted", "last-line", "silent"])
def test_a_probe_passes_on_exit_zero_and_fails_on_its_last_stderr_line(tmp_path, body, record):
    probe = _file(tmp_path, body)

    assert retro.replay_probe(probe, Path(sys.executable), tmp_path) == [{"nodeid": "RX.py", **record}]


@pytest.mark.nightly
def test_a_probe_runs_in_the_tree_without_this_tree_s_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    monkeypatch.setenv("RETRO_PROBE", "kept")
    tree = tmp_path / "tree"
    tree.mkdir()
    probe = _file(tmp_path, "import os, sys\nfrom pathlib import Path\n"
                            "assert 'PYTHONPATH' not in os.environ\n"
                            "assert os.environ['RETRO_PROBE'] == 'kept'\n"
                            "assert Path.cwd().resolve() == Path(sys.argv[1]).resolve()\n")

    assert retro.replay_probe(probe, Path(sys.executable), tree)[0]["outcome"] == "passed"


@pytest.mark.parametrize("header, install, extra", [
    (b"# requires: a==1 b==2\n# install: editable\n", "editable", ("a==1", "b==2")),
    (b"# requires: a==1\n", "link", ("a==1",)),
])
def test_a_probe_s_venv_takes_the_packages_and_install_its_header_names(tmp_path, monkeypatch, header,
                                                                         install, extra):
    probe = tmp_path / "R5.py"
    probe.write_bytes(header)
    built = []
    monkeypatch.setattr(retro, "build_venv", lambda tree, python, site, extra=(): built.append(
        (tree, python, site, extra)) or Path("venv-python"))
    monkeypatch.setattr(retro, "replay_probe", lambda probe, interpreter, tree: [(probe, interpreter, tree)])
    site = retro.Site(repo=tmp_path, work=tmp_path, install="link")

    records = retro._probe_records(probe, tmp_path / "tree", "3.12", site)

    assert records == [(probe, Path("venv-python"), tmp_path / "tree")]
    assert built == [(tmp_path / "tree", "3.12", replace(site, install=install), extra)]


def test_a_probe_bug_replays_its_probe_file_in_the_commit_s_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(retro, "worktree", lambda sha, site: tmp_path / sha)
    monkeypatch.setattr(retro, "_probe_records", lambda probe, tree, python, site: [
        (probe, tree, python, site)])
    site = retro.Site(work=tmp_path)

    got = retro._records(retro.Bug("R5", NODE, "a", "b", "R97.py"), "abc", "3.12", site)

    assert got == [(retro.RETRO / "probes" / "R97.py", tmp_path / "abc", "3.12", site)]


def test_a_node_bug_replays_its_check_against_the_commit_s_venv_under_its_env(tmp_path, monkeypatch):
    monkeypatch.setattr(retro, "worktree", lambda sha, site: tmp_path / sha)
    monkeypatch.setattr(retro, "build_venv", lambda tree, python, site, extra=(): (tree, python, site, extra))
    monkeypatch.setattr(retro, "replay_node",
                        lambda test, interpreter, env, tree: [(test, interpreter, env, tree)])
    site = retro.Site(work=tmp_path)

    got = retro._records(retro.Bug("R6", NODE, "a", "b", env=CUT), "abc", "3.12", site)

    assert got == [(NODE, (tmp_path / "abc", "3.12", site, ()), CUT, tmp_path / "abc")]


# --- tables, rows and the ledger ----------------------------------------------------------------------

def test_a_table_with_an_empty_file_or_a_stray_tab_in_its_header_is_refused(tmp_path):
    empty = _file(tmp_path, "", "empty.tsv")
    stray = _file(tmp_path, "id\ttest\t\nR1\tt\t\n", "stray.tsv")

    for path in (empty, stray):
        with pytest.raises(retro.RetroError) as refused:
            retro.read_table(path, ("id", "test"))
        assert str(refused.value) == f"{path}: the header must be id test (tab-separated)"


def test_a_table_reads_its_rows_past_blank_lines_and_a_short_row_names_its_line(tmp_path):
    good = _file(tmp_path, "id\ttest\n\nR1\tt[a b]\n", "good.tsv")
    short = _file(tmp_path, "id\ttest\nR1\tt\nR2\n", "short.tsv")

    assert retro.read_table(good, ("id", "test")) == [{"id": "R1", "test": "t[a b]"}]
    assert retro.read_table(tmp_path / "missing.tsv", ("id",)) == []
    with pytest.raises(retro.RetroError) as refused:
        retro.read_table(short, ("id", "test"))
    assert str(refused.value) == f"{short}:3: 1 cells, the header has 2"


def test_a_written_table_is_lf_utf8_and_flattens_tabs_and_runs_of_space(tmp_path):
    path = tmp_path / "t.tsv"

    retro.write_table(path, ("id", "note"), [{"id": "R1", "note": "a\tb   c\nd é"}])

    assert path.read_bytes() == "id\tnote\nR1\ta b c d é\n".encode()


def test_a_landed_table_keeps_a_test_id_that_holds_a_space(tmp_path):
    table = "id\ttest\tplatform\nR1\ttests/x.py::t[a b]\tany\n"

    assert retro.landed(_landed(tmp_path, table)) == {
        "p1": [{"id": "R1", "test": "tests/x.py::t[a b]", "platform": "any"}]}


def test_the_waiting_rows_say_open_or_pending_and_nothing_else():
    common = {"test": NODE, "before_commit": "a" * 12, "fix_commit": "b" * 12, "lizard": "",
              "failure_class": "", "before_evidence": "", "fix_evidence": "", "digest": "",
              "replayed": ""}

    assert retro._waiting(_bug_row("R1", replay="open")) == {
        "id": "R1", **common, "before": "open", "fix": "open",
        "note": "branch not merged: strict xfail until it lands"}
    assert retro._waiting(_bug_row("R2")) == {
        "id": "R2", **common, "before": "pending", "fix": "pending", "note": retro.PENDING_NOTE}


# A check outside this packet: its digest does not already hold the probe as packet data.
OTHER = "tests/accuracy/kit/test_kit_docrange.py::t"


def test_a_probe_moves_the_digest_of_a_check_outside_its_packet():
    assert retro.digest(OTHER, "R97.py") != retro.digest(OTHER)


def test_a_ledger_row_records_today_the_verdicts_and_the_probe_s_digest():
    bug = retro.Bug("R1", OTHER, "a" * 12, "b" * 12, "R97.py")

    row = retro.ledger_row(bug, retro.Outcome("red", "AssertionError", "wrong"),
                           retro.Outcome("pass", "", "1 item(s) passed"))

    assert row == {"id": "R1", "test": OTHER, "before_commit": "a" * 12, "fix_commit": "b" * 12,
                   "lizard": retro.LIZARD, "before": "red", "failure_class": "AssertionError",
                   "before_evidence": "wrong", "fix": "pass", "fix_evidence": "1 item(s) passed",
                   "digest": retro.digest(OTHER, "R97.py"),
                   "replayed": datetime.date.today().isoformat(), "note": ""}


def test_a_probe_row_is_stale_until_its_digest_covers_the_probe():
    row = {**_bug_row("R1"), "test": OTHER, "probe": "R97.py"}
    recorded = {**_ledger_row("R1"), "test": OTHER, "digest": retro.digest(OTHER)}

    assert retro._is_stale(row, {retro.row_key(row): recorded})
    assert not retro._is_stale(row, {retro.row_key(row): {**recorded, "digest": retro.digest(OTHER, "R97.py")}})


def test_a_row_whose_platform_is_a_raw_name_runs_only_there():
    row = {**_bug_row("R1"), "platform": "darwin"}

    assert (retro.replayable_here(row, "linux"), retro.replayable_here(row, "darwin23")) == (False, True)


def test_the_public_rows_leave_out_the_open_and_the_bundle_ones():
    rows = [_bug_row("R1", replay="open"), _bug_row("R2", replay="bundle"), _bug_row("R3")]

    assert retro._public(rows) == [rows[2]]


def test_rows_keep_the_order_bugs_tsv_gave_them_within_a_bug():
    bugs = [{**_bug_row("R1"), "test": "t2"}, {**_bug_row("R1"), "test": "t1"},
            {**_bug_row("R2"), "test": "t0"}]
    new = {**_bug_row("R1"), "test": "t0"}

    assert sorted([bugs[2], new, *bugs[1::-1]], key=retro._placed(bugs)) == [*bugs[:2], new, bugs[2]]


def test_a_bug_new_to_a_packet_s_test_carries_no_probe_from_another_test():
    bugs = [{**_bug_row("R1"), "packet": "p1", "test": "t_old", "probe": "R1.py"}]

    fresh = retro._bug_for(bugs, "p1", {"id": "R1", "test": "t_new"})

    assert (fresh["test"], fresh["probe"], fresh["platform"]) == ("t_new", "", "any")


def test_sync_names_the_packet_and_the_bug_bugs_tsv_does_not_know():
    with pytest.raises(retro.RetroError) as refused:
        retro._template([], "p1", "R9")

    assert str(refused.value) == ("p1/retro.tsv names R9, which bugs.tsv has no row for: "
                                  "triage the commit that fixed it first")


# --- the commands hand their python on and only `run --record` writes -----------------------------

def _replays(monkeypatch, before: str = "red") -> list[str]:
    pythons = []

    def replay(bug, python, site=None):
        pythons.append(python)
        return retro.Outcome(before, "AssertionError", "wrong"), retro.Outcome("pass", "", "ok")

    monkeypatch.setattr(retro, "replay", replay)
    return pythons


@pytest.mark.parametrize("argv", [["run", "R1"], ["nightly"], ["release"]])
def test_every_replaying_command_hands_on_its_python_and_leaves_the_ledger(tables, monkeypatch, argv):
    tables.write([_bug_row("R1")], [_ledger_row("R1", digest="stale")])
    before = tables.ledger.read_bytes()
    pythons = _replays(monkeypatch)

    assert retro.main(["--python", "3.99", *argv]) == 0

    assert pythons == ["3.99"]
    assert tables.ledger.read_bytes() == before


def test_a_refused_replay_leaves_the_row_pending_with_the_refusal_in_its_note(monkeypatch, capsys):
    """The plan: a check that passes on its before commit is refused, and the fix must
    pass. A refused replay is not evidence, so --record keeps the row pending."""
    _replays(monkeypatch, before="green")

    problem, row = retro._replay_one(_bug_row("R1"), {}, "3.99")

    assert problem == "R1: the check passes on its before commit, so it catches nothing"
    assert (row["before"], row["fix"], row["digest"]) == ("pending", "pending", "")
    assert row["note"] == f"refused {datetime.date.today().isoformat()}: {problem}"
    assert capsys.readouterr().out == f"retro: R1 {NODE}: before green, fix pass\n"


def test_a_not_replayable_before_is_recorded_with_the_probe_it_needs(monkeypatch):
    _replays(monkeypatch, before="not replayable")

    problem, row = retro._replay_one(_bug_row("R1"), {}, "3.99")

    assert problem == ""
    assert (row["before"], row["fix"]) == ("not replayable", "pass")
    assert row["note"] == retro.PROBE_NOTE


def test_an_accepted_red_replay_is_recorded_with_no_note(monkeypatch):
    _replays(monkeypatch, before="red")

    problem, row = retro._replay_one(_bug_row("R1"), {}, "3.99")

    assert (problem, row["before"], row["fix"], row["note"]) == ("", "red", "pass", "")


def test_digest_with_a_probe_prints_the_digest_that_covers_it(capsys):
    assert retro.main(["digest", OTHER, "--probe", "R97.py"]) == 0

    assert capsys.readouterr().out == retro.digest(OTHER, "R97.py") + "\n"


@pytest.mark.parametrize("argv, expected", [
    (["run", "R1", "R2", "--record"],
     {"python": retro.CURRENT, "command": "run", "ids": ["R1", "R2"], "record": True}),
    (["--python", "3.13", "run", "R1"],
     {"python": "3.13", "command": "run", "ids": ["R1"], "record": False}),
    (["nightly"], {"python": retro.CURRENT, "command": "nightly", "platform_only": False}),
    (["nightly", "--platform-only"], {"python": retro.CURRENT, "command": "nightly", "platform_only": True}),
    (["adopt"], {"python": retro.CURRENT, "command": "adopt", "ids": [], "env_key": ""}),
    (["adopt", "R1", "R2", "--env-key", "linux-3.12-push-0a1b2c3d4e5f"],
     {"python": retro.CURRENT, "command": "adopt", "ids": ["R1", "R2"],
      "env_key": "linux-3.12-push-0a1b2c3d4e5f"}),
    (["release"], {"python": retro.CURRENT, "command": "release"}),
    (["digest", "t::x", "--probe", "R1.py"],
     {"python": retro.CURRENT, "command": "digest", "test": "t::x", "probe": "R1.py"}),
    (["digest", "t::x"], {"python": retro.CURRENT, "command": "digest", "test": "t::x", "probe": ""}),
    (["stale"], {"python": retro.CURRENT, "command": "stale"}),
    (["sync"], {"python": retro.CURRENT, "command": "sync"}),
])
def test_every_retro_command_parses_to_what_its_code_reads(argv, expected):
    parsed = vars(retro._parser().parse_args(argv))

    assert parsed == expected
    assert {key: type(value) for key, value in parsed.items()} == {
        key: type(value) for key, value in expected.items()}


@pytest.mark.parametrize("argv", [[], ["run"], ["digest"], ["nope"]])
def test_a_retro_command_missing_what_it_needs_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        retro._parser().parse_args(argv)

    assert stopped.value.code == 2
    assert capsys.readouterr().err.startswith("usage: retro.py ")


def test_the_retro_parser_says_what_the_tool_and_record_do(capsys):
    parser = retro._parser()

    assert (parser.prog, parser.description) == (
        "retro.py", "Replay every past calculation bug's check on the commit before its fix and on the fix.")
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "-h"])
    assert "--record rewrite the replayed ledger rows" in " ".join(capsys.readouterr().out.split())


# --- git, for real ------------------------------------------------------------------------------------

def _git_repo(tmp_path: Path, name: str) -> tuple[Path, str]:
    """A repo with one commit whose content is its own name, so two repos never share a sha."""
    repo = tmp_path / name
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
                 ["config", "commit.gpgsign", "false"]):
        retro._checked(["git", *args], repo)
    (repo / "f.txt").write_bytes(name.encode())
    retro._checked(["git", "add", "f.txt"], repo)
    retro._checked(["git", "commit", "-qm", name], repo)
    return repo, retro._checked(["git", "rev-parse", "HEAD"], repo).strip()


@pytest.mark.nightly
@pytest.mark.process
def test_a_commit_only_the_bundle_holds_gets_a_worktree_at_it(tmp_path, monkeypatch):
    source, sha = _git_repo(tmp_path, "source")
    retro._checked(["git", "bundle", "create", "-q", str(tmp_path / "h.bundle"), "--all"], source)
    clone, _ = _git_repo(tmp_path, "clone")
    monkeypatch.setenv(retro.BUNDLE_ENV, str(tmp_path / "h.bundle"))

    assert not retro.have_commit(sha, clone)
    tree = retro.worktree(sha, retro.Site(repo=clone, work=tmp_path / "work"))

    assert retro.have_commit(sha, clone)
    assert retro._checked(["git", "rev-parse", "HEAD"], tree).strip() == sha
    assert (tree / "f.txt").read_bytes() == b"source"


@pytest.mark.nightly
@pytest.mark.process
def test_a_restored_worktree_is_reused_at_its_commit_and_rebuilt_when_its_gitdir_dangles(tmp_path):
    """The accuracy.yml cache restores CRAPKIT_RETRO_WORK into a fresh clone, whose
    .git/worktrees has no entry for it: real git, both ways."""
    repo, sha = _git_repo(tmp_path, "repo")
    site = retro.Site(repo=repo, work=tmp_path / "work")
    tree = retro.worktree(sha, site)
    (tree / "warm").write_bytes(b"")
    venv = tmp_path / "work" / f"{sha[:12]}-venv-3.12-wheel"
    venv.mkdir()

    assert retro.worktree(sha, site) == tree
    assert (tree / "warm").is_file() and venv.is_dir()

    shutil.rmtree(repo / ".git" / "worktrees")
    assert retro.worktree(sha, site) == tree

    assert not (tree / "warm").exists() and not venv.exists()
    assert retro._checked(["git", "rev-parse", "HEAD"], tree).strip() == sha


# --- a row's env, its check's node id, and what the commands say ---------------------------------

def test_a_row_s_env_moves_the_digest_its_ledger_row_records_and_reads():
    bug = retro.Bug("R1", OTHER, "a" * 12, "b" * 12, "", CUT)
    recorded = retro.ledger_row(bug, retro.Outcome("red", "E", "e"), retro.Outcome("pass", "", "p"))
    listed = {**_bug_row("R1"), "test": OTHER, "env": CUT}

    assert recorded["digest"] == retro.digest(OTHER, env=CUT) != retro.digest(OTHER)
    assert not retro._is_stale(listed, {retro.row_key(listed): recorded})


def test_a_row_without_an_env_cell_replays_with_no_env():
    row = {name: value for name, value in _bug_row("R1").items() if name != "env"}

    assert retro.bug_of(row).env == ""


def test_a_check_inside_a_class_exists_once_its_file_defines_the_method(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_c.py").write_text(
        "class TestC:\n    def test_x(self):\n        pass\n", encoding="utf-8")

    assert retro.check_exists({"test": "tests/test_c.py::TestC::test_x[a]"}, tmp_path)


def test_an_env_word_outside_the_switches_is_refused_with_every_switch_named():
    with pytest.raises(retro.RetroError) as refused:
        retro.switches("PATH=/bin")

    assert str(refused.value) == (
        "a bugs.tsv env names PATH; it may set only CRAPKIT_ACCURACY_LANGUAGES, "
        "CRAPKIT_ACCURACY_ROOT_PATHS, each as NAME=value")


def test_nightly_s_platform_only_flag_says_which_rows_it_replays(capsys):
    with pytest.raises(SystemExit):
        retro.main(["nightly", "--help"])

    assert ("--platform-only replay only the rows whose platform is this OS (a Windows or "
            "macOS cell); the Linux job replays the `any` rows") in " ".join(
        capsys.readouterr().out.split())
