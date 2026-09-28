"""An MCP argument value reaches the CLI as a value, whatever its first character.

The server refuses a missing, undeclared or mistyped argument in its own words
(ADR 0001), but it used to hand every string to the child CLI as a bare argv
word. argparse reads a word that starts with `-` as a flag, so
`get_function_brief path="--help"` answered brief's help text marked as a
successful result, and `path="-x.py"`, `exclude=["-legacy"]` and
`check_gate path="-x.py"` answered a usage dump, on every Python version.
build_argv now binds each option as `--flag=value`, puts `--repo=` and `--json`
after them, and ends with `--` and the positionals.

The argv is fed to crapkit's own parser here, the parser the child runs, so a
value the parser would misread fails the first half of this file on the
interpreter the suite runs on. The second half spawns the child against a
measured repo holding a file named `-x.ts`.
"""
import json
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git, istanbul

from crapkit import mcp_server
from crapkit.cli import main
from crapkit.cli.parser import build_parser
from crapkit.mcp_server import TOOLS, build_argv

REPO = "R"


def _tool(name: str) -> dict:
    return next(t for t in TOOLS if t["name"] == name)


def _parsed(name: str, arguments: dict, repo: str = REPO) -> dict:
    """What the child's parser makes of the argv the server builds, handler dropped."""
    namespace = build_parser().parse_args(build_argv(_tool(name), arguments, repo))
    return {key: value for key, value in vars(namespace).items() if key != "func"}


# First the four calls whose value the CLI once read as a flag, then the other
# places a string reaches argv.
VALUE_CASES = {
    "brief path --help": ("get_function_brief", {"path": "--help", "name": "f"},
                          {"path": "--help", "name": "f", "json": True}),
    "brief path -x.py": ("get_function_brief", {"path": "-x.py", "name": "f"},
                         {"path": "-x.py", "name": "f"}),
    "next-item exclude -legacy": ("get_next_item", {"exclude": ["-legacy"]},
                                  {"exclude": ["-legacy"]}),
    "gate path -x.py": ("check_gate", {"path": "-x.py"},
                        {"files": ["-x.py"], "gate": True, "json": True}),
    "brief name --version": ("get_function_brief", {"path": "a.py", "name": "--version"},
                             {"path": "a.py", "name": "--version"}),
    "history path and name dashed": ("get_function_history",
                                     {"path": "-a.py", "name": "-h", "history": True},
                                     {"path": "-a.py", "name": "-h", "history": True,
                                      "tests": False}),
    "next-item exclude --help and a plain one": ("get_next_item",
                                                 {"exclude": ["--help", "grade"], "top": 2},
                                                 {"exclude": ["--help", "grade"], "top": 2}),
    "next-item scope dashed": ("get_next_item", {"scope": ["-web"]}, {"scope": ["-web"]}),
    "worklist scope dashed": ("list_worklist", {"scope": ["-s", "api"], "top": 3},
                              {"scope": ["-s", "api"], "top": 3}),
    "exclude holding =": ("get_next_item", {"exclude": ["a=b"]}, {"exclude": ["a=b"]}),
    "exclude empty string": ("get_next_item", {"exclude": [""]}, {"exclude": [""]}),
    "negative top": ("get_next_item", {"top": -1}, {"top": -1}),
    "coupling numbers": ("list_coupled_files", {"min_support": 2, "min_confidence": 0.25},
                         {"min_support": 2, "min_confidence": 0.25}),
    "duplication number": ("list_duplicate_functions", {"similarity": 0.5},
                           {"similarity": 0.5}),
}


@pytest.mark.parametrize("name, arguments, expected", VALUE_CASES.values(), ids=list(VALUE_CASES))
def test_every_value_lands_on_the_argument_it_was_sent_as(name, arguments, expected):
    parsed = _parsed(name, arguments)

    assert {key: parsed[key] for key in expected} == expected
    assert parsed["repo"] == REPO


def test_a_repo_that_starts_with_a_dash_is_still_the_repo():
    assert _parsed("list_runs", {}, repo="-checkout")["repo"] == "-checkout"


@pytest.mark.skipif(sys.version_info < (3, 12),
                    reason="argparse before 3.12 drops a second `--` from the positionals "
                           "(fixed in 3.12); no file or function is named `--`")
def test_a_positional_spelled_as_the_separator_itself_is_kept():
    assert _parsed("get_function_brief", {"path": "a.py", "name": "--"})["name"] == "--"


def _dest(tool: dict, key: str) -> str:
    """Where the parser stores an MCP key: its own name, except check_gate's
    path, which is rescore's `files`."""
    return "files" if (tool["argv"][0], key) == ("rescore", "path") else key


_SAMPLE = {"string": "-v", "array": ["-v", "--w"], "integer": 3, "number": 0.5,
           "boolean": True}


