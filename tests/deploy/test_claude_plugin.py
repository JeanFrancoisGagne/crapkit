"""The Claude Code plugin, installed and upgraded the way the README says.

The README's marketplace and install lines run verbatim. github.com is the
kit's mirror: Claude Code clones a marketplace through the git binary, so the
insteadOf rules in the sandbox's ~/.gitconfig reach it, and a release is the
mirror's main moving. Each cell asserts on what the user reads: `claude mcp
list`, `claude plugin list --json`, `crapkit doctor --plugin-root`.

    lin-claude-plugin-fresh        README lines, Connected, doctor 0, every hook handler spawned
    win-uvtool-claude-plugin       the same on Windows with a uv tool CLI and the shim .exe
    lin-up-claude-plugin-0.7.6     CLI first, the version-gap line, the README update lines
    lin-up-claude-plugin-0.4.2     the python -m launcher of 0.4.x becomes `crapkit mcp`
    lin-up-claude-plugin-0.5.1     0.5.x allowlist names reach today's tools through the rename table

The helpers below serve every module of the deploy-plugins packet.
"""
from __future__ import annotations

import itertools
import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from kit import docsnip, gitmirror, hooks_rules, profiles, repos, shim, stub_anthropic
from kit.cells import cell

PACKET = "deploy-plugins"
WINDOWS = os.name == "nt"
TOOLS = 12
PLUGIN = "crapkit@crapkit"
CLAUDE = "The Claude Code plugin"
# docs/lanes.md#containers: the lane key that lets a coverage.py lane run in a container.
CONTAINER_KEY = "container_ok = true"
# A new function over the default ceiling of 6: ccn 11.
BREACH = '''def route(kind, size, fast, retry, user):
    if kind == "a" and size > 1:
        return 1
    if kind == "b" or fast:
        return 2
    if retry and user:
        return 3
    if size > 10:
        return 4
    if size > 20 and not fast:
        return 5
    if user == "root":
        return 6
    return 7
'''
CRAPKIT_SIDE = ("crapkit-side contract: the kit spawns each handler of the installed hooks.json with the "
                "argv Claude Code builds from it; Claude Code does not drive these spawns")


# --- the machine a user has --------------------------------------------------------

def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def scripts(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def launcher(directory: Path) -> Path:
    return directory / ("crapkit.exe" if WINDOWS else "crapkit")


SANDBOX_DEPTH = ("the sandbox HOME sits about 50 characters deeper than C:\\Users\\<name>, and a marketplace clone "
                 "of the whole repository writes a 139-character docs path (deploy-plugins-10); core.longpaths in the "
                 "sandbox gitconfig gives back the depth the sandbox took")


def harness_on_path(box) -> None:
    """The pinned harness CLIs, behind the toolchain on the sandbox PATH. On
    Windows, git may write paths past MAX_PATH, for the reason SANDBOX_DEPTH gives."""
    box.env["PATH"] = os.pathsep.join([*box.path_dirs(), *box.toolchain["harness_bin"]])
    if WINDOWS:
        box.transcript.note(SANDBOX_DEPTH)
        box.run(["git", "config", "--global", "core.longpaths", "true"], expect=0)


def page_lines(heading: str, index: int = 0, page: str = "README.md", base: Path | None = None) -> list[str]:
    return docsnip.commands(docsnip.fence(page, heading, index=index, base=base))


def fence_holding(page: str, text: str, base: Path | None = None) -> docsnip.Fence:
    """The first fence of `page` holding `text`, under whatever heading an old page used."""
    found = [block for block in docsnip.fences(page, base) if text in block.text]
    if not found:
        raise docsnip.DocSnipError(f"{page}: no fence holds {text!r}")
    return found[0]


def table_command(page: str, row: str) -> str:
    """The code span in the second cell of the table row whose first cell is `row`."""
    text = (docsnip.root() / page).read_text(encoding="utf-8")
    match = re.search(rf"^\| {re.escape(row)} \| `([^`]+)` \|", text, re.MULTILINE)
    if match is None:
        raise docsnip.DocSnipError(f"{page}: no table row {row!r}")
    return match[1]


def cli_venv(box, spec: str | None = None, python: str = "3.12") -> Path:
    """crapkit in a fresh venv whose scripts lead the sandbox PATH: the README's
    install line verbatim, or `spec` for the older release a cell upgrades from.
    The venv also holds pytest and pytest-cov, the fixture repo's own test deps."""
    venv = box.root / "venv"
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(scripts(venv))
    box.run(["python", "-m", "pip", "install", "-q", "pytest", "pytest-cov"], expect=0)
    if spec:
        box.run(["python", "-m", "pip", "install", "-q", spec], expect=0)
    else:
        box.script(page_lines("The 60-second start")[0], expect=0)
    return launcher(scripts(venv))


def upgrade_cli(box) -> None:
    """docs/upgrading.md's line for a pip install in the active environment."""
    box.script(table_command("docs/upgrading.md", "pip in the active environment"), expect=0)


def crapkit(box, repo: Path, *args: str, expect: int | None = 0):
    return box.run(["crapkit", *args], cwd=repo, expect=expect)


def allow_container_lane(repo: Path) -> None:
    config = repo / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("[[lane]]\n", f"[[lane]]\n{CONTAINER_KEY}\n", 1), encoding="utf-8")


