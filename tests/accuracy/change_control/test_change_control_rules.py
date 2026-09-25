"""Change control's verdict on seeded repos: each rule fails its two-commit repo,
and a declared change passes.

This is the independent test of the calc "Change-control verdict". Each
scenario's expected rule set is written from the rules as the accuracy plan
states them (tools/accuracy/change_control.py's docstring repeats them), before
the tool ran: an edited metric-digests row breaks B1, a relock under an old
change breaks B5, and so on. The goldens' numbers come from the README's CRAP
definition and from radon and complexipy, worked by hand in cc_seeds.

Three methods: hand (the scenario table on real git repos), model (a Hypothesis
draw of independent edits whose expected rules follow from the plan's text, run
on in-memory trees) and metamorphic (a diff split over two commits gets the
verdict it gets as one).
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import re

from hypothesis import given, strategies as st
import pytest

from accuracy.change_control import cc_seeds as seeds
from accuracy.kit import repos
from accuracy.kit.settings import pure

REPO = Path(__file__).resolve().parents[3]
cc = seeds.tool()

LIZARD = "1.24.0"
BASE = seeds.base()
BASE_CCN8 = seeds.base(seeds.ccn8())


def _tree(files: dict[str, str]) -> cc.DictTree:
    return cc.DictTree({path: text.encode("utf-8") for path, text in files.items()})


def pure_rules(base: dict, head: dict) -> set[str]:
    problems, _ = cc.verdict(_tree(base), _tree(head), cc.running(_tree(head), LIZARD))
    return {problem.rule for problem in problems}


# --- the scenarios -------------------------------------------------------------------------

def _edited_metric_row(tree):
    return seeds.replace(tree, seeds.DIGESTS, "0123456789abcdef", "fedcba9876543210")


def _golden_relocked_under_an_old_change(tree):
    return seeds.relock(seeds.replace(tree, seeds.WORKLIST, "12", "13"), "C2", seeds.WORKLIST)


def _golden_changed_lock_untouched(tree):
    return seeds.replace(tree, seeds.WORKLIST, "12", "13")


def _defect_to_fixed_with_no_fix_change(tree, key="C3"):
    head = seeds.replace(tree, seeds.RULINGS, "\t2\t1\tdefect\t", "\t1\t1\tfixed\t")
    head = seeds.change(head, key, "none", "", reason="the ?? row now agrees")
    return seeds.relock(head, key, seeds.RULINGS)


def _deleted_rulings_row(tree, key="C3"):
    line = next(line for line in tree[seeds.RULINGS].splitlines() if line.startswith("R-D5\t"))
    head = seeds.replace(tree, seeds.RULINGS, line + "\n", "")
    head = seeds.change(head, key, "none", "", reason="the tie ruling is gone")
    return seeds.relock(head, key, seeds.RULINGS)


def _declared_definition(tree):
    head = seeds.fixed_crap(tree, kind="definition", bug=False)
    head = seeds.append(head, seeds.RULINGS, "R-F1", "CRAP score", "kit.exact",
                        "a function every test reaches", "1.0", "1.0", "definition",
                        "https://www.artima.com/weblogs/viewpost.jsp?thread=210575#crap",
                        "README.md#crap", f"{seeds.SEED_TEST}::test_crap", "")
    head = seeds.moved(head, "C3", [(seeds.SCORED, "src/a.py", "f1", "crap", "1.5", "1.0",
                                     "kit.exact", "1.0", "R-F1")])
    head = seeds.append(head, seeds.HAND, 1, "1.0", "1.0", "Savoia and Evans (2007): cov 1 "
                                                           "leaves ccn")
    head = seeds.relock(head, "C3", seeds.RULINGS, seeds.HAND)
    return seeds.replace(head, "README.md", "+ ccn.", "+ ccn; full coverage leaves ccn.")


FAILING = {
    "an edited metric-digests row": (BASE, _edited_metric_row, {"B1"}),
    "a golden relocked under an old change": (BASE, _golden_relocked_under_an_old_change,
                                              {"B5", "B10"}),
    "a golden changed with the lock untouched": (BASE, _golden_changed_lock_untouched,
                                                 {"T2", "B10"}),
    "a fix row with no bugs row": (BASE, lambda tree: seeds.fixed_crap(tree, bug=False), {"B7"}),
    "defect to fixed with no fix row": (BASE, _defect_to_fixed_with_no_fix_change, {"B9"}),
    "a moved calc not declared": (BASE_CCN8,
                                  lambda tree: seeds.fixed_ccn(tree, calcs="CRAP score"),
                                  {"B10"}),
    "over-declared calcs": (BASE, lambda tree: seeds.fixed_crap(
        tree, calcs="CRAP score; Cognitive complexity"), {"B10"}),
    "a definition with no docs or rulings change": (BASE, lambda tree: seeds.fixed_crap(
        tree, kind="definition", bug=False), {"B8"}),
    "a deleted rulings row": (BASE, _deleted_rulings_row, {"B2"}),
    "a lowered floor": (BASE, lambda tree: seeds.replace(tree, seeds.FLOORS, "\t95", "\t90"),
                        {"B3"}),
    "a dropped test count": (BASE, lambda tree: seeds.replace(tree, seeds.COUNTS, "\t3", "\t2"),
                             {"B4"}),
    "a calc-module diff with no change row": (BASE, seeds.module_changed, {"B6"}),
    "a calc-module diff under a change naming another module's calc": (
        BASE_CCN8, lambda tree: seeds.module_changed(seeds.fixed_ccn(tree)), {"B6"}),
    "an analyze.py edit beyond the version bump under a CRAP fix": (
        BASE, lambda tree: seeds.reader_changed(seeds.fixed_crap(tree)), {"B6"}),
}
CLEAN = {
    "a declared fix": (BASE, seeds.fixed_crap),
    "a declared ccn fix": (BASE_CCN8, seeds.fixed_ccn),
    "a declared definition": (BASE, _declared_definition),
    "a module refactor declared as none": (BASE, lambda tree: seeds.change(
        seeds.module_changed(tree), "C3", "none", "", reason="a comment, nothing moves")),
    "a CRAP fix in score.py with the ANALYSIS_VERSION bump": (
        BASE, lambda tree: seeds.module_changed(seeds.fixed_crap(tree))),
    "a fix of a calc no golden shows": (BASE, seeds.fixed_gate),
    "a none change next to a fix": (BASE_CCN8, lambda tree: seeds.change(
        seeds.module_changed(seeds.fixed_ccn(tree)), "C4", "none", "",
        reason="a comment in score.py")),
}



# --- hand: each scenario on a real two-commit repo ------------------------------------------

def _rules(text: str) -> set[str]:
    return set(re.findall(r"^(T\d|B\d+) ", text, re.M))


def git_verdict(make_repo, *trees: dict) -> tuple[int, str]:
    top = seeds.seeded(make_repo, *trees)
    return cc.check(top, f"HEAD~{len(trees) - 1}", "HEAD", lizard=LIZARD)


@pytest.mark.process
@pytest.mark.parametrize("name", sorted(FAILING))
def test_each_rule_fails_its_seeded_repo(make_repo, name):
    base, edit, expected = FAILING[name]

    code, text = git_verdict(make_repo, base, edit(base))

    assert (code, _rules(text)) == (1, expected), text
    assert "moved calcs:" in text and "  fix: " in text


@pytest.mark.process
@pytest.mark.parametrize("name", sorted(CLEAN))
def test_a_declared_change_passes_its_seeded_repo(make_repo, name):
    base, edit = CLEAN[name]

    code, text = git_verdict(make_repo, base, edit(base))

    assert (code, text.splitlines()[0][:21]) == (0, "change control: pass "), text


def _many_moved(tree):
    rows = seeds.scored_rows(f_cov="0.5", f_crap={k: "1.125" for k in range(1, 12)})
    return seeds.module_changed({**tree, seeds.SCORED: seeds.scored(rows)})


@pytest.mark.process
def test_a_failure_prints_the_moved_calcs_the_first_ten_rows_and_the_fix(make_repo):
    """f1 to f11 lose half their coverage: 11 cov cells and 11 CRAP cells, (1 - 0.5)^3 + 1
    = 1.125 each, under a module edit nobody declared."""
    code, text = git_verdict(make_repo, BASE, _many_moved(BASE))

    assert code == 1
    assert "moved calcs: CRAP score (11 cells), Function coverage ratio (11 cells)" in text
    assert "moved rows (first 10 of 22):" in text
    shown = [line for line in text.splitlines() if line.startswith(f"  {seeds.SCORED}\t")]
    assert len(shown) == 10
    assert f"  {seeds.SCORED}\tsrc/a.py\tf1\tcov\t1.0\t0.5\t-" in shown
    assert f"  {seeds.SCORED}\tsrc/a.py\tf10\tcrap\t1.0\t1.125\tkit.exact 1.125" in shown
    assert ('fix: python tools/accuracy/change_control.py declare C3 --kind fix --calcs '
            '"Function coverage ratio"') in text
    assert {"B6", "B10", "T2", "T5"} <= _rules(text)


# --- metamorphic: one diff in two commits ----------------------------------------------------

def _halves(base: dict, head: dict) -> dict:
    """base with the first half (by path) of the diff to head applied."""
    delta = sorted(seeds.delta(base, head).items())
    first = dict(delta[:len(delta) // 2])
    tree = {**base, **{path: text for path, text in first.items() if text is not None}}
    return {path: text for path, text in tree.items() if first.get(path, "") is not None}


SPLIT = ("a fix row with no bugs row", "a moved calc not declared", "a deleted rulings row")


@pytest.mark.process
@pytest.mark.parametrize("name", [*SPLIT, "a declared fix"])
def test_a_diff_split_over_two_commits_gets_the_same_verdict(make_repo, name):
    base, edit, *_ = FAILING.get(name) or CLEAN[name]
    head = edit(base)

    whole = git_verdict(make_repo, base, head)
    split = git_verdict(make_repo, base, _halves(base, head), head)

    assert (split[0], _rules(split[1])) == (whole[0], _rules(whole[1]))


# --- model: independent edits drawn together --------------------------------------------------

EDITS = {
    "metric": _edited_metric_row,
    "floor": lambda tree: seeds.replace(tree, seeds.FLOORS, "\t95", "\t90"),
    "count": lambda tree: seeds.replace(tree, seeds.COUNTS, "\t3", "\t2"),
    "module": seeds.module_changed,
    "ruling fixed": lambda tree: _defect_to_fixed_with_no_fix_change(tree, "C4"),
    "ruling deleted": lambda tree: _deleted_rulings_row(tree, "C5"),
    "surface": _golden_relocked_under_an_old_change,
}
FIXES = {"": lambda tree: tree, "fix": seeds.fixed_crap,
         "fix, no bug": lambda tree: seeds.fixed_crap(tree, bug=False)}


# An edit that breaks its rule whatever else the diff holds.
ALWAYS = {"metric": "B1", "floor": "B3", "count": "B4", "ruling fixed": "B9",
          "ruling deleted": "B2", "surface": "B5"}


def _fresh(edits: set[str], fix: str) -> bool:
    """Each ruling edit brings a fresh kind-none change, and a fix a fresh fix."""
    return bool(fix) or bool(edits & {"ruling fixed", "ruling deleted"})


def _b6(edits: set[str], fix: str) -> bool:
    """A module edit with no fresh change at all; a fresh none change covers a
    module edit that moves nothing."""
    return "module" in edits and not _fresh(edits, fix)


def _b10(edits: set[str], fix: str) -> bool:
    """A moved surface needs its own calc unless a table cell of its golden set moved."""
    return "surface" in edits and not fix


CONDITIONAL = (("B6", _b6), ("B10", _b10), ("B7", lambda edits, fix: fix == "fix, no bug"))


def expected_rules(edits: set[str], fix: str) -> set[str]:
    """The plan's rules applied to the drawn edits, read off its text."""
    always = {ALWAYS[edit] for edit in edits if edit in ALWAYS}
    return always | {rule for rule, hit in CONDITIONAL if hit(edits, fix)}


