"""The setup and upkeep commands: `init` (sniff the repo, write a starter
crapkit.toml and extend .gitignore), `doctor` (does crapkit.toml still describe
this repo: keys, scopes, lanes, tools, unmeasured directories, plus the --json
report and the --tune knob advice) and `watch` (poll tracked files and rescore
what moved)."""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from .. import __version__, config
from .._package import upgraded_to
from ..config import load_config_text
from ..doctor import Finding
from ..errors import ConfigError, CrapkitError, GitError, ToolError
from ..gitio import _common_dir, _git, _git_dir, ls_files
from ..invocation import _self, quoted_path, shell_arg
from ..lane_command import (LaunchSpec, expand_launchers, first_word, install_python, launch_spec,
                            pytest_head, pytest_python, python_token, shell_segments)
from ..named import first_few
from ..programs import find, search_path
from ..repopath import typed_path
from ..rootfind import MAX_LEVELS, find_root
from ..store import SnapshotStore
from ..gitpaths import readable
from ..universe import overlapping_scope, path_matchers
from ..watch import Snapshot, poll, snapshot
from ._shared import (_command_root, _init_root, _load_repo_config, _print_json, _say_left_out,
                      _scan, repo_text)


def _present_lockfiles(root: Path) -> frozenset[str]:
    from ..scaffold import LOCKFILE_RUNNERS

    return frozenset(name for name, _ in LOCKFILE_RUNNERS if (root / name).is_file())


def _python_name() -> str:
    """The interpreter name a committed config can call. sys.executable is this
    machine's absolute path and would not survive the repo reaching anyone else.

    `py` comes last because it is the one name that does not travel: the Windows
    launcher exists nowhere else, so a committed `py -m pytest` fails every Unix
    collaborator's doctor. It is still better than the alternative it replaces.
    On Windows `python3` resolves only through the same WindowsApps alias that
    supplies `python`, so where the first name is missing the second is missing
    too, and writing it names an interpreter this very machine cannot run."""
    for name in ("python", "python3", "py"):
        if find(name):
            return name
    return "python3"


# Where a repo keeps the environment it means, and what a venv looks like from
# the outside: `pyvenv.cfg` is the file that makes a directory one, so a `venv/`
# package of somebody's sources is never mistaken for an interpreter.
_VENV_MARKER = "pyvenv.cfg"
_VENV_DIR_NAMES = (".venv", "venv")
_VENV_LAUNCHER = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")


def _venv_dirs(root: Path, scopes: tuple[str, ...]) -> list[Path]:
    """The directories that may hold this repo's own environment: beside
    crapkit.toml first, then inside a scope, for a repo whose python sits one
    level down and keeps its venv down there with it."""
    return [root / name for name in _VENV_DIR_NAMES] + [root / s / ".venv" for s in scopes]


def _venv_launcher(venv: Path) -> Path | None:
    """The interpreter inside this venv, or None when the directory is not a
    venv or does not carry one."""
    if not (venv / _VENV_MARKER).is_file():
        return None
    launcher = venv.joinpath(*_VENV_LAUNCHER)
    return launcher if launcher.is_file() else None


def _imports_pytest(launcher: Path) -> bool:
    """Does this environment carry the suite's own runner? An empty venv is not
    the environment the lane wants, and naming it would trade one broken lane
    for another. Asked through the same shell the lane runs under, with an
    absolute path, so the answer does not depend on where init was invoked.

    init already probes the interpreter it picks for pytest-cov, so asking the
    same interpreter one more question costs one more spawn and no new rule.
    """
    from ..procs import run_bounded

    probe = f'{_shell_quote(str(launcher))} -c "import pytest"'
    try:
        return run_bounded(probe, _PROBE_TIMEOUT_SECONDS) == 0
    except OSError:
        return False


def _committed_launcher(venv: Path, root: Path) -> str:
    r"""The launcher as a committed config spells it: the launcher token for
    this venv, repo-relative, so it travels the way an absolute path never
    could, to either OS.

    The OS's own spelling did not travel. Windows wrote `.venv\\Scripts\\python.exe`
    (cmd.exe reads an unquoted `/` as the end of the command name, and `\`
    opens an escape in a TOML basic string), which no Linux checkout has, and
    Linux wrote `.venv/bin/python`, which cmd.exe cannot start. The loader
    expands `{python:.venv}` into the launcher of the OS reading the file.
    """
    return python_token(venv.relative_to(root).as_posix())


def _repo_venv_python(root: Path, scopes: tuple[str, ...] = ()) -> str | None:
    """The repo's own virtualenv launcher, or None when it has none that can
    run the suite.

    A bare `python` is the shell's answer, not the repo's: a library whose
    `.venv` holds pytest, checked out on a machine whose PATH python holds
    none, got a lane naming that PATH python. init exited 0, doctor called the
    config clean, and the first `crapkit coverage` exited 5 on `No module named
    pytest` — with the right interpreter sitting in the tree the whole time.
    """
    for venv in _venv_dirs(root, scopes):
        launcher = _venv_launcher(venv)
        if launcher and _imports_pytest(launcher):
            return _committed_launcher(venv, root)
    return None


def _interpreter(root: Path, scopes: tuple[str, ...] = ()) -> str:
    """The python invocation a committed config can call.

    A lockfile at the root wins: `uv run python` and its siblings resolve to the
    environment the repo pins, while a bare `python` resolves to whichever venv
    the shell happens to have active — which in two worktrees of one branch is
    how a lane measures the OTHER checkout and scores this one untested.

    The manager only PREFIXES the name; which name it prefixes is still
    `_python_name`'s answer, so a Windows PATH carrying no `python` gets
    `uv run py` rather than a word the shell cannot start.

    With no lockfile, a virtualenv in the tree is the next best thing the repo
    says about its own environment, and the bare name is what is left when it
    says nothing at all.
    """
    from ..scaffold import lockfile_runner

    runner = lockfile_runner(_present_lockfiles(root))
    if runner:
        return f"{runner} {_python_name()}"
    return _repo_venv_python(root, scopes) or _bare_python()


def _bare_python() -> str:
    """The `{python}` token where the name it reads as on this OS resolves
    here, else `_python_name`'s answer.

    A bare name did not travel either: the `python` a Windows init wrote does
    not exist on an Ubuntu without python-is-python3, and the token reads as
    `python3` there. A machine where only another name resolves (`py` on
    Windows, `python` alone on POSIX) keeps that name, so the config still
    runs on the machine that wrote it."""
    token = python_token()
    return token if find(expand_launchers(token)) else _python_name()


def _present_markers(root: Path) -> frozenset[str]:
    from ..scaffold import PYTEST_MARKERS

    return frozenset(name for name in PYTEST_MARKERS if (root / name).is_file())


def _marker_texts(root: Path) -> dict[str, str]:
    """The pytest config files this repo carries, by name. Presence of one picks
    the lane; `testpaths` inside it says whether one lane can measure them all."""
    from ..repotext import plain_utf8
    from ..scaffold import PYTEST_MARKERS

    return {name: plain_utf8((root / name).read_bytes())
            for name in PYTEST_MARKERS if (root / name).is_file()}



class PackageMap(NamedTuple):
    """Every tracked package.json doctor or init read, by the directory holding it
    ("" for the root), and the path of each one it could not read with the reason."""
    packages: dict
    unreadable: dict[str, str]


NO_PACKAGES = PackageMap({}, {})


def _package_json(root: Path, caller=None) -> dict:
    """Every tracked package.json, parsed once here into the fields init reads
    (scaffold.NpmPackage), keyed by the directory holding it and "" for the
    root one. No other module parses the file.

    A monorepo names its test runner in the workspace that owns the tests. Read
    from the root alone, init bound the js lane to a root script that only
    chains the workspaces and produces no coverage of its own. A vendored
    node_modules is skipped: its packages describe somebody else's tests.

    `caller` is the reader that reads one file for its command, and says what a
    file it cannot read means there: init's by default, which stops on the root
    one and skips a nested one with a line; doctor's records it and goes on.
    """
    from ..scaffold import npm_package

    read = caller or _package_object
    found = {}
    for path in _package_files(root):
        data = read(root, path)
        if data is not None:
            found[path.rpartition("/")[0]] = npm_package(data)
    return found


def _package_files(root: Path) -> list[str]:
    return [path for path in ls_files(root) if _tracked_manifest(path) and (root / path).is_file()]


def _tracked_manifest(path: str) -> bool:
    """A tracked package.json outside node_modules. One under a directory whose
    name is not UTF-8 has no cwd a lane can spell; init named it left out."""
    return path.rpartition("/")[2] == "package.json" and "node_modules/" not in path and readable(path)


def _package_object(root: Path, rel: str) -> dict | None:
    """A package.json read by repotext's JSON kind: UTF-8, a byte-order mark read
    past as npm reads past it, one JSON object. A BOM used to cost the js lane
    in silence, one é ended init with a traceback after crapkit.toml was
    written, and a file that did not parse read as an empty one.

    A root package.json init cannot read stops init before it writes anything:
    the lane comes from that file, and a lane read off a guess is worse than
    none. A nested one, a test fixture say, is skipped with one line naming it."""
    from ..repotext import repo_json

    if "/" not in rel:
        try:
            return repo_json(root / rel, rel)
        except ConfigError as exc:
            raise ConfigError(f"init wrote no file: {exc}") from None
    try:
        return repo_json(root / rel, "it")
    except ConfigError as exc:
        print(f"crapkit: init skipped {rel}: {exc}", file=sys.stderr)
        return None

def _next_step(scopes: dict, lanes: tuple) -> str:
    """What to run next, which is not the same sentence in all three cases.

    A repo whose every language is cc-only was told to declare a lane per
    coverage command. There is no lane to declare: neither parser reads Go,
    Rust, shell or the six others, `init` just wrote `coverage_optional` for
    each scope, and `crapkit coverage` scores them from complexity alone.
    """
    from ..scaffold import cc_only_scope

    if lanes:
        return (f"detected {len(lanes)} lane(s) from this repo's own files: "
                f"{', '.join(lane.name for lane in lanes)} - next: run `{_self()} coverage`")
    if all(cc_only_scope(languages) for languages in scopes.values()):
        return ("no coverage parser reads this repo's languages, so every scope is "
                "cc-only (coverage_optional = true) and needs no lane - next: run "
                f"`{_self()} coverage`")
    return ("next: declare a [[lane]] per coverage command (see the commented template), "
            f"then run `{_self()} coverage`")


def _unrouted_workspaces_note(written: tuple, package_json) -> str | None:
    """Why no lane init wrote runs a JS runner when several workspaces could
    each have had one. File presence cannot pick among them, and saying nothing
    left a monorepo lead to learn it from doctor's next line."""
    from ..scaffold import runner_workspaces

    named = runner_workspaces(package_json)
    found = [(lane, _lane_toolchain(lane, PackageMap(package_json, {}))) for lane in written]
    if len(named) < 2 or any(_js_runner(runner) for _, runner in found):
        return None
    listed = ", ".join(f"{directory}: {runner}" for directory, runner in named)
    return (f"{len(named)} workspaces name a runner ({listed}) and the root names none, so "
            f"{_in_their_place(found)}, each with its own cwd and artifact")


def _js_runner(found) -> bool:
    """Does toolchain.infer name a runner a package.json can name (vitest,
    jest)? devDependencies count: the note's own claim, that the root names
    none, is a devDependencies fact, so a root lane whose runner only
    devDependencies name answers it."""
    from ..toolchain import TOOLCHAINS

    return found.name is not None and TOOLCHAINS[found.name].dev_dependency is not None


def _in_their_place(found: list) -> str:
    """What init wrote in place of the workspace lanes. A root test script
    that only fans out to the workspaces gets the root js lane, whose runner
    nothing names; saying no js lane was written then contradicted init's own
    `detected ... lane(s)` line one line up. The one shape this misreads: a
    root script that names a single non-JS runner, such as pytest, reads as
    no js lane."""
    unknown = [(lane, runner) for lane, runner in found if runner.name is None]
    if not unknown:
        return ("no js lane was written: declare one [[lane]] per workspace from the "
                "commented template")
    lane, runner = unknown[0]
    return (f"the runner of lane {lane.name!r} is unknown ({_unknown_runner(lane, runner)}): "
            "replace it with one [[lane]] per workspace")


def _print_init_summary(scopes: dict, lanes: tuple, package_json=None) -> None:
    from ..scaffold import live_lanes

    print(f"wrote crapkit.toml with {len(scopes)} scope(s): {', '.join(scopes)}")
    print(_next_step(scopes, lanes))
    note = _unrouted_workspaces_note(live_lanes(lanes, scopes), package_json)
    if note:
        print(note)


def _no_scopes_reason(root: Path) -> str:
    """Why init found nothing to scope.

    crapkit reads `git ls-files`, so source nobody added is source it cannot see
    — and that is the common case on the first command a new user runs. Blaming
    the directory sends them to look in the one place that is already right.
    """
    from ..gitio import untracked_files
    from ..scaffold import source_candidates

    untracked = sorted(source_candidates(untracked_files(root)))
    if not untracked:
        return "no source files found to scope - is this the repo root?"
    return ("no tracked source files to scope - crapkit scores git-tracked files only; "
            f"run `git add` first ({len(untracked)} untracked source file(s) found: "
            f"{first_few(untracked)})")


_PROBE_TIMEOUT_SECONDS = 15
_CMD_COULD_NOT_RUN_IT = 9009
_SH_COULD_NOT_RUN_IT = (126, 127)  # not found, and found but not executable


def _could_not_run_it(returncode: int | None) -> bool:
    """Did the shell refuse to start the command, or did the command run and
    fail? cmd.exe exits 9009 for a name it could not start, and so does the
    Windows Store python alias a stock PATH carries with no Store app behind
    it. sh has no 9009 — it truncates an exit status to a byte — and answers
    127 for a name it cannot find, 126 for one it cannot execute."""
    if returncode is None:
        return False  # the deadline, which says nothing either way
    if config.SHELL_IS_CMD:
        return returncode == _CMD_COULD_NOT_RUN_IT
    return returncode in _SH_COULD_NOT_RUN_IT


