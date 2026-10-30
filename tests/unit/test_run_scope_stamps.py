"""Each coverage and verify run records its scopes' analysis numbers and coverage modes.

A run's tool_versions, the store's record, holds `analysis_versions`,
`reader_versions` and `scope_stamps` beside the three keys every payload prints.
Each scope's coverage mode comes from the config, so `ratchet.running_stamp(cfg)`
builds the same stamp a run records, and `ratchet.run_stamp` reads it back.
"""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest
from cli_inproc_repo import commit_all, git, istanbul

from crapkit.analyze import ANALYSIS_VERSIONS
from crapkit.cli import main
from crapkit.config import Config, Lane, Scope, load_config_text
from crapkit.coverage_format import READER_VERSIONS
from crapkit.errors import ConfigError
from crapkit.ratchet import (MetricStamp, ScopeStamp, ScopeStamps, SingleNumberStamp,
                             run_stamp, running_stamp)
from crapkit.store import SnapshotStore

APP_PY = "def pick(x):\n    if x:\n        return 1\n    return 0\n"
UI_TS = 'export function render(n: number): string {\n  return n > 0 ? "up" : "down";\n}\n'
MAIN_GO = "package main\n\nfunc pick(n int) int {\n\tif n > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n"


def _scope(name: str, language: str, extra: str = "") -> str:
    return (f'[[scope]]\nname = "{name}"\npaths = ["{name}"]\n'
            f'languages = ["{language}"]\n{extra}\n')


def _lane(name: str, parser: str, scopes: list[str]) -> str:
    return (f'[[lane]]\nname = "{name}"\ncommand = "python -c pass"\n'
            f'artifact = "coverage/{name}.json"\nparser = "{parser}"\nscopes = {json.dumps(scopes)}\n\n')


TOML = ("[crapkit]\ntarget = 6\nanalysis_workers = 1\n\n"
        + _scope("api", "python") + _scope("web", "typescript")
        + _scope("tools", "go", "coverage_optional = true\n")
        + _lane("py", "coveragepy", ["api"]) + _lane("ui", "istanbul", ["web"]))

# The three scopes' stamps, written out: no value here is read from crapkit.
RECORDED = {"api": {"analysis": {"python": 13}, "coverage": {"coveragepy": 1}},
            "web": {"analysis": {"typescript": 13}, "coverage": {"istanbul": 1}},
            "tools": {"analysis": {"go": 13}, "coverage": "cc-only"}}


def _coveragepy_artifact(root: Path) -> None:
    fn = {"start_line": 1, "executed_lines": [2, 3, 4], "missing_lines": [], "excluded_lines": [],
          "summary": {"num_statements": 3, "covered_lines": 3, "num_branches": 2,
                      "covered_branches": 2, "excluded_lines": 0}}
    module = {"start_line": 1, "executed_lines": [1], "missing_lines": [],
              "summary": {"num_statements": 1, "covered_lines": 1}}
    report = {"meta": {"version": "7.13.2", "branch_coverage": True},
              "files": {"api/app.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": [],
                                       "functions": {"pick": fn, "": module}}},
              "totals": {"covered_lines": 4}}
    (root / "coverage" / "py.json").write_text(json.dumps(report), encoding="utf-8")


def _repo(root: Path, toml: str = TOML) -> Path:
    for path, text in {"api/app.py": APP_PY, "web/ui.ts": UI_TS, "tools/main.go": MAIN_GO,
                       "shims/run.py": APP_PY, "crapkit.toml": toml,
                       ".gitignore": ".crapkit/\ncoverage/\n"}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    (root / "coverage").mkdir()
    _coveragepy_artifact(root)
    istanbul(root, "coverage/ui.json", "web/ui.ts", {"render": (1, 3, 2)})
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo")


def _run(repo: Path, capsys, *argv: str) -> str:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    assert code == 0, out.err
    return out.out


def _runs(repo: Path) -> list[dict]:
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        return store.list_runs()


def _config(repo: Path) -> Config:
    return load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8"), root=repo)


def test_a_coverage_run_records_each_scope_s_numbers_and_coverage_mode(repo, capsys):
    _run(repo, capsys, "coverage", "--reuse-artifacts")

    versions = _runs(repo)[-1]["tool_versions"]
    assert versions["scope_stamps"] == RECORDED
    assert versions["analysis_versions"] == ANALYSIS_VERSIONS
    assert set(versions["analysis_versions"].values()) == {13}
    assert versions["reader_versions"] == READER_VERSIONS == {"coveragepy": 1, "istanbul": 1}
    assert versions["analysis_version"] == "13"


def test_running_stamp_is_the_stamp_a_coverage_run_on_the_config_records(repo, capsys):
    _run(repo, capsys, "coverage", "--reuse-artifacts")

    recorded = run_stamp(_runs(repo)[-1]["tool_versions"])
    assert isinstance(recorded, ScopeStamps)
    assert recorded == running_stamp(_config(repo))
    assert recorded.scopes["tools"] == ScopeStamp({"go": 13}, "cc-only")


def test_a_run_of_one_lane_records_the_modes_of_a_full_run(repo, capsys):
    _run(repo, capsys, "coverage", "--reuse-artifacts", "--lane", "ui")

    assert _runs(repo)[-1]["tool_versions"]["scope_stamps"] == RECORDED