def _apply(edits: set[str], fix: str) -> dict:
    head = FIXES[fix](BASE)
    for name in sorted(edits):
        head = EDITS[name](head)
    return head


@pure
@given(edits=st.sets(st.sampled_from(sorted(EDITS))), fix=st.sampled_from(sorted(FIXES)))
def test_drawn_edits_break_exactly_the_rules_the_plan_names(edits, fix):
    assert pure_rules(BASE, _apply(edits, fix)) == expected_rules(edits, fix)


@pytest.mark.parametrize("name", sorted(FAILING))
def test_each_scenario_gets_the_same_verdict_in_memory(name):
    base, edit, expected = FAILING[name]

    assert pure_rules(base, edit(base)) == expected


# --- more ways to break a rule, judged in memory ------------------------------------------------

def _unlogged_fix(tree):
    return seeds.without_line(seeds.fixed_crap(tree), "CHANGELOG.md", "- a change.")


def _stale_metric_row(tree):
    return seeds.without_line(seeds.fixed_crap(tree), seeds.DIGESTS, "12\t")


def _swapped_metric_rows(tree):
    first, second = tree[seeds.DIGESTS].splitlines()[1:3]
    return seeds.replace(tree, seeds.DIGESTS, f"{first}\n{second}\n", f"{second}\n{first}\n")


