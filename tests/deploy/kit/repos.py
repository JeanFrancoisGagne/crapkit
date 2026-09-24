"""Fixture repos a cell adopts crapkit into, built once per session.

    repo = repos.checkout(box, "py-pytest", cache=templates)

The first cell that asks for a template builds it under `cache` behind a file
lock (xdist workers share the directory), through tests/e2e/repo_templates.py;
every cell then gets its own copy. A template that holds an absolute path it
cannot carry (a .venv) is built fresh for each checkout instead.

Templates: py-pytest, ts-vitest-only, jest, go-rust-shell, uv-project,
poetry-project, pdm-project, pipenv-project, dot-venv, subdir-root (a
monorepo whose crapkit root sits below the git top), submodule, zero-commit,
not-git, brownfield and consumer. None of them holds crapkit config: adopting
crapkit is the cell's job.
"""
from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import hang_guard
from e2e import repo_templates

GRADE_PY = '''def grade(score, attempts, late, bonus):
    if score > 90 and not late:
        return "A"
    if score > 80:
        return "B" if attempts < 3 else "C"
    if bonus:
        return "C"
    if late and attempts > 2:
        return "F"
    return "D"


def curve(scores, floor):
    return [max(score, floor) for score in scores]
'''
TEST_GRADE_PY = '''from calc.grade import curve, grade


def test_an_early_high_score_is_an_a():
    assert grade(95, 1, False, False) == "A"


def test_curve_lifts_to_the_floor():
    assert curve([40, 90], 50) == [50, 90]
'''
PYPROJECT_PY = '''[project]
name = "calc"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
'''
GRADE_TS = '''export function grade(score: number, attempts: number, late: boolean, bonus: boolean): string {
  if (score > 90 && !late) return "A";
  if (score > 80) return attempts < 3 ? "B" : "C";
  if (bonus) return "C";
  if (late && attempts > 2) return "F";
  return "D";
}
'''
GRADE_TEST_TS = '''import { describe, expect, it } from "vitest";
import { grade } from "./grade";

describe("grade", () => {
  it("gives an early high score an A", () => expect(grade(95, 1, false, false)).toBe("A"));
});
'''
GRADE_JS = GRADE_TS.replace(": number", "").replace(": boolean", "").replace("): string", ")").replace(
    "export function", "function") + "module.exports = { grade };\n"
GRADE_TEST_JS = '''const { grade } = require("./grade");

test("an early high score is an A", () => expect(grade(95, 1, false, false)).toBe("A"));
'''
MAIN_GO = '''package main

import "fmt"

func classify(n int, strict bool) string {
\tif n < 0 {
\t\treturn "negative"
\t}
\tfor i := 0; i < n; i++ {
\t\tif strict && i%2 == 1 {
\t\t\treturn "odd"
\t\t}
\t}
\treturn "ok"
}

func main() { fmt.Println(classify(3, true)) }
'''
LIB_RS = '''pub fn label(n: u8) -> &'static str {
    match n {
        0 => "zero",
        1 => "one",
        2 => "two",
        3 => "three",
        _ => "many",
    }
}
'''
DEPLOY_SH = '''#!/bin/sh
deploy() {
    case "$1" in
        prod) [ -n "$2" ] && echo "prod $2" || echo "prod" ;;
        stage) echo "stage" ;;
        *) echo "unknown" ;;
    esac
}
'''