@lru_cache(maxsize=None)
def _start_probe(word: str, spec: LaunchSpec) -> int | None:
    """The shell's exit code for this one word, or None when the question could
    not be put at all. `--version` and not the bare word: the probe must not do
    the lane's work by accident, and a lane starting with `pytest` would run
    the suite. A runner that rejects the flag still started, which is all this
    asks; only the shell's own could-not-run code answers no.

    Asked of the child the lane starts: from the lane's directory, with the
    lane's environment. Memoized on the word and that launch spec, which
    together are the whole question, so two lanes starting with `pnpm` from one
    directory under one environment cannot get different answers. Probe each
    distinct pair once, including when it returns None after OSError or the
    15 s deadline. Repeated lanes then share the answer without repeating a
    hung runner's timeout."""
    from ..procs import run_bounded

    try:
        return run_bounded(f"{_shell_quote(word)} --version", _PROBE_TIMEOUT_SECONDS,
                           **spec.popen_kwargs())
    except OSError:
        return None


def _dead_first_word(spec: LaunchSpec, command: str) -> tuple[str, int] | None:
    """The command's first word and the shell's verdict, when the shell cannot
    start it. None when it starts, and None when the word does not resolve
    where the lane's shell looks for it: that one is already its own finding,
    and running nothing proves nothing."""
    word = first_word(command)
    if not word or spec.resolve(word) is None:
        return None
    code = _start_probe(word, spec)
    return (word, code) if _could_not_run_it(code) else None


def _probe_answered_no(returncode: int) -> bool:
    """Did an interpreter run and say no, or did nothing run at all? cmd.exe
    exits 9009 for a command it could not start, and so does the Windows Store
    python alias that a stock PATH carries when no Store app is installed:
    which() finds that stub, and it answers nothing about pytest_cov. sh has no
    such code — it truncates an exit status to a byte — so this reads 9009 as
    an ordinary failure anywhere the lane's shell is not cmd.exe."""
    if config.SHELL_IS_CMD and returncode == _CMD_COULD_NOT_RUN_IT:
        return False
    return returncode != 0


def _pytest_cov_probe(spec: LaunchSpec, command: str) -> bool:
    """Can the interpreter this lane names import pytest_cov? The probe runs
    through the same shell as the lane, from the lane's directory and with its
    environment, so a bare `python` resolves to the one the lane will get (a
    .bat shim or a `[lane.env] PATH` entry included), not the one CreateProcess
    finds.
    True too when the probe cannot run — only a clean "no" earns the warning,
    and a missing interpreter is doctor's finding, not this one's. True as well
    when no python runs the suite, `uv run python -m pytest` included: nothing
    here can be asked."""
    from ..procs import run_bounded

    word = pytest_python(command)
    if word is None or spec.resolve(word) is None:
        return True
    probe = f'{_shell_quote(word)} -c "import pytest_cov"'
    try:
        # run_bounded: the interpreter is the shell's child, and run()'s timeout
        # kills the shell alone. The 15 bounded nothing and left one interpreter
        # running per timeout. None here is that deadline, and it is not an
        # answer about pytest_cov.
        code = run_bounded(probe, _PROBE_TIMEOUT_SECONDS, **spec.popen_kwargs())
    except OSError:
        return True
    return code is None or not _probe_answered_no(code)


def _shell_quote(word: str) -> str:
    """One word, quoted for the shell the lane runs under."""
    import shlex

    if os.name != "nt":
        return shlex.quote(word)
    return f'"{word}"' if " " in word else word


def _shell_label() -> str:
    return "cmd.exe" if config.SHELL_IS_CMD else "the shell"


def _dead_interpreter_note(name: str, word: str, code: int) -> str:
    """Nothing ran, so nothing about pytest-cov is worth saying. Name the word
    the lane starts with: that is the one thing the reader has to change."""
    fix = ("install Python from python.org, or point the lane at `py`"
           if config.SHELL_IS_CMD else "install it, or point the lane at an "
           "interpreter this machine has")
    return (f"note: lane {name!r} names `{word}`, and {_shell_label()} cannot run it "
            f"(exit {code}) - {fix}, then `{_self()} coverage`")


def _missing_pytest_cov_note(name: str, word: str, spec: LaunchSpec) -> str:
    """Name the interpreter the probe asked and where that word landed here.

    "this python" named nothing, and a machine has more than one. A repo whose
    own `.venv` carries pytest-cov still gets this note when the lane names the
    `python` a stock PATH answers with, and then installing a package is the
    wrong move: the reader has to be able to tell which of the two was asked.
    The install command names the same interpreter, so it lands in that
    interpreter's environment rather than whichever one the reader's shell has
    active; in a venv uv made, which holds no pip, it is `uv pip install
    --python WORD`. Where the word lands is read the way the lane's shell reads
    it, so a relative launcher names the same file from any directory doctor
    runs in, and the install command names that file (`install_python`)."""
    from ..launchers import pip_install

    resolved = spec.resolve(word) or word
    return (f"note: lane {name!r} names `{word}`, which resolves here to {resolved} and "
            f"cannot import pytest_cov - run "
            f"`{pip_install(resolved, 'pytest-cov', install_python(word, spec))}` in the "
            "environment the suite runs in "
            # Double quotes, not single: cmd.exe passes ' through as an
            # ordinary character and pip rejects the requirement. Double
            # quotes are the one form cmd, PowerShell, bash and zsh share,
            # and the bare form still breaks zsh's globbing.
            '(pip install "crapkit[py]" when that is crapkit\'s own environment), '
            f"then `{_self()} coverage`")


def _absent_manager(spec: LaunchSpec, command: str) -> str | None:
    """The environment manager heading this lane, when the PATH the lane runs
    on carries no such word. None when it resolves, and None when nothing
    manages the lane at all.

    A lockfile is a property of the REPO, so `init` writes `uv run python` off
    its presence alone — right for the repo, and unrunnable on a checkout whose
    owner installed the dependencies with pip. Nothing else catches it: the
    start check skips a word that does not resolve, and the pytest-cov probe
    refuses to provision an environment to ask a question about it.
    """
    from ..scaffold import LOCKFILE_RUNNERS

    managers = {runner.split()[0] for _, runner in LOCKFILE_RUNNERS}
    head = first_word(command)
    return head if head in managers and spec.resolve(head) is None else None


def _missing_manager_note(name: str, manager: str) -> str:
    return (f"note: lane {name!r} runs through `{manager}`, which this machine's PATH does "
            f"not carry - install {manager}, or point the lane's command in crapkit.toml at "
            f"an interpreter that resolves here, then `{_self()} coverage`")


def _lane_first_run_note(spec: LaunchSpec, lane) -> str | None:
    """What init owes this lane before the first `crapkit coverage`, or None
    when the lane will run. Three different gaps, and they are not the same
    sentence: a manager that is not installed never gets as far as a python, and
    an interpreter that never started answered nothing about pytest_cov, so
    `pip install pytest-cov` fixes neither."""
    manager = _absent_manager(spec, lane.command)
    if manager:
        return _missing_manager_note(lane.name, manager)
    dead = _dead_first_word(spec, lane.command)
    if dead:
        return _dead_interpreter_note(lane.name, *dead)
    word = pytest_python(lane.command)
    if word and not _pytest_cov_probe(spec, lane.command):
        return _missing_pytest_cov_note(lane.name, word, spec)
    return None


def _probed_lanes(lanes: tuple, packages: PackageMap = NO_PACKAGES) -> list:
    """Only a lane that spells `pytest --cov` has a plugin to probe: pytest
    named in its command or in the package.json script it runs
    (toolchain.infer's `spelled`), with --cov written where pytest is."""
    return [lane for lane in lanes if _spells_pytest_cov(lane, packages)]


def _spells_pytest_cov(lane, packages: PackageMap) -> bool:
    found = _lane_toolchain(lane, packages)
    return found.name == "pytest" and found.spelled and "--cov" in _spelling_text(lane, found, packages)


def _spelling_text(lane, found, packages: PackageMap) -> str:
    """The text the runner word was read from: the lane's command, or the
    package.json script that names it, from the package infer read."""
    if found.script is None:
        return lane.command
    cwd_package, root_package = _lane_packages(packages, lane.cwd)
    return (cwd_package or root_package).scripts[found.script]


def _warn_missing_pytest_cov(root: Path, lanes: tuple, packages: PackageMap = NO_PACKAGES) -> None:
    """The first-run trap, caught where it starts. The py lane shells out to
    `pytest --cov`, and the --cov flags come from pytest-cov — a package of the
    REPO's interpreter, so a crapkit dependency could only ever cover installs
    sharing the suite's venv. Say the fix now, instead of `coverage` exiting 5
    with a lane log the first run has to decode.

    Only a lane whose pytest segment starts with a python is probed at all. A
    lane an environment manager heads is not: `uv run` and its siblings create
    or sync the project environment before running anything, so probing one
    would provision an environment to ask a question about it. Such a lane
    still earns the two notes ahead of the probe, a manager PATH does not carry
    and a first word the shell cannot start.
    """
    for lane in _probed_lanes(lanes, packages):
        note = _lane_first_run_note(launch_spec(root, lane), lane)
        if note:
            print(note, file=sys.stderr)


def _store_ignored_above(root: Path) -> bool:
    """Whether a .gitignore above the root already ignores the state directory.

    git applies an unanchored pattern at every depth, so a root `.crapkit/`
    line ignores web/.crapkit/ too and a nested init has nothing to add. git
    consults nothing above a repository's own top, so a root that is one (a
    nested repository, a linked worktree, a checkout under an ignoring
    directory) reads no ancestor at all, and a deeper root stops reading at
    the first top it finds."""
    if (root / ".git").exists():
        return False
    for directory in root.parents[:MAX_LEVELS]:
        if _ignores_store(directory / ".gitignore"):
            return True
        if (directory / ".git").exists():
            return False
    return False


def _ignores_store(gitignore: Path) -> bool:
    """Whether this .gitignore holds the store's line, read as git reads it:
    past a UTF-8 BOM, which git skips."""
    from ..repotext import lenient

    if not gitignore.is_file():
        return False
    lines = {line.strip() for line in lenient(gitignore.read_bytes()).splitlines()}
    return bool(lines & {".crapkit/", ".crapkit"})


class _GitignoreStep(NamedTuple):
    """What init's .gitignore step did: the entries it appended, or, for a
    UTF-16 file git cannot read, the sentence saying init left it as it was
    and which entries to add. Each caller decides what that sentence is: a
    first init prints it and still writes crapkit.toml, and a second init
    refuses with it, since the .gitignore is the one step left to finish."""
    added: list[str]
    unreadable: str | None = None


def _extend_gitignore(root: Path, lanes: tuple) -> _GitignoreStep:
    """Ignore what adopting crapkit will write: the store, and each lane's
    artifact. Without this the consumer's next `git status` is a wall of
    untracked coverage output nobody asked for. A nested configuration under a
    root whose .gitignore already ignores the store writes nothing (ADR 0002).
    What it did comes back as a _GitignoreStep, for the caller to print or to
    refuse with.

    git reads .gitignore as bytes, and so does this: every byte already there
    stays, a cp1252 comment included, and the entries take the file's own line
    ending. A UTF-16 file, which git cannot read, is left as it was and named."""
    from ..repotext import utf16_marked

    if _store_ignored_above(root):
        return _GitignoreStep([])
    path = root / ".gitignore"
    raw = path.read_bytes() if path.is_file() else b""
    if utf16_marked(raw):
        return _GitignoreStep([], _unreadable_gitignore(raw, lanes))
    extended, added = _extended_gitignore(raw, lanes)
    if added:
        path.write_bytes(extended)
    return _GitignoreStep(added)


def _print_gitignore_step(step: _GitignoreStep) -> None:
    if step.unreadable:
        print(f"crapkit: {step.unreadable}", file=sys.stderr)
    if step.added:
        print(f"added to .gitignore: {', '.join(step.added)}")


def _extended_gitignore(raw: bytes, lanes: tuple) -> tuple[bytes, list[str]]:
    """`raw` with crapkit's entries appended in the line ending it already uses.
    The lines are compared as git reads them, past a UTF-8 BOM; the file's own
    bytes are kept, and only the appended tail is new."""
    from ..repotext import lenient
    from ..scaffold import gitignore_update

    current = lenient(raw)
    text, added = gitignore_update(current, lanes)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw + text[len(current):].replace("\n", newline).encode("utf-8"), added


def _unreadable_gitignore(raw: bytes, lanes: tuple) -> str:
    from ..repotext import utf16_cause
    from ..scaffold import gitignore_entries

    return (f"left .gitignore as it was: it is UTF-16 (first bytes {raw[:2].hex(' ')}, "
            f"{utf16_cause(raw)}), which git cannot read; save it as UTF-8 "
            f"and add {', '.join(gitignore_entries(lanes))}")


def _refuse_claimed_by_ancestor(root: Path) -> None:
    """A second crapkit.toml under a directory an ancestor's scope path already
    claims would be two configurations selecting the same files, one store
    each, with the nested one shadowing the root's for everything below it
    (ADR 0002: nearest wins). The walk starts at the root itself, so a `.git`
    entry there, a nested repository or a linked worktree, stops it and init
    proceeds; a root that is the repository top never walks past its own `.git`."""
    above = find_root(root)
    if above is None:
        return
    directory = root.relative_to(above).as_posix()
    cfg = _load_repo_config(above)
    scope = overlapping_scope(directory, path_matchers({s.name: s.paths for s in cfg.scopes}))
    if scope is not None:
        raise ConfigError(f"crapkit.toml at {above} already claims {directory} "
                          f"(scope {scope!r}); edit that configuration instead")


