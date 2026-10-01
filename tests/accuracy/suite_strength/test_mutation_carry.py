"""tools/accuracy/mutation.py: a calc run judges only what its stored verdicts do not carry.

A weekly or diff run used to judge every mutant of its scope, or of the
functions since() named, whatever an earlier run had judged at the same text:
four diff runs on 2026-10-01 judged the same 1,089 mutants at heads where 0 of
their 93 functions differed, and the weekly costs about 2,024 container-minutes
a run. Each receipt now stores what each function's verdicts rest on, and a run
judges a function only when one of those moved.

Every rule is checked both ways: a changed input reruns exactly the functions
it reaches (with the canary, which every judging run adds), and an unchanged
input reruns nothing, with the carried verdicts equal to a cold run's. mutmut
and the stage are stood in for (FakeMutmut); the last test runs mutmut itself
in the accuracy image.
"""
from __future__ import annotations

import copy
import datetime
import fnmatch
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_carry",
                                                  REPO / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load()

SCORE = ("def crap(ccn, cov):\n    return ccn * ccn * (1 - cov) ** 3 + ccn\n\n\n"
         "def grade(value):\n    return value > 5\n")
DIGEST = ("LIMIT = 3\n\n\ndef totals(rows):\n    return sum(rows[:LIMIT])\n\n\n"
          "class Shown:\n    @property\n    def size(self):\n        return self\n")
FILES = {
    "src/crapkit/score.py": SCORE,
    "src/crapkit/digest.py": DIGEST,
    "src/crapkit/helper.py": "def helper():\n    return 1\n",
    "tests/conftest.py": "import pytest\n",
    "tests/unit/test_score.py": "def test_crap():\n    pass\n\n\ndef test_grade():\n    pass\n",
    "tests/unit/test_digest.py": "def test_totals():\n    pass\n",
    "tests/unit/data/rows.txt": "1\n2\n",
    "README.md": "readme\n",
}
MODULES = ["src/crapkit/digest.py", "src/crapkit/score.py"]
CRAP, GRADE = ("src/crapkit/score.py", "crap"), ("src/crapkit/score.py", "grade")
TOTALS, SIZE = ("src/crapkit/digest.py", "totals"), ("src/crapkit/digest.py", "Shown.size")
EVERY = [CRAP, GRADE, TOTALS, SIZE]
# What mutmut makes of FILES: Shown.size is a property, so it makes no mutant of it.
CODES = {CRAP: {"crapkit.score.x_crap__mutmut_1": 1, "crapkit.score.x_crap__mutmut_2": 1},
         GRADE: {"crapkit.score.x_grade__mutmut_1": 1},
         TOTALS: {"crapkit.digest.x_totals__mutmut_1": 1, "crapkit.digest.x_totals__mutmut_2": 3}}
T_CRAP, T_GRADE = "tests/unit/test_score.py::test_crap", "tests/unit/test_score.py::test_grade"
T_TOTALS = "tests/unit/test_digest.py::test_totals"
MAP = {"crapkit.score.x_crap": [T_CRAP], "crapkit.score.x_grade": [T_GRADE],
       "crapkit.digest.x_totals": [T_TOTALS]}
NOTHING = {"reads": [], "dirs": [], "spawns": []}
REACH = {"outside": dict(NOTHING),
         "tests": {T_CRAP: dict(NOTHING), T_GRADE: dict(NOTHING),
                   T_TOTALS: {"reads": ["tests/unit/data/rows.txt"], "dirs": ["tests/unit/data"],
                              "spawns": []}}}


def _tree(files: dict[str, str]):
    blobs = {path: mutation._sha(text) for path, text in files.items()}
    texts = {mutation._sha(text): text for text in files.values()}
    return mutation.Tree(blobs, texts.__getitem__)


def _put(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data).encode())


def _globs(*functions) -> list[str]:
    return sorted(mutation.mutmut_glob(*function) for function in functions)


