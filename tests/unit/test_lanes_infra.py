"""Lane infrastructure: streamed logs, crapkit-owned timeouts, retries. Real subprocesses."""
import json
import re
import sys

import pytest

from crapkit.config import Lane, load_config_text
from crapkit.errors import ConfigError, ToolError
from crapkit.lanes import run_lane
from crapkit.repotext import lenient

PY = sys.executable

MINIMAL_ART = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})


def _lane_log(root, name: str) -> str:
    """The lane log as crapkit reads it. The body is the command's own bytes:
    on Windows cmd.exe writes its "not recognized" line in the OEM code page,
    so a strict UTF-8 read raised before the assertion ran."""
    return lenient((root / ".crapkit" / f"lane-{name}.log").read_bytes())


def test_lane_timeout_kills_the_command_and_says_so(tmp_path):
    lane = Lane(name="slow", command=f'"{PY}" -c "import time; time.sleep(30)"',
                artifact="cov.json", parser="istanbul", scopes=(), timeout_seconds=1)
    with pytest.raises(ToolError, match="timed out"):
        run_lane(tmp_path, lane)


def test_lane_retries_recover_a_flaky_command(tmp_path):
    # First attempt plants a marker and dies without an artifact; the second
    # sees the marker and writes a valid artifact. retries=1 must recover.
    script = (
        "import pathlib, json, sys; m = pathlib.Path('marker'); "
        "(pathlib.Path('cov.json').write_text(json.dumps({'C:/r/a.ts': {'fnMap': {}, 'f': {}, 'branchMap': {}, 'b': {}}})), sys.exit(0)) "
        "if m.exists() else (m.write_text('x'), sys.exit(3))"
    )
    lane = Lane(name="flaky", command=f'"{PY}" -c "{script}"',
                artifact="cov.json", parser="istanbul", scopes=(), retries=1)
    outcome = run_lane(tmp_path, lane)
    coverage, prov = outcome.coverage, outcome.provenance
    assert prov["exit_code"] == 0
    log = _lane_log(tmp_path, "flaky")
    assert "attempt 2" in log, "the retry must be visible in the lane log"


def test_lane_log_streams_the_command_header_even_on_failure(tmp_path):
    lane = Lane(name="dead", command=f'"{PY}" -c "import sys; sys.exit(9)"',
                artifact="cov.json", parser="istanbul", scopes=())
    with pytest.raises(ToolError, match="no artifact"):
        run_lane(tmp_path, lane)
    log = _lane_log(tmp_path, "dead")
    assert log.startswith("$ "), "the log opens with the command that ran"


def test_a_lane_that_left_coverage_shards_is_told_they_are_there(tmp_path, on_a_host):
    """coverage.py in parallel mode writes one `.coverage.<host>.<pid>.<rand>`
    per process and merges them only at the end, so a run that was killed leaves
    every measurement on disk and no JSON. One reporter combined them by hand
    and got a usable artifact; crapkit said only that the artifact was missing,
    and the shards sit a directory above the path it names."""
    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    (tmp_path / ".coverage.box.pid6.bbbb").write_text("x", encoding="utf-8")
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact=".crapkit/cov/coverage.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    message = str(exc.value)
    assert "2 coverage shards" in message
    assert "coverage combine" in message and ".crapkit/cov/coverage.json" in message
    assert "--reuse-artifacts" in message