def _hand_row_under_another_packets_calc(tree):
    head = seeds.append(tree, seeds.HAND, 1, "1.0", "1.0", "Savoia and Evans (2007)")
    head = seeds.change(head, "C3", "fix", "Churn counts and recency weight")
    return seeds.relock(seeds.changelog(head, "C3"), "C3", seeds.HAND)


def _silent_none(tree):
    return seeds.change(seeds.module_changed(tree), "C3", "none", "", reason="")


def _misquoted_oracle(tree):
    return seeds.fixed_crap(tree, listed=[(seeds.SCORED, "src/a.py", "f1", "crap", "1.5", "1.0",
                                           "kit.exact", "2.0", "")])


def _disagreement_under_a_ruling_of_another_calc(tree):
    """f1's CRAP set to 1.25, which the formula does not give (1.0), cited under the
    ccn ruling R-CCN."""
    return seeds.fixed_crap(tree, new="1.25", listed=[
        (seeds.SCORED, "src/a.py", "f1", "crap", "1.5", "1.25", "kit.exact", "1.0", "R-CCN")])


MORE_FAILING = {
    "an unknown change kind": (lambda tree: seeds.changelog(
        seeds.change(tree, "C3", "tweak", "CRAP score"), "C3"), {"T3"}),
    "a repeated change id": (lambda tree: seeds.change(tree, "C2", "none", "", reason="again"),
                             {"T3"}),
    "a fix missing from the changelog": (_unlogged_fix, {"T4"}),
    "a stale last metric row": (_stale_metric_row, {"T5"}),
    "metric rows out of order": (_swapped_metric_rows, {"T5", "B1"}),
    "a renamed column in bugs.tsv": (lambda tree: seeds.replace(
        tree, seeds.BUGS, "id\tplatform", "bug\tplatform"), {"B2"}),
    "a survivor added with no evidence": (lambda tree: seeds.append(
        tree, seeds.SURVIVORS, "src/crapkit/score.py", "crap", "ab" * 32, ""), {"B3"}),
    "a hand row relocked under another packet's calc": (_hand_row_under_another_packets_calc,
                                                        {"B5", "B7", "B10"}),
    "a bug with no retro row": (lambda tree: seeds.without_line(
        seeds.fixed_crap(tree), seeds.RETRO, "R02\t"), {"B7"}),
    "a none change with no reason": (_silent_none, {"B6"}),
    "golden cells moved under a none change": (lambda tree: seeds.fixed_crap(
        tree, kind="none", calcs="", bug=False), {"B6", "B10"}),
    "moved.tsv misses its cell": (lambda tree: seeds.fixed_crap(tree, listed=[]), {"B11"}),
    "moved.tsv misquotes the oracle": (_misquoted_oracle, {"B11"}),
    "a disagreement under a ruling of another calc": (
        _disagreement_under_a_ruling_of_another_calc, {"B11"}),
}


