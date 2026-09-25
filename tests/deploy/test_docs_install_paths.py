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

from docs_support import adopt_measured, install_candidate, user_bin, version_of
from kit import docsnip, httpstub, pyindex, repos, wheels
from kit.cells import cell

PACKET = "deploy-docs"
WINDOWS = os.name == "nt"
REFUSAL = "externally-managed-environment"
FALLBACKS = ("pipx", "uv tool", "venv")


def names_dir(text: str, directory: Path) -> bool:
    """Whether the installer's message names `directory`, in any spelling of it
    (uv prints `$XDG_DATA_HOME/../bin`)."""
    tokens = re.split(r"[\s`'\"]+", text)
    return any(token and os.path.normpath(token) == os.path.normpath(directory) for token in tokens)


# A login shell sets SHELL; uv reads it to decide whether to print `uv tool update-shell`.
LOGIN_SHELL = {} if WINDOWS else {"SHELL": "/bin/bash"}


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


# --- the upgrade table in docs/upgrading.md ----------------------------------------

def upgrade_row(installation: str) -> str:
    """The command in docs/upgrading.md's table row for `installation`."""
    page = (docsnip.root() / "docs" / "upgrading.md").read_text(encoding="utf-8")
    rows = [line for line in page.splitlines() if line.startswith(f"| {installation} |")]
    assert len(rows) == 1, f"docs/upgrading.md has {len(rows)} rows for {installation!r}"
    return rows[0].split("|")[2].strip().strip("`")


PIPX_FROM = "0.4.15"


@cell("docs-upgrade-pipx", channel="pipx", harness="none",
      scenario="upgrade: pipx install of 0.4.15, then the pipx row of docs/upgrading.md reaches the candidate",
      use_cases="upgrade guide", os=("linux", "windows"), image="core", cadence="push")
def test_the_pipx_row_upgrades_a_pipx_install(box, candidate):
    assert PIPX_FROM in wheels.releases()
    box.run(["pipx", "install", f"crapkit=={PIPX_FROM}"], expect=0)
    box.prepend_path(user_bin(box))
    assert PIPX_FROM in version_of(box)

    box.script(upgrade_row("pipx"), expect=0)

    assert candidate.version in version_of(box)


# uvx against an index with PyPI's headers: the sandbox's offline switches off,
# the index in their place.
OFFLINE_KEYS = ("UV_OFFLINE", "UV_NO_INDEX", "UV_FIND_LINKS")


def serve_uvx_from(box, index) -> None:
    for key in OFFLINE_KEYS:
        box.env.pop(key, None)
    box.env["UV_DEFAULT_INDEX"] = index.simple


def release(index, dist: Path) -> None:
    """The index starts listing the files in `dist`, as PyPI does after a publish."""
    index.files.update({path.name: path for path in dist.iterdir() if path.name.endswith(pyindex.SUFFIXES)})


def uvx_version(box, *spec: str) -> str:
    return box.run(["uvx", *spec, "--version"], expect=0).stdout.strip()


class _HeadersOnly:
    """A response stream that passes the header block and drops the body."""

    def __init__(self, stream):
        self.stream, self.sent = stream, False

    def write(self, data: bytes) -> int:
        if not self.sent:
            self.stream.write(data)
        self.sent = True
        return len(data)

    def flush(self) -> None:
        self.stream.flush()


def _head(handler) -> None:
    handler.wfile = _HeadersOnly(handler.wfile)
    try:
        handler._serve()
    finally:
        handler.wfile = handler.wfile.stream


def head_without_a_body(monkeypatch) -> None:
    """kit/httpstub.py answers HEAD with a body on a keep-alive connection. uv asks
    HEAD before it fetches a wheel, and its next response on that connection then
    parses as `invalid HTTP version`; one run in 18 failed that way. Until the kit
    sends no body on HEAD, this cell's index does not either."""
    monkeypatch.setattr(httpstub._Handler, "do_HEAD", _head)


@cell("docs-upgrade-uvx", channel="uvx against a PEP 503 index with PyPI's cache headers", harness="none",
      scenario="upgrade: uvx cached N-1, the index publishes the candidate; what plain `uvx crapkit` answers, and "
               "the uvx row of docs/upgrading.md reaches the candidate",
      use_cases="uvx refresh", os=("linux", "windows"), image="core", cadence="push")