class FakeMutmut:
    """Stands in for mutmut_in: answers each mutant the globs name with its exit code
    in `codes`, and leaves what mutmut and the launcher leave in the stage: the
    stats map, the reach record and each module's meta file."""

    def __init__(self):
        self.codes, self.map, self.reach = copy.deepcopy(CODES), copy.deepcopy(MAP), copy.deepcopy(REACH)
        self.calls, self.complete = [], True

    def __call__(self, stage, globs, env, children, budget=None):
        self.calls.append(sorted(globs))
        _put(stage / mutation.STATS_MAP, {"tests_by_mangled_function_name": self.map})
        _put(stage / mutation.REACH_FILE, self.reach)
        for module in MODULES:
            placed = {mutation.mangled(*function).rpartition(".")[2]: "h"
                      for function in self.codes if function[0] == module}
            _put(stage / "mutants" / f"{module}.meta", {"hash_by_function_name": placed})
        return self.rows(globs), self.complete

    def rows(self, globs):
        return [self.row(function, name, code) for function, named in self.codes.items()
                for name, code in named.items()
                if any(fnmatch.fnmatchcase(name, glob) for glob in globs)]

    @staticmethod
    def row(function, name, code):
        status = mutation.STATUS_BY_EXIT[code]
        return mutation.Result(name, *function, status, "" if status in mutation.KILLED else name)


