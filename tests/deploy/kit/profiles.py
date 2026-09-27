"""Harness profiles: what each agent harness does when it starts crapkit.

One TOML file per harness under tests/deploy/profiles/, named by its key. The
sections every profile carries:

  [doc]         where docs/harnesses.md configures it: the heading (equal to
                `name`), the config format, the file a user writes and the
                config the profile holds until that page carries the section
  [spawn]       the working directory, the environment and how the command is
                resolved when the harness starts `crapkit mcp`
  [initialize]  the protocol revision it offers, whether it asks
                `server/discover` first, and the client a sim cell drives it with
  [limits]      what it keeps of one tool result, and its tool-name prefix
  [hooks]       whether it loads the plugin's hooks/hooks.json, the handler
                fields it keeps, the events and matchers it honours, and what
                a handler's exit 2 means to it
  [real_cli]    the pinned CLI a real cell runs (pins.toml key, version, floor),
                or none
  [evidence]    one tag per field above: observed (a cell or calibrate.py saw
                it), source (read in the harness's own code), docs (its
                documentation says so) or inferred (nobody has checked)

    profile = profiles.load("codex")
    profile.spawn["cwd"], profile.tag("spawn.cwd")

The second half of this module is the user side a harness cell sets up once:
crapkit installed from the candidate the way the README's 60-second start
installs it, and a repo it has measured.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import socket
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import hang_guard
from e2e import repo_templates
from kit import clock, repos, shim, stub_anthropic, stub_gemini, stub_openai, wheels, writers
from kit.transcript import Step

PROFILES = Path(__file__).resolve().parents[1] / "profiles"
SECTIONS = ("doc", "spawn", "initialize", "limits", "hooks", "real_cli", "evidence")
TAGGED = ("spawn", "initialize", "limits", "hooks")
TAGS = ("observed", "source", "docs", "inferred")
WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Profile:
    key: str
    data: dict

    @property
    def name(self) -> str:
        return self.data["name"]

    def __getattr__(self, section: str) -> dict:
        if section in SECTIONS:
            return self.data[section]
        raise AttributeError(section)

    @property
    def mcp(self) -> bool:
        """False for a harness that runs crapkit as a command, not as a server (Aider)."""
        return self.data.get("mode", "mcp") == "mcp"

    def tag(self, field: str) -> str:
        return self.data["evidence"].get(field, "")

    def inferred(self, fields: list[str] | None = None) -> list[str]:
        """The fields, among `fields` (default: every tagged one), that nobody has checked."""
        names = fields if fields is not None else tagged_fields(self.data)
        return [name for name in names if self.tag(name) == "inferred"]


def load(key: str, root: Path = PROFILES) -> Profile:
    data = tomllib.loads((root / f"{key}.toml").read_text(encoding="utf-8"))
    return Profile(key, data)


def keys(root: Path = PROFILES) -> list[str]:
    return sorted(path.stem for path in root.glob("*.toml"))


def all_profiles(root: Path = PROFILES) -> list[Profile]:
    return [load(key, root) for key in keys(root)]


def tagged_fields(data: dict) -> list[str]:
    """Every "section.field" the [evidence] table must tag."""
    return [f"{section}.{field}" for section in TAGGED for field in data.get(section, {})]


def problems(profile: Profile) -> list[str]:
    """What keeps a profile from being read by the cells: a missing section, a
    field with no evidence tag or an unknown one, a heading that is not the name."""
    missing = [f"{profile.key}: no [{section}]" for section in SECTIONS if section not in profile.data]
    return missing or _untagged(profile) + _unknown_tags(profile) + _heading_problem(profile)


def _untagged(profile: Profile) -> list[str]:
    return [f"{profile.key}: {name} has no evidence tag" for name in tagged_fields(profile.data)
            if not profile.tag(name)]


def _unknown_tags(profile: Profile) -> list[str]:
    tags = {name: tag for name, tag in profile.evidence.items() if isinstance(tag, str)}
    return [f"{profile.key}: {name} is tagged {tag!r}" for name, tag in tags.items() if tag not in TAGS]


def _heading_problem(profile: Profile) -> list[str]:
    heading = profile.doc.get("heading")
    return [] if heading == profile.name else [f"{profile.key}: [doc] heading {heading!r} is not {profile.name!r}"]


# --- how a harness starts the server ---------------------------------------------------
# [spawn] cwd: workspace (the repo), launch (the directory the harness was
# started in: a sim cell starts every harness in the repo, a user in a
# subdirectory gets the subdirectory), home, root (the filesystem root), config
# (the config's own cwd, else home); plugin_cwd, where set, is where a plugin's
# server starts instead (plugin: its install directory). env: inherit, sdk-default (the MCP
# TypeScript SDK's getDefaultEnvironment), allowlist (env_allow, or
# env_allow_windows on Windows). resolve / windows: execvp and cross-spawn
# (PATH, with PATHEXT on Windows), createprocess (PATH, .exe only), cwd-first
# (the working directory before PATH), shell (sh -c, or cmd /c), powershell-c
# (powershell -Command without -NoProfile), none (no build for this OS). path,
# where set: login-shell, for a harness that takes PATH from the user's login
# shell rather than its own environment (Zed; VS Code started from the desktop),
# so a crapkit only a venv holds is not found; the GUI cells act on it.

SDK_DEFAULT = {"posix": ["HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"],
               "nt": ["APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PROCESSOR_ARCHITECTURE",
                      "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "USERNAME", "USERPROFILE", "PROGRAMFILES"]}
WORKSPACE_TOKENS = ("${workspaceFolder}", "${workspaceRoot}")


@dataclass(frozen=True)
class Launch:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    rule: str


def launch_cwd(profile: Profile, server, box, repo: Path) -> Path:
    rule = profile.spawn["cwd"]
    if rule == "config" and server.cwd:
        spelled = server.cwd
        for token in WORKSPACE_TOKENS:
            spelled = spelled.replace(token, str(repo))
        return Path(spelled)
    return {"workspace": repo, "launch": repo, "home": box.home, "root": Path(repo.anchor), "config": box.home}[rule]


def _folded(name: str) -> str:
    """An environment name as the OS compares it: without case on Windows."""
    return name.upper() if WINDOWS else name


def _picked(env: dict[str, str], names: list[str]) -> dict[str, str]:
    """env's entries named in `names`."""
    wanted = {_folded(name) for name in names}
    return {key: value for key, value in env.items() if _folded(key) in wanted}