@pytest.mark.parametrize("tool", TOOLS, ids=[t["name"] for t in TOOLS])
def test_every_tool_takes_a_dashed_value_for_every_argument_it_declares(tool):
    arguments = {key: _SAMPLE[prop["type"]] for key, prop in tool["properties"].items()}

    namespace = vars(build_parser().parse_args(build_argv(tool, arguments, REPO)))

    for key, value in arguments.items():
        dest = _dest(tool, key)
        assert namespace[dest] == ([value] if dest == "files" else value), key


def test_the_positionals_come_last_after_the_separator():
    argv = build_argv(_tool("check_gate"), {"path": "src/a.py"}, REPO)

    assert argv == ["rescore", "--gate", "--repo=R", "--json", "--", "src/a.py"]


def test_a_tool_without_positionals_ends_on_its_options():
    argv = build_argv(_tool("get_next_item"), {"top": 3, "exclude": ["a", "b"]}, REPO)

    assert argv == ["next-item", "--top=3", "--exclude=a", "--exclude=b", "--repo=R"]


# --- through the child the server spawns --------------------------------------

TOML = """[crapkit]
target = 6

[[scope]]
name = "root"
paths = ["."]
languages = ["typescript"]

[[lane]]
name = "unit"
command = "python -c pass"
artifact = "coverage/unit.json"
parser = "istanbul"
scopes = ["root"]
"""

# ccn 8 and half its branches run: over the ceiling of 6, so the queue holds it.
DASH_TS = "export function dash(n: number): number {\n" + "".join(
    f"  if (n > {i}) {{ return {i}; }}\n" for i in range(1, 8)) + "  return 0;\n}\n"


@pytest.fixture(scope="module")
def dashed_repo(tmp_path_factory) -> Path:
    """A measured repo whose one source file is named `-x.ts` at the root, so
    its repo-relative path starts with a dash."""
    root = tmp_path_factory.mktemp("dashed")
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (root / "-x.ts").write_text(DASH_TS, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".crapkit/\ncoverage/\n", encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    istanbul(root, "coverage/unit.json", "-x.ts", {"dash": (1, 10, 1)})
    assert main(["coverage", "--reuse-artifacts", "--repo", str(root)]) == 0
    return root


_COLOUR = {"no colour env": {}, "FORCE_COLOR=1": {"FORCE_COLOR": "1"}}


@pytest.fixture(params=sorted(_COLOUR))
def colour_env(request, monkeypatch):
    """The child inherits the server's environment; FORCE_COLOR is what Claude
    Code exports."""
    for name in ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "PY_COLORS", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in _COLOUR[request.param].items():
        monkeypatch.setenv(name, value)


def _call(root: Path, name: str, arguments: dict) -> dict:
    return mcp_server._call_tool(root, name, arguments)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_a_file_named_with_a_leading_dash_gets_its_brief(dashed_repo, colour_env, capsys):
    result = _call(dashed_repo, "get_function_brief", {"path": "-x.ts", "name": "dash"})

    assert result["isError"] is False, _text(result)
    assert result["structuredContent"]["path"] == "-x.ts"
    assert result["structuredContent"]["function"].startswith("dash")


def test_check_gate_judges_a_file_named_with_a_leading_dash(dashed_repo, colour_env):
    result = _call(dashed_repo, "check_gate", {"path": "-x.ts"})

    assert result["isError"] is False, _text(result)
    assert result["structuredContent"]["gate"]["ok"] is True


def test_history_reads_a_file_named_with_a_leading_dash(dashed_repo, colour_env):
    result = _call(dashed_repo, "get_function_history",
                   {"path": "-x.ts", "name": "dash", "history": True})

    assert result["isError"] is False, _text(result)
    (function,) = result["structuredContent"]["functions"]
    assert function["commits"], "git log reached the file"


def test_an_exclude_fragment_with_a_leading_dash_excludes(dashed_repo, colour_env):
    kept = json.loads(_text(_call(dashed_repo, "get_next_item", {"exclude": ["-y"]})))
    dropped = json.loads(_text(_call(dashed_repo, "get_next_item", {"exclude": ["-x"]})))

    assert kept["item"]["path"] == "-x.ts", kept
    assert dropped["empty"] is True and dropped["reasons"]["excluded_by_flag"] == 1, dropped


def test_a_path_spelled_like_help_is_never_help_marked_as_a_result(dashed_repo, colour_env):
    """The one call that used to answer isError false: brief's own help text."""
    result = _call(dashed_repo, "get_function_brief", {"path": "--help", "name": "dash"})
    text = _text(result)

    assert result["isError"] is True, text
    assert "usage:" not in text and "\x1b" not in text, text
    assert json.loads(text)["error"]["kind"], "the CLI's error object, not argparse text"