def _finish_init(root: Path) -> int:
    """A second init over a crapkit.toml an earlier run wrote. Before 0.8.1 init
    wrote crapkit.toml first, so a run that died on the .gitignore step left a
    config the next init refused to touch, and .crapkit/ was never ignored.
    The missing .gitignore entries come from the lanes crapkit.toml declares
    now; crapkit.toml itself is left byte for byte. A UTF-16 .gitignore is the
    step it cannot finish, so the refusal names that file and nothing else.
    With nothing missing, this is the refusal it always was."""
    step = _extend_gitignore(root, _load_repo_config(root).lanes)
    if step.unreadable:
        raise ConfigError(step.unreadable)
    if not step.added:
        raise ConfigError(f"crapkit.toml already exists in {root} - edit it instead")
    print("crapkit.toml was already there and init left it as it was; it finished the step "
          "an earlier run left undone")
    _print_gitignore_step(step)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Every read comes first, so a file init cannot read stops it before it
    writes anything. Then .gitignore, then crapkit.toml: a run stopped between
    the two leaves no config, and the next init starts over."""
    from ..scaffold import (detect_lanes, live_lanes, pytest_testpaths, sniff_scopes,
                            starter_toml)

    root = _init_root(args.repo)  # init writes where the user stands; it adopts nothing
    toml_path = root / "crapkit.toml"
    if toml_path.is_file():
        return _finish_init(root)
    _refuse_claimed_by_ancestor(root)
    files = _init_files(root)
    scopes = sniff_scopes(files)
    if not scopes:
        raise ConfigError(_no_scopes_reason(root))
    # A config whose lanes are all commented out scores every function no-lane,
    # so a fresh repo cannot rank anything until somebody hand-writes a lane.
    # The interpreter goes to both: a repo with no pytest marker file gets no
    # lane to read it back off, and its commented template is what the reader
    # uncomments. The tracked files and the package.json map go to the starter
    # too: they decide which scoped-test form each scope gets.
    interpreter = _interpreter(root, tuple(scopes))
    packages = _package_json(root)
    lanes = detect_lanes(_present_markers(root), packages, interpreter=interpreter)
    text = starter_toml(scopes, lanes, interpreter=interpreter,
                        testpaths=pytest_testpaths(_marker_texts(root)),
                        tracked=files, package_json=packages)
    # Self-check: never write a config crapkit cannot read back. The probe asks
    # the lanes as they load, with the launcher token read for this OS.
    written = load_config_text(text)
    gitignore = _extend_gitignore(root, live_lanes(lanes, scopes))
    toml_path.write_text(text, encoding="utf-8", newline="\n")
    _print_init_summary(scopes, lanes, packages)
    _warn_missing_pytest_cov(root, written.lanes, PackageMap(packages, {}))
    _print_gitignore_step(gitignore)
    return 0


def _unknown_key_text(unknown) -> str:
    """Name the key AND the spellings its table accepts. A bare rejection makes
    the reader hunt for a key list the tool never printed anywhere."""
    from ..doctor import table_label, valid_keys

    noun = "keys" if unknown.table else "tables"
    return (f"unknown key {unknown.path} - crapkit ignores it (typo?); "
            f"{table_label(unknown.table)} accepts these {noun}: "
            f"{', '.join(valid_keys(unknown.table))}")


def _key_finding(unknown) -> Finding:
    """A key crapkit ignores: a WARN naming its replacement when crapkit once
    read it, else a FAIL listing the spellings its table accepts."""
    from ..config_contract import deprecation

    replacement = deprecation(unknown.path)
    if replacement is None:
        return Finding("FAIL", _unknown_key_text(unknown))
    return Finding("WARN", f"{unknown.path} is deprecated and ignored: {replacement}; delete the key")


def _doctor_keys(raw: dict) -> list[Finding]:
    from ..doctor import unknown_key_findings

    problems = [_key_finding(u) for u in unknown_key_findings(raw)]
    return problems or [Finding("ok", "config keys all recognized")]


def _listed_files(files: list[str], show: bool) -> list[Finding]:
    return [Finding("", f"       {f}") for f in files] if show else []


def _doctor_scope_files(files_by_scope: dict, cfg, show_files: bool) -> list[Finding]:
    out: list[Finding] = []
    for scope in cfg.scopes:
        files = files_by_scope.get(scope.name, [])
        # The quickstart publishes this line, so a one-file scope printing
        # "1 files" is the first crapkit output a new reader sees.
        noun = "file" if len(files) == 1 else "files"
        out.append(Finding("ok" if files else "FAIL",
                           f"scope {scope.name!r}: {len(files)} {noun}"))
        out += _listed_files(files, show_files)
    return out


def _doctor_unclaimed(unclaimed: tuple[str, ...]) -> list[Finding]:
    """Tracked source in a declared language that no scope path owns. It is
    analyzed by nothing and gated by nothing, and it says so nowhere else.

    The paths ride the finding itself rather than trailing it as loose lines, so
    the machine report names them too.
    """
    if not unclaimed:
        return [Finding("ok", "every tracked source file belongs to a scope")]
    return [Finding("FAIL", f"{len(unclaimed)} tracked file(s) match a scope language but "
                            f"no scope path: {', '.join(unclaimed)} - add a [[scope]] "
                            "claiming them, or an [exclude] glob (docs/configuration.md)")]


def _covered_scope_names(cfg) -> set[str]:
    return {s for lane in cfg.lanes for s in lane.scopes} | cfg.coverage_optional_scopes


def _uncovered_scopes(cfg) -> list[str]:
    # A repo with no lanes at all is the inventory-only case the lane summary
    # already notes; coverage_optional scopes never need one.
    if not cfg.lanes:
        return []
    covered = _covered_scope_names(cfg)
    return [s.name for s in cfg.scopes if s.name not in covered]


def _doctor_uncovered(cfg) -> list[Finding]:
    return [Finding("FAIL", f"scope {name!r} is in no lane's scopes list - its functions "
                            "can only score no-lane (declare a lane, or "
                            "coverage_optional = true)")
            for name in _uncovered_scopes(cfg)]


def _doctor_oversized(oversized: tuple[tuple[str, int], ...]) -> list[Finding]:
    """Reported, never a failure: skipping the blob is what max_file_bytes asked for."""
    return [Finding("note", f"{path} ({size} bytes) skipped: over max_file_bytes")
            for path, size in oversized]


def _utf16_mark(path: Path) -> bytes:
    """The UTF-16 byte-order mark `path` opens with, or b"" for none."""
    from ..repotext import utf16_marked

    try:
        with path.open("rb") as fh:
            head = fh.read(2)
    except OSError:
        return b""
    return head if utf16_marked(head) else b""


def _doctor_utf16_sources(root: Path, by_scope: dict) -> list[Finding]:
    """A note, never a failure: crapkit scores a source that opens with a
    UTF-16 byte-order mark, and git diffs it as binary (`Binary files ...
    differ`). One note per byte order, since only the little-endian one is
    PowerShell 5.1's default. One file read of two bytes per scoped source."""
    from ..repotext import utf16_cause

    marked: dict[bytes, list[str]] = {}
    for path in sorted(f for files in by_scope.values() for f in files):
        marked.setdefault(_utf16_mark(root / path), []).append(path)
    marked.pop(b"", None)
    return [Finding("note", f"{len(paths)} source file(s) open with a UTF-16 byte-order mark, "
                            f"{utf16_cause(mark)}: {', '.join(paths)}. crapkit "
                            "scores them, but git diffs them as binary; save them as UTF-8 "
                            "(PowerShell: Set-Content -Encoding utf8) to diff them as text")
            for mark, paths in sorted(marked.items(), reverse=True)]


def _init_files(root: Path) -> list[str]:
    """The tracked files init sniffs scopes from. No scope exists yet to take
    an unreadable name, so each one is named left out and the config is still
    written; the first command that loads it decides the claim."""
    files = ls_files(root)
    _say_left_out(tuple(sorted({path for path in files if not readable(path)})))
    return [path for path in files if readable(path)]


def _doctor_scopes(root: Path, cfg, files: list[str], show_files: bool) -> list[Finding]:
    universe = _scan(root, files, cfg)
    _say_left_out(universe.unreadable)
    return (_doctor_scope_files(universe.by_scope, cfg, show_files)
            + _doctor_unclaimed(universe.unclaimed)
            + _doctor_uncovered(cfg)
            + _doctor_oversized(universe.oversized)
            + _doctor_utf16_sources(root, universe.by_scope))


def _lane_problem(root: Path, lane) -> str | None:
    if not launch_spec(root, lane).cwd.is_dir():
        return f"lane {lane.name!r}: cwd {quoted_path(lane.cwd)} does not exist"
    return None


_SCRIPT_SUFFIXES = (".py", ".mjs", ".js", ".ts", ".ps1", ".sh")


def _missing_named_script(cwd: Path, tok: str) -> bool:
    if not tok.endswith(_SCRIPT_SUFFIXES) or tok.startswith("-") or "=" in tok:
        return False
    return not (cwd / tok).is_file()


def _segment_problems(name: str, spec: LaunchSpec, tokens: list[str]) -> list[str]:
    """One command's argv: its runner, looked up where the lane's shell looks
    for it, then the files it names. Nothing is executed.

    A runner the lane's own `[lane.env] PATH` supplies resolves, and a relative
    launcher resolves from the lane's cwd, whatever directory doctor started
    in: `mcp_server._run_cli` spawns `crapkit doctor --repo <repo>` with no cwd
    of its own, and asking from there failed every repo but the server's."""
    if not tokens:
        return []
    runner = ([f"lane {name!r}: executable {quoted_path(tokens[0])} does not resolve on PATH"]
              if spec.resolve(tokens[0]) is None else [])
    return runner + [f"lane {name!r}: command names {quoted_path(tok)}, which does not exist"
                     for tok in tokens[1:] if _missing_named_script(spec.cwd, tok)]


def _lane_command_problems(root: Path, lane) -> list[str]:
    """Config rot a lane would only reveal 40 minutes in: a runner that no
    longer resolves, a named script that left the repo.

    Read by the shell that will run the lane, one segment at a time. A
    whitespace split answered three questions wrong: it broke a quoted
    interpreter path at its space (`'"C:/Program'` resolves nowhere), it never
    looked past the first word, so a dead runner after `&&` passed doctor, and
    it read a quoted `-k "tests/gone.py or x"` as a test file the repo owes."""
    spec = launch_spec(root, lane)
    return [problem for segment in shell_segments(lane.command)
            for problem in _segment_problems(lane.name, spec, segment)]


def _lane_start_problem(root: Path, lane) -> str | None:
    """The rot which() cannot see: a first word that resolves and then will not
    run. %LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe is the case — a stock
    Windows PATH carries that stub with no Store app behind it, which() finds
    it, and doctor cleared a repo whose only lane exits 9009 while `coverage`
    exited 5 on the same command. This one does start the runner, with
    --version, which is why it is not part of _lane_command_problems."""
    dead = _dead_first_word(launch_spec(root, lane), lane.command)
    if not dead:
        return None
    word, code = dead
    return (f"lane {lane.name!r}: {_shell_label()} cannot run {quoted_path(word)} (exit {code}) - "
            "the lane cannot start, so its scopes can only ever score no-lane")


def _doctor_lane_summary(cfg) -> Finding:
    """No lanes is a gap in most repos and the finished state in a cc-only one,
    where every scope declares coverage_optional and `coverage` runs anyway."""
    if cfg.lanes:
        return Finding("ok", f"{len(cfg.lanes)} lane(s) declared")
    if cfg.lane_less_scopes:
        return Finding("note", "no [[lane]] declared - inventory works; coverage needs one")
    return Finding("ok", "no [[lane]] declared: every scope is cc-only, so none is needed")


def _lane_problems_of(root: Path, lane) -> list[str]:
    return [p for p in (_lane_problem(root, lane), *_output_directories(root, lane),
                        *_lane_command_problems(root, lane),
                        _lane_start_problem(root, lane)) if p]


def _output_directories(root: Path, lane) -> list[str]:
    """A declared output that names a directory. The runner clears each declared
    path before an attempt and reads the one file there, so the lane fails every
    run, and doctor passed it."""
    outputs = (("artifact", lane.artifact), ("results_artifact", lane.results_artifact))
    return [f"lane {lane.name!r}: {key} {quoted_path(path)} names a directory, and a lane "
            "output is one file; point it at the report file the command writes inside it"
            for key, path in outputs if path and (root / path).is_dir()]


def _lane_findings(cfg, by_lane: list[tuple]) -> list[Finding]:
    return ([Finding("FAIL", p) for _, problems in by_lane for p in problems]
            or [_doctor_lane_summary(cfg)])


# --- the runner each lane runs ---------------------------------------------------
#
# Read from the lane's command, then the package.json script it runs, then
# devDependencies (toolchain.infer). The package map is read once per doctor and
# handed to both the text lines and `doctor --json`.


def _doctor_packages(root: Path) -> PackageMap:
    """The package map, read through the one reader with doctor as its caller:
    a file doctor cannot read is recorded, never fatal."""
    unreadable: dict[str, str] = {}

    def read(root: Path, rel: str) -> dict | None:
        from ..repotext import repo_json

        try:
            return repo_json(root / rel, "it")
        except ConfigError as exc:
            unreadable[rel] = str(exc)
            return None

    return PackageMap(_package_json(root, read), unreadable)


def _upward(cwd: str) -> list[str]:
    """The lane's directory, then each directory above it, ending at the root ("")."""
    parts = [part for part in cwd.replace("\\", "/").split("/") if part not in ("", ".")]
    return ["/".join(parts[:end]) for end in range(len(parts), -1, -1)]


def _lane_packages(packages: PackageMap, cwd: str) -> tuple:
    """(the package at the lane's cwd or nearest above it, the root's). Both
    None when the nearest is one doctor could not read: the lane's runner is
    then read from its command alone."""
    unread = {rel.rpartition("/")[0] for rel in packages.unreadable}
    nearest = _nearest(cwd, {*packages.packages, *unread})
    if nearest is None or nearest in unread:
        return None, None
    return packages.packages[nearest], packages.packages.get("")


def _nearest(cwd: str, directories: set[str]) -> str | None:
    """The first of `directories` at or above the lane's cwd, or None."""
    return next((d for d in _upward(cwd) if d in directories), None)


def _lane_toolchain(lane, packages: PackageMap):
    from ..toolchain import infer

    cwd_package, root_package = _lane_packages(packages, lane.cwd)
    return infer(lane.command, cwd_package=cwd_package, root_package=root_package)


