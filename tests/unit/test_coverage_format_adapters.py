"""Each coverage format is one adapter module, looked up once from the lane.

An adapter owns what its format decides when an artifact is read: function
coverage, dead lines, per-line test contexts, the path key it builds and the
inverse the wrong-tree check needs, and the advice a refusal gives. The lane
layer, the dark-line fold and `explain --tests` ask the adapter; none of them
compares parser strings of its own.
"""
import json
from types import SimpleNamespace

import pytest

from crapkit import coverage_format, coverage_istanbul, coverage_py
from crapkit.config import Lane
from crapkit.errors import ToolError


def _lane(parser: str, *, artifact: str = "cov.json", path_prefix: str = "") -> Lane:
    return Lane(name="l", command="x", artifact=artifact, parser=parser, scopes=("src",),
                path_prefix=path_prefix)


def _istanbul(path: str) -> dict:
    return {path: {"fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}},
                                   "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
                   "f": {"0": 1},
                   "statementMap": {"0": {"start": {"line": 2}}, "1": {"start": {"line": 3}}},
                   "s": {"0": 1, "1": 0}, "branchMap": {}, "b": {}}}


def _coveragepy(path: str) -> dict:
    return {"meta": {"branch_coverage": True},
            "files": {path: {"missing_lines": [3], "contexts": {"2": ["t.py::test_a|run"]},
                             "functions": {"f": {
                                 "start_line": 1, "executed_lines": [2], "missing_lines": [3],
                                 "summary": {"covered_lines": 1, "num_statements": 2,
                                             "num_branches": 0, "covered_branches": 0}}}}}}


def test_each_parser_names_its_own_adapter_module():
    assert coverage_format.lane_format(_lane("istanbul")) is coverage_istanbul
    assert coverage_format.lane_format(_lane("coveragepy")) is coverage_py


def test_an_unknown_parser_is_refused_by_the_one_lookup():
    with pytest.raises(ToolError) as raised:
        coverage_format.lane_format(_lane("cobertura"))
    assert str(raised.value) == "lane 'l': parser 'cobertura' not implemented yet"


def test_the_istanbul_adapter_reads_coverage_dead_lines_and_digest_in_one_walk(tmp_path):
    artifact = tmp_path / "cov.json"
    artifact.write_text(json.dumps(_istanbul(f"{tmp_path.as_posix()}/src/a.ts")),
                        encoding="utf-8")

    per_file, dead, digest = coverage_istanbul.read(_lane("istanbul"), tmp_path, artifact)

    assert [fn.name for fn in per_file["src/a.ts"]] == ["f"]
    assert dead == {"src/a.ts": {3}}
    assert len(digest) == 64
    assert coverage_istanbul.missing(_lane("istanbul"), tmp_path, artifact) == dead


def test_the_coveragepy_adapter_keys_every_path_with_the_lanes_prefix(tmp_path):
    artifact = tmp_path / "cov.json"
    artifact.write_text(json.dumps(_coveragepy("pkg\\mod.py")), encoding="utf-8")
    lane = _lane("coveragepy", path_prefix="backend")

    per_file, dead, _ = coverage_py.read(lane, tmp_path, artifact)

    assert list(per_file) == ["backend/pkg/mod.py"]
    assert dead == {"backend/pkg/mod.py": {3}}
    assert coverage_py.missing(lane, tmp_path, artifact) == dead
    assert coverage_py.contexts(lane, tmp_path, artifact, "backend/pkg/mod.py") == {
        2: ["t.py::test_a"]}


def test_istanbul_records_no_test_contexts_and_opens_nothing_to_say_so(tmp_path):
    assert coverage_istanbul.contexts(_lane("istanbul"), tmp_path, tmp_path / "absent.json",
                                      "src/a.ts") == {}


def test_the_coveragepy_inverse_takes_back_only_the_prefix_it_added():
    lane = _lane("coveragepy", path_prefix="backend/")
    assert coverage_py.as_reported(lane, "backend//other/checkout/a.py") == "/other/checkout/a.py"
    assert coverage_py.as_reported(lane, "elsewhere/a.py") == "elsewhere/a.py"


def test_the_istanbul_inverse_is_the_key_itself_whatever_path_prefix_says():
    lane = _lane("istanbul", path_prefix="/ci/")
    assert coverage_istanbul.as_reported(lane, "/ci/other/checkout/a.ts") == "/ci/other/checkout/a.ts"


def test_the_runner_advice_belongs_to_the_format_that_has_the_runner():
    assert "uv run python -m pytest" in coverage_py.WRONG_TREE_FIX
    assert "relative_files" in coverage_py.ABSOLUTE_FIX
    assert "path_prefix" in coverage_py.UNMEASURED_READING
    for advice in (coverage_istanbul.WRONG_TREE_FIX, coverage_istanbul.ABSOLUTE_FIX,
                   coverage_istanbul.UNMEASURED_READING):
        assert "pytest" not in advice and "path_prefix" not in advice


def test_the_dark_line_fold_reads_each_lane_through_its_adapter(tmp_path, monkeypatch):
    from crapkit import uncovered

    seen = []
    for module in (coverage_istanbul, coverage_py):
        real = module.missing
        monkeypatch.setattr(module, "missing",
                            lambda lane, root, path, _r=real: (seen.append(lane.parser),
                                                               _r(lane, root, path))[1])
    (tmp_path / "ist.json").write_text(json.dumps(_istanbul("src/a.ts")), encoding="utf-8")
    (tmp_path / "py.json").write_text(json.dumps(_coveragepy("src/b.py")), encoding="utf-8")
    cfg = SimpleNamespace(lanes=[_lane("istanbul", artifact="ist.json"),
                                 _lane("coveragepy", artifact="py.json")])

    missing = uncovered.missing_by_path(tmp_path, cfg)

    assert seen == ["istanbul", "coveragepy"]
    assert missing == {"src/a.ts": {3}, "src/b.py": {3}}


def test_explain_asks_every_lane_for_contexts_and_istanbul_answers_none(tmp_path, monkeypatch):
    from crapkit.cli import reports

    asked = []
    real = coverage_py.contexts
    monkeypatch.setattr(coverage_py, "contexts",
                        lambda lane, root, path, source, _r=real: (asked.append(lane.name),
                                                                   _r(lane, root, path, source))[1])
    (tmp_path / "ist.json").write_text(json.dumps(_istanbul("src/b.py")), encoding="utf-8")
    (tmp_path / "py.json").write_text(json.dumps(_coveragepy("src/b.py")), encoding="utf-8")
    lanes = [_lane("istanbul", artifact="ist.json")._replace(name="js"),
             _lane("coveragepy", artifact="py.json")._replace(name="py")]

    by_line = reports._contexts_for_path(tmp_path, SimpleNamespace(lanes=lanes), "src/b.py")

    assert asked == ["py"]
    assert by_line == {2: {"t.py::test_a"}}
