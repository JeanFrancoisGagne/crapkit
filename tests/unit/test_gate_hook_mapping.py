"""hook-precommit as a gate adapter: what it hands the gate, and how it maps the findings.

`hook.gate_staged` turns each staged function into a `gate.Function` whose
CRAP bound is what a blob can say with no coverage: at least ccn, at most the
CRAP `score` gives it at coverage 0 under its scope's flag. cli/verifying
judges the rows through `gate.judge` and maps each kind of finding to the
hook's exit. In this slot a marked breach passes on the mark's existence,
whatever its kind; mission-3-04 turns the marked_rise row below into a refusal.

`hook_split` is this module's helper for the older mark tests: it runs
hand-built staged violations through the gate and the hook's mapping.
"""
from __future__ import annotations

import pytest

from test_gate_cases import Marks, changed, claimed, fn, judged, unread

from crapkit import gate, hook
from crapkit.cli import verifying
from crapkit.config import load_config_text
from crapkit.errors import CrapkitError, UnreadableNameError
from crapkit.hook import Violation
from crapkit.merge import FunctionRecord
from crapkit.ratchet import RatchetEntry

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

[[lane]]
name = "py"
command = "python -m pytest"
artifact = "coverage.json"
parser = "coveragepy"
scopes = ["src"]
"""
CFG = load_config_text(CONFIG)
PATH = "src/a.py"
NAME = "route( a , b )"
REASON = "hotfix, ticket 7"
MARKS = "crapkit-ratchet.tsv"
UNSUPPORTED_KEYS = "# crapkit-keys=99\npath\tlong_name\tcrap\n"


def record(name: str = NAME, start: int = 1, ccn: int = 8, path: str = PATH) -> FunctionRecord:
    return FunctionRecord(path, name, start, start + 9, ccn, ccn, ccn, 10, 2, 1)


def staged_function(rec: FunctionRecord, scope: str = "src") -> gate.Function:
    """A staged record as the hook hands it to the gate, under its scope's flag."""
    return hook._changed_file(rec.path, [rec], gate.WHOLE, scope, CFG, {}).content[0]


def hook_split(violations, entries, ceiling: int = 6) -> tuple[list, int]:
    """(refused, how many a mark carries) for staged violations, each one
    function of its file at ccn^2 + ccn untested, through the gate's pardon and
    the hook's mapping. `entries` are the marks, keyed (path, key name)."""
    files: dict[str, list[gate.Function]] = {}
    for v in violations:
        rec = record(v.long_name, v.start, v.ccn, v.path)
        files.setdefault(v.path, []).append(fn(v.long_name, v.start, v.start, low=v.ccn,
                                               high=float(v.ccn * v.ccn + v.ccn), record=rec))
    marks = {(entry.path, entry.long_name): entry.crap for entry in entries}
    result, _ = judged(*(changed(path, *found) for path, found in files.items()), ceiling=ceiling,
                       marks=marks)
    return verifying._refused_violations(CFG, result)


# --- the bound a staged function carries --------------------------------------------

@pytest.mark.parametrize(("scope", "bound"), [
    pytest.param("cc", gate.CrapBound(8, 8.0), id="cc-only-is-exact"),
    pytest.param("src", gate.CrapBound(8, 72.0), id="measured-is-ccn-to-untested"),
])
def test_a_staged_ccn_8_function_is_bounded_by_its_scopes_flag(scope, bound):
    function = staged_function(record(path=f"{scope}/a.py"), scope)

    assert function.bound == bound
    assert (function.scope, function.record.ccn) == (scope, 8)


def test_a_staged_file_no_reader_read_is_taken_whole():
    found = hook._changed_file(PATH, [], [(3, 4)], "src", CFG, {PATH: "src/a.py:3: refused"})

    assert found == gate.ChangedFile(PATH, gate.WHOLE, gate.Unread(PATH, "src/a.py:3: refused"))


def test_the_staged_bound_judges_by_ccn_against_the_ceiling():
    """The low end is ccn, so ccn 6 sits at the ceiling of 6 and ccn 7 over it,
    whatever the untested CRAP."""
    rows = changed(PATH, staged_function(record("at( )", 1, 6)), staged_function(record("over( )", 20, 7)))

    result, _ = judged(rows)

    assert [b.function.long_name for b in result.over_ceiling] == ["over( )"]


