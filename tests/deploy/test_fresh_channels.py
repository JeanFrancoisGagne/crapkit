"""The other ways onto a machine: uvx, pip with the Python extra into a pinned
venv, pip --user, pip from git, pip from a clone, an sdist build.

Each cell installs through its channel with the line the docs print (or, for
the channels the docs name no line for, pip's own flag on the README line),
then runs what that user runs next and asserts the line they act on: the PATH
warning pip prints, the plugin that cannot start crapkit, the fix doctor
names, the version a git install reports.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from kit import docsnip, gitmirror, installers, repos
from kit.cells import cell
from kit.installers import README, NOT_PYTHON, readme_start, said

PACKET = "deploy-channels"
WINDOWS = os.name == "nt"


# --- uvx on a repo that is not Python ---------------------------------------------------

CC_ONLY = "no coverage parser reads this repo's languages, so every scope is cc-only"


def _polyglot(box, templates, name: str, env: dict | None = None) -> dict[str, object]:
    """'A repo that is not Python', its three uvx lines on Go, Rust and shell."""
    repo = repos.checkout(box, "go-rust-shell", cache=templates, repo_name=name)
    steps = {line: box.script(line, cwd=repo, env=env, expect=0)
             for line in installers.fence_commands(README, NOT_PYTHON)}
    python = box.run([*installers.uvx_prefix()[:1], "--from", "crapkit", "python", "-c",
                      "import sys; print(sys.version.split()[0])"], env=env, expect=0).stdout.strip()
    box.transcript.note(f"{name}: uvx ran crapkit on Python {python}")
    return {"repo": repo, "python": python, **steps}


@cell("lin-uvx-polyglot-start", channel="uvx", harness="none",
      scenario="fresh: 'A repo that is not Python' on Go+Rust+shell; once with uv's own interpreter (recorded), "
               "once UV_PYTHON=3.14", use_cases="uvx start, cc-only coverage", os="linux", image="core",
      cadence="push")
def test_uvx_scores_a_go_rust_shell_repo_on_any_interpreter_uv_picks(box, templates, candidate):
    own = _polyglot(box, templates, "polyglot")
    pinned = _polyglot(box, templates, "polyglot-314", env={"UV_PYTHON": "3.14"})
    init, coverage, worklist = installers.fence_commands(README, NOT_PYTHON)

    for run in (own, pinned):
        assert CC_ONLY in said(run[init]) and "coverage_optional = true" in (run["repo"] / "crapkit.toml").read_text(
            encoding="utf-8")
        assert re.search(r": (\d+) functions scored: \1 cc-only, 0 over ceiling 6", said(run[coverage]))
        assert {"go/main.go", "rust/src/lib.rs"} <= set(re.findall(r"(\S+\.\w+):\d+", said(run[worklist])))
    assert pinned["python"].startswith("3.14")
    assert candidate.version in said(installers.uvx(box).steps[0])


@cell("lin-uvx-polyglot-start", channel="uvx", harness="none",
      scenario="fresh: the next command init and coverage print under uvx runs as printed", use_cases="uvx start",
      os="linux", image="core", cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-4: under uvx, init and coverage print "
                                       "`crapkit coverage` and `crapkit worklist` as the next command, and no "
                                       "PATH entry holds `crapkit`")
def test_the_next_command_uvx_prints_runs_as_printed(box, templates):
    repo = repos.checkout(box, "go-rust-shell", cache=templates)
    init = box.script(installers.fence_commands(README, NOT_PYTHON)[0], cwd=repo, expect=0)
    printed = re.search(r"next: run `([^`]+)`", said(init))[1]

    box.script(printed, cwd=repo, expect=0)


# --- the Python extra into a venv that pins older coverage --------------------------------

# pytest-cov 6 needs coverage 7.5 or newer, so a venv on coverage 7.4 holds pytest-cov 5.
PINS = 'coverage==7.4.*\npytest-cov>=5,<6\npytest\n'


def _version(box, package: str) -> tuple[int, ...]:
    shown = box.run(["python", "-m", "pip", "show", package], expect=0).stdout
    return tuple(int(part) for part in re.search(r"^Version: (.+)$", shown, re.M)[1].split("."))


def _pinned_venv(box, templates) -> tuple[Path, object]:
    """A repo whose test venv pins coverage 7.4, then the README's
    `pip install "crapkit[py]"` into it: the step that line prints."""
    repo = repos.checkout(box, "py-pytest", cache=templates)
    (repo / "requirements-dev.txt").write_text(PINS, encoding="utf-8")
    installers.pip_venv(box, "3.12", line="pip install -q -r requirements-dev.txt", channel="the repo's pins",
                        cwd=repo)
    extra = box.script(installers.inline(README, "Install", 'pip install "crapkit[py]"'), cwd=repo, expect=0)
    return repo, extra


def _resynced(box, repo: Path) -> object:
    """The repo's own pins installed again, as its next `pip install -r` does."""
    resync = box.run(["python", "-m", "pip", "install", "-r", "requirements-dev.txt"], cwd=repo, expect=0)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    installers.allow_containers_here(repo)
    return resync


