"""`verify --json` lists every finding once in `findings`, and counts in `counts`.

0.8.1 printed one list per kind under that kind's own key, and a file a scope
takes whose name is not UTF-8 stopped verify with an error object instead of a
verdict. Now each finding is one item: the fields every item carries (kind,
fails, exit_code, overridable, dirty and the rule label the Action's comment
prints) from its kind's row in verify.FINDING_KINDS, then the kind's own
fields. The eight 0.8.1 keys are gone: every value they held is in `findings`
or `counts`. The claimed-name stop prints the same payload, holding the one
kind no lane has to run for, and still exits 3 with the 0.8.1 stderr line.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, git, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from accuracy.verdict_model import model_verdict as model
from crapkit import universe, verify
from crapkit.agent_fields import FIELDS
from crapkit.cli import main, verifying
from crapkit.verify import (FINDING_KINDS, GateViolation, RatchetRegression, UncoveredViolation,
                            UnreadableName, Unread, Verdict)

ROOT = Path(__file__).resolve().parents[2]

GATE = GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose", key_name="f( x )")
UNREAD = Unread("src/b.ts", "src/b.ts:12: arrow refused")
ROSE = RatchetRegression("lib/m.py", "g( y )", 4.0, 9.0)
FAILURE = "tests/t.py::test_a"
LINE = UncoveredViolation("src/a.py", 7)
NAME = UnreadableName("src/caf\udce9.py", "src")

# One entry per kind, a second one its file's uncommitted edits make dirty,
# and the Verdict field it lives in.
KINDS = {
    "unreadable_name": ("claimed_names", NAME, NAME._replace(path="src/d\udce9.py", dirty=True)),
    "gate_violation": ("gate_violations", GATE, GATE._replace(start=30, dirty=True)),
    "unread_file": ("unread_files", UNREAD, UNREAD._replace(path="src/c.ts", dirty=True)),
    "ratchet_regression": ("ratchet_regressions", ROSE, ROSE._replace(path="lib/n.py", dirty=True)),
    "new_failure": ("new_failures", FAILURE, "tests/u.py::test_b"),
    "diff_uncovered": ("uncovered_violations", LINE, LINE._replace(path="src/e.py", dirty=True)),
    "overridden": ("overridden", GATE, GATE._replace(start=30, dirty=True)),
}
ORDER = list(KINDS)

COMMON = ("kind", "fails", "exit_code", "overridable", "dirty", "rule")
RULES = {"unreadable_name": "unreadable name", "gate_violation": "complexity gate",
         "unread_file": "complexity gate", "ratchet_regression": "ratchet regressions",
         "new_failure": "new test failures", "diff_uncovered": "diff-coverage ceiling",
         "overridden": "override"}
SENTENCE = ("src/caf\\xe9.py is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit "
            "reads every path as UTF-8; a file a scope takes is refused, not left out, so no gate passes "
            "it unread: rename it (git mv) to a UTF-8 name")
GATE_FIELDS = {"path": "src/a.py", "long_name": "f( x )", "start": 3, "ccn": 9, "cov": 0.5, "crap": 84.0,
               "remedy": "decompose", "key_name": "f( x )"}
# Each kind's own fields on its clean entry.
OWN = {
    "unreadable_name": {"path": "src/caf\\xe9.py", "scope": "src", "reason": SENTENCE},
    "gate_violation": GATE_FIELDS,
    "unread_file": {"path": "src/b.ts", "reason": "src/b.ts:12: arrow refused"},
    "ratchet_regression": {"path": "lib/m.py", "long_name": "g( y )", "recorded": 4.0, "fresh_crap": 9.0},
    "new_failure": {"test": "tests/t.py::test_a"},
    "diff_uncovered": {"path": "src/a.py", "line": 7},
    "overridden": GATE_FIELDS,
}
BASELINE = {"id": 1, "commit": "a" * 40}


def holding(*kinds: str, dirty: bool = False) -> Verdict:
    """A verdict holding one entry of each kind named, settled as verify settles one;
    `dirty` adds each kind's dirty entry beside its clean one."""
    fields: dict[str, list] = {}
    for kind in kinds:
        field, clean, dirtied = KINDS[kind]
        fields[field] = [clean, dirtied] if dirty else [clean]
    dirty_failures = ["tests/u.py::test_b"] if dirty and "new_failure" in kinds else []
    return verify.settle_verdict(Verdict.passing()._replace(dirty_failures=dirty_failures, **fields))