def measured_repo(box, templates, name: str = "py-pytest") -> Path:
    """A fixture repo adopted and scored once: init, the container key where the
    guard would refuse the lane, coverage."""
    repo = repos.checkout(box, name, cache=templates)
    crapkit(box, repo, "init")
    if in_container():
        allow_container_lane(repo)
    crapkit(box, repo, "coverage")
    return repo


def plain_repo(box, name: str = "work") -> Path:
    """A repo with one commit and no crapkit config: where a session starts."""
    repo = box.root / name
    repo.mkdir()
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    (repo / "README.md").write_text("# work\n", encoding="utf-8")
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "start"], cwd=repo, env=box.commit_env(), expect=0)
    return repo


def github(box, candidate=None, at: str | None = None) -> gitmirror.Mirror:
    """github.com answered by the mirror: main at release `at`, or at the candidate
    published as a release on top of main."""
    mirror = gitmirror.make(box)
    if at:
        mirror.release_to(at)
    else:
        mirror.publish(candidate.staged, candidate.version)
    return mirror


def old_page(box, mirror: gitmirror.Mirror, version: str, page: str = "README.md") -> Path:
    """`page` as release `version` printed it, in a directory docsnip reads as a base."""
    base = box.root / f"pages-v{version}"
    (base / page).parent.mkdir(parents=True, exist_ok=True)
    (base / page).write_text(mirror.git("show", f"v{version}:{page}"), encoding="utf-8")
    return base


def run_lines(box, lines: list[str], cwd: Path, expect: int | None = 0) -> list:
    return [box.script(line, cwd=cwd, expect=expect) for line in lines]


# --- what Claude Code reports ----------------------------------------------------------

def installed(box, claude: str = "claude") -> dict:
    """The crapkit entry of `claude plugin list --json`, or {} when none is installed."""
    listed = json.loads(box.run([claude, "plugin", "list", "--json"], expect=0).stdout)
    return next((entry for entry in listed if entry.get("id") == PLUGIN), {})


def mcp_line(box, cwd: Path, claude: str = "claude") -> str:
    """The plugin server's line of `claude mcp list`."""
    step = box.run([claude, "mcp", "list"], cwd=cwd)
    lines = [line for line in step.stdout.splitlines() if line.startswith("plugin:crapkit:crapkit")]
    assert lines, f"`claude mcp list` names no plugin:crapkit:crapkit server\n{step.stdout}{step.stderr}"
    return lines[0]


def doctor_plugin(box, *root: str, cwd: Path | None = None):
    return box.run(["crapkit", "doctor", "--plugin-root", *root], cwd=cwd or box.root)


def offered_tools(box, cwd: Path, claude: str = "claude") -> list[str]:
    """The tool names Claude Code sends the model: one `claude -p` turn against the
    Messages stub, which answers with text and records the request."""
    with stub_anthropic.serve([{"text": "done"}]) as stub:
        box.run([claude, "-p", "list your tools"], cwd=cwd, expect=0,
                env={"ANTHROPIC_BASE_URL": stub.url, "ANTHROPIC_API_KEY": "sk-ant-stub"})
    return [tool["name"] for tool in stub.bodies()[0].get("tools", [])]


# --- the hook contract, crapkit's side ---------------------------------------------------

# The edits the hook contract replays: the breach in calc/big.py by either tool,
# and a Markdown file no lane measures, which the hook must leave alone.
EDITS = (("Edit", "calc/big.py"), ("Write", "calc/big.py"), ("Edit", "notes.md"))


class Spawn(NamedTuple):
    path: str
    argv: list[str]
    exit: int
    stderr: str