@pytest.mark.parametrize("name", sorted(MORE_FAILING))
def test_each_further_break_fails_its_rule_in_memory(name):
    edit, expected = MORE_FAILING[name]

    assert pure_rules(BASE, edit(BASE)) == expected


def test_before_the_first_lock_only_the_in_tree_rules_hold():
    """Neither side locked: a calc-module diff passes, a lockable file waits for
    the lock (T2); an emptied CHANGES.tsv on a locked base still fails B2."""
    bare = seeds.uninitialized()
    no_goldens = {path: text for path, text in bare.items() if path not in seeds.LOCKED}
    emptied = {**BASE, seeds.CHANGES: BASE[seeds.CHANGES].splitlines()[0] + "\n"}

    assert pure_rules(no_goldens, seeds.module_changed(no_goldens)) == set()
    assert pure_rules(bare, seeds.module_changed(bare)) == {"T2"}
    assert {"B2"} <= pure_rules(BASE, emptied)


def test_a_survivor_with_evidence_passes_and_is_printed(capsys):

    head = seeds.append(BASE, seeds.SURVIVORS, "src/crapkit/score.py", "crap", "cd" * 32,
                        "10,000 examples equal (mutation.py run 7)")

    assert pure_rules(BASE, head) == set()
    assert "survivors.tsv adds src/crapkit/score.py/crap/cdcd" in capsys.readouterr().out


# --- golden sets, surfaces and the corpus each set measures -------------------------------------

SESSION = "tests/accuracy/corpus_goldens/goldens/session"
CLAIMS = f"{SESSION}/claims.json"
HISTORY = "tests/accuracy/corpus_goldens/goldens/history/scored.tsv"


def _with_claims(tree: dict, text: str, change: str) -> dict:
    return seeds.relock({**tree, CLAIMS: text}, change, CLAIMS)


def _problem_texts(base: dict, head: dict) -> list[str]:
    problems, _ = cc.verdict(_tree(base), _tree(head), cc.running(_tree(head), LIZARD))
    return [f"{problem.rule} {problem.text}" for problem in problems]


def test_a_session_file_that_moves_alone_needs_its_own_calc():
    """claims.json moves under a CRAP fix whose goldens did not move."""
    base = _with_claims(BASE, '{"claims": []}\n', "C1")
    head = seeds.with_bug(seeds.changelog(seeds.change(
        _with_claims(base, '{"claims": [1]}\n', "C3"), "C3", "fix", "CRAP score"), "C3"))

    assert sorted(_problem_texts(base, head)) == [
        "B10 declared calc did not move: CRAP score",
        "B10 moved calc not declared: Claim ownership and closing"]


def test_a_session_file_moves_with_a_small_corpus_row_and_needs_nothing_more():
    """The session set measures the small corpus, so f1's moved CRAP explains it."""
    base = _with_claims(BASE, '{"claims": []}\n', "C1")
    head = _with_claims(seeds.fixed_crap(base), '{"claims": [1]}\n', "C3")

    assert _problem_texts(base, head) == []


@pytest.mark.parametrize("path, calcs", [
    (CLAIMS, ("Claim ownership and closing",)),
    (f"{SESSION}/verify.json.stderr", ("verify gate violations",
                                       "Verdict exit code and dirty split")),
    (f"{SESSION}/worklist-batches.json", ("Batch split",)),
    (f"{SESSION}/mcp-get_trend.json", ("MCP tool results",)),
    (f"{SESSION}/something-new.bin", ("Inventory rows and TSV exports",)),
])
def test_each_surface_maps_to_its_calc(path, calcs):
    assert cc.surface_calcs(path)[:len(calcs)] == calcs


def test_a_history_row_is_judged_on_the_small_corpus_source(oracle):
    """The history bundle's last commit is the small corpus, so radon reads parse there."""
    oracle("radon")
    tree = _tree({**BASE, HISTORY: seeds.scored(seeds.ccn8())})
    cell = cc.Cell(HISTORY, "src/a.py", "parse", "ccn", "7", "8")

    judged = cc.judge(tree, cell)

    assert (judged.oracle, judged.value, judged.agrees) == ("radon", "7", False)


def test_a_member_set_reads_the_full_corpus(tmp_path, monkeypatch, oracle):
    oracle("radon")
    member = tmp_path / "requests" / "src" / "a.py"
    member.parent.mkdir(parents=True)
    member.write_text(seeds.PARSE.replace("    return text\n", ""), encoding="utf-8")
    monkeypatch.setenv(cc.CORPUS_ENV, str(tmp_path))
    golden = "tests/accuracy/corpus_goldens/goldens/requests/scored.tsv"
    tree = _tree({cc.CORPUS_TOML: '[small]\n\n[member.requests]\ncommit = "x"\n',
                  golden: seeds.scored(seeds.scored_rows())})

    judged = cc.judge(tree, cc.Cell(golden, "src/a.py", "parse", "ccn", "8", "7"))

    assert (cc.corpus_name(tree, golden), judged.oracle, judged.value) == ("requests", "radon", "7")
    assert cc.corpus_name(tree, "tests/accuracy/corpus_goldens/goldens/history/x") == "small"


