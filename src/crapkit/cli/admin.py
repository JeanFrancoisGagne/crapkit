"""The setup and upkeep commands: `init` (sniff the repo, write a starter
crapkit.toml and extend .gitignore), `doctor` (does crapkit.toml still describe
this repo: keys, scopes, lanes, tools, unmeasured directories, plus the --json
report and the --tune knob advice) and `watch` (poll tracked files and rescore
what moved)."""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePath

from .. import __version__, config
from ..config import load_config_text, shell_words
from ..doctor import Finding
from ..errors import ConfigError, ToolError
from ..gitio import _common_dir, _git_dir, ls_files
from ..store import SnapshotStore
from ..universe import assign_files, scan_files
from ._shared import _file_sizer, _load_repo_config, _print_json


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
    import shutil

    for name in ("python", "python3", "py"):
        if shutil.which(name):
            return name
    return "python3"


def _interpreter(root: Path) -> str:
    """The python invocation a committed config can call.

    A lockfile at the root wins: `uv run python` and its siblings resolve to the
    environment the repo pins, while a bare `python` resolves to whichever venv
    the shell happens to have active — which in two worktrees of one branch is
    how a lane measures the OTHER checkout and scores this one untested.

    The manager only PREFIXES the name; which name it prefixes is still
    `_python_name`'s answer, so a Windows PATH carrying no `python` gets
    `uv run py` rather than a word the shell cannot start.
    """
    from ..scaffold import lockfile_runner

    runner = lockfile_runner(_present_lockfiles(root))
    name = _python_name()
    return f"{runner} {name}" if runner else name


def _present_markers(root: Path) -> frozenset[str]:
    from ..scaffold import PYTEST_MARKERS

    return frozenset(name for name in PYTEST_MARKERS if (root / name).is_file())


def _package_json(root: Path) -> str:
    path = root / "package.json"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


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
                f"{', '.join(lane.name for lane in lanes)} — next: run `crapkit coverage`")
    if all(cc_only_scope(languages) for languages in scopes.values()):
        return ("no coverage parser reads this repo's languages, so every scope is "
                "cc-only (coverage_optional = true) and needs no lane — next: run "
                "`crapkit coverage`")
    return ("next: declare a [[lane]] per coverage command (see the commented template), "
            "then run `crapkit coverage`")


def _print_init_summary(scopes: dict, lanes: tuple) -> None:
    print(f"wrote crapkit.toml with {len(scopes)} scope(s): {', '.join(scopes)}")
    print(_next_step(scopes, lanes))


def _no_scopes_reason(root: Path) -> str:
    """Why init found nothing to scope.

    crapkit reads `git ls-files`, so source nobody added is source it cannot see
    — and that is the common case on the first command a new user runs. Blaming
    the directory sends them to look in the one place that is already right.
    """
    from ..gitio import untracked_files
    from ..scaffold import source_candidates

    untracked = source_candidates(untracked_files(root))
    if not untracked:
        return "no source files found to scope — is this the repo root?"
    return ("no tracked source files to scope — crapkit scores git-tracked files only; "
            f"run `git add` first ({len(untracked)} untracked source file(s) found)")


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
def _start_probe(word: str) -> int | None:
    """The shell's exit code for this one word, or None when the question could
    not be put at all. `--version` and not the bare word: the probe must not do
    the lane's work by accident, and a lane starting with `pytest` would run
    the suite. A runner that rejects the flag still started, which is all this
    asks; only the shell's own could-not-run code answers no.

    Memoized on the word, which is the whole question: no cwd, no env, so two
    lanes starting with `pnpm` cannot get different answers. A repo with N
    lanes over K distinct first words spawned N shells to learn K things —
    openclaw declares 14 lanes over 2 words, and doctor spent 5.6 of its 6.9
    seconds waiting on the 12 duplicates. None is cached on purpose: it is the
    answer for OSError and for the 15 s deadline alike, and caching it turns a
    hung runner from 14 timeouts into 1. Whoever gives the probe a cwd or an
    env has to put it in the key in the same commit."""
    from ..procs import run_bounded

    try:
        return run_bounded(f"{_shell_quote(word)} --version", _PROBE_TIMEOUT_SECONDS)
    except OSError:
        return None


