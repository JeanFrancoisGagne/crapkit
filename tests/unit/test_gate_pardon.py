"""The pardon, at the bound hook-precommit hands the gate.

A staged blob carries no coverage, so what the hook knows of a function's CRAP
is a range: ccn at full coverage up to the CRAP at coverage 0 under its scope's
flag. `gate.judge` sorts a marked function over its ceiling by that range
against its mark, each end at the 4 places a mark is stored at: pardoned when
the high end sits at or under the mark, a marked rise when the low end sits
above it, unproven when the two ends straddle it. hook-precommit refuses a
marked rise (cli/verifying maps it); the other two pass.

Every bound here comes from `hook._changed_file`, the function that builds the
hook's rows, so the table judges what the hook hands the gate. The property at
the bottom holds the classes to the exact CRAP at every coverage a lane can
report in 60ths.

An unproven function whose ccn rose above its ccn at the diff base, or that the
base holds no function under its key, is an unproven rise: the hook warns about
it and still passes. `gate.judge` asks the base through the fetch it is handed,
once, and only when a function is unproven; the rows at the end hold
`hook.ccn_at`, the hook's fetch, to the base's keys.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from test_gate_cases import Marks

from accuracy.kit import exact
from crapkit import gate, hook
from crapkit.config import load_config_text
from crapkit.merge import FunctionRecord
from crapkit.score import CRAP_PLACES, flagged_crap

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[scope]]
name = "cc"
paths = ["cc"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "bare"
paths = ["bare"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest"
artifact = "coverage.json"
parser = "coveragepy"
scopes = ["src"]
"""
CFG = load_config_text(CONFIG)
FLAGS = {"src": "untested", "cc": "cc-only"}
NAME = "read_row( line , cols )"
KINDS = ("over_ceiling", "pardoned", "marked_rise", "unproven", "unproven_rise")


def record(ccn: int, name: str = NAME, start: int = 1, path: str = "src/a.py") -> FunctionRecord:
    return FunctionRecord(path, name, start, start + 9, ccn, ccn, ccn, 10, 2, 1)


def staged(scope: str, *records: FunctionRecord) -> gate.ChangedFile:
    """The staged file as hook-precommit builds it, every function touched."""
    return hook._changed_file(f"{scope}/a.py", list(records), gate.WHOLE, scope, CFG, {})


def kinds(result: gate.GateResult) -> dict[str, str]:
    """Each breach's key name with the kind of finding the gate made of it."""
    return {breach.key_name: kind for kind in KINDS for breach in getattr(result, kind)}


def judge(change: gate.ChangedFile, marks: dict, ceiling: int = 6) -> tuple[gate.GateResult, Marks]:
    fetch = Marks(marks)
    return gate.judge([change], lambda scope: ceiling, fetch), fetch


@dataclass
class Base:
    """The base fetch: each key's ccn at the diff base from `ccn`, None for a
    key it does not hold, recording the keys of every call."""
    ccn: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def __call__(self, keys):
        self.calls.append(tuple(keys))
        return {key: self.ccn.get(key) for key in keys}


def judge_against(change: gate.ChangedFile, marks: dict, base: Base, ceiling: int = 6) -> gate.GateResult:
    return gate.judge([change], lambda scope: ceiling, Marks(marks), base)


# --- one row per case -------------------------------------------------------------

ROWS = [
    # (scope, ccn, mark, kind): measured ccn 8 is the range (8, 72), cc-only (8, 8).
    pytest.param("src", 8, 7.3333, "marked_rise", id="measured-8-mark-7.3333"),
    pytest.param("src", 8, 7.9999, "marked_rise", id="measured-8-mark-7.9999"),
    pytest.param("src", 8, 8.0, "unproven", id="measured-8-mark-8-ccn-at-the-mark-is-no-rise"),
    pytest.param("src", 8, 63.6, "unproven", id="measured-8-mark-63.6"),
    pytest.param("src", 8, 72.0, "pardoned", id="measured-8-mark-72"),
    pytest.param("cc", 8, 7.0, "marked_rise", id="cc-only-8-mark-7"),
    pytest.param("cc", 8, 8.0, "pardoned", id="cc-only-8-mark-8"),
    pytest.param("src", 8, None, "over_ceiling", id="measured-8-no-mark"),
]


@pytest.mark.parametrize(("scope", "ccn", "mark", "kind"), ROWS)
def test_each_range_against_its_mark_is_one_kind_of_finding(scope, ccn, mark, kind):
    change = staged(scope, record(ccn, path=f"{scope}/a.py"))
    marks = {} if mark is None else {(f"{scope}/a.py", NAME): mark}

    result, fetch = judge(change, marks)

    assert kinds(result) == {NAME: kind}
    assert fetch.calls == 1
    assert [breach.mark for kind_ in KINDS for breach in getattr(result, kind_)] == [mark]


