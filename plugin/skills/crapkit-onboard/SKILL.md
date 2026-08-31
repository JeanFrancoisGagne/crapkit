---
name: crapkit-onboard
description: Adopting crapkit in a repo for the first time.
disable-model-invocation: true
---

# Adopting crapkit

Two artifacts, installed once each: the CLI scores the repo, the plugin gives an agent the
skills. Run them in that order, CLI first.

## The plugin

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

One install carries all three skills, the read-side MCP server, and the advisory PostToolUse
hook, at a version that tracks the CLI's. `crapkit doctor --plugin-root` reports drift
between the two later. With no path it reads Claude Code's own plugin directory; with a
path it takes the plugin root or any directory above it, `~/.claude` included. When it
picks a root for you it names the one it chose:

    $ crapkit doctor --plugin-root
    crapkit doctor: checking C:\Users\jfgag\.claude\plugins\cache\crapkit\crapkit\0.4.4

Nothing after that line, and exit 0, means the manifest version and the hook protocol both
agree with this CLI. Otherwise it prints one line per disagreement, at exit 1. Finding no
install at all is its own line, and which line you get depends on how you asked. Both exit 1.

With no path, the search names the command that fixes it:

    crapkit doctor: no installed crapkit plugin under DIR (install with `claude plugin install crapkit@crapkit`, or pass --plugin-root PATH)

With a PATH you typed that holds no `.claude-plugin/plugin.json` at or under it, the line
names the path and nothing else:

    crapkit doctor: the plugin at PATH has no .claude-plugin/plugin.json

No install command in that one, because the path is what to correct. An empty directory and
a path that does not exist both get it.

Install it after the repo scores, not before. Earlier, `crapkit-onboard` points at a config
that does not exist yet and `crapkit` points at a store with no run in it.

Fallback for a runtime with no plugin marketplace: copy `plugin/skills/*` from a clone into
`~/.claude/skills`, or that runtime's equivalent. A copy gets the skills alone, never the
hook or the MCP server, and carries no version to compare against the CLI.

## The repo

Install the CLI first ([README: install](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#install)).
Then, in the repo being adopted: `crapkit init` writes the starter config, `crapkit doctor`
says whether it still describes the repo, `crapkit coverage` produces the first scored run.

    $ crapkit init
    wrote crapkit.toml with 1 scope(s): calc
    detected 1 lane(s) from this repo's own files: py — next: run `crapkit coverage`
    added to .gitignore: .crapkit/, .coverage, __pycache__/

A detected lane comes out with its test-results file already wired, `--junitxml` on the
command and `results_artifact` on the lane, because without one the crashed-worker check
and the no-new-failures check (exit 8) cannot run. `doctor` WARNs about a lane that has
none.

Read init's notes before the first `crapkit coverage`. Two of them are about the
interpreter, and they are different problems. One says the shell cannot run the word the
lane starts with, naming the exit code; on Windows that is usually the Store `python.exe`
alias, and the fix is a real Python or a lane pointed at `py`. The other says the python
that runs pytest cannot import `pytest_cov`, and the fix is `pip install pytest-cov` in
the environment the SUITE runs in, or `pip install "crapkit[py]"` when that environment is
crapkit's own. Keep those double quotes: cmd.exe passes `'` through as an ordinary
character and pip then rejects the requirement.

On a Windows PATH that carries only the `py` launcher, init writes `py` into the lane. It
is last in the fallback chain because it exists nowhere else, so a committed `py -m pytest`
fails every Unix collaborator's doctor.

Read [docs: adoption](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md)
before the first `crapkit init`. It carries the judgment the quickstarts leave out: how
coarse to cut scopes, exclude versus lane, where `scoped_tests` belongs, the first-verify
taint.

Then run [README: quickstart, Python](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-python)
or [README: quickstart, TypeScript](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-typescript)
for the mechanics, in order.