def _first_word(command: str) -> str:
    """The word the shell will try to start, read the way that shell reads the
    line: a quoted interpreter path stays one word, where a whitespace split
    would break it at its space. "" when the line holds no word at all."""
    words = shell_words(command)
    return words[0] if words else ""


def _dead_first_word(command: str) -> tuple[str, int] | None:
    """The command's first word and the shell's verdict, when the shell cannot
    start it. None when it starts, and None when the word does not resolve on
    PATH at all: that one is already its own finding, and running nothing
    proves nothing."""
    import shutil

    word = _first_word(command)
    if not word or shutil.which(word) is None:
        return None
    code = _start_probe(word)
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


# The names a python answers to. `-c "import pytest_cov"` is a python's flag
# and nobody else's: `coverage -c` takes a config file and rejects the code.
_PYTHON_NAME = re.compile(r"py(thon3?(\.\d+)?)?(\.exe)?$", re.IGNORECASE)


def _is_python(word: str) -> bool:
    """Does this word name a python interpreter? A bare name or a path, with or
    without a version suffix: `python`, `python3`, `py`, `python3.12`,
    `C:/Program Files/Python311/python.exe`."""
    return _PYTHON_NAME.fullmatch(PurePath(word).name) is not None


def _pytest_segment(command: str) -> list[str]:
    """The command on the line that runs pytest, or nothing when none does. A
    lane chains steps (`coverage run -m pytest --cov=pylib && coverage json`),
    and only the one holding pytest says anything about pytest-cov."""
    for segment in config.shell_segments(command):
        if any(tok.endswith("pytest") for tok in segment):
            return segment
    return []


def _probe_interpreter(command: str) -> str | None:
    """The python this lane runs pytest with, or None when no python runs it.
    `coverage run -m pytest` names no interpreter at all: the probe asked
    `coverage` to import pytest_cov, read its argument error as a missing
    package, and printed the pip note on a machine where pytest_cov imports.

    An environment manager heads its segment for the same reason: `uv run` and
    its siblings CREATE or sync the project environment before running anything,
    so init has no business provisioning one to ask a question about it, and the
    head word is not a python — `uv -c "import pytest_cov"` is not the probe it
    looks like, and would have warned about the wrong gap on every uv repo.
    """
    segment = _pytest_segment(command)
    if not segment or not _is_python(segment[0]):
        return None
    return segment[0]


def _pytest_cov_probe(command: str) -> bool:
    """Can the interpreter this lane names import pytest_cov? The probe runs
    through the same shell as the lane, so a bare `python` resolves to the one
    the lane will get (a .bat shim included), not the one CreateProcess finds.
    True too when the probe cannot run — only a clean "no" earns the warning,
    and a missing interpreter is doctor's finding, not this one's. True as well
    when no python runs the suite, `uv run python -m pytest` included: nothing
    here can be asked."""
    import shutil

    from ..procs import run_bounded

    word = _probe_interpreter(command)
    if word is None or shutil.which(word) is None:
        return True
    probe = f'{_shell_quote(word)} -c "import pytest_cov"'
    try:
        # run_bounded: the interpreter is the shell's child, and run()'s timeout
        # kills the shell alone. The 15 bounded nothing and left one interpreter
        # running per timeout. None here is that deadline, and it is not an
        # answer about pytest_cov.
        code = run_bounded(probe, _PROBE_TIMEOUT_SECONDS)
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
            f"(exit {code}) — {fix}, then `crapkit coverage`")


def _missing_pytest_cov_note(name: str) -> str:
    return (f"note: lane {name!r} runs `pytest --cov`, and this python cannot "
            "import pytest_cov — pip install pytest-cov where the suite runs "
            # Double quotes, not single: cmd.exe passes ' through as an
            # ordinary character and pip rejects the requirement. Double
            # quotes are the one form cmd, PowerShell, bash and zsh share,
            # and the bare form still breaks zsh's globbing.
            '(pip install "crapkit[py]" when that is crapkit\'s own environment), '
            "then `crapkit coverage`")