def _lines(verdict: Verdict) -> tuple[list, set]:
    """The uncovered lines verify hands its payload, and the dirty files among them."""
    held = verdict.uncovered_violations
    return [(line.path, line.line) for line in held], {line.path for line in held if line.dirty}


def payload(verdict: Verdict, uncovered: list | None = None, maximum: int | None = None,
            dirty: set | None = None) -> dict:
    """The verdict's part of `verify --json`, as it reaches stdout."""
    lines, flagged = _lines(verdict)
    built = verifying._verify_result(verdict, 1, BASELINE, "a" * 40, {}, lines if uncovered is None else uncovered,
                                     maximum, 0, flagged if dirty is None else dirty)
    return json.loads(json.dumps(built))


def items(verdict: Verdict, kind: str | None = None) -> list[dict]:
    listed = payload(verdict)["findings"]
    return [item for item in listed if kind in (None, item["kind"])]


def row(kind: str):
    return next(r for r in FINDING_KINDS if r.kind == kind)


_JSON_TYPES = {"boolean": bool, "integer": int, "number": (int, float), "string": str, "null": type(None)}


def json_type_ok(value, types: tuple[str, ...]) -> bool:
    """A bool is no number here, though Python calls it one."""
    if isinstance(value, bool):
        return "boolean" in types
    return any(isinstance(value, _JSON_TYPES[t]) for t in types)


def _declared(key: str) -> tuple[str, ...]:
    (found,) = [f.types for f in FIELDS if (f.payload, f.key) == ("verify --json", f"findings[].{key}")]
    return found


# --- one item per kind -------------------------------------------------------------

@pytest.mark.parametrize("kind", ORDER)
def test_each_kind_lists_one_item_with_its_rows_fields_and_its_own(kind):
    found = row(kind)

    (item,) = items(holding(kind))

    assert item == {"kind": kind, "fails": found.fails, "exit_code": found.exit,
                    "overridable": found.granted, "dirty": False, "rule": RULES[kind], **OWN[kind]}
    assert found.rule == RULES[kind]
    assert [key for key, value in item.items() if not json_type_ok(value, _declared(key))] == []


@pytest.mark.parametrize("kind", ORDER)
def test_an_entry_in_a_file_with_uncommitted_edits_lists_dirty(kind):
    listed = items(holding(kind, dirty=True))

    assert [item["dirty"] for item in listed] == [False, True]
    assert {item["kind"] for item in listed} == {kind}


def test_an_overridden_violation_fails_nothing_and_names_no_exit():
    (item,) = items(holding("overridden"))

    assert (item["fails"], item["exit_code"], item["overridable"]) == (False, None, False)


def test_only_a_gate_violation_is_overridable():
    listed = items(holding(*ORDER))

    assert {item["kind"] for item in listed if item["overridable"]} == {"gate_violation"}


# --- order -------------------------------------------------------------------------

def _own(item: dict) -> dict:
    return {key: value for key, value in item.items() if key not in COMMON}


def test_items_follow_the_exit_order_of_their_kinds():
    listed = items(holding(*reversed(ORDER), dirty=True))

    assert [item["kind"] for item in listed] == [kind for kind in ORDER for _ in range(2)]


@pytest.mark.parametrize("kind", ORDER)
def test_inside_a_kind_items_keep_the_order_the_verdict_holds(kind):
    verdict = holding(kind, dirty=True)
    field = KINDS[kind][0]
    held = list(reversed(getattr(verdict, field)))
    printed = payload(verdict._replace(**{field: held}))

    assert [item["dirty"] for item in printed["findings"]] == [True, False]
    assert [_own(item) for item in printed["findings"]] == [_own(row(kind).item(e)) for e in held]


def test_claimed_names_keep_the_order_the_gate_gave_them():
    verdict = holding("unreadable_name", dirty=True)
    verdict = verdict._replace(claimed_names=verdict.claimed_names[::-1])

    assert [item["path"] for item in items(verdict)] == ["src/d\\xe9.py", "src/caf\\xe9.py"]


# --- diff_uncovered and counts -----------------------------------------------------

FIFTY_ONE = [("src/a.py", line) for line in range(1, 52)]


def _uncovered(printed: dict) -> list[dict]:
    return [item for item in printed["findings"] if item["kind"] == "diff_uncovered"]


def test_51_uncovered_lines_list_50_items_and_count_51():
    printed = payload(Verdict.passing(), FIFTY_ONE)

    assert [item["line"] for item in _uncovered(printed)] == list(range(1, 51))
    assert printed["counts"] == {"diff_uncovered_count": 51, "diff_uncovered_max": None}


