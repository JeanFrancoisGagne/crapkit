"""`test-scoped`: which scope's template runs for each file, and the argv it gets.

AGENTS.md (4. Run the owning scope's tests) and docs/configuration.md
(scoped_tests) define it, restated in `route` below:

- each file routes to the scope whose paths entry matches deepest, spelled
  from the root however it was typed (./, a backslash, a cwd below the root);
- `{files}` becomes that scope's files, each quoted, in the order passed, one
  command per scope in name order; a template with no `{files}` runs as written;
- a test file outside every scope routes to the single templated scope, and
  with two there is no owner (exit 3); a file under no scope, or a scope with
  no template, is exit 3; a runner that fails is exit 1.

What a "test file" is has one definition, the file universe's: a path with a
directory component `test`, `tests` or `__tests__`, in any case (README,
[exclude]: "Test directories leave the corpus on their own"; test names such
as test_*.py leave it only through exclude globs). test-scoped also calls
test_*.py, *.test.* and *.spec.* names tests: ruling D14, an open defect.

Each template here is `python record.py SCOPE {files}`, which appends the argv
the shell handed it to argv.jsonl, so the check reads the words the runner
received: the shell's own split is the oracle for the quoting.
"""
from __future__ import annotations

import json
import posixpath
import re
import sys

import pytest

from accuracy.kit import drive, repos, rulings

RECORD = ("import json, sys\n"
          "with open('argv.jsonl', 'a', encoding='utf-8') as out:\n"
          "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n")
FN = "def f(x):\n    return x\n"
POSIX_ONLY = ("src/quote\"d.py", "src/back`tick.py")
NAMES = ("src/a.py", "src/my file.py", "src/dollar$x.py", "src/bêta.py", "src/core/c.py",
         "src/core/d e.py", "src/-dash.py", "src/amp&and.py", "src/semi;colon.py")
FILES = [name for name in NAMES + POSIX_ONLY if sys.platform != "win32" or name not in POSIX_ONLY]


def _config(templates: dict) -> str:
    scopes = "".join(f'[[scope]]\nname = "{name}"\npaths = ["{path}"]\nlanguages = ["python"]\n'
                     "coverage_optional = true\n\n"
                     for name, path in (("app", "src"), ("core", "src/core"), ("lib", "lib")))
    lines = "".join(f'{name} = "{command}"\n' for name, command in templates.items())
    return f"[crapkit]\ntarget = 6\n\n{scopes}[crapkit.scoped_tests]\n{lines}"


TWO = {"app": "python record.py app {files}", "core": "python record.py core {files}"}


def _built(make_repo, templates: dict = TWO) -> drive.Driver:
    files = {"crapkit.toml": _config(templates), "record.py": RECORD, "lib/l.py": FN,
             "tools/test_helper.py": FN, "tools/x.spec.py": FN, "tests/test_t.py": FN,
             "tools/Tests/t.py": FN, **{name: FN for name in FILES}}
    return drive.Driver(make_repo(repos.Spec(steps=(repos.Commit(files=files),))).root)


def _ran(driver: drive.Driver, *paths: str, where: str = "") -> tuple[int, list, str]:
    """(exit, the argv each command received, stderr) for `test-scoped PATHS`."""
    log = driver.root / "argv.jsonl"
    if log.exists():
        log.unlink()
    runner = drive.Driver(driver.root / where) if where else driver
    result = runner.run("test-scoped", *paths)
    got = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] \
        if log.exists() else []
    return result.code, got, result.stderr


# --- the model ---------------------------------------------------------------------------------

SCOPES = {"app": ("src",), "core": ("src/core",), "lib": ("lib",)}
TEST_DIR = re.compile(r"(^|/)(tests?|__tests__)/", re.IGNORECASE)


def spelled(path: str, cwd: str = "") -> str:
    """The root-relative spelling: backslashes as slashes, ./ and .. resolved,
    a cwd below the root prefixed."""
    return posixpath.normpath(f"/{cwd}/{path}".replace("\\", "/")).lstrip("/")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def owner(path: str) -> str | None:
    """The scope whose paths entry matches deepest."""
    matches = [(len(prefix), name) for name, prefixes in SCOPES.items() for prefix in prefixes
               if _under(path, prefix)]
    return max(matches, default=(0, None))[1]


def is_test(path: str) -> bool:
    """The file universe's rule: a test directory component, any case."""
    return TEST_DIR.search(path) is not None


def route(path: str, templates: dict) -> str:
    """The scope a file runs under, or `refused`."""
    scope = owner(path)
    if scope is None and is_test(path) and len(templates) == 1:
        scope = next(iter(templates))
    return scope if scope in templates else "refused"


def expected_runs(paths: list[str], templates: dict) -> tuple[int, list]:
    """(exit, one argv per scope in name order) for a passing runner."""
    routed = [(route(path, templates), path) for path in paths]
    if any(scope == "refused" for scope, _ in routed):
        return 3, []
    return 0, [[scope, *files] for scope, files in sorted(_grouped(routed).items())]


def _grouped(routed: list[tuple[str, str]]) -> dict:
    """{scope: its files in the order they were passed}."""
    by_scope: dict = {}
    for scope, path in routed:
        by_scope.setdefault(scope, []).append(path)
    return by_scope