class World:
    """A repo, a stage and receipts under tmp_path, with HEAD's files in `files`."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.files, self.env = dict(FILES), "env-1"
        self.now = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
        self.repo, self.stage, self.mutmut = tmp_path / "repo", tmp_path / "stage", FakeMutmut()
        self.stage.mkdir(parents=True)
        self.repo.mkdir()
        tables = tmp_path / "tables"
        tables.mkdir()
        for name, columns in (("survivors.tsv", mutation.SURVIVOR_COLUMNS),
                              ("equivalent.tsv", mutation.EQUIVALENT_COLUMNS),
                              ("floors.tsv", mutation.FLOOR_COLUMNS)):
            mutation.write_table(tables / name, columns, [])
        for name, value in (("REPO", self.repo), ("TABLES", tables), ("mutmut_in", self.mutmut)):
            monkeypatch.setattr(mutation, name, value)
        monkeypatch.setattr(mutation, "weekly_modules", lambda: list(MODULES))
        monkeypatch.setattr(mutation, "calc_functions", lambda root=None: {})
        monkeypatch.setattr(mutation, "stage_deselected", lambda: [])
        monkeypatch.setattr(mutation, "_prepare_stage", lambda targets, where, deselect=(): self.stage)
        monkeypatch.setattr(mutation, "head_tree", lambda repo: _tree(self.files))
        monkeypatch.setattr(mutation, "env_key", lambda stage: self.env)
        monkeypatch.setattr(mutation, "_now", lambda: self.now)
        monkeypatch.setattr(mutation, "_git", lambda repo, *args: "f" * 40 + "\n")

    def run(self, *argv: str) -> int:
        self.now += datetime.timedelta(minutes=1)
        return mutation.main(list(argv) or ["weekly", "--shard", "1", "--of", "1"])

    def judged(self) -> list[str] | None:
        """The globs the last run handed mutmut, or None when it started no mutmut."""
        return self.mutmut.calls.pop() if self.mutmut.calls else None

    def receipt(self, name: str = "weekly-1.json") -> dict:
        return json.loads((self.repo / mutation.RECEIPTS / name).read_bytes())

    def verdicts(self, name: str = "weekly-1.json") -> dict[str, str]:
        return {row["name"]: row["status"] for row in self.receipt(name)["results"]}


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    return World(tmp_path, monkeypatch)


def _first_run(world: World) -> dict[str, str]:
    assert world.run() == 0
    assert world.judged() == _globs(*EVERY)
    return world.verdicts()


# --- the skip direction: nothing moved, nothing is judged ----------------------------------------

def test_a_second_weekly_run_at_an_unchanged_tree_judges_no_mutant(world, capsys):
    first = _first_run(world)

    assert world.run() == 0

    assert world.judged() is None
    assert world.verdicts() == first
    assert "mutation: 4 function(s) carry their stored verdicts; judging 0" in capsys.readouterr().out


def test_a_carried_verdict_equals_what_a_cold_run_judges(world, capsys):
    _first_run(world)
    world.files["src/crapkit/score.py"] = SCORE.replace("value > 5", "value >= 5")
    world.run()
    carried = world.verdicts()
    world.judged()

    assert world.run("weekly", "--shard", "1", "--of", "1", "--cold") == 0

    assert world.judged() == _globs(*EVERY)
    assert world.verdicts() == carried
    assert world.receipt()["cold_mismatch"] == []


def test_a_diff_run_judges_nothing_at_a_tree_the_weekly_run_judged(world):
    _first_run(world)

    assert world.run("diff") == 0

    assert world.judged() is None
    assert world.receipt(f"diff-{'f' * 12}.json")["complete"] is True


# --- the rerun direction: each input reruns exactly what it reaches ----------------------------------

def _edit(world: World, path: str, text: str | None) -> None:
    if text is None:
        del world.files[path]
    else:
        world.files[path] = text


@pytest.mark.parametrize("path, text, judged", [
    ("src/crapkit/score.py", SCORE.replace("value > 5", "value >= 5"), [GRADE]),
    ("tests/unit/test_digest.py", "def test_totals():\n    assert True\n", [TOTALS]),
    ("tests/unit/data/rows.txt", "1\n2\n3\n", [TOTALS]),
    ("tests/unit/data/more.txt", "4\n", [TOTALS]),
    ("tests/unit/test_digest.py", None, [TOTALS]),
    ("src/crapkit/helper.py", "def helper():\n    return 2\n", [GRADE, TOTALS]),
    ("tests/conftest.py", "import os\n", [GRADE, TOTALS]),
    ("src/crapkit/digest.py", DIGEST.replace("LIMIT = 3", "LIMIT = 4"), [GRADE, TOTALS, SIZE]),
    ("src/crapkit/digest.py", DIGEST.replace("return self", "return None"), [GRADE, TOTALS, SIZE]),
    ("README.md", "changed\n", []),
    ("tests/unit/test_new.py", "def test_new():\n    pass\n", []),
], ids=["function-body", "covering-test-module", "file-a-covering-test-reads",
        "folder-a-covering-test-lists", "covering-test-module-removed", "unmapped-src-module",
        "conftest", "module-code-outside-functions", "function-mutmut-keeps-no-map-for",
        "file-no-test-reads", "new-test-module-and-every-verdict-a-kill"])
def test_a_changed_input_reruns_exactly_the_functions_it_reaches(world, path, text, judged):
    """The canary, score.crap, is judged with whatever is judged; a function with
    no mutant (Shown.size) carries while its key holds, since a mutmut that made no
    mutant of its text makes none again."""
    _first_run(world)
    _edit(world, path, text)

    world.run()

    assert world.judged() == (_globs(CRAP, *judged) if judged else None)


def test_the_run_says_what_carried_and_why_each_other_function_is_judged(world, capsys):
    _first_run(world)
    capsys.readouterr()
    world.files["src/crapkit/score.py"] = SCORE.replace("value > 5", "value >= 5")

    world.run()

    said = capsys.readouterr().out
    assert "mutation: 2 function(s) carry their stored verdicts; judging 2\n" in said
    assert ("mutation: judging 1 function(s): its text, or its module's code outside any "
            "function, changed\n") in said
    assert "mutation: judging 1 function(s): a cold run, or the canary\n" in said


def test_a_function_a_covering_test_also_runs_reruns_with_it(world):
    world.mutmut.map["crapkit.score.x_grade"].append(T_TOTALS)
    _first_run(world)
    world.files["src/crapkit/score.py"] = SCORE.replace("value > 5", "value >= 5")

    world.run()

    assert world.judged() == _globs(CRAP, GRADE, TOTALS)


def test_a_changed_environment_reruns_everything(world, capsys):
    _first_run(world)
    world.env = "env-2"

    world.run()

    assert world.judged() == _globs(*EVERY)
    assert "the environment changed" in capsys.readouterr().out


def test_a_verdict_past_28_days_is_judged_again_and_one_inside_carries(world):
    _first_run(world)
    world.now += datetime.timedelta(days=27, hours=23)
    world.run()
    assert world.judged() is None

    world.now += datetime.timedelta(days=1)
    world.run()

    assert world.judged() == _globs(*EVERY)


def test_a_survivor_carries_only_to_the_tree_it_was_judged_at(world):
    """A new or moved test may reach a survivor anywhere, so its verdict holds only
    at its own tree; a kill stays a kill whatever test is added."""
    world.mutmut.codes[GRADE]["crapkit.score.x_grade__mutmut_1"] = 0
    world.run()
    world.judged()
    world.run()
    assert world.judged() is None

    world.files["README.md"] = "changed\n"
    world.run()

    assert world.judged() == _globs(CRAP, GRADE)


def test_an_unfinished_mutant_is_judged_again_by_every_run(world):
    world.mutmut.codes[GRADE]["crapkit.score.x_grade__mutmut_1"] = 36
    world.run()
    world.judged()

    world.run()

    assert world.judged() == _globs(CRAP, GRADE)


def test_a_test_that_starts_a_program_voids_its_functions_on_any_change(world):
    world.mutmut.reach["tests"][T_GRADE]["spawns"] = ["python3"]
    _first_run(world)
    world.run()
    assert world.judged() is None

    world.files["README.md"] = "changed\n"
    world.run()

    assert world.judged() == _globs(CRAP, GRADE)


def test_a_file_the_suite_reads_outside_any_test_voids_every_function_with_mutants(world):
    world.mutmut.reach["outside"]["reads"] = ["README.md"]
    _first_run(world)
    world.run()
    assert world.judged() is None

    world.files["README.md"] = "changed\n"
    world.run()

    assert world.judged() == _globs(CRAP, GRADE, TOTALS)


def test_a_covering_test_with_no_reach_record_stores_nothing_for_its_function(world):
    del world.mutmut.reach["tests"][T_TOTALS]
    _first_run(world)

    world.run()

    assert world.judged() == _globs(CRAP, TOTALS)


def test_a_run_whose_reach_record_broke_stores_only_its_functions_with_no_mutant(world):
    world.mutmut.reach = {"broken": True}
    _first_run(world)

    world.run()

    assert world.judged() == _globs(CRAP, GRADE, TOTALS)


def test_a_diff_run_judges_what_does_not_carry_where_since_named_nothing(world):
    """since() read only the diff of calc modules, so a run after a test-only change
    judged no mutant; the stored carry reruns the function that test covers."""
    _first_run(world)
    world.files["tests/unit/test_digest.py"] = "def test_totals():\n    assert 1\n"

    assert world.run("diff") == 0

    assert world.judged() == _globs(CRAP, TOTALS)
    receipt = world.receipt(f"diff-{'f' * 12}.json")
    assert sorted(map(tuple, receipt["judged"])) == sorted([CRAP, TOTALS])
    assert sorted(map(tuple, receipt["functions"])) == sorted(EVERY)


def test_a_diff_run_its_cap_stopped_is_incomplete_and_keeps_the_verdicts_it_has(world, capsys):
    world.mutmut.complete = False

    assert world.run("diff", "--cap-minutes", "1") == 1

    assert "incomplete: the 1-minute cap stopped the run" in capsys.readouterr().out
    world.mutmut.complete = True
    world.run("diff")
    assert world.judged() == _globs(CRAP, SIZE)


def test_a_cold_run_that_judges_a_carried_verdict_otherwise_voids_every_older_receipt(
        world, capsys):
    _first_run(world)
    world.run("diff")
    world.mutmut.codes[TOTALS]["crapkit.digest.x_totals__mutmut_1"] = 0

    assert world.run("weekly", "--shard", "1", "--of", "1", "--cold") == 1

    assert world.receipt()["cold_mismatch"] == ["crapkit.digest.x_totals__mutmut_1"]
    assert ("crapkit.digest.x_totals__mutmut_1: a fresh judgement differs from its carried "
            "verdict") in capsys.readouterr().out
    receipts = mutation.loaded(world.repo / mutation.RECEIPTS)
    assert [receipt["kind"] for receipt in mutation.trusted(receipts)] == ["weekly"]


# --- the pieces ----------------------------------------------------------------------------------

def test_a_function_key_holds_its_decorators_and_its_module_s_code_outside_functions():
    facts = mutation.module_facts(DIGEST)
    key = mutation.function_key(facts, "totals")

    assert mutation.function_key(mutation.module_facts(DIGEST.replace("LIMIT = 3", "LIMIT = 4")),
                                 "totals") != key
    assert mutation.function_key(mutation.module_facts(DIGEST.replace("@property", "@cached")),
                                 "Shown.size") != mutation.function_key(facts, "Shown.size")
    assert mutation.function_key(mutation.module_facts(DIGEST + "\n"), "totals") != key
    assert mutation.function_key(mutation.module_facts(DIGEST), "totals") == key


def test_the_code_outside_functions_keeps_imports_constants_and_class_lines():
    assert mutation.outside_code(DIGEST) == "LIMIT = 3\n\n\n\n\nclass Shown:\n"


@pytest.mark.parametrize("statuses, kind", [
    ((), "none"), (("killed", "caught by type check"), "killed"), (("killed", "survived"), "alive"),
    (("no tests",), "alive"), (("skipped",), "alive"), (("killed", "timeout"), "unfinished"),
    (("segfault",), "unfinished"), (("killed", "not checked"), ""), (("suspicious",), "")])
def test_a_function_s_verdicts_are_stored_by_their_kind(statuses, kind):
    rows = [mutation.Result(f"m.x_f__mutmut_{n}", "m.py", "f", status)
            for n, status in enumerate(statuses)]

    assert mutation.verdict_kind(rows) == kind


@pytest.mark.parametrize("path, test", [
    ("tests/unit/test_score.py", True), ("tests/accuracy/p/score_test.py", True),
    ("tests/conftest.py", False), ("tests/accuracy/kit/exact.py", False),
    ("src/crapkit/test_x.py", False), ("tests/unit/test_data.txt", False)])
def test_a_test_module_is_one_pytest_collects_tests_from(path, test):
    assert mutation.is_test_module(path) is test


def test_no_receipt_older_than_a_cold_mismatch_is_trusted_and_tools_receipts_never_are():
    receipts = [{"kind": "diff", "created": "2026-10-01T10:00:00Z"},
                {"kind": "weekly", "created": "2026-10-01T11:00:00Z", "cold_mismatch": ["m"]},
                {"kind": "diff", "created": "2026-10-01T12:00:00Z"},
                {"kind": "tools", "created": "2026-10-01T13:00:00Z"}]

    assert mutation.trusted(receipts) == receipts[1:3]


def test_a_stats_map_made_at_another_tree_is_dropped_before_mutmut_runs(tmp_path):
    head = mutation.Head(_tree(FILES), "env-1", datetime.datetime.now(datetime.timezone.utc))
    for name in (mutation.STATS_MAP, mutation.REACH_FILE):
        _put(tmp_path / name, {})

    mutation._stamp_stats(tmp_path, head)
    mutation._fresh_stats(tmp_path, head)
    assert (tmp_path / mutation.STATS_MAP).is_file() and (tmp_path / mutation.REACH_FILE).is_file()

    mutation._fresh_stats(tmp_path, mutation.Head(_tree({**FILES, "README.md": "x\n"}), "env-1",
                                                  head.now))
    assert not (tmp_path / mutation.STATS_MAP).exists()
    assert not (tmp_path / mutation.REACH_FILE).exists()


# --- the launcher's reach record ----------------------------------------------------------------------

def _launcher() -> dict:
    namespace: dict = {}
    exec(compile(mutation.LAUNCHER.split("if __name__")[0], "launcher", "exec"), namespace)
    return namespace


def _caller(name: str, filename: str, body: str, **names):
    """A function whose frame looks like `name`'s code in `filename`."""
    scope = {"__name__": name, **names}
    exec(compile(f"def call(*args):\n    {body}\n", filename, "exec"), scope)
    return scope["call"]


