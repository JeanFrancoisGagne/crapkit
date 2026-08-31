"""Config, Scope and Lane are tuples, like every other row type in crapkit.

They were the last three frozen dataclasses in the tool, and `dataclasses` costs
9ms to import. Every command loads crapkit.config — the parser reaches it
through _shared before it can find a repo root — so every command paid that,
including `hook-precommit` at every git commit.

NamedTuple gives the same frozen fields with the same names and defaults, plus
the `_replace`/`_asdict` the rest of the codebase already speaks, for the import
cost of `typing`, which argparse already pulls in.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit.config import Config, Lane, Scope, load_config_text
from untraced_child import untraced_env

MINIMAL = """
[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "web"
command = "vitest run --coverage"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""


def _imports_of(module: str) -> set[str]:
    """Every module a fresh interpreter holds after importing this one.

    A child marked for tracing starts coverage, and that coverage imports
    dataclasses before the probe runs a line. The question here is what crapkit
    costs, so the child runs untraced: `untraced_env` drops both families of
    marker, because the lane sets both."""
    env = untraced_env()
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    probe = f"import {module}\nimport sys, json\nprint(json.dumps(sorted(sys.modules)))\n"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env)
    return set(json.loads(done.stdout.splitlines()[-1]))


def test_reading_the_config_module_costs_no_dataclasses_import():
    assert "dataclasses" not in _imports_of("crapkit.config")


def test_the_probe_child_stays_untraced_under_coverages_subprocess_patch(tmp_path, monkeypatch):
    """The other door into a child, and the one pytest-cov 7 leaves open.

    pytest-cov 7.0.0 dropped its own subprocess measurement, so `[run] patch =
    subprocess` is what measures the CLI the e2e suite drives, and it marks
    children with COVERAGE_PROCESS_* instead of COV_CORE_*. A probe that strips
    only the old family reads coverage's imports as crapkit's."""
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run]\nsource = crapkit\n", encoding="utf-8", newline="\n")
    monkeypatch.setenv("COVERAGE_PROCESS_START", str(rc))

    assert "dataclasses" not in _imports_of("crapkit.config")


def test_config_replaces_one_field_and_keeps_the_rest():
    cfg = load_config_text(MINIMAL)

    narrowed = cfg._replace(max_file_bytes=1000)

    assert narrowed.max_file_bytes == 1000
    assert narrowed.target == cfg.target
    assert narrowed.scopes == cfg.scopes
    assert narrowed.lanes == cfg.lanes
    assert cfg.max_file_bytes is None


def test_scope_and_lane_replace_too():
    cfg = load_config_text(MINIMAL)

    assert cfg.scopes[0]._replace(target=9).target == 9
    assert cfg.scopes[0]._replace(target=9).paths == ("src",)
    assert cfg.lanes[0]._replace(retries=2).retries == 2
    assert cfg.lanes[0]._replace(retries=2).command == "vitest run --coverage"


def test_the_field_names_and_order_are_the_ones_the_config_declared():
    assert Scope._fields == ("name", "paths", "languages", "target", "coverage_optional")
    assert Lane._fields[:5] == ("name", "command", "artifact", "parser", "scopes")
    assert Config._fields[:3] == ("target", "scopes", "exclude_globs")


def test_the_defaults_survive_the_move():
    bare = Config()

    assert (bare.target, bare.scopes, bare.exclude_globs) == (6, (), ())
    assert (bare.max_file_bytes, bare.diff_uncovered_max) == (None, None)
    assert (bare.churn_window_months, bare.worklist_floor, bare.worklist_top) == (12, 5, 50)
    assert bare.ratchet_file == "crapkit-ratchet.tsv"
    assert (bare.max_parallel_lanes, bare.analysis_workers, bare.mutation_workers) == (1, 0, 1)


def test_the_derived_views_still_read_off_the_scopes():
    cfg = load_config_text(MINIMAL + '\n[[scope]]\nname = "gen"\npaths = ["gen"]\n'
                                     'languages = ["python"]\ntarget = 12\n'
                                     "coverage_optional = true\n")

    assert cfg.scope_targets == {"src": 6, "gen": 12}
    assert cfg.coverage_optional_scopes == frozenset({"gen"})
    assert cfg.scope_paths == {"src": ("src",), "gen": ("gen",)}


def test_two_configs_with_the_same_fields_are_equal():
    assert load_config_text(MINIMAL) == load_config_text(MINIMAL)
    assert load_config_text(MINIMAL) != load_config_text(MINIMAL)._replace(target=9)