# --- the checks ----------------------------------------------------------------------------------

@pytest.mark.process
def test_every_file_reaches_its_scope_as_one_word(make_repo):
    """All the special names at once: each reaches the runner as the one word
    it is, under the deepest scope, one command per scope in name order."""
    driver = _built(make_repo)
    code, got, stderr = _ran(driver, *FILES)
    assert (code, got) == expected_runs(FILES, TWO), stderr


@pytest.mark.process
@pytest.mark.parametrize("typed, cwd", [("./src/a.py", ""), ("src\\a.py", ""), ("a.py", "src"),
                                        ("core/c.py", "src"), ("./src/core/../a.py", "")],
                         ids=["dot-slash", "backslash", "from-src", "nested-from-src", "dot-dot"])
def test_every_spelling_routes_as_the_root_relative_path(make_repo, typed, cwd):
    driver = _built(make_repo)
    assert _ran(driver, typed, where=cwd)[:2] == expected_runs([spelled(typed, cwd)], TWO)


@pytest.mark.process
@pytest.mark.parametrize("path", ["tests/test_t.py", "tools/Tests/t.py"])
@pytest.mark.parametrize("templates", [{"app": TWO["app"]}, TWO], ids=["one-template", "two"])
def test_a_test_file_outside_every_scope(make_repo, path, templates):
    """One templated scope owns it; two leave it without an owner (exit 3)."""
    driver = _built(make_repo, templates)
    code, got, stderr = _ran(driver, path)
    assert (code, got) == expected_runs([path], templates), stderr


@pytest.mark.process
@pytest.mark.parametrize("path", ["tools/test_helper.py", "tools/x.spec.py"])
@rulings.applies("D14")
def test_a_test_named_file_outside_a_test_directory_is_source(make_repo, path):
    """Under the file universe's rule tools/test_helper.py is source that no
    scope claims: exit 3, runner not started. test-scoped reads the name as a
    test and hands it to the one templated scope."""
    driver = _built(make_repo, {"app": TWO["app"]})
    code, got, _ = _ran(driver, path)
    want = expected_runs([path], {"app": TWO["app"]})
    said = "routed to app" if got else f"exit {code}"
    rulings.pin_ruling("D14", crapkit=said, oracle=f"exit {want[0]}")


@pytest.mark.process
def test_a_file_under_a_scope_with_no_template_is_refused(make_repo):
    assert _ran(_built(make_repo), "lib/l.py")[:2] == expected_runs(["lib/l.py"], TWO) == (3, [])


@pytest.mark.process
def test_a_template_with_no_files_runs_as_written(make_repo):
    templates = {"app": "python record.py whole-suite", "core": TWO["core"]}
    driver = _built(make_repo, templates)
    assert _ran(driver, "src/a.py", "src/my file.py")[:2] == (0, [["whole-suite"]])


@pytest.mark.process
def test_a_failing_runner_exits_1(make_repo):
    templates = {"app": "python -c \\\"raise SystemExit(5)\\\" {files}", "core": TWO["core"]}
    assert _ran(_built(make_repo, templates), "src/a.py")[0] == 1


@pytest.mark.process
def test_brief_hands_back_the_test_scoped_call_for_its_file(make_repo):
    """AGENTS.md: commands.scoped_tests calls `crapkit test-scoped` with the
    packet's literal file; run through the shell it reaches the same runner."""
    driver = _built(make_repo)
    assert driver.run("coverage").code == 0
    command = driver.run("brief", "src/my file.py", "f", "--json").json()["commands"]["scoped_tests"]
    assert "test-scoped" in command and "my file.py" in command
    assert _ran(driver, "src/my file.py")[:2] == expected_runs(["src/my file.py"], TWO)


# --- the template init writes ---------------------------------------------------------------------

INIT_LAYOUT = {"pkg/__init__.py": "", "pkg/x.py": "def x(a):\n    return a\n",
               "tests/test_x.py": "from pkg.x import x\n\n\ndef test_x():\n    assert x(1) == 1\n"}


@pytest.mark.process
def test_init_s_scoped_command_collects_a_test(make_repo):
    """docs/configuration.md, scoped_tests: with no test file under pkg/, init
    writes the whole-suite form, so `test-scoped pkg/x.py` runs the suite in
    tests/ and passes, where a {files} form hands pytest a source path and
    collects nothing (runner exit 5, crapkit exit 1). With no lane proving the
    runner, init writes the block commented (AGENTS.md: uncommenting is
    usually the whole job), so the check uncomments it as a user would."""
    root = make_repo(repos.Spec(steps=(repos.Commit(files=INIT_LAYOUT),))).root
    driver = drive.Driver(root)
    assert driver.run("init").code == 0
    config = (root / "crapkit.toml").read_text(encoding="utf-8")
    template = re.search(r"^# pkg = \"(.*)\"$", config, re.M)
    assert template and "{files}" not in template.group(1), config
    assert "# pkg: no test file under pkg/" in config, config
    live = config.replace("# [crapkit.scoped_tests]", "[crapkit.scoped_tests]")
    (root / "crapkit.toml").write_text(live.replace(template.group(0), template.group(0)[2:]),
                                       encoding="utf-8")
    result = driver.run("test-scoped", "pkg/x.py")
    assert result.code == 0, result.stdout + result.stderr