@cell("lin-pyextra-conflict", channel="pip [py] into a venv pinning coverage 7.4 + pytest-cov 5", harness="none",
      scenario="fresh: what pip does, what coverage says", use_cases="install", os="linux", image="core",
      cadence="nightly")
def test_the_python_extra_over_a_venv_that_pins_coverage_74(box, templates):
    repo, extra = _pinned_venv(box, templates)
    upgraded = _version(box, "coverage")
    resync = _resynced(box, repo)
    coverage = box.run(["crapkit", "coverage"], cwd=repo, expect=5)

    assert "Successfully uninstalled coverage-7.4" in said(extra) and upgraded >= (7, 10, 6)
    assert "Successfully installed coverage-7.4" in said(resync) and "crapkit" not in said(resync)
    assert "has no function regions for any of its 3 file(s) — needs coverage >= 7.6" in said(coverage)


@cell("lin-pyextra-conflict", channel="pip [py] into a venv pinning coverage 7.4 + pytest-cov 5", harness="none",
      scenario="fresh: doctor names the coverage.py floor before coverage refuses", use_cases="doctor",
      os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-6: with coverage 7.4 in the lane's venv, "
                                       "`crapkit doctor` prints 'no problems found' and `crapkit coverage` then "
                                       "exits 5 with 'needs coverage >= 7.6'")
def test_doctor_names_the_coverage_floor_before_coverage_refuses(box, templates):
    repo, _ = _pinned_venv(box, templates)
    _resynced(box, repo)
    doctor = box.run(["crapkit", "doctor"], cwd=repo)
    box.run(["crapkit", "coverage"], cwd=repo, expect=5)

    assert "7.6" in doctor.stdout and "no problems found" not in doctor.stdout


# --- pip --user: a launcher on no PATH -------------------------------------------------------

def _user_install(box, candidate):
    """pip --user on an interpreter with no PEP 668 marker: the launcher lands in
    the user scripts directory and pip warns it is not on PATH."""
    python = installers.markerless_python(box, "3.12")
    install = installers.pip_user(box, python)
    assert install.steps[0].exit == 0
    assert f"installed in '{install.path_entry}' which is not on PATH" in said(install.steps[0])
    assert box.which("crapkit") is None
    return install


def _claude_plugin(box, candidate) -> None:
    """The README's two plugin lines, verbatim, against the mirror at the candidate."""
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    for directory in reversed(box.toolchain["harness_bin"]):
        box.prepend_path(directory)
    for line in installers.fence_commands(README, "The Claude Code plugin"):
        box.script(line, cwd=box.root, expect=0)


@cell("lin-pipuser-path", channel="pip --user", harness="Claude plugin + shell",
      scenario="fresh on a Python with no PEP 668 marker: PATH warning, plugin fails, doctor --plugin-root names "
               "the fix, then Connected; on Debian python the refusal", use_cases="install, doctor --plugin-root",
      os="linux", image="core", cadence="nightly")
def test_a_user_site_launcher_off_path_fails_the_plugin_until_path_names_it(box, candidate):
    debian = box.run([box.toolchain["system_python"], "-m", *installers.readme_install().split(), "--user"])
    install = _user_install(box, candidate)
    _claude_plugin(box, candidate)
    failed = box.run(["claude", "mcp", "list"], expect=0)
    doctor = install.run(box, box.root, "doctor", "--plugin-root", expect=1)
    box.prepend_path(install.path_entry)
    connected = box.run(["claude", "mcp", "list"], expect=0)
    agreed = box.run(["crapkit", "doctor", "--plugin-root"], expect=0)

    assert debian.exit == 1 and "externally-managed-environment" in said(debian)
    assert "plugin:crapkit:crapkit: crapkit mcp - ✘ Failed to connect" in failed.stdout
    assert "FAIL no `crapkit` on PATH" in doctor.stdout and "pipx install crapkit" in doctor.stdout
    assert "plugin:crapkit:crapkit: crapkit mcp - ✔ Connected" in connected.stdout
    assert "crapkit doctor: checking" in agreed.stdout and "FAIL" not in agreed.stdout


@cell("win-pipuser-path", channel="pip --user", harness="shell", scenario="fresh: Scripts dir not on PATH",
      use_cases="install", os="windows", image=None, cadence="nightly")
def test_a_user_site_launcher_is_not_on_the_windows_path(box, candidate):
    install = _user_install(box, candidate)
    missing = box.script("crapkit --version", cwd=box.root)
    box.prepend_path(install.path_entry)
    found = box.script("crapkit --version", cwd=box.root, expect=0)

    assert install.path_entry.name == "Scripts" and install.launcher.name == "crapkit.exe"
    assert missing.exit != 0 and "'crapkit' is not recognized" in said(missing)
    assert f"crapkit {candidate.version}" in said(found)