def allowed_names(profile: Profile) -> list[str]:
    rule = profile.spawn["env"]
    if rule == "sdk-default":
        return SDK_DEFAULT[os.name]
    return profile.spawn.get("env_allow_windows" if WINDOWS else "env_allow", [])


def launch_env(profile: Profile, server, box) -> dict[str, str]:
    """The environment the server starts with: the harness's rule applied to
    the harness's own environment, then the config's env and forwarded names."""
    base = dict(box.env) if profile.spawn["env"] == "inherit" else _picked(box.env, allowed_names(profile))
    forwarded = _picked(box.env, list(server.env_vars))
    return {**base, **forwarded, **server.env}


def _search(names: list[str], dirs: list[str]) -> str | None:
    for directory in dirs:
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return None


def _with_extensions(command: str, env: dict[str, str]) -> list[str]:
    extensions = _picked(env, ["PATHEXT"]).get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    return [command] + [command + ext.lower() for ext in extensions.split(";") if ext]


def _candidates(command: str, rule: str, env: dict[str, str]) -> list[str]:
    """The file names a lookup tries: PATHEXT's list on Windows, .exe only for CreateProcess."""
    if not WINDOWS:
        return [command]
    if rule == "createprocess":
        return [command if command.lower().endswith(".exe") else command + ".exe"]
    return _with_extensions(command, env)


def search_dirs(rule: str, env: dict[str, str], cwd: Path) -> list[str]:
    """PATH's directories in order, the working directory first for cwd-first."""
    dirs = [part for part in _picked(env, ["PATH"]).get("PATH", "").split(os.pathsep) if part]
    return [str(cwd), *dirs] if rule == "cwd-first" else dirs


def resolved(command: str, rule: str, env: dict[str, str], cwd: Path) -> str:
    """The file the rule starts, or an AssertionError naming the dirs searched."""
    if os.path.dirname(command):
        return command
    dirs = search_dirs(rule, env, cwd)
    found = _search(_candidates(command, rule, env), dirs)
    if found is None:
        raise AssertionError(f"{rule} finds no {command!r} in {dirs}")
    return found