def test_verify_records_the_stamps_of_the_coverage_run_before_it(repo, capsys):
    _run(repo, capsys, "coverage", "--reuse-artifacts")
    payload = json.loads(_run(repo, capsys, "verify", "--reuse-artifacts", "--json"))

    coverage, verify = _runs(repo)[-2:]
    assert (coverage["kind"], verify["kind"]) == ("coverage", "verify")
    assert verify["tool_versions"]["scope_stamps"] == coverage["tool_versions"]["scope_stamps"]
    assert run_stamp(verify["tool_versions"]) == running_stamp(_config(repo))
    # The three record keys stay in the store: verify --json prints the three it always did.
    assert set(payload["tool_versions"]) == {"analysis_version", "crapkit", "lizard"}


def test_doctor_names_a_lane_less_scope_exactly_where_the_run_records_no_lane(tmp_path, capsys):
    """The coverage-mode derivation read a third way: doctor's lane-less finding
    and its lane list against the modes a run recorded, scope by scope."""
    repo = _repo(tmp_path / "repo", TOML + _scope("shims", "python"))
    _run(repo, capsys, "coverage", "--reuse-artifacts")
    stamps = _runs(repo)[-1]["tool_versions"]["scope_stamps"]
    main(["doctor", "--json", "--repo", str(repo)])
    doctor = json.loads(capsys.readouterr().out)

    cfg = _config(repo)
    lanes = {lane.name: lane for lane in cfg.lanes}
    assert [lane["name"] for lane in doctor["lanes"]] == ["py", "ui"]
    for scope in cfg.scopes:
        named = any(f"scope {scope.name!r} is in no lane's scopes list" in problem
                    for problem in doctor["problems"])
        parsers = sorted(lanes[lane["name"]].parser for lane in doctor["lanes"]
                         if scope.name in lanes[lane["name"]].scopes)
        mode = stamps[scope.name]["coverage"]
        assert named == (mode == "no-lane"), scope.name
        if isinstance(mode, dict):
            assert list(mode) == parsers, scope.name
    assert stamps["shims"] == {"analysis": {"python": 13}, "coverage": "no-lane"}


def _cfg(scopes: list[Scope], lanes: list[Lane]) -> Config:
    return Config(scopes=tuple(scopes), lanes=tuple(lanes))


def _scope_of(name: str, *languages: str, optional: bool = False) -> Scope:
    return Scope(name, (name,), languages, coverage_optional=optional)


def _lane_of(name: str, parser: str, *scopes: str) -> Lane:
    return Lane(name, "true", f"{name}.json", parser, scopes)


def test_a_scope_two_parsers_measure_records_both_sorted_and_one_no_lane_measures_records_no_lane():
    cfg = _cfg([_scope_of("app", "typescript", "python"), _scope_of("shims", "shell")],
               [_lane_of("web", "istanbul", "app"), _lane_of("py", "coveragepy", "app")])

    scopes = running_stamp(cfg).scopes
    assert scopes["app"] == ScopeStamp({"python": 13, "typescript": 13},
                                       {"coveragepy": 1, "istanbul": 1})
    assert list(scopes["app"].coverage) == ["coveragepy", "istanbul"]
    assert scopes["shims"] == ScopeStamp({"shell": 13}, "no-lane")


def test_a_lane_that_measures_two_scopes_puts_its_parser_in_both():
    cfg = _cfg([_scope_of("a", "python"), _scope_of("b", "python"), _scope_of("c", "go", optional=True)],
               [_lane_of("py", "coveragepy", "a", "b", "c")])

    scopes = running_stamp(cfg).scopes
    assert scopes["a"].coverage == scopes["b"].coverage == {"coveragepy": 1}
    # coverage_optional wins over a lane that lists the scope: it is scored cc-only.
    assert scopes["c"].coverage == "cc-only"


def test_a_run_0_8_1_wrote_reads_as_its_single_number_and_one_that_recorded_none_as_none():
    old = run_stamp({"crapkit": "0.8.1", "analysis_version": "13", "lizard": "1.24.0"})

    assert isinstance(old, SingleNumberStamp)
    assert old == "crapkit-analysis=13 lizard=1.24.0"
    assert run_stamp({}) is MetricStamp.NONE
    assert run_stamp({"crapkit": "0.8.1"}) is MetricStamp.NONE


def test_seed_still_refuses_a_run_that_recorded_no_metric():
    from crapkit.cli.ratchet_cmds import _seed_metric

    with pytest.raises(ConfigError, match="run 4 recorded no metric"):
        _seed_metric({"id": 4, "tool_versions": {"crapkit": "0.8.1"}})


@pytest.mark.parametrize("versions, current", [
    ({"analysis_versions": {"typescript": 13}}, 1),
    ({"analysis_version": "9"}, 0),
    ({"analysis_versions": {"typescript": 9}, "analysis_version": "13"}, 0),
])
def test_an_expression_claim_reads_its_language_s_number_from_the_source_run(tmp_path, versions, current):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    with closing(store._conn):
        run_id = store.write_run(commit="c0ffee", tool_versions=versions, rows=[])
        assert store._claim_key_version("web/app.ts", "(anonymous)", run_id) == current
