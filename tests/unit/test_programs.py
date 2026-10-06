"""crapkit starts the programs it runs from PATH's absolute entries, never from
the working directory.

Handed a bare `git`, Windows' CreateProcess looks in the current directory
before PATH unless the caller's environment sets
NoDefaultCurrentDirectoryInExePath, and shutil.which does the same. On POSIX an
empty or relative PATH entry names that directory. So a repo holding a planted
`git.exe` at its root had crapkit run it. `programs.find` reads PATH's absolute
entries alone, and every place crapkit starts a program of its own goes
through it.

CreateProcess reads that variable from the caller's environment, not the
child's, and a Claude Code shell sets it: with it set, the planted cases on
Windows pass with the bug still in place. Each planted case deletes it from
this process, asserts it is gone, and first shows the planted copy running
when a bare name is started from the same place.
"""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePath
from types import SimpleNamespace

import pytest
from hang_guard import HANG_SECONDS

from crapkit import _process_owner, gitio, invocation, launchers, procs, programs
from crapkit.cli import admin, claude_hook
from crapkit.errors import GitError

WINDOWS = os.name == "nt"
NO_CWD_SEARCH = "NoDefaultCurrentDirectoryInExePath"
MARKER = "planted-ran.txt"
# A program that records each run in a file beside itself and fails.
_PLANTED_CS = (
    "class Planted { static int Main(string[] args) {"
    " string here = System.IO.Path.GetDirectoryName("
    "System.Reflection.Assembly.GetEntryAssembly().Location);"
    " System.IO.File.AppendAllText(System.IO.Path.Combine(here, \"" + MARKER + "\"),"
    " string.Join(\" \", args) + \"\\n\"); return 1; } }")


def _executable(path: Path, body: bytes = b"#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o755)
    return path


def _path(*entries) -> str:
    return os.pathsep.join(str(entry) for entry in entries)


# --- find, at its interface ----------------------------------------------------

@pytest.fixture(params=[True, False], ids=["windows-names", "posix-names"])
def names(request, monkeypatch):
    """Each OS's file-name rule, on every OS: `git.exe` and `git`."""
    monkeypatch.setattr(programs, "_WINDOWS", request.param)
    return (lambda name: name + ".exe") if request.param else (lambda name: name)


@pytest.fixture(params=[(True, ".exe"), (True, ".cmd"), (True, ".bat"), (False, "")],
                ids=["windows-exe", "windows-cmd", "windows-bat", "posix"])
def planted_names(request, monkeypatch):
    """A name rule, on every OS, and the file a planted copy takes under it:
    git.exe, git.cmd or git.bat on Windows' rule, git on POSIX's. Answers
    (the name an absolute entry holds, the planted name)."""
    windows, extension = request.param
    monkeypatch.setattr(programs, "_WINDOWS", windows)
    return ("git.exe" if windows else "git"), "git" + extension


def test_find_takes_the_first_absolute_entry_that_holds_the_program(tmp_path, monkeypatch,
                                                                     planted_names):
    held, planted_name = planted_names
    here = tmp_path / "repo"
    for planted in (here, here / "rel"):
        _executable(planted / planted_name)
    (tmp_path / "empty").mkdir()
    (tmp_path / "dir-named-git" / held).mkdir(parents=True)
    second = _executable(tmp_path / "second" / held)
    _executable(tmp_path / "third" / held)
    monkeypatch.chdir(here)
    env = {"PATH": _path(".", "", "rel", tmp_path / "empty", tmp_path / "dir-named-git",
                         tmp_path / "second", tmp_path / "third")}

    found = programs.find("git", env)

    assert found == str(second)
    assert PurePath(found).is_absolute()


def test_find_answers_none_when_only_a_planted_copy_is_reachable(tmp_path, monkeypatch, planted_names):
    _executable(tmp_path / planted_names[1])
    monkeypatch.chdir(tmp_path)

    assert programs.find("git", {"PATH": _path(".", "")}) is None


def test_find_reads_os_environ_when_no_env_is_given(tmp_path, monkeypatch, names):
    found = _executable(tmp_path / "bin" / names("git"))
    monkeypatch.setenv("PATH", str(found.parent))

    assert programs.find("git") == str(found)


@pytest.mark.parametrize("pathext,picked", [(".CMD;.EXE", "tool.cmd"), (".EXE;.CMD", "tool.exe"),
                                            (None, "tool.exe")])
def test_windows_pathext_order_decides_between_two_files_in_one_folder(tmp_path, monkeypatch,
                                                                         pathext, picked):
    monkeypatch.setattr(programs, "_WINDOWS", True)
    for name in ("tool.cmd", "tool.exe"):
        _executable(tmp_path / name)
    env = {"PATH": str(tmp_path)} if pathext is None else {"PATH": str(tmp_path), "PATHEXT": pathext}

    assert programs.find("tool", env) == str(tmp_path / picked)


def test_windows_a_name_ending_in_a_pathext_extension_is_looked_up_as_written(tmp_path, monkeypatch):
    monkeypatch.setattr(programs, "_WINDOWS", True)
    for name in ("tool.cmd.exe", "tool.cmd"):
        _executable(tmp_path / name)

    assert programs.find("tool.cmd", {"PATH": str(tmp_path), "PATHEXT": ".EXE;.CMD"}) \
        == str(tmp_path / "tool.cmd")
    assert programs.find("tool.cmd", {"PATH": str(tmp_path), "PATHEXT": ".exe;.cmd"}) \
        == str(tmp_path / "tool.cmd")


def test_a_file_this_user_may_not_execute_is_passed_over(tmp_path, monkeypatch, names):
    """As execvp passes it over. Windows has no execute bit to withhold, so the
    refusal is os.access's answer, on every OS."""
    refused = _executable(tmp_path / "first" / names("git"))
    second = _executable(tmp_path / "second" / names("git"))
    real = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: path != str(refused) and real(path, mode))

    assert programs.find("git", {"PATH": _path(refused.parent, second.parent)}) == str(second)


