r"""Every spelling of a crapkit.toml path scores exactly what git's spelling scores.

A committed crapkit.toml is read on every OS, and the person who wrote it typed
paths the way their shell does. Before 0ae9ade, `path_prefix = 'backend\'`
keyed each measured file outside its scope and a tested function scored
untested; `[exclude] globs = ['src\gen\**']` excluded nothing and generated code
stayed in the CRAP load; `paths = ['Src']` claimed nothing on a case-insensitive
disk; an absolute scope path folded into a prefix that named nothing. Each run
exited 0.

Each test here builds one repo, scores it with git's spelling as the control,
then with one other spelling, and compares the scored rows (path, function,
ccn, coverage, CRAP) and the run's totals with zero tolerance. The lanes copy
fixed coverage.py reports, so the numbers depend on the config alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner()


def _folds_case(folder: Path) -> bool:
    """Does the filesystem under `folder` open a name in another letter case?"""
    probe = folder / "Case-Probe"
    probe.write_text("", encoding="utf-8")
    try:
        return (folder / "case-probe").exists()
    finally:
        probe.unlink()


def _report(functions: dict[str, dict]) -> str:
    """A coverage.py JSON report: per file, its functions' line and branch counts."""
    files = {}
    for path, spec in functions.items():
        name, start, run, missing, branches, taken = spec
        files[path] = {"executed_lines": run, "missing_lines": missing,
                       "functions": {name: {"start_line": start, "executed_lines": run,
                                            "missing_lines": missing,
                                            "summary": {"covered_lines": len(run),
                                                        "num_statements": len(run) + len(missing),
                                                        "num_branches": branches,
                                                        "covered_branches": taken}}}}
    return json.dumps({"meta": {"branch_coverage": True}, "files": files})


SOURCES = {
    "backend/pkg/__init__.py": "",
    "backend/pkg/mod.py": "def f(x):\n    if x:\n        return 1\n    return 2\n",
    "backend/pkg/two.py": ("def g(x, y):\n    if x and y:\n        return 1\n"
                           "    if y:\n        return 2\n    return 3\n"),
    "src/app.py": "def h(n):\n    if n:\n        return 1\n    return 0\n",
    "src/gen/client.py": ("def k(a, b, c):\n    if a:\n        return 1\n    if b:\n"
                          "        return 2\n    if c:\n        return 3\n    return 4\n"),
    # The backend suite ran from backend/, so its report keys start at pkg/.
    "reports/backend.json": _report({
        "pkg/mod.py": ("f", 1, [1, 2, 3, 4], [], 2, 2),
        "pkg/two.py": ("g", 1, [1, 2, 4, 6], [3, 5], 4, 2)}),
    "reports/src.json": _report({
        "src/app.py": ("h", 1, [1, 2, 4], [3], 2, 1),
        "src/gen/client.py": ("k", 1, [1, 2, 3], [4, 5, 6, 7, 8], 6, 1)}),
    "copy_report.py": ("import os, shutil, sys\n"
                       "os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True)\n"
                       "shutil.copyfile(sys.argv[1], sys.argv[2])\n"),
    ".gitignore": ".crapkit/\n",
}

CONTROL = {"backend_path": "backend", "src_path": "src", "prefix": "backend",
           "glob": "src/gen/**", "be_artifact": ".crapkit/cov/be.json"}


