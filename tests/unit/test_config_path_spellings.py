r"""Every path a crapkit.toml carries, in every spelling a person types one.

Scope paths and lane inputs were spelled the way git spells a path at load:
`/` between directories, no `./`, no trailing separator. The rest of the file's
paths were read as written. `[exclude] globs = ['web\dist\**']` excluded
nothing on either OS, `path_prefix = 'api\'` keyed every measured file
`api\/src/calc.py` and scored a tested function untested, and a lane committed
from Windows with `cwd = 'api\'` or `artifact = '.crapkit\cov.json'` died on
Linux CI with a traceback or `produced no artifact`. On a case-insensitive disk
`paths = ["Src"]` claimed nothing, because git names the directory `src`.

Each test loads the config against a real tree and hands the loaded value to
the reader that consumes it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crapkit import coverage_py
from crapkit.config import load_config_text
from crapkit.doctor import artifact_litter, scope_top_dirs
from crapkit.errors import ConfigError
from crapkit.lane_command import launch_spec
from crapkit.universe import scan_files

from path_spellings import need_case_insensitive, need_case_sensitive, only_posix, only_windows

SCOPES = """
[[scope]]
name = "web"
paths = ["web"]
languages = ["javascript"]

[[scope]]
name = "backend"
paths = ["backend"]
languages = ["python"]
"""


def _tree(root: Path) -> Path:
    for rel in ("web/src/a.js", "web/dist/a.js", "web/gen/b.py", "backend/pkg/mod.py",
                ".crapkit/cov.json", ".crapkit/junit.xml", "backend/cov.json"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")
    return root


def _lane(**fields: str) -> str:
    extra = "".join(f"{key} = '{value}'\n" for key, value in fields.items())
    return ("[[lane]]\nname = 'py'\ncommand = 'python -m pytest'\nparser = 'coveragepy'\n"
            "scopes = ['backend']\nfull_suite = false\n"
            + ("" if "artifact" in fields else "artifact = '.crapkit/cov.json'\n") + extra)


def _load(root: Path, text: str):
    return load_config_text(SCOPES + text, root=root)


# --- [exclude] globs ----------------------------------------------------------

@pytest.mark.parametrize("glob, gone", [
    ("web/dist/**", "web/dist/a.js"),
    ("WEB/DIST/**", "web/dist/a.js"),
    ("web\\dist\\**", "web/dist/a.js"),
    ("web/dist\\**", "web/dist/a.js"),
    ("./web/dist/**", "web/dist/a.js"),
    (".\\web\\dist\\**", "web/dist/a.js"),
    ("web/dist/", "web/dist/a.js"),
    ("/web/dist/**", "web/dist/a.js"),
    ("**/dist/**", "web/dist/a.js"),
    ("**\\dist\\**", "web/dist/a.js"),
    ("web\\dist\\*.js", "web/dist/a.js"),
])
def test_an_exclude_glob_in_any_spelling_leaves_its_files_out(tmp_path, glob, gone):
    cfg = _load(_tree(tmp_path), f"[exclude]\nglobs = ['{glob}']\n")

    scored = scan_files(["web/src/a.js", "web/dist/a.js"], cfg).by_scope["web"]

    assert gone not in scored, (glob, cfg.exclude_globs)
    assert "web/src/a.js" in scored


# --- path_prefix --------------------------------------------------------------

REPORT = {"meta": {"branch_coverage": True}, "files": {"pkg/mod.py": {"functions": {
    "f": {"start_line": 1, "executed_lines": [1, 2], "missing_lines": [],
          "summary": {"covered_lines": 2, "num_statements": 2,
                      "num_branches": 2, "covered_branches": 2}}}}}}


@pytest.mark.parametrize("prefix", ["backend", "backend/", "backend//", "backend\\", "./backend",
                                    ".\\backend", "/backend", "./backend/"])
def test_a_path_prefix_in_any_spelling_keys_the_file_under_its_scope(tmp_path, prefix):
    """The key has to be git's `backend/pkg/mod.py`, or the scope's one tested
    function scores untested with a warning that says to set path_prefix."""
    root = _tree(tmp_path)
    cfg = _load(root, _lane(path_prefix=prefix))
    artifact = root / ".crapkit" / "cov.json"
    artifact.write_text(json.dumps(REPORT), encoding="utf-8")

    per_file, _, _ = coverage_py.read(cfg.lanes[0], root, artifact)

    assert list(per_file) == ["backend/pkg/mod.py"], cfg.lanes[0].path_prefix


def test_a_path_prefix_takes_a_backslash_key_too(tmp_path):
    root = _tree(tmp_path)
    cfg = _load(root, _lane(path_prefix="backend"))
    artifact = root / ".crapkit" / "cov.json"
    artifact.write_text(json.dumps({**REPORT, "files": {"pkg\\mod.py": REPORT["files"]["pkg/mod.py"]}}),
                        encoding="utf-8")

    per_file, _, _ = coverage_py.read(cfg.lanes[0], root, artifact)

    assert list(per_file) == ["backend/pkg/mod.py"]


# A key in another letter case than the directories list. coverage.py on
# Windows corrects a key's case itself; on macOS it keeps the case the import
# system handed it, and `PKG/mod.py` named no file git tracks, so the tested
# function scored untested.
CASED_KEYS = [("backend", "PKG/mod.py"), ("backend", "pkg/MOD.py"), ("backend", "PKG\\MOD.py"),
              ("", "BACKEND/pkg/mod.py"), ("", "Backend\\Pkg\\mod.py"),
              ("", "./BACKEND/pkg/mod.py")]


def _cased_report(root: Path, prefix: str, key: str):
    cfg = _load(root, _lane(path_prefix=prefix) if prefix else _lane())
    body = {**REPORT["files"]["pkg/mod.py"], "missing_lines": [3],
            "contexts": {"1": ["tests/test_mod.py::test_f|run"]}}
    artifact = root / ".crapkit" / "cov.json"
    artifact.write_text(json.dumps({**REPORT, "files": {key: body}}), encoding="utf-8")
    return cfg.lanes[0], artifact


@pytest.mark.parametrize("prefix, key", CASED_KEYS)
def test_a_coveragepy_key_in_another_case_reads_as_git_spells_the_file(tmp_path, prefix, key):
    need_case_insensitive(tmp_path)
    root = _tree(tmp_path)
    lane, artifact = _cased_report(root, prefix, key)

    per_file, dead, _ = coverage_py.read(lane, root, artifact)

    assert list(per_file) == list(dead) == ["backend/pkg/mod.py"]
    assert list(coverage_py.missing(lane, root, artifact)) == ["backend/pkg/mod.py"]
    assert coverage_py.contexts(lane, root, artifact, "backend/pkg/mod.py") == {
        1: ["tests/test_mod.py::test_f"]}


@pytest.mark.parametrize("prefix, key", [("backend", "PKG/mod.py"), ("", "BACKEND/pkg/mod.py")])
def test_a_coveragepy_key_in_another_case_stays_as_written_on_a_case_sensitive_disk(
        tmp_path, prefix, key):
    """On ext4 `PKG` is another directory, one the runner never opened."""
    need_case_sensitive(tmp_path)
    root = _tree(tmp_path)
    lane, artifact = _cased_report(root, prefix, key)

    per_file, _, _ = coverage_py.read(lane, root, artifact)

    assert list(per_file) == [f"{prefix}/{key}".lstrip("/")]


def test_an_absolute_coveragepy_key_stays_absolute_for_the_wrong_tree_check(tmp_path):
    """coverage.py's own switch, relative_files, is the fix the lane names; the
    reader does not rebase an absolute key as istanbul's reader does."""
    root = _tree(tmp_path).resolve()
    key = str(root / "backend" / "pkg" / "mod.py")
    lane, artifact = _cased_report(root, "", key)

    per_file, _, _ = coverage_py.read(lane, root, artifact)

    assert list(per_file) == [key.replace("\\", "/")]