def _quoted(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if WINDOWS else shlex.join(argv)


SHELLS = {"shell": lambda line: ["cmd.exe", "/d", "/s", "/c", line] if WINDOWS else ["sh", "-c", line],
          "powershell-c": lambda line: ["powershell.exe", "-Command", line]}


def launch(profile: Profile, server, box, repo: Path) -> Launch:
    """How this harness starts the server the config names, on this OS."""
    rule = profile.spawn["windows" if WINDOWS else "resolve"]
    cwd, env = launch_cwd(profile, server, box, repo), launch_env(profile, server, box)
    if rule in SHELLS:
        return Launch(SHELLS[rule](_quoted(server.argv)), cwd, env, rule)
    command = resolved(server.command, rule, env, cwd)
    return Launch([command, *server.args], cwd, env, rule)


# --- the user side --------------------------------------------------------------------

CONTAINER_KEY = "container_ok = true"


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def install_crapkit(box, venv: Path, python: str = "3.12", spec: str = "crapkit[py]") -> Path:
    """`pip install crapkit` into `venv`, the README's 60-second start; the
    [py] extra brings pytest-cov for the repo's coverage lane. Returns the launcher."""
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.run([str(scripts_dir(venv) / "python"), "-m", "pip", "install", "-q", spec], expect=0)
    return launcher(venv)


def launcher(venv: Path) -> Path:
    return scripts_dir(venv) / ("crapkit.exe" if WINDOWS else "crapkit")


def session_crapkit(box, cache: Path) -> Path:
    """The candidate installed once per session, first on this sandbox's PATH.
    A venv records its own absolute path, so cells share it rather than copy it."""
    venv = cache / "harness-venv"
    with repos.file_lock(cache / "harness-venv.lock"):
        if not (venv / "ready").exists():
            install_crapkit(box, venv)
            (venv / "ready").write_text("", encoding="utf-8")
    box.prepend_path(scripts_dir(venv))
    return launcher(venv)


def measured_repo(box, cache: Path) -> Path:
    """This cell's copy of py-pytest, adopted and measured once per session by
    the session's crapkit: every MCP tool answers from it."""
    session_crapkit(box, cache)
    (cache / "measured").mkdir(parents=True, exist_ok=True)
    with repos.file_lock(cache / "measured.lock"):
        built = repo_templates.template(cache / "measured" / "staging", "measured-py-pytest",
                                        lambda repo: _measured(box, cache, repo))
    return repo_templates.copy_of(built, box.root / "repo")


def sdk_fixtures(box, cache: Path) -> Path:
    """The npm-fixtures install mcp_node_client.mjs reads its SDK aliases from:
    the toolchain's own when toolchain.py already installed it (native runs),
    else the kit's once-per-session install (the images keep only the lock)."""
    installed = Path(box.toolchain["npm_fixtures"])
    if (installed / "node_modules" / "sdk-1-12" / "package.json").is_file():
        return installed
    return repos.npm_fixtures(box, cache)


def _measured(box, cache: Path, repo: Path) -> None:
    repo_templates.copy_of(repos.built(box, "py-pytest", cache), repo)
    measure(box, repo)


def allow_container_lane(repo: Path) -> None:
    """docs/lanes.md#containers: the key a coverage.py lane needs in a container."""
    config = repo / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("[[lane]]\n", f"[[lane]]\n{CONTAINER_KEY}\n", 1), encoding="utf-8")