def test_a_quoted_entry_names_its_folder(tmp_path, names):
    found = _executable(tmp_path / "with space" / names("git"))

    assert programs.find("git", {"PATH": f'"{found.parent}"'}) == str(found)


def test_an_unset_path_reads_os_defpath_without_its_relative_entries():
    assert programs.search_path({}) == ([r"C:\bin"] if WINDOWS else ["/bin", "/usr/bin"])


def test_require_answers_the_file_a_bare_start_runs(tmp_path, monkeypatch, names):
    """On Windows that is `name.exe`, the one name CreateProcess tries, so a
    batch file earlier in PATHEXT's order is not the answer."""
    for name in ("tool.cmd", names("tool")):
        _executable(tmp_path / name)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".CMD;.EXE")

    assert programs.require("tool") == str(tmp_path / names("tool"))


def test_require_raises_what_a_bare_start_raised_when_no_absolute_entry_holds_it(tmp_path,
                                                                                  monkeypatch):
    _executable(tmp_path / ("git.exe" if WINDOWS else "git"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", _path(".", ""))

    with pytest.raises(FileNotFoundError) as missing:
        programs.require("git")

    assert (missing.value.errno, missing.value.filename) == (errno.ENOENT, "git")


# --- a planted program --------------------------------------------------------

def _csc() -> Path | None:
    framework = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET"
    found = (framework / bits / "v4.0.30319" / "csc.exe" for bits in ("Framework64", "Framework"))
    return next((path for path in found if path.is_file()), None)


@pytest.fixture(scope="session")
def planted_exe(tmp_path_factory) -> Path | None:
    """Windows: a compiled program that records its runs, made once a session.
    The C# compiler ships with .NET Framework 4, which every Windows 10 and 11
    carries."""
    if not WINDOWS:
        return None
    csc = _csc() or pytest.skip("no .NET Framework C# compiler to build a planted program")
    where = tmp_path_factory.mktemp("planted")
    (where / "planted.cs").write_text(_PLANTED_CS, encoding="utf-8")
    subprocess.run([str(csc), "/nologo", "/out:planted.exe", "planted.cs"], cwd=where,
                   check=True, capture_output=True)
    return where / "planted.exe"


@pytest.fixture()
def plant(planted_exe, monkeypatch):
    """Put a copy of a recording program in a folder under a program's name and
    answer the marker it writes when it runs. Windows starts a bare name as
    `name.exe`; POSIX runs a script. NoDefaultCurrentDirectoryInExePath leaves
    this process first."""
    monkeypatch.delenv(NO_CWD_SEARCH, raising=False)

    def planted(folder: Path, name: str) -> Path:
        marker = folder / MARKER
        if WINDOWS:
            shutil.copyfile(planted_exe, folder / f"{name}.exe")
        else:
            _executable(folder / name, f'#!/bin/sh\necho "$*" >> "{marker}"\nexit 1\n'.encode())
        return marker
    return planted


def _cwd_search_is_on() -> bool:
    """CreateProcess searches the current directory: the variable is unset here."""
    return NO_CWD_SEARCH.upper() not in {key.upper() for key in os.environ}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def planted_repo(tmp_path, plant, monkeypatch):
    """A committed repo whose root holds a planted git, entered, with PATH
    leading with an empty entry and `.` before the real git. A bare `git` started
    from here runs the planted copy; that is checked, then its marker cleared."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a")
    head = _git(root, "rev-parse", "HEAD")
    marker = plant(root, "git")
    monkeypatch.chdir(root)
    monkeypatch.setenv("PATH", _path("", ".", os.environ["PATH"]))
    subprocess.run(["git", "--version"], cwd=root, capture_output=True)
    assert marker.exists(), "a bare `git` started here runs the planted copy"
    marker.unlink()
    return SimpleNamespace(root=root, head=head, marker=marker,
                           planted="git.exe" if WINDOWS else "git")


def _owned_rev_parse(root: Path) -> str:
    with procs.own_processes(()) as owner:
        return gitio._worktree_git(root, "rev-parse", "HEAD", owner=owner).strip()


SITES = {
    "gitio._spawn": lambda r: gitio._spawn(r.root, ("rev-parse", "HEAD")).stdout.strip() == r.head,
    "gitio._git_lines": lambda r: "".join(gitio._git_lines(r.root, "rev-parse", "HEAD")).strip()
    == r.head,
    "gitio._batch_stream": lambda r: gitio._batch_stream(r.root, b"HEAD\n").split()[0].decode() == r.head,
    "gitio._Started": lambda r: gitio._Started(r.root, ("rev-parse", "HEAD"), stdin=False).result()
    .decode().strip() == r.head,
    "gitio._worktree_git": lambda r: _owned_rev_parse(r.root) == r.head,
    "claude_hook._repo_top": lambda r: os.path.samefile(claude_hook._repo_top(r.root), r.root),
    "claude_hook._porcelain": lambda r: f"?? {r.planted}" in claude_hook._porcelain(r.root).split("\0"),
    "claude_hook._head_resolves": lambda r: claude_hook._head_resolves(r.root),
    "claude_hook._listed": lambda r: claude_hook._listed(r.root, "a.py") == [],
}


@pytest.fixture()
def started(monkeypatch) -> list[str]:
    """The file each process started from here runs."""
    files = []

    class Recorded(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            files.append(str(kwargs.get("executable") or args[0]))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    return files


@pytest.mark.parametrize("site", sorted(SITES))
def test_each_git_spawn_site_runs_paths_git_and_never_the_planted_copy(site, planted_repo, started):
    assert _cwd_search_is_on()

    assert SITES[site](planted_repo)

    assert not planted_repo.marker.exists()
    assert started and all(PurePath(file).is_absolute() for file in started), started


def test_git_on_no_absolute_entry_is_git_missing_as_before(planted_repo, monkeypatch):
    monkeypatch.setenv("PATH", _path("", "."))
    assert _cwd_search_is_on()

    with pytest.raises(GitError, match="^git executable not found$"):
        gitio.ls_files(planted_repo.root)
    assert not planted_repo.marker.exists()


TASKKILL = "taskkill.exe" if WINDOWS else "taskkill"
KILL_4242 = ["taskkill", "/F", "/T", "/PID", "4242"]


@pytest.fixture()
def windows_branch(monkeypatch) -> list:
    """kill_process_tree's Windows branch, driven on every OS with the start
    faked. Answers each taskkill start as (argv, the file it runs)."""
    monkeypatch.setattr(_process_owner, "os", SimpleNamespace(name="nt", path=os.path))
    starts = []
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kwargs: starts.append((argv, kwargs["executable"])))
    return starts


def test_taskkill_starts_from_the_system_directory_first(tmp_path, monkeypatch, windows_branch):
    system = _executable(tmp_path / "system" / "taskkill.exe")
    _executable(tmp_path / "bin" / TASKKILL)
    monkeypatch.setattr(_process_owner, "_system_directory", lambda: str(system.parent))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    _process_owner.kill_process_tree(4242)

    assert windows_branch == [(KILL_4242, str(system))]


@pytest.mark.parametrize("system", [None, "system"], ids=["unreported", "holding-none"])
def test_taskkill_falls_back_to_its_absolute_path_entry(tmp_path, monkeypatch, windows_branch, system):
    """An unreported system directory joins to a bare `taskkill.exe`, which the
    copy planted in the working directory would answer; it is refused."""
    for name in {"taskkill.exe", TASKKILL}:
        _executable(tmp_path / name)
    on_path = _executable(tmp_path / "bin" / TASKKILL)
    if system:
        (tmp_path / system).mkdir()
    monkeypatch.setattr(_process_owner, "_system_directory",
                        lambda: str(tmp_path / system) if system else "")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", _path("", ".", on_path.parent))

    _process_owner.kill_process_tree(4242)

    assert windows_branch == [(KILL_4242, str(on_path))]


def test_taskkill_found_nowhere_raises_what_its_bare_start_raised(tmp_path, monkeypatch, windows_branch):
    for name in {"taskkill.exe", TASKKILL}:
        _executable(tmp_path / name)
    monkeypatch.setattr(_process_owner, "_system_directory", lambda: "")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", _path("", "."))

    with pytest.raises(FileNotFoundError):
        _process_owner.kill_process_tree(4242)
    assert windows_branch == []


def _sleeper() -> subprocess.Popen:
    """The base interpreter asleep in a group of its own: one process, so a
    failed kill still ends it with child.kill()."""
    return subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-c",
                             "import time; time.sleep(600)"],
                            stdin=subprocess.DEVNULL, **_process_owner._OWN_GROUP)


def test_a_planted_taskkill_never_runs(tmp_path, plant, monkeypatch):
    """Windows kills the tree with taskkill, POSIX with the group's signal; on
    either, a taskkill planted where crapkit stands stays unrun."""
    marker = plant(tmp_path, "taskkill")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", _path("", ".", os.environ["PATH"]))
    assert _cwd_search_is_on()
    subprocess.run(["taskkill", "/?"], capture_output=True)
    assert marker.exists(), "a bare `taskkill` started here runs the planted copy"
    marker.unlink()
    child = _sleeper()
    try:
        _process_owner.kill_process_tree(child.pid)

        assert child.wait(timeout=HANG_SECONDS) != 0
        assert not marker.exists()
    finally:
        child.kill()


def test_a_tree_dies_with_no_system_folder_on_path(tmp_path, monkeypatch):
    """CreateProcess found a bare `taskkill` in the Windows system directory
    whatever PATH held, and crapkit still does. POSIX signals the group."""
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert programs.find("taskkill") is None
    child = _sleeper()
    try:
        _process_owner.kill_process_tree(child.pid)

        assert child.wait(timeout=HANG_SECONDS) != 0
    finally:
        child.kill()


# --- lookups that report or run what they find --------------------------------

def _planted_script(folder: Path, name: str) -> tuple[Path, Path]:
    """A recording script a lookup that honours PATHEXT finds: `name.cmd` on
    Windows. Answers (script, marker)."""
    marker = folder / MARKER
    if WINDOWS:
        script = folder / f"{name}.cmd"
        script.write_text(f'@echo %* >> "{marker}"\r\n@exit /b 1\r\n', encoding="utf-8")
        return script, marker
    return _executable(folder / name, f'#!/bin/sh\necho "$*" >> "{marker}"\nexit 1\n'.encode()), marker


@pytest.fixture()
def planted_here(tmp_path, monkeypatch):
    """This process standing in a folder of planted programs, with PATH holding
    only `.`, an empty entry and an empty folder."""
    monkeypatch.delenv(NO_CWD_SEARCH, raising=False)
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", _path(".", "", tmp_path / "empty"))
    assert _cwd_search_is_on()
    return tmp_path


@pytest.fixture()
def fresh_probes():
    admin._claude_code_version.cache_clear()
    admin._spawned_cli.cache_clear()
    yield
    admin._claude_code_version.cache_clear()
    admin._spawned_cli.cache_clear()


def test_doctor_never_runs_or_names_a_planted_claude(planted_here, fresh_probes):
    _, marker = _planted_script(planted_here, "claude")
    assert shutil.which("claude"), "a which() lookup from here finds the planted copy"

    assert admin._claude_code_version() is None
    assert not marker.exists()


# A launcher that starts its interpreter by bare name: npm's shim for a JS bin
# runs `"%_prog%" "%dp0%\cli.js"`, with _prog `node` when no node.exe sits beside
# it, and on POSIX a `#!/usr/bin/env node` script has env search PATH for node.
PLANTED_NODES = ("node.cmd", "node.bat") if WINDOWS else ("node",)
PROBES = {
    "claude": lambda launcher: admin._claude_code_version(),
    "crapkit": lambda launcher: admin._probed_cli_version(str(launcher)),
}


def _bare_node_launcher(folder: Path, name: str) -> Path:
    if WINDOWS:
        launcher = folder / f"{name}.cmd"
        launcher.write_text('@node "%~dp0\\cli.js" %*\r\n', encoding="utf-8")
        return launcher
    return _executable(folder / name, b"#!/usr/bin/env node\nconsole.log('1.0.0')\n")


def _recorder(path: Path, marker: Path) -> None:
    """A program at `path` that records each run in `marker`."""
    if WINDOWS:
        path.write_text(f'@echo planted>> "{marker}"\r\n@echo 9.9.9\r\n', encoding="utf-8")
    else:
        _executable(path, f'#!/bin/sh\necho planted >> "{marker}"\necho 9.9.9\n'.encode())


@pytest.mark.parametrize("planted", PLANTED_NODES)
@pytest.mark.parametrize("probe", sorted(PROBES))
def test_a_probe_never_lets_its_launcher_start_a_planted_interpreter(tmp_path, monkeypatch,
                                                                     fresh_probes, probe, planted):
    """The launcher sits on an absolute PATH entry and the interpreter it names
    by bare name is planted where crapkit stands. cmd.exe searches its current
    directory first, and env reads an empty PATH entry as that directory, so a
    probe runs from the launcher's own folder with cmd.exe's search of it off."""
    folder = tmp_path / "bin"
    folder.mkdir()
    launcher = _bare_node_launcher(folder, probe)
    here = tmp_path / "repo"
    here.mkdir()
    marker = here / MARKER
    _recorder(here / planted, marker)
    monkeypatch.delenv(NO_CWD_SEARCH, raising=False)
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATH", _path("", folder))
    assert _cwd_search_is_on()
    subprocess.run([str(launcher), "--version"], capture_output=True)
    assert marker.exists(), "the launcher started from here runs the planted interpreter"
    marker.unlink()

    PROBES[probe](launcher)

    assert not marker.exists()


def test_doctor_never_runs_or_names_a_crapkit_on_a_relative_entry(planted_here, fresh_probes):
    _, marker = _planted_script(planted_here, "crapkit")
    assert launchers.path_launchers(os.environ["PATH"]), "a relative entry lists the planted copy"

    assert admin._path_launchers() == []
    assert admin._spawned_cli() is None
    assert not marker.exists()


def test_a_planted_python_is_not_the_interpreter_init_names(planted_here):
    for name in ("python", "python3", "py"):
        _executable(planted_here / (f"{name}.exe" if WINDOWS else name))
    assert shutil.which("python"), "a which() lookup from here finds the planted copy"

    assert admin._python_name() == "python3"
    assert admin._bare_python() == "python3"


def test_a_planted_console_script_is_not_the_crapkit_messages_name(planted_here, monkeypatch):
    _executable(planted_here / ("crapkit.exe" if WINDOWS else "crapkit"))
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(sys, "prefix", str(planted_here / "venv"))
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {planted_here.resolve()})
    assert invocation._runs_here(shutil.which("crapkit")), "which() names the planted copy"

    assert invocation._self().endswith(" -m crapkit")