_READ_FROM = {"command": "named in its command",
              "script": 'named in package.json script "{script}"',
              "package.json": "package.json devDependencies; the command names no runner"}


def _runner_line(lane, found) -> Finding:
    """One line per lane: the runner and where crapkit read it, or why it
    cannot tell and what that turns off."""
    if found.name:
        where = _READ_FROM[found.source].format(script=found.script)
        return Finding("ok", f"lane {lane.name!r}: runs {found.name} ({where})")
    return Finding("note", f"lane {lane.name!r}: runner unknown ({_unknown_runner(lane, found)}); "
                           "runner-specific hints and refusals are off for it")


def _unknown_runner(lane, found) -> str:
    if found.words:
        return f"it runs more than one: {', '.join(found.words)}"
    return f"{lane.command} names none crapkit knows"


def _unreadable_packages(packages: PackageMap) -> list[Finding]:
    return [Finding("WARN", f"{rel}: {reason}; doctor read the runner of each lane under it "
                            "from the lane's command alone")
            for rel, reason in sorted(packages.unreadable.items())]


def _doctor_runners(cfg, packages: PackageMap) -> list[Finding]:
    """A WARN per package.json doctor cannot read, then the runner line of every lane."""
    return _unreadable_packages(packages) + [_runner_line(lane, _lane_toolchain(lane, packages))
                                             for lane in cfg.lanes]


def _doctor_lanes(root: Path, cfg, packages: PackageMap = NO_PACKAGES) -> list[Finding]:
    """The lane checks, then the probe of every lane that passed them. A lane
    with a problem of its own is not probed: the dead-interpreter FAIL already
    names the word, and init's note would say it again one line down."""
    from ..doctor import unreadable_payloads

    by_lane = [(lane, _lane_problems_of(root, lane)) for lane in cfg.lanes]
    healthy = [lane for lane, problems in by_lane if not problems]
    return (_lane_findings(cfg, by_lane) + _doctor_results_artifacts(cfg, packages)
            + list(unreadable_payloads(cfg.lanes)) + _doctor_lane_probes(root, healthy, packages))


# One probe answers three questions about the python a lane names: where the
# word lands, and which pytest and pytest-cov it carries. Printed on a clean
# doctor, because `no problems found` on a lane running the system python
# while the repo's own venv held the plugin is the report this came from.
_RUNNER_MARKER = "CRAPKIT_RUNNER_REPORT "
_VERSION_PROBE = ('-c "import sys, pytest, pytest_cov, coverage; '
                  f"print('{_RUNNER_MARKER}' + sys.executable, "
                  'pytest.__version__, pytest_cov.__version__, coverage.__version__)"')


def _runner_versions(report: str) -> tuple[str, str, str, str] | None:
    line = next((line[len(_RUNNER_MARKER):] for line in report.splitlines()
                 if line.startswith(_RUNNER_MARKER)), "")
    parts = line.rsplit(None, 3)
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 else None


@lru_cache(maxsize=None)
def _runner_report(word: str, spec: LaunchSpec) -> tuple[str, str, str, str] | None:
    """(executable, pytest, pytest-cov and coverage.py versions) the interpreter
    word answers through the lane's shell, from the lane's directory and with
    its environment, or None when it cannot say. Memoized on the word and the
    launch spec for the reason `_start_probe` is: one fact per child, however
    many lanes start it the same way. The path may hold spaces, so the three
    versions are split off the right. pytest-cov imports coverage, so asking
    for it costs no import the probe did not already pay."""
    from tempfile import TemporaryFile
    from ..procs import run_bounded
    from ..repotext import lenient

    try:
        with TemporaryFile() as output:
            code = run_bounded(f"{_shell_quote(word)} {_VERSION_PROBE}",
                               _PROBE_TIMEOUT_SECONDS, stream=output,
                               **spec.popen_kwargs({"PYTHONIOENCODING": "utf-8"}))
            output.seek(0)
            report = lenient(output.read())
    except OSError:
        return None
    return _runner_versions(report) if code == 0 else None


def _same_environment(a: str, b: str) -> bool:
    """Do two interpreter paths name one environment? Judged on the directory
    each executable sits in, never on the binary: on POSIX `python -m venv`
    symlinks bin/python to the base interpreter, so samefile on the two
    executables read a venv and its base as one python while a package
    installed in one stayed invisible to the other. `python` and `python3.12`
    beside each other in one bin are one install, symlink or not."""
    dir_a, dir_b = os.path.dirname(os.path.abspath(a)), os.path.dirname(os.path.abspath(b))
    try:
        return os.path.samefile(dir_a, dir_b)
    except OSError:
        return os.path.normcase(dir_a) == os.path.normcase(dir_b)


def _foreign_interpreter(name: str, executable: str) -> list[Finding]:
    """WARN when the lane's python is not the one running this doctor. A
    package installed in one is invisible to the other, which is how a lane
    ran the system python while the repo's venv held pytest-cov."""
    if _same_environment(executable, sys.executable):
        return []
    return [Finding("WARN", f"lane {name!r} runs {executable}, not the python running this "
                            f"doctor ({sys.executable}); a package installed in one is not "
                            "seen by the other")]


def _unprobed_lane_note(lane) -> Finding:
    """A lane no python heads names nothing doctor can ask: `uv run` and its
    siblings provision the environment they run in, and asking one would
    provision it to answer. Said out loud, because a lane that printed nothing
    read the same as one probed and found healthy."""
    return Finding("note", f"lane {lane.name!r} runs pytest through `{pytest_head(lane.command)}`, "
                           "which is not a python doctor can ask: interpreter and pytest-cov not "
                           f"probed, the first `{_self()} coverage` will say whether the plugin imports")


def _first_run_failure(spec: LaunchSpec, lane) -> list[Finding]:
    """init's first-run note as a FAIL, or nothing when a stub interpreter
    answered neither the version probe nor the import probe."""
    note = _lane_first_run_note(spec, lane)
    return [Finding("FAIL", note.removeprefix("note: "))] if note else []


def _lane_probe_findings(root: Path, lane) -> list[Finding]:
    """The interpreter and plugin versions a healthy lane resolves to, plus a
    FAIL when its coverage.py predates function regions and a WARN when that
    interpreter is foreign; init's first-run note as a FAIL when
    the interpreter cannot say; a note when no python heads the lane. The
    version report goes first: it imports pytest_cov on its way, so it answers
    the first-run question too, and a healthy lane costs one interpreter start
    instead of two."""
    word = pytest_python(lane.command)
    if word is None:
        return [_unprobed_lane_note(lane)]
    spec = launch_spec(root, lane)
    report = _runner_report(word, spec)
    if report is None:
        return _first_run_failure(spec, lane)
    executable, pytest_version, plugin_version, coverage_version = report
    resolved = Finding("ok", f"lane {lane.name!r}: {word} -> {executable} (pytest {pytest_version}, "
                             f"pytest-cov {plugin_version}, coverage {coverage_version})")
    return [resolved, *_coverage_floor(lane.name, executable, coverage_version),
            *_foreign_interpreter(lane.name, executable)]


def _coverage_floor(name: str, executable: str, version: str) -> tuple[Finding, ...]:
    """The FAIL for a lane whose coverage.py writes no function start lines, with
    the install line spelled for the interpreter that lane runs."""
    from ..coverage_py import REGIONS_FLOOR
    from ..doctor import coverage_floor_gap
    from ..launchers import pip_install

    upgrade = pip_install(executable, f'"coverage>={REGIONS_FLOOR}"', _shell_quote(executable))
    return coverage_floor_gap(name, executable, version, upgrade)


def _doctor_lane_probes(root: Path, lanes, packages: PackageMap = NO_PACKAGES) -> list[Finding]:
    """init's first-run lane note, asked again of every lane that spells
    `pytest --cov` (_probed_lanes), so a lane whose python cannot import
    pytest-cov fails doctor instead of the first `crapkit coverage`. Only those
    lanes: another runner has no plugin to import. A lane no python heads
    names no python to ask and gets a note saying so."""
    return [finding for lane in _probed_lanes(lanes, packages)
            for finding in _lane_probe_findings(root, lane)]


# The hint for a lane whose runner is not spelled, or whose runner's row carries
# none: which flags write a junit file is the runner's to say, so only the key
# is named.
_JUNIT_HINT = ("add the runner's junit reporter to the command and a results_artifact "
               "naming the file it writes")


def _doctor_results_artifacts(cfg, packages: PackageMap = NO_PACKAGES) -> list[Finding]:
    """WARN, never FAIL: the lane measures coverage exactly as it did. What it
    cannot do without a results file is feed the two checks that read one, the
    crashed-worker trust check and no-new-failures, and until now nothing said
    they were off (#26)."""
    return [Finding("WARN", f"lane {lane.name!r} declares no results_artifact: the "
                            "crashed-worker check and the no-new-failures check (exit 8) "
                            f"cannot run for it; {_junit_hint(lane, packages)}")
            for lane in cfg.lanes if not lane.results_artifact]


def _junit_hint(lane, packages: PackageMap) -> str:
    """The junit_hint of the runner the lane spells, in its command or the
    package.json script it runs; the generic hint when it spells none, or one
    whose row carries no hint. devDependencies alone name no flags."""
    from ..toolchain import TOOLCHAINS

    found = _lane_toolchain(lane, packages)
    hint = TOOLCHAINS[found.name].junit_hint if found.spelled else None
    return (hint or _JUNIT_HINT).format(name=lane.name)


def _doctor_artifact_litter(cfg) -> list[Finding]:
    """WARN, never FAIL: a lane writing at the repo root still measures what it
    always did. Failing here would break every consumer that adopted crapkit
    before its lanes wrote under .crapkit/, over tree hygiene."""
    from ..doctor import artifact_litter, scope_top_dirs

    return [Finding("WARN", f"lane {item.lane!r} writes {item.path} at the repo root - "
                            f"point it under .crapkit/ (for example .crapkit/cov/{item.lane}/) "
                            "to keep the tree clean")
            for item in artifact_litter(cfg.lanes, scope_top_dirs(cfg.scopes))]


def _doctor_shared_coverage_data(cfg) -> list[Finding]:
    """WARN when lanes whose coverage.py data files one of them deletes and
    combines may run at once. A serial lane deletes the others' files before it
    starts and they are done with them, so max_parallel_lanes = 1 says nothing."""
    from ..doctor import shared_coverage_data, shared_data_words

    if cfg.max_parallel_lanes < 2:
        return []
    return [Finding("WARN", f"{what}, and max_parallel_lanes = {cfg.max_parallel_lanes} can "
                            "start them together, which can fail one lane and leave the run "
                            f"partial; {fix}")
            for what, fix in map(shared_data_words, shared_coverage_data(cfg.lanes))]


def _lizard_version() -> str | None:
    try:
        import lizard
    except ImportError:
        return None
    return getattr(lizard, "version", "?")


def _doctor_tools() -> list[Finding]:
    """lizard's version, or a FAIL naming the install for the python running
    crapkit. `pip install lizard` landed in whatever environment the shell's pip
    belongs to, and a `uv tool install` of crapkit runs in a venv uv made, which
    holds no pip of its own."""
    from ..launchers import pip_install

    version = _lizard_version()
    if version is None:
        install = pip_install(sys.executable, "lizard", _shell_quote(sys.executable))
        return [Finding("FAIL", f"lizard is not importable by the python running crapkit "
                                f"({sys.executable}) - run `{install}`, or reinstall crapkit")]
    return [Finding("ok", f"lizard {version}")]


def _store_path(root: Path) -> Path:
    return root / ".crapkit" / "crap.sqlite"


def _store_if_any(root: Path) -> SnapshotStore | None:
    """The store, or None when this repo has never run inventory. Doctor is the
    one command that must describe a repo with nothing recorded yet."""
    path = _store_path(root)
    return SnapshotStore(path) if path.is_file() else None


def _newest_coverage_run(store: SnapshotStore) -> dict | None:
    runs = [r for r in store.list_runs() if r["kind"] == "coverage"]
    return runs[-1] if runs else None


def _doctor_unmeasured(root: Path, cfg, files: list[str]) -> list[Finding]:
    """WARN, never FAIL: a directory whose functions are all untested while its
    tests exist is a lane that runs without measuring the code it covers.

    The store does the grouping. This used to build a hundred thousand
    sixteen-field rows to read three fields off each of them, then filter the
    coverage_optional scopes back out after reading them.
    """
    from ..doctor import unmeasured_directories

    store = _store_if_any(root)
    run = _newest_coverage_run(store) if store else None
    if run is None:
        return []
    counts = store.count_by_path(run["id"], flag="untested",
                                 skip_scopes=cfg.coverage_optional_scopes)
    return [Finding("WARN", f"{g.directory}: {g.functions} function(s) all flagged untested "
                            f"while {g.example_test} exists - tests exist but no lane "
                            "measures them")
            for g in unmeasured_directories(counts, files)]


def _doctor_unread(root: Path, cfg, files: list[str]) -> list[Finding]:
    """WARN, never FAIL: each file the newest coverage run could not read and no
    reader can read now. The commit gate refuses it once staged, so the user
    meets it here and not at a refused commit. A file the run scored a
    function in was read, so only the files it scored nothing in are read
    again, and a file fixed since then is no longer named."""
    from ..merge import UNREAD_ADVICE

    scored = _newest_scored_paths(root)
    if scored is None:
        return []
    unread = _unread_now(root, sorted(_scoped_files(root, cfg, files) - scored))
    return [Finding("WARN", f"{path} could not be read, so the commit gate refuses it when "
                            f"staged: {why}; {UNREAD_ADVICE}")
            for path, why in sorted(unread.items())]


def _newest_scored_paths(root: Path) -> set[str] | None:
    """The paths the newest coverage run scored a function in; None with no such run."""
    store = _store_if_any(root)
    run = _newest_coverage_run(store) if store else None
    if run is None:
        return None
    return {path for path, *_ in store.count_by_path(run["id"], flag="untested")}


def _scoped_files(root: Path, cfg, files: list[str]) -> set[str]:
    return {f for scoped in _scan(root, files, cfg).by_scope.values() for f in scoped}


