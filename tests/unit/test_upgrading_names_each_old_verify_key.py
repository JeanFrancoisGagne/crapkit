"""docs/upgrading.md maps each of the eight keys 0.9.0 dropped from verify --json
to where its value went, and each place it names is in a real payload.

The table sits in the gate-group block of "Upgrading to 0.9.0". A row names a
`findings` kind (with any field it calls out, and `fails` false and
`exit_code` null for a kind that never fails) or a `counts` key. The payloads
it is checked against are what verify prints: its builder over a verdict
holding every kind past a diff-coverage ceiling, and the command itself at
the stop on a file a scope takes whose name is not UTF-8.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit.cli import main, verifying
from crapkit.gate import Unread, UnreadableName
from crapkit.verify import GateViolation, RatchetRegression, Verdict, settle_verdict, with_diff_coverage

ROOT = Path(__file__).resolve().parents[2]
OLD_KEYS = ("gate_violations", "ratchet_regressions", "overridden", "new_failures", "diff_uncovered",
            "unread_files", "diff_uncovered_count", "diff_uncovered_max")

_ROW = re.compile(r"^\| `(\w+)` \| (.+) \|$")
_KIND = re.compile(r"of kind `(\w+)`")
_FIELD = re.compile(r"in field `(\w+)`")
_COUNTS = re.compile(r"^`counts\.(\w+)`$")


def _block() -> str:
    page = (ROOT / "docs" / "upgrading.md").read_text(encoding="utf-8")
    return page.split("<!-- 0.9.0:gate-group -->", 1)[1].split("<!-- /0.9.0:gate-group -->", 1)[0]


def _rows() -> dict[str, str]:
    """Each old key the table names, and the cell saying where its value went."""
    section = _block().split("### verify --json drops the 0.8.1 per-kind keys", 1)[1]
    found = [_ROW.match(line) for line in section.splitlines()]
    return {match[1]: match[2] for match in found if match and match[1] in OLD_KEYS}


def _every_kind() -> dict:
    """verify's payload over a verdict holding one entry of every kind, the
    lines past a ceiling of 0, as it reaches stdout."""
    gate = GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose", True, "f( x )")
    verdict = settle_verdict(Verdict.passing()._replace(
        claimed_names=(UnreadableName("src/caf\udce9.py", "src", True),), gate_violations=[gate],
        unread_files=(Unread("src/b.ts", "src/b.ts:12: arrow refused"),),
        ratchet_regressions=[RatchetRegression("lib/m.py", "g( y )", 4.0, 9.0)],
        new_failures=["tests/t.py::test_a"], overridden=(gate._replace(start=30),)))
    lines = [("src/a.py", line) for line in range(1, 61)]
    verdict = with_diff_coverage(verdict, lines, 0, set())
    built = verifying._verify_result(verdict, 2, {"id": 1, "commit": "a" * 40}, "a" * 40,
                                     {"src/a.py": [(1, 60)]}, lines, 0, 0)
    return json.loads(json.dumps(built))


def _stage(root: Path, name: bytes) -> None:
    """The name in the index under its own bytes and nowhere on disk."""
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root, input=KNOTTY.encode(),
                          capture_output=True, check=True).stdout.strip()
    subprocess.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=root,
                   input=b"100644 " + blob + b"\t" + name + b"\0", capture_output=True, check=True)


@pytest.fixture()
def stopped(repo, capsys) -> dict:
    """verify --json run by the command over a tree holding a claimed name."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    _stage(repo, b"src/caf\xe9.ts")
    capsys.readouterr()
    assert main(["verify", "--reuse-artifacts", "--repo", str(repo), "--json"]) == 3
    return json.loads(capsys.readouterr().out)


def _items(payload: dict, kind: str) -> list[dict]:
    return [item for item in payload["findings"] if item["kind"] == kind]


def _missing(old: str, cell: str, payload: dict) -> list[str]:
    """What the row says that the payload does not hold."""
    counted = _COUNTS.match(cell)
    if counted:
        return [] if counted[1] in payload["counts"] else [f"{old}: counts.{counted[1]}"]
    kinds = _KIND.findall(cell)
    gaps = [f"{old}: kind {kind}" for kind in kinds if not _items(payload, kind)]
    for field in _FIELD.findall(cell):
        gaps += [f"{old}: field {field}" for item in _items(payload, kinds[0]) if field not in item]
    if "`fails` false and `exit_code` null" in cell:
        gaps += [f"{old}: fails" for item in _items(payload, kinds[0])
                 if (item["fails"], item["exit_code"]) != (False, None)]
    return gaps


def test_the_table_has_one_row_per_old_key_each_naming_a_place():
    rows = _rows()

    assert sorted(rows) == sorted(OLD_KEYS)
    assert [old for old, cell in rows.items() if not (_COUNTS.match(cell) or _KIND.search(cell))] == []


def test_each_row_s_new_place_is_in_a_payload_holding_every_kind():
    payload = _every_kind()

    assert [gap for old, cell in _rows().items() for gap in _missing(old, cell, payload)] == []
    assert len(_items(payload, "diff_uncovered")) == 50
    assert [key for key in OLD_KEYS if key in payload] == []


def test_the_unreadable_name_row_is_the_item_the_command_prints_at_the_stop(stopped):
    (item,) = stopped["findings"]
    (row,) = [old for old, cell in _rows().items() if "`unreadable_name`" in cell]

    assert row == "unread_files"
    assert (item["kind"], item["path"], item["scope"]) == ("unreadable_name", "src/caf\\xe9.ts", "src")
    assert item["reason"].endswith("rename it (git mv) to a UTF-8 name")
    assert [key for key in OLD_KEYS if key in stopped] == []