def test_an_oracle_that_finds_no_function_at_the_start_line_answers_nothing(oracle):
    oracle("radon")
    oracle("complexipy")
    tree = _tree(BASE)
    cell = cc.Cell(seeds.SCORED, "src/a.py", "parse", "ccn", "8", "7")
    moved = cc.Cell(seeds.SCORED, "src/a.py", "f1", "cognitive", "0", "1")

    assert cc.radon_ccn({"start": "2"}, seeds.SMALL) is None
    assert cc.radon_ccn({"start": "1"}, "def parse(:\n") is None
    assert cc.complexipy_cognitive({"start": "1"}, "def parse(:\n") is None
    assert cc.judge(tree, cell).value == "7"
    assert cc.judge(tree, moved).value == "0"


class _CountsTable:
    """A stand-in for the coverage packet's counts table: f1 (start 14) with one
    row, f2 (start 18) measured by two lanes; every ratio 1/2."""

    def __init__(self):
        self.read = []

    def table(self, directory: Path) -> dict:
        self.read.append(sorted(path.relative_to(directory).as_posix()
                                for path in directory.rglob("*") if path.is_file()))
        return {("src/a.py", 14): ["f1"], ("src/a.py", 18): ["f2 py", "f2 js"]}

    @staticmethod
    def ratio(counts) -> Fraction:
        return Fraction(1, 2)


def test_a_cov_cell_is_judged_by_the_counts_table_on_the_written_out_corpus(monkeypatch):
    stand_in = _CountsTable()
    monkeypatch.setattr(cc, "counts_module", lambda: stand_in)
    tree = _tree(BASE)
    f1 = cc.judge(tree, cc.Cell(seeds.SCORED, "src/a.py", "f1", "cov", "1.0", "0.75"))
    f2 = cc.judge(tree, cc.Cell(seeds.SCORED, "src/a.py", "f2", "cov", "1.0", "0.5"))

    assert (f1.oracle, f1.value, f1.agrees) == ("counts table", "0.5", False)
    assert cc._refusal(f1).startswith("crapkit now says 0.75, counts table says 0.5 at "
                                      "src/a.py:f1 (cov)")
    assert (f2.oracle, f2.value) == ("", "")
    assert stand_in.read == [["src/a.py"]]


@pytest.mark.parametrize("row, crap", [
    ({"ccn": "3", "cov": "0.0", "flag": "cc-only"}, "3.0"),
    ({"ccn": "3", "cov": "0.0", "flag": "untested"}, "12.0"),
    ({"ccn": "7", "cov": "0.5", "flag": "measured"}, "13.125"),
    ({"ccn": "7", "cov": "nan?"}, None),
])
def test_the_crap_oracle_follows_the_readme_formula_and_the_cc_only_flag(row, crap):
    """README: CRAP = ccn^2 (1 - cov)^3 + ccn (9 * 1 + 3 = 12, 49 * 0.125 + 7 =
    13.125), and a cc-only row scores crap = ccn."""
    assert cc.exact_crap(row, None) == crap


def test_an_untested_or_no_lane_row_reads_cov_0_without_the_counts_table(monkeypatch):
    monkeypatch.setattr(cc, "counts_module", lambda: pytest.fail("the table was read"))

    for flag in ("untested", "no-lane"):
        assert cc.counts_cov(_tree(BASE), cc.Cell(seeds.SCORED, "src/a.py", "f1", "cov", "", ""),
                             {"start": "14", "flag": flag}) == "0.0"


def test_without_the_counts_table_a_cov_cell_has_no_oracle():

    judged = cc.judge(_tree(BASE), cc.Cell(seeds.SCORED, "src/a.py", "f1", "cov", "1.0", "0.5"))

    assert cc.counts_module() is None or callable(cc.counts_module().table)
    assert cc.counts_module() is not None or (judged.oracle, judged.value) == ("", "")


# --- the row oracle: Python's ast on a function that appears or goes ---------------------------

F11 = "\n\ndef f11(x):\n    return x\n"


def _without_f11(tree: dict, source: bool) -> dict:
    """f11's row gone from both goldens; with `source`, its def gone from src/a.py too."""
    rows = [row for row in seeds.scored_rows() if row["long_name"] != "f11( x )"]
    head = {**tree, seeds.SCORED: seeds.scored(rows), seeds.INVENTORY: seeds.inventory(rows)}
    return seeds.replace(head, seeds.SOURCE, F11, "") if source else head


def test_a_row_that_goes_moves_one_row_cell_per_golden_table():
    cells = cc.moved_cells(_tree(BASE), _tree(_without_f11(BASE, source=False)))

    assert [cell.key() for cell in cells] == [
        (golden, "src/a.py", "f11", "row", "present", "absent")
        for golden in (seeds.INVENTORY, seeds.SCORED)]


@pytest.mark.parametrize("source, value", [(False, "present"), (True, "absent")])
def test_ast_judges_a_row_that_goes_at_its_base_start_line(source, value):
    """f11's def stands at line 54 of src/a.py (F_START): ast finds it there while
    the source keeps it, and finds nothing once the source drops it too."""
    head = _tree(_without_f11(BASE, source))
    cell = cc.Cell(seeds.SCORED, "src/a.py", "f11", "row", "present", "absent")

    judged = cc.judge(head, cell, _tree(BASE))

    assert (judged.oracle, judged.value, judged.agrees) == ("ast", value, value == "absent")


