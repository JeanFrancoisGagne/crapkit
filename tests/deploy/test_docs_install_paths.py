"""The install and upgrade lines the docs print, run the way a reader runs them.

Each cell takes its commands from the stamped docs through docsnip, so a fence
that moves or a line that stops working fails here, naming the page. The
checks are what the reader sees: the refusal pip prints, the PATH warning an
installer prints, the version `crapkit --version` answers after the step.

Cells of the deploy-docs packet: the doc half of lin-sys-python-start,
lin-pipuser-path, lin-up-pipx-0.4.15, lin-up-uvx-n1 and lin-downgrade-0.7.6.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from kit import docsnip, repos, wheels
from kit.cells import cell

PACKET = "deploy-docs"
WINDOWS = os.name == "nt"
REFUSAL = "externally-managed-environment"
FALLBACKS = ("pipx", "uv tool", "venv")


def scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def user_bin(box) -> Path:
    """Where pipx and uv tool put a launcher: ~/.local/bin on every OS."""
    return box.home / ".local" / "bin"


def names_dir(text: str, directory: Path) -> bool:
    """Whether the installer's message names `directory`, in any spelling of it
    (uv prints `$XDG_DATA_HOME/../bin`)."""
    tokens = re.split(r"[\s`'\"]+", text)
    return any(token and os.path.normpath(token) == os.path.normpath(directory) for token in tokens)


# A login shell sets SHELL; uv reads it to decide whether to print `uv tool update-shell`.
LOGIN_SHELL = {} if WINDOWS else {"SHELL": "/bin/bash"}


def version_of(box, cwd=None) -> str:
    return box.run(["crapkit", "--version"], cwd=cwd, expect=0).stdout.strip()


# --- when pip refuses ------------------------------------------------------------

def refusal_fence() -> docsnip.Fence:
    return docsnip.fence("README.md", "When pip refuses", contains="$ pip install crapkit")


@cell("docs-pip-refused", channel="system pip (Debian python3, EXTERNALLY-MANAGED)", harness="none",
      scenario="fresh: README `pip install crapkit` verbatim on Debian's python3; the refusal is the line the "
               "README's 'When pip refuses' prints",
      use_cases="install", os="linux", image="core", cadence="push")
def test_the_readme_install_line_is_refused_the_way_the_readme_says(box):
    install = docsnip.commands(docsnip.fence("README.md", "Install"))[0]
    (command, printed), = docsnip.outputs(refusal_fence())

    refused = box.script(install, expect=1)

    assert command == install, "the refusal transcript runs the Install line"
    assert printed.splitlines()[0] in refused.stderr + refused.stdout


def fallback_block(channel: str) -> docsnip.Fence:
    """The fence under 'When pip refuses' that holds this channel's lines."""
    if channel != "venv":
        return docsnip.fence("README.md", "When pip refuses", contains=f"{channel} install crapkit")
    marker = "Scripts\\activate" if WINDOWS else "bin/activate"
    return docsnip.fence("README.md", "When pip refuses", contains=marker)


def run_fallback(box, channel: str, cwd: Path):
    """Run the channel's lines as the reader pastes them into their shell."""
    block = fallback_block(channel)
    if channel != "venv":
        line = next(line for line in docsnip.commands(block) if line.startswith(channel))
        return box.script(line, cwd=cwd, env=LOGIN_SHELL, expect=0)
    if WINDOWS:
        return box.run(["cmd.exe", "/d", "/q"], cwd=cwd, input=block.text + "\ncrapkit --version\n", expect=0)
    return box.script(block.text + "\ncrapkit --version\n", cwd=cwd, expect=0)


@cell("docs-pip-fallback", channel="pipx, uv tool, venv: the README's fallback when pip refuses", harness="none",
      scenario="fresh: each fallback line under 'When pip refuses' installs the candidate and puts a crapkit "
               "command where the README says",
      use_cases="install", os=("linux", "windows"), image="core", cadence="push")
@pytest.mark.parametrize("channel", FALLBACKS)
def test_each_fallback_the_readme_prints_installs_a_working_crapkit(box, templates, candidate, channel):
    repo = repos.checkout(box, "py-pytest", cache=templates)

    installed = run_fallback(box, channel, repo)

    if channel == "venv":
        assert candidate.version in installed.stdout
        return
    said = installed.stdout + installed.stderr
    assert names_dir(said, user_bin(box)), "the installer names where the launcher went"
    assert ("ensurepath" if channel == "pipx" else "update-shell") in said, "and the command that puts it on PATH"
    box.prepend_path(user_bin(box))
    assert candidate.version in version_of(box, cwd=repo)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