def _unread_now(root: Path, paths: list[str]) -> dict[str, str]:
    """{path: the reader's reason} for each of `paths` no reader can read."""
    from ..analyze import analyze_source, read_source, unread_reasons

    present = [p for p in paths if (root / p).is_file()]
    return unread_reasons({p: analyze_source(p, read_source(str(root / p)), note=False)
                           for p in present})


def _hook_modes(root: Path) -> dict[str, str]:
    """Index modes of the files the repo's `core.hooksPath` points at.

    Empty when no hooks path is configured, when it points outside the worktree
    (an absolute path is a legitimate setup, and `git ls-files` refuses it with
    exit 128), and when nothing under it is tracked — the local `.git/hooks`
    route commits no files, so there is no bit to be wrong.
    """
    from ..errors import GitError
    from ..gitio import config_value, index_modes

    hooks_path = config_value(root, "core.hooksPath")
    if not hooks_path:
        return {}
    try:
        return index_modes(root, hooks_path)
    except GitError:
        return {}


def _doctor_hook_modes(root: Path) -> list[Finding]:
    """WARN, never FAIL: on Windows the bit is unreadable from the filesystem and
    the hook still runs, so a Windows author must not be blocked by it. On Linux
    and macOS git skips a 100644 hook without a word, which is how crapkit's own
    contributor gate armed nothing. The fix command quotes the path the way the
    reader's shell needs it, so a hooks directory with a space stays one path."""
    from ..doctor import non_executable_hooks

    return [Finding("WARN", f"{path} is not executable in the index - core.hooksPath "
                            "is set, so Unix clones silently skip it; fix with "
                            f"`git update-index --chmod=+x {shell_arg(path)}` and commit")
            for path in non_executable_hooks(_hook_modes(root))]


# The byte-order marks a Windows editor or shell puts in front of a script, and
# how the warning names each. git execs the file as-is, and no kernel or sh
# reads `\xef\xbb\xbf#!/bin/sh` as a shebang.
_LEADING_MARKS = (
    (b"\xef\xbb\xbf", "a UTF-8 byte-order mark (ef bb bf)"),
    (b"\xff\xfe", "a UTF-16 byte-order mark (ff fe, the PowerShell 5.1 Out-File default)"),
    (b"\xfe\xff", "a UTF-16 byte-order mark (fe ff)"),
)


def _hook_file(root: Path) -> str | None:
    """The pre-commit hook git would spawn for this checkout, spelled the way git
    spells it: under `core.hooksPath` when that is set, else the admin
    directory's `hooks/`, so a linked worktree lands on the right one. None
    outside a repository."""
    try:
        return _git(root, "rev-parse", "--git-path", "hooks/pre-commit").strip() or None
    except GitError:
        return None


def _leading_mark(path: Path) -> str | None:
    """What the file opens with when that is a byte-order mark, else None; a
    hook that is not there is not a finding."""
    try:
        head = path.read_bytes()[:3]
    except OSError:
        return None
    return next((what for sign, what in _LEADING_MARKS if head.startswith(sign)), None)


def _doctor_hook_encoding(root: Path) -> list[Finding]:
    """WARN on a pre-commit hook whose first bytes git cannot spawn.

    A Windows author who wrote the hook with `Out-File` got a BOM (or UTF-16)
    in front of the shebang, git said `cannot spawn .git/hooks/pre-commit` at
    the first commit and refused it without running the gate (git 2.43 for
    Windows), and doctor had passed the file. WARN, not FAIL: the config is
    fine, the file beside it is not.
    """
    named = _hook_file(root)
    mark = _leading_mark(root / named) if named else None
    if mark is None:
        return []
    return [Finding("WARN", f"{named} starts with {mark}, which git cannot spawn; rewrite "
                            "it as ASCII (PowerShell: Set-Content -Encoding ascii)")]


_CG_SIGNATURE = b"CGPH"
_BLOOM_CHUNK = b"BIDX"  # the changed-path Bloom filter index


def _object_info_dir(root: Path) -> Path | None:
    """Where the commit-graph lives, or None when there is no repository here.

    gitio finds the git directory the way git does — walking up to the first
    ancestor holding a .git, following a `gitdir:` pointer relative to its
    holder, and reading `commondir` so a linked worktree lands on the object
    store it shares. A private copy here looked at `root/.git` alone, so under a
    crapkit root one directory below the repo top it named a path that does not
    exist and the check went silent.
    """
    gitdir = _git_dir(root)
    return _common_dir(gitdir) / "objects" / "info" if gitdir else None


def _graph_files(info: Path | None) -> list[Path]:
    """Every commit-graph layer this repo has: the single file, or the layers a
    chain file names. `git maintenance` writes the chain, `gc` writes the file.
    No object store (no repository) means no layers."""
    if info is None:
        return []
    chain = info / "commit-graphs" / "commit-graph-chain"
    if not chain.is_file():
        single = info / "commit-graph"
        return [single] if single.is_file() else []
    return [info / "commit-graphs" / f"graph-{line}.graph"
            for line in chain.read_text(encoding="utf-8").split()]


def _graph_chunks(path: Path) -> frozenset | None:
    """The chunk ids one commit-graph declares, or None when it declares none we
    can trust. The header is signature, version, hash version, chunk count, base
    count; then a 12-byte table entry per chunk plus a terminator. The chunk
    bodies are never read, so this is one short read per layer.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
            if len(head) < 8 or head[:4] != _CG_SIGNATURE:
                return None
            toc = fh.read((head[6] + 1) * 12)
    except OSError:
        return None
    return frozenset(toc[i:i + 4] for i in range(0, len(toc), 12))


def _bloomless_graphs(root: Path) -> list[Path]:
    """Commit-graph layers written without changed-path Bloom filters."""
    layers = [(path, _graph_chunks(path)) for path in _graph_files(_object_info_dir(root))]
    return [path for path, chunks in layers if chunks and _BLOOM_CHUNK not in chunks]


def _doctor_commit_graph(root: Path) -> list[Finding]:
    """WARN, never FAIL: a commit-graph carrying no changed-path Bloom filters.

    Every per-file history walk crapkit makes — churn, `brief`,
    `explain --history` — asks git which commits touched one path, and without
    the filters git opens every tree along the way: 1,147 ms against 194 ms on
    the flagship consumer's 72,470 commits. A repo with NO commit-graph is left
    alone; there is no shape to fix, and git decides when a history wants one.
    """
    if not _bloomless_graphs(root):
        return []
    return [Finding("WARN", "the commit-graph carries no changed-path Bloom filters, so every "
                            "per-file history walk (churn, brief, explain --history) opens "
                            "every tree it passes - fix with `git commit-graph write "
                            "--reachable --changed-paths`")]


_UTF8_NAMES = frozenset({"utf-8", "utf8"})


def _doctor_commit_encoding(root: Path) -> list[Finding]:
    """A note, never a failure: i18n.commitEncoding only labels a commit, it
    does not convert what the client typed. Git for Windows passes a message
    and a name as UTF-8, so under ISO-8859-1 git stores UTF-8 bytes labelled
    Latin-1, and every reader that asks for UTF-8, crapkit included, gets
    `José` back as `JosÃ©`: one author counted twice in churn."""
    from ..gitio import config_value

    value = config_value(root, "i18n.commitEncoding")
    if not value or value.lower() in _UTF8_NAMES:
        return []
    return [Finding("note", f"i18n.commitEncoding is {value}: git labels each new commit "
                            f"{value} and crapkit reads commits back as UTF-8, so a name or "
                            "subject a client wrote in UTF-8 (Git for Windows does) comes out "
                            f"garbled, its accented letters read as {value} characters, in "
                            "churn and history; unset it (git config --unset "
                            f"i18n.commitEncoding) unless this repo's clients write {value}")]


def _doctor_container(cfg) -> list[Finding]:
    """A pytest lane `crapkit coverage` refuses in this container (WARN)."""
    from ..doctor import container_lane_findings, container_marker

    marker = container_marker(os.environ, Path("/.dockerenv").exists())
    return list(container_lane_findings(cfg.lanes, marker))


def _text_at(path: Path) -> str:
    from ..repotext import lenient

    try:
        return lenient(path.read_bytes())
    except OSError:
        return ""


def _hooks_path_setting(root: Path) -> tuple[str, str]:
    """(scope, value) of core.hooksPath, or ("", "") when unset."""
    try:
        answer = _git(root, "config", "--show-scope", "--get", "core.hooksPath")
    except GitError:
        return "", ""
    scope, _, value = answer.strip().partition("\t")
    return scope, value


def _absolute(root: Path, *rev_parse: str) -> Path:
    return Path(_git(root, "rev-parse", "--path-format=absolute", *rev_parse).strip()).resolve()


def _shown(top: Path, path: Path) -> str:
    """A path as a reader types it at the git top: relative when it sits under
    the top, which is also how git reads a relative core.hooksPath."""
    try:
        return path.relative_to(top).as_posix()
    except ValueError:
        return path.as_posix()


def _husky_delegate(effective: Path) -> Path:
    """husky 9 points core.hooksPath at .husky/_, and its stub there runs
    .husky/pre-commit, the file a user edits. Any other hook is its own."""
    return effective.parent.parent / effective.name if effective.parent.name == "_" else effective


def _hook_route(root: Path, top: Path):
    """The pre-commit file git spawns here, what it and husky's delegate say,
    and the core.hooksPath setting behind it. Raises GitError outside a
    repository."""
    from ..doctor import HookRoute

    effective = _absolute(root, "--git-path", "hooks/pre-commit")
    edit = _husky_delegate(effective)
    text = _text_at(effective) + (_text_at(edit) if edit != effective else "")
    return HookRoute(_shown(top, effective), text, _shown(top, edit), *_hooks_path_setting(root))


def _tracked_hooks(top: Path) -> list[str]:
    """Every committed file named pre-commit, as paths from the top."""
    listed = _git(top, "ls-files", "-z", "--", ":(glob)**/pre-commit")
    return [path for path in listed.split("\0") if path]


def _gate_hooks(root: Path, top: Path) -> tuple:
    """The repo's own .git/hooks/pre-commit and every committed pre-commit,
    each with the git config line that points git at its directory."""
    from ..doctor import GateHook

    default = _absolute(root, "--git-common-dir") / "hooks" / "pre-commit"
    local = GateHook(_shown(top, default), _text_at(default),
                     f"git config --local core.hooksPath {_shell_quote(_shown(top, default.parent))}")
    return (local, *(GateHook(path, _text_at(top / path),
                              f"git config core.hooksPath {_shell_quote(posixpath.dirname(path) or '.')}")
                     for path in _tracked_hooks(top)))


def _doctor_silent_gates(root: Path) -> list[Finding]:
    """A gate that is set up and never judges anything (WARN): a crapkit hook
    git is not sent to, and a pre-commit config naming the gate that nothing
    installed. pre-commit run in CI is a gate: outside a commit, with nothing
    staged, the hook judges every tracked file."""
    from ..doctor import skipped_gates

    try:
        top = _absolute(root, "--show-toplevel")
        route, hooks = _hook_route(root, top), _gate_hooks(root, top)
    except GitError:
        return []
    precommit = _text_at(top / ".pre-commit-config.yaml")
    return list(skipped_gates(route, hooks, framework="crapkit-gate" in precommit))


def _merge_attribute(root: Path, path: str) -> str:
    """The value of the merge attribute git gives `path`, "unspecified" when
    none. Raises GitError outside a repository."""
    answer = _git(root, "check-attr", "merge", "--", path)
    return answer.strip().rpartition(": ")[2] or "unspecified"


def _doctor_merge_driver(root: Path, cfg) -> list[Finding]:
    """The marks file routed to a merge driver this clone never defined (WARN)."""
    from ..doctor import undefined_merge_driver
    from ..gitio import config_value

    try:
        driver = _merge_attribute(root, cfg.ratchet_file)
    except GitError:
        return []
    return list(undefined_merge_driver(cfg.ratchet_file, driver,
                                       config_value(root, f"merge.{driver}.driver")))


def _doctor_marks_stamp(root: Path, cfg) -> list[Finding]:
    """The marks file verify refuses for its metric stamp. Right after an
    upgrade that moves the analysis version, verify exits 3 before any lane
    runs, and doctor said `no problems found`.

    Marks an older metric stamped WARN: the upgrade guide runs doctor first
    and resolves its failures before it measures, reviews and re-seeds, so a
    FAIL stopped the guide at its first step and sent the user to re-seed
    before the review. Marks a newer crapkit or lizard stamped FAIL, since
    only an upgrade of this install clears them, and so does a marks file
    crapkit cannot read, which stops verify too."""
    from ..ratchet import metric_version, newer_tools
    from ..ratchetfile import RatchetFile

    try:
        marks = RatchetFile.read(root / cfg.ratchet_file)
    except ToolError as exc:
        return [Finding("FAIL", str(exc))]
    conflict = marks.stamp_conflict(metric_version())
    if not conflict:
        return []
    level = "FAIL" if newer_tools(marks.metric_stamp, metric_version()) else "WARN"
    return [Finding(level, f"`{_self()} verify` refuses {cfg.ratchet_file} at exit 3: {conflict}")]


def _doctor_findings(root: Path, cfg, raw: dict, files: list[str],
                     show_files: bool, packages: PackageMap) -> list[Finding]:
    named = [f for f in files if readable(f)]
    return (_doctor_keys(raw)
            + _doctor_scopes(root, cfg, files, show_files)
            + _doctor_path_names(cfg, raw, files)
            + _doctor_lanes(root, cfg, packages)
            + _doctor_runners(cfg, packages)
            + _doctor_inputs(root, cfg.lanes)
            + _doctor_stamps(root, cfg.lanes)
            + _doctor_artifact_litter(cfg)
            + _doctor_shared_coverage_data(cfg)
            + _doctor_hook_modes(root)
            + _doctor_hook_encoding(root)
            + _doctor_commit_graph(root)
            + _doctor_commit_encoding(root)
            + _doctor_container(cfg)
            + _doctor_silent_gates(root)
            + _doctor_merge_driver(root, cfg)
            + _doctor_marks_stamp(root, cfg)
            + _doctor_launchers()
            + _doctor_tools()
            + _doctor_scoped_tests(cfg, named)
            + _doctor_unmeasured(root, cfg, named)
            + _doctor_unread(root, cfg, named))


def _written_globs(raw: dict, cfg) -> tuple[tuple[str, str], ...]:
    """Each [exclude] glob as crapkit.toml holds it, paired with the glob the
    loader reads, leaving out init's default set: those guard trees a repo may
    never track, and matching nothing is them doing their job."""
    from ..scaffold import DEFAULT_EXCLUDES

    written = raw.get("exclude", {}).get("globs", ())
    return tuple((glob, read) for glob, read in zip(written, cfg.exclude_globs)
                 if read not in DEFAULT_EXCLUDES)


def _doctor_path_names(cfg, raw: dict, files: list[str]) -> list[Finding]:
    """An [exclude] glob that matches no tracked file, and a tracked name
    holding `\\` (both WARN)."""
    from ..doctor import backslash_names, unmatched_globs

    return [*unmatched_globs(_written_globs(raw, cfg), files), *backslash_names(files)]


def _doctor_inputs(root: Path, lanes) -> list[Finding]:
    """A lane `inputs` entry that matches no file git sees (FAIL). One git read
    narrowed to the entries, and none when no lane declares inputs."""
    from ..doctor import unmatched_inputs
    from ..lane_changes import visible_paths

    entries = sorted({entry for lane in lanes for entry in lane.inputs})
    return list(unmatched_inputs(lanes, visible_paths(root, entries)))


def _doctor_scoped_tests(cfg, files: list[str]) -> list[Finding]:
    """The scope a lane measures with no template (WARN), and the {files}
    template on a scope that holds no test file (FAIL)."""
    from ..doctor import files_template_gaps, scoped_test_gaps

    return (list(scoped_test_gaps(cfg.lanes, cfg.scoped_tests))
            + list(files_template_gaps(cfg.scoped_tests, cfg.scope_paths, files)))


def _at_level(findings: list[Finding], level: str) -> list[str]:
    return [f.text for f in findings if f.level == level]


def _version_report() -> dict:
    import platform

    return {"crapkit": __version__, "lizard": _lizard_version(),
            "python": platform.python_version()}


def _store_report(root: Path) -> dict:
    path = _store_path(root)
    present = path.is_file()
    return {"path": ".crapkit/crap.sqlite", "present": present,
            "size_bytes": path.stat().st_size if present else 0}


def _newest_run_report(store: SnapshotStore | None) -> dict | None:
    runs = store.list_runs() if store else []
    if not runs:
        return None
    return {"id": runs[-1]["id"], "kind": runs[-1]["kind"],
            "verdict_ok": runs[-1]["verdict_ok"]}


def _lane_report(root: Path, lane, stamps, packages: PackageMap = NO_PACKAGES) -> dict:
    """One `lanes[]` item of doctor --json. With no package map the toolchain
    is read from the lane's command alone."""
    stamp = stamps.entry(lane.artifact)
    found = _lane_toolchain(lane, packages)
    return {"artifact": lane.artifact,
            "artifact_present": (root / lane.artifact).is_file(),
            "commit": stamp.get("commit"),
            "name": lane.name,
            "refusal": _lane_refusal(lane, stamps),
            "seconds": stamp.get("seconds"),
            "toolchain": {"name": found.name, "source": found.source}}


