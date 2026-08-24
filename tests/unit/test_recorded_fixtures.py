"""Pure-core replay of recorded real lizard output against a committed golden export.

The recording (raw_std.json / raw_mod.json) was captured once from lizard over
the committed fixture sources; the golden pins the merge+snapshot+export
pipeline byte-for-byte. Pipeline behavior changes fail here; upstream lizard
format drift fails the e2e lane instead, and re-recording is an explicit,
reviewed act.
"""
import json
from pathlib import Path

from crapkit.merge import RawFn, merge_passes
from crapkit.snapshot import build_inventory_rows, tsv_lines

RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "recorded"


def _load(name: str) -> list[RawFn]:
    return [RawFn(*vals) for vals in json.loads((RECORDED / name).read_text(encoding="utf-8"))]


def test_recorded_passes_replay_to_the_committed_golden_export():
    std = _load("raw_std.json")
    mod = _load("raw_mod.json")
    by_scope = {}
    for scope, prefix in (("src", "src/"), ("py", "pylib/")):
        s = [r for r in std if r.path.startswith(prefix)]
        m = [r for r in mod if r.path.startswith(prefix)]
        by_scope[scope] = merge_passes(s, m)
    produced = "".join(tsv_lines(build_inventory_rows(by_scope)))
    golden = (RECORDED / "golden.tsv").read_bytes().decode("utf-8")
    assert produced == golden


def test_recorded_switch_function_shows_the_variant_split():
    std = {(r.path, r.long_name): r for r in _load("raw_std.json")}
    mod = {(r.path, r.long_name): r for r in _load("raw_mod.json")}
    key = ("src/app.ts", "dispatch ( kind )")
    assert std[key].ccn == 7, "recorded standard CCN for the 7-case switch"
    assert mod[key].ccn == 2, "recorded modified CCN collapses the switch"