def test_ast_refuses_a_new_row_where_the_source_has_no_def_of_that_name():
    """A row g at line 2, which is parse's `if not text:`: no def g starts there."""
    rows = seeds.scored_rows()
    rows.append({**rows[1], "long_name": "g( x )", "start": 2, "end": 3})
    head = _tree({**BASE, seeds.SCORED: seeds.scored(rows), seeds.INVENTORY: seeds.inventory(rows)})

    judged = cc.judge(head, cc.Cell(seeds.SCORED, "src/a.py", "g", "row", "absent", "present"))
    kept = cc.judge(head, cc.Cell(seeds.SCORED, "src/a.py", "f11", "row", "absent", "present"))

    assert (judged.oracle, judged.value, judged.agrees) == ("ast", "absent", False)
    assert (kept.oracle, kept.value, kept.agrees) == ("ast", "present", True)


def test_ast_answers_nothing_on_a_source_it_cannot_parse():
    head = seeds.replace(_without_f11(BASE, source=False), seeds.SOURCE, "strict):", "strict)")
    cell = cc.Cell(seeds.SCORED, "src/a.py", "f11", "row", "present", "absent")

    judged = cc.judge(_tree(head), cell, _tree(BASE))

    assert (judged.oracle, judged.value, judged.agrees) == ("", "", True)


@pytest.mark.parametrize("text, oracle, hit", [

    ("radon", "radon", True), ("radon 6.0.1 cc_visit", "radon", True),
    ("kit.exact half-even", "kit.exact", True), ("Radon 6.0.1", "radon", True),
    ("radon_mccabe", "radon", False), ("hand: NIST SP 500-235", "radon", False),
    ("kit.exactly", "kit.exact", False), ("", "radon", False), ("radon", "", False),
])
def test_a_rulings_oracle_cell_names_the_oracle_as_a_word(text, oracle, hit):
    assert cc.names_oracle(text, oracle) is hit


def test_a_version_only_analyze_edit_touches_no_calc():
    bumped = seeds.bump(BASE, "12")
    rows = cc.calcs_of(_tree(BASE))

    assert cc.touched_calcs(rows, {seeds.ANALYZE}, _tree(BASE), _tree(bumped)) == {}
    assert cc.touched_calcs(rows, {seeds.ANALYZE}, _tree(BASE),
                            _tree(seeds.reader_changed(bumped))) == {
        seeds.ANALYZE: ["Cognitive complexity", seeds.CCN]}


# --- pieces the verdict rests on -----------------------------------------------------------------


def test_the_metric_digest_is_the_documented_fields_hashed():
    """One scored row, its digest line written out from the docstring's field order:
    path, handle, ccn_std, ccn_mod, ccn, cognitive, nesting, nloc, params, start,
    end, CRAP at 4 dp (half even) and cov."""
    row = seeds.scored_rows()[:1]
    line = "src/a.py\tparse\t7\t7\t7\t8\t2\t11\t2\t1\t11\t13.1250\t0.5"

    tree = _tree({seeds.SCORED: seeds.scored(row)})

    assert cc.metric_digest(tree) == hashlib.sha256(line.encode()).hexdigest()[:16]


@pytest.mark.parametrize("pattern, path, hit", [
    ("tests/accuracy/*/goldens/**", "tests/accuracy/corpus_goldens/goldens/small/a.tsv", True),
    ("tests/accuracy/*/goldens/**", "tests/accuracy/corpus_goldens/x/goldens.tsv", False),
    ("tests/accuracy/*/probes/**/probes.tsv", "tests/accuracy/analysis_oracles/probes/probes.tsv",
     True),
    ("tests/accuracy/*/probes/**/probes.tsv", "tests/accuracy/a/probes/py/deep/probes.tsv", True),
    ("tests/accuracy/*/rulings.tsv", "tests/accuracy/a/b/rulings.tsv", False),
    ("tests/accuracy/suite_strength/retro/probes/R*.py",
     "tests/accuracy/suite_strength/retro/probes/R12.py", True),
    ("tests/accuracy/**/floors.tsv", "tests/accuracy/floors.tsv", True),
])
def test_patterns_keep_star_inside_one_directory(pattern, path, hit):
    assert cc.matches(path, pattern) is hit


def test_calcs_typed_with_commas_keep_the_names_that_hold_one():
    known = {"CRAP score", seeds.CCN, "Parameter list (params, packet.params)"}

    parsed = cc.parse_calcs("CRAP score,ccn_std, ccn_mod and gated ccn,"
                            "Parameter list (params, packet.params)", known)

    assert parsed == sorted(known)
    assert cc.parse_calcs("CRAP score; Remedy label", known) == ["CRAP score", "Remedy label"]


def test_a_stopped_measurement_is_one_skip_line(tmp_path):
    (tmp_path / "candidate").mkdir()
    (tmp_path / "candidate" / "failure.json").write_text(
        '{"phase": "install", "error": "no wheel"}', encoding="utf-8")

    assert cc.measurement_stopped(tmp_path) == ("change control: skipped, the candidate "
                                                "measurement stopped in install: no wheel")
    assert cc.measurement_stopped(None) is None
    assert cc.measurement_stopped(tmp_path / "absent") is None


@pytest.mark.process
def test_check_exits_0_with_the_skip_line_on_a_stopped_measurement(tmp_path, capsys):
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "failure.json").write_text('{"phase": "measure", "error": "boom"}',
                                                    encoding="utf-8")

    code = cc.main(["--base", "no-such-ref", "--measured", str(tmp_path)])

    assert code == 0
    assert capsys.readouterr().out.strip() == ("change control: skipped, the base measurement "
                                               "stopped in measure: boom")