def commit(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def measure(box, repo: Path) -> None:
    """crapkit init, then coverage, then a commit: the two commands every MCP
    tool needs first. In a container the lane gets the documented key."""
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    if in_container():
        allow_container_lane(repo)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    commit(box, repo, "adopt crapkit")


# --- the real CLIs, headless ------------------------------------------------------------
# A real cell runs the pinned harness binary. crapkit comes first on PATH through
# the shim (kit/shim.py), so each start is recorded; the harness CLIs follow.
# Harnesses that need a model talk to a scripted stub (kit/stub_openai.py,
# kit/stub_anthropic.py) that calls one crapkit tool and records every request.

# Switches the sandbox's QUIET table lacks: Junie is a JVM app that reads the
# account's home from the OS unless JUNIE_HOME names one.
REAL_CLI_ENV = {"COPILOT_OFFLINE": "true", "CRUSH_DISABLE_PROVIDER_AUTO_UPDATE": "1", "CRUSH_DISABLE_METRICS": "1"}
PROMPT = "Use the crapkit tools to answer."


def real_cli_box(box, cache: Path) -> Path:
    """A measured repo, the candidate behind the shim, the harness CLIs on PATH."""
    repo = measured_repo(box, cache)
    real = session_crapkit(box, cache)
    box.prepend_path(shim.install(box, str(real)))
    add_harnesses(box)
    return repo


def last_start(box) -> dict:
    """The shim's record of the newest crapkit the harness started."""
    starts = shim.starts(box)
    assert starts, "the harness never started crapkit"
    return starts[-1]


def _message(line: str | None) -> dict:
    try:
        return json.loads(line) if line else {}
    except ValueError:
        return {}


def initialize_params(start: dict) -> dict:
    """The params of the initialize the harness sent, after any server/discover
    (the shim records the client's lines up to it), or {} when none came."""
    messages = [_message(line) for line in start.get("lines") or [start.get("first_line")]]
    return next((message.get("params", {}) for message in messages if message.get("method") == "initialize"), {})


# On Windows, `npm ci` of the harness locks leaves no .bin shim for cline:
# cline and its platform package both declare the bin name, and the platform
# package's cline.exe is the binary cline's own launcher runs. A global
# `npm i -g cline` nests that package and links cline.cmd.
PLATFORM_BINS = {"nt": [Path("@cline") / "cli-windows-x64" / "bin"]}


def _unlinked(bin_dir: Path) -> list[Path]:
    """The platform binary directories npm left without a shim next to one .bin directory."""
    return [bin_dir.parent / relative for relative in PLATFORM_BINS.get(os.name, [])]


def harness_dirs(box) -> list[str]:
    """The toolchain's harness bin directories, and the platform binaries npm left unlinked."""
    listed = [Path(directory) for directory in box.toolchain.get("harness_bin", [])]
    found = listed + sum((_unlinked(bin_dir) for bin_dir in listed), [])
    return [str(directory) for directory in found if directory.is_dir()]


def add_harnesses(box) -> None:
    """The pinned harness CLIs after everything on the sandbox PATH, and the
    switches that keep them offline and inside the sandbox."""
    box.env["PATH"] = os.pathsep.join([*box.path_dirs(), *harness_dirs(box)])
    box.env.update(REAL_CLI_ENV, JUNIE_HOME=str(box.home / ".junie"))


def doc_server(box, repo: Path, key: str) -> writers.Server:
    """The profile's doc config written where the harness reads it; the server it names."""
    profile = load(key)
    text, source = writers.doc_config(profile)
    path = writers.write(profile, text, box, repo)
    box.transcript.note(f"{profile.name}: config from {source}, written to {path}")
    return writers.parse(profile, path)


def offered_tools(body: dict) -> list[str]:
    """The tool names a model request offers, in any of the four API shapes."""
    return [name for tool in body.get("tools") or [] for name in _tool_names(tool)]


def _tool_names(tool: dict) -> list[str]:
    """One entry of a request's tools: a Gemini group of functionDeclarations,
    or one tool named at the top or under `function`."""
    if "functionDeclarations" in tool:
        return [declaration.get("name", "") for declaration in tool["functionDeclarations"]]
    return [tool.get("name") or tool.get("function", {}).get("name", "")]


# How each API asks for a tool call: the turn key a stub reads, and the field of its arguments.
CALL_SHAPES = {"openai": ("tool_call", "arguments"), "anthropic": ("tool_use", "input"),
               "gemini": ("function_call", "args")}


class ToolCall:
    """A scripted model: each request that offers crapkit's tools gets the next
    call in `calls` (a tool-name suffix and its arguments); once they are all
    out, a request gets text, which ends the turn."""

    def __init__(self, kind: str, calls: list[tuple[str, dict]], watch=None):
        self.kind, self.pending = kind, list(calls)
        self.offered: list[str] = []
        self.called: list[str] = []
        self.watch = watch

    def __call__(self, body: dict, turn: int) -> dict:
        if self.watch is not None:
            self.watch(body)
        names = offered_tools(body)
        self.offered = self.offered or names
        name = self._target(names)
        if name is None:
            return {"text": "done"}
        self.called.append(name)
        return self._call(name, self.pending.pop(0)[1])

    def _target(self, names: list[str]) -> str | None:
        """The offered tool the next pending call names, if any is pending."""
        if not self.pending:
            return None
        return next((name for name in names if crapkit_tool(name) == self.pending[0][0]), None)

    def _call(self, name: str, arguments: dict) -> dict:
        turn, field = CALL_SHAPES[self.kind]
        return {turn: {"name": name, field: arguments}}

    def crapkit_tools(self) -> list[str]:
        return [name for name in self.offered if crapkit_tool(name)]


# A crapkit tool as a harness names it: the bare name, or the name after a
# prefix that names the server (mcp__crapkit__, crapkit__, crapkit-). Cline's
# own team_list_runs ends like list_runs and is not one.
TOOL_NAME = re.compile(r"(?:^|crapkit[_-]+)(get_next_item|list_worklist|list_runs|get_trend|get_function_brief|"
                       r"get_function_history|check_config|list_coupled_files|list_duplicate_functions|"
                       r"get_ratchet_report|check_gate|list_claims)$")


def crapkit_tool(name: str) -> str | None:
    """The crapkit tool a harness's tool name stands for, or None."""
    match = TOOL_NAME.search(name)
    return match[1] if match else None


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [part.get("text", "") for part in content or [] if isinstance(part, dict)]


def _blocks(item: dict) -> list[dict]:
    content = item.get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _anthropic_results(item: dict) -> list[str]:
    blocks = [block for block in _blocks(item) if block.get("type") == "tool_result"]
    return [text for block in blocks for text in _texts(block.get("content"))]


def _gemini_results(item: dict) -> list[str]:
    """The functionResponse parts of a Gemini content, each as its text."""
    return [_output_text(part["functionResponse"].get("response")) for part in item.get("parts") or []
            if isinstance(part, dict) and "functionResponse" in part]


def _output_text(answer) -> str:
    """A functionResponse's `output` text where it holds one, else the response as JSON."""
    output = answer.get("output") if isinstance(answer, dict) else None
    return output if isinstance(output, str) else json.dumps(answer)


def _result_of(item: dict) -> list[str]:
    """A tool result in a chat message, a Responses input item, an Anthropic
    block or a Gemini content."""
    if item.get("role") == "tool":
        return _texts(item.get("content"))
    if item.get("type") == "function_call_output":
        return _texts(item.get("output"))
    if "parts" in item:
        return _gemini_results(item)
    return _anthropic_results(item)


def _items(body: dict) -> list[dict]:
    listed = body.get("messages") or body.get("input") or body.get("contents")
    return _dicts(listed) if isinstance(listed, list) else []


def _dicts(listed: list) -> list[dict]:
    return [item for item in listed if isinstance(item, dict)]


def tool_results(bodies: list[dict]) -> list[str]:
    """Every tool result text the harness sent back to the model, oldest first."""
    texts = [text for body in bodies for item in _items(body) for text in _result_of(item)]
    return list(dict.fromkeys(texts))


# --- one headless session per harness that needs a model -----------------------------
# Each writes the profile's doc config where the harness reads it, points the
# harness at the stub, and returns the argv and extra env of one headless run.

STUB_MODEL = "gpt-4.1"
STUB_KEY = "sk-deploy-cell"


def parsed_doc(key: str, repo: Path) -> writers.Server:
    """The server the profile's doc config names, without writing it anywhere."""
    profile = load(key)
    text, _ = writers.doc_config(profile)
    return writers.FORMATS[profile.doc["format"]](writers.filled(text, repo))


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def session_claude(box, repo: Path, url: str):
    server = parsed_doc("claude-code", repo)
    box.run(["claude", "mcp", "add", "-s", "local", "crapkit", "--", *server.argv], cwd=repo, expect=0)
    return (["claude", "-p", PROMPT, "--allowedTools", "mcp__crapkit"],
            stub_anthropic.claude_env(url, "sk-ant-deploy-cell"))


def session_opencode(box, repo: Path, url: str):
    doc_server(box, repo, "opencode")
    provider = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
                "provider": {"openai": {"options": {"baseURL": url + "/v1", "apiKey": STUB_KEY},
                                        "models": {STUB_MODEL: {}}}}, "model": f"openai/{STUB_MODEL}"}
    config = _write_json(box.root / "opencode-provider.json", provider)
    return ["opencode", "run", PROMPT], {"OPENCODE_CONFIG": str(config)}