@pytest.fixture
def reach(tmp_path, monkeypatch):
    launcher = _launcher()
    (tmp_path / "mutants").mkdir()
    monkeypatch.chdir(tmp_path / "mutants")
    recorder = launcher["Reach"]((tmp_path / "mutants", tmp_path))
    hook = _caller("test_score", "tests/unit/test_score.py",
                   "recorder.audit(*args, sys._getframe())", recorder=recorder, sys=sys)
    return types.SimpleNamespace(launcher=launcher, recorder=recorder, audit=hook, root=tmp_path)


def _saved(reach, whole=True) -> dict:
    reach.recorder.save(reach.root / mutation.REACH_FILE, whole)
    return json.loads((reach.root / mutation.REACH_FILE).read_bytes())


def test_each_read_goes_to_the_test_that_made_it_as_a_repo_path(reach):
    reach.audit("open", ("README.md", "r", 0))
    reach.recorder.enter("mutants/tests/unit/test_score.py::test_crap")
    reach.audit("open", (str(reach.root / "mutants" / "tests" / "data.txt"), "r", 0))
    reach.audit("open", (str(reach.root / "src" / "crapkit" / "score.py"), "r", 0))
    reach.audit("open", (str(reach.root.parent / "elsewhere.txt"), "r", 0))
    reach.audit("open", (3, "r", 0))
    reach.recorder.leave()

    saved = _saved(reach)

    assert saved["outside"] == {"reads": ["README.md"], "dirs": [], "spawns": []}
    assert saved["tests"] == {"tests/unit/test_score.py::test_crap": {
        "reads": ["src/crapkit/score.py", "tests/data.txt"], "dirs": [], "spawns": []}}