def _lane_reports(root: Path, cfg, packages: PackageMap) -> list[dict]:
    from ..lane_stamps import read

    stamps = read(root)
    return [_lane_report(root, lane, stamps, packages) for lane in cfg.lanes]


def _lane_refusal(lane, stamps) -> str | None:
    """Why --reuse-artifacts refuses the lane's artifact, or None when it would
    score it. It is the question reuse itself asks (lane_stamps.Stamps.refusal),
    so both give one answer for a leftover, touched or not, and for an artifact
    whose record crapkit cannot read. An artifact on disk used to read as the
    lane's healthy output even when its last attempt wrote nothing."""
    cause = stamps.refusal(lane.artifact).cause(lane.artifact)
    if not cause:
        return None
    return f"{cause}; --reuse-artifacts will not score it until a run of the lane writes it again"


def _unreadable_stamp_note(key: str, writers: dict[str, str]) -> str:
    """`writers` maps each declared lane's artifact to the lane's name. A run
    merges its stamps over the file and rewrites only the keys its lanes own,
    so an entry no lane declares stays until someone deletes it."""
    writer = writers.get(key)
    fix = (f"lane {writer!r} replaces it on its next successful run, or delete the entry"
           if writer else "no declared lane writes this key, so delete the entry")
    from ..lane_stamps import STAMPS_FILE

    return (f"{STAMPS_FILE}: the entry for {key!r} is not an object, so crapkit "
            f"reads it as no stamp (no commit, no duration) and `--reuse-artifacts` refuses the "
            f"lane while it stands; {fix}")


def _doctor_stamps(root: Path, lanes) -> list[Finding]:
    """WARN, never FAIL: every reader already takes a mangled entry, or a file
    it cannot read, as no stamp. Named anyway, because the file is hand-edited
    and the reader has to find the line doctor skipped. A refused leftover is a
    WARN too: the lane's next run clears it."""
    from ..lane_stamps import read

    stamps = read(root)
    if stamps.unreadable:
        return [Finding("WARN", _unreadable_stamps_file_note(stamps.unreadable))]
    writers = {lane.artifact: lane.name for lane in lanes}
    mangled = [Finding("WARN", _unreadable_stamp_note(key, writers)) for key in stamps.mangled()]
    return mangled + _refusal_findings(lanes, stamps)


def _refusal_findings(lanes, stamps) -> list[Finding]:
    """One WARN per lane whose artifact --reuse-artifacts refuses."""
    refusals = ((lane, _lane_refusal(lane, stamps)) for lane in lanes)
    return [Finding("WARN", f"lane {lane.name!r}: {refusal}") for lane, refusal in refusals
            if refusal]


def _unreadable_stamps_file_note(fault: str) -> str:
    from ..lane_stamps import STAMPS_FILE

    return (f"{STAMPS_FILE} cannot be read ({fault}), so crapkit reads it as no stamps: "
            "--reuse-unchanged reruns every lane and --reuse-artifacts refuses every lane's "
            "artifact, since it cannot tell a failed attempt's leftover; the next lane run "
            "writes the file again, or delete it")


def _doctor_report(root: Path, cfg, findings: list[Finding], packages: PackageMap) -> dict:
    """Everything a wrapper needs to tell lane rot from a stale artifact without
    parsing prose: versions, store, newest run, per-lane stamps, findings."""
    from ..analyze import ANALYSIS_VERSION

    return {"analysis_version": ANALYSIS_VERSION,
            "lanes": _lane_reports(root, cfg, packages),
            "newest_run": _newest_run_report(_store_if_any(root)),
            "problems": _at_level(findings, "FAIL"),
            "resources": _resource_policy(cfg),
            "store": _store_report(root),
            "versions": _version_report(),
            "warnings": _at_level(findings, "WARN")}


def _resource_policy(cfg) -> dict:
    from ..resources import resource_status
    return {**resource_status(analysis_workers=cfg.analysis_workers,
                              worker_budget=cfg.analysis_worker_budget),
            "log_max_bytes": cfg.log_max_bytes,
            # Deprecated: crapkit applies no test evidence retention; its
            # development runner does. Zero disables a limit, and none applies.
            "test_retention_days": 0,
            "test_retention_count": 0}


def _doctor_verdict(findings: list[Finding]) -> str:
    """The closing line, with the WARN count: a container's lane WARN closed
    on a bare "no problems found", the one line a skimming reader reads."""
    problems, warnings = len(_at_level(findings, "FAIL")), len(_at_level(findings, "WARN"))
    verdict = f"doctor: {problems} problem(s)" if problems else "doctor: no problems found"
    if not warnings:
        return verdict
    return f"{verdict}, {warnings} warning{'s' if warnings > 1 else ''} above"


def _print_findings(findings: list[Finding]) -> None:
    for f in findings:
        print(f"{f.level:<4} {f.text}" if f.level else f.text)
    print(_doctor_verdict(findings))


def _emit_doctor(root: Path, cfg, findings: list[Finding], as_json: bool,
                 packages: PackageMap) -> None:
    if as_json:
        _print_json(_doctor_report(root, cfg, findings, packages))
        return
    policy = _resource_policy(cfg)
    print(f"resources: up to {policy['pool_worker_limit']} analysis worker(s) per pool, "
          f"{policy['shared_pool_limit']} shared slot(s); "
          f"lane log limit {policy['log_max_bytes']} bytes per file")
    _print_findings(findings)


def _lane_durations(root: Path, cfg) -> tuple[tuple[float, ...], tuple[str, ...]]:
    """The durations on disk, and the names of the lanes that left none. A lane
    with no cost signal is named, never summed as 0 and never dropped unsaid. Each lane's
    cost is read the way the start order reads it (lanes.lane_seconds)."""
    from ..lanes import lane_seconds, read_stamps

    stamps = read_stamps(root)
    known, unknown = [], []
    for lane in cfg.lanes:
        seconds = lane_seconds(root, stamps, lane)
        if seconds is None:
            unknown.append(lane.name)
        else:
            known.append(seconds)
    return tuple(known), tuple(unknown)


def _doctor_tune(root: Path, cfg) -> int:
    """Advisory only: knob lines from this machine's cpu count and whatever lane
    durations are already on disk. Nothing is written and nothing is executed."""
    from ..doctor import shared_coverage_data, suggest_knobs, tune_lines
    from ..resources import available_cpus

    for finding in _doctor_stamps(root, cfg.lanes):
        print(f"{finding.level} {finding.text}", file=sys.stderr)
    cpus, _ = available_cpus()
    knobs = suggest_knobs(cpus=cpus, lanes=len(cfg.lanes), shared=shared_coverage_data(cfg.lanes))
    durations, unmeasured = _lane_durations(root, cfg)
    for line in tune_lines(cpus=cpus, knobs=knobs, durations=durations, unmeasured=unmeasured):
        print(line)
    return 0


def _plugin_json(path: Path) -> dict | None:
    """One JSON object off an installed plugin, or None.

    Missing, unreadable, half-written and not an object all read the same,
    because doctor's job here is to name the file rather than to raise inside
    it. A plugin cache is written by an installer this process does not
    control.

    Read the way Claude Code reads it, since that is the reader this check
    answers for: a byte that is not UTF-8 as U+FFFD, and a byte-order mark as
    the JSON error it is to Node (`claude plugin validate` refuses one). A
    strict read called a manifest Claude Code loads missing.
    """
    import json

    from ..repotext import plain_utf8

    try:
        value = json.loads(plain_utf8(path.read_bytes()))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _hook_handlers(hooks: dict) -> list[dict]:
    """Every command handler in a hooks.json, flat across events and matchers."""
    return [handler for event in hooks.get("hooks", {}).values()
            for matcher in event for handler in matcher.get("hooks", [])]


def _handler_words(handler: dict) -> list[str]:
    """The words one handler starts: its `args` in exec form, else its
    `command` split the way the shell that runs a shell-form hook splits it.
    Raises ValueError on a shape no harness could run."""
    import shlex

    words = handler["args"] if "args" in handler else shlex.split(handler.get("command", ""))
    if not isinstance(words, list) or any(not isinstance(word, str) for word in words):
        raise ValueError("hook args must be a list of strings")
    return words


def _named_protocol(handler: dict) -> str | None:
    """The `--protocol` value one handler spawns crapkit with, or None.

    Read off `args` in exec form and off the command string in shell form,
    spelled `--protocol N` or `--protocol=N` as argparse takes either. Paired
    off the word list rather than indexed past the flag: a handler whose words
    end at `--protocol` is malformed, and reading it must not raise.
    """
    words = _handler_words(handler)
    inline = next((word.partition("=")[2] for word in words if word.startswith("--protocol=")), None)
    return inline or dict(zip(words, words[1:])).get("--protocol")


def _exec_form(root: Path) -> bool:
    """Does any of the plugin's hooks pass `args`? Only those reach crapkit
    through a field Claude Code below 2.1.139 drops."""
    hooks = _plugin_json(root / "hooks" / "hooks.json")
    try:
        return any("args" in handler for handler in _hook_handlers(hooks))
    except (AttributeError, TypeError):
        return False


def _hook_protocols(root: Path) -> tuple[str, ...] | None:
    """Every protocol the plugin's hooks name, or None for unreadable hooks.
    The empty tuple is the third state: a hooks file naming no protocol,
    which argparse defaults to the supported one."""
    hooks = _plugin_json(root / "hooks" / "hooks.json")
    if not isinstance(hooks, dict):
        return None
    try:
        named = [_named_protocol(handler) for handler in _hook_handlers(hooks)]
    except (AttributeError, TypeError, ValueError):
        return None
    return tuple(p for p in named if p is not None)


def _manifest_field(root: Path, field: str) -> str | None:
    manifest = _plugin_json(root / ".claude-plugin" / "plugin.json")
    return manifest.get(field) if isinstance(manifest, dict) else None


def _manifest_version(root: Path) -> str | None:
    """The manifest's version when it is a string, else None: a number or a
    list there ranks no install and matches no CLI, and ranking by it raised."""
    version = _manifest_field(root, "version")
    return version if isinstance(version, str) and version else None


def _manifest_fault(root: Path) -> str:
    """Why the manifest gives no version, in doctor.plugin_handshake's words:
    no file, a file that is not a JSON object, or an object with no version."""
    path = root / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        return "missing"
    return "unversioned" if isinstance(_plugin_json(path), dict) else "not-an-object"


# Claude Code keeps an install at <config>/plugins/cache/<marketplace>/<plugin>/
# <version>/. From any directory an operator would name, stepping into `plugins`
# and `cache` when they exist puts the manifest at most three levels down. The
# marketplace clones beside the cache are sources, not the plugin Claude Code
# runs, and the other vendors' plugins sharing the cache are never the answer.
_LAYOUT_HOPS = ("plugins", "cache")
_CACHE_DEPTHS = ("*", "*/*", "*/*/*")