def test_a_second_twin_is_judged_by_its_own_mark():
    """Two functions of one name: the first twin's mark of 80 would pardon a
    (8, 72) range, and the second twin's mark of 7 is what judges it."""
    first, second = record(8, start=1), record(8, start=20)
    marks = {("src/a.py", NAME): 80.0, ("src/a.py", f"{NAME}#2"): 7.0}

    result, _ = judge(staged("src", first, second), marks)

    assert kinds(result) == {NAME: "pardoned", f"{NAME}#2": "marked_rise"}
    assert [(b.key_name, b.mark, b.function.start) for b in result.marked_rise] == [(f"{NAME}#2", 7.0, 20)]


def test_a_clean_change_never_calls_the_marks_fetch():
    result, fetch = judge(staged("src", record(6), record(3, "coerce( word )", 20)), {("src/a.py", NAME): 7.0})

    assert result == gate.GateResult(judged=2)
    assert fetch.calls == 0


# --- the range rows: the bound the hook builds ------------------------------------

@pytest.mark.parametrize(("scope", "ccn", "bound"), [
    pytest.param("cc", 8, gate.CrapBound(8, 8.0), id="cc-only-ccn-8"),
    pytest.param("src", 8, gate.CrapBound(8, 72.0), id="measured-ccn-8"),
    pytest.param("src", 1, gate.CrapBound(1, 2.0), id="measured-ccn-1"),
    pytest.param("bare", 8, gate.CrapBound(8, 72.0), id="lane-less-scope-without-coverage-optional"),
])
def test_the_hook_bounds_a_staged_function_by_its_scopes_flag(scope, ccn, bound):
    (function,) = staged(scope, record(ccn, path=f"{scope}/a.py")).content

    assert function.bound == bound


@pytest.mark.parametrize("scope", sorted(FLAGS))
def test_the_high_end_is_the_crap_at_coverage_0_and_its_closed_form(scope):
    """ccn^2 + ccn where a lane measures coverage, ccn in a cc-only scope: the
    accuracy kit's exact CRAP at cov 0, and score's own CRAP under the flag."""
    closed = (lambda ccn: exact.crap(ccn, 0)) if scope == "src" else (lambda ccn: ccn)
    records = [record(ccn, f"f{ccn}( x )", 10 * ccn, f"{scope}/a.py") for ccn in range(1, 61)]

    bounds = [function.bound for function in staged(scope, *records).content]

    assert [b.high for b in bounds] == [flagged_crap(ccn, 0.0, FLAGS[scope]) for ccn in range(1, 61)]
    assert [b.high for b in bounds] == [float(closed(ccn)) for ccn in range(1, 61)]
    assert [b.low for b in bounds] == list(range(1, 61))


# --- the property: a refusal and a pardon hold at every coverage ------------------

COVS = [k / 60 for k in range(61)]


def exact_craps(ccn: int, flag: str) -> list[float]:
    """The CRAP a lane could report for this function, at 4 places, for each
    coverage in 60ths: what verify compares with the mark."""
    return [round(flagged_crap(ccn, cov, flag), CRAP_PLACES) for cov in COVS]


def candidate_marks(craps: list[float]) -> list[float]:
    """Integers, 4 dp values one step either side, and the exact tie values:
    the CRAP at each coverage, which is where a wrong comparison would show."""
    step = 10 ** -CRAP_PLACES
    near = {round(value + delta, CRAP_PLACES) for value in craps for delta in (-step, 0.0, step)}
    whole = {float(n) for value in craps for n in (int(value) - 1, int(value), int(value) + 1)}
    return sorted(near | whole)


@pytest.mark.parametrize("scope", sorted(FLAGS))
def test_a_rise_and_a_pardon_hold_at_every_coverage(scope):
    """Over ccn 1 to 40: a marked rise means the CRAP rounds above the mark at
    every coverage, so verify reports the rise whatever the tests reach; a
    pardon means it rounds at or under the mark at every coverage."""
    path, checked = f"{scope}/a.py", {"marked_rise": 0, "pardoned": 0, "unproven": 0}
    for ccn in range(1, 41):
        craps = exact_craps(ccn, FLAGS[scope])
        marks = candidate_marks(craps)
        records = [record(ccn, f"f{i}( x )", 10 * i + 1, path) for i in range(len(marks))]
        result, _ = judge(staged(scope, *records), {(path, f"f{i}( x )"): m for i, m in enumerate(marks)},
                          ceiling=0)
        for name, kind in kinds(result).items():
            mark = marks[int(name[1:name.index("(")])]
            checked[kind] += 1
            assert kind != "marked_rise" or all(crap > mark for crap in craps), (ccn, mark)
            assert kind != "pardoned" or all(crap <= mark for crap in craps), (ccn, mark)

    # A cc-only range is one point, so nothing there straddles its mark.
    assert {kind for kind, count in checked.items() if count} == (
        {"marked_rise", "pardoned", "unproven"} if scope == "src" else {"marked_rise", "pardoned"}), checked


