# Wiring crapkit into your agent

crapkit's MCP server is `crapkit mcp`: a stdio server with twelve read-side tools
([the MCP contract](agent-json.md#mcp-server)). Every agent below starts it from its own
config file. Find your agent's section, paste its first block into the file the section
names, replace `/absolute/path/to/your/repo` with the repository you scored, and start a
new session.

Three rules hold for every agent:

- The agent has to find `crapkit` on the PATH it starts processes with. A desktop app does
  not always get your shell's PATH. When the agent reports that it cannot start
  `crapkit`, put the absolute path `command -v crapkit` prints (`where crapkit` on
  Windows) in place of the bare name.
- `--repo` names the repository the server scores, wherever the agent starts it. Each tool
  also takes a `repo` argument, so one server can answer for several checkouts.
- The tools read a scored run. Run `crapkit init` and `crapkit coverage` in the repository
  first; before that every tool answers with the missing-config or no-run result.

The Claude Code plugin's advisory hook is a Claude Code hook. The other agents either do
not load it or load it wrongly, and each section says which. The commit gate
([README: the gate](../README.md#the-gate)) is the check every agent shares, because git
runs it.

| Agent | Config file | crapkit's advisory hook |
|---|---|---|
| [Claude Code](#claude-code) | the plugin, `.mcp.json` or `~/.claude.json` | runs, from the plugin |
| [Claude Desktop](#claude-desktop) | `claude_desktop_config.json` | none |
| [Claude Agent SDK](#claude-agent-sdk) | `options.mcpServers` in code | runs when the plugin is loaded |
| [claude-code-action](#claude-code-action) | a file `claude_args` names | none |
| [Codex](#codex) | `~/.codex/config.toml` | none: the plugin's Codex manifest keeps it out |
| [Cursor](#cursor) | `.cursor/mcp.json` | none from this config; see the section |
| [Windsurf](#windsurf) | `~/.codeium/windsurf/mcp_config.json` | none |
| [VS Code with GitHub Copilot](#vs-code-with-github-copilot) | `.vscode/mcp.json` | none; do not add the plugin as a VS Code agent plugin |
| [GitHub Copilot CLI](#github-copilot-cli) | `~/.copilot/mcp-config.json` | none; do not install the plugin with `copilot plugin install` |
| [Copilot cloud agent](#copilot-cloud-agent) | the repository's Copilot settings | none |
| [Kiro](#kiro) | `.kiro/settings/mcp.json` | none |
| [Gemini CLI](#gemini-cli) | `~/.gemini/settings.json` | none |
| [Qwen Code](#qwen-code) | `~/.qwen/settings.json` | none |
| [OpenCode](#opencode) | `opencode.json` | none |
| [Goose](#goose) | `~/.config/goose/config.yaml` | none |
| [Amp](#amp) | `~/.config/amp/settings.json` | none |
| [Crush](#crush) | `crush.json` | none |
| [oh-my-pi](#oh-my-pi) | `.omp/mcp.json` | none |
| [Cline](#cline) | `cline_mcp_settings.json` | none |
| [Roo Code](#roo-code) | `.roo/mcp.json` | none |
| [Kilo Code](#kilo-code) | `.kilocode/mcp.json` | none |
| [Continue](#continue) | `~/.continue/config.yaml` | none |
| [Zed](#zed) | Zed's `settings.json` | none |
| [JetBrains AI Assistant](#jetbrains-ai-assistant) | Settings, Model Context Protocol (MCP) | none |
| [Junie](#junie) | `~/.junie/mcp/mcp.json` | none |
| [Aider](#aider) | `.aider.conf.yml` (a linter, not MCP) | none |
| [Antigravity](#antigravity) | `~/.gemini/antigravity/mcp_config.json` | none |

"Measured" below means the deploy suite ran that release with a recording `crapkit` in
front of the real one and read what the agent passed it.

## Claude Code

```json
{
  "mcpServers": {
    "crapkit": {
      "type": "stdio",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Claude Code |
|---|---|
| Config file | The plugin carries this server already: `claude plugin install crapkit@crapkit` ([README](../README.md#the-claude-code-plugin)). Without the plugin, the block goes in `.mcp.json` at the repository root, or `claude mcp add --scope user crapkit -- crapkit mcp --repo /absolute/path/to/your/repo` writes it into `~/.claude.json`. A user entry named `crapkit` hides the plugin's server. |
| Starts in | The directory Claude Code runs in (measured, 2.1.281). A project `.mcp.json` can drop `--repo`. |
| Environment | Claude Code's own environment, plus `CLAUDE_PROJECT_DIR` and `CLAUDECODE` (measured). `env` adds variables. |
| Versions | The plugin's hook needs 2.1.139 or later. Older releases ignore the hook's `args`, run a bare `crapkit`, and hand its exit-2 usage error to the model after every matched edit; `crapkit doctor --plugin-root` names a Claude Code below that. The deploy suite runs 2.1.281. |
| Plugin hooks | Runs the advisory PostToolUse hook on `Edit` and `Write`. Add the `Bash` entry yourself ([README](../README.md#the-claude-code-plugin)). |
| After an upgrade | `claude plugin marketplace update crapkit`, then `claude plugin update crapkit@crapkit --scope user`, then restart the session. A server from `.mcp.json` restarts with the session, or from `/mcp`. |

## Claude Desktop

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Claude Desktop |
|---|---|
| Config file | `claude_desktop_config.json`, opened from Settings, Developer, Edit Config: `~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows. An MSIX install keeps it under `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`. |
| Starts in | A directory of the app's choosing, not your repository, so `--repo` is required. |
| Environment | A short fixed list, not your shell's: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER` on macOS and Linux. If the app cannot start `crapkit`, write its absolute path as `command`. |
| Versions | Any release with local MCP servers. The deploy suite has no headless check for it. |
| Plugin hooks | This config adds the tools only; the advisory hook needs Claude Code. |
| After an upgrade | Quit Claude Desktop and open it again. |

## Claude Agent SDK

```json
{
  "mcpServers": {
    "crapkit": {
      "type": "stdio",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

The `mcpServers` object goes into the options of each query:

```ts
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Call get_next_item and say which function to fix first.",
  options: {
    mcpServers: {
      crapkit: { type: "stdio", command: "crapkit", args: ["mcp", "--repo", "/absolute/path/to/your/repo"] },
    },
  },
})) {
  console.log(message);
}
```

| | Claude Agent SDK |
|---|---|
| Config file | `options.mcpServers` in TypeScript; `ClaudeAgentOptions(mcp_servers={"crapkit": {...}})` in Python, with the same keys. |
| Starts in | `options.cwd`, else the process's working directory. |
| Environment | `options.env` replaces the environment the SDK hands Claude Code and the server. Keep `PATH` in it, or `crapkit` is not found. |
| Versions | The deploy suite runs the TypeScript SDK 0.3.281 and the Python SDK 0.2.159. |
| Plugin hooks | `plugins: [{ type: "local", path }]` with the installed plugin's directory loads the hook as Claude Code does. |
| After an upgrade | The next `query()` starts a new server. |

## claude-code-action

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

Save it as `.github/crapkit-mcp.json` and name it in the action's `claude_args`. The
path is the checkout the job runs in, `/home/runner/work/REPO/REPO` for a repository
named `REPO` on a GitHub-hosted runner. Earlier steps install crapkit and score the
checkout:

```yaml
steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
  - run: pip install -e ".[dev]" crapkit   # what your lanes need, and crapkit
  - run: crapkit coverage
  - uses: anthropics/claude-code-action@v1
    with:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      claude_args: --mcp-config .github/crapkit-mcp.json --allowedTools mcp__crapkit
```

| | claude-code-action |
|---|---|
| Config file | The file `--mcp-config` names in the step's `claude_args`. `--allowedTools mcp__crapkit` lets the model call the twelve tools without a permission prompt, which a workflow run has no one to answer. |
| Starts in | The checkout the job runs in. |
| Environment | The job's environment. A job with a `container:` runs in a container, where a coverage.py lane refuses to start ([docs: containers](lanes.md#containers)). |
| Versions | `@v1`. |
| Plugin hooks | None: this config adds the tools only. |
| After an upgrade | Every run installs crapkit afresh. Pin the version in the install step, the way [Route 4](../README.md#route-4-ci) pins it. |

## Codex

```toml
[mcp_servers.crapkit]
command = "crapkit"
args = ["mcp", "--repo", "/absolute/path/to/your/repo"]
```

| | Codex |
|---|---|
| Config file | `~/.codex/config.toml`. `codex mcp add crapkit -- crapkit mcp --repo /absolute/path/to/your/repo` writes the same table. The plugin carries this server as well ([README: Codex](../README.md#codex)). |
| Starts in | The thread's working directory (measured, 0.156.1), or the table's `cwd`. |
| Environment | Only the variables on Codex's allowlist, when they are set: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR` and `TZ` on Linux and macOS. An activated virtualenv's `VIRTUAL_ENV` does not reach the server (measured, 0.156.1). `env = { KEY = "value" }` sets more, and `env_vars = ["VIRTUAL_ENV"]` passes named variables through. |
| Versions | The README's two plugin lines need 0.131.0 or later: 0.130.0 has no `codex plugin add` (measured). The config block needs no plugin support. The deploy suite runs 0.156.1. |
| Plugin hooks | None. The plugin's `.codex-plugin/plugin.json` empties `hooks`, because Codex keeps only each entry's `command` from Claude Code's `hooks/hooks.json` and would run a bare `crapkit` after every edit. A plugin from 0.8.0 or earlier has no Codex manifest, and Codex lists its hooks as untrusted: leave them so. |
| After an upgrade | Start a new thread. Codex upgrades configured git marketplaces when it starts, so upgrade the CLI first ([docs: upgrading](upgrading.md#plugin-and-mcp-clients)). |

## Cursor

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Cursor |
|---|---|
| Config file | `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for every project. The agent CLI needs `cursor-agent mcp enable crapkit` once; `cursor-agent mcp list-tools crapkit` then lists the twelve tools (measured, 2026.09.23). Write the repository's absolute path: the agent CLI passes `${workspaceFolder}` to crapkit as literal text, and every tool call then answers that no `crapkit.toml` is there. |
| Starts in | The project directory (measured, agent CLI 2026.09.23). |
| Environment | Only `HOME`, `LC_CTYPE` and `PATH` (measured, agent CLI 2026.09.23). Put anything else under `env`. |
| Versions | The deploy suite runs agent CLI 2026.09.23-86fc751. |
| Plugin hooks | None from this config. Cursor can import a Claude Code plugin installed in the same home, and its converter keeps only each hook's `command`, `matcher` and `timeout` (read in the agent CLI's code), so crapkit's hook would run there as a bare `crapkit` that prints its usage and exits 2. The agent CLI lists no MCP server from the plugin: after the README's two plugin lines in the same home, `cursor-agent mcp list` shows none (measured), so give Cursor this block either way. |
| After an upgrade | Restart the agent, or turn the server off and on in Cursor's MCP settings. |

## Windsurf

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Windsurf |
|---|---|
| Config file | `~/.codeium/windsurf/mcp_config.json`, opened from Cascade's MCP settings. |
| Starts in | Not your repository in general; `--repo` names it. |
| Environment | Not measured. `env` adds variables. |
| Versions | No floor measured. |
| Plugin hooks | None. Windsurf installs no Claude Code plugin. |
| After an upgrade | Refresh the server in Cascade's MCP panel, or restart Windsurf. |

## VS Code with GitHub Copilot

```json
{
  "servers": {
    "crapkit": {
      "type": "stdio",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | VS Code with GitHub Copilot |
|---|---|
| Config file | Your user `mcp.json` (MCP: Open User Configuration), which serves every workspace. In a workspace's `.vscode/mcp.json`, write `${workspaceFolder}` in place of the path. |
| Starts in | Servers from the user profile start in your home directory, not the workspace, so the user file needs the absolute path. `"cwd"` sets it. |
| Environment | VS Code's own environment. `env` and `envFile` add variables. |
| Versions | The deploy suite runs 1.139.0, in a check that does not block a release yet. |
| Plugin hooks | None from this config. Do not add crapkit's Claude Code plugin to VS Code as an agent plugin: VS Code keeps only each hook's `command`, so every hook would run a bare `crapkit`, and it starts the plugin's server in the plugin's directory, where the server finds no repository (measured, 1.139.0). |
| After an upgrade | MCP: List Servers, pick crapkit, Restart Server. |

## GitHub Copilot CLI

```json
{
  "mcpServers": {
    "crapkit": {
      "type": "local",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"],
      "tools": ["*"]
    }
  }
}
```

| | GitHub Copilot CLI |
|---|---|
| Config file | `~/.copilot/mcp-config.json`, or `.mcp.json` or `.github/mcp.json` in the workspace. `copilot mcp add crapkit -- crapkit mcp --repo /absolute/path/to/your/repo` writes the user entry. |
| Starts in | The directory Copilot runs in (measured, 1.0.88). |
| Environment | Copilot's own environment (measured). `env` adds variables. |
| Versions | The deploy suite runs 1.0.88. |
| Plugin hooks | None from this config. Do not install crapkit's plugin with `copilot plugin install`: Copilot keeps only each hook's `command`, `matcher` and `timeout`, and one edit of a Python file then started 50 bare `crapkit` processes, each printing its usage, and held the edit for 29 seconds (measured, 1.0.88). |
| After an upgrade | Start a new `copilot` session. |

## Copilot cloud agent

```json
{
  "mcpServers": {
    "crapkit": {
      "type": "local",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"],
      "tools": ["*"]
    }
  }
}
```

The agent works in a GitHub Actions checkout, `/home/runner/work/REPO/REPO` for a
repository named `REPO`; that is the path to write. Its setup steps install crapkit and
score the checkout before the agent starts:

```yaml
# .github/workflows/copilot-setup-steps.yml
on: workflow_dispatch
jobs:
  copilot-setup-steps:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install crapkit
      - run: crapkit coverage
```

| | Copilot cloud agent |
|---|---|
| Config file | The repository's Settings, Copilot, Coding agent, MCP configuration. |
| Starts in | The runner; `--repo` names the checkout. |
| Environment | The runner's environment after the setup steps. |
| Versions | Hosted; no version to pin. |
| Plugin hooks | None. |
| After an upgrade | Each session runs the setup steps again. Pin the version in `pip install`. |

## Kiro

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Kiro |
|---|---|
| Config file | `.kiro/settings/mcp.json` in the workspace, or `~/.kiro/settings/mcp.json`. |
| Starts in | Not measured; `--repo` names the repository. |
| Environment | Not measured. `env` adds variables. |
| Versions | No floor measured. |
| Plugin hooks | None. Kiro's agent hooks are its own format. |
| After an upgrade | Reconnect the server from Kiro's MCP panel, or restart Kiro. |

## Gemini CLI

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Gemini CLI |
|---|---|
| Config file | `~/.gemini/settings.json`, or `.gemini/settings.json` in the project. `gemini mcp add -s user crapkit crapkit mcp --repo /absolute/path/to/your/repo` writes the user entry (measured, 0.61.0). |
| Starts in | The folder Gemini runs in (measured, 0.61.0). In a folder Gemini does not trust it disables every MCP server, user ones included: `gemini mcp list` shows crapkit as Disabled until you trust the folder, or set `GEMINI_CLI_TRUST_WORKSPACE=true` for a headless run (measured). |
| Environment | Gemini's own environment, plus `GEMINI_CLI` (measured). `env` adds variables. |
| Versions | The deploy suite runs 0.61.0. |
| Plugin hooks | None. crapkit ships no Gemini extension. |
| After an upgrade | `/mcp refresh` restarts the servers; a new session also does. |

## Qwen Code

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Qwen Code |
|---|---|
| Config file | `~/.qwen/settings.json`, or `.qwen/settings.json` in the project. Qwen Code is a fork of Gemini CLI and reads the same `mcpServers` block. |
| Starts in | Not measured; `--repo` names the repository. |
| Environment | Not measured. `env` adds variables. |
| Versions | No floor measured. |
| Plugin hooks | None. |
| After an upgrade | Start a new session. |

## OpenCode

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "crapkit": {
      "type": "local",
      "command": ["crapkit", "mcp", "--repo", "/absolute/path/to/your/repo"],
      "enabled": true
    }
  }
}
```

| | OpenCode |
|---|---|
| Config file | `opencode.json` in the project, or `~/.config/opencode/opencode.json`. `opencode mcp list` shows crapkit as connected (measured, 1.18.32). |
| Starts in | The project directory (measured, 1.18.32). |
| Environment | OpenCode's own environment, plus `OPENCODE` and `OPENCODE_PID` (measured). `environment` adds variables. |
| Versions | The deploy suite runs 1.18.32. |
| Plugin hooks | None. OpenCode's plugins are its own format. |
| After an upgrade | Restart OpenCode. |

## Goose

```yaml
extensions:
  crapkit:
    enabled: true
    name: crapkit
    type: stdio
    cmd: crapkit
    args: [mcp, --repo, /absolute/path/to/your/repo]
    timeout: 300
```

| | Goose |
|---|---|
| Config file | `~/.config/goose/config.yaml` (`%APPDATA%\Block\goose\config\config.yaml` on Windows). `goose configure`, Add Extension, Command-line Extension writes the same entry. |
| Starts in | The directory Goose runs in (measured, 1.52.0). |
| Environment | Goose's own environment (measured). `envs` adds variables. |
| Versions | The deploy suite runs 1.52.0. |
| Plugin hooks | None. |
| After an upgrade | Start a new session. |

## Amp

```json
{
  "amp.mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Amp |
|---|---|
| Config file | `~/.config/amp/settings.json`. `amp mcp add crapkit -- crapkit mcp --repo /absolute/path/to/your/repo` writes the entry; `--workspace` writes it into the workspace's settings instead, which `amp mcp approve crapkit` then has to approve. |
| Starts in | The directory Amp runs in (measured). |
| Environment | Amp's own environment (measured). `env` adds variables. |
| Versions | The deploy suite runs 0.0.1790265644. `amp mcp doctor crapkit` checks the server; with no `AMP_API_KEY` set it opens a browser login first (measured). |
| Plugin hooks | None. |
| After an upgrade | Restart Amp. |

## Crush

```json
{
  "$schema": "https://charm.land/crush.json",
  "mcp": {
    "crapkit": {
      "type": "stdio",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Crush |
|---|---|
| Config file | `crush.json` or `.crush.json` in the project, or `~/.config/crush/crush.json`. |
| Starts in | The directory Crush runs in (measured, 0.96.1). |
| Environment | Crush's own environment (measured). `env` adds variables. |
| Versions | The deploy suite runs 0.96.1. |
| Plugin hooks | None. |
| After an upgrade | Restart Crush. |

## oh-my-pi

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | oh-my-pi |
|---|---|
| Config file | `.omp/mcp.json` in the project, or `~/.omp/agent/mcp.json`. It also reads the `.mcp.json`, `.cursor/mcp.json` and `.vscode/mcp.json` other agents write. |
| Starts in | Not measured; `--repo` names the repository. |
| Environment | Not measured. `env` adds variables. |
| Versions | The deploy suite runs 18.3.0. |
| Plugin hooks | None. |
| After an upgrade | Start a new session; `/mcp test crapkit` checks the server. |

## Cline

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Cline |
|---|---|
| Config file | In the editor extension, `cline_mcp_settings.json`, opened from the MCP Servers panel, Configure. The CLI writes its own copy: `cline mcp install crapkit --yes -- crapkit mcp --repo /absolute/path/to/your/repo` stores it in `~/.cline/data/settings/cline_mcp_settings.json` (measured, 3.0.65). |
| Starts in | Not your workspace: the extension starts servers from the editor's own process. `--repo` is required. |
| Environment | The MCP SDK's short default list, not the editor's: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER` on macOS and Linux. `env` adds variables. |
| Versions | The deploy suite runs the CLI 3.0.65. |
| Plugin hooks | None. |
| After an upgrade | Restart the server from the MCP Servers panel. |

## Roo Code

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Roo Code |
|---|---|
| Config file | `.roo/mcp.json` in the project, or the global `mcp_settings.json` from the MCP Servers panel. |
| Starts in | Not your workspace in general; `--repo` names it. `cwd` sets the directory. |
| Environment | Not measured. `env` adds variables. |
| Versions | Roo Code's repository is archived at 3.54.0. |
| Plugin hooks | None. |
| After an upgrade | Restart the server from the MCP Servers panel. |

## Kilo Code

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Kilo Code |
|---|---|
| Config file | `.kilocode/mcp.json` in the project, or the global `mcp_settings.json` from the MCP Servers panel. Kilo Code descends from Cline and Roo Code and reads the same block. |
| Starts in | Not your workspace in general; `--repo` names it. |
| Environment | Not measured. `env` adds variables. |
| Versions | No floor measured. |
| Plugin hooks | None. |
| After an upgrade | Restart the server from the MCP Servers panel. |

## Continue

```yaml
name: Local Assistant
version: 1.0.0
schema: v1
mcpServers:
  - name: crapkit
    command: crapkit
    args:
      - mcp
      - --repo
      - /absolute/path/to/your/repo
```

| | Continue |
|---|---|
| Config file | `~/.continue/config.yaml`, which the `cn` CLI and the editor extensions both read. If the file exists, add the `mcpServers` entry to it. The extensions also read `.continue/mcpServers/*.yaml` in the workspace; `cn` 1.5.47 does not (measured). |
| Starts in | The directory `cn` runs in (measured, 1.5.47). |
| Environment | Continue's own environment (measured). `env` adds variables. |
| Versions | The deploy suite runs the `cn` CLI 1.5.47. |
| Plugin hooks | None. Continue loads skills: `cn` 1.5.47 reads `SKILL.md` files from `.continue/skills` and `.claude/skills` in the workspace and from `~/.continue/skills`, so a copy of `plugin/skills/*` there gives it crapkit's three skills. |
| After an upgrade | Start a new session. |

## Zed

```json
{
  "context_servers": {
    "crapkit": {
      "source": "custom",
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Zed |
|---|---|
| Config file | Zed's `settings.json` (zed: open settings). |
| Starts in | Not measured; `--repo` names the repository. |
| Environment | Not measured. `env` adds variables. On Windows Zed runs the command through PowerShell without `-NoProfile`, so anything your PowerShell profile prints lands in the protocol stream: keep the profile silent. |
| Versions | The deploy suite runs 1.21.0, in a check that does not block a release yet. |
| Plugin hooks | None. Zed loads skills from `~/.agents/skills`, so a copy of `plugin/skills/*` there gives it crapkit's three skills. |
| After an upgrade | Restart the server from the agent panel's settings, or restart Zed. |

## JetBrains AI Assistant

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | JetBrains AI Assistant |
|---|---|
| Config file | Settings, Tools, AI Assistant, Model Context Protocol (MCP), Add, then paste the block as JSON. |
| Starts in | The working directory the dialog names; `--repo` names the repository either way. |
| Environment | Not measured. The dialog's variables are added. |
| Versions | No floor measured. |
| Plugin hooks | None. |
| After an upgrade | Restart the server from the same settings page, or restart the IDE. |

## Junie

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Junie |
|---|---|
| Config file | `~/.junie/mcp/mcp.json`, or `.junie/mcp/mcp.json` in the project. Junie reads `~` as your account's home directory (or `JUNIE_HOME`), not `$HOME`. |
| Starts in | The directory Junie runs in (measured, 1468.30.0). |
| Environment | Junie's own environment (measured). `env` adds variables. |
| Versions | The deploy suite runs the CLI 1468.30.0. It offers protocol revision `2025-03-26`, which crapkit speaks. |
| Plugin hooks | None. Junie runs no PostToolUse hooks. |
| After an upgrade | Start a new session. |

## Aider

```yaml
lint-cmd:
  - "crapkit rescore --gate --repo /absolute/path/to/your/repo"
auto-lint: true
```

| | Aider |
|---|---|
| Config file | `.aider.conf.yml` in the repository. Aider has no MCP client, so crapkit reaches it as the linter: after each edit Aider runs the command with the edited files appended. |
| Starts in | The repository Aider runs in. |
| Environment | Aider's own environment. |
| Versions | The deploy suite runs 0.86.2. |
| Plugin hooks | None. Aider reads every non-zero exit as lint errors and shows the output to the model: exit 6 is a function over its ceiling, and exit 1 with `no snapshot` means `crapkit coverage` has not run yet. A clean file exits 0. |
| After an upgrade | Nothing to restart: each lint starts crapkit afresh. |

## Antigravity

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

| | Antigravity |
|---|---|
| Config file | `~/.gemini/antigravity/mcp_config.json`, opened from the agent panel's MCP Servers, Manage MCP Servers, View raw config. |
| Starts in | Not measured; `--repo` names the repository. |
| Environment | Not measured. `env` adds variables. |
| Versions | No floor measured. |
| Plugin hooks | None. |
| After an upgrade | Refresh the server from Manage MCP Servers, or restart Antigravity. |