def _absent_manager(command: str) -> str | None:
    """The environment manager heading this lane, when this machine's PATH
    carries no such word. None when it resolves, and None when nothing manages
    the lane at all.

    A lockfile is a property of the REPO, so `init` writes `uv run python` off
    its presence alone — right for the repo, and unrunnable on a checkout whose
    owner installed the dependencies with pip. Nothing else catches it: the
    start check skips a word that does not resolve, and the pytest-cov probe
    refuses to provision an environment to ask a question about it.
    """
    import shutil

    from ..scaffold import LOCKFILE_RUNNERS

    managers = {runner.split()[0] for _, runner in LOCKFILE_RUNNERS}
    head = _first_word(command)
    return head if head in managers and shutil.which(head) is None else None


def _missing_manager_note(name: str, manager: str) -> str:
    return (f"note: lane {name!r} runs through `{manager}`, which this machine's PATH does "
            f"not carry — install {manager}, or point the lane's command in crapkit.toml at "
            f"an interpreter that resolves here, then `crapkit coverage`")


def _lane_first_run_note(lane) -> str | None:
    """What init owes this lane before the first `crapkit coverage`, or None
    when the lane will run. Three different gaps, and they are not the same
    sentence: a manager that is not installed never gets as far as a python, and
    an interpreter that never started answered nothing about pytest_cov, so
    `pip install pytest-cov` fixes neither."""
    manager = _absent_manager(lane.command)
    if manager:
        return _missing_manager_note(lane.name, manager)
    dead = _dead_first_word(lane.command)
    if dead:
        return _dead_interpreter_note(lane.name, *dead)
    if not _pytest_cov_probe(lane.command):
        return _missing_pytest_cov_note(lane.name)
    return None


def _probed_lanes(lanes: tuple) -> list:
    """Only a coveragepy lane running `pytest --cov` has anything to probe."""
    return [lane for lane in lanes
            if lane.parser == "coveragepy" and "--cov" in lane.command]


def _warn_missing_pytest_cov(lanes: tuple) -> None:
    """The first-run trap, caught where it starts. The py lane shells out to
    `pytest --cov`, and the --cov flags come from pytest-cov — a package of the
    REPO's interpreter, so a crapkit dependency could only ever cover installs
    sharing the suite's venv. Probe the python the lane will actually run and
    say the fix now, instead of `coverage` exiting 5 with a lane log the first
    run has to decode."""
    for lane in _probed_lanes(lanes):
        note = _lane_first_run_note(lane)
        if note:
            print(note, file=sys.stderr)


def _extend_gitignore(root: Path, lanes: tuple) -> None:
    """Ignore what adopting crapkit will write: the store, and each lane's
    artifact. Without this the consumer's next `git status` is a wall of
    untracked coverage output nobody asked for."""
    from ..scaffold import gitignore_update

    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    text, added = gitignore_update(current, lanes)
    if not added:
        return
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"added to .gitignore: {', '.join(added)}")


def cmd_init(args: argparse.Namespace) -> int:
    from ..scaffold import detect_lanes, live_lanes, sniff_scopes, starter_toml

    root = Path(args.repo).resolve()
    toml_path = root / "crapkit.toml"
    if toml_path.is_file():
        raise ConfigError(f"crapkit.toml already exists in {root} — edit it instead")
    scopes = sniff_scopes(ls_files(root))
    if not scopes:
        raise ConfigError(_no_scopes_reason(root))
    # A config whose lanes are all commented out scores every function no-lane,
    # so a fresh repo cannot rank anything until somebody hand-writes a lane.
    # The interpreter goes to both: a repo with no pytest marker file gets no
    # lane to read it back off, and its commented template is what the reader
    # uncomments.
    interpreter = _interpreter(root)
    lanes = detect_lanes(_present_markers(root), _package_json(root), interpreter=interpreter)
    text = starter_toml(scopes, lanes, interpreter=interpreter)
    load_config_text(text)  # self-check: never write a config crapkit cannot read back
    toml_path.write_text(text, encoding="utf-8", newline="\n")
    _print_init_summary(scopes, lanes)
    _warn_missing_pytest_cov(live_lanes(lanes, scopes))
    _extend_gitignore(root, live_lanes(lanes, scopes))
    return 0


def _unknown_key_text(unknown) -> str:
    """Name the key AND the spellings its table accepts. A bare rejection makes
    the reader hunt for a key list the tool never printed anywhere."""
    from ..doctor import table_label, valid_keys

    noun = "keys" if unknown.table else "tables"
    return (f"unknown key {unknown.path} — crapkit ignores it (typo?); "
            f"{table_label(unknown.table)} accepts these {noun}: "
            f"{', '.join(valid_keys(unknown.table))}")