def session_goose(box, repo: Path, url: str):
    doc_server(box, repo, "goose")
    return (["goose", "run", "--no-session", "-t", PROMPT],
            {"GOOSE_PROVIDER": "openai", "GOOSE_MODEL": STUB_MODEL, "OPENAI_HOST": url, "OPENAI_API_KEY": STUB_KEY,
             "OPENAI_BASE_PATH": "v1/chat/completions"})


def free_port() -> int:
    """A local port nothing listens on now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def session_cline(box, repo: Path, url: str):
    # Cline's hub listens on 25463 unless CLINE_HUB_PORT names another port.
    # Sandboxes share the host's network, so without a port of its own one
    # sandbox's hub never came up beside another's, and a native run could
    # reach the hub of a Cline the machine's user runs.
    box.env.setdefault("CLINE_HUB_PORT", str(free_port()))
    doc_server(box, repo, "cline")
    box.run(["cline", "auth", "-p", "openai", "-k", STUB_KEY, "-m", STUB_MODEL, "-b", url + "/v1"], cwd=repo,
            expect=0)
    return ["cline", "--json", "-t", "120", PROMPT], {}


def session_continue(box, repo: Path, url: str):
    doc_server(box, repo, "continue")
    config = box.home / ".continue" / "config.yaml"
    models = (f"models:\n  - name: stub\n    provider: openai\n    model: {STUB_MODEL}\n    apiBase: {url}/v1\n"
              f"    apiKey: {STUB_KEY}\n    roles: [chat]\n")
    config.write_text(config.read_text(encoding="utf-8").rstrip("\n") + "\n" + models, encoding="utf-8")
    return ["cn", "-p", "--auto", PROMPT], {}


# Copilot CLI's first start extracts its 160 MB package into
# COPILOT_CACHE_HOME/pkg: 5 to 8 s in a container, 30 to 73 s on Windows, and
# 186 to 324 s on a loaded Windows machine, where the extraction alone ran past
# the kit's hang bound. The package is the CLI, not user state: each pytest
# worker extracts it once, under the install bound, and each sandbox links it.
COPILOT_WARM = "warm.done"


def link_dir(target: Path, link: Path) -> None:
    """`link` pointing at `target`: a junction on Windows, which needs no privilege."""
    if link.exists():
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def copilot_package(box) -> None:
    shared = box.root.parents[1] / "copilot-cache"
    if not (shared / COPILOT_WARM).is_file():
        step = bounded(box, ["copilot", "--version"], {"COPILOT_CACHE_HOME": str(shared)},
                       "extract Copilot's package once for this pytest worker")
        assert step.exit == 0, step.text()
        (shared / COPILOT_WARM).write_text(step.stdout, encoding="utf-8")
    link_dir(shared / "pkg", Path(box.env["COPILOT_CACHE_HOME"]) / "pkg")


def session_copilot(box, repo: Path, url: str):
    doc_server(box, repo, "copilot-cli")
    copilot_package(box)
    return (["copilot", "-p", PROMPT, "--allow-all-tools", "--no-auto-update"],
            {"COPILOT_PROVIDER_BASE_URL": url + "/v1", "COPILOT_MODEL": STUB_MODEL, "COPILOT_PROVIDER_API_KEY": STUB_KEY})


def session_crush(box, repo: Path, url: str):
    doc_server(box, repo, "crush")
    provider = {"options": {"disable_provider_auto_update": True, "disable_metrics": True},
                "providers": {"stub": {"type": "openai-compat", "base_url": url + "/v1", "api_key": STUB_KEY,
                                       "models": [{"id": STUB_MODEL, "name": "stub", "context_window": 128000,
                                                   "default_max_tokens": 4096}]}},
                "models": {"large": {"model": STUB_MODEL, "provider": "stub"},
                           "small": {"model": STUB_MODEL, "provider": "stub"}}}
    _write_json(Path(box.env["CRUSH_GLOBAL_CONFIG"]) / "crush.json", provider)
    return ["crush", "run", "--quiet", PROMPT], {}


def session_junie(box, repo: Path, url: str):
    doc_server(box, repo, "junie")
    model = {"baseUrl": url + "/v1/chat/completions", "id": STUB_MODEL, "apiType": "OpenAICompletion",
             "apiKey": STUB_KEY}
    _write_json(Path(box.env["JUNIE_HOME"]) / "models" / "stub.json", model)
    return ["junie", "--skip-update-check", "--model", "custom:stub", PROMPT], {}


GEMINI_MODEL = "gemini-2.5-flash"


def session_gemini(box, repo: Path, url: str):
    """gemini -p on the Gemini API stub. gemini-api-key auth sends the key to
    GOOGLE_GEMINI_BASE_URL (a base URL alone selects gateway auth, and -p then
    exits 41); the folder is trusted as a user trusts it at the prompt."""
    doc_server(box, repo, "gemini-cli")
    settings = box.home / ".gemini" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    data.setdefault("security", {}).setdefault("auth", {})["selectedType"] = "gemini-api-key"
    _write_json(settings, data)
    return (["gemini", "-m", GEMINI_MODEL, "-p", PROMPT],
            {"GOOGLE_GEMINI_BASE_URL": url, "GEMINI_API_KEY": "stub-placeholder", "GEMINI_CLI_TRUST_WORKSPACE": "true"})


SESSIONS = {"claude-code": ("anthropic", session_claude), "opencode": ("openai", session_opencode),
            "goose": ("openai", session_goose), "cline": ("openai", session_cline),
            "continue": ("openai", session_continue), "copilot-cli": ("openai", session_copilot),
            "crush": ("openai", session_crush), "junie": ("openai", session_junie),
            "gemini-cli": ("gemini", session_gemini)}
STUBS = {"openai": stub_openai.serve, "anthropic": stub_anthropic.serve, "gemini": stub_gemini.serve}


# A Cline task in its default backend (auto) finds no hub, runs locally and
# starts a hub daemon in the background. The hub outlives the CLI, and on
# Windows it keeps the sandbox's .cline/data/db files locked, so the next
# session cannot clear its basetemp. It took 54 s to come up on a loaded
# Windows machine, and `cline hub stop` finds it only once it has written its
# discovery file.

def cline_hub_file(box) -> Path:
    return Path(box.env["CLINE_DIR"]) / "data" / "locks" / "hub" / "production.json"


def stop_cline_hub(box) -> None:
    hang_guard.wait_for(cline_hub_file(box))
    step = box.run(["cline", "hub", "stop"], cwd=box.root, note="stop the hub the Cline task started")
    assert '"stopped":true' in step.stdout, step.text()


LEFT_RUNNING = {"cline": stop_cline_hub}


def stop_left_running(box, key: str) -> None:
    LEFT_RUNNING.get(key, lambda _box: None)(box)


def stub_session(box, repo: Path, key: str, calls: list[tuple[str, dict]], watch=None):
    """One headless run of the harness against a stub that makes `calls`;
    `watch` sees each request body as the model would, before the stub answers.
    Returns the script (what was offered and called), every request body and the run's step."""
    kind, configure = SESSIONS[key]
    script = ToolCall(kind, calls, watch)
    serve = STUBS[kind]
    with serve(script) as stub:
        argv, env = configure(box, repo, stub.url)
        step = box.run(argv, cwd=repo, env=env, note=f"{key} against the {kind} stub")
        stop_left_running(box, key)
    return script, stub.bodies(), step