def spawn_handlers(box, plugin_root: Path, repo: Path) -> list[Spawn]:
    """Every handler Claude Code runs for each of EDITS, spawned as Claude Code
    spawns it, after a Write that left a breach in calc/big.py. The shipped
    plugin runs its one hook on all three; a 0.8.0-style plugin runs only the
    handler whose `if` admits the edit."""
    box.transcript.note(CRAPKIT_SIDE)
    (repo / "calc" / "big.py").write_text(BREACH, encoding="utf-8", newline="\n")
    (repo / "notes.md").write_text("# Notes\n", encoding="utf-8", newline="\n")
    verdicts = []
    for tool, path, argv in hooks_rules.spawns(profiles.load("claude-code"), plugin_root, EDITS):
        step = box.run(argv, cwd=repo, input=json.dumps(hooks_rules.payload(repo, path, tool)))
        verdicts.append(Spawn(path, argv, step.exit, step.stderr))
    return verdicts


def hook_starts(box) -> list[list[str]]:
    """The argv tail of every claude-hook start the shim recorded."""
    return [start["argv"][1:] for start in shim.starts(box) if "claude-hook" in start["argv"]]


def silent(spawn: Spawn) -> bool:
    return spawn.exit == 0 and not spawn.stderr.strip()


def python_edit(spawn: Spawn) -> bool:
    return spawn.path.endswith(".py")


def assert_handler_verdicts(verdicts: list[Spawn]) -> None:
    """The Edit and the Write of calc/big.py name the breach at exit 2; every
    other spawn is silent at 0."""
    python = [(spawn.exit, "over ceiling 6 in calc/big.py" in spawn.stderr) for spawn in filter(python_edit, verdicts)]
    loud = [spawn for spawn in itertools.filterfalse(python_edit, verdicts) if not silent(spawn)]
    assert python == [(2, True), (2, True)]
    assert loud == []


# --- the cells --------------------------------------------------------------------------

def fresh_install(box, candidate, templates, real: Path) -> None:
    repo = measured_repo(box, templates)
    github(box, candidate)
    harness_on_path(box)
    box.prepend_path(shim.install(box, real))
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    plugin = installed(box)
    root = Path(plugin["installPath"])
    doctor = doctor_plugin(box)

    assert plugin["version"] == candidate.version
    assert "Connected" in mcp_line(box, repo)
    assert doctor.exit == 0 and doctor.stdout.splitlines() == [f"crapkit doctor: checking {root}"]
    verdicts = spawn_handlers(box, root, repo)
    assert_handler_verdicts(verdicts)
    assert hook_starts(box) == [spawn.argv[1:] for spawn in verdicts]


@cell("lin-claude-plugin-fresh", channel="Claude marketplace, README line via insteadOf mirror",
      harness="Claude Code", scenario="fresh: marketplace add + install; mcp list Connected; doctor --plugin-root 0; "
      "hook contract spawn per handler (labelled crapkit-side contract)",
      use_cases="plugin install, doctor --plugin-root", os="linux", image="core", cadence="push")
def test_claude_plugin_fresh(box, candidate, templates):
    fresh_install(box, candidate, templates, cli_venv(box))


def uv_tool_cli(box) -> Path:
    """The README's `uv tool install crapkit`, with uv's bin dir put on PATH the way
    `uv tool update-shell` would."""
    readme = (docsnip.root() / "README.md").read_text(encoding="utf-8")
    assert "`uv tool install crapkit`" in readme
    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    bin_dir = Path(box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip())
    box.prepend_path(bin_dir)
    box.run(["python", "-m", "venv", str(box.root / "suite")], expect=0)
    box.prepend_path(scripts(box.root / "suite"))
    box.run(["python", "-m", "pip", "install", "-q", "pytest", "pytest-cov"], expect=0)
    return launcher(bin_dir)


@cell("win-uvtool-claude-plugin", channel="uv tool + Claude marketplace", harness="Claude Code",
      scenario="fresh: Connected with crapkit.exe; hook contract spawn of shim .exe; doctor --plugin-root",
      use_cases="plugin install", os="windows", image=None, cadence="push")
def test_claude_plugin_fresh_uv_tool_windows(box, candidate, templates):
    box.prepend_path(Path(box.toolchain.python("3.12")).parent)
    fresh_install(box, candidate, templates, uv_tool_cli(box))


def old_lines(box, mirror: gitmirror.Mirror, version: str, holding: str) -> list[str]:
    """The commands of the README fence holding `holding`, as release `version` printed it."""
    return docsnip.commands(fence_holding("README.md", holding, base=old_page(box, mirror, version)))


