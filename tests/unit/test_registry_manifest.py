"""server.json, the MCP Registry's view of this server, pinned to the package.

The registry hosts metadata only: `server.json` names the PyPI package and the
transport, and ownership is proven by an `mcp-name:` marker in the README as
PyPI serves it. Nothing imports either file, so without these contracts a
release could bump every other version surface and ship a manifest that points
the registry's clients at last release's package.
"""
import json
import re
from pathlib import Path

from crapkit import __version__

ROOT = Path(__file__).resolve().parent.parent.parent
NAME = "io.github.JeanFrancoisGagne/crapkit"


def _manifest() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_the_manifest_names_this_release():
    """Both version fields, because the registry reads the outer one and a
    client installs the inner one."""
    data = _manifest()

    assert data["version"] == __version__
    assert data["packages"][0]["version"] == __version__


def test_the_manifest_points_at_the_pypi_package_over_stdio():
    (package,) = _manifest()["packages"]

    assert package["registryType"] == "pypi"
    assert package["identifier"] == "crapkit"
    assert package["transport"] == {"type": "stdio"}
    assert {"type": "positional", "value": "mcp"} in package["packageArguments"], \
        "a client that installs the package has to learn the subcommand somewhere"


def test_the_readme_carries_the_ownership_marker_the_registry_checks():
    """The marker has to reach PyPI's copy of the README: the registry verifies
    package ownership by finding `mcp-name: <server name>` in the description
    the package serves, so the comment lives in README.md and rides the sdist."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"mcp-name: {NAME}" in readme


def test_the_manifest_and_the_marker_agree_on_the_name():
    assert _manifest()["name"] == NAME


def test_the_description_reads_as_one_plain_sentence():
    description = _manifest()["description"]

    assert description.strip() == description and len(description) <= 100, "the registry refuses over 100 chars"
    assert re.match(r"^[A-Z]", description) and description.endswith(".")


def test_the_description_counts_the_tenth_tool():
    """0.5.0 added `gate`. The registry republishes this sentence after PyPI,
    so it moves with the count in `_INSTRUCTIONS`, AGENTS.md and the MCP tables."""
    description = _manifest()["description"]

    assert description.startswith("Ten read-only tools"), description