# --- the rise: an unproven function whose ccn rose since the diff base --------------

KEY = ("src/a.py", NAME)

RISE_ROWS = [
    # (staged ccn, mark, base ccn, kind). ccn 12 is the range (12, 156), ccn 8 (8, 72).
    pytest.param(12, 110.0, 10, "unproven_rise", id="the-prd-probe-ccn-10-to-12-under-mark-110"),
    pytest.param(12, 110.0, 12, "unproven", id="a-comment-edit-keeps-ccn-12"),
    pytest.param(12, 110.0, 13, "unproven", id="ccn-fell-from-13"),
    pytest.param(8, 8.0, 7, "unproven_rise", id="ccn-7-to-8-under-mark-8"),
    pytest.param(12, 110.0, None, "unproven_rise", id="the-base-holds-no-function-under-the-key"),
]


@pytest.mark.parametrize(("ccn", "mark", "base_ccn", "kind"), RISE_ROWS)
def test_an_unproven_function_is_a_rise_when_its_ccn_rose_or_the_base_lacks_it(ccn, mark, base_ccn, kind):
    base = Base({} if base_ccn is None else {KEY: base_ccn})

    result = judge_against(staged("src", record(ccn)), {KEY: mark}, base)

    assert kinds(result) == {NAME: kind}
    assert base.calls == [(KEY,)]
    (breach,) = getattr(result, kind)
    assert (breach.function.record.ccn, breach.base_ccn, breach.mark, breach.key_name) == (ccn, base_ccn, mark, NAME)


def test_a_range_the_high_end_pardons_never_asks_the_base():
    """ccn 10 is the range (10, 110): its whole range sits at the mark of 110."""
    base = Base({KEY: 1})

    result = judge_against(staged("src", record(10)), {KEY: 110.0}, base)

    assert kinds(result) == {NAME: "pardoned"}
    assert base.calls == []


def test_the_base_is_asked_once_with_only_the_unproven_functions_keys():
    """Four marked breaches over two files: one pardoned, one marked rise and
    two unproven, a twin's `#2` among them. One call names the two."""
    first = staged("src", record(8, start=1), record(8, start=20), record(8, "plain( )", 40))
    second = hook._changed_file("src/b.py", [record(8, path="src/b.py")], gate.WHOLE, "src", CFG, {})
    marks = {KEY: 72.0, ("src/a.py", f"{NAME}#2"): 30.0, ("src/a.py", "plain( )"): 7.0,
             ("src/b.py", NAME): 30.0}
    base = Base({("src/a.py", f"{NAME}#2"): 8})

    result = gate.judge([first, second], lambda scope: 6, Marks(marks), base)

    assert base.calls == [(("src/a.py", f"{NAME}#2"), ("src/b.py", NAME))]
    assert [(b.path, b.key_name, b.base_ccn) for b in result.unproven] == [("src/a.py", f"{NAME}#2", 8)]
    assert [(b.path, b.key_name, b.base_ccn) for b in result.unproven_rise] == [("src/b.py", NAME, None)]


@pytest.mark.parametrize("marks", [
    pytest.param({}, id="no-mark"),
    pytest.param({KEY: 72.0}, id="pardoned"),
    pytest.param({KEY: 7.0}, id="marked-rise"),
])
def test_a_change_with_no_unproven_function_never_asks_the_base(marks):
    base = Base()

    judge_against(staged("src", record(8), record(3, "coerce( word )", 20)), marks, base)

    assert base.calls == []


def test_a_caller_with_no_base_fetch_gets_unproven_unchanged():
    change, marks = staged("src", record(12)), {KEY: 110.0}

    without = gate.judge([change], lambda scope: 6, Marks(marks))

    assert kinds(without) == {NAME: "unproven"}
    assert without.unproven_rise == ()
    assert without.unproven[0].base_ccn is None


# --- the property: the rise at every coverage and every base ------------------------

BASES = [None, *range(0, 41)]


def straddles(craps: list[float], mark: float) -> bool:
    """Whether coverage alone decides: some coverage puts the exact CRAP past
    the mark and another holds it at or under."""
    return any(crap > mark for crap in craps) and any(crap <= mark for crap in craps)