def test_the_shard_hint_looks_in_the_directory_the_lane_ran_in(tmp_path, on_a_host):
    """A lane with a `cwd` writes its shards there, not at the repo root.

    `artifact` is repo-relative and the hint is run from the shard directory, so
    printing the key verbatim tells the operator to write the JSON one directory
    down from where crapkit reads it. The `-o` target has to resolve, from the
    directory the hint names, to the file the next run opens. The count is one,
    so the noun is singular: `1 coverage shards` is a message that says crapkit
    did not read what it wrote.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact=".crapkit/cov/coverage.json", parser="coveragepy",
                scopes=(), cwd="pkg")

    with pytest.raises(ToolError, match=r"1 coverage shard \(") as exc:
        run_lane(tmp_path, lane)

    message = str(exc.value)
    assert str(tmp_path / "pkg") in message
    target = re.search(r"coverage json -o (\S+)` there", message).group(1)
    assert (tmp_path / "pkg" / target).resolve() == (tmp_path / lane.artifact).resolve()


def test_an_istanbul_lane_is_never_told_to_combine_coverage_shards(tmp_path):
    """`coverage combine` is a coverage.py command. Two lanes rooted at the same
    directory means the python lane's shards sit beside the JS lane's refusal,
    and a recipe that cannot work for the lane it is printed under is worse than
    no recipe: the reader spends a run finding that out."""
    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    lane = Lane(name="js", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact="cov.json", parser="istanbul", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    assert "coverage shard" not in str(exc.value)
    assert "coverage combine" not in str(exc.value)


def test_a_lane_with_no_shards_gets_no_salvage_hint(tmp_path):
    """The hint is for the one failure it fits. Printing it on every missing
    artifact is how a reader learns to skip the end of the message."""
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact="cov.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    assert "coverage shard" not in str(exc.value)


def test_an_init_written_pytest_lane_is_refused_in_a_container(tmp_path, monkeypatch):
    """The lane init writes runs `python -m pytest --cov`, which spells pytest,
    so inside a container it is refused before it starts, as it always was."""
    from crapkit.scaffold import detect_lanes

    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    (spec,) = detect_lanes(frozenset({"pyproject.toml"}), None, interpreter="python")
    lane = Lane(name=spec.name, command=spec.command, artifact=spec.artifact, parser=spec.parser,
                scopes=())

    with pytest.raises(ToolError, match="runs the python suite, which is host-only"):
        run_lane(tmp_path, lane)
    assert not (tmp_path / ".crapkit" / "lane-py.log").exists(), "refused before it started"


def test_a_coveragepy_lane_running_make_cov_starts_in_a_container(tmp_path, monkeypatch):
    """`make cov` names no pytest, so the container guard has nothing to read:
    the lane starts, and fails here only because no Makefile writes the file."""
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    lane = Lane(name="py", command="make cov", artifact="cov.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    assert "host-only" not in str(exc.value)
    log = _lane_log(tmp_path, "py")
    assert log.startswith("$ make cov"), log


def test_config_parses_timeout_and_retries():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
        '[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = "c.json"\n'
        'parser = "coveragepy"\nscopes = ["src"]\ntimeout_seconds = 600\nretries = 2\n')
    assert cfg.lanes[0].timeout_seconds == 600 and cfg.lanes[0].retries == 2


def test_config_rejects_negative_timeout_or_retries():
    base = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
            '[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = "c.json"\n'
            'parser = "coveragepy"\nscopes = ["src"]\n')
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config_text(base + "timeout_seconds = -5\n")
    with pytest.raises(ConfigError, match="retries"):
        load_config_text(base + "retries = -1\n")
    with pytest.raises(ConfigError, match="no_progress_seconds"):
        load_config_text(base + "no_progress_seconds = -1\n")


def test_config_parses_the_progress_deadline():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
        '[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = "c.json"\n'
        'parser = "coveragepy"\nscopes = ["src"]\nno_progress_seconds = 300\n')
    assert cfg.lanes[0].no_progress_seconds == 300


def test_the_shard_recipe_runs_in_windows_powershell(tmp_path, on_a_host):
    """Windows PowerShell 5.1 has no `&&`, so `coverage combine && coverage json
    -o ...` stopped at a parse error before either command ran. The recipe names
    the two commands one after the other, and the `-o` target goes in as one
    word of the reader's shell."""
    from crapkit.invocation import shell_arg

    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact="cov out/coverage.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    message = str(exc.value)
    assert "&&" not in message
    assert f"`coverage json -o {shell_arg('cov out/coverage.json')}` there" in message
