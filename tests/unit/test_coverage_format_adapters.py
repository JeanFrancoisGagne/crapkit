"""Each coverage format is one adapter module, looked up once from the lane.

An adapter owns what its format decides when an artifact is read: function
coverage, dead lines, per-line test contexts, the path key it builds, the
absolute keys it did not place with the placing step's reason, and the advice a
refusal gives. The lane layer, the dark-line fold and `explain --tests` ask the
adapter; none of them compares parser strings of its own.
"""
import json
import os
from array import array
from types import SimpleNamespace

import pytest

from crapkit import coverage_format, coverage_istanbul, coverage_py, lanes, repopath
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.repopath import Unplaced
from crapkit.score import FileEvidence


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

    per_file, evidence, digest = coverage_istanbul.read(_lane("istanbul"), tmp_path, artifact)

    assert [fn.name for fn in per_file["src/a.ts"]] == ["f"]
    assert evidence == {"src/a.ts": FileEvidence(None, array("I", [3]))}
    assert len(digest) == 64
    assert coverage_istanbul.missing(_lane("istanbul"), tmp_path, artifact) == {"src/a.ts": {3}}


def test_the_coveragepy_adapter_keys_every_path_with_the_lanes_prefix(tmp_path):
    artifact = tmp_path / "cov.json"
    artifact.write_text(json.dumps(_coveragepy("pkg\\mod.py")), encoding="utf-8")
    lane = _lane("coveragepy", path_prefix="backend")

    per_file, evidence, _ = coverage_py.read(lane, tmp_path, artifact)

    assert list(per_file) == ["backend/pkg/mod.py"]
    assert evidence == {"backend/pkg/mod.py": FileEvidence(None, array("I", [3]))}
    assert coverage_py.missing(lane, tmp_path, artifact) == {"backend/pkg/mod.py": {3}}
    assert coverage_py.contexts(lane, tmp_path, artifact, "backend/pkg/mod.py") == {
        2: ["t.py::test_a"]}


def test_istanbul_records_no_test_contexts_and_opens_nothing_to_say_so(tmp_path):
    assert coverage_istanbul.contexts(_lane("istanbul"), tmp_path, tmp_path / "absent.json",
                                      "src/a.ts") == {}


# (parser, path_prefix, key the runner wrote, measured key): the wrong-tree check
# reads each measured key back as the runner wrote it, taking off only the
# prefix a format that takes one glued on. An absolute key carries none.
WRITTEN = [
    ("coveragepy", "backend/", "/other/checkout/a.py", "/other/checkout/a.py"),
    ("coveragepy", "backend/", "elsewhere/a.py", "backend/elsewhere/a.py"),
    ("coveragepy", "backend", "../sibling/a.py", "backend/../sibling/a.py"),
    ("istanbul", "/ci/", "/ci/other/checkout/a.ts", "/ci/other/checkout/a.ts"),
    ("istanbul", "web", "web/a.ts", "web/a.ts"),
]


@pytest.mark.parametrize("parser, path_prefix, written, measured", WRITTEN)
def test_the_check_reads_each_measured_key_as_the_runner_wrote_it(tmp_path, parser, path_prefix,
                                                                  written, measured):
    lane = _lane(parser, path_prefix=path_prefix)
    report = _istanbul(written) if parser == "istanbul" else _coveragepy(written)
    (tmp_path / "cov.json").write_text(json.dumps(report), encoding="utf-8")

    per_file, _, _ = coverage_format.lane_format(lane).read(lane, tmp_path, tmp_path / "cov.json")

    assert list(per_file) == [measured]
    assert lanes._written(lane, measured) == written


def test_only_the_coveragepy_reader_takes_path_prefix():
    assert coverage_py.TAKES_PATH_PREFIX is True
    assert coverage_istanbul.TAKES_PATH_PREFIX is False


def _keys(root) -> dict:
    """An absolute key per reason, and one in this checkout."""
    other = root.parent / "other"
    return {"here": (root / "src" / "a.x").as_posix(),
            "elsewhere": (other / "src" / "a.x").as_posix(),
            "unopenable": f"{other.as_posix()}/s\0rc/a.x"}