def test_the_pushed_heads_are_every_line_that_deletes_nothing():
    zero = "0" * 40
    stdin = (f"refs/heads/a {'1' * 40} refs/heads/a {zero}\n"
             f"(delete) {zero} refs/heads/b {'2' * 40}\n"
             f"refs/heads/c {'3' * 40} refs/heads/c {'4' * 40}\n")

    assert cc.pushed_heads(stdin) == ["1" * 40, "3" * 40]


# --- declare --------------------------------------------------------------------------------------

def _working(make_repo, base: dict, head: dict):
    """A repo holding `base` as its one commit and `head` in its working tree."""
    top = seeds.seeded(make_repo, base)
    seeds.write(top, base, head)
    return top


def _request(kind="fix", calcs=(seeds.CCN,), against=(), key="C3"):
    return cc.Request(key, kind, tuple(calcs), "parse's ccn moved", tuple(against), "2026-09-24")


def _ccn9(tree: dict) -> dict:
    """parse at ccn 9, which radon does not give it: 81 * 0.125 + 9 = 19.125."""
    rows = seeds.scored_rows(parse_ccn=9, parse_crap="19.125")
    return seeds.bump({**tree, seeds.SCORED: seeds.scored(rows),
                       seeds.INVENTORY: seeds.inventory(rows)}, "12")


def _declare(top: Path, request) -> str:
    return cc.declare(top, request, "HEAD", regenerate_goldens=False, lizard=LIZARD)


@pytest.mark.process
def test_declare_refuses_a_move_the_oracle_disagrees_with(make_repo, oracle):
    oracle("radon")
    top = _working(make_repo, BASE, _ccn9(BASE))

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request())

    assert "crapkit now says 9, radon says 7 at src/a.py:parse (ccn)" in str(refused.value)
    assert "--against-oracle <ruling-id>" in str(refused.value)
    assert not (top / cc.MOVED).exists()


@pytest.mark.process
def test_declare_takes_a_disagreement_a_named_ruling_covers(make_repo, oracle):
    oracle("radon")
    top = _working(make_repo, BASE, _ccn9(BASE))

    text = _declare(top, _request(against=("R-D5", "R-CCN")))

    moved = cc.rows((top / cc.MOVED / "C3.moved.tsv").read_bytes())
    assert {(row["column"], row["oracle"], row["oracle_value"], row["ruling"]) for row in moved} \
        == {(column, "radon", "7", "R-CCN") for column in ("ccn", "ccn_mod", "ccn_std")} \
        | {("crap", "kit.exact", "19.125", "")}
    assert "ruling(s) R-CCN cover a difference" in text


@pytest.mark.process
def test_declare_refuses_a_crap_the_formula_does_not_give(make_repo):
    head = seeds.replace(BASE, seeds.SCORED, "\tf2( x )\t18\t19\t1\t1\t1\t2\t1\t0\t1.0\tmeasured"
                         "\t1.0\t", "\tf2( x )\t18\t19\t1\t1\t1\t2\t1\t0\t1.0\tmeasured\t1.5\t")
    top = _working(make_repo, BASE, seeds.bump(head, "12"))

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request(calcs=("CRAP score",)))

    assert "crapkit now says 1.5, kit.exact says 1.0 at src/a.py:f2 (crap)" in str(refused.value)


@pytest.mark.process
def test_declare_asks_for_the_analysis_bump_a_moved_digest_needs(make_repo):
    head = {**BASE_CCN8, **{path: seeds.base()[path] for path in (seeds.SCORED, seeds.INVENTORY)}}
    top = _working(make_repo, BASE_CCN8, head)

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request())

    assert ("bump ANALYSIS_VERSION in src/crapkit/analyze.py to 12 (A1), then rerun this "
            "declare") in str(refused.value)


@pytest.mark.process
def test_declare_refuses_undeclared_and_over_declared_calcs_and_a_none_that_moves(make_repo):
    head = seeds.bump({**BASE_CCN8, **{path: BASE[path] for path in (seeds.SCORED,
                                                                     seeds.INVENTORY)}}, "12")
    top = _working(make_repo, BASE_CCN8, head)

    with pytest.raises(cc.ChangeControlError) as wrong:
        _declare(top, _request(calcs=("CRAP score", "Cognitive complexity")))
    with pytest.raises(cc.ChangeControlError) as none:
        _declare(top, _request(kind="none", calcs=()))

    assert "moved calc not declared: Python reader: spans, names, inline_body, unread-def net " \
           "or ccn_std, ccn_mod and gated ccn" in str(wrong.value)
    assert "declared calc did not move: Cognitive complexity" in str(wrong.value)
    assert "7 golden cells or files moved; kind none declares a change that moves nothing" \
        in str(none.value)