def _judged(capsys, root: Path, prefix: str) -> str:
    """What the lane says about an artifact keyed `pkg/mod.py`, read under
    `prefix`: nothing when the keys reach the scope, else the warning."""
    from crapkit.lanes import _judge_artifact_scope

    cfg = _load(root, _lane(path_prefix=prefix))
    artifact = root / ".crapkit" / "cov.json"
    artifact.write_text(json.dumps(REPORT), encoding="utf-8")
    per_file, _, _ = coverage_py.read(cfg.lanes[0], root, artifact)
    capsys.readouterr()
    _judge_artifact_scope(cfg.lanes[0], per_file, cfg.scope_paths, root)
    return capsys.readouterr().err


@pytest.mark.parametrize("prefix", ["backend", "backend\\", "./backend", ".\\backend", "/backend"])
def test_a_path_prefix_spelling_that_folds_earns_no_warning(tmp_path, capsys, prefix):
    assert _judged(capsys, _tree(tmp_path), prefix) == ""


@pytest.mark.parametrize("prefix", ["web", "web\\", "./web/", "/web"])
def test_the_unmeasured_warning_names_the_path_prefix_it_read(tmp_path, capsys, prefix):
    """It quoted `backend\\/pkg/mod.py` and told the reader to set path_prefix,
    which was already set: the value that broke the keys went unnamed. It now
    names the prefix as crapkit read it, and stops asking for one."""
    err = _judged(capsys, _tree(tmp_path), prefix)

    assert "it measured web/pkg/mod.py" in err, err
    assert err.rstrip().endswith("or path_prefix 'web', which crapkit.toml sets for this lane, "
                                 "does not rebase the runner's paths onto those scopes"), err
    assert "needs path_prefix" not in err


