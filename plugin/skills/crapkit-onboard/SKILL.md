---
name: crapkit-onboard
description: Adopting crapkit in a repo for the first time.
disable-model-invocation: true
---

# Adopting crapkit

Two artifacts, installed once each: the CLI scores the repo, the plugin gives an agent the
skills. Run them in that order, CLI first.

## The repo

Read [docs: adoption](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md)
before the first `crapkit init`. It carries the judgment the quickstarts leave out: how
coarse to cut scopes, exclude versus lane, where `scoped_tests` belongs, the first-verify
taint.

Install the CLI first ([README: install](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#install)).
Then, in the repo being adopted: `crapkit init` writes the starter config, `crapkit doctor`
says whether it still describes the repo, `crapkit coverage` produces the first scored run.

    $ crapkit init
    wrote crapkit.toml with 1 scope(s): calc
    detected 1 lane(s) from this repo's own files: py - next: run `crapkit coverage`
    added to .gitignore: .crapkit/, .coverage, __pycache__/

A detected lane comes out with its test-results file already wired, `--junitxml` on the
command and `results_artifact` on the lane, because without one the crashed-worker check
and the no-new-failures check (exit 8) cannot run. `doctor` WARNs about a lane that has
none.

A lockfile at the root decides which python those lines call: `uv.lock` writes
`uv run python -m pytest ...`, and `poetry.lock`, `pdm.lock` and `Pipfile.lock` write
their own `run` prefix, first match in that order. Every python line the file holds names
that launcher, the commented `[[lane]]` template included, which a repo with no pytest
marker file (`pyproject.toml`, `pytest.ini`, `setup.cfg`) gets in place of a live lane.
Uncommenting it is therefore safe now: it used to hand back a bare `python`, which binds
to whichever venv the shell has active rather than the one the repo pins.

With no lockfile, `init` looks for a venv the repo carries (`.venv`, `venv`, or one
`.venv` per sniffed scope) and writes its interpreter as a launcher token, but only when
that directory holds `pyvenv.cfg` and its python imports pytest: `{python:.venv}`, which
the loader reads as `.venv/bin/python` on Linux and macOS and `.venv\Scripts\python.exe`
on Windows, so one committed line runs on every collaborator's OS. With no venv either,
it writes `{python}`, read as `python3` on Linux and macOS and `python` on Windows. A
machine where that name does not resolve gets the one that does, `py` included. When
you edit a lane by hand, keep the token in front of `-m pytest`.

Only git-tracked files are scored. On a repo whose source nobody has added, `init`
exits 3 and names up to three of the files it found, ending
``run `git add` first (2 untracked source file(s) found: lib/util.py, src/app.ts)``.
`git add` them and run `init` again.

Read init's notes before the first `crapkit coverage`. Two of them are about the
interpreter, and they are different problems. One says the shell cannot run the word the
lane starts with, naming the exit code; on Windows that is usually the Store `python.exe`
alias, and the fix is a real Python or a lane pointed at `py`. The other says the python
that runs pytest cannot import `pytest_cov`, and the fix is `pip install pytest-cov` in
the environment the suite runs in, or `pip install "crapkit[py]"` when that environment is
crapkit's own. Keep those double quotes: cmd.exe passes `'` through as an ordinary
character and pip then rejects the requirement.

On a Windows PATH that carries only the `py` launcher, init writes `py` into the lane. It
exists nowhere else, so a committed `py -m pytest` fails every Unix collaborator's doctor.
The fix is a Python install that puts `python` on that PATH, then `{python}` in place of
`py`. A config an older `init` wrote names one OS's venv launcher, `.venv/bin/python` or
`.venv\\Scripts\\python.exe`; write `{python:.venv}` in its place.

Then run [README: quickstart, Python](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-python)
or [README: quickstart, TypeScript](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-typescript)
for the mechanics, in order.

## The plugin

Install it with the plugin manager of the agent you run. In Claude Code:

```
claude plugin marketplace add JeanFrancoisGagne/crapkit --sparse .claude-plugin plugin
claude plugin install crapkit@crapkit
```

In Codex:

```
codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.1 --sparse .claude-plugin --sparse plugin
codex plugin add crapkit@crapkit
```

In GitHub Copilot CLI: `copilot plugin marketplace add JeanFrancoisGagne/crapkit`, then
`copilot plugin install crapkit@crapkit` (on Windows run `git config --global
core.longpaths true` first). Cursor loads the plugin Claude Code installed, and VS Code
loads it once you add it as an agent plugin; both run its hook and still take the MCP
server from their own config.

Any other agent takes the MCP server from its own config file:
[docs: wiring crapkit into your agent](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/harnesses.md).

One install carries all three skills and the read-side MCP server, at a version that
tracks the CLI's; in Claude Code, Cursor, Copilot CLI and VS Code it also carries the
advisory hook.
`crapkit doctor --plugin-root` reports drift between the two later. With no path it
reads Claude Code's own plugin directory, then Codex's when Claude Code holds none; with a
path it takes the plugin root or any directory above it, `~/.claude` and `~/.codex`
included. To check the Codex copy on a machine where Claude Code holds one too, pass it:
`~/.codex/plugins/cache/crapkit/crapkit/VERSION`. When it picks a root for you it names
the one it chose:

    $ crapkit doctor --plugin-root
    crapkit doctor: checking <home>\.claude\plugins\cache\crapkit\crapkit\<version>

Nothing after that line, and exit 0, means the manifest version and the hook protocol both
agree with the `crapkit` on PATH, which is the one the hooks spawn. Otherwise it prints
one line per disagreement, at exit 1. Finding no install at all is its own line, and which
line you get depends on how you asked. Both exit 1.

With no path, the search looks in Claude Code's plugin directory, then Codex's, and names
the commands that fix it:

    crapkit doctor: no installed crapkit plugin under DIR or CODEX_DIR. Claude Code installs it with `claude plugin marketplace add JeanFrancoisGagne/crapkit --sparse .claude-plugin plugin`, then `claude plugin install crapkit@crapkit`; Codex with `codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.1 --sparse .claude-plugin --sparse plugin`, then `codex plugin add crapkit@crapkit`. For a plugin kept anywhere else, pass --plugin-root PATH.

With a PATH you typed that holds no `.claude-plugin/plugin.json` at or under it, the line
names the path and where to point instead:

    crapkit doctor: the plugin at PATH has no .claude-plugin/plugin.json, so it is no plugin root; name the plugin root or a directory above it, or run `crapkit doctor --plugin-root` with no PATH to check the installs Claude Code and Codex recorded.

No install command in that one, because the path is what to correct. An empty directory and
a path that does not exist both get it.

Install it after the repo scores, not before. Earlier, `crapkit-onboard` points at a config
that does not exist yet and `crapkit` points at a store with no run in it.

The hook already works in a repo with no commit yet: with no HEAD to diff against, it
judges every function in the edited file, staged or not. When it cannot judge an edit it
says so, still at exit 2: `PATH could not be read` when no reader could parse the file,
with the reader's reason on an `UNREAD` line, and `git could not report what changed in PATH` when git fails, with
git's error.

Fallback for a runtime with no plugin marketplace: copy `plugin/skills/*` from a clone,
each skill's whole directory, into the one that runtime reads: `~/.claude/skills` for
Claude Code, `$CODEX_HOME/skills` (`~/.codex/skills` by default) for Codex,
`~/.gemini/skills` for Gemini CLI. A copy gets the skills alone, never the hook or the MCP
server, and carries no version to compare against the CLI. Codex keeps `crapkit-onboard`
out of the model's list through the skill's `agents/openai.yaml`, which the whole-directory
copy carries. Gemini CLI reads no such file and lists all three skills to its model; once
the repo is adopted, `gemini skills disable crapkit-onboard --scope user` takes the
onboarding skill out. That runtime
starts the MCP server from its own config file, in its own key and fields:
[the block for each agent](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/harnesses.md).

### Optional, Claude Code: advise Bash writes too

The plugin registers the hook on `Edit|Write`. A `Bash` event carries the command and no
file path, so a session that writes source through a heredoc or `python - <<PY` gets no
advisory at all. A second entry, same command, matcher `Bash`, closes that. Add it to
`~/.claude/settings.json`, or to `.claude/settings.json` for one repo, merging into any
`hooks` object already there:

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "Bash",
            "hooks": [
              { "type": "command", "command": "crapkit claude-hook --protocol 1", "timeout": 20 }
            ]
          }
        ]
      }
    }

The hook then reads the working tree: the `*.py` files git reports dirty or untracked
whose mtime lands inside a 12-second window, at most 25 of them, each judged the way an
edited file is judged. It records the bytes each judgement read, per session, under
`.git/crapkit/claude-hook/`, and skips a file whose bytes that session already judged, so
a `touch` or a test run after an edit does not repeat an advisory. A file moved or copied
in with its old mtime (`mv`, `cp -p`, an unpacked archive) is not judged here; the commit
gate still judges it.

The cost is why it is the consumer's choice and not the plugin's. Every shell call inside
a repo pays one `git rev-parse` plus one `git status`, whether or not it wrote anything,
and a repo with no `crapkit.toml` pays both before finding nothing to judge. A shell call
outside any repo stops at the `rev-parse`.