def test_with_no_ceiling_each_uncovered_line_fails_nothing():
    printed = payload(Verdict.passing(), FIFTY_ONE)

    assert {(item["fails"], item["exit_code"]) for item in _uncovered(printed)} == {(False, None)}
    assert printed["ok"] is True


def test_past_the_ceiling_each_uncovered_line_fails_with_exit_9():
    verdict = verify.with_diff_coverage(Verdict.passing(), FIFTY_ONE, 10, {"src/a.py"})

    printed = payload(verdict, FIFTY_ONE, 10, {"src/a.py"})

    assert {(i["fails"], i["exit_code"], i["dirty"]) for i in _uncovered(printed)} == {(True, 9, True)}
    assert printed["counts"] == {"diff_uncovered_count": 51, "diff_uncovered_max": 10}


def test_at_or_under_the_ceiling_the_lines_are_listed_and_fail_nothing():
    verdict = verify.with_diff_coverage(Verdict.passing(), FIFTY_ONE, 51, set())

    printed = payload(verdict, FIFTY_ONE, 51)

    assert len(_uncovered(printed)) == 50
    assert {(item["fails"], item["exit_code"]) for item in _uncovered(printed)} == {(False, None)}
    assert printed["counts"]["diff_uncovered_max"] == 51


def test_no_uncovered_line_lists_no_item_and_counts_0():
    printed = payload(Verdict.passing(), [])

    assert (printed["findings"], printed["counts"]["diff_uncovered_count"]) == ([], 0)


# --- the eight 0.8.1 keys are gone -----------------------------------------------------

OLD_KEYS = frozenset({"gate_violations", "ratchet_regressions", "overridden", "new_failures",
                      "diff_uncovered", "unread_files", "diff_uncovered_count", "diff_uncovered_max"})


def _every_kind() -> dict:
    """verify --json over a verdict holding a clean and a dirty entry of every
    kind, 51 uncovered lines past a ceiling of 10, as it reaches stdout."""
    held = holding(*(kind for kind in ORDER if kind != "diff_uncovered"), dirty=True)
    verdict = verify.with_diff_coverage(held, FIFTY_ONE, 10, {"src/a.py"})
    return payload(verdict, FIFTY_ONE, 10, {"src/a.py"})


def _as_listed(item: dict) -> dict | str:
    """A findings item as the 0.8.1 list of its kind held the entry."""
    if item["kind"] == "new_failure":
        return item["test"]
    if item["kind"] == "diff_uncovered":
        return {"path": item["path"], "line": item["line"]}
    return {**_own(item), "dirty": item["dirty"]}


def _listed(printed: dict, kind: str) -> list:
    return [_as_listed(item) for item in printed["findings"] if item["kind"] == kind]


def test_a_payload_with_every_kind_prints_no_old_key_and_findings_and_counts_carry_their_values():
    """Each 0.8.1 key's value, written out by hand from the entries the verdict
    holds, is the findings items of its kind or the counts key of its name."""
    gates = [{**GATE_FIELDS, "dirty": False}, {**GATE_FIELDS, "start": 30, "dirty": True}]
    old = {
        "gate_violations": gates,
        "unread_files": [{"path": "src/b.ts", "reason": "src/b.ts:12: arrow refused", "dirty": False},
                         {"path": "src/c.ts", "reason": "src/b.ts:12: arrow refused", "dirty": True}],
        "ratchet_regressions": [{"path": "lib/m.py", "long_name": "g( y )", "recorded": 4.0,
                                 "fresh_crap": 9.0, "dirty": False},
                                {"path": "lib/n.py", "long_name": "g( y )", "recorded": 4.0,
                                 "fresh_crap": 9.0, "dirty": True}],
        "new_failures": ["tests/t.py::test_a", "tests/u.py::test_b"],
        "diff_uncovered": [{"path": "src/a.py", "line": line} for line in range(1, 51)],
        "overridden": gates,
    }
    kinds = {"gate_violations": "gate_violation", "unread_files": "unread_file",
             "ratchet_regressions": "ratchet_regression", "new_failures": "new_failure",
             "diff_uncovered": "diff_uncovered", "overridden": "overridden"}

    printed = _every_kind()

    assert sorted(OLD_KEYS & set(printed)) == []
    assert {key: _listed(printed, kind) for key, kind in kinds.items()} == old
    assert printed["counts"] == {"diff_uncovered_count": 51, "diff_uncovered_max": 10}
    assert sorted({item["kind"] for item in printed["findings"]}) == sorted(ORDER)


# --- the hand exit table and the model (acc-verdict-model) -------------------------

