r"""A path crapkit writes for another reader comes back as the file it named.

Five places hand a repo path to something outside crapkit: a flake retest's
command line (a JUnit classname substituted for {files} and {tests}), the gate
command brief prints for a person or an agent to paste into cmd.exe, PowerShell
or sh, the watch loop's listing of tracked names, the committed marks file the
commit gate reads back, and the SARIF uri code scanning decodes. The boundary
hunt found each one sound for every name it tried; the tests that pin them
held some of those names. Each name here is a row, fed through the real code
path and read back the way the other reader reads it.

A name the OS cannot hold (`*`, `?`, `"` and tab on Windows) is a POSIX row.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import quoteattr

import pytest

from cli_inproc_repo import commit_all, git
from crapkit.cli import main
from crapkit.cli._shared import _load_repo_config
from crapkit.cli.admin import _watched_files
from crapkit.junitparse import failed_test_ids
from crapkit.lanes import _retest_template
from crapkit.ratchet import load_ratchet
from crapkit.watch import changed_paths, snapshot_mtimes

from path_spellings import WINDOWS, need_case_insensitive

RECORD = ("import json, sys\nfrom pathlib import Path\n"
          "Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n")


def tangled(name: str) -> str:
    """ccn 8, over the ceiling of 6."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def _repo(root: Path, sources: dict[str, str]) -> Path:
    """One cc-only python scope over src/, so no lane has to run."""
    root.mkdir(parents=True)
    (root / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
        'languages = ["python"]\ncoverage_optional = true\n', encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\nargv.json\nout.sarif\n", encoding="utf-8")
    for rel, text in sources.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    return root


# Names every OS holds, and the ones only POSIX does.
COMMON_NAMES = {
    "plain": "src/plain.py",
    "space": "src/sp ace/mod.py",
    "non-ascii": "src/\u00fcn\u00ef/mod.py",
    "glob-brackets": "src/[id]/mod.py",
    "apostrophe-ampersand": "src/o'k&b/mod.py",
    "percent-hash": "src/p%20#x/mod.py",
    "percent-variable": "src/%PATH%/mod.py",
    "caret-bang": "src/c^a!r/mod.py",
    "leading-dash": "src/-dash.py",
}
POSIX_NAMES = {
    "double-quote": 'src/q"uote/mod.py',
    "backslash": "src/we\\ird.py",
    "tab": "src/t\tab.py",
    "star-question": "src/*?/mod.py",
    "dollar-backtick": "src/$HOME`x`/mod.py",
}
NAMES = {**COMMON_NAMES, **({} if WINDOWS else POSIX_NAMES)}


# --- a flake retest's command line (boundary-10) -------------------------------

# id -> (JUnit classname, test name). A classname is a path for vitest and for
# jest-junit's classNameTemplate "{filepath}".
RETEST_IDS = {
    "relative": ("src/mod.test.ts", "adds"),
    "space-non-ascii": ("src/sub dir/\u00e9t\u00e9.test.ts", "adds one"),
    "dotted-pytest": ("tests.test_mod", "test_x[a b]"),
}
if WINDOWS:
    RETEST_IDS.update({
        "jest-junit-backslash": ("src\\mod.test.ts", "adds"),
        "backslash-quote-ending": ("src\\we\"ird\\", "adds"),
        "cmd-metacharacters": ("src\\a&b %PATH% ^x !y!.test.ts", "adds"),
        "absolute-windows": ("C:\\work\\repo\\src\\mod.test.ts", "adds"),
    })
else:
    RETEST_IDS.update({
        "backslash": ("src/we\\ird.test.ts", "adds"),
        "sh-metacharacters": ("src/a$HOME`x`'q\".test.ts", "adds"),
    })


@pytest.mark.parametrize("which", RETEST_IDS)
def test_a_retest_hands_the_runner_the_failed_id_as_written(tmp_path, monkeypatch, which):
    classname, name = RETEST_IDS[which]
    report = (f"<testsuite tests=\"1\"><testcase classname={quoteattr(classname)} "
              f"name={quoteattr(name)}><failure>boom</failure></testcase></testsuite>")
    (tmp_path / "record.py").write_text(RECORD, encoding="utf-8")
    monkeypatch.setenv("x", "EXPANDED")
    template = f'"{sys.executable}" record.py --files {{files}} --tests {{tests}}'

    command, env = _retest_template(template, failed_test_ids(report))
    subprocess.run(command, shell=True, cwd=tmp_path, env={**os.environ, **env}, check=True)

    argv = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert argv == ["--files", classname, "--tests", f"{classname}::{name}"]


# --- brief's printed gate command (boundary-12) ---------------------------------

SHELLS = ["cmd.exe", "powershell"] if WINDOWS else ["sh"]


def _console_script_on_path(monkeypatch) -> None:
    """The printed command starts `crapkit`, the console script pip installs
    for this interpreter: beside it in a venv, in its Scripts directory for a
    Windows install with no venv, as setup-python's is on a CI runner."""
    scripts = sysconfig.get_path("scripts")
    if shutil.which("crapkit", path=scripts) is None:
        pytest.skip("needs the crapkit console script of this python (pip install -e .)")
    monkeypatch.setenv("PATH", scripts + os.pathsep + os.environ["PATH"])


def _pasted(command: str, shell: str, root: Path) -> subprocess.CompletedProcess:
    if shell == "powershell":
        args = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                command + "; exit $LASTEXITCODE"]
        return subprocess.run(args, cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    return subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("which", NAMES)
def test_the_printed_gate_command_judges_the_file_brief_opened(tmp_path, monkeypatch, capsys,
                                                               which, shell):
    _console_script_on_path(monkeypatch)
    rel = NAMES[which]
    root = _repo(tmp_path / "repo", {rel: "def f(n):\n    return n\n"})
    assert main(["coverage", "--repo", str(root)]) == 0
    capsys.readouterr()
    assert main(["brief", "--json", "--repo", str(root), "--", rel, "f"]) == 0
    gate = json.loads(capsys.readouterr().out)["commands"]["gate"]
    (root / rel).write_text(tangled("f"), encoding="utf-8")

    pasted = _pasted(gate, shell, root)

    assert pasted.returncode == 6, (gate, pasted.stdout + pasted.stderr)


# --- watch's listing of tracked names (boundary-16) -----------------------------

@pytest.mark.parametrize("which", NAMES)
def test_watch_sees_a_tracked_file_change_whatever_its_name(tmp_path, which):
    rel = NAMES[which]
    root = _repo(tmp_path / "repo", {rel: "def f(n):\n    return n\n"})

    _assert_watch_names(root, rel, root / rel)


def test_watch_sees_a_file_renamed_on_disk_to_another_case(tmp_path):
    """The index holds src/Case.py and the disk src/case.py: the listing misses
    the name, and the stat that answers for it opens the file anyway."""
    root = _repo(tmp_path / "repo", {"src/Case.py": "def f(n):\n    return n\n"})
    need_case_insensitive(root)
    (root / "src" / "Case.py").rename(root / "src" / "case.py")

    _assert_watch_names(root, "src/Case.py", root / "src" / "case.py")


def _assert_watch_names(root: Path, rel: str, on_disk: Path) -> None:
    files = _watched_files(root, _load_repo_config(root))
    before = snapshot_mtimes(root, files)
    later = on_disk.stat().st_mtime + 60  # a new mtime, not a wait
    os.utime(on_disk, (later, later))

    after = snapshot_mtimes(root, files)

    assert rel in before, before
    assert changed_paths(before, after) == [rel]


# --- the committed marks and the SARIF uri (boundary-20, boundary-21) ----------

@pytest.fixture(scope="module")
def marked(tmp_path_factory) -> tuple[Path, dict]:
    """Every name holds one over-ceiling function, measured with --sarif,
    seeded into the marks file and committed; then each function grows one
    line at the same ccn and is staged."""
    functions = {rel: f"f{i}" for i, rel in enumerate(NAMES.values())}
    root = _repo(tmp_path_factory.mktemp("marked") / "repo",
                 {rel: tangled(fn) for rel, fn in functions.items()})
    assert main(["coverage", "--sarif", "out.sarif", "--repo", str(root)]) == 0
    assert main(["ratchet", "seed", "--repo", str(root)]) == 0
    commit_all(root, "seed")
    for rel, fn in functions.items():
        (root / rel).write_text(tangled(fn).replace("    return n\n", "    n = n\n    return n\n"),
                                encoding="utf-8")
    git(root, "add", "-A")
    sarif = json.loads((root / "out.sarif").read_text(encoding="utf-8"))
    uris = [unquote(result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"])
            for run in sarif["runs"] for result in run["results"]]
    return root, {"uris": uris}


@pytest.mark.parametrize("which", NAMES)
def test_the_marks_file_reads_each_path_back_as_written(marked, which):
    root, _ = marked
    marks = load_ratchet((root / "crapkit-ratchet.tsv").read_text(encoding="utf-8"))

    assert NAMES[which] in {entry.path for entry in marks}


def test_the_commit_gate_pardons_every_marked_function_it_reads_back(marked, capsys):
    """A mark whose path read back as another path pardons nothing, and the
    grown function would be refused as a new breach."""
    root, _ = marked

    code = main(["hook-precommit", "--repo", str(root)])

    assert code == 0, capsys.readouterr().out


@pytest.mark.parametrize("which", NAMES)
def test_the_sarif_uri_decodes_to_the_file_it_names(marked, which):
    _, found = marked

    assert NAMES[which] in found["uris"]
