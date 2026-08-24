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
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit import __version__

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
    env = dict(os.environ)
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


def test_a_source_tree_with_nothing_installed_falls_back_to_the_package():
    """-S drops site-packages, so no distribution is reachable at all: the
    merge driver's situation, and the package constant is all there is."""
    line, imported = _run([_src_root()], "-S")

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