HAND = {"unreadable_name": "unreadable_name", "unread": "unread_file", "gate": "gate_violation",
        "ratchet": "ratchet_regression", "failures": "new_failure", "diff_uncovered": "diff_uncovered"}


def _hand_rows() -> list[dict]:
    with (ROOT / "tests/accuracy/verdict_model/hand_exit.tsv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _subset(hand: dict) -> frozenset:
    return frozenset(name for name in HAND if hand[name] == "1")


@pytest.mark.parametrize("hand", _hand_rows(), ids=lambda hand: "+".join(sorted(_subset(hand))) or "none")
def test_the_first_failing_item_names_the_hand_tables_exit(hand):
    """Check 1 and 2: the first failing item's exit_code is the process exit
    the hand table gives, and the model, written from the docs, lists the
    same (kind, fails, exit_code) for each item."""
    subset = _subset(hand)
    listed = items(holding(*(HAND[name] for name in subset)))

    first = next((item["exit_code"] for item in listed if item["fails"]), 0)
    assert first == int(hand["exit"]) == model.exit_code(subset)
    assert [(item["kind"], item["fails"], item["exit_code"]) for item in listed] == model.findings(subset)


def test_the_model_lists_lines_under_no_ceiling_and_a_grant_after_the_failing_items():
    verdict = holding("ratchet_regression", "overridden")

    listed = payload(verdict, [("src/a.py", 7)])["findings"]

    assert [(item["kind"], item["fails"], item["exit_code"]) for item in listed] == model.findings(
        frozenset({"ratchet"}), uncovered_listed=True, overridden=True)


# --- the claimed-name stop, through the command ----------------------------------------

NAMED = "src/caf\udce9.ts"
SHOWN = "src/caf\\xe9.ts"


def _stage(root: Path, *names: bytes) -> None:
    """Each name in the index under its own bytes and nowhere on disk, which
    every OS can do: git reads the file as staged and deleted, so dirty."""
    for name in names:
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root, input=KNOTTY.encode(),
                              capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=root,
                       input=b"100644 " + blob + b"\t" + name + b"\0", capture_output=True, check=True)


def _runs(root: Path) -> int:
    from crapkit.store import SnapshotStore

    return len(SnapshotStore(root / ".crapkit" / "crap.sqlite").list_runs())