def _config(backend_path: str, src_path: str, prefix: str, glob: str, be_artifact: str) -> str:
    """crapkit.toml in TOML literal strings, so each spelling reaches the loader
    as typed."""
    return (
        "[crapkit]\nworklist_floor = 1\n\n"
        "[[scope]]\nname = 'backend'\n"
        f"paths = ['{backend_path}']\nlanguages = ['python']\n\n"
        f"[[scope]]\nname = 'src'\npaths = ['{src_path}']\nlanguages = ['python']\n\n"
        f"[exclude]\nglobs = ['{glob}', 'copy_report.py']\n\n"
        "[[lane]]\nname = 'be'\nparser = 'coveragepy'\nscopes = ['backend']\n"
        f"path_prefix = '{prefix}'\nartifact = '{be_artifact}'\n"
        "command = 'python copy_report.py reports/backend.json .crapkit/cov/be.json'\n\n"
        "[[lane]]\nname = 'web'\nparser = 'coveragepy'\nscopes = ['src']\n"
        "artifact = '.crapkit/cov/src.json'\n"
        "command = 'python copy_report.py reports/src.json .crapkit/cov/src.json'\n")


def _repo(tmp_path: Path, name: str, **spelled: str) -> Path:
    repo = tmp_path / name
    for rel, body in {**SOURCES, "crapkit.toml": _config(**{**CONTROL, **spelled})}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "sources")
    return repo


_TOTALS = ("files", "functions", "measured", "untested", "no_lane", "crap_load", "over_target",
           "grade", "lane_failures", "unmeasured_scopes")


def _scored(repo: Path, *extra: str) -> tuple[list[tuple], dict]:
    """The scored rows and the run's totals, the two a spelling must not move."""
    run = run_cli(repo, "coverage", "--json", *extra)
    assert run.returncode == 0, run.stdout + run.stderr
    totals = json.loads(run.stdout)
    listed = run_cli(repo, "worklist", "--json", "--top", "1000")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    return _rows(json.loads(listed.stdout)), {key: totals[key] for key in _TOTALS}


def _rows(worklist: dict) -> list[tuple]:
    rows = [*worklist["active"], *worklist["dormant_top"]]
    return sorted((row["path"], row["function"], row["ccn"], row["cov"], row["crap"])
                  for row in rows)


@pytest.fixture(scope="module")
def control(tmp_path_factory) -> tuple[list[tuple], dict]:
    return _scored(_repo(tmp_path_factory.mktemp("control"), "repo"))


def test_the_control_scores_what_the_reports_say(control):
    """Four functions: the generated client excluded, backend keyed under its
    scope through path_prefix, every one of them measured."""
    rows, totals = control
    assert [row[:2] for row in rows] == [("backend/pkg/mod.py", "f( x )"),
                                         ("backend/pkg/two.py", "g( x , y )"),
                                         ("src/app.py", "h( n )")], rows
    assert (totals["measured"], totals["untested"], totals["lane_failures"]) == (3, 0, {})


def _same_as_control(tmp_path: Path, control, **spelled: str) -> None:
    assert _scored(_repo(tmp_path, "repo", **spelled)) == control, spelled


def _case_rows(tmp_path: Path) -> None:
    if not _folds_case(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (Windows NTFS, macOS APFS)")


# --- path_prefix (PC2) ---------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["backend/", "backend//", "backend\\", "./backend", "./backend/",
                                    ".\\backend", ".\\backend\\", "/backend", "/backend/"])
def test_a_path_prefix_spelling_scores_like_the_control(tmp_path, control, prefix):
    _same_as_control(tmp_path, control, prefix=prefix)


@pytest.mark.parametrize("prefix", ["Backend", "BACKEND/"])
def test_a_path_prefix_in_another_case_scores_like_the_control(tmp_path, control, prefix):
    _case_rows(tmp_path)
    _same_as_control(tmp_path, control, prefix=prefix)


# --- [exclude] globs (PC1) -------------------------------------------------------------

@pytest.mark.parametrize("glob", ["src\\gen\\**", "src\\gen/**", "**\\gen\\**", "src\\gen\\*.py",
                                  "./src/gen/**", ".\\src\\gen\\**", "/src/gen/**", "src/gen/",
                                  "src\\gen\\", "SRC/GEN/**", "**/gen/**"])
def test_an_exclude_glob_spelling_scores_like_the_control(tmp_path, control, glob):
    _same_as_control(tmp_path, control, glob=glob)


