r"""`doctor --plugin-root` finds the installed plugin whatever spelling names
its directory.

Two paths lead there: CLAUDE_CONFIG_DIR, which a user sets by hand in any
spelling their shell accepts, and the --plugin-root PATH argument. Either is
joined to plugins/cache/... and globbed for manifests. A directory named with a
space and `[x]` must not read as a glob pattern, and on Windows a forward-slash
or lower-case-drive spelling must still open the install. The boundary hunt
found every row sound and no test held any of them. Git Bash's `/c/...`, WSL's
`/mnt/c/...`, `\\?\C:\...` and `\\localhost\C$\...` read as the drive they
name, through repopath's typed entry, as `--repo` does; before, the first two
named `C:\c\...` and found no install.

A found install at 9.9.9 disagrees with this CLI, so doctor names 9.9.9; an
install it missed would answer "no installed crapkit plugin".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main

from path_spellings import admin_share, linked_checkout, lower_drive, need

INSTALLED = "9.9.9"


@pytest.fixture(autouse=True)
def _agreeing_path_crapkit(monkeypatch):
    """The `crapkit` on PATH answers with this CLI's version, so only the
    install's own version can disagree."""
    admin._spawned_cli.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: ("crapkit", crapkit.__version__))


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    """A Claude Code config directory holding one crapkit install at 9.9.9."""
    root = tmp_path.resolve() / "claude cfg [x]"
    manifest = (root / "plugins" / "cache" / "mkt" / "crapkit" / INSTALLED / ".claude-plugin"
                / "plugin.json")
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "crapkit", "version": INSTALLED}), encoding="utf-8")
    return root


def _install(config: Path) -> Path:
    return config / "plugins" / "cache" / "mkt" / "crapkit" / INSTALLED


def _found(capsys, argv: list[str]) -> str:
    code = main(argv)
    out = capsys.readouterr().out
    assert code == 1, out
    assert "no installed crapkit plugin" not in out, out
    assert INSTALLED in out, out
    return out


# id -> (need, the spelling of the config directory)
DIRECTORY_SPELLINGS = {
    "native": ("", str),
    "trailing-separator": ("", lambda root: str(root) + os.sep),
    "linked": ("", lambda root: str(linked_checkout(root))),
    "forward-slashes": ("windows", lambda root: root.as_posix()),
    "lower-drive": ("windows", lower_drive),
    "msys": ("windows", lambda root: "/" + root.drive[0].lower() + root.as_posix()[2:]),
    "wsl": ("windows", lambda root: "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]),
    "extended-length": ("windows", lambda root: "\\\\?\\" + str(root)),
    "admin-share": ("windows", admin_share),
}


@pytest.mark.parametrize("which", DIRECTORY_SPELLINGS)
def test_claude_config_dir_in_any_spelling_finds_the_install(config, monkeypatch, capsys, which):
    spec, spell = DIRECTORY_SPELLINGS[which]
    need(spec, config)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", spell(config))

    _found(capsys, ["doctor", "--plugin-root"])


@pytest.mark.parametrize("which", DIRECTORY_SPELLINGS)
def test_a_plugin_root_argument_in_any_spelling_finds_the_install(config, capsys, which):
    spec, spell = DIRECTORY_SPELLINGS[which]
    need(spec, config)

    _found(capsys, ["doctor", "--plugin-root", spell(config)])


@pytest.mark.parametrize("which", ["native", "forward-slashes"])
def test_a_plugin_root_argument_naming_the_install_itself_checks_it(config, capsys, which):
    spec, spell = DIRECTORY_SPELLINGS[which]
    need(spec, config)

    out = _found(capsys, ["doctor", "--plugin-root", spell(_install(config))])

    assert str(_install(config)) in out or _install(config).as_posix() in out, out
