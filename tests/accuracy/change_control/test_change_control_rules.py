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

import hashlib
import os
from pathlib import Path
import re
import sys
import time

from hypothesis import given, strategies as st
import pytest

import hang_guard
from accuracy.change_control import cc_seeds as seeds
from accuracy.kit import repos
from accuracy.kit.settings import pure

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "tools" / "accuracy"
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))
import change_control as cc  # noqa: E402

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


def _module_changed(tree):
    return seeds.replace(tree, seeds.MODULE, "** 3 + ccn", "** 3 + ccn  # the README formula")


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
    "a calc-module diff with no change row": (BASE, _module_changed, {"B6"}),
}
CLEAN = {
    "a declared fix": (BASE, seeds.fixed_crap),
    "a declared ccn fix": (BASE_CCN8, seeds.fixed_ccn),
    "a declared definition": (BASE, _declared_definition),
    "a module refactor declared as none": (BASE, lambda tree: seeds.change(
        _module_changed(tree), "C3", "none", "", reason="a comment, nothing moves")),
}


# --- hand: each scenario on a real two-commit repo ------------------------------------------

def _delta(before: dict, after: dict) -> dict:
    """The files a commit writes (None deletes) to turn `before` into `after`."""
    gone = {path: None for path in before if path not in after}
    return {**gone, **{path: text for path, text in after.items() if before.get(path) != text}}


def _write(top: Path, before: dict, after: dict) -> None:
    for path, text in _delta(before, after).items():
        target = top / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))


def _commit(top: Path, before: dict, after: dict, number: int) -> None:
    """One commit turning `before` into `after`, with two git calls."""
    _write(top, before, after)
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "--allow-empty", "-m", f"step {number}",
              date=repos.EPOCH + 60 * number)


def seeded(make_repo, *trees: dict) -> Path:
    """A repo whose commits hold `trees` in order; the first is built once per session."""
    top = make_repo(repos.Spec(steps=(repos.Commit(files=trees[0], message="base"),))).top
    for number, (before, after) in enumerate(zip(trees, trees[1:]), start=1):
        _commit(top, before, after, number)
    return top


def _rules(text: str) -> set[str]:
    return set(re.findall(r"^(T\d|B\d+) ", text, re.M))


def git_verdict(make_repo, *trees: dict) -> tuple[int, str]:
    top = seeded(make_repo, *trees)
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
    return _module_changed({**tree, seeds.SCORED: seeds.scored(rows)})


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
    delta = sorted(_delta(base, head).items())
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
    "module": _module_changed,
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
    top = seeded(make_repo, base)
    _write(top, base, head)
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
def test_declare_refuses_a_move_the_oracle_disagrees_with(make_repo):
    top = _working(make_repo, BASE, _ccn9(BASE))

    with pytest.raises(cc.ChangeControlError) as refused:
        _declare(top, _request())

    assert "crapkit now says 9, radon says 7 at src/a.py:parse (ccn)" in str(refused.value)
    assert "--against-oracle <ruling-id>" in str(refused.value)
    assert not (top / cc.MOVED).exists()


@pytest.mark.process
def test_declare_takes_a_disagreement_a_named_ruling_covers(make_repo):
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
def test_a_declared_and_committed_move_passes_the_check(make_repo):
    """parse's ccn goes from 8 to 7, where radon puts it; declare records it, the
    author adds the CHANGELOG line, the bug and its retro row, and the push passes."""
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
def test_declare_none_records_a_change_that_moves_nothing(make_repo):
    top = _working(make_repo, BASE, _module_changed(BASE))
    lock = (top / cc.LOCK).read_bytes()

    text = _declare(top, _request(kind="none", calcs=()))

    assert text.startswith("declared C3 (none: no calc): 0 locked files relocked")
    assert (top / cc.LOCK).read_bytes() == lock
    assert cc.changes_of(cc.DirTree(top))["C3"]["kind"] == "none"


# --- the pre-push hook ------------------------------------------------------------------------

def _pushed(make_repo, head: dict):
    """A repo at `head` whose origin/main is BASE."""
    top = seeded(make_repo, BASE, head)
    repos.git(top, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    return top


def _push_line(top: Path) -> str:
    sha = repos.git(top, "rev-parse", "HEAD").strip()
    return f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"


@pytest.mark.process
def test_pre_push_refuses_an_undeclared_module_change_and_runs_its_calc_checks(make_repo,
                                                                               capfd):
    top = _pushed(make_repo, _module_changed(BASE))

    code = cc.pre_push(top, "origin", _push_line(top))

    out = capfd.readouterr().out
    assert code == 1
    assert "B6 src/crapkit/score.py holds CRAP score and changed with no declared change" in out
    assert "1 passed" in out


@pytest.mark.process
def test_pre_push_passes_a_declared_fix_and_runs_the_checks_of_the_bumped_module(make_repo,
                                                                                 capfd):
    """The fix bumps ANALYSIS_VERSION in analyze.py, the module of two seeded calcs."""
    top = _pushed(make_repo, seeds.fixed_crap(BASE))

    code = cc.pre_push(top, "origin", _push_line(top))

    out = capfd.readouterr().out
    assert code == 0, out
    assert "change control: pass" in out and "2 passed" in out


@pytest.mark.process
def test_pre_push_runs_nothing_when_no_calc_module_moved(make_repo, capfd):
    top = _pushed(make_repo, seeds.replace(BASE, "CONTEXT.md", "# Context", "# The context"))

    code = cc.pre_push(top, "origin", _push_line(top))

    assert code == 0
    assert "the push changes no calc module; no accuracy check to run" in capfd.readouterr().out


@pytest.mark.process
def test_the_hook_script_stops_a_real_push_within_a_minute(make_repo, tmp_path):
    """git runs git-hooks/pre-push itself: an undeclared module change is refused,
    and a declared fix goes through."""
    remote = tmp_path / "remote.git"
    repos.git(tmp_path, "init", "-q", "--bare", str(remote))
    bad = _pushed(make_repo, _module_changed(BASE))
    good = _pushed(make_repo, seeds.fixed_crap(BASE))

    refused, refused_seconds = _push(bad, remote)
    accepted, accepted_seconds = _push(good, remote)

    assert refused.returncode != 0 and "B6 src/crapkit/score.py" in refused.stdout + refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert max(refused_seconds, accepted_seconds) < 60


def _push(top: Path, remote: Path):
    """`git push` with core.hooksPath at this checkout's git-hooks, the way
    CONTRIBUTING sets it up, and this interpreter first on PATH."""
    repos.git(top, "config", "core.hooksPath", (REPO / "git-hooks").as_posix())
    env = {**os.environ, "PATH": os.pathsep.join((str(Path(sys.executable).parent),
                                                  os.environ["PATH"]))}
    started = time.monotonic()
    done = hang_guard.run(["git", "push", "-q", str(remote), "HEAD:refs/heads/main", "--force"],
                          cwd=top, env=env, text=True, encoding="utf-8", errors="replace")
    return done, time.monotonic() - started