def _cache_under(under: Path) -> Path:
    """`under`, stepped into Claude Code's plugin cache when it sits above one."""
    for hop in _LAYOUT_HOPS:
        if (under / hop).is_dir():
            under = under / hop
    return under


def _crapkit_installs(cache: Path) -> list[Path]:
    """Every install up to three levels under `cache` whose manifest is named
    crapkit. The other vendors' plugins sharing the cache are never the answer."""
    roots = [m.parent.parent for depth in _CACHE_DEPTHS
             for m in cache.glob(f"{depth}/.claude-plugin/plugin.json")]
    return [r for r in roots if _manifest_field(r, "name") == "crapkit"]


def _manifest_roots(under: Path) -> list[Path]:
    """crapkit plugin roots at or below `under`: the directory itself when it
    holds a manifest, else the installs under the cache it sits above (#28)."""
    if (under / ".claude-plugin" / "plugin.json").is_file():
        return [under]
    return _crapkit_installs(_cache_under(under))


def _version_key(version: str | None) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def _newest_root(roots: list[Path]) -> Path | None:
    """The install with the highest manifest version. An update leaves the old
    version beside the new one in the cache, and the old one is not the
    plugin Claude Code runs."""
    return max(roots, key=lambda r: (_version_key(_manifest_version(r)), str(r)), default=None)


def _plugins_dir() -> Path:
    """Where Claude Code keeps plugins: under CLAUDE_CONFIG_DIR, else ~/.claude."""
    from ..userhome import user_home

    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (typed_path(base) if base else user_home() / ".claude") / "plugins"


def _plugin_entries(recorded) -> dict:
    """installed_plugins.json's `plugins` object, or {} for any other shape."""
    entries = recorded.get("plugins") if isinstance(recorded, dict) else None
    return entries if isinstance(entries, dict) else {}


def _install_path(entry) -> str:
    path = entry.get("installPath") if isinstance(entry, dict) else None
    return path if isinstance(path, str) else ""


def _listed_installs(installs) -> list:
    """The installs one plugin id records. Claude Code writes V2, a list of
    installs; an older one wrote V1, one object, and a newer one converts it
    only when it loads the file."""
    return installs if isinstance(installs, list) else [installs]


def _crapkit_install_lists(recorded) -> list:
    return [installs for key, installs in _plugin_entries(recorded).items()
            if key.startswith("crapkit@")]


def _crapkit_records(recorded) -> list[dict]:
    """Every install installed_plugins.json records for crapkit, V1 or V2; an
    entry that is not an object records nothing."""
    return [e for installs in _crapkit_install_lists(recorded) for e in _listed_installs(installs)
            if isinstance(e, dict)]


def _recorded_roots(recorded) -> list[Path]:
    """Install directories installed_plugins.json records for crapkit.

    An entry of any shape but a string installPath records nothing, and the
    cache scan beside this still finds the install: V1 and hand-edited files
    ended the command meant to diagnose the plugin in a traceback."""
    return [Path(path) for path in map(_install_path, _crapkit_records(recorded)) if path]

def _newest_first(root: Path) -> tuple:
    return _version_key(_manifest_version(root)), str(root)


def _recorded_installs(plugins: Path) -> list[Path]:
    """Every install installed_plugins.json records that is still on disk,
    newest version first. Each is a plugin some session runs: a user install
    made at one version and a project install made at a later one are two
    cache directories, and both run."""
    recorded = _recorded_roots(_plugin_json(plugins / "installed_plugins.json"))
    live = [r for r in dict.fromkeys(recorded) if (r / ".claude-plugin" / "plugin.json").is_file()]
    return sorted(live, key=_newest_first, reverse=True)


def _newest(roots: list[Path]) -> list[Path]:
    newest = _newest_root(roots)
    return [newest] if newest else []


def _codex_home() -> Path:
    """Where Codex keeps its state: CODEX_HOME, else ~/.codex."""
    from ..userhome import user_home

    base = os.environ.get("CODEX_HOME")
    return Path(base) if base else user_home() / ".codex"


class _Found(NamedTuple):
    """A plugin root to check, and why a root the search found is that one."""
    root: Path
    why: str = ""


class _PluginRoots(NamedTuple):
    """The plugin roots to check, none when none was found, and where they
    were looked for."""
    roots: tuple[_Found, ...]
    looked_in: str


_IN_PLACE = " (Claude Code loads a plugin from a local directory marketplace in place)"


def _resolve_plugin_root(arg: str) -> _PluginRoots:
    """The plugin roots to check, and where they were looked for.

    An explicit PATH resolves to the newest install at or under it, or to
    itself when it holds no manifest, so the handshake names the missing file
    at the path the operator typed. With none, every install Claude Code
    recorded, else the newest in its cache, else the newest in Codex's.
    """
    if not arg:
        return _default_plugin_root()
    under = typed_path(arg)
    return _PluginRoots((_Found(_newest_root(_manifest_roots(under)) or under),), str(under))


def _where_it_loads(root: Path) -> _Found:
    """The copy Claude Code runs for the install at `root`. It runs a plugin
    from a marketplace added as a local directory in place, so for that one it
    is the directory, not its cache copy."""
    listed = _marketplace_copy(root)
    return _Found(listed[1], _IN_PLACE) if listed and listed[0] == "directory" else _Found(root)


def _default_plugin_root() -> _PluginRoots:
    """Every install Claude Code recorded; the newest in its cache when no
    record names one on disk; else the newest in Codex's cache. A cached
    version no record names is one `claude plugin update` left behind, and no
    session runs it."""
    plugins, codex = _plugins_dir(), _codex_home()
    found = (_recorded_installs(plugins) or _newest(_manifest_roots(plugins))
             or _newest(_manifest_roots(codex)))
    return _PluginRoots(tuple(dict.fromkeys(map(_where_it_loads, found))), f"{plugins} or {codex}")


def _claude_plugins_of(root: Path) -> Path | None:
    """The plugins directory an install at <plugins>/cache/<marketplace>/
    <plugin>/<version> sits in, else None."""
    parents = root.parents
    if len(parents) < 4 or parents[2].name != "cache" or parents[3].name != "plugins":
        return None
    return parents[3]


def _listed_plugins(clone: Path) -> list[dict]:
    listing = _plugin_json(clone / ".claude-plugin" / "marketplace.json")
    listed = listing.get("plugins", []) if isinstance(listing, dict) else []
    return [entry for entry in listed if isinstance(entry, dict)] if isinstance(listed, list) else []


def _listed_source(clone: Path, name: str) -> str | None:
    """The relative source the marketplace at `clone` lists for plugin `name`."""
    source = next((p.get("source") for p in _listed_plugins(clone) if p.get("name") == name), None)
    return source if isinstance(source, str) else None


def _marketplace_entry(root: Path) -> dict:
    """known_marketplaces.json's record of the marketplace an install came from."""
    plugins = _claude_plugins_of(root)
    known = _plugin_json(plugins / "known_marketplaces.json") if plugins else None
    entry = known.get(root.parents[1].name) if isinstance(known, dict) else None
    return entry if isinstance(entry, dict) else {}


def _marketplace_copy(root: Path) -> tuple[str, Path] | None:
    """(the marketplace's source kind, its own copy of this plugin) for an
    install in Claude Code's cache, else None."""
    entry = _marketplace_entry(root)
    clone = entry.get("installLocation")
    source = _listed_source(Path(clone), root.parent.name) if isinstance(clone, str) and clone else None
    kind = entry.get("source", {}).get("source", "") if isinstance(entry.get("source"), dict) else ""
    return (kind, Path(clone) / source) if source else None


def _directory_entry(entry) -> bool:
    source = entry.get("source") if isinstance(entry, dict) else None
    return isinstance(source, dict) and source.get("source") == "directory"         and isinstance(entry.get("installLocation"), str)


def _directory_marketplaces() -> list[Path]:
    """Every marketplace known_marketplaces.json records as a local directory."""
    known = _plugin_json(_plugins_dir() / "known_marketplaces.json")
    entries = known.values() if isinstance(known, dict) else ()
    return [Path(entry["installLocation"]) for entry in entries if _directory_entry(entry)]