def test_a_test_that_reads_nothing_is_still_recorded(reach):
    """A covering test with no record stores nothing for its function; one that read
    nothing must say so."""
    reach.recorder.enter("tests/unit/test_score.py::test_grade")
    reach.recorder.leave()

    assert _saved(reach)["tests"] == {"tests/unit/test_score.py::test_grade": NOTHING}


def test_pytest_s_and_mutmut_s_own_reads_are_not_a_test_s(reach):
    reach.recorder.enter("tests/unit/test_score.py::test_crap")
    for module in ("_pytest.assertion.rewrite", "pluggy._callers", "mutmut.__main__"):
        _caller(module, f"{module}.py", "recorder.audit(*args, sys._getframe())",
                recorder=reach.recorder, sys=sys)("open", ("tests/unit/test_score.py", "rb", 0))

    assert _saved(reach)["tests"]["tests/unit/test_score.py::test_crap"] == NOTHING


def test_an_import_counts_only_when_it_loads_a_test_module(reach):
    """The carry rule keys every other .py file itself; a test module one test
    imports from another is that test's to reach."""
    get_data = _caller("importlib._bootstrap_external", "<frozen importlib._bootstrap_external>",
                       "recorder.audit(*args, sys._getframe())", recorder=reach.recorder, sys=sys)
    importer = _caller("test_score", "tests/unit/test_score.py", "get_data(*args)",
                       get_data=get_data)
    reach.recorder.enter("tests/unit/test_score.py::test_crap")
    importer("open", ("src/crapkit/score.py", "rb", 0))
    importer("open", ("tests/unit/test_helpers.py", "rb", 0))
    importer("open", ("tests/unit/helpers.py", "rb", 0))

    assert _saved(reach)["tests"]["tests/unit/test_score.py::test_crap"]["reads"] == [
        "tests/unit/test_helpers.py"]