def install_old_plugin(box, version: str, cwd: Path) -> gitmirror.Mirror:
    """CLI and plugin at release `version`, each from the lines that release printed."""
    cli_venv(box, spec=f"crapkit=={version}")
    mirror = github(box, at=version)
    harness_on_path(box)
    run_lines(box, old_lines(box, mirror, version, "claude plugin install"), cwd=cwd)
    assert installed(box)["version"] == version
    return mirror


def upgrade_both(box, mirror: gitmirror.Mirror, candidate, cwd: Path):
    """The release lands, the CLI upgrades first, then the README update lines run.
    Returns what doctor printed between the two."""
    mirror.publish(candidate.staged, candidate.version)
    upgrade_cli(box)
    gap = doctor_plugin(box, cwd=cwd)
    steps = run_lines(box, page_lines(CLAUDE, index=1), cwd=cwd)
    return gap, steps


@cell("lin-up-claude-plugin-0.7.6", channel="Claude marketplace", harness="Claude Code",
      scenario="upgrade: mirror moves; CLI first; version-gap text; README update lines; doctor 0; plugin list --json "
      "version", use_cases="plugin upgrade", os="linux", image="core", cadence="push")
def test_claude_plugin_upgrade_from_0_7_6(box, candidate):
    repo = plain_repo(box)
    mirror = install_old_plugin(box, "0.7.6", repo)
    gap, steps = upgrade_both(box, mirror, candidate, repo)

    assert gap.exit == 1
    assert (f"is version 0.7.6, and the crapkit its hooks spawn ({box.which('crapkit')}) is {candidate.version}"
            in gap.stdout)
    assert steps[-1].exit == 0
    assert installed(box)["version"] == candidate.version
    assert "Connected" in mcp_line(box, repo)


def server_command(entry: dict) -> list[str]:
    server = entry["mcpServers"]["crapkit"]
    return [server["command"], *server.get("args", [])]


@cell("lin-up-claude-plugin-0.4.2", channel="Claude marketplace", harness="Claude Code",
      scenario="upgrade: python -m launcher to `crapkit mcp`", use_cases="plugin upgrade", os="linux", image="core",
      cadence="nightly")
def test_claude_plugin_upgrade_from_0_4_2(box, candidate):
    repo = plain_repo(box)
    mirror = install_old_plugin(box, "0.4.2", repo)
    before = server_command(installed(box))
    connected_before = mcp_line(box, repo)
    upgrade_both(box, mirror, candidate, repo)

    assert before == ["python", "-m", "crapkit", "mcp"] and "Connected" in connected_before
    assert server_command(installed(box)) == ["crapkit", "mcp"]
    assert "Connected" in mcp_line(box, repo)


def rename_table() -> dict[str, str]:
    """CHANGELOG's 0.5.x to 0.6.0 tool names, as its table prints them."""
    text = (docsnip.root() / "CHANGELOG.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| `(\w+)` \| `(\w+)` \|$", text, re.MULTILINE))


def served_tools(box) -> list[str]:
    """The tools the `crapkit mcp` on PATH lists."""
    from kit.mcp_client import McpClient
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=box.root) as client:
        client.initialize()
        names = [tool["name"] for tool in client.tools()]
        client.close()
    return names


def edit_settings(box, change) -> None:
    """Claude Code's user settings, read, changed by `change` and written back:
    the plugin install keeps its enabledPlugins entry there."""
    path = box.home / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    change(settings)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def allow(box, names: list[str]) -> None:
    """A user's Claude settings that pre-approve the plugin's tools by id."""
    ids = [f"mcp__plugin_crapkit_crapkit__{name}" for name in names]
    edit_settings(box, lambda settings: settings.setdefault("permissions", {}).setdefault("allow", []).extend(ids))


@cell("lin-up-claude-plugin-0.5.1", channel="Claude marketplace", harness="Claude Code",
      scenario="upgrade: 0.5.x allowlist names lead to the rename table", use_cases="plugin upgrade, MCP renames",
      os="linux", image="core", cadence="nightly")
def test_claude_plugin_upgrade_from_0_5_1_allowlist(box, candidate):
    repo = plain_repo(box)
    mirror = install_old_plugin(box, "0.5.1", repo)
    old = served_tools(box)
    allow(box, old)
    upgrade_both(box, mirror, candidate, repo)
    offered = offered_tools(box, repo)
    table = rename_table()

    assert sorted(old) == sorted(table)
    assert {f"mcp__plugin_crapkit_crapkit__{table[name]}" for name in old} <= set(offered)
    assert not {f"mcp__plugin_crapkit_crapkit__{name}" for name in old} & set(offered)