@pytest.mark.process
def test_a_declared_and_committed_move_passes_the_check(make_repo, oracle):
    """parse's ccn goes from 8 to 7, where radon puts it; declare records it, the
    author adds the CHANGELOG line, the bug and its retro row, and the push passes."""
    oracle("radon")
    head = seeds.bump({**BASE_CCN8, **{path: BASE[path] for path in (seeds.SCORED,
                                                                     seeds.INVENTORY)}}, "12")
    top = _working(make_repo, BASE_CCN8, head)

    text = _declare(top, _request())
    tree = {path: (top / path).read_text(encoding="utf-8") for path in cc.DirTree(top).paths()}
    tree = seeds.with_bug(seeds.changelog(tree, "C3"))
    for path in (seeds.BUGS, seeds.RETRO, "CHANGELOG.md"):
        (top / path).write_bytes(tree[path].encode("utf-8"))
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "fix parse's ccn", date=repos.EPOCH + 120)

    code, verdict = cc.check(top, "HEAD~1", "HEAD", lizard=LIZARD)
    assert "7 golden cells moved, 7 judged by an oracle" in text
    assert "- parse's ccn moved. (accuracy change C3)" in text
    assert code == 0, verdict


@pytest.mark.process
def test_declare_refuses_a_row_that_goes_while_its_def_stays(make_repo):
    top = _working(make_repo, BASE, seeds.bump(_without_f11(BASE, source=False), "12"))

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request(calcs=(cc.SPAN,)))

    text = str(refused.value)
    assert "crapkit now says absent, ast says present at src/a.py:f11 (row)" in text
    assert f"  {seeds.SCORED}\tsrc/a.py\tf11\trow\tpresent\tabsent\tast present" in text


@pytest.mark.process
def test_a_row_that_goes_with_its_def_is_declared_and_passes_the_check(make_repo):
    """f11 leaves the corpus and both goldens: ast finds no def at line 54, so the
    two row cells agree, and the check re-judges them against the base's rows."""
    top = _working(make_repo, BASE, seeds.bump(_without_f11(BASE, source=True), "12"))

    text = _declare(top, _request(kind="feature", calcs=(cc.SPAN,)))
    tree = seeds.changelog({path: (top / path).read_text(encoding="utf-8")
                            for path in cc.DirTree(top).paths()}, "C3")
    (top / "CHANGELOG.md").write_bytes(tree["CHANGELOG.md"].encode("utf-8"))
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "drop f11", date=repos.EPOCH + 120)

    code, verdict = cc.check(top, "HEAD~1", "HEAD", lizard=LIZARD)
    moved = cc.rows((top / cc.MOVED / "C3.moved.tsv").read_bytes())
    assert "2 golden cells moved, 2 judged by an oracle" in text
    assert {(row["column"], row["oracle"], row["oracle_value"]) for row in moved} == {
        ("row", "ast", "absent")}
    assert code == 0, verdict


@pytest.mark.process
def test_declare_none_records_a_change_that_moves_nothing(make_repo):
    top = _working(make_repo, BASE, seeds.module_changed(BASE))
    lock = (top / cc.LOCK).read_bytes()

    text = _declare(top, _request(kind="none", calcs=()))

    assert text.startswith("declared C3 (none: no calc): 0 locked files relocked")
    assert (top / cc.LOCK).read_bytes() == lock
    assert cc.changes_of(cc.DirTree(top))["C3"]["kind"] == "none"


def _cognitive9(tree: dict) -> dict:
    """parse's cognitive at 9, where complexipy gives 8."""
    head = seeds.replace(tree, seeds.SCORED, "add-tests\t8\t1", "add-tests\t9\t1")
    return seeds.bump(seeds.replace(head, seeds.INVENTORY, "\t2\t2\t8\t1\n", "\t2\t2\t9\t1\n"), "12")


@pytest.mark.process
def test_declare_judges_python_cognitive_by_complexipy(make_repo, oracle):
    oracle("complexipy")
    top = _working(make_repo, BASE, _cognitive9(BASE))

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request(calcs=("Cognitive complexity",)))

    assert "crapkit now says 9, complexipy says 8 at src/a.py:parse (cognitive)" in \
        str(refused.value)


@pytest.mark.process
def test_a_definition_records_its_named_ruling_on_every_cell_of_that_calc(make_repo):
    top = _working(make_repo, BASE_CCN8, seeds.bump(
        {**BASE_CCN8, **{path: BASE[path] for path in (seeds.SCORED, seeds.INVENTORY)}}, "12"))

    _declare(top, _request(kind="definition", against=("R-CCN",)))

    moved = cc.rows((top / cc.MOVED / "C3.moved.tsv").read_bytes())
    assert {(row["column"], row["ruling"]) for row in moved} == {
        ("ccn", "R-CCN"), ("ccn_mod", "R-CCN"), ("ccn_std", "R-CCN"), ("crap", "")}


@pytest.mark.process
def test_declare_refuses_a_lizard_older_than_the_last_metric_row(make_repo):
    head = {**BASE_CCN8, **{path: BASE[path] for path in (seeds.SCORED, seeds.INVENTORY)}}
    top = _working(make_repo, BASE_CCN8, head)

    with pytest.raises(cc.ChangeControlError) as refused:
        cc.declare(top, _request(), "HEAD", regenerate_goldens=False, lizard="1.23.0")

    assert ("analysis 11, lizard 1.23.0 is older than the last metric-digests row (11, 1.24.0)"
            in str(refused.value))


@pytest.mark.process
def test_declare_refuses_a_fix_when_nothing_moved_and_a_reused_id(make_repo):
    top = _working(make_repo, BASE, BASE)

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request(key="C2", calcs=("No such calc",)))

    text = str(refused.value)
    assert "nothing moved since the lock; a change that moves nothing is kind none" in text
    assert "C2 is already declared; the next free id is C3" in text
    assert "no calc is named 'No such calc'" in text
