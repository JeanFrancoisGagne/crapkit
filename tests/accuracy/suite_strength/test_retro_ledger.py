"""The retro tables: every src commit triaged, every past bug joined to its
replay, and every replay recorded the way the rules allow.

retro/triage.tsv has one row per commit on main, up to TRIAGE_THROUGH, that
touches src/, tools/action/comment.py or action.yml: `calc-fix` with the R ids
of the bugs it fixed, or `not-calc` with the reason it changed no computed
value. A commit after TRIAGE_THROUGH falls under change control instead, where
a calc-module diff needs a CHANGES row (tests/accuracy/change_control). kit-close
moves TRIAGE_THROUGH to its own commit and triages the packets' src commits.

retro/bugs.tsv names each bug's fix commits, the commit before them, the check
that must catch it and where it replays. retro/ledger.tsv holds one row per
bugs row: what tools/accuracy/retro.py saw on the before commit (red only on an
AssertionError; anything else is `not replayable`, with its class) and on the
fix (pass). A row stays `pending` only while this tree does not hold its check
yet; `open` marks a bug whose fix is not on main.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

import hang_guard

HERE = Path(__file__).resolve().parent
RETRO = HERE / "retro"
REPO = HERE.parents[2]
ACCURACY = HERE.parent
TRIAGE_THROUGH = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
TRIAGED_PATHS = ("src/", "tools/action/comment.py", "action.yml")
BUG_COLUMNS = ("id", "fix_commits", "before_commit", "packet", "test", "probe", "method",
               "platform", "replay", "calc", "symptom")
LEDGER_COLUMNS = ("id", "test", "before_commit", "fix_commit", "lizard", "before",
                  "failure_class", "before_evidence", "fix", "fix_evidence", "digest",
                  "replayed", "note")
TRIAGE_COLUMNS = ("sha", "date", "label", "r_ids", "reason", "subject")
METHODS = {"hand", "oracle", "model", "metamorphic", "property", "cross_surface", "golden"}
PLATFORMS = {"any", "windows", "linux", "macos"}
ASSERTIONS = {"AssertionError", "RulingDefect"}
HEX12 = re.compile(r"^[0-9a-f]{12}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _nonblank(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _uneven(rows: list[list[str]], width: int) -> list[int]:
    return [number for number, cells in enumerate(rows, 2) if len(cells) != width]


def _table(path: Path, columns: tuple[str, ...]) -> list[dict]:
    lines = _nonblank(path)
    assert tuple(lines[0].split("\t")) == columns, f"{path.name} header"
    rows = [line.split("\t") for line in lines[1:]]
    assert _uneven(rows, len(columns)) == [], f"{path.name}: rows without {len(columns)} cells"
    return [dict(zip(columns, cells)) for cells in rows]


BUGS = _table(RETRO / "bugs.tsv", BUG_COLUMNS)
LEDGER = _table(RETRO / "ledger.tsv", LEDGER_COLUMNS)
TRIAGE = _table(RETRO / "triage.tsv", TRIAGE_COLUMNS)


def _key(row: dict) -> tuple[str, str]:
    return row["id"], row["test"]


def _ids(cell: str) -> list[str]:
    return [part for part in cell.split(",") if part]


# --- triage -----------------------------------------------------------------------------------

def _src_commits() -> list[str]:
    argv = ["git", "rev-list", TRIAGE_THROUGH, "--", *TRIAGED_PATHS]
    done = hang_guard.run(argv, cwd=REPO, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, (f"git rev-list {TRIAGE_THROUGH[:12]} failed: {done.stderr}; "
                                  "the triage check needs full history (fetch-depth: 0)")
    return done.stdout.split()


@pytest.mark.process
def test_every_src_commit_through_the_cutoff_has_one_triage_row():
    shas = [row["sha"] for row in TRIAGE]

    assert sorted(shas) == sorted(set(shas)), "a commit has two triage rows"
    assert set(_src_commits()) == set(shas)


def _triage_problem(row: dict, known: set[str]) -> str | None:
    ids = _ids(row["r_ids"])
    rules = {"calc-fix": bool(ids) and set(ids) <= known,
             "not-calc": not ids and bool(row["reason"].strip())}
    return None if rules.get(row["label"], False) else f"{row['sha'][:12]} {row['label']} {ids}"


def test_a_triage_row_is_a_calc_fix_with_bugs_or_not_calc_with_a_reason():
    known = {row["id"] for row in BUGS}

    assert [p for p in (_triage_problem(row, known) for row in TRIAGE) if p] == []


def _fixed_on_main(row: dict, triaged: dict) -> list[str]:
    return [triaged[sha] for sha in triaged if any(sha.startswith(fix) for fix in
                                                    _ids(row["fix_commits"]))]


def test_a_triaged_fix_commit_names_every_bug_it_fixed():
    triaged = {row["sha"]: row for row in TRIAGE}
    missing = [f"{bug['id']} not named by {row['sha'][:12]}" for bug in BUGS
               for row in _fixed_on_main(bug, triaged) if bug["id"] not in _ids(row["r_ids"])]

    assert missing == []


# --- bugs -------------------------------------------------------------------------------------------

def _replay_kind(bug_id: str) -> str:
    number = int(bug_id[1:])
    return "bundle" if number <= 12 else ("open" if bug_id in ("R98", "R99") else "public")


BUG_RULES = (
    ("an R id", lambda row: re.fullmatch(r"R\d+", row["id"])),
    ("fix commits are hex", lambda row: all(HEX12.match(sha) for sha in _ids(row["fix_commits"]))),
    ("the before commit is hex", lambda row: HEX12.match(row["before_commit"])),
    ("the check sits in its packet",
     lambda row: row["test"].startswith(f"tests/accuracy/{row['packet']}/test_")),
    ("a known method", lambda row: row["method"] in METHODS),
    ("a known platform", lambda row: row["platform"] in PLATFORMS),
    ("bundle for R01 to R12, open for R98 and R99",
     lambda row: row["replay"] == _replay_kind(row["id"])),
    ("a calc and a symptom", lambda row: row["calc"].strip() and row["symptom"].strip()),
    ("a named probe exists", lambda row: not row["probe"] or (RETRO / "probes" / row["probe"]).is_file()),
)


def _bug_problems(row: dict) -> list[str]:
    return [f"{row['id']} {row['test']}: {rule}" for rule, check in BUG_RULES if not check(row)]


def test_every_bugs_row_is_well_formed():
    keys = [_key(row) for row in BUGS]

    assert len(keys) == len(set(keys)), "a bug names one check twice"
    assert [problem for row in BUGS for problem in _bug_problems(row)] == []


# --- the ledger --------------------------------------------------------------------------------------

def test_every_bugs_row_joins_one_ledger_row():
    ledger = [_key(row) for row in LEDGER]

    assert len(ledger) == len(set(ledger))
    assert set(ledger) == {_key(row) for row in BUGS}


REPLAYED_RULES = (
    ("before is red or not replayable", lambda row: row["before"] in ("red", "not replayable")),
    ("red only on an assertion",
     lambda row: row["before"] != "red" or row["failure_class"] in ASSERTIONS),
    ("not replayable names its class",
     lambda row: row["before"] != "not replayable" or bool(row["failure_class"] + row["note"])),
    ("the fix passed", lambda row: row["fix"] == "pass"),
    ("the replay's lizard and digest", lambda row: bool(row["lizard"]) and
     re.fullmatch(r"[0-9a-f]{16}", row["digest"]) is not None),
    ("the replay's date", lambda row: DATE.match(row["replayed"]) is not None),
)


def _replayed_problem(row: dict) -> str | None:
    """A replayed row: red on an assertion or not replayable with its class, a fix
    that passed, and the lizard, digest and date of the replay."""
    broken = [rule for rule, check in REPLAYED_RULES if not check(row)]
    return f"{row['id']} {row['test']}: {', '.join(broken)}" if broken else None


def _function_exists(test: str) -> bool:
    path, _, name = test.partition("::")
    file = REPO / path
    if not file.is_file():
        return False
    names = {node.name for node in ast.walk(ast.parse(file.read_bytes()))
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return name.split("::")[-1] in names


def _ledger_problem(row: dict) -> str | None:
    state = (row["before"], row["fix"])
    if state == ("open", "open"):
        return None if row["id"] in ("R98", "R99") else f"{row['id']}: only an unmerged fix is open"
    if state == ("pending", "pending"):
        return (f"{row['id']} {row['test']}: the check exists, replay it with "
                f"`python tools/accuracy/retro.py run {row['id']} --record`"
                if _function_exists(row["test"]) else None)
    return _replayed_problem(row)


def test_every_ledger_row_follows_the_replay_rules():
    assert [problem for problem in map(_ledger_problem, LEDGER) if problem] == []


def test_a_ledger_row_records_the_bugs_row_s_commits():
    bugs = {_key(row): row for row in BUGS}
    wrong = [_key(row) for row in LEDGER if bugs[_key(row)]["before_commit"] != row["before_commit"]
             or _ids(bugs[_key(row)]["fix_commits"])[-1] != row["fix_commit"]]

    assert wrong == []


# --- probes and the packets' retro tables ---------------------------------------------------------------

def _unsourced(probes: list[Path]) -> list[str]:
    return [path.name for path in probes if "# source: " not in path.read_text(encoding="utf-8")]


def test_every_probe_cites_its_source_and_is_named_by_a_bug():
    named = {row["probe"] for row in BUGS}
    probes = sorted((RETRO / "probes").glob("R*.py"))

    assert _unsourced(probes) == []
    assert sorted({path.name for path in probes} - named) == []


def _retro_rows() -> list[tuple[str, str]]:
    rows = []
    for path in sorted(ACCURACY.glob("*/retro.tsv")):
        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        rows += [(cells["id"], cells["test"]) for cells in
                 (dict(zip(header, line.split("\t"))) for line in lines[1:] if line.strip())]
    return rows


def test_every_packet_retro_row_is_a_bugs_row():
    known = {_key(row) for row in BUGS}

    assert [row for row in _retro_rows() if row not in known] == []


def _bound(node: ast.stmt) -> list[str]:
    """The names one top-level statement binds: a def, a class, or plain assignments."""
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return [node.name]
    targets = node.targets if isinstance(node, ast.Assign) else []
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _top_level_names(tree: ast.Module) -> list[str]:
    return [name for node in tree.body for name in _bound(node)]


@pytest.mark.parametrize("path", sorted(HERE.glob("test_*.py")), ids=lambda path: path.name)
def test_no_test_module_here_defines_a_top_level_name_twice(path):
    """A second CHECK and a second _bug in test_retro_tool.py each replaced the
    planted replay's own, and only the nightly replay noticed."""
    names = _top_level_names(ast.parse(path.read_text(encoding="utf-8")))

    assert sorted({name for name in names if names.count(name) > 1}) == []