def test_a_listing_and_a_started_program_go_to_the_running_test(reach):
    reach.recorder.enter("tests/unit/test_score.py::test_crap")
    reach.audit("os.listdir", (None,))
    reach.audit("os.scandir", ("tests/unit",))
    reach.audit("subprocess.Popen", (None, ["git", "status"], str(reach.root.parent), None))
    reach.audit("subprocess.Popen", (None, "git log", None, None))
    reach.audit("subprocess.Popen", ("/usr/bin/python3.12", ["python"], None, None))
    reach.audit("os.system", ("sh -c true",))
    reach.audit("os.posix_spawn", ("/bin/NODE.EXE", ["node"], {}))

    assert _saved(reach)["tests"]["tests/unit/test_score.py::test_crap"] == {
        "reads": [], "dirs": ["", "tests/unit"], "spawns": ["git", "node", "python3.12", "sh"]}


def test_a_fixture_wider_than_one_test_reads_for_every_test(reach):
    plugin = reach.launcher["reach_plugin"](reach.recorder)
    plugin.pytest_runtest_logstart("tests/unit/test_score.py::test_crap", None)
    wide = plugin.pytest_fixture_setup(types.SimpleNamespace(scope="session"), None)
    next(wide)
    reach.audit("open", ("README.md", "r", 0))
    with pytest.raises(StopIteration):
        wide.send("value")
    narrow = plugin.pytest_fixture_setup(types.SimpleNamespace(scope="function"), None)
    next(narrow)
    reach.audit("open", ("tests/unit/data/rows.txt", "r", 0))
    with pytest.raises(StopIteration):
        narrow.send("value")
    plugin.pytest_runtest_logfinish("tests/unit/test_score.py::test_crap", None)

    saved = _saved(reach)

    assert saved["outside"]["reads"] == ["README.md"]
    assert saved["tests"]["tests/unit/test_score.py::test_crap"]["reads"] == [
        "tests/unit/data/rows.txt"]