# --- the mapping ----------------------------------------------------------------------

class Hooked:
    """`_hook_exit` on staged rows with CRAPKIT_OVERRIDE_REASON set, so a grant
    fires wherever the mapping reaches it; the grant only records, and the
    marks the gate reads are `marks`, keyed (path, key name)."""

    def __init__(self, root, monkeypatch, capsys):
        self.root, self.monkeypatch, self.capsys, self.grants = root, monkeypatch, capsys, []
        monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", REASON)
        monkeypatch.setattr(verifying, "_grant_env_override",
                            lambda root, cfg, violations, reason, records=(): self.grants.append(violations))
        monkeypatch.setattr(verifying, "_note_stale_staged", lambda *args, **kwargs: None)

    def __call__(self, *changes: gate.ChangedFile, marks: dict | None = None, reason: bool = True,
                 whole: bool = False) -> tuple[int, str, str]:
        if not reason:
            self.monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON")
        self.monkeypatch.setattr(verifying, "_hook_marks", lambda root, cfg, records: Marks(marks or {}))
        code = verifying._hook_exit(self.root, CFG, hook.StagedGate(changes, whole=whole))
        out = self.capsys.readouterr()
        return code, out.out, out.err


@pytest.fixture()
def hooked(tmp_path, monkeypatch, capsys) -> Hooked:
    return Hooked(tmp_path, monkeypatch, capsys)


def breach() -> gate.ChangedFile:
    """A staged ccn-8 function in a measured scope, bound (8, 72)."""
    return changed(PATH, staged_function(record()))


def marked(mark: float | None) -> dict:
    """`mark` on the breach's key, or no mark."""
    return {} if mark is None else {(PATH, NAME): mark}


CARRIED = "crapkit gate: 1 staged function(s) carry a ratchet mark and were not gated"
BREACH_KINDS = ("pardoned", "marked_rise", "unproven", "over_ceiling")

# Each kind of breach: the mark that puts the (8, 72) function there, the
# hook's exit with an override reason set, and whether the grant ran.
KINDS = [
    pytest.param(80.0, "pardoned", 0, False, id="pardoned"),
    # mission-3-04 turns this row into a refusal.
    pytest.param(7.0, "marked_rise", 0, False, id="marked_rise"),
    pytest.param(30.0, "unproven", 0, False, id="unproven"),
    pytest.param(None, "over_ceiling", 0, True, id="over_ceiling-granted"),
]


@pytest.mark.parametrize(("mark", "kind", "code", "granted"), KINDS)
def test_each_breach_kind_maps_to_its_exit(hooked, mark, kind, code, granted):
    result, _ = judged(breach(), marks=marked(mark))
    assert [bool(getattr(result, name)) for name in BREACH_KINDS] == [name == kind for name in BREACH_KINDS]

    got, out, err = hooked(breach(), marks=marked(mark))

    assert (got, bool(hooked.grants)) == (code, granted), out + err
    assert (CARRIED in err) == (not granted), err


def test_a_function_over_its_ceiling_with_no_reason_exits_6(hooked):
    code, out, _ = hooked(breach(), reason=False)

    assert (code, hooked.grants) == (6, [])
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:" in out
    assert f"ccn   8  {PATH}:1  {NAME}" in out
    assert "decompose before committing" in out


@pytest.mark.parametrize(("scope", "high"), [pytest.param("cc", 8.0, id="cc-only"),
                                              pytest.param("src", 72.0, id="untested")])
def test_the_grant_records_the_gates_high_bound(hooked, scope, high):
    """Z24 through the gate: the grant marks the bound's high end, ccn in a
    cc-only scope and ccn^2 + ccn where a lane measures coverage."""
    rec = record(path=f"{scope}/a.py")

    code, _, _ = hooked(changed(rec.path, staged_function(rec, scope)))

    assert code == 0
    assert [(v.path, v.ccn, v.high, v.key_name, v.ceiling) for v in hooked.grants[0]] == [
        (rec.path, 8, high, NAME, 6)]


def test_an_unread_file_exits_6_before_any_grant(hooked):
    code, out, _ = hooked(unread("src/b.py", "src/b.py:2: refused"), breach())

    assert (code, hooked.grants) == (6, [])
    assert "crapkit: override refused:" in out, out