def _write(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _commit(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def _init(box, repo: Path) -> None:
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)


def _python_files() -> dict[str, str]:
    return {"calc/__init__.py": "", "calc/grade.py": GRADE_PY, "tests/test_grade.py": TEST_GRADE_PY,
            ".gitignore": ".venv/\n__pycache__/\n"}


def _npm_lock(box, repo: Path) -> None:
    """package-lock.json from the npm cache, offline; node_modules is not kept."""
    box.run(["npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=repo, expect=0)
    shutil.rmtree(repo / "node_modules", ignore_errors=True)


def _fixture_version(name: str, box) -> str:
    package = Path(box.toolchain["npm_fixtures"]) / "package.json"
    return json.loads(package.read_text(encoding="utf-8"))["devDependencies"][name]


# --- the templates ----------------------------------------------------------------

def py_pytest(box, repo: Path) -> None:
    _init(box, repo)
    _write(repo, {**_python_files(), "pyproject.toml": PYPROJECT_PY})
    _commit(box, repo, "calc: grades and a curve")


def ts_vitest_only(box, repo: Path) -> None:
    _init(box, repo)
    package = {"name": "calc", "private": True, "type": "module", "scripts": {"test": "vitest run"},
               "devDependencies": {"vitest": _fixture_version("vitest", box)}}
    _write(repo, {"package.json": json.dumps(package, indent=2) + "\n", "src/grade.ts": GRADE_TS,
                  "src/grade.test.ts": GRADE_TEST_TS, ".gitignore": "node_modules/\n"})
    _npm_lock(box, repo)
    _commit(box, repo, "calc: grades in TypeScript")


def jest(box, repo: Path) -> None:
    _init(box, repo)
    package = {"name": "calc", "private": True, "scripts": {"test": "jest"},
               "devDependencies": {"jest": _fixture_version("jest", box),
                                   "jest-junit": _fixture_version("jest-junit", box)}}
    _write(repo, {"package.json": json.dumps(package, indent=2) + "\n", "src/grade.js": GRADE_JS,
                  "src/grade.test.js": GRADE_TEST_JS, ".gitignore": "node_modules/\n"})
    _npm_lock(box, repo)
    _commit(box, repo, "calc: grades under jest")


def go_rust_shell(box, repo: Path) -> None:
    _init(box, repo)
    _write(repo, {"go/main.go": MAIN_GO, "rust/src/lib.rs": LIB_RS, "scripts/deploy.sh": DEPLOY_SH})
    _commit(box, repo, "three languages, no Python")


def uv_project(box, repo: Path) -> None:
    _init(box, repo)
    # uv lock resolves for every Python the project allows; the Windows and
    # macOS wheelhouse rows start at 3.12, where no tomli backport is needed.
    pyproject = PYPROJECT_PY.replace(">=3.11", ">=3.12") + '\n[dependency-groups]\ndev = ["pytest", "pytest-cov"]\n'
    _write(repo, {**_python_files(), "pyproject.toml": pyproject})
    box.run(["uv", "lock"], cwd=repo, expect=0)
    _commit(box, repo, "calc under uv")


def poetry_project(box, repo: Path) -> None:
    _init(box, repo)
    pyproject = ('[tool.poetry]\nname = "calc"\nversion = "0.1.0"\ndescription = ""\nauthors = []\n'
                 'package-mode = false\n\n[tool.poetry.dependencies]\npython = "^3.11"\n\n'
                 '[tool.poetry.group.dev.dependencies]\npytest = "*"\npytest-cov = "*"\n\n'
                 '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n')
    _write(repo, {**_python_files(), "pyproject.toml": pyproject})
    _commit(box, repo, "calc under poetry")


def pdm_project(box, repo: Path) -> None:
    _init(box, repo)
    pyproject = PYPROJECT_PY + '\n[tool.pdm]\ndistribution = false\n\n[dependency-groups]\ndev = ["pytest", "pytest-cov"]\n'
    _write(repo, {**_python_files(), "pyproject.toml": pyproject})
    _commit(box, repo, "calc under pdm")


def pipenv_project(box, repo: Path) -> None:
    _init(box, repo)
    pipfile = '[[source]]\nurl = "https://pypi.org/simple"\nverify_ssl = true\nname = "pypi"\n\n' \
              '[dev-packages]\npytest = "*"\npytest-cov = "*"\n\n[requires]\npython_version = "3.12"\n'
    _write(repo, {**_python_files(), "Pipfile": pipfile, "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = .\n"})
    _commit(box, repo, "calc under pipenv")


def dot_venv(box, repo: Path) -> None:
    """py-pytest plus its own .venv holding pytest and pytest-cov, installed offline."""
    py_pytest(box, repo)
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(repo / ".venv")], expect=0)
    python = repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    box.run([str(python), "-m", "pip", "install", "-q", "pytest", "pytest-cov"], expect=0)


def subdir_root(box, repo: Path) -> None:
    """A monorepo: the Python package crapkit adopts lives in packages/api."""
    _init(box, repo)
    api = {f"packages/api/{name}": text for name, text in {**_python_files(), "pyproject.toml": PYPROJECT_PY}.items()}
    _write(repo, {**api, "packages/web/index.js": GRADE_JS, "README.md": "# monorepo\n"})
    _commit(box, repo, "a monorepo with an api package")


def submodule(box, repo: Path) -> None:
    library = repo.parent.parent / "submodule-library"
    shutil.rmtree(library, ignore_errors=True)
    library.mkdir(parents=True)
    go_rust_shell(box, library)
    py_pytest(box, repo)
    box.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", library.as_uri(), "vendor/lib"],
            cwd=repo, expect=0)
    _commit(box, repo, "vendor a library as a submodule")