def _doctor_keys(raw: dict) -> list[Finding]:
    from ..doctor import unknown_key_findings

    problems = [Finding("FAIL", _unknown_key_text(u)) for u in unknown_key_findings(raw)]
    return problems or [Finding("ok", "config keys all recognized")]


def _listed_files(files: list[str], show: bool) -> list[Finding]:
    return [Finding("", f"       {f}") for f in files] if show else []


def _doctor_scope_files(files_by_scope: dict, cfg, show_files: bool) -> list[Finding]:
    out: list[Finding] = []
    for scope in cfg.scopes:
        files = files_by_scope.get(scope.name, [])
        out.append(Finding("ok" if files else "FAIL",
                           f"scope {scope.name!r}: {len(files)} files"))
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
                            f"no scope path: {', '.join(unclaimed)} — add a [[scope]] "
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
    return [Finding("FAIL", f"scope {name!r} is in no lane's scopes list — its functions "
                            "can only score no-lane (declare a lane, or "
                            "coverage_optional = true)")
            for name in _uncovered_scopes(cfg)]


def _doctor_oversized(oversized: tuple[tuple[str, int], ...]) -> list[Finding]:
    """Reported, never a failure: skipping the blob is what max_file_bytes asked for."""
    return [Finding("note", f"{path} ({size} bytes) skipped: over max_file_bytes")
            for path, size in oversized]


def _doctor_scopes(root: Path, cfg, files: list[str], show_files: bool) -> list[Finding]:
    universe = scan_files(files, cfg, size_of=_file_sizer(root))
    return (_doctor_scope_files(universe.by_scope, cfg, show_files)
            + _doctor_unclaimed(universe.unclaimed)
            + _doctor_uncovered(cfg)
            + _doctor_oversized(universe.oversized))


def _lane_problem(root: Path, lane) -> str | None:
    if lane.cwd and not (root / lane.cwd).is_dir():
        return f"lane {lane.name!r}: cwd {lane.cwd!r} does not exist"
    return None


_SCRIPT_SUFFIXES = (".py", ".mjs", ".js", ".ts", ".ps1", ".sh")


def _missing_named_script(cwd: Path, tok: str) -> bool:
    if not tok.endswith(_SCRIPT_SUFFIXES) or tok.startswith("-") or "=" in tok:
        return False
    return not (cwd / tok).is_file()


def _segment_problems(name: str, cwd: Path, tokens: list[str]) -> list[str]:
    """One command's argv: its runner, then the files it names. Nothing is
    executed."""
    import shutil

    if not tokens:
        return []
    runner = ([f"lane {name!r}: executable {tokens[0]!r} does not resolve on PATH"]
              if shutil.which(tokens[0]) is None else [])
    return runner + [f"lane {name!r}: command names {tok!r}, which does not exist"
                     for tok in tokens[1:] if _missing_named_script(cwd, tok)]


def _lane_command_problems(root: Path, lane) -> list[str]:
    """Config rot a lane would only reveal 40 minutes in: a runner that no
    longer resolves, a named script that left the repo.

    Read by the shell that will run the lane, one segment at a time. A
    whitespace split answered three questions wrong: it broke a quoted
    interpreter path at its space (`'"C:/Program'` resolves nowhere), it never
    looked past the first word, so a dead runner after `&&` passed doctor, and
    it read a quoted `-k "tests/gone.py or x"` as a test file the repo owes."""
    cwd = root / lane.cwd if lane.cwd else root
    return [problem for segment in config.shell_segments(lane.command)
            for problem in _segment_problems(lane.name, cwd, segment)]


def _lane_start_problem(lane) -> str | None:
    """The rot which() cannot see: a first word that resolves and then will not
    run. %LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe is the case — a stock
    Windows PATH carries that stub with no Store app behind it, which() finds
    it, and doctor cleared a repo whose only lane exits 9009 while `coverage`
    exited 5 on the same command. This one does start the runner, with
    --version, which is why it is not part of _lane_command_problems."""
    dead = _dead_first_word(lane.command)
    if not dead:
        return None
    word, code = dead
    return (f"lane {lane.name!r}: {_shell_label()} cannot run {word!r} (exit {code}) — "
            "the lane cannot start, so its scopes can only ever score no-lane")


