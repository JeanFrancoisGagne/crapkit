"""Change control's readers and messages at their edges.

A table row is a dict over its header, so a cell the header lacks, or a short
row leaves off, reads through the default the reader gives: an empty string,
never a calc, an id or an oracle of its own. Each refusal and report line is
checked word for word, and each git or pytest process is started with the
flags, timeout and working directory it needs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from accuracy.change_control import cc_seeds as seeds

cc = seeds.tool()

SCORED = seeds.SCORED
CRAP = "CRAP score"


def _tree(files: dict[str, bytes]) -> cc.DictTree:
    return cc.DictTree(files)


def _cell(column: str = "crap", new: str = "2", path: str = "a.py") -> cc.Cell:
    return cc.Cell(SCORED, path, "f", column, "1", new)


# --- tables read through their defaults ----------------------------------------------------

def test_a_header_is_split_at_tabs_only():
    data = b"a b\tc\nx\ty\n"

    assert cc.rows(data) == [{"a b": "x", "c": "y"}]
    assert cc.raw_rows(data) == [({"a b": "x", "c": "y"}, "x\ty")]


def test_a_lock_row_without_its_digest_and_change_reads_them_as_empty():
    assert cc.lock_of(_tree({cc.LOCK: b"path\nx\n"})) == {"x": ("", "")}


def test_a_calcs_table_without_its_columns_reads_them_as_empty():
    tree = _tree({seeds.CALCS: b"other\nv\n"})

    assert cc.calcs_of(tree) == [cc.CalcRow("score_model", "", "", ())]


def test_a_modules_cell_splits_at_commas_only():
    assert cc._modules("a.py, b c.py") == ("a.py", "b c.py")


def test_a_row_s_first_is_its_id_wherever_the_id_column_is():
    assert (cc._first({"x": "1", "id": "C9"}), cc._first({})) == ("C9", "")


def test_two_changes_without_an_id_are_one_empty_id_twice():
    tree = _tree({cc.CHANGES: b"kind\nfix\nfix\n"})

    assert [problem.text for problem in cc.changes_problems(tree)] == [
        "CHANGES.tsv:  appears 2 times"]


def test_a_kind_none_change_without_a_reason_cell_is_silent():
    assert cc._silent({"C9": {"kind": "none"}}) == [
        cc.Problem("B6", "change C9 is kind none with no reason", "give C9 a reason")]


def test_a_hand_row_without_a_source_cell_is_not_sourced():
    assert cc._sourced([{}]) is False


def test_an_added_survivor_without_a_reason_cell_is_a_problem(capsys):
    path = seeds.SURVIVORS

    problems = cc._evidence_problems(path, [], [({"module": "m"}, "m")])

    assert [problem.text for problem in problems] == [f"{path}: m is added with no reason"]


def test_a_floor_whose_row_lost_its_floor_cell_fell_to_nothing():
    old = [{"group": "g", "floor": "90"}]

    problems = cc._floor_problems("f.tsv", old, [{"group": "g"}])

    assert [problem.text for problem in problems] == ["f.tsv: floor g fell from 90 to nothing"]


def test_an_unset_floor_that_stays_unset_did_not_fall():
    assert cc._floor_problems("f.tsv", [{"group": "g", "floor": ""}], [{"group": "g"}]) == []


def test_a_ledger_row_keeps_its_id_and_test_whatever_the_test_id_holds():
    assert cc._kept(cc.LEDGER, ["R1\ttests/a.py::t[a b]\tred"]) == ["R1\ttests/a.py::t[a b]"]


def test_a_bugs_row_without_an_id_cell_is_the_empty_id():
    diff = SimpleNamespace(base=_tree({cc.BUGS: b"replay\nopen\n"}))

    assert cc._open_bugs(diff) == {""}


def test_a_retro_row_without_a_test_cell_names_no_test():
    assert cc._retro_ids(_tree({seeds.RETRO: b"id\nR1\n"})) == {"R1": ""}


def test_a_metric_digests_row_without_versions_orders_first():
    assert cc._order({}) == (0, ("",))


def test_a_moved_row_without_its_cells_reads_them_as_empty():
    assert cc._recorded({}) == ("", "")
    assert cc._moved_row_key({}) == ("",) * 6


def test_a_column_one_side_lacks_reads_there_as_empty():
    assert cc._row_cells(SCORED, ("a.py", "f"), {"crap": "1"}, {}) == [
        cc.Cell(SCORED, "a.py", "f", "crap", "1", "")]


def test_a_row_a_tree_lacks_is_an_empty_row_and_asks_no_oracle():
    asked = []

    assert cc._row_at(_tree({}), _cell()) == {}
    assert cc._answer(_tree({}), _cell(), lambda *args: asked.append(args)) is None
    assert asked == []


def test_a_cell_no_oracle_answers_is_judged_by_none():
    cell = _cell(column="no_such_column")

    assert cc.judge(_tree({}), cell) == cc.Judgement(cell, "", "")


def test_a_cell_emptied_on_the_head_is_judged_by_none(monkeypatch):
    monkeypatch.setattr(cc, "oracle_for", lambda cell: ("radon", lambda *args: "7"))
    monkeypatch.setattr(cc, "_row_at", lambda tree, cell: {"ccn": ""})
    cell = _cell(column="ccn", new="")

    assert cc.judge(_tree({}), cell) == cc.Judgement(cell, "", "")


def test_no_covering_ruling_is_the_empty_name():
    judgement = cc.Judgement(_cell(), "radon", "3")

    assert cc._covering({}, judgement, ()) == ""


def test_a_golden_no_lock_row_names_belongs_to_no_change():
    diff = SimpleNamespace(head=_tree({cc.LOCK: b"path\tsha256\tchange\n"}), cells=[_cell()],
                           fresh={"XXXX": {"kind": "fix"}})

    assert cc._attributed(diff) == {}


def test_a_missing_golden_reads_as_empty_bytes_for_ownership():
    lock = {"g.tsv": (hashlib.sha256(b"").hexdigest(), "C9")}

    assert cc._owned(_tree({}), lock, "g.tsv", {"C9"}) is True


def test_a_relock_under_a_packet_with_calcs_is_exempt_only_where_other_rules_judge():
    golden = "tests/accuracy/corpus_goldens/goldens/small/scored.tsv"

    assert cc._exempt(golden, {"kind": "fix"}, {"c"}) is True
    assert cc._exempt("tests/accuracy/p/hand.tsv", {"kind": "fix"}, set()) is True


# --- changes, rulings and bugs without their cells ------------------------------------------

def _fresh(rows: dict) -> SimpleNamespace:
    return SimpleNamespace(fresh_of=lambda *kinds: rows, fresh=rows, head_changes={},
                           packet_calcs=lambda packet: {"XXXX"})


def test_a_fresh_change_without_a_calcs_cell_declares_and_covers_nothing():
    diff = _fresh({"C9": {"kind": "fix"}})

    assert cc.Diff.declared(diff) == set()
    assert cc._covers(diff, "XXXX") is False
    assert cc._unexercised(SimpleNamespace(), "C9", {"kind": "definition"}) == []


def test_a_relock_under_a_change_without_a_calcs_cell_names_no_calc():
    path = "tests/accuracy/score_model/hand_score.tsv"

    problem = cc._relock_problem(_fresh({"C9": {"kind": "fix"}}), path, "C9")

    assert problem.text == f"{path} moved under C9, which names no calc of packet score_model (XXXX)"


def test_a_rulings_row_without_a_calc_cell_is_covered_by_no_calc():
    diff = _fresh({"C9": {"kind": "fix", "calcs": "XXXX"}})

    assert cc._uncovered(diff, {}, {}) is True


def test_a_changed_rulings_row_without_a_calc_cell_names_the_empty_calc():
    diff = SimpleNamespace(changed_rulings=lambda: {"R1": ({}, {})}, head_changes={})

    assert cc._ruling_calcs(diff) == {""}
    assert cc._ruling_moved(diff, "R1", {}, {}) == cc.Problem(
        "B9", "rulings row R1 changed (None to None, crapkit None to None) with no fresh fix "
              "or definition change naming ",
        f'{cc.TOOL} declare C1 --kind fix --calcs "<calc>" --reason "<why>"')


def test_a_rulings_edit_without_a_calc_cell_may_name_the_empty_calc():
    rulings = "tests/accuracy/score_model/rulings.tsv"
    base, head = _tree({rulings: b"id\truling\nR1\ta\n"}), _tree({rulings: b"id\truling\nR1\tb\n"})

    assert cc._more(base, head, frozenset()) == {""}


def test_moved_rows_without_a_ruling_cell_cite_no_ruling():
    moved = f"{cc.MOVED}/C9.moved.tsv"
    diff = SimpleNamespace(changed_rulings=dict, head_rulings={}, base_rulings={},
                           head=_tree({moved: b"golden\truling\nx\tR1\ny\n"}))

    assert [problem.text.split(" cite ")[1].split(",")[0]
            for problem in cc._loose_rulings(diff, "C9")] == ["ruling <none>", "ruling R1"]


def _ruled(key: str, oracle: str) -> dict:
    return {key: ("rulings.tsv", {"calc": CRAP, "oracle": oracle}, "line")}


@pytest.mark.parametrize("rulings, row, covered", [
    ({}, {"oracle_value": "3"}, False),
    (_ruled("", "radon"), {"oracle": "radon"}, True),
    (_ruled("XXXX", "radon"), {"oracle": "radon"}, False),
    (_ruled("", "XXXX"), {}, False),
])
def test_a_moved_row_without_a_ruling_cell_is_covered_only_by_a_ruling_keyed_empty(
        rulings, row, covered):
    problem = cc._ruling_problem(SimpleNamespace(head_rulings=rulings), "C9", row, _cell())

    assert (problem is None) is covered


def test_an_uncovered_moved_row_says_which_oracle_and_ruling_it_names():
    problem = cc._ruling_problem(SimpleNamespace(head_rulings={}), "C9", {"oracle_value": "3"},
                                 _cell())

    assert problem.text == ("changes/C9.moved.tsv: a.py:f crap 2 disagrees with None 3 and "
                            "ruling <none> does not cover that calc and oracle")


def test_bugs_added_with_no_fix_change_need_no_retro_row():
    diff = SimpleNamespace(fresh_of=lambda *kinds: {}, head=_tree({cc.BUGS: b"id\nR9\n"}),
                           base=_tree({}))

    assert cc.rule_b7(diff) == []


def test_too_few_bugs_names_every_fix_change():
    [problem] = cc._too_few_bugs({"C4": {}, "C3": {}}, set())

    assert problem.text == "2 fix change(s) (C3, C4) and 0 new bugs.tsv row(s)"


def test_an_open_bug_that_lost_every_row_is_named_with_its_fix():
    diff = SimpleNamespace(head=_tree({cc.BUGS: b"id\nR1\n"}))

    assert cc._unrowed(diff, {"R9"}) == [cc.Problem(
        "B2", f"open bug R9 lost every {cc.BUGS} row",
        "keep the row that names the check that catches it")]


# --- small values and exact lines -----------------------------------------------------------

def test_the_first_change_id_is_c1_and_an_empty_number_is_0():
    assert (cc.next_id({}), cc._number("")) == ("C1", 0.0)


def test_no_analysis_version_is_the_empty_version():
    assert cc.analysis_version(_tree({})) == ""


def test_the_analysis_version_line_is_blanked_and_a_missing_file_is_no_bytes():
    assert cc._unversioned(b"x = 1\nANALYSIS_VERSION = 3\n") == b"x = 1\n\n"
    assert cc._unversioned(None) == b""


def test_a_method_row_is_found_by_its_own_name():
    source = "class C:\n    def m(self):\n        pass\n"

    assert cc.ast_row({"long_name": "C.m( self )", "start": "2"}, source) == "present"


def test_a_parametrized_function_counts_once_whatever_its_ids_hold():
    ids = ["tests/accuracy/kit/test_x.py::test_a[x[1]]", "tests/accuracy/kit/test_x.py::test_a[y]"]

    assert cc.packet_counts(ids) == {"kit": 1}


def test_the_declare_command_names_every_calc_sorted():
    assert cc.declare_command("C9", "fix", {"b", "a"}) == (
        f'{cc.TOOL} declare C9 --kind fix --calcs "a, b" --reason "<why>"')


def test_the_changelog_line_keeps_the_reason_s_last_word_whole():
    request = cc.Request("C9", "fix", (), "handle LaTeX.")

    assert cc._changelog_lines(request)[1] == "- handle LaTeX. (accuracy change C9)"


def _plan(calcs=("a", "b")) -> cc.Plan:
    return cc.Plan(cc.Request("C9", "fix", calcs, "why", today="2026-01-01"), [], {}, [], None, "")


def test_the_judged_line_names_every_calc():
    assert cc._judged_line(_plan()) == (
        "declared C9 (fix: a; b): 0 locked files relocked, 0 golden cells moved, "
        "0 judged by an oracle")


def test_a_change_that_moves_nothing_is_told_its_kind():
    moves = cc.Moves([], [], set())

    assert cc._nothing_moved(cc.Request("C9", "fix", (), "r"), moves) == [
        "nothing moved since the lock; a change that moves nothing is kind none"]
    assert cc.moved_block(_tree({}), []) == ["moved calcs: none (no golden cell or file moved)"]


def test_a_written_change_row_joins_its_calcs_with_semicolons(tmp_path):
    cc.write_plan(tmp_path, _plan(), cc.Running("7", "1.24.0"))

    row = (tmp_path / cc.CHANGES).read_bytes().decode("utf-8").splitlines()[-1]
    assert row == "C9\t2026-01-01\tfix\ta; b\t7\t1.24.0\t#unreleased\twhy"


def test_a_digest_that_moved_under_no_version_asks_for_version_1():
    row = {"analysis_version": "", "lizard_version": "1.24.0", "corpus": "c", "digest": "d"}

    [text] = cc._digest_refusal([row], row, cc.Running("", "1.24.0"))

    assert f"bump ANALYSIS_VERSION in {cc.ANALYZE} to 1 (A1)" in text


def test_the_parsers_say_what_each_command_does():
    assert cc._check_parser().description == "Judge a diff."
    assert cc._declare_parser().description == "Judge and record a change that moves goldens."


def test_the_suggestion_for_a_diff_that_moves_nothing_is_kind_none():
    diff = SimpleNamespace(head=_tree({}), cells=[], surfaces=[], head_changes={})

    assert cc._suggestion(diff) == f'{cc.TOOL} declare C1 --kind none --reason "<why>"'


def test_the_required_calcs_list_in_their_sorted_order(monkeypatch):
    monkeypatch.setattr(cc, "acceptable", lambda cell: frozenset(cell.column.split("+")))
    monkeypatch.setattr(cc, "_follows", lambda cell, columns: False)

    assert cc.required(_tree({}), [_cell("b"), _cell("a+b")], []) == [
        frozenset({"a", "b"}), frozenset({"b"})]


def test_a_refused_declare_ends_with_every_moved_line(monkeypatch):
    monkeypatch.setattr(cc, "moved_block", lambda *args: ["m1", "m2"])
    tree = _tree({path: text.encode("utf-8") for path, text in seeds.base().items()})

    with pytest.raises(cc.ChangeControlError) as refused:
        cc.plan_declare(tree, tree, cc.Request("c9", "fix", (), "r"), cc.running(tree, "1.24.0"))

    assert str(refused.value).endswith("\nm1\nm2")


def test_the_push_that_changes_no_calc_module_says_so(capsys):
    assert cc.run_tests(Path("."), []) == 0
    assert capsys.readouterr().out == (
        "change control: the push changes no calc module; no accuracy check to run\n")


def test_counts_reports_under_its_own_label(tmp_path, monkeypatch, capsys):
    (tmp_path / cc.COUNTS).parent.mkdir(parents=True)
    (tmp_path / cc.COUNTS).write_bytes(b"packet\ttests\nkit\t1\n")
    monkeypatch.setattr(cc, "collect_counts", lambda root: {"kit": 1})

    assert cc._counts_main(["--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "change control: pass (test counts)\n"


def test_a_full_corpus_member_not_on_disk_has_no_counts(monkeypatch):
    monkeypatch.delenv(cc.CORPUS_ENV, raising=False)

    assert cc._counts_of(_tree({}), "member") == {}


class _Clock(datetime):
    """23:30 UTC on Jan 1, which is already Jan 2 in a zone 5 hours east."""

    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)
        return moment.astimezone(tz or timezone(timedelta(hours=5)))


def test_today_is_the_utc_date(monkeypatch):
    monkeypatch.setattr(cc, "datetime", _Clock)

    assert cc._today() == "2026-01-01"


@pytest.mark.parametrize("name", ["accuracy", "accuracy.coverage_oracles"])
def test_a_tree_without_the_counts_packet_has_no_counts_module(monkeypatch, name):
    def missing(module):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(cc.importlib, "import_module", missing)

    assert cc.counts_module() is None


def test_a_counts_module_missing_something_else_is_an_error(monkeypatch):
    def missing(module):
        raise ModuleNotFoundError("No module named 'yaml'", name="yaml")

    monkeypatch.setattr(cc.importlib, "import_module", missing)

    with pytest.raises(ModuleNotFoundError):
        cc.counts_module()


def test_the_tests_environment_writes_no_bytecode(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)

    assert cc._tests_env(tmp_path, {})["PYTHONDONTWRITEBYTECODE"] == "1"


# --- declare's pieces ----------------------------------------------------------------------

def _declare_with(monkeypatch, note: str) -> list:
    seen = []
    monkeypatch.setattr(cc, "regenerate", lambda root, corpus: seen.append(corpus) or note)
    monkeypatch.setattr(cc, "running", lambda tree, lizard: cc.Running("7", "1.24.0"))
    monkeypatch.setattr(cc, "_planned", lambda *args: _plan())
    monkeypatch.setattr(cc, "write_plan", lambda *args: None)
    monkeypatch.setattr(cc, "summary", lambda plan, now: "S")
    monkeypatch.setattr(cc, "full_corpus_line", lambda corpus: f"F {corpus}")
    return seen


def test_declare_remeasures_the_corpus_it_is_handed_and_says_so(monkeypatch, tmp_path):
    seen = _declare_with(monkeypatch, "")
    request = _plan().request

    assert cc.declare(tmp_path, request, corpus=Path("c")) == "S\nF c"
    assert cc.declare(tmp_path, request, regenerate_goldens=False) == "S"
    assert seen == [Path("c")]


def test_declare_without_a_regenerator_says_why_and_no_full_line(monkeypatch, tmp_path):
    _declare_with(monkeypatch, "no regenerator")

    assert cc.declare(tmp_path, _plan().request) == "no regenerator\nS"


# --- the processes it starts -----------------------------------------------------------------

def test_collect_counts_runs_pytest_without_the_repo_s_addopts(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(cc, "_process", lambda label, argv, seconds, cwd, env: started.append(
        (argv, seconds, env[cc.tiers.COLLECT_ALL_ENV])) or "tests/accuracy/kit/test_x.py::t\n")

    assert cc.collect_counts(tmp_path) == {"kit": 1}
    [(argv, seconds, collect_all)] = started
    assert (argv[3:5], seconds, collect_all) == (["-o", "addopts="], cc.GIT_SECONDS * 5, "1")


def test_a_node_script_runs_under_its_timeout(monkeypatch):
    started = []
    monkeypatch.setattr(cc, "_process", lambda *args: started.append(args) or "{}")

    cc._node(Path("s.cjs"), "a")

    assert started == [("node s.cjs", ["node", "s.cjs", "a"], cc.NODE_SECONDS)]


def test_git_refuses_to_start_from_a_test_without_the_process_marker():
    with pytest.raises(AssertionError, match="^this test spawns git: mark it"):
        cc.git(Path("."), "status")


@pytest.mark.process
def test_git_runs_under_its_timeout_and_reads_a_failure_s_bytes_as_they_are(monkeypatch):
    started = []
    monkeypatch.setattr(cc.subprocess, "run", lambda argv, **kw: started.append(
        kw["timeout"]) or subprocess.CompletedProcess(argv, 1, b"", b"bad \xff byte"))

    with pytest.raises(cc.ChangeControlError) as failed:
        cc.git(Path("."), "status")

    assert str(failed.value) == "git status exited 1: bad \ufffd byte"
    assert started == [cc.GIT_SECONDS]


@pytest.mark.process
def test_a_process_s_output_is_read_as_utf8_whatever_the_locale():
    script = "import sys; sys.stdout.buffer.write('caf\\u00e9'.encode('utf-8'))"

    assert cc._process("python", [sys.executable, "-c", script], 60) == "caf\u00e9"


def test_a_batch_of_blobs_is_read_header_by_header(monkeypatch):
    """`git cat-file --batch` prints `<id> blob <size>`, the bytes, then a newline."""
    monkeypatch.setattr(cc, "git", lambda repo, *args, stdin=None: b"a blob 1\nX\nb blob 1\nY\n")

    assert cc._cat_blobs(Path("."), ["a", "b"]) == {"a": b"X", "b": b"Y"}


@pytest.mark.nightly
@pytest.mark.process
def test_the_report_names_both_commits_by_12_hex(make_repo):
    top = seeds.seeded(make_repo, seeds.base(), seeds.module_changed(seeds.base()))
    base, head = (cc.git(top, "rev-parse", ref).decode().strip() for ref in ("HEAD~1", "HEAD"))

    _, text = cc.check(top, "HEAD~1")

    assert f"(HEAD~1 at {base[:12]} to HEAD ({head[:12]}))" in text.splitlines()[0]


def _git_repo(top: Path) -> list[str]:
    """Two commits; the second holds a/x.txt and y.txt, one byte each."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=top, capture_output=True, text=True,
                              check=True).stdout.strip()
    run("init", "-q")
    (top / "a").mkdir()
    shas = []
    for text in ("0", "1"):
        (top / "a" / "x.txt").write_bytes(text.encode())
        (top / "y.txt").write_bytes(b"2")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", text)
        shas.append(run("rev-parse", "HEAD"))
    return shas


@pytest.mark.process
def test_a_git_tree_lists_every_blob_from_a_subdirectory_and_reads_them_in_one_batch(tmp_path):
    _, head = _git_repo(tmp_path)

    found = cc._ls_tree(tmp_path / "a", "HEAD")

    assert sorted(found) == ["a/x.txt", "y.txt"]
    assert cc._cat_blobs(tmp_path, [found["a/x.txt"], found["y.txt"]]) == {
        found["a/x.txt"]: b"1", found["y.txt"]: b"2"}
    assert cc.GitTree(tmp_path, "HEAD").label == f"HEAD ({head[:12]})"


@pytest.mark.process
def test_a_range_names_no_one_commit(tmp_path):
    _git_repo(tmp_path)

    with pytest.raises(cc.ChangeControlError, match="names no commit here"):
        cc.resolve(tmp_path, "HEAD~1..HEAD")