# --- reading crapkit's answer back out of what a harness sent its model -----------------

def _decoded(text: str):
    """The JSON value that starts at the text's first bracket or brace."""
    start = min(position for position in (text.find("{"), text.find("[")) if position >= 0)
    return json.JSONDecoder().raw_decode(text[start:])[0]


def _wrapped(value):
    """The content list a harness wrapped crapkit's text in, or None."""
    if isinstance(value, list):
        return value
    return None if "schema" in value else value.get("content")


def _first_json(text: str):
    """The first JSON value in a tool result, with any wrapper a harness adds
    around crapkit's text unwrapped: a content list, or {"content": [...]}."""
    value = _decoded(text)
    wrapped = _wrapped(value)
    return _first_json(wrapped[0]["text"]) if isinstance(wrapped, list) else value


def result_json(text: str):
    """crapkit's JSON answer inside one tool result a harness sent its model, or
    None when the text holds none (a truncated or reworded result)."""
    try:
        return _first_json(text)
    except (ValueError, KeyError, TypeError, IndexError):
        return None


# --- what a real harness did: the observation calibrate-all holds to the profile ------------
# The shim's newest start says where the harness started crapkit, with what
# environment, and what it offered first; the tools a model was offered give
# the prefix. tools/deploy/calibrate.py compares these with the profile.