def test_the_uvx_row_reaches_a_release_plain_uvx_keeps_cached(box, candidate, monkeypatch):
    head_without_a_body(monkeypatch)
    with pyindex.serve([box.toolchain["wheelhouse"]]) as index:
        serve_uvx_from(box, index)
        assert wheels.n_minus_1() in uvx_version(box, "crapkit")
        release(index, candidate.dist)

        cached = uvx_version(box, "crapkit")
        refreshed = box.script(upgrade_row("uvx"), expect=0).stdout
        after = uvx_version(box, "crapkit")

    box.transcript.note(f"plain uvx after the release: {cached!r}; after the row: {after!r}")
    assert candidate.version in refreshed
    assert wheels.n_minus_1() in cached, "docs/upgrading.md says plain uvx keeps the cached release"
    assert candidate.version in after, "and that the row's refresh is what later plain calls run"


# pip --user: a Python with no EXTERNALLY-MANAGED marker (python.org, the Windows
# installer). The toolchain's uv Pythons carry one, and pip's own switch drops it.
NO_MARKER = {"PIP_BREAK_SYSTEM_PACKAGES": "1"}
USER_SCRIPTS = "import os, sysconfig; print(sysconfig.get_path('scripts', os.name + '_user'))"


@cell("docs-pip-user-path", channel="pip --user on a Python with no PEP 668 marker", harness="none",
      scenario="fresh + upgrade: pip --user names the scripts dir it left off PATH; the pip --user row of "
               "docs/upgrading.md reaches the candidate",
      use_cases="install, upgrade guide", os=("linux", "windows"), image="core", cadence="push")
def test_the_pip_user_row_upgrades_and_pip_names_the_path_to_add(box, candidate):
    python = box.toolchain.python("3.12")
    box.prepend_path(Path(python).parent)
    installed = box.run([python, "-m", "pip", "install", "--user", f"crapkit=={wheels.n_minus_1()}"],
                        env=NO_MARKER, expect=0)
    scripts = Path(box.run([python, "-c", USER_SCRIPTS], expect=0).stdout.strip())

    assert "which is not on PATH" in installed.stderr and names_dir(installed.stderr, scripts)
    box.prepend_path(scripts)
    box.script(upgrade_row("pip --user").replace("python ", f'"{python}" ', 1), env=NO_MARKER, expect=0)
    assert candidate.version in version_of(box)


# --- downgrade -----------------------------------------------------------------------

DOWNGRADE_TO = "0.7.6"
def downgrade_line(installer: str) -> str:
    """The command docs/upgrading.md's Downgrading list gives this installer."""
    page = (docsnip.root() / "docs" / "upgrading.md").read_text(encoding="utf-8")
    section = page[page.index("## Downgrading"):]
    (line,) = re.findall(rf"^- {re.escape(installer)}: `([^`]+)`$", section, re.M)
    return line


def stamps(refusal: str) -> set[str]:
    return set(re.findall(r"crapkit-analysis=\d+", refusal))


@cell("docs-downgrade", channel="pip venv, pipx, uv tool", harness="none",
      scenario="downgrade: a repo adopted under the candidate, then each downgrade row of docs/upgrading.md "
               "installs 0.7.6; doctor, coverage and next-item work, verify refuses naming both stamps, and the "
               "page's re-seed block ends at verify OK",
      use_cases="downgrade", os=("linux", "windows"), image="core", cadence="push")
@pytest.mark.parametrize("installer", ["pip", "pipx", "uv tool"])
def test_a_downgrade_does_what_the_upgrade_guide_says(box, templates, installer):
    repo = repos.checkout(box, "py-pytest", cache=templates)
    install_candidate(box, installer)
    adopt_measured(box, repo)
    line = downgrade_line(installer)

    assert f"crapkit=={DOWNGRADE_TO}" in line and DOWNGRADE_TO in wheels.releases()
    box.script(line, cwd=repo, expect=0)
    assert DOWNGRADE_TO in version_of(box, cwd=repo)
    for works in (["doctor"], ["coverage"], ["next-item"]):
        box.run(["crapkit", *works], cwd=repo, expect=0)
    refused = box.run(["crapkit", "verify"], cwd=repo, expect=3)
    reseed = box.script(docsnip.fence("docs/upgrading.md", "Downgrading").text, cwd=repo, expect=0)

    assert len(stamps(refused.stderr)) == 2, "the refusal names both stamps"
    assert "verify OK" in reseed.stdout