def _recorded(tmp_path, parser: str, path_prefix: str = "") -> tuple[dict, dict, dict]:
    """(keys, measured keys, record) for one report keying each of `_keys`."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    keys = _keys(root.resolve())
    report = {}
    for key in keys.values():
        report = _merged(report, _istanbul(key) if parser == "istanbul" else _coveragepy(key))
    (root / "cov.json").write_text(json.dumps(report), encoding="utf-8")
    lane = _lane(parser, path_prefix=path_prefix)
    unplaced: dict = {}
    per_file, _, _ = coverage_format.lane_format(lane).read(lane, root.resolve(),
                                                           root / "cov.json", unplaced=unplaced)
    return keys, per_file, unplaced


def _merged(report: dict, more: dict) -> dict:
    if "files" in more:
        return {**more, "files": {**report.get("files", {}), **more["files"]}}
    return {**report, **more}


def test_the_istanbul_reader_records_what_it_could_not_rebase_and_nothing_it_did(tmp_path):
    keys, per_file, unplaced = _recorded(tmp_path, "istanbul", "/ci/")

    assert unplaced == {keys["elsewhere"]: Unplaced.ANOTHER_TREE,
                        keys["unopenable"]: Unplaced.UNOPENABLE}
    assert "src/a.x" in per_file


@pytest.mark.parametrize("path_prefix", ["", "backend", "backend/"])
def test_the_coveragepy_reader_records_every_absolute_key_as_written(tmp_path, path_prefix):
    """No lane prefix on any of them: the reader glues it onto relative keys."""
    keys, per_file, unplaced = _recorded(tmp_path, "coveragepy", path_prefix)

    assert unplaced == {keys["here"]: Unplaced.KEPT_ABSOLUTE,
                        keys["elsewhere"]: Unplaced.ANOTHER_TREE,
                        keys["unopenable"]: Unplaced.UNOPENABLE}
    assert sorted(per_file) == sorted(keys.values())


def test_a_reader_handed_no_record_reads_as_before(tmp_path):
    keys, _, _ = _recorded(tmp_path, "coveragepy", "backend")
    root = tmp_path / "repo"

    per_file, _, _ = coverage_py.read(_lane("coveragepy", path_prefix="backend"), root.resolve(),
                                      root / "cov.json")

    assert sorted(per_file) == sorted(keys.values())


def _counted(monkeypatch) -> list:
    calls: list = []
    real = repopath.place
    monkeypatch.setattr(repopath, "place",
                        lambda path, root: calls.append(path) or real(path, root))
    return calls


def _coveragepy_report(keys) -> dict:
    one = _coveragepy("x.py")["files"]["x.py"]
    return {"meta": {"branch_coverage": True}, "files": dict.fromkeys(keys, one)}


def test_a_report_of_relative_keys_asks_the_placing_step_nothing(tmp_path, monkeypatch):
    calls = _counted(monkeypatch)
    keys = [f"pkg{i % 400}/m{i}.py" for i in range(31_459)]
    (tmp_path / "cov.json").write_text(json.dumps(_coveragepy_report(keys)), encoding="utf-8")
    unplaced: dict = {}

    per_file, _, _ = coverage_py.read(_lane("coveragepy"), tmp_path, tmp_path / "cov.json",
                                      unplaced=unplaced)

    assert len(per_file) == 31_459
    assert unplaced == {} and calls == []


def test_absolute_keys_ask_the_placing_step_once_a_folder(tmp_path, monkeypatch):
    calls = _counted(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    other = (tmp_path / "other").as_posix()
    keys = [f"{other}/d{i % 10}/m{i}.py" for i in range(200)]
    (root / "cov.json").write_text(json.dumps(_coveragepy_report(keys)), encoding="utf-8")
    unplaced: dict = {}

    coverage_py.read(_lane("coveragepy"), root, root / "cov.json", unplaced=unplaced)

    assert len(calls) == 10
    assert set(unplaced.values()) == {Unplaced.ANOTHER_TREE} and len(unplaced) == 200


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


# Producer facts: what coverage.py itself writes and leaves, whichever runner
# starts it. `parser` names the producer, so a wrapped command keeps them.
def _producing(parser: str, command: str = "python -m pytest --cov", *, name: str = "l",
               env: tuple = (), cwd: str = "") -> Lane:
    return Lane(name=name, command=command, artifact="cov.json", parser=parser, scopes=("src",),
                cwd=cwd, env=env)


# (command, env, cwd, where the data file lands as path parts)
DATA_FILES = [
    ("coverage run --data-file=out/.cov -m pytest", (), "", ("out", ".cov")),
    ("coverage run --data-file out/.cov -m pytest", (("COVERAGE_FILE", ".env"),), "",
     ("out", ".cov")),
    ("python -m pytest --cov", (("COVERAGE_FILE", ".coverage.unit"),), "", (".coverage.unit",)),
    ("python -m pytest --cov", (), "", (".coverage",)),
    ("make cov", (), "pkg", ("pkg", ".coverage")),
    ("python -m pytest --cov", (("COVERAGE_FILE", "../.coverage.web"),), "web",
     (".coverage.web",)),
]


@pytest.mark.parametrize("command, env, cwd, lands", DATA_FILES)
def test_coveragepy_names_where_its_data_file_lands(command, env, cwd, lands):
    lane = _producing("coveragepy", command, env=env, cwd=cwd)

    assert coverage_py.data_file(lane) == os.path.normcase(os.path.join(*lands))


def test_coveragepy_names_its_shards_their_combine_and_what_it_drops():
    assert coverage_py.SHARD_GLOB == ".coverage.*"
    assert coverage_py.COMBINE_RECIPE == ("coverage combine", "coverage json -o {target}")
    assert coverage_py.DROPPINGS == (".coverage", "__pycache__/")


def test_istanbul_has_no_producer_facts():
    lane = _producing("istanbul", "npx vitest run --coverage")

    assert coverage_istanbul.data_file(lane) is None
    assert coverage_istanbul.SHARD_GLOB is None
    assert coverage_istanbul.COMBINE_RECIPE is None
    assert coverage_istanbul.DROPPINGS == ()


@pytest.mark.parametrize("command", ["python -m pytest --cov --cov-report=json", "make cov"])
def test_two_coveragepy_lanes_on_one_data_file_collide_whatever_they_run(command):
    from crapkit.doctor import shared_coverage_data

    pair = [_producing("coveragepy", command, name="a"), _producing("coveragepy", command, name="b")]

    assert shared_coverage_data(pair) == (("a", "b"),)


def test_an_istanbul_lane_never_shares_a_coverage_data_file():
    from crapkit.doctor import shared_coverage_data

    pytest_cmd = "python -m pytest --cov"
    assert shared_coverage_data([_producing("istanbul", pytest_cmd, name="a"),
                                 _producing("istanbul", pytest_cmd, name="b")]) == ()
    assert shared_coverage_data([_producing("coveragepy", pytest_cmd, name="a"),
                                 _producing("istanbul", pytest_cmd, name="b")]) == ()


@pytest.mark.parametrize("command", ["python -m pytest --cov", "make cov"])
def test_a_coveragepy_lane_that_left_shards_gets_the_recipe_whatever_it_runs(tmp_path, command):
    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")

    hint = lanes._shard_hint(tmp_path, _producing("coveragepy", command))

    assert hint == (f"; 1 coverage shard (.coverage.box.pid5.aaaa, ...) sits in {tmp_path}, "
                    "which is what a killed parallel run leaves behind: `coverage combine` "
                    "followed by `coverage json -o cov.json` there, then a re-run with "
                    "--reuse-artifacts, scores what that suite did measure")


def test_an_istanbul_lane_beside_coverage_shards_gets_no_recipe(tmp_path):
    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")

    assert lanes._shard_hint(tmp_path, _producing("istanbul", "python -m pytest --cov")) == ""