def _doctor_lane_summary(cfg) -> Finding:
    """No lanes is a gap in most repos and the finished state in a cc-only one,
    where every scope declares coverage_optional and `coverage` runs anyway."""
    if cfg.lanes:
        return Finding("ok", f"{len(cfg.lanes)} lane(s) declared")
    if cfg.lane_less_scopes:
        return Finding("note", "no [[lane]] declared — inventory works; coverage needs one")
    return Finding("ok", "no [[lane]] declared: every scope is cc-only, so none is needed")


def _lane_problems(root: Path, cfg) -> list[str]:
    return [p for lane in cfg.lanes
            for p in (_lane_problem(root, lane), *_lane_command_problems(root, lane),
                      _lane_start_problem(lane)) if p]


def _doctor_lanes(root: Path, cfg) -> list[Finding]:
    return ([Finding("FAIL", p) for p in _lane_problems(root, cfg)]
            or [_doctor_lane_summary(cfg)]) + _doctor_results_artifacts(cfg)


_RESULTS_HINT = {
    "coveragepy": ("add --junitxml=.crapkit/cov/junit-{name}.xml to the command and "
                   'results_artifact = ".crapkit/cov/junit-{name}.xml" to the lane'),
    "istanbul": ("add a junit reporter (vitest: --reporter=default --reporter=junit "
                 "--outputFile=.crapkit/cov/{name}/junit.xml; jest: jest-junit) and a "
                 "results_artifact naming its file"),
}


def _doctor_results_artifacts(cfg) -> list[Finding]:
    """WARN, never FAIL: the lane measures coverage exactly as it did. What it
    cannot do without a results file is feed the two checks that read one, the
    crashed-worker trust check and no-new-failures, and until now nothing said
    they were off (#26)."""
    return [Finding("WARN", f"lane {lane.name!r} declares no results_artifact: the "
                            "crashed-worker check and the no-new-failures check (exit 8) "
                            f"cannot run for it; {_RESULTS_HINT[lane.parser].format(name=lane.name)}")
            for lane in cfg.lanes if lane.parser in _RESULTS_HINT and not lane.results_artifact]


def _doctor_artifact_litter(cfg) -> list[Finding]:
    """WARN, never FAIL: a lane writing at the repo root still measures what it
    always did. Failing here would break every consumer that adopted crapkit
    before its lanes wrote under .crapkit/, over tree hygiene."""
    from ..doctor import artifact_litter, scope_top_dirs

    return [Finding("WARN", f"lane {item.lane!r} writes {item.path} at the repo root — "
                            f"point it under .crapkit/ (for example .crapkit/cov/{item.lane}/) "
                            "to keep the tree clean")
            for item in artifact_litter(cfg.lanes, scope_top_dirs(cfg.scopes))]


def _lizard_version() -> str | None:
    try:
        import lizard
    except ImportError:
        return None
    return getattr(lizard, "version", "?")


def _doctor_tools() -> list[Finding]:
    version = _lizard_version()
    if version is None:
        return [Finding("FAIL", "lizard is not importable — pip install lizard")]
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


@dataclass
class _DirCount:
    """One directory's share of a run: how many functions it holds, how many of
    them carry a verdict other than untested, and the file stems to match on."""
    functions: int = 0
    others: int = 0
    stems: set = field(default_factory=set)


def _dirs_from_counts(counts: list[tuple]) -> dict[str, _DirCount]:
    from ..doctor import _dir_of, _stem_of

    dirs: dict[str, _DirCount] = {}
    for path, functions, others in counts:
        entry = dirs.setdefault(_dir_of(path), _DirCount())
        entry.functions += functions
        entry.others += others
        entry.stems.add(_stem_of(path))
    return dirs


def _unmeasured_gaps(counts: list[tuple], tracked: list[str]) -> tuple:
    """doctor.unmeasured_directories, fed per-path counts instead of per-row rows.

    Same rule, same order, same findings: a directory qualifies when nothing in
    it carries a verdict other than untested and a tracked test file names its
    code. The matching itself stays doctor's, so the mirror rule has one copy.
    """
    from ..doctor import UnmeasuredDir, _matching_test, _test_files

    test_files = _test_files(tracked)
    found = []
    for directory, stats in sorted(_dirs_from_counts(counts).items()):
        example = _matching_test(directory, stats.stems, test_files) if not stats.others else None
        if example:
            found.append(UnmeasuredDir(directory, stats.functions, example))
    return tuple(found)


