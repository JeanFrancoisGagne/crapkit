"""A repo whose every scope is cc-only runs with zero lanes, and that run counts.

Three refusals stood between such a repo and a score, all of them reading the
lane list as the proof that something was measured:

* `coverage` exited 3 on an empty lane list, so no run was ever written,
* `verify` exited 3 on an empty `[[lane]]` table,
* and `is_trusted` read lane provenance, so the run that did get written was no
  baseline for worklist, next-item, rescore, ratchet seed or verify.

Lane provenance answers "did a lane run", never "was this scored". `kind` is
what says scored, and it is what these read now. The refusal survives where it
belongs: a scope with no lane AND no `coverage_optional` is still exit 3, because
that scope really is unmeasured.
"""
import pytest

from crapkit.cli import _refuse_lane_less_verify, _select_lanes
from crapkit.config import Config, Lane, Scope
from crapkit.errors import ConfigError
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore, default_baseline, is_trusted

GO = Scope(name="cmd", paths=("cmd",), languages=("go",), coverage_optional=True)
PY = Scope(name="calc", paths=("calc",), languages=("python",))
JS = Scope(name="web", paths=("web",), languages=("typescript",))
PY_LANE = Lane(name="py", command="pytest", artifact="cov.json",
               parser="coveragepy", scopes=("calc",))
JS_LANE = Lane(name="js", command="vitest", artifact="cov-final.json",
               parser="istanbul", scopes=("web",))


def rows():
    return [InventoryRow("cmd", "cmd/route.go", "Classify( n )", 1, 9, 6, 6, 6, 8, 1, 2)]


# --- which scopes still owe a lane -------------------------------------------

def test_a_cc_only_repo_owes_no_lane():
    assert Config(scopes=(GO,)).lane_less_scopes == ()


def test_a_python_scope_with_no_lane_still_owes_one():
    assert Config(scopes=(GO, PY)).lane_less_scopes == ("calc",)


def test_a_declared_lane_settles_the_scope_it_measures():
    assert Config(scopes=(GO, PY), lanes=(PY_LANE,)).lane_less_scopes == ()


# --- what `coverage` agrees to run -------------------------------------------

def test_coverage_runs_a_cc_only_repo_with_no_lanes_at_all():
    assert _select_lanes(Config(scopes=(GO,)), None) == []


def test_coverage_still_exits_3_when_a_scope_no_key_excuses_has_no_lane():
    with pytest.raises(ConfigError) as exc:
        _select_lanes(Config(scopes=(GO, PY)), None)

    assert "no [[lane]] to run" in str(exc.value)
    assert "calc" in str(exc.value), "the message names the scope that owes one"


def test_the_refusal_never_names_the_scope_that_declared_the_key():
    with pytest.raises(ConfigError) as exc:
        _select_lanes(Config(scopes=(GO, PY)), None)

    assert "cmd" not in str(exc.value)


def test_naming_a_lane_that_does_not_exist_is_still_its_own_refusal():
    with pytest.raises(ConfigError, match="no lane named 'ghost'"):
        _select_lanes(Config(scopes=(GO, PY), lanes=(PY_LANE,)), "ghost")


def test_a_named_lane_still_selects_itself():
    assert _select_lanes(Config(scopes=(PY,), lanes=(PY_LANE,)), "py") == [PY_LANE]


# --- coverage and verify do not answer this the same way ----------------------
#
# Measured on a Python+Rust repo whose rust scope had lost its key: `coverage`
# exited 0 and scored `5 functions — 2 measured / 0 untested / 3 no-lane`, while
# `verify` on the same config exited 3 naming `rustsrc`. Both are right for what
# they do, and the pair is easy to write down backwards, so both are pinned.

def test_coverage_scores_a_lane_less_scope_rather_than_refusing_when_a_lane_exists():
    """`no-lane` is a flag `coverage` prints, which it could not be if a scope
    owing a lane refused the run. The refusal is for a run with no lane at all."""
    cfg = Config(scopes=(GO, PY, JS), lanes=(JS_LANE,))

    assert cfg.lane_less_scopes == ("calc",)
    assert _select_lanes(cfg, None) == [JS_LANE]


def test_verify_refuses_the_same_config_coverage_scores():
    """verify names every lane-less scope, whatever else the repo declares:
    coverage is half its verdict, so an unmeasured scope has no verdict."""
    cfg = Config(scopes=(GO, PY, JS), lanes=(JS_LANE,))

    with pytest.raises(ConfigError) as exc:
        _refuse_lane_less_verify(cfg)

    assert "calc" in str(exc.value)
    assert "cmd" not in str(exc.value), "the cc-only scope owes nothing"


# --- what counts as a scored baseline afterwards ------------------------------

def _run(store: SnapshotStore, kind: str, lanes: dict) -> int:
    return store.write_run(commit="a", tool_versions={}, rows=rows(), kind=kind, lanes=lanes)


def test_a_coverage_run_with_no_lanes_is_trusted(tmp_path):
    """The one that used to fall through to `no scored run — run coverage first`
    after coverage had just run."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = _run(store, "coverage", {})

    assert default_baseline(store)["id"] == run_id


def test_an_inventory_run_is_still_no_baseline(tmp_path):
    """Both carry no lane provenance; only one of them scored anything."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    _run(store, "inventory", {})

    assert default_baseline(store) is None


def test_a_passing_verify_with_no_lanes_is_trusted(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = _run(store, "verify", {})
    store.set_verdict_ok(run_id, True, findings=0)

    assert default_baseline(store)["id"] == run_id


def test_a_failed_verify_is_still_no_baseline(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = _run(store, "verify", {})
    store.set_verdict_ok(run_id, False, findings=2)

    assert default_baseline(store) is None


def test_a_hook_run_is_still_no_baseline(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    _run(store, "hook", {"_hook_override": {"staged": True}})

    assert default_baseline(store) is None


def test_a_partial_run_is_still_no_baseline(tmp_path):
    """A --lane subset measured a different lane set than verify will."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    _run(store, "partial", {"py": {}})

    assert default_baseline(store) is None


def test_a_legacy_row_still_needs_its_lane_provenance(tmp_path):
    """Rows migrated from before `kind` existed carry one label for inventory
    and coverage alike, so provenance stays the only thing telling them apart."""
    assert is_trusted({"kind": "legacy", "lanes": {}, "verdict_ok": None}) is False
    assert is_trusted({"kind": "legacy", "lanes": {"py": {}}, "verdict_ok": None}) is True