def test_a_lane_without_path_prefix_keeps_the_hint_to_set_one(tmp_path, capsys):
    err = _judged(capsys, _tree(tmp_path), "")

    assert err.rstrip().endswith("or the runner reports paths this lane needs path_prefix to "
                                 "rebase"), err


@pytest.mark.parametrize("prefix", ["Backend", "BACKEND/"])
def test_a_path_prefix_in_another_case_takes_the_listed_case(tmp_path, prefix):
    need_case_insensitive(tmp_path)
    cfg = _load(_tree(tmp_path), _lane(path_prefix=prefix))

    assert cfg.lanes[0].path_prefix == "backend"


# --- cwd, artifact, results_artifact -------------------------------------------

@pytest.mark.parametrize("cwd", ["backend", "backend/", "./backend", "backend\\", ".\\backend",
                                 "backend/../backend"])
def test_a_lane_cwd_in_any_spelling_starts_the_lane_in_that_directory(tmp_path, cwd):
    """On Linux `root / 'backend\\'` named no directory, and Popen died with a
    Python traceback and exit 1."""
    root = _tree(tmp_path)

    spec = launch_spec(root, _load(root, _lane(cwd=cwd)).lanes[0])

    assert spec.cwd.is_dir() and spec.cwd.resolve() == (root / "backend").resolve(), spec.cwd


@only_windows
def test_windows_starts_a_lane_cwd_in_another_case(tmp_path):
    root = _tree(tmp_path)

    spec = launch_spec(root, _load(root, _lane(cwd="Backend")).lanes[0])

    assert spec.cwd.is_dir()


ARTIFACTS = [".crapkit/cov.json", "./.crapkit/cov.json", ".crapkit\\cov.json",
             ".\\.crapkit\\cov.json"]


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_a_lane_artifact_in_any_spelling_opens_the_file_the_lane_wrote(tmp_path, artifact):
    """A lane that ran and passed was failed on Linux with `produced no artifact
    at .crapkit\\cov.json`: the file crapkit opened kept the backslash."""
    root = _tree(tmp_path)
    lane = _load(root, _lane(artifact=artifact, results_artifact=artifact.replace("cov.json",
                                                                                   "junit.xml")))
    lane = lane.lanes[0]

    assert (root / lane.artifact).is_file(), lane.artifact
    assert (root / lane.results_artifact).is_file(), lane.results_artifact


@only_posix
def test_a_lane_cwd_folds_its_backslash_even_where_the_tree_holds_that_name(tmp_path):
    r"""A path crapkit.toml carries separates on every OS (Q18). A POSIX tree
    that happens to hold a directory named `backend\` does not turn the
    committed `cwd = 'backend\'` back into that literal name: the lane starts in
    backend/, as it does on the Windows machine that wrote the file."""
    root = _tree(tmp_path)
    (root / "backend\\").mkdir()

    lane = _load(root, _lane(cwd="backend\\")).lanes[0]

    assert lane.cwd == "backend/", lane.cwd


# --- ratchet_file ---------------------------------------------------------------

RATCHET_FILES = ["gates/ratchet.tsv", "gates\\ratchet.tsv", "./gates/ratchet.tsv",
                 ".\\gates\\ratchet.tsv"]