def zero_commit(box, repo: Path) -> None:
    _init(box, repo)
    _write(repo, _python_files())
    box.run(["git", "add", "-A"], cwd=repo, expect=0)


def not_git(box, repo: Path) -> None:
    _write(repo, _python_files())


def brownfield(box, repo: Path) -> None:
    """Five commits of history, tests for one module and none for the other."""
    py_pytest(box, repo)
    for number in range(1, 5):
        _write(repo, {f"calc/legacy_{number}.py": GRADE_PY.replace("def grade", f"def grade_{number}")})
        _commit(box, repo, f"legacy module {number}")


def consumer(box, repo: Path) -> None:
    """A repo that will call the GitHub Action: main, then a branch with a breach."""
    py_pytest(box, repo)
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    _write(repo, {"calc/breach.py": GRADE_PY.replace("def grade", "def breach").replace(
        'return "D"', 'if attempts > 5 and bonus:\n        return "E"\n    return "D"')})
    _commit(box, repo, "a change that raises complexity")
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)


TEMPLATES = {"py-pytest": py_pytest, "ts-vitest-only": ts_vitest_only, "jest": jest, "go-rust-shell": go_rust_shell,
             "uv-project": uv_project, "poetry-project": poetry_project, "pdm-project": pdm_project,
             "pipenv-project": pipenv_project, "dot-venv": dot_venv, "subdir-root": subdir_root,
             "submodule": submodule, "zero-commit": zero_commit, "not-git": not_git, "brownfield": brownfield,
             "consumer": consumer}
# A venv records its own absolute path, so a copy of one is not a venv.
FRESH = {"dot-venv"}


# --- building and copying ----------------------------------------------------------

def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


@contextmanager
def file_lock(path: Path):
    """An exclusive lock on `path`, waited for under the hang bound."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        hang_guard.wait_until(lambda: _try_lock(handle), what=f"the lock on {path}")
        yield


def built(box, name: str, cache: Path) -> Path:
    """The session's built template, built now if no worker has built it yet."""
    (cache / name).mkdir(parents=True, exist_ok=True)
    with file_lock(cache / f"{name}.lock"):
        return repo_templates.template(cache / name / "staging", name, lambda repo: TEMPLATES[name](box, repo))


def _install_npm_fixtures(box, project: Path) -> None:
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(Path(box.toolchain["npm_fixtures"]) / name, project / name)
    box.run(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=project, expect=0)


def npm_fixtures(box, cache: Path) -> Path:
    """The npm-fixtures project installed offline once per session: each MCP
    SDK minor under its alias (sdk-1-12 ... sdk-1-29), vitest and jest. Cells
    read it and never write to it; mcp_node_client.mjs takes it as --fixtures.
    One install serves every TS-SDK profile: a per-cell `npm ci` of its 420
    packages ran past the hang bound on a busy Windows machine."""
    (cache / "npm-fixtures").mkdir(parents=True, exist_ok=True)
    with file_lock(cache / "npm-fixtures.lock"):
        return repo_templates.template(cache / "npm-fixtures" / "staging", "npm-fixtures",
                                       lambda project: _install_npm_fixtures(box, project))


def checkout(box, name: str, *, cache: Path, dest: Path | None = None, repo_name: str | None = None) -> Path:
    """A private copy of template `name` for this cell, at `dest` or at
    <sandbox root>/<repo_name> (the template's name when neither is given):
    repo_name="my repo é" is the odd-path cells' checkout."""
    dest = dest or box.root / (repo_name or name)
    if name in FRESH:
        dest.mkdir(parents=True)
        TEMPLATES[name](box, dest)
        return dest
    return repo_templates.copy_of(built(box, name, cache), dest)