def cwd_rule(box, repo: Path, cwd: str) -> str:
    """A start directory as a [spawn] cwd value, or the directory itself."""
    return {str(repo): "workspace", str(box.home): "home", repo.anchor: "root"}.get(cwd, cwd)


def _folded_set(names) -> set[str]:
    return {_folded(name) for name in names}


def env_rule(box, names: set[str], configured: set[str]) -> str:
    """The [spawn] env value a start's environment matches: the SDK default set,
    most of the harness's own environment (inherit), or an allowlist. The
    names libfaketime adds under run.py --faketime belong to no rule."""
    own = _folded_set(names - configured - clock.added_env())
    if own <= _folded_set(SDK_DEFAULT[os.name]):
        return "sdk-default"
    return "inherit" if len(own & _folded_set(box.env)) >= 0.9 * len(box.env) else "allowlist"


def prefix(tools: list[str]) -> str | None:
    """The part of a model-facing tool name before crapkit's own name."""
    named = [(name, crapkit_tool(name)) for name in tools if crapkit_tool(name)]
    return named[0][0][: -len(named[0][1])] if named else None


def _offer(start: dict | None) -> dict:
    """What the harness sent first: initialize's revision and client, or server/discover."""
    first = json.loads(start["first_line"]) if start and start.get("first_line") else {}
    params = initialize_params(start) if first else {}
    return {"protocol": params.get("protocolVersion"), "discover_first": first.get("method") == "server/discover"
            if first else None, "client_name": params.get("clientInfo", {}).get("name")}


def _spawn(box, repo: Path, start: dict | None, server) -> dict:
    if not start:
        return {"cwd": None, "env": None}
    configured = set(server.env) | set(server.env_vars) if server else set()
    return {"cwd": cwd_rule(box, repo, start["cwd"]), "env": env_rule(box, set(start["env"]), configured)}