def test_an_event_it_cannot_read_breaks_the_record_and_never_the_test(reach):
    reach.audit("subprocess.Popen", (None, [], None, None))

    assert _saved(reach) == {"broken": True}
    assert mutation.load_reach(reach.root) is None


def test_a_run_of_new_tests_adds_to_the_record_and_a_full_run_replaces_it(reach):
    path = reach.root / mutation.REACH_FILE
    _put(path, {"outside": {"reads": ["old.txt"], "dirs": [], "spawns": []},
                "tests": {"tests/unit/test_old.py::t": NOTHING}})
    reach.recorder.enter("tests/unit/test_score.py::test_crap")
    reach.recorder.leave()

    merged = _saved(reach, whole=False)
    assert sorted(merged["tests"]) == ["tests/unit/test_old.py::t",
                                       "tests/unit/test_score.py::test_crap"]
    assert merged["outside"]["reads"] == ["old.txt"]
    assert sorted(_saved(reach, whole=True)["tests"]) == ["tests/unit/test_score.py::test_crap"]


def test_the_stats_run_records_reach_for_each_test_it_runs(tmp_path):
    """run_stats hands pytest the reach plugin before the failures plugin, listens
    for audit events only while the stats run lasts, and writes the record."""
    launcher, heard = _launcher(), []
    launcher.update(_run_stats=lambda self, *, tests: self.execute_pytest(["-x"], plugins=["stats"]),
                    _state=lambda: types.SimpleNamespace(tests_by_mangled_function_name={}),
                    FAILURES=tmp_path / "failures.txt", REACH=tmp_path / mutation.REACH_FILE,
                    MUTANTS=tmp_path / "mutants", STAGE=tmp_path, listen=heard.append)
    handed = []

    class Runner:
        def execute_pytest(self, params, plugins=(), **kwargs):
            handed.append(plugins)
            plugins[1].pytest_runtest_logstart("tests/unit/test_score.py::test_crap", None)
            plugins[1].pytest_runtest_logfinish("tests/unit/test_score.py::test_crap", None)
            return 0

    assert launcher["run_stats"](Runner(), tests=[]) == 0

    ((stats, plugin, failures),) = handed
    assert (stats, type(failures).__name__) == ("stats", "Failures")
    assert [type(item).__name__ for item in heard] == ["Reach", "NoneType"]
    assert mutation.load_reach(tmp_path)["tests"] == {"tests/unit/test_score.py::test_crap": NOTHING}


# --- mutmut itself, in the accuracy image -------------------------------------------------------------

# A package of its own name, so no installed crapkit answers the tests' imports.
REAL = {name: ("src/carrykit/" + name.partition(":")[0], name.partition(":")[2])
        for name in ("score.py:crap", "score.py:grade", "digest.py:totals")}
REAL_SCORE = ("def crap(ccn, cov):\n    return ccn * ccn * (1 - cov) ** 3 + ccn\n\n\n"
              "def grade(value):\n    return 'high' if value > 5 else 'low'\n")