def test_an_unread_file_exits_6_with_no_reason_set(hooked):
    code, out, _ = hooked(unread("src/b.py", "src/b.py:2: refused"), reason=False)

    assert (code, hooked.grants) == (6, [])
    assert "crapkit gate: 1 staged file(s) could not be read" in out, out


def test_a_claimed_name_exits_3_before_every_other_finding_and_any_grant(hooked):
    """Two claimed names beside an unread file and a ccn-8 breach: the scan's
    one sentence naming the first and counting the other, exit 3, nothing
    printed first (the whole-tree note included) and nothing granted."""
    rows = (unread("src/b.py"), breach(), claimed("src/caf\udce9.py"), claimed("src/o\udc92brien.py"))

    with pytest.raises(UnreadableNameError) as refused:
        hooked(*rows, whole=True)

    assert refused.value.exit_code == 3
    assert str(refused.value).startswith("src/caf\\xe9.py (and 1 more) is in scope 'src', but git names it")
    assert refused.value.names == ("src/caf\udce9.py", "src/o\udc92brien.py")
    assert hooked.grants == []
    assert hooked.capsys.readouterr() == ("", "")


# --- the marks file: read only on a breach ------------------------------------------

def staged_gate(ccn: int) -> hook.StagedGate:
    return hook.StagedGate((changed(PATH, staged_function(record(ccn=ccn))),))


def test_every_note_prints_before_a_breach_reads_an_unreadable_marks_file(tmp_path, capsys):
    """0.8.1's order: the whole-tree note, the names left out, the unscoped
    warning and the unread block print, then the breach reads the marks file
    and an unreadable one exits 3. Judging first printed only the marks line."""
    (tmp_path / MARKS).write_text(UNSUPPORTED_KEYS, encoding="utf-8", newline="\n")
    staged = hook.StagedGate((unread("src/old.ts", "src/old.ts:2: refused"), breach()), ["tools/side.py"],
                             unreadable=("lib/n\udce9.py",), whole=True)

    with pytest.raises(CrapkitError) as refused:
        verifying._hook_exit(tmp_path, CFG, staged)

    out = capsys.readouterr()
    assert refused.value.exit_code == 3
    assert "unreadable ratchet file" in str(refused.value)
    assert "nothing is staged and no commit is running" in out.err
    assert "crapkit: left out lib/" in out.err
    assert "belong to no scope and were not gated: tools/side.py" in out.err
    assert "crapkit gate: 1 tracked file(s) could not be read" in out.out
    assert "src/old.ts" in out.out


def test_the_hook_never_opens_the_marks_file_when_nothing_breached(tmp_path):
    (tmp_path / MARKS).write_text(UNSUPPORTED_KEYS, encoding="utf-8", newline="\n")

    assert verifying._judge_hook(tmp_path, CFG, staged_gate(3)) == gate.GateResult(judged=1)


def test_the_hook_reads_the_marks_file_on_a_breach(tmp_path):
    (tmp_path / MARKS).write_text(f"path\tlong_name\tcrap\n{PATH}\t{NAME}\t80.0000\n",
                                  encoding="utf-8", newline="\n")

    result = verifying._judge_hook(tmp_path, CFG, staged_gate(8))

    assert [(b.key_name, b.mark) for b in result.pardoned] == [(NAME, 80.0)]


def test_a_marks_file_the_hook_cannot_compare_refuses_on_a_breach(tmp_path):
    (tmp_path / MARKS).write_text(UNSUPPORTED_KEYS, encoding="utf-8", newline="\n")

    with pytest.raises(CrapkitError) as refused:
        verifying._judge_hook(tmp_path, CFG, staged_gate(8))

    assert refused.value.exit_code == 3


# --- the hook's order ------------------------------------------------------------------

def test_refused_rows_print_worst_ccn_first_and_marked_ones_are_counted():
    order = [Violation(PATH, "mild( n )", 1, 7), Violation(PATH, "legacy( n )", 20, 12),
             Violation(PATH, "worst( n )", 40, 20)]

    refused, carried = hook_split(order, [RatchetEntry(PATH, "legacy( n )", 63.6)])

    assert [v.long_name for v in refused] == ["worst( n )", "mild( n )"]
    assert carried == 1