@pytest.fixture()
def measured(repo, capsys):
    """A trusted baseline with a function over the ceiling committed."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def _verify(root: Path, capsys, *flags: str) -> tuple[int, str, str]:
    code = main(["verify", "--reuse-artifacts", "--repo", str(root), *flags])
    out, err = capsys.readouterr()
    return code, out, err


def _stopped(root: Path, capsys, *flags: str) -> tuple[int, str, str]:
    runs = _runs(root)
    code, out, err = _verify(root, capsys, *flags)
    assert _runs(root) == runs, "the stop stores no run"
    return code, out, err


def _sentence(*names: str) -> str:
    return universe.claimed_text([(name, "src") for name in names])


def test_the_stop_prints_a_verify_payload_holding_one_unreadable_name_item(measured, capsys):
    _stage(measured, b"src/caf\xe9.ts")

    code, out, err = _stopped(measured, capsys, "--json")
    printed = json.loads(out)

    assert code == 3
    assert err == f"crapkit: {_sentence(NAMED)}\n"
    assert printed["findings"] == [{"kind": "unreadable_name", "fails": True, "exit_code": 3,
                                    "overridable": False, "dirty": True, "rule": "unreadable name",
                                    "path": SHOWN, "scope": "src", "reason": _sentence(NAMED)}]
    assert (printed["ok"], printed["run_id"], printed["baseline_run"]) == (False, None, 1)
    assert sorted(OLD_KEYS & set(printed)) == []
    assert printed["counts"] == {"diff_uncovered_count": 0, "diff_uncovered_max": None}
    assert (printed["changed_files"], printed["changed_paths"]) == (0, [])
    assert printed["commit"] == git(measured, "rev-parse", "HEAD").strip()
    assert (printed["committed_findings"], printed["dirty_findings"]) == (0, 1)
    assert "error" not in printed


def test_the_stop_prints_every_key_a_verdict_prints(measured, capsys):
    """The stop's payload is a verify payload, so a reader of either finds
    every key; the same tree without the name passes and prints them all."""
    _stage(measured, b"src/caf\xe9.ts")
    stopped = json.loads(_stopped(measured, capsys, "--json")[1])
    _unstage(measured, b"src/caf\xe9.ts")

    code, out, _ = _verify(measured, capsys, "--json")

    assert code == 0, out
    assert sorted(stopped) == sorted(json.loads(out))


def _unstage(root: Path, name: bytes) -> None:
    subprocess.run(["git", "update-index", "--force-remove", "-z", "--stdin"], cwd=root,
                   input=name + b"\0", capture_output=True, check=True)


def test_the_text_form_prints_the_0_8_1_line_and_nothing_on_stdout(measured, capsys):
    _stage(measured, b"src/caf\xe9.ts")

    assert _stopped(measured, capsys) == (3, "", f"crapkit: {_sentence(NAMED)}\n")


def test_two_names_list_two_items_under_one_line_naming_the_first(measured, capsys):
    _stage(measured, b"src/o\x92brien.ts", b"src/caf\xe9.ts")

    code, out, err = _stopped(measured, capsys, "--json")

    names = ["src/caf\udce9.ts", "src/o\udc92brien.ts"]
    assert (code, err) == (3, f"crapkit: {_sentence(*names)}\n")
    assert "(and 1 more)" in err
    assert [item["path"] for item in json.loads(out)["findings"]] == [SHOWN, "src/o\\x92brien.ts"]


def test_an_override_at_the_stop_grants_nothing_and_says_why(measured, capsys):
    _stage(measured, b"src/caf\xe9.ts")

    code, out, err = _stopped(measured, capsys, "--override", "a reviewed exemption")

    assert (code, out) == (3, "")
    assert err.splitlines() == [
        f"crapkit: {_sentence(NAMED)}",
        f"override refused: 1 unreadable name ({SHOWN}) never qualifies for an override; "
        "rename it (git mv) to a UTF-8 name"]
    assert not (measured / "alerts.log").exists()


def test_sarif_at_the_stop_writes_one_unreadable_name_result(measured, capsys):
    _stage(measured, b"src/caf\xe9.ts")

    code, out, _ = _stopped(measured, capsys, "--sarif", "out.sarif")

    document = json.loads((measured / "out.sarif").read_text(encoding="utf-8"))
    (result,) = document["runs"][0]["results"]
    assert (code, out) == (3, "")
    assert result == {"ruleId": "crapkit/unreadable-name", "level": "error",
                      "message": {"text": _sentence(NAMED)},
                      "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/caf%E9.ts"},
                                                          "region": {"startLine": 1}}}]}
    assert "crapkit/unreadable-name" in [rule["id"] for rule in document["runs"][0]["tool"]["driver"]["rules"]]


def test_github_at_the_stop_prints_one_annotation_naming_the_file_as_xnn(measured, capsys):
    _stage(measured, b"src/caf\xe9.ts")

    code, out, _ = _stopped(measured, capsys, "--github")

    assert (code, out.splitlines()) == (3, [
        f"::error file={SHOWN},line=1,title=crapkit/unreadable-name::{_sentence(NAMED)}"])


# --- the agent page's example is a real run ----------------------------------------------

_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def _masked(value):
    """A payload with what a rerun moves masked: commit and file digests, and
    the versions of the build that ran it."""
    if isinstance(value, dict):
        return {key: "<versions>" if key == "tool_versions" else _masked(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_masked(inner) for inner in value]
    return "<sha>" if isinstance(value, str) and _SHA.match(value) else value


def _page_example() -> dict:
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    section = page.split("\n## `verify`\n", 1)[1].split("\n## ", 1)[0]
    return json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])


def _lane_artifacts(root: Path) -> None:
    """What mini_repo's two lanes write, written by this interpreter: the
    istanbul stand-in and the pytest run with coverage and junit."""
    env = {**os.environ, "COVERAGE_PROCESS_CONFIG": "", "COV_CORE_DATAFILE": "", "PYTEST_ADDOPTS": ""}
    subprocess.run([sys.executable, "make_cov.py"], cwd=root, env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "pytest", "pylib", "-p", "no:cacheprovider", "-p", "no:randomly",
                    "--cov=pylib", "--cov-branch", "--cov-report=json:coverage-py.json", "--junitxml=junit.xml",
                    "-q"], cwd=root, env=env, check=True, capture_output=True)


def test_the_agent_page_example_is_a_real_verify_payload_on_mini_repo(tmp_path, capsys):
    root = tmp_path / "mini"
    shutil.copytree(ROOT / "tests" / "fixtures" / "mini_repo", root)
    git(root, "init", "-q")
    commit_all(root, "mini")
    _lane_artifacts(root)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(root)]) == 0
    add_knotty(root)
    capsys.readouterr()

    code, out, err = _verify(root, capsys, "--json")

    assert code == 6, err
    assert _masked(_page_example()) == _masked(json.loads(out))
