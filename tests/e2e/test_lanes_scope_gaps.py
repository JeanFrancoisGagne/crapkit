"""Lane scope-reuse decisions, plus the small pure helpers that feed them.

Four seams, one theme: what counts as "still describes this code". The reuse
tests drive the real CLI over a real git repo so the decision is observed as
lane RERUNS, not as a return value; the rest call public functions directly.
Every fixture is built inline; nothing is read from the repo's own tests.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import config
from crapkit.churn import parse_git_log
from crapkit.config import load_config_text
from crapkit.errors import ConfigError
from crapkit.lanes import lane_unchanged, run_lane, write_stamps
from crapkit.scaffold import sniff_scopes

# Forward slashes survive TOML basic strings and cmd.exe alike.
PY = sys.executable.replace("\\", "/")

APP_TS = (
    "export function dispatch(kind: string): number {\n"
    "  if (kind === 'a') {\n"
    "    return 1;\n"
    "  }\n"
    "  return 2;\n"
    "}\n"
)

MAKE_COV = (
    '"""Stand-in for a real coverage run: writes an istanbul artifact for src/app.ts."""\n'
    "import json\n"
    "import os\n"
    "import pathlib\n"
    "root = os.getcwd()\n"
    'app = os.path.join(root, "src", "app.ts")\n'
    "artifact = {app: {\n"
    '    "path": app,\n'
    '    "fnMap": {"0": {"name": "dispatch", "decl": {"start": {"line": 1}},\n'
    '                    "loc": {"start": {"line": 1}, "end": {"line": 6}}}},\n'
    '    "f": {"0": 2},\n'
    '    "branchMap": {"0": {"loc": {"start": {"line": 2}},\n'
    '                        "locations": [{"start": {"line": 2}}, {"start": {"line": 3}}]}},\n'
    '    "b": {"0": [1, 0]},\n'
    "}}\n"
    'pathlib.Path(root, "coverage").mkdir(exist_ok=True)\n'
    'pathlib.Path(root, "coverage", "coverage-final.json").write_text(\n'
    '    json.dumps(artifact), encoding="utf-8")\n'
)

# The lane command counts its own executions, so a test can assert that
# --reuse-unchanged did or did not actually rerun the suite.
RUN_COUNTED = (
    "import subprocess\n"
    "import sys\n"
    'with open("runs.txt", "a", encoding="utf-8") as fh:\n'
    '    fh.write("run\\n")\n'
    'sys.exit(subprocess.run([sys.executable, "make_cov.py"]).returncode)\n'
)

CRAPKIT_TOML = f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[exclude]
globs = ["**/node_modules/**", "**/*.test.ts"]

[[lane]]
name = "unit"
command = '"{PY}" run_counted.py'
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""

SCOPE_PATHS = {"src": ("src",)}


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                         timeout=60, check=True)
    return res.stdout


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=300, env=dict(os.environ))


def _lane_runs(repo: Path) -> int:
    marker = repo / "runs.txt"
    return len(marker.read_text(encoding="utf-8").splitlines()) if marker.is_file() else 0


def _the_lane(repo: Path):
    """The repo's own lane, parsed through the public config seam."""
    return load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8")).lanes[0]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mini"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    (root / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")
    (root / "crapkit.toml").write_text(CRAPKIT_TOML, encoding="utf-8")
    (root / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (root / "run_counted.py").write_text(RUN_COUNTED, encoding="utf-8")
    # generated output stays untracked, so a later `git add -A` commits source only
    (root / ".gitignore").write_text(
        ".crapkit/\ncoverage/\nruns.txt\n__pycache__/\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    return root


def _run_and_stamp(repo: Path, lane) -> None:
    """run_lane hands its stamp back instead of writing it; persisting is the
    caller's job, so that N lanes at once cannot lose each other's entry."""
    write_stamps(repo, {lane.artifact: run_lane(repo, lane).stamp})


def _touch_scope_file(repo: Path) -> None:
    app = repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")


# --- lane_unchanged: the reuse decision itself -------------------------------


def test_a_lane_that_never_ran_is_not_unchanged(repo: Path):
    assert lane_unchanged(repo, _the_lane(repo), SCOPE_PATHS) is False


def test_a_fresh_artifact_over_untouched_scopes_is_unchanged(repo: Path):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is True


def test_a_deleted_artifact_is_never_unchanged(repo: Path):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    (repo / lane.artifact).unlink()
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is False


def test_an_uncommitted_edit_under_a_scope_makes_the_lane_changed(repo: Path):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    _touch_scope_file(repo)
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is False


def test_a_commit_after_the_stamp_makes_the_lane_changed(repo: Path):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    _touch_scope_file(repo)
    _commit(repo, "touch src")
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is False


def test_changes_outside_the_scopes_leave_the_lane_unchanged(repo: Path):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    (repo / "docs" / "notes.md").write_text("edited\n", encoding="utf-8")
    _commit(repo, "docs only")
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is True


@pytest.mark.parametrize("scope_paths", [{}, {"src": ()}, {"other": ("src",)}])
def test_a_lane_with_no_scope_prefixes_has_nothing_to_go_stale(repo: Path, scope_paths):
    """No prefixes to compare against means no file can fall inside them."""
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    _touch_scope_file(repo)
    assert lane_unchanged(repo, lane, scope_paths) is True


def test_a_stamp_commit_that_left_history_is_not_unchanged(repo: Path):
    first = _git(repo, "rev-parse", "HEAD").strip()
    _touch_scope_file(repo)
    _commit(repo, "second")
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    _git(repo, "reset", "--hard", "-q", first)
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is False


def test_without_git_the_lane_is_never_unchanged(repo: Path, monkeypatch):
    lane = _the_lane(repo)
    _run_and_stamp(repo, lane)
    monkeypatch.setenv("PATH", str(repo))
    assert lane_unchanged(repo, lane, SCOPE_PATHS) is False


# --- the same decision seen through `crapkit coverage --reuse-unchanged` -----


def test_cli_reuse_unchanged_skips_the_lane_when_nothing_moved(repo: Path):
    first = _run_cli(repo, "coverage", "--json")
    assert first.returncode == 0, first.stderr
    assert _lane_runs(repo) == 1

    second = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")
    assert second.returncode == 0, second.stderr
    assert _lane_runs(repo) == 1, "untouched scopes must not rerun the lane"
    assert "reusing without rerun" in second.stderr
    assert json.loads(second.stdout)["functions"] == json.loads(first.stdout)["functions"]


def test_cli_reuse_unchanged_runs_a_lane_that_has_no_artifact_yet(repo: Path):
    res = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")
    assert res.returncode == 0, res.stderr
    assert _lane_runs(repo) == 1, "with no stamp there is nothing to reuse"
    assert "reusing without rerun" not in res.stderr


def test_cli_reuse_unchanged_reruns_after_an_uncommitted_scope_edit(repo: Path):
    assert _run_cli(repo, "coverage", "--json").returncode == 0
    _touch_scope_file(repo)

    res = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")
    assert res.returncode == 0, res.stderr
    assert _lane_runs(repo) == 2, "a working-tree edit under the scope must rerun the lane"
    assert "reusing without rerun" not in res.stderr


def test_cli_reuse_unchanged_reruns_when_the_stamp_commit_left_history(repo: Path):
    first = _git(repo, "rev-parse", "HEAD").strip()
    _touch_scope_file(repo)
    _commit(repo, "second")
    assert _run_cli(repo, "coverage", "--json").returncode == 0
    _git(repo, "reset", "--hard", "-q", first)

    res = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")
    assert res.returncode == 0, res.stderr
    assert _lane_runs(repo) == 2, "an artifact from a commit off the branch cannot be reused"


# --- sniff_scopes: which paths become scopes --------------------------------


@pytest.mark.parametrize("path", [
    "setup.py",                            # root-level loose file: no dir to name
    "README.md",
    ".venv/lib/site.py",                   # dot-dir
    ".github/workflows/ci.yml",
    "web/node_modules/left-pad/index.js",  # excluded tree
    "src/dist/bundle.js",
    "src/app.test.ts",                     # excluded test file
    "pylib/test_mod.py",
    "pylib/conftest.py",
    "tests/helpers/util.py",               # excluded test dir
    "docs/guide.md",                       # not a source language
])
def test_paths_that_never_become_scopes(path: str):
    assert sniff_scopes([path]) == {}


def test_scopes_group_by_top_level_dir_with_every_language_they_hold():
    files = ["src/app.ts", "src/util.py", "pylib\\mod.py", "docs/guide.md", "setup.py"]
    assert sniff_scopes(files) == {"pylib": ("python",), "src": ("python", "typescript")}


# --- coveragepy lane command validation -------------------------------------

COVPY = """[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[[lane]]
name = "py"
command = {command}
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
"""


def _toml_string(command: str) -> str:
    """A TOML string holding the command verbatim: literal when no single quote
    is in the way, else basic with its backslashes and double quotes escaped."""
    if "'" not in command:
        return f"'{command}'"
    return '"' + command.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _covpy_lane(command: str):
    return load_config_text(COVPY.format(command=_toml_string(command))).lanes[0]


def test_a_command_that_does_not_run_pytest_keeps_its_positional_arguments():
    command = "coverage run -m unittest discover pylib"
    assert _covpy_lane(command).command == command


def test_pytest_named_only_inside_another_token_is_not_a_pytest_run():
    command = "tox -e pytest-lane pylib"
    assert _covpy_lane(command).command == command


def test_flags_after_pytest_are_never_positional_filters():
    command = ("python -m pytest --cov=pylib --cov-branch "
               "--cov-report=json:coverage-py.json -q")
    assert _covpy_lane(command).command == command


def test_a_path_after_the_pytest_flags_still_narrows_a_full_suite_lane():
    with pytest.raises(ConfigError, match="narrows a full-suite"):
        _covpy_lane("python -m pytest --cov=pylib pylib/unit")


# The three transcripts the guard used to refuse (issues #19, #22). Every one is
# a flag's value written the space-separated way, which is how xdist, pytest's
# ini override and the plugin switch are written everywhere.
@pytest.mark.parametrize("command", [
    "python -m pytest --cov --cov-report=json:coverage-py.json -n 8",
    "python -m pytest -o timeout=300 -p no:randomly --cov=pylib",
    "python -m pytest -p no:randomly --cov=pylib",
    "python -m pytest -k smoke --cov=pylib",
    "python -m pytest -m unit --cov=pylib",
    "python -m pytest --deselect pylib/test_mod.py::test_slow --cov=pylib",
])
def test_a_space_separated_flag_value_is_not_a_positional_argument(command: str):
    assert _covpy_lane(command).command == command


def test_a_value_taking_flag_does_not_excuse_the_path_that_follows_its_value():
    with pytest.raises(ConfigError, match="narrows a full-suite"):
        _covpy_lane("python -m pytest -n 8 pylib/unit")


def test_a_quoted_marker_expression_is_one_flag_value_not_four_positionals():
    """The faro transcript: a whitespace split cut "not live and not perf" into
    five tokens and refused 'live' — an argument the shell never hands pytest.
    Double quotes are the portable spelling: sh and cmd.exe both read them."""
    command = ('python -m pytest -m "not live and not perf" --cov --cov-branch '
               "--cov-report=json:.crapkit/cov/py.json")
    assert _covpy_lane(command).command == command


def test_under_sh_a_single_quoted_marker_expression_is_one_value(monkeypatch):
    monkeypatch.setattr(config, "SHELL_IS_CMD", False)
    command = "python -m pytest -m 'not live and not perf' --cov=pylib"
    assert _covpy_lane(command).command == command


def test_under_cmd_a_single_quoted_marker_expression_is_refused_with_the_quote_hint(monkeypatch):
    """cmd.exe knows no quote but the double one: it hands pytest `'not`, `live`,
    `and`, `not`, `perf'`; pytest looks for a file called `perf'` and the lane
    writes no artifact. Reading the command like sh would accept that lane."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    with pytest.raises(ConfigError, match="double quotes") as caught:
        _covpy_lane("python -m pytest -m 'not live and not perf' --cov=pylib")
    assert "'live'" in str(caught.value)


def test_under_cmd_a_backslash_path_is_still_a_narrowing_positional(monkeypatch):
    """POSIX shlex eats the backslash (`tests\\unit` -> `testsunit`), the token
    stops looking like a path, and a lane that really narrows sails through."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    with pytest.raises(ConfigError, match="narrows a full-suite") as caught:
        _covpy_lane(r"python -m pytest -x tests\unit --cov=pylib")
    assert r"tests\unit" in str(caught.value), "the refusal names the token as written"


def test_a_step_chained_after_the_run_is_not_the_runner_arguments():
    """`&&` starts a new process, and the shell hands pytest only the words in
    its own segment. Reading the whole line flat refused `coverage run -m pytest
    && coverage json`, the standard workflow, naming the second command's own
    program as a positional pytest never sees."""
    assert _covpy_lane("python -m pytest --cov=pylib && echo done").command
    assert _covpy_lane("python -m pytest --cov=pylib & echo done").command
    assert _covpy_lane("python -m pytest --cov=pylib || echo failed").command
    assert _covpy_lane("python -m pytest --cov=pylib | tee run.log").command


def test_a_step_chained_before_the_run_still_leaves_the_run_checked():
    assert _covpy_lane("cd tests && python -m pytest --cov=pylib").command
    with pytest.raises(ConfigError, match="narrows a full-suite") as caught:
        _covpy_lane("cd tests && python -m pytest --cov=pylib pylib/unit")
    assert "'pylib/unit'" in str(caught.value)


def test_every_chained_segment_that_runs_pytest_is_checked():
    """Stopping at the first operator would hide a second run that really does
    narrow: both segments are pytest argv, so both are read."""
    with pytest.raises(ConfigError, match="narrows a full-suite") as caught:
        _covpy_lane("python -m pytest --cov=pylib && python -m pytest pylib/unit --cov-append")
    assert "'pylib/unit'" in str(caught.value)


def test_an_operator_inside_a_quoted_value_is_part_of_the_word():
    """`-k "a && b"` is one marker expression, not two commands."""
    assert _covpy_lane('python -m pytest -k "a && b" --cov=pylib').command


def test_the_refusal_names_the_narrowing_path_not_a_word_from_the_next_command(monkeypatch):
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    with pytest.raises(ConfigError, match="narrows a full-suite") as caught:
        _covpy_lane(r"python -m pytest -x tests\unit --cov=pylib && echo x")
    assert r"'tests\unit'" in str(caught.value)
    assert "echo" not in str(caught.value)


def test_under_cmd_a_caret_escaped_quote_holds_the_flag_value_together(monkeypatch):
    """cmd.exe hands pytest `-k` and `not slow`. Keeping the caret in the token
    split the value and refused the lane, naming 'slow^"', two pieces of shell
    syntax glued together and nothing the operator can drop."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    command = 'python -m pytest --cov=pylib -k ^"not slow^"'
    assert _covpy_lane(command).command == command


def test_under_cmd_a_quoted_path_inside_a_flag_value_is_not_a_positional(monkeypatch):
    """cmd.exe hands pytest `--cov-report=json:a b\\py.json`, one argument, so the
    lane runs. Reading the quote as a word boundary refused it and named
    'b\\py.json"', a token that is no argument the operator wrote."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    command = r'python -m pytest --cov --cov-report=json:"a b\py.json"'
    assert _covpy_lane(command).command == command


def test_under_cmd_a_mid_token_quote_in_a_real_positional_is_named_as_written(monkeypatch):
    """The refusal has to name the argument pytest gets, not a fragment of the
    line: `tests/"a b"/test_x.py` is the one path `tests/a b/test_x.py`."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    with pytest.raises(ConfigError, match="narrows a full-suite") as caught:
        _covpy_lane('python -m pytest --cov=pylib tests/"a b"/test_x.py')
    assert "'tests/a b/test_x.py'" in str(caught.value)


def test_a_quoted_positional_path_still_narrows_a_full_suite_lane():
    with pytest.raises(ConfigError, match="narrows a full-suite"):
        _covpy_lane("python -m pytest 'pylib/sub dir' --cov=pylib")


def test_an_unbalanced_quote_falls_back_to_the_whitespace_read():
    """shlex refuses a command sh would refuse too; the lint still runs on the
    naive split rather than crashing config load on a ValueError."""
    with pytest.raises(ConfigError, match="narrows a full-suite"):
        _covpy_lane("python -m pytest pylib/unit --cov 'unclosed")


def test_an_unknown_flag_swallows_a_word_but_never_a_path():
    """A plugin flag crapkit has never heard of takes its value the same way, so
    a word after one is that value. A path is the one token that outranks it."""
    assert _covpy_lane("python -m pytest --dist loadscope").command
    with pytest.raises(ConfigError, match="narrows a full-suite"):
        _covpy_lane("python -m pytest -q pylib/unit")


def test_the_refusal_names_the_attached_form_as_a_remedy():
    """Dropping the token is the wrong fix when it really is a flag's value, and
    the old message offered nothing else. `-n8` / `--numprocesses=8` is the edit
    that both keeps the command working and satisfies the guard."""
    with pytest.raises(ConfigError) as caught:
        _covpy_lane("python -m pytest pylib/unit")
    assert str(caught.value) == (
        "lane 'py': positional argument 'pylib/unit' narrows a full-suite coverage run; "
        "drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), "
        "or set full_suite = false deliberately")


# --- parse_git_log: malformed and mixed logs --------------------------------


def test_paths_before_the_first_author_line_belong_to_no_commit():
    log = ("src/orphan.ts\n"
           "   \n"
           "\x01alice\x021700000000\n"
           "src/real.ts\n\n")
    assert set(parse_git_log(log)) == {"src/real.ts"}


def test_a_non_numeric_timestamp_degrades_to_the_commit_count():
    churn = parse_git_log("\x01alice\x02last tuesday\nsrc/a.ts\n")
    assert churn["src/a.ts"].commits == 1
    assert churn["src/a.ts"].authors == 1
    assert churn["src/a.ts"].weight == 1.0


def test_in_a_half_timestamped_log_only_stamped_files_get_a_time_weight():
    log = ("\x01alice\x021700000000\nsrc/hot.ts\n\n"
           "\x01bob\nsrc/cold.ts\n")
    churn = parse_git_log(log)
    assert churn["src/cold.ts"].weight == 1.0, "no stamp: fall back to the commit count"
    assert churn["src/hot.ts"].weight < 1.0