USER_SITE_DOCS = ("README.md", "docs/upgrading.md")


def _names_user_site(page: str) -> bool:
    text = (docsnip.root() / page).read_text(encoding="utf-8")
    return "--user" in text and ("not on PATH" in text or "on your PATH" in text)


@cell("lin-pipuser-path", channel="pip --user", harness="shell",
      scenario="fresh: the docs say where pip --user puts crapkit and what to add to PATH", use_cases="install",
      os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-5: neither README nor docs/upgrading.md names "
                                       "pip --user, where it puts the crapkit launcher, or the PATH entry it needs")
def test_the_docs_name_the_user_site_launcher_and_its_path(box):
    assert [page for page in USER_SITE_DOCS if _names_user_site(page)]


@cell("win-pipuser-path", channel="pip --user", harness="shell",
      scenario="fresh: the docs say where pip --user puts crapkit.exe and what to add to PATH", use_cases="install",
      os="windows", image=None, cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-5: neither README nor docs/upgrading.md names "
                                       "pip --user, where it puts the crapkit launcher, or the PATH entry it needs")
def test_the_docs_name_the_user_site_scripts_dir_on_windows(box):
    assert [page for page in USER_SITE_DOCS if _names_user_site(page)]


# --- from git, from a clone, from an sdist ------------------------------------------------------

def _published(box, candidate) -> gitmirror.Mirror:
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    return mirror


@cell("lin-pipgit-mirror", channel="pip git (README URL via mirror)", harness="none",
      scenario="fresh: verbatim URL; --version; init", use_cases="install", os="linux", image="core",
      cadence="nightly")
def test_the_readme_git_url_installs_main(box, templates, candidate):
    _published(box, candidate)
    install = installers.pip_git(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    init = install.run(box, repo, "init")

    assert install.steps[0].argv and "git+https://github.com/JeanFrancoisGagne/crapkit.git" in install.steps[0].note
    assert f"crapkit {candidate.version}" in install.run(box, repo, "--version").stdout
    assert said(init).startswith("wrote crapkit.toml with 1 scope(s): calc")


def _clone(box) -> Path:
    """The README's git URL, cloned the way a contributor clones it."""
    url = installers.install_lines()[0].split("git+", 1)[1]
    clone = box.root / "crapkit"
    box.run(["git", "clone", "-q", url, str(clone)], expect=0)
    return clone


PROBE = "src/crapkit/_deploy_probe.py"


def _hooked_commit(box, clone: Path, body: str, expect: int):
    (clone / PROBE).write_text(body, encoding="utf-8")
    box.run(["git", "add", PROBE], cwd=clone, expect=0)
    return box.run(["git", "commit", "-q", "-m", "probe"], cwd=clone, env=box.commit_env(), expect=expect)


BREACH = '''def breach(a, b, c, d):
    if a and b:
        return 1
    if b or c:
        return 2
    if c and d:
        return 3
    return 4 if a else 5
'''


@cell("lin-local-checkout", channel="pip . and -e .[dev]", harness="none",
      scenario="fresh: clone install; crapkit's own git-hooks/pre-commit", use_cases="install",
      os="linux", image="core", cadence="nightly")
def test_a_clone_installs_with_pip_dot_and_arms_its_own_gate(box, candidate):
    _published(box, candidate)
    clone = _clone(box)
    local = installers.pip_local(box, clone)
    version = local.run(box, clone, "--version").stdout
    installers.editable(box, clone)
    box.script(installers.development_lines()[1], cwd=clone, expect=0)
    refused = _hooked_commit(box, clone, BREACH, expect=1)
    accepted = _hooked_commit(box, clone, "def fine(a):\n    return a\n", expect=0)

    assert f"crapkit {candidate.version}" in version
    assert "crapkit gate: 1 staged function(s) exceed" in said(refused)
    assert "crapkit: commit blocked by the complexity gate." in said(refused)
    assert accepted.exit == 0


@cell("lin-sdist", channel="pip --no-binary", harness="none", scenario="fresh: sdist install then start",
      use_cases="install", os="linux", image="core", cadence="nightly")
def test_an_sdist_build_runs_the_readme_start(box, templates, candidate):
    install = installers.sdist(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)

    assert f"crapkit-{candidate.version}.tar.gz" in said(install.steps[0])
    assert f"crapkit {candidate.version}" in install.run(box, repo, "--version").stdout
    assert readme_start(box, repo)


@cell("lin-online-pipgit", channel="GitHub git+https", harness="none", scenario="fresh: README URL verbatim",
      use_cases="install", os="linux", image="core", cadence="weekly+published", online=True)
def test_the_readme_git_url_installs_from_github(box, templates):
    box.env.update(installers.online_env(box))
    install = installers.pip_git(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)

    assert re.match(r"crapkit \d+\.\d+\.\d+", install.run(box, repo, "--version").stdout)
    assert said(install.run(box, repo, "init")).startswith("wrote crapkit.toml")
