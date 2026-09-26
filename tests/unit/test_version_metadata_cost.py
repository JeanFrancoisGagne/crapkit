"""`crapkit --version` reads a header, not a metadata package.

importlib.metadata drags in email, csv, typing and importlib.resources to hand
back one string. The string it hands back is the `Version:` header of the
installed distribution's METADATA file, which is a directory listing and an open
away. When reading it straight produces the number the package already carries,
the two sources agree and the expensive one has nothing to add.

The rule it must not bend: the installed distribution is what `--version`
reports. So a dist-info that disagrees with `crapkit.__version__`, and a tree
where no dist-info can be found at all, both hand the question to
importlib.metadata and take its answer.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
from crapkit import __version__
from untraced_child import untraced_env

PROBE = ("from crapkit.cli.parser import _version_line\n"
         "line = _version_line()\n"
         "import sys, json\n"
         "print(json.dumps([line, 'importlib.metadata' in sys.modules]))\n")


def _src_root() -> str:
    return str(Path(crapkit.__file__).resolve().parent.parent)


def _fake_dist_info(root: Path, version: str) -> str:
    """A sys.path entry holding crapkit-<version>.dist-info, as pip would leave it."""
    info = root / f"crapkit-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: crapkit\nVersion: {version}\n"
        "Summary: deterministic CRAP-score framework\n\nVersion: not this one\n",
        encoding="utf-8")
    return str(root)


def _run(path_entries: list[str], *flags: str) -> tuple[str, bool]:
    # A traced child starts coverage and imports what coverage imports before
    # the probe asks; the probe measures crapkit's cost, so it runs untraced.
    env = untraced_env()
    env["PYTHONPATH"] = os.pathsep.join(path_entries)
    done = subprocess.run([sys.executable, *flags, "-c", PROBE],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    line, imported = json.loads(done.stdout.splitlines()[-1])
    return line, imported


def test_an_agreeing_distribution_answers_without_importing_metadata(tmp_path):
    line, imported = _run([_fake_dist_info(tmp_path, __version__), _src_root()])

    assert line == f"crapkit {__version__}"
    assert imported is False


def test_a_disagreeing_distribution_still_wins_and_pays_for_it(tmp_path):
    """The number lives in pyproject.toml too. An installed 9.9.9 is what the
    user is running, whatever the package constant says."""
    line, imported = _run([_fake_dist_info(tmp_path, "9.9.9"), _src_root()])

    assert line == "crapkit 9.9.9"
    assert imported is True


def test_a_source_tree_with_nothing_installed_falls_back_to_the_package(tmp_path):
    """Copy the imported package without its distribution metadata. With -S,
    only these package files and the standard library remain reachable."""
    shutil.copytree(Path(crapkit.__file__).resolve().parent, tmp_path / "crapkit",
                    ignore=shutil.ignore_patterns("__pycache__"))
    line, imported = _run([str(tmp_path)], "-S")

    assert line == f"crapkit {__version__}"
    assert imported is True


def test_a_dist_info_with_no_version_header_defers_to_metadata(tmp_path):
    """A METADATA with no Version: is not a number to report, so the question
    goes to importlib.metadata and its answer stands, `None` and all."""
    info = tmp_path / "crapkit-0.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: crapkit\n", encoding="utf-8")

    line, imported = _run([str(tmp_path), _src_root()], "-S")

    assert line == "crapkit None"
    assert imported is True


# --- several dist-infos in one directory ---------------------------------------
#
# A botched upgrade can leave crapkit-0.7.5.dist-info beside crapkit-0.8.0.dist-info.
# The header scan takes the first crapkit dist-info os.listdir returns, and
# importlib.metadata does the same. NTFS lists names sorted, ext4 in hash order
# and tmpfs newest first, so two machines can answer different numbers for the
# same broken install. One machine always answers one number: --version says
# what importlib.metadata says.

# The package's own dist-info beside two that earlier installs left: the scan
# answers without asking importlib.metadata only when it lands on the
# package's own number, the one case where the two could part.
LEFT_BEHIND = ["0.7.5", __version__, "0.6.0"]


def _answers(path_entries: list[str]) -> tuple[str, str]:
    """What `python -m crapkit --version` and importlib.metadata each say."""
    env = untraced_env()
    env["PYTHONPATH"] = os.pathsep.join(path_entries)
    cli = subprocess.run([sys.executable, "-m", "crapkit", "--version"],
                         capture_output=True, text=True, env=env)
    meta = subprocess.run([sys.executable, "-c",
                           "import importlib.metadata as m; print(m.version('crapkit'))"],
                          capture_output=True, text=True, env=env)
    assert cli.returncode == 0 and meta.returncode == 0, cli.stderr + meta.stderr
    return cli.stdout.strip(), meta.stdout.strip()


@pytest.mark.parametrize("made", [LEFT_BEHIND, LEFT_BEHIND[::-1]], ids=["upgrade-order", "reverse-order"])
def test_several_dist_infos_in_one_entry_answer_what_importlib_metadata_answers(tmp_path, made):
    for version in made:
        _fake_dist_info(tmp_path, version)

    cli, meta = _answers([str(tmp_path), _src_root()])

    assert meta in LEFT_BEHIND, meta
    assert cli == f"crapkit {meta}", (sorted(os.listdir(tmp_path)), os.listdir(tmp_path))