REAL_DIGEST = ("from pathlib import Path\n\n\ndef totals(path):\n"
               "    return sum(int(line) for line in Path(path).read_text().split())\n")
REAL_TESTS = {
    "tests/unit/test_score.py": (
        "from carrykit.score import crap, grade\n\n\n"
        "def test_crap():\n    assert crap(2, 0.0) == 6\n    assert crap(3, 0.5) == 4.125\n"
        "    assert crap(1, 1.0) == 1\n\n\n"
        "def test_grade():\n    assert grade(6) == 'high'\n    assert grade(5) == 'low'\n"),
    "tests/unit/test_digest.py": (
        "from pathlib import Path\n\nfrom carrykit.digest import totals\n\n\n"
        "def test_totals():\n"
        "    assert totals(Path(__file__).parent / 'data' / 'rows.txt') == 6\n"),
    "tests/unit/data/rows.txt": "1\n2\n3\n",
}


def _git_repo(root: Path, files: dict[str, str]) -> None:
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
                 ["config", "commit.gpgsign", "false"]):
        mutation._git(root, *args)
    _commit(root, files)


def _commit(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(text.encode())
    mutation._git(root, "add", "-A")
    mutation._git(root, "commit", "-qm", "c")


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.platform("linux")
def test_a_real_mutmut_run_carries_what_holds_and_judges_what_moved(tmp_path, monkeypatch, oracle):
    """mutmut 3.8.0 and the launcher end to end on a small repo: the first weekly
    run judges every mutant, a second judges none and stores the same verdicts, an
    edit to the data file a test reads judges that test's function and the canary
    alone, and a cold run at that tree judges what the carry kept."""
    oracle("mutmut")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo, {"src/carrykit/__init__.py": "", "src/carrykit/score.py": REAL_SCORE,
                     "src/carrykit/digest.py": REAL_DIGEST, "pyproject.toml": "[project]\nname = 'x'\n",
                     ".gitignore": ".crapkit/\n", **REAL_TESTS})
    tables = tmp_path / "tables"
    tables.mkdir()
    for name, columns in (("survivors.tsv", mutation.SURVIVOR_COLUMNS),
                          ("equivalent.tsv", mutation.EQUIVALENT_COLUMNS),
                          ("floors.tsv", mutation.FLOOR_COLUMNS)):
        mutation.write_table(tables / name, columns, [])
    for name, value in (("REPO", repo), ("TABLES", tables), ("CALC_TESTS", ("tests/unit",)),
                        ("CANARY", REAL["score.py:crap"])):
        monkeypatch.setattr(mutation, name, value)
    monkeypatch.setattr(mutation, "weekly_modules", lambda: ["src/carrykit/digest.py",
                                                             "src/carrykit/score.py"])
    monkeypatch.setattr(mutation, "calc_functions", lambda root=None: {})
    monkeypatch.setattr(mutation, "stage_deselected", lambda: [])
    runs, real = [], mutation.mutmut_in
    monkeypatch.setattr(mutation, "mutmut_in", lambda stage, globs, *rest: runs.append(
        sorted(globs)) or real(stage, globs, *rest))
    weekly = ["weekly", "--shard", "1", "--of", "1", "--max-children", "2"]
    receipt = repo / mutation.RECEIPTS / "weekly-1.json"

    def verdicts() -> dict:
        return {row["name"]: row["status"] for row in json.loads(receipt.read_bytes())["results"]}

    assert mutation.main(weekly) in (0, 1)
    first = verdicts()
    assert runs == [_globs(*REAL.values())]
    assert first and set(first.values()) <= mutation.KILLED | mutation.ALIVE

    assert mutation.main(weekly) in (0, 1)
    assert len(runs) == 1 and verdicts() == first

    _commit(repo, {"tests/unit/data/rows.txt": "1\n2\n3\n\n"})
    assert mutation.main(weekly) in (0, 1)
    assert runs[-1] == _globs(REAL["digest.py:totals"], REAL["score.py:crap"])
    carried = verdicts()

    assert mutation.main([*weekly, "--cold"]) in (0, 1)
    assert verdicts() == carried
    assert json.loads(receipt.read_bytes())["cold_mismatch"] == []
