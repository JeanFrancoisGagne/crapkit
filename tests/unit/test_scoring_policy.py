"""Scoring policy for scopes whose code cannot carry coverage, and the byte
ceiling that keeps a minified blob out of the corpus. Pure."""
import json
from pathlib import Path

import pytest

from crapkit.config import load_config_text
from crapkit.doctor import _KNOWN
from crapkit.errors import ConfigError
from crapkit.score import overlay_stale_coverage, score_rows
from crapkit.snapshot import InventoryRow

ROOT = Path(__file__).resolve().parent.parent.parent

BASE = """
[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[scope]]
name = "shims"
paths = ["shims"]
languages = ["python"]
coverage_optional = true

[exclude]
globs = ["**/node_modules/**"]
max_file_bytes = 200000
"""


def _row(scope="shims", ccn=7):
    return InventoryRow(scope, "shims/mod.py", "f( )", 1, 13, ccn + 2, ccn, ccn, 8, 1, 2)


# --- config surface ---

def test_coverage_optional_defaults_false_and_parses_true():
    by_name = {s.name: s for s in load_config_text(BASE).scopes}
    assert by_name["src"].coverage_optional is False
    assert by_name["shims"].coverage_optional is True


def test_the_config_names_its_coverage_optional_scopes():
    assert load_config_text(BASE).coverage_optional_scopes == frozenset({"shims"})


def test_max_file_bytes_parses_and_defaults_to_no_limit():
    assert load_config_text(BASE).max_file_bytes == 200000
    bare = '[[scope]]\nname = "a"\npaths = ["a"]\nlanguages = ["python"]\n'
    assert load_config_text(bare).max_file_bytes is None


def test_max_file_bytes_rejects_a_non_integer():
    with pytest.raises(ConfigError, match="max_file_bytes"):
        load_config_text(BASE.replace("max_file_bytes = 200000", 'max_file_bytes = "big"'))


def test_the_new_keys_are_known_to_both_doctor_and_the_schema():
    props = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))["properties"]
    assert "coverage_optional" in _KNOWN["scope"]
    assert "coverage_optional" in props["scope"]["items"]["properties"]
    assert "max_file_bytes" in _KNOWN["exclude"]
    assert "max_file_bytes" in props["exclude"]["properties"]


# --- cc-only scoring ---

def test_a_coverage_optional_scope_scores_cc_only_at_its_own_ccn():
    (scored,) = score_rows([_row(ccn=7)], {}, lane_scopes={"src"}, target=6,
                           cc_only_scopes={"shims"})
    assert scored.flag == "cc-only"
    assert scored.cov == 0.0
    assert scored.crap == 7.0, "cc-only means crap IS ccn; the coverage join never runs"
    assert scored.remedy == "decompose", "ccn 7 sits over the ceiling of 6"


def test_a_cc_only_function_under_the_ceiling_is_ok_never_add_tests():
    (scored,) = score_rows([_row(ccn=5)], {}, lane_scopes={"src"}, target=6,
                           cc_only_scopes={"shims"})
    assert (scored.crap, scored.remedy) == (5.0, "ok"), \
        "the coverage join would have scored 5*5*1+5 = 30 and demanded tests nobody can write"


def test_a_per_scope_target_decides_the_cc_only_remedy():
    (scored,) = score_rows([_row(ccn=7)], {}, lane_scopes=set(), target=6,
                           scope_targets={"shims": 8}, cc_only_scopes={"shims"})
    assert scored.remedy == "ok"


def test_cc_only_wins_over_no_lane_so_the_scope_needs_no_lane():
    (scored,) = score_rows([_row()], {}, lane_scopes=set(), cc_only_scopes={"shims"})
    assert scored.flag == "cc-only"


def test_other_scopes_keep_the_coverage_join_when_one_scope_is_cc_only():
    rows = [_row(), InventoryRow("src", "src/a.ts", "g( )", 1, 9, 5, 5, 5, 8, 1, 2)]
    shim, app = score_rows(rows, {}, lane_scopes={"src"}, target=6, cc_only_scopes={"shims"})
    assert (shim.flag, app.flag) == ("cc-only", "untested")


def test_the_rescore_overlay_scores_cc_only_scopes_the_same_way():
    (scored,) = overlay_stale_coverage([_row(ccn=7)], [], lane_scopes={"shims"}, target=6,
                                       cc_only_scopes={"shims"})
    assert (scored.flag, scored.crap) == ("cc-only", 7.0)