# --- scope paths (PL7, PC6) --------------------------------------------------------------

@pytest.mark.parametrize("src_path", ["./src", "./src/", "src/", "src\\", ".\\src", "/src"])
def test_a_relative_scope_path_spelling_scores_like_the_control(tmp_path, control, src_path):
    _same_as_control(tmp_path, control, src_path=src_path, backend_path=src_path.replace("src",
                                                                                        "backend"))


@pytest.mark.parametrize("src_path", ["Src", "SRC/", ".\\Src"])
def test_a_scope_path_in_another_case_scores_like_the_control(tmp_path, control, src_path):
    """PL7: git names the directory `src`, and `Src/` claimed none of its files,
    so the scope scored 0 functions at exit 0."""
    _case_rows(tmp_path)
    _same_as_control(tmp_path, control, src_path=src_path, backend_path="Backend")


def test_a_scope_path_in_another_case_names_nothing_on_a_case_sensitive_disk(tmp_path):
    """The Linux control: `Src` is another directory there, so the scope is empty
    and doctor says so, as it should."""
    if _folds_case(tmp_path):
        pytest.skip("needs a case-sensitive filesystem (Linux ext4)")
    repo = _repo(tmp_path, "repo", src_path="Src")

    rows, _ = _scored(repo)

    assert [row[0] for row in rows] == ["backend/pkg/mod.py", "backend/pkg/two.py"], rows
    assert "FAIL scope 'src': 0 files" in run_cli(repo, "doctor").stdout


def _absolute(repo: Path, which: str) -> str:
    """The repo's src/ spelled absolutely, the way each source writes it."""
    src = (repo / "src").resolve().as_posix()
    tail = src[2:] if src[1:2] == ":" else src
    return {"posix": tail, "msys": f"/c{tail}", "wsl": f"/mnt/c{tail}",
            "unc": "\\\\server\\share\\src"}[which]


def _respelled(repo: Path, **spelled: str) -> None:
    (repo / "crapkit.toml").write_text(_config(**{**CONTROL, **spelled}), encoding="utf-8")


@pytest.mark.parametrize("which", ["posix", "msys", "wsl", "unc"])
def test_an_absolute_scope_path_is_refused_instead_of_scoring_nothing(tmp_path, which):
    """PC6: it loaded, scored the scope at 0 files and exited 0, and only doctor
    said anything. It is refused at load, naming the path as written."""
    repo = _repo(tmp_path, "repo")
    written = _absolute(repo, which)
    _respelled(repo, src_path=written)

    res = run_cli(repo, "coverage")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"scope 'src': path {written!r} names nothing under the root" in res.stderr, res.stderr


@pytest.mark.parametrize("src_path", ["src/../src", "..\\repo\\src", "../repo/src"])
def test_a_scope_path_that_climbs_out_of_the_root_is_refused(tmp_path, src_path):
    """The green rows no test held: `..` is refused as a segment, whatever the
    separator, rather than read into an empty scope."""
    repo = _repo(tmp_path, "repo")
    _respelled(repo, src_path=src_path)

    res = run_cli(repo, "coverage")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"path {src_path!r} can never match a tracked file" in res.stderr, res.stderr


# --- a lane artifact spelled from Windows, reused ----------------------------------------

@pytest.mark.parametrize("artifact", [".crapkit\\cov\\be.json", "./.crapkit/cov/be.json",
                                      ".\\.crapkit\\cov\\be.json"])
def test_an_artifact_spelling_scores_like_the_control_and_reuses(tmp_path, control, artifact):
    """boundary-6: the lane wrote .crapkit/cov/be.json, and on Linux a key
    spelled `.crapkit\\cov\\be.json` opened another name and failed the lane.
    It reads the file written, on a run and on --reuse-artifacts."""
    repo = _repo(tmp_path, "repo", be_artifact=artifact)

    assert _scored(repo) == control
    assert _scored(repo, "--reuse-artifacts") == control
