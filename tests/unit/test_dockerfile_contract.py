"""The Dockerfile at the repo root, pinned to the parser and to what it copies.

Whatever builds a server from that file never runs this suite, so nothing else
would notice an ENTRYPOINT naming a subcommand the parser dropped or a COPY
naming a file the tree no longer has. Both fail at build or start time, on
someone else's machine. These read the Dockerfile the way
`test_action_contract.py` reads `action.yml`.
"""
import argparse
import json
import tomllib
from functools import lru_cache
from pathlib import Path

from crapkit.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
DOCS = ROOT / "docs" / "agent-json.md"


def _logical_lines(text: str) -> list[str]:
    """Instruction lines, comments dropped and backslash continuations joined."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1].rstrip() + " "
            continue
        out.append(buf + line)
        buf = ""
    return out


@lru_cache(maxsize=None)
def _instructions() -> tuple[tuple[str, str], ...]:
    lines = _logical_lines(DOCKERFILE.read_text(encoding="utf-8"))
    split = [line.split(None, 1) for line in lines]
    return tuple((head.upper(), rest[0] if rest else "") for head, *rest in split)


def _arguments(instruction: str) -> list[str]:
    return [arg for name, arg in _instructions() if name == instruction]


@lru_cache(maxsize=None)
def _console_scripts() -> dict[str, str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["scripts"]


def _subcommands() -> dict:
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


def _copy_sources() -> list[str]:
    """Every source path a COPY names: its words minus the trailing destination
    and minus any `--from=` style flag."""
    sources: list[str] = []
    for arg in _arguments("COPY"):
        words = [w for w in arg.split() if not w.startswith("--")]
        sources.extend(words[:-1])
    return sources


def test_entrypoint_runs_the_console_script_and_a_subcommand_the_parser_defines():
    entrypoints = _arguments("ENTRYPOINT")
    assert len(entrypoints) == 1, "one ENTRYPOINT, or the last one silently wins"
    argv = json.loads(entrypoints[0])
    assert argv[0] in _console_scripts(), f"{argv[0]} is not a [project.scripts] name"
    assert argv[1] in _subcommands(), f"{argv[1]} is not a crapkit subcommand"
    assert argv[1] == "mcp", "the image serves the MCP server over stdio"


def test_every_copy_source_exists_in_the_tree():
    for source in _copy_sources():
        assert (ROOT / source.rstrip("/")).exists(), f"COPY {source} names nothing"


def test_the_install_reaches_the_console_script():
    """`pip install .` is what puts `crapkit` on PATH. A COPY of src/ alone
    leaves the ENTRYPOINT resolving to nothing."""
    assert any("pip install" in arg for arg in _arguments("RUN"))
    assert "pyproject.toml" in _copy_sources()
    assert any(source.rstrip("/") == "src" for source in _copy_sources())


def test_readme_and_license_are_copied_because_the_metadata_reads_them():
    """`readme = { file = "README.md" }` and `license`: the build fails without
    the files, and the failure names a path, not the reason."""
    assert "README.md" in _copy_sources()
    assert "LICENSE" in _copy_sources()


def test_git_is_installed_because_the_tools_shell_to_a_cli_that_reads_git():
    runs = " ".join(_arguments("RUN"))
    assert "apt-get install" in runs
    assert " git" in runs


def test_the_server_runs_unprivileged():
    users = _arguments("USER")
    assert users, "no USER: the server runs as root"
    account = users[-1].split()[0]
    assert account not in ("root", "0")
    assert any(account in arg for arg in _arguments("RUN")), f"{account} is never created"


def test_base_image_is_a_pinned_python_slim():
    froms = _arguments("FROM")
    assert froms == ["python:3.12-slim"]


def test_dockerignore_keeps_the_suite_and_the_store_out_of_the_context():
    entries = {line.strip() for line in _logical_lines(DOCKERIGNORE.read_text(encoding="utf-8"))}
    for kept_out in ("tests/", "docs/", ".crapkit/", ".git/"):
        assert kept_out in entries, f"{kept_out} rides into every build context"


def test_docs_document_the_image_under_the_mcp_section():
    text = DOCS.read_text(encoding="utf-8")
    assert "\n## Docker\n" in text
    assert text.index("\n## MCP server\n") < text.index("\n## Docker\n")
    assert "docker build -t crapkit ." in text
    assert 'docker run -i --rm -v "$PWD:/repo" -w /repo crapkit' in text