def _ratchet_repo(root: Path) -> Path:
    """A repo whose marks file sits under gates/, committed once."""
    import subprocess

    (root / "gates").mkdir(parents=True)
    (root / "gates" / "ratchet.tsv").write_text("pkg/mod.py\tf( )\t7.0000\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "marks"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


@pytest.mark.parametrize("written", RATCHET_FILES)
def test_a_ratchet_file_in_any_spelling_is_the_path_git_spells(tmp_path, written):
    r"""`ratchet_file = 'gates\ratchet.tsv'`, committed from Windows, named a
    file literally called `gates\ratchet.tsv` on Linux: verify and seed read no
    marks, and `ratchet report` asked git for the history of a path git never
    tracked."""
    cfg = _load(tmp_path, f"[crapkit]\nratchet_file = '{written}'\n")

    assert cfg.ratchet_file == "gates/ratchet.tsv"


@pytest.mark.parametrize("written", RATCHET_FILES)
def test_a_ratchet_file_in_any_spelling_opens_the_marks_and_their_history(tmp_path, written):
    """The two readers: the file on disk, and git's log of it."""
    from crapkit.gitio import file_log_patches

    root = _ratchet_repo(tmp_path / "repo")
    cfg = _load(root, f"[crapkit]\nratchet_file = '{written}'\n")

    assert (root / cfg.ratchet_file).is_file()
    assert len(file_log_patches(root, cfg.ratchet_file)) == 1


@pytest.mark.parametrize("artifact", [*ARTIFACTS, "./backend/cov.json", ".\\backend\\cov.json"])
def test_doctor_reads_a_dot_slash_artifact_under_the_store_as_clean(tmp_path, artifact):
    """The litter check read the first part of `./.crapkit/cov.json` as `.` and
    told the user to move a file that already sat under .crapkit/."""
    cfg = _load(_tree(tmp_path), _lane(artifact=artifact))

    assert artifact_litter(cfg.lanes, scope_top_dirs(cfg.scopes)) == ()


# --- scope paths ----------------------------------------------------------------

def _scope(root: Path, path: str):
    return load_config_text(f"[[scope]]\nname = 's'\npaths = ['{path}']\n"
                            "languages = ['python']\n", root=root).scopes[0]


@pytest.mark.parametrize("path", ["web", ".\\web", "/web", "./web/", "web\\"])
def test_a_scope_path_in_any_relative_spelling_lands_on_gits(tmp_path, path):
    assert _scope(_tree(tmp_path), path).paths == ("web",)


@pytest.mark.parametrize("path", ["Web", "WEB/", ".\\Web"])
def test_a_scope_path_in_another_case_claims_the_directory_git_names(tmp_path, path):
    """git names the directory `web`, the scope prefix `Web/` matched none of
    its files, and inventory scored 0 functions in 0 files at exit 0."""
    need_case_insensitive(tmp_path)

    assert _scope(_tree(tmp_path), path).paths == ("web",)


def _absolute_spelling(root: Path, which: str) -> str:
    """The scope's directory, absolute, as each source writes it. On Windows the
    POSIX spelling drops the drive, as a path copied from Git Bash's `pwd -W`
    output with the drive cut off does."""
    web = (root / "web").resolve().as_posix()
    tail = web[2:] if web[1:2] == ":" else web
    return {"posix": tail, "msys": f"/c{tail}", "wsl": f"/mnt/c{tail}",
            "unc": r"\\server\share\web", "unc-forward": "//server/share/web"}[which]


@pytest.mark.parametrize("which", ["posix", "msys", "wsl", "unc", "unc-forward"])
def test_an_absolute_scope_path_is_refused_by_name(tmp_path, which):
    """Folded into a relative prefix, `/tmp/x/repo/web` became `tmp/x/repo/web`,
    which names nothing, and the scope scored zero files; the docstring had
    promised a refusal all along. The refusal names the spelling that works."""
    root = _tree(tmp_path)
    written = _absolute_spelling(root, which)

    with pytest.raises(ConfigError) as refusal:
        _scope(root, written)

    message = str(refusal.value)
    assert repr(written) in message and "repo-relative" in message, message


def test_an_absolute_scope_path_in_this_checkout_is_told_its_relative_spelling(tmp_path):
    root = _tree(tmp_path)
    web = (root / "web").resolve().as_posix()
    written = web if web.startswith("/") else "/" + web[0].lower() + web[2:]

    with pytest.raises(ConfigError) as refusal:
        _scope(root, written)

    assert "write 'web'" in str(refusal.value), refusal.value


def test_a_root_relative_scope_path_that_does_not_exist_yet_still_loads(tmp_path):
    """A relative path is a promise about a directory to come; only the leading
    `/` shape reads two ways, and that one has to name something."""
    assert _scope(_tree(tmp_path), "later/src").paths == ("later/src",)


# --- lane inputs ----------------------------------------------------------------

@pytest.mark.parametrize("entry", ["backend", "./backend", "backend\\", ".\\backend", "backend/"])
def test_a_lane_input_in_any_spelling_is_the_path_git_spells(tmp_path, entry):
    lane = _load(_tree(tmp_path), _lane() + f"inputs = ['{entry}']\n").lanes[0]

    assert lane.inputs == ("backend",)


@pytest.mark.parametrize("entry, listed", [("Backend", "backend"),
                                           ("BACKEND/PKG/MOD.PY", "backend/pkg/mod.py"),
                                           ("backend\\Pkg\\Mod.py", "backend/pkg/mod.py")])
def test_a_lane_input_in_another_case_is_the_path_git_spells(tmp_path, entry, listed):
    """git reads inputs as case-sensitive pathspecs even with core.ignorecase,
    so `Src` saw no change under src/ and the lane reused a stale artifact."""
    need_case_insensitive(tmp_path)
    lane = _load(_tree(tmp_path), _lane() + f"inputs = ['{entry}']\n").lanes[0]

    assert lane.inputs == (listed,)