def _doctor_unmeasured(root: Path, cfg, files: list[str]) -> list[Finding]:
    """WARN, never FAIL: a directory whose functions are all untested while its
    tests exist is a lane that runs without measuring the code it covers.

    The store does the grouping. This used to build a hundred thousand
    sixteen-field rows to read three fields off each of them, then filter the
    coverage_optional scopes back out after reading them.
    """
    store = _store_if_any(root)
    run = _newest_coverage_run(store) if store else None
    if run is None:
        return []
    counts = store.count_by_path(run["id"], flag="untested",
                                 skip_scopes=cfg.coverage_optional_scopes)
    return [Finding("WARN", f"{g.directory}: {g.functions} function(s) all flagged untested "
                            f"while {g.example_test} exists — tests exist but no lane "
                            "measures them")
            for g in _unmeasured_gaps(counts, files)]


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
    contributor gate armed nothing."""
    from ..doctor import non_executable_hooks

    return [Finding("WARN", f"{path} is not executable in the index — core.hooksPath "
                            "is set, so Unix clones silently skip it; fix with "
                            f"`git update-index --chmod=+x {path}` and commit")
            for path in non_executable_hooks(_hook_modes(root))]


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
                            "every tree it passes — fix with `git commit-graph write "
                            "--reachable --changed-paths`")]


def _doctor_findings(root: Path, cfg, raw: dict, files: list[str],
                     show_files: bool) -> list[Finding]:
    return (_doctor_keys(raw)
            + _doctor_scopes(root, cfg, files, show_files)
            + _doctor_lanes(root, cfg)
            + _doctor_artifact_litter(cfg)
            + _doctor_hook_modes(root)
            + _doctor_commit_graph(root)
            + _doctor_tools()
            + _doctor_scoped_tests(cfg)
            + _doctor_unmeasured(root, cfg, files))


def _doctor_scoped_tests(cfg) -> list[Finding]:
    from ..doctor import scoped_test_gaps
    return list(scoped_test_gaps(cfg.lanes, cfg.scoped_tests))


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


def _lane_report(root: Path, lane, stamp: dict) -> dict:
    return {"artifact": lane.artifact,
            "artifact_present": (root / lane.artifact).is_file(),
            "commit": stamp.get("commit"),
            "name": lane.name,
            "seconds": stamp.get("seconds")}


def _lane_reports(root: Path, cfg) -> list[dict]:
    from ..lanes import read_stamps

    stamps = read_stamps(root)
    return [_lane_report(root, lane, stamps.get(lane.artifact, {})) for lane in cfg.lanes]


def _doctor_report(root: Path, cfg, findings: list[Finding]) -> dict:
    """Everything a wrapper needs to tell lane rot from a stale artifact without
    parsing prose: versions, store, newest run, per-lane stamps, findings."""
    from ..analyze import ANALYSIS_VERSION

    return {"analysis_version": ANALYSIS_VERSION,
            "lanes": _lane_reports(root, cfg),
            "newest_run": _newest_run_report(_store_if_any(root)),
            "problems": _at_level(findings, "FAIL"),
            "store": _store_report(root),
            "versions": _version_report(),
            "warnings": _at_level(findings, "WARN")}


def _print_findings(findings: list[Finding]) -> None:
    for f in findings:
        print(f"{f.level:<4} {f.text}" if f.level else f.text)
    problems = _at_level(findings, "FAIL")
    print("doctor: no problems found" if not problems else f"doctor: {len(problems)} problem(s)")


def _emit_doctor(root: Path, cfg, findings: list[Finding], as_json: bool) -> None:
    if as_json:
        _print_json(_doctor_report(root, cfg, findings))
        return
    _print_findings(findings)


def _junit_seconds(path: Path) -> float | None:
    from ..junitparse import suite_seconds

    if not path.is_file():
        return None
    try:
        return suite_seconds(path.read_text(encoding="utf-8"))
    except ToolError:
        return None


def _lane_seconds(root: Path, lane, stamps: dict) -> float | None:
    """What this lane costs, best signal first: the duration its own run
    recorded, else the wall time its junit report claims. None means this lane
    has never left a cost signal on disk — which is not the same as costing 0."""
    recorded = stamps.get(lane.artifact, {}).get("seconds")
    if isinstance(recorded, (int, float)):
        return float(recorded)
    return _junit_seconds(root / lane.results_artifact) if lane.results_artifact else None


def _lane_durations(root: Path, cfg) -> tuple[float, ...]:
    from ..lanes import read_stamps

    stamps = read_stamps(root)
    measured = [_lane_seconds(root, lane, stamps) for lane in cfg.lanes]
    return tuple(s for s in measured if s is not None)


def _doctor_tune(root: Path, cfg) -> int:
    """Advisory only: knob lines from this machine's cpu count and whatever lane
    durations are already on disk. Nothing is written and nothing is executed."""
    import os

    from ..doctor import suggest_knobs, tune_lines

    cpus = os.cpu_count() or 1
    knobs = suggest_knobs(cpus=cpus, lanes=len(cfg.lanes))
    for line in tune_lines(cpus=cpus, knobs=knobs, durations=_lane_durations(root, cfg)):
        print(line)
    return 0


def _plugin_json(path: Path):
    """One JSON file off an installed plugin, or None.

    Missing, unreadable and half-written all read the same, because doctor's job
    here is to name the file rather than to raise inside it. A plugin cache is
    written by an installer this process does not control.
    """
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _hook_handlers(hooks: dict) -> list[dict]:
    """Every command handler in a hooks.json, flat across events and matchers."""
    return [handler for event in hooks.get("hooks", {}).values()
            for matcher in event for handler in matcher.get("hooks", [])]


def _named_protocol(handler: dict) -> str | None:
    """The `--protocol` value one handler spawns crapkit with, or None.

    Paired off the arg list rather than indexed past the flag: a handler whose
    args end at `--protocol` is malformed, and reading it must not raise.
    """
    args = handler.get("args", [])
    return dict(zip(args, args[1:])).get("--protocol")


def _hook_protocols(root: Path) -> tuple[str, ...] | None:
    """Every protocol the plugin's hooks name, or None when it ships no hooks
    file. The empty tuple is the third state: a hooks file naming no protocol,
    which argparse defaults to the supported one."""
    hooks = _plugin_json(root / "hooks" / "hooks.json")
    if not isinstance(hooks, dict):
        return None
    named = [_named_protocol(handler) for handler in _hook_handlers(hooks)]
    return tuple(p for p in named if p is not None)


def _manifest_field(root: Path, field: str) -> str | None:
    manifest = _plugin_json(root / ".claude-plugin" / "plugin.json")
    return manifest.get(field) if isinstance(manifest, dict) else None


def _manifest_version(root: Path) -> str | None:
    return _manifest_field(root, "version")


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
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "plugins"


def _recorded_roots(recorded) -> list[Path]:
    """Install directories installed_plugins.json records for crapkit."""
    entries = recorded.get("plugins", {}) if isinstance(recorded, dict) else {}
    return [Path(e["installPath"]) for key, installs in entries.items()
            if key.startswith("crapkit@") for e in installs if e.get("installPath")]


def _installed_crapkit_roots(plugins: Path) -> list[Path]:
    """Every crapkit install under Claude Code's plugin directory: what the
    installer recorded plus what the cache holds, so a stale record and a
    missing record alone cannot hide the plugin."""
    recorded = _recorded_roots(_plugin_json(plugins / "installed_plugins.json"))
    cached = _manifest_roots(plugins)
    return [r for r in dict.fromkeys(recorded + cached)
            if (r / ".claude-plugin" / "plugin.json").is_file()]


def _resolve_plugin_root(arg: str) -> tuple[Path | None, str]:
    """The plugin root to check, and where it was looked for.

    An explicit PATH with no manifest at or under it resolves to itself, so
    the handshake names the missing file at the path the operator typed.
    """
    if arg:
        under = Path(arg)
        return _newest_root(_manifest_roots(under)) or under, str(under)
    plugins = _plugins_dir()
    return _newest_root(_installed_crapkit_roots(plugins)), str(plugins)


def _doctor_plugin(plugin_root: str) -> int:
    """`--plugin-root [PATH]`: the installed plugin against this CLI.

    No repo is read. The plugin cache is not a repo, and an operator asking
    whether their plugin is behind their CLI is rarely standing in one. PATH
    is the plugin root or any directory above it, ~/.claude included; empty
    means Claude Code's own plugin directory (#28).

    `PROTOCOL` comes from the hook module itself, so the number doctor promises
    and the number `claude-hook` accepts cannot drift apart.
    """
    from ..doctor import plugin_handshake
    from .claude_hook import PROTOCOL

    root, looked_in = _resolve_plugin_root(plugin_root)
    if root is None:
        print(f"crapkit doctor: no installed crapkit plugin under {looked_in} (install with "
              "`claude plugin install crapkit@crapkit`, or pass --plugin-root PATH)")
        return 1
    # A root the search found, not one the operator typed: the glob reaches three
    # levels under the named directory, so a source checkout can win over an
    # install. Naming it is how the reader knows which tree the verdict is about.
    if str(root) != looked_in:
        print(f"crapkit doctor: checking {root}")
    lines = plugin_handshake(where=str(root), version=_manifest_version(root),
                             cli_version=__version__, protocols=_hook_protocols(root),
                             supported=PROTOCOL)
    for line in lines:
        print(line)
    return 1 if lines else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import tomllib

    if args.plugin_root is not None:
        return _doctor_plugin(args.plugin_root)
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)  # a config that does not parse already exits 3 here
    if args.tune:
        return _doctor_tune(root, cfg)
    raw = tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8"))
    findings = _doctor_findings(root, cfg, raw, ls_files(root), args.show_files)
    _emit_doctor(root, cfg, findings, args.json)
    return 1 if _at_level(findings, "FAIL") else 0


def _watch_rescore(root: Path, moved: list[str]) -> None:
    import subprocess

    present = [f for f in moved if (root / f).is_file()]
    if not present:
        return
    # flush: watch output exists to be tailed live; a block-buffered pipe sits silent
    print(f"--- changed: {', '.join(moved)}", flush=True)
    # a subprocess so a half-saved syntax error can never kill the watcher
    subprocess.run([sys.executable, "-m", "crapkit", "rescore", *present, "--repo", str(root)])


def _watched_files(root: Path, cfg) -> list[str]:
    """Every tracked file a scope claims, flat — the whole subject of one poll."""
    by_scope = assign_files(ls_files(root), cfg, size_of=_file_sizer(root))
    return [f for files in by_scope.values() for f in files]


def _watch_cycles(cycles: int | None):
    """The poll counter: `cycles` polls, or an endless one when nothing bounds it.

    Unbounded is the default, because a watcher an operator starts is meant to
    outlive the shell it was typed into. A bound is what lets the loop be driven
    to a known end — by a test, or by a caller that wants one sweep and its exit
    code rather than a process to kill.
    """
    from itertools import count

    return count() if cycles is None else range(cycles)


def _watch_banner(watched: int, interval: float, cycles: int | None) -> str:
    """The first line, naming how this run ends. Telling an operator to press
    ctrl-c on a `--cycles 3` run describes a loop that is not the one running."""
    stop = "ctrl-c to stop" if cycles is None else f"{cycles} poll(s) then stop"
    return f"watching {watched} tracked files every {interval}s — {stop}"


def _watch_cycle(root: Path, files: list[str], prev: dict[str, float],
                 interval: float) -> dict[str, float]:
    """One poll: wait, re-stat, rescore whatever moved; the new snapshot out."""
    import time

    from ..watch import changed_paths, snapshot_mtimes

    time.sleep(interval)
    cur = snapshot_mtimes(root, files)
    moved = changed_paths(prev, cur)
    if moved:
        _watch_rescore(root, moved)
    return cur


def cmd_watch(args: argparse.Namespace) -> int:
    from ..watch import snapshot_mtimes

    root = Path(args.repo).resolve()
    files = _watched_files(root, _load_repo_config(root))
    prev = snapshot_mtimes(root, files)
    print(_watch_banner(len(prev), args.interval, args.cycles), flush=True)
    try:
        for _ in _watch_cycles(args.cycles):
            prev = _watch_cycle(root, files, prev, args.interval)
    except KeyboardInterrupt:
        pass
    return 0