def test_a_rise_is_exactly_a_straddling_function_whose_ccn_rose_or_had_no_base():
    """Over ccn 1 to 40, every mark from the sets above with the base holding
    no function (the case that warns on any straddle) and the base at the same
    ccn (the case that never warns), then every base ccn 0 to 40 on three
    straddling marks per ccn: the low end, the CRAP at half coverage and one
    step under the high end."""
    path = "src/a.py"
    for ccn in range(1, 41):
        craps = exact_craps(ccn, FLAGS["src"])
        straddling = [mark for mark in candidate_marks(craps) if straddles(craps, mark)]
        cases = [(mark, base) for mark in candidate_marks(craps) for base in (None, ccn)]
        cases += [(mark, base) for mark in (straddling[0], craps[30], straddling[-1]) for base in BASES]
        names = [f"f{i}( x )" for i in range(len(cases))]
        base = Base({(path, name): base for name, (_, base) in zip(names, cases) if base is not None})

        result = judge_against(staged("src", *(record(ccn, name, 10 * i + 1, path) for i, name in enumerate(names))),
                               {(path, name): mark for name, (mark, _) in zip(names, cases)}, base, ceiling=0)

        rose = {name for name, (mark, before) in zip(names, cases)
                if straddles(craps, mark) and (before is None or ccn > before)}
        assert {b.key_name for b in result.unproven_rise} == rose, ccn
        assert len(base.calls) == 1, ccn


# --- the hook's base fetch -----------------------------------------------------------

def tangled(name: str, ccn: int) -> str:
    """`def NAME(n)` at `ccn`: ccn - 1 ifs."""
    body = "".join(f"    if n > {i}:\n        n += 1\n" for i in range(1, ccn))
    return f"def {name}(n):\n{body}    return n\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def committed(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """A repo whose one commit holds `files`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return repo


def test_the_hook_answers_each_twin_with_the_bases_own_twin(tmp_path):
    """Two `legacy` defs at ccn 3 and ccn 8: the base's `#2` answers for the
    staged `#2`, and a key the base never held answers None."""
    source = tangled("legacy", 3) + "\n" + tangled("legacy", 8)
    repo = committed(tmp_path, {"src/mod.py": source.encode("utf-8")})

    answers = hook.ccn_at(repo, "HEAD", [("src/mod.py", "legacy( n )#2"), ("src/mod.py", "legacy( n )"),
                                        ("src/mod.py", "renamed( n )"), ("src/gone.py", "legacy( n )")])

    assert answers == {("src/mod.py", "legacy( n )#2"): 8, ("src/mod.py", "legacy( n )"): 3,
                       ("src/mod.py", "renamed( n )"): None, ("src/gone.py", "legacy( n )"): None}


def test_the_base_read_prints_no_note_of_its_own(tmp_path, capsys):
    """The staged read already named the file's twins; a second note about
    the base's copy would print the same line twice beside the warn."""
    repo = committed(tmp_path, {"src/mod.py": (tangled("legacy", 3) + "\n" + tangled("legacy", 8)).encode("utf-8")})

    hook.ccn_at(repo, "HEAD", [("src/mod.py", "legacy( n )#2")])

    assert capsys.readouterr().err == ""


ENCODINGS = {
    "utf-8": lambda text: text.encode("utf-8"),
    "utf-16-le-bom": lambda text: b"\xff\xfe" + text.encode("utf-16-le"),
    "crlf": lambda text: text.replace("\n", "\r\n").encode("utf-8"),
}


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_the_base_ccn_is_the_same_in_every_encoding(tmp_path, encoding):
    repo = committed(tmp_path, {"src/mod.py": ENCODINGS[encoding](tangled("legacy", 8))})

    assert hook.ccn_at(repo, "HEAD", [("src/mod.py", "legacy( n )")]) == {("src/mod.py", "legacy( n )"): 8}


def test_the_hook_reads_every_base_path_in_one_blobs_at_call(tmp_path, monkeypatch):
    from crapkit import gitio

    repo = committed(tmp_path, {f"src/{n}.py": tangled("legacy", 8).encode("utf-8") for n in "abc"})
    calls, real = [], gitio.blobs_at
    monkeypatch.setattr(gitio, "blobs_at", lambda root, revision, paths: calls.append(list(paths))
                        or real(root, revision, paths))

    answers = hook.ccn_at(repo, "HEAD", [(f"src/{n}.py", "legacy( n )") for n in "cab"])

    assert calls == [["src/a.py", "src/b.py", "src/c.py"]]
    assert set(answers.values()) == {8}
