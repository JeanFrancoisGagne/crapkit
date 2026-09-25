"""tools/deploy/README.md and the --help of run.py and toolchain.py, held to
the code they describe.

The README described the kit as it was before the images packet: no
cells-arm64 or full-latest image, no --faketime, no Linux toolchain root,
none of the environment variables that keep two checkouts on one machine
apart, and not the QEMU line run.py prints when a host cannot run arm64
containers. run.py's --help printed half of its docstring's first sentence
and said nothing about 14 of its 16 flags, and the docstring's usage lines
left out full-latest. These tests read the images, flags and variables from
the code, so a new one fails here until the page and the help name it.
"""
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tools" / "deploy"
sys.path.append(str(DEPLOY))

import pins as pinsfile  # noqa: E402
import run  # noqa: E402
import toolchain  # noqa: E402

README = (DEPLOY / "README.md").read_text(encoding="utf-8")
TOOLS = [run, toolchain]


def section(heading: str) -> str:
    """The README text under `## heading`, up to the next `## `; empty when
    the page has no such heading."""
    _, _, after = README.partition(f"\n## {heading}\n")
    return after.split("\n## ", 1)[0]


def tables(text: str) -> list[list[str]]:
    """The first cell of each body row, one list per Markdown table."""
    found, rows = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            rows = None
            continue
        if rows is None:
            rows = []
            found.append(rows)
        rows.append(line.split("|")[1].strip().strip("`"))
    return [body[2:] for body in found]


def options(parser) -> list:
    return [action for action in parser._actions if action.dest != "help"]


def long_options(parser) -> list[str]:
    return sorted(flag for action in options(parser) for flag in action.option_strings if flag.startswith("--"))


def variables(paths=None) -> list[str]:
    """Every environment variable the tools here (or these files) read from
    their user: the `*_ENV = "..."` constants."""
    source = "".join(path.read_text(encoding="utf-8") for path in sorted(paths or DEPLOY.glob("*.py")))
    return sorted(set(re.findall(r'^\w+_ENV = "(\w+)"', source, re.M)))


def run_lines() -> list[list[str]]:
    """Each `python tools/deploy/run.py ...` line of the README, as the argv
    after the script, without the comment and the `...` that stands for more."""
    lines = [line.split("#")[0] for line in README.splitlines()
             if line.strip().startswith("python tools/deploy/run.py")]
    return [arguments(line) for line in lines]


def arguments(line: str) -> list[str]:
    return [word for word in shlex.split(line)[2:] if word != "..."]


def test_the_scan_finds_the_variables_the_modules_define():
    assert {run.REPO_ENV, toolchain.ROOT_ENV, toolchain.BASETEMP_ENV} <= set(variables())


@pytest.mark.parametrize("image", sorted(pinsfile.IMAGE_CHAIN))
def test_the_images_section_says_what_each_image_holds_and_what_it_measured(image):
    holds, measured = (tables(section("Images")) + [[], []])[:2]

    assert image in holds, f"the Images table never says what {image} holds"
    assert image in measured, f"the Images measurements have no row for {image}"


def test_the_images_section_gives_the_qemu_line_run_py_prints():
    assert f"`{run.QEMU_FIX}`" in section("Images")


@pytest.mark.parametrize("flag", long_options(run.build_parser()))
def test_the_readme_names_every_run_py_flag(flag):
    assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", README), f"the README never names run.py {flag}"


@pytest.mark.parametrize("name", variables())
def test_the_environment_table_names_each_variable_a_tool_reads(name):
    assert f"| `{name}` |" in section("Environment")


@pytest.mark.parametrize("platform_name", ["win32", "darwin", "linux"])
def test_the_readme_names_the_toolchain_root_on_each_os(platform_name, monkeypatch, tmp_path):
    windows = platform_name == "win32"
    monkeypatch.delenv(toolchain.ROOT_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(toolchain, "sys", SimpleNamespace(platform=platform_name))
    monkeypatch.setattr(toolchain, "WINDOWS", windows)
    monkeypatch.setattr(toolchain, "long_path", lambda path: path)
    monkeypatch.setattr(toolchain.Path, "home", lambda: tmp_path)

    parts = toolchain.default_root().relative_to(tmp_path).parts
    spelled = "%LOCALAPPDATA%\\" + "\\".join(parts) if windows else "~/" + "/".join(parts)

    assert f"`{spelled}`" in section("Native runs")


@pytest.mark.parametrize("argv", run_lines(), ids=" ".join)
def test_every_run_py_line_in_the_readme_is_one_run_py_takes(argv):
    run.parse(argv)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.__name__)
def test_help_prints_the_whole_docstring(tool):
    assert tool.__doc__.strip() in tool.build_parser().format_help()


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.__name__)
def test_help_names_each_variable_its_tool_defines(tool):
    shown = tool.build_parser().format_help()

    for name in variables([Path(tool.__file__)]):
        assert name in shown, f"{tool.__name__}.py --help never names {name}"


def test_run_py_usage_lines_name_every_image_and_flag():
    usage = run.__doc__.split("\n\n")[1]

    for said in [*pinsfile.IMAGE_CHAIN, *long_options(run.build_parser())]:
        assert re.search(rf"[\s\[|]{re.escape(said)}[\s\]|]", usage), f"run.py's usage lines leave out {said}"


def test_run_py_help_names_each_variable_the_readme_and_the_qemu_line():
    shown = run.build_parser().format_help()

    for said in [*variables(), "tools/deploy/README.md", run.QEMU_FIX]:
        assert said in shown, f"run.py --help never says {said}"


@pytest.mark.parametrize("tool, flag", [(tool, action.option_strings[0]) for tool in TOOLS
                                        for action in options(tool.build_parser())],
                         ids=lambda value: getattr(value, "__name__", value))
def test_every_flag_says_what_it_does(tool, flag):
    action = next(action for action in options(tool.build_parser()) if flag in action.option_strings)

    assert action.help, f"{tool.__name__}.py --help says nothing about {flag}"