def _same_directory(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _git_commands(marketplace: Path, root: Path) -> tuple[str | None, str | None]:
    """The commands that update a marketplace directory that is a git checkout
    and restore a file of the plugin at `root` inside it; none for a directory
    that is no checkout."""
    if not (marketplace / ".git").exists():
        return None, None
    return (f"git -C {_shell_quote(str(marketplace))} pull",
            f"git -C {_shell_quote(str(root))} checkout --")


def _in_place(root: Path):
    """The local directory marketplace whose crapkit plugin is `root`, which
    Claude Code loads in place, or None."""
    from ..doctor import InPlace

    for marketplace in _directory_marketplaces():
        source = _listed_source(marketplace, "crapkit")
        if source and _same_directory(marketplace / source, root):
            return InPlace(str(marketplace), *_git_commands(marketplace, root))
    return None


def _probed_cli_version(executable: str) -> str | None:
    """The launcher's declared version, or None when it cannot answer.

    Read as bytes and decoded here: on Windows a text-mode read decodes in
    subprocess's reader thread, so a byte that is not UTF-8 printed that
    thread's traceback into doctor's stderr and never reached an except."""
    import subprocess

    from ..repotext import lenient

    try:
        done = _asked_version(executable)
    except (OSError, subprocess.SubprocessError):
        return None
    return _declared_version(lenient(done.stdout or done.stderr)) if done.returncode == 0 else None


# Windows' own spelling is case-blind; os.environ keeps its keys upper-case there,
# so this spelling replaces the caller's rather than sitting beside it.
_NO_CWD_SEARCH = {"NODEFAULTCURRENTDIRECTORYINEXEPATH": "1"}


def _asked_version(executable: str):
    """`executable --version`, run from the executable's own folder with cmd.exe's
    search of its current directory off. A launcher may start its interpreter by
    bare name: npm's shim for a JS bin runs `node` through cmd.exe, which looks
    in its current directory first, and a `#!/usr/bin/env node` script has env
    read an empty PATH entry as that directory. Run from where crapkit stood, a
    `node.bat` planted in the repo answered doctor's probe."""
    import subprocess

    return subprocess.run([executable, "--version"], capture_output=True, timeout=_PROBE_TIMEOUT_SECONDS,
                          cwd=os.path.dirname(executable), env={**os.environ, **_NO_CWD_SEARCH})


def _declared_version(answer: str) -> str | None:
    """The version in `crapkit X.Y.Z`. One holding U+FFFD, a byte that was not
    UTF-8, is no version: printed, it named a CLI nothing on the machine is."""
    words = answer.split()
    readable = len(words) == 2 and words[0] == "crapkit" and "\ufffd" not in words[1]
    return words[1] if readable else None


def _path_launchers() -> list[str]:
    """Every crapkit launcher on this PATH's absolute entries, in order, less
    any environment a one-command runner (uvx, `uv run --with`, `pipx run`)
    built, which that runner put on doctor's PATH and nothing else on the
    machine inherits. A relative entry names the working directory, and a
    launcher found there is a planted file doctor must neither name nor run."""
    from ..launchers import path_launchers

    return path_launchers(os.pathsep.join(search_path()))


@lru_cache(maxsize=None)
def _launcher_report(path: str) -> tuple[tuple[str, str | None], ...]:
    """(launcher, version) for every launcher when PATH holds more than one,
    else (). Memoized on PATH: one machine fact, and a version costs a spawn."""
    found = _path_launchers()
    return tuple((launcher, _probed_cli_version(launcher)) for launcher in found) \
        if len(found) > 1 else ()


def _doctor_launchers() -> list[Finding]:
    """Two or more crapkit launchers on PATH: a WARN naming each with its
    version, or a note while they agree."""
    from ..doctor import launcher_skew

    return list(launcher_skew(_launcher_report(os.environ.get("PATH", ""))))


@lru_cache(maxsize=None)
def _spawned_cli() -> tuple[str, str | None] | None:
    """The console script the plugin actually starts and the version it answers,
    or None when PATH carries no `crapkit` at all.

    `plugin/hooks/hooks.json` names a bare `crapkit` on all 48 PostToolUse
    entries and `plugin/.mcp.json` names it for the MCP server, so the CLI the
    plugin will call is PATH's answer, never the module this handshake runs in.
    Compared against `__version__` the check described its own process twice
    over: inside a project `.venv` with no `crapkit` on PATH it printed nothing
    and exited 0 while every edit fired a command that cannot start, and beside
    an older pipx copy it called the two versions equal while the hook spawned
    the older one. Under uvx it found the launcher uvx had put on its own PATH
    and on no other, and passed a plugin whose hooks could not start: PATH is
    read without this process's own cached environment.

    Under uvx, `uv run --with` or `pipx run` the PATH doctor inherits starts with
    the environments that runner built for this one command. The plugin's hooks
    never see them, so they are left out: `uvx crapkit doctor --plugin-root`
    found crapkit there and passed while `claude mcp list` failed with ENOENT,
    and `uv run --with crapkit` did the same from uv's builds-v0 bucket.

    Memoized because the answer is one machine fact and `doctor --plugin-root`
    would otherwise spawn it once per call.
    """
    found = _path_launchers()
    return (found[0], _probed_cli_version(found[0])) if found else None


def _launcher_dirs() -> list[Path]:
    """Where this interpreter puts a console script: its own scheme's scripts
    directory (a venv's bin or Scripts), then the user scheme's, which is where
    `pip install --user` writes (~/.local/bin, %APPDATA%\\Python\\PythonXY\\Scripts)."""
    import sysconfig

    user = sysconfig.get_preferred_scheme("user")
    return [Path(sysconfig.get_path("scripts")), Path(sysconfig.get_path("scripts", user))]


def _unlisted_launcher() -> Path | None:
    """The directory holding this crapkit's own launcher. Asked only once PATH
    answered no `crapkit`, so PATH does not list it."""
    name = "crapkit.exe" if os.name == "nt" else "crapkit"
    return next((directory for directory in _launcher_dirs() if (directory / name).is_file()), None)


def _no_crapkit_on_path() -> str:
    """The FAIL for a machine where nothing the plugin declares can start. It
    names both files that spawn the bare name, because the reader is about to
    look for a plugin problem and the problem is an install location. Under a
    one-command runner it names the environment that runner's tool built, the
    one crapkit this process did find, and the install that stays. Otherwise it
    names the directory this crapkit's launcher sits in when there is one; under
    a runner that directory is the runner's own environment, which the tool
    deletes or rebuilds, so it is never named there."""
    from ..launchers import ephemeral_runner, install_line

    builder = ephemeral_runner(sys.prefix)
    if builder:
        return (f"crapkit doctor: FAIL no `crapkit` on PATH outside the environment {builder} "
                f"built for this one command ({sys.prefix}), and the plugin's hooks never "
                "inherit that one: its hooks/hooks.json and .mcp.json both spawn the bare name, "
                "so every PostToolUse edit fires a command that cannot start and the MCP server "
                "never comes up. Install crapkit where the hook's PATH can see it "
                f"({install_line(builder)}), then run this check again.")
    found = _unlisted_launcher()
    where = (f" This crapkit's launcher is in {found}, which PATH does not list: add that "
             "directory to PATH, then restart the agent.") if found else ""
    return ("crapkit doctor: FAIL no `crapkit` on PATH - the plugin's hooks/hooks.json and "
            ".mcp.json both spawn that bare name, so every PostToolUse edit fires a command "
            "that cannot start and the MCP server never comes up. Install it where the "
            "PATH the hook inherits can see it (`pipx install crapkit`), or point the "
            "plugin at the environment holding it." + where)


# The README's install lines: Claude Code's sparse, Codex's also pinned to the
# tag of this CLI's release, since an unpinned Codex marketplace follows main
# and moves the plugin past the CLI at the next Codex start.
_INSTALL_PLUGIN = (
    "Claude Code installs it with `claude plugin marketplace add JeanFrancoisGagne/crapkit "
    "--sparse .claude-plugin plugin`, then `claude plugin install crapkit@crapkit`; Codex with "
    "`{codex_add}`, then `codex plugin add crapkit@crapkit`. For a plugin kept anywhere else, "
    "pass --plugin-root PATH."
)


def _install_plugin() -> str:
    from ..doctor import CODEX_MARKETPLACE_ADD

    return _INSTALL_PLUGIN.format(codex_add=CODEX_MARKETPLACE_ADD.format(version=__version__))


def _name_found_root(found: _Found, looked_in: str) -> None:
    """A root the search found, not one the operator typed: the glob reaches
    three levels under the named directory, so a source checkout can win over an
    install. Naming it is how the reader knows which tree the verdict is about.
    """
    if str(found.root) != looked_in:
        print(f"crapkit doctor: checking {found.root}{found.why}")


@lru_cache(maxsize=None)
def _claude_code_version() -> tuple[str, str] | None:
    """The `claude` on PATH's absolute entries and what its `--version`
    printed, or None when they hold none or it cannot answer. Memoized: one
    machine fact."""
    import subprocess

    from ..repotext import lenient

    executable = find("claude")
    if executable is None:
        return None
    try:
        done = _asked_version(executable)
    except (OSError, subprocess.SubprocessError):
        return None
    return executable, lenient(done.stdout).strip()


def _claude_code_drops_args(root: Path) -> bool:
    """Would a Claude Code below the floor drop this install's hook args? Only
    for an install Claude Code runs (Codex runs none of its hooks) whose hooks
    pass any: a shell-form hook runs as written."""
    from ..doctor import plugin_harness

    return plugin_harness(str(root), os.environ.get("CODEX_HOME")) == "claude" and _exec_form(root)


def _claude_code_floor(root: Path) -> list[str]:
    """The line for a Claude Code on PATH too old to pass the plugin's hook
    args, when this install's hooks depend on them."""
    from ..doctor import claude_code_floor_gap

    found = _claude_code_version() if _claude_code_drops_args(root) else None
    line = claude_code_floor_gap(*found) if found else None
    return [line] if line else []


def _doctor_plugin(plugin_root: str) -> int:
    """`--plugin-root [PATH]`: the installed plugin against this CLI.

    No repo is read. The plugin cache is not a repo, and an operator asking
    whether their plugin is behind their CLI is rarely standing in one. PATH
    is the plugin root or any directory above it, ~/.claude included; empty
    means Claude Code's own plugin directory (#28).

    `PROTOCOL` comes from the hook module itself, so the number doctor promises
    and the number `claude-hook` accepts cannot drift apart. The version comes
    from the `crapkit` on PATH, because that bare name is what the plugin's
    hooks and its MCP server spawn — see `_spawned_cli`.
    """
    found = _resolve_plugin_root(plugin_root)
    if not found.roots:
        print(f"crapkit doctor: no installed crapkit plugin under {found.looked_in}. {_install_plugin()}")
        return 1
    for each in found.roots:
        _name_found_root(each, found.looked_in)
    return _print_problems(_spawn_failure() or _roots_lines(found.roots))


def _roots_lines(roots: tuple[_Found, ...]) -> list[str]:
    """Every root's disagreements, each once: the Claude Code floor is one
    machine fact, whichever install it came up under."""
    return list(dict.fromkeys(line for each in roots for line in _plugin_lines(each.root)))


def _print_problems(lines: list[str]) -> int:
    for line in lines:
        print(line)
    return 1 if lines else 0


def _spawn_failure() -> list[str]:
    """The FAIL for a `crapkit` the plugin cannot start or that answers no
    version, else nothing: there is no CLI to compare the plugin with."""
    spawned = _spawned_cli()
    if spawned is None:
        return [_no_crapkit_on_path()]
    executable, cli_version = spawned
    if cli_version is None:
        from ..launchers import reinstall_command

        return [f"crapkit doctor: FAIL {executable} gave no readable answer to `crapkit --version`. "
                f"Reinstall the crapkit it belongs to with "
                f"`{reinstall_command(executable, _shell_quote)}`, then run this check again."]
    return []


def _plugin_lines(root: Path) -> list[str]:
    """Every disagreement between the plugin at `root` and the crapkit its
    hooks spawn, each repair spelled for the harness that installed the plugin,
    each scope that holds it, and the installer that owns the launcher; then an
    install whose files the marketplace has moved past at one version, and the
    Claude Code floor."""
    from ..doctor import plugin_handshake, plugin_harness
    from ..launchers import upgrade_command
    from .claude_hook import PROTOCOL

    executable, cli_version = _spawned_cli()
    handshake = plugin_handshake(where=str(root), version=_manifest_version(root),
                                 cli_version=cli_version, cli_where=executable,
                                 protocols=_hook_protocols(root), supported=PROTOCOL,
                                 harness=plugin_harness(str(root), os.environ.get("CODEX_HOME")),
                                 cli_upgrade=upgrade_command(executable, _shell_quote),
                                 scopes=_install_scopes(root), in_place=_in_place(root),
                                 manifest_fault=_manifest_fault(root))
    return handshake + _stale_copy(root) + _claude_code_floor(root)


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _differing_files(source: Path, install: Path) -> tuple[str, ...]:
    """The marketplace copy's files the install lacks or holds other bytes
    for, sorted. Files only the install holds are Claude Code's own (.in_use)."""
    files = (path.relative_to(source) for path in source.rglob("*") if path.is_file())
    return tuple(sorted(rel.as_posix() for rel in files if not _same_bytes(source / rel, install / rel)))


def _scope_of(record: dict):
    """The scope one installed_plugins.json record names, with its project
    directory when it has one, or None for a record naming no scope."""
    from ..doctor import InstallScope

    scope, project = record.get("scope"), record.get("projectPath")
    if not isinstance(scope, str):
        return None
    return InstallScope(scope, project if isinstance(project, str) and project else None)


def _install_scopes(root: Path) -> tuple:
    """Every scope installed_plugins.json records this install under, in its
    order. Claude Code keeps one cache directory per version, so a user install
    and project installs of one version share it. Empty for an install no
    record names, or one outside Claude Code's cache."""
    plugins = _claude_plugins_of(root)
    recorded = _plugin_json(plugins / "installed_plugins.json") if plugins else None
    same = (e for e in _crapkit_records(recorded)
            if os.path.normcase(str(e.get("installPath", ""))) == os.path.normcase(str(root)))
    return tuple(filter(None, map(_scope_of, same)))


def _stale_copy(root: Path) -> list[str]:
    """The line for a Claude Code install whose files differ from its
    marketplace's copy at one version. A local directory marketplace loads in
    place, so its cache copy is never the one that runs."""
    from ..doctor import stale_copy

    listed = _marketplace_copy(root)
    if listed is None or listed[0] == "directory":
        return []
    source = listed[1]
    line = stale_copy(where=str(root), version=_manifest_version(root), source=str(source),
                      source_version=_manifest_version(source), scopes=_install_scopes(root),
                      differing=_differing_files(source, root))
    return [line] if line else []


def cmd_doctor(args: argparse.Namespace) -> int:
    import tomllib

    if args.plugin_root is not None:
        return _doctor_plugin(args.plugin_root)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)  # a config that does not parse already exits 3 here
    if args.tune:
        return _doctor_tune(root, cfg)
    raw = tomllib.loads(repo_text(root / "crapkit.toml", "crapkit.toml"))
    packages = _doctor_packages(root)  # read once, for the text lines and --json alike
    findings = _doctor_findings(root, cfg, raw, ls_files(root), args.show_files, packages)
    _emit_doctor(root, cfg, findings, args.json, packages)
    return 1 if _at_level(findings, "FAIL") else 0


def _refuse_upgraded() -> None:
    """A rescore after `pip install -U` would import the new release's modules
    into this process, and the watcher died with a traceback from inside them:
    stop and name the restart instead."""
    installed = upgraded_to()
    if installed:
        raise CrapkitError(f"crapkit was upgraded from {__version__} to {installed} while "
                           f"`crapkit watch` ran, and this process still runs {__version__}'s "
                           "code, which cannot load the new files; restart `crapkit watch`")


def _watch_rescore(root: Path, moved: list[str]) -> None:
    _refuse_upgraded()
    from ..procs import run_owned

    present = [f for f in moved if (root / f).is_file()]
    if not present:
        return
    # flush: watch output exists to be tailed live; a block-buffered pipe sits silent
    print(f"--- changed: {', '.join(moved)}", flush=True)
    # a subprocess so a half-saved syntax error can never kill the watcher.
    # PYTHONSAFEPATH=1: `-m` puts this working directory, the watched repo's
    # root, first on sys.path, where a crapkit.py ran in place of crapkit. A
    # rescore starts no configured command, so the variable reaches none.
    run_owned([sys.executable, "-m", "crapkit", "rescore", *present, "--repo", str(root)],
              env={**os.environ, "PYTHONSAFEPATH": "1"})


def _watched_files(root: Path, cfg) -> list[str]:
    """Every file a scope claims, tracked or not yet added, ignored ones left
    out, flat: the whole subject of one poll. Listed again each poll, so a
    file created in a scope while the watch runs is rescored too. The first
    poll's scan names the unreadable names it left out (cmd_watch). Raises
    GitError."""
    return _flat(_watched_scan(root, cfg))


def _watched_scan(root: Path, cfg):
    """The scan of every file under the declared scope paths: git looks for new
    files there only, since over the whole tree of a 33k-file repo that search
    took 0.88 s a poll, under its scopes 0.27 s. A claimed name that is not
    UTF-8 is refused and an unclaimed one is left out, as at every scan."""
    from ..lane_changes import visible_paths

    declared = tuple(dict.fromkeys(path for scope in cfg.scopes for path in scope.paths))
    return _scan(root, list(visible_paths(root, declared)), cfg)


def _flat(universe) -> list[str]:
    return [f for files in universe.by_scope.values() for f in files]


class _Watching(NamedTuple):
    """What one poll hands the next: the files it listed, what it saw, and
    the git error its listing hit, "" when git listed them."""
    files: list[str]
    seen: Snapshot
    fault: str


def _relisted(root: Path, cfg, state: _Watching) -> tuple[list[str], str]:
    """This poll's files. When git cannot list them, the last poll's files, and
    the error named on the first poll that hits it, not on every one after."""
    try:
        return _watched_files(root, cfg), ""
    except GitError as exc:
        fault = str(exc)
    if fault != state.fault:
        print(f"crapkit watch: could not list the files your scopes claim ({fault}); "
              "fix what git reports. Until git lists them, each poll reads the last list "
              "and asks git again", flush=True)
    return state.files, fault


def _watch_cycles(cycles: int | None):
    """The poll counter: `cycles` polls, or an endless one when nothing bounds it.

    Unbounded is the default; the caller must explicitly supervise or detach a
    persistent watcher. A bound is what lets the loop be driven
    to a known end — by a test, or by a caller that wants one sweep and its exit
    code rather than a process to kill.
    """
    from itertools import count

    return count() if cycles is None else range(cycles)


def _watch_banner(watched: int, interval: float, cycles: int | None) -> str:
    """The first line, naming how this run ends. Telling an operator to press
    ctrl-c on a `--cycles 3` run describes a loop that is not the one running.
    `watched` counts every file a scope claims, untracked ones included."""
    stop = "ctrl-c to stop" if cycles is None else f"{cycles} poll(s) then stop"
    return f"watching {watched} file(s) in scope every {interval}s - {stop}"


def _watch_cycle(root: Path, cfg, state: _Watching, interval: float) -> _Watching:
    """One poll: wait, list the files again, rescore whatever holds new bytes;
    what the next poll starts from out."""
    import time

    time.sleep(interval)
    files, fault = _relisted(root, cfg, state)
    seen, moved = poll(root, files, state.seen)
    if moved:
        _watch_rescore(root, moved)
    return _Watching(files, seen, fault)


def cmd_watch(args: argparse.Namespace) -> int:
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    universe = _watched_scan(root, cfg)
    _say_left_out(universe.unreadable)
    files = _flat(universe)
    state = _Watching(files, snapshot(root, files), "")
    print(_watch_banner(len(state.seen.mtimes), args.interval, args.cycles), flush=True)
    try:
        for _ in _watch_cycles(args.cycles):
            state = _watch_cycle(root, cfg, state, args.interval)
    except KeyboardInterrupt:
        pass
    return 0