def _seen(starts: list[dict], version: str) -> dict:
    """What a reader of an observed file wants beside the compared fields."""
    start = starts[-1] if starts else {}
    return {"version": version, "starts": len(starts), "env": sorted(start.get("env", {})),
            "first_line": start.get("first_line")}


def observe(box, repo: Path, key: str, tools: list[str], version: str) -> dict:
    """calibrate-all's record of one harness: [spawn], [initialize], [limits] and [seen]."""
    starts = shim.starts(box)
    start = starts[-1] if starts else None
    server = parsed_doc(key, repo) if load(key).mcp else None
    return {"spawn": _spawn(box, repo, start, server), "initialize": _offer(start),
            "limits": {"tool_prefix": prefix(tools)}, "seen": _seen(starts, version)}


# Amp prints its release's age after the version, which changes every hour
# and would change a committed observed file on every run.
RELEASE_AGE = re.compile(r" \(released [^)]*\)$")


def harness_version(box, key: str) -> str:
    """The first line `<command> --version` prints, or what it printed on failure."""
    step = box.run([load(key).real_cli["command"], "--version"])
    lines = (step.stdout or step.stderr).strip().splitlines()
    return RELEASE_AGE.sub("", lines[0]) if lines else f"exit {step.exit}"


def calibrate_module():
    """tools/deploy/calibrate.py from the tree under test: the comparison the
    calibrate-all cell and the host command share."""
    spec = importlib.util.spec_from_file_location("calibrate", wheels.SRC / "tools" / "deploy" / "calibrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def out_dir() -> Path:
    """Where a run keeps its output (tests/deploy/conftest.py reads the same variable)."""
    return Path(os.environ.get("CRAPKIT_DEPLOY_OUT") or Path.cwd() / ".crapkit" / "deploy-out")


def write_observed(folder: str, key: str, text: str) -> Path:
    path = out_dir() / folder / f"{key}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- a harness at its newest release (weekly, network on) -----------------------------
# The npm harnesses install their package @latest; the others run the install
# line their own docs give. Both land in the sandbox home, first on PATH.

LATEST_SCRIPTS = {
    "cursor": "curl -fsSL https://cursor.com/install | bash",
    "goose": "curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash",
    # Aider's own install line: without --python, uv took its newest Python,
    # for which numpy 1.26.4 has no wheel, and the build found no compiler.
    "aider": "uv tool install --force --python python3.12 --with pip aider-chat@latest",
    "kiro": "curl -fsSL https://cli.kiro.dev/install | bash",
    "windsurf": "curl -fsSL https://cli.devin.ai/install.sh | bash",
    "qwen-code": "npm i -g @qwen-code/qwen-code@latest",
}
# The sandbox is offline by default: uv may reach PyPI for an install of the newest release.
ONLINE_ENV = {"UV_OFFLINE": "0", "UV_NO_INDEX": "0"}


def pins() -> dict:
    return tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))


def latest_script(key: str, pinned: dict) -> str:
    """The shell line that installs the harness's newest release."""
    spec = pinned["harness"].get(load(key).real_cli["pins_key"], {})
    return f"npm i -g {spec['npm']}@latest" if "npm" in spec else LATEST_SCRIPTS[key]


# A download of a newest release is bounded by the network, not by a hang: a
# global npm install of Qwen Code took two minutes, past the kit's 120 s bound.
INSTALL_SECONDS = 900


def bounded(box, argv: list[str], env: dict, note: str) -> Step:
    """Run argv under INSTALL_SECONDS, not the hang bound, and record it in the transcript."""
    return box.run(argv, cwd=box.root, env=env, note=note, bound=INSTALL_SECONDS)


def _install(box, key: str) -> Step:
    """Run the install line and record it in the transcript. npm resolves
    @latest as the registry stands now, not at the cache's npm_before."""
    script = box.tmp / f"install-{key}.sh"
    script.write_text(latest_script(key, pins()) + "\n", encoding="utf-8")
    box.env.pop("npm_config_before", None)
    return bounded(box, [*box.shell("sh"), str(script)], ONLINE_ENV, f"{key} at its newest release")


def install_latest(box, key: str) -> None:
    """The harness's newest release in the sandbox home, ahead of the pinned one on
    PATH. An installer may exit non-zero after installing (Devin's asks for a
    login it cannot get); the command being on PATH is what counts."""
    step = _install(box, key)
    for directory in (box.home / ".local" / "bin", Path(box.env["npm_config_prefix"]) / "bin"):
        box.prepend_path(directory)
    command = load(key).real_cli["command"]
    assert box.which(command), f"{key}: the install put no {command} on PATH\n{step.text()}"
