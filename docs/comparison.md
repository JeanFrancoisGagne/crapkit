# crapkit next to crap4py, radon, xenon, wily and SonarQube

Evaluators arrive with one of these already installed, so this page says what each tool
answers and where crapkit overlaps them: mostly it does not. crapkit's one idea is the
join: complexity and coverage multiplied into one per-function number,
`ccn^2 x (1 - cov)^3 + ccn`, ranked by churn and held down by a ratchet.

| Tool | What it measures | What it gates | Runs as |
| --- | --- | --- | --- |
| [crap4py](https://github.com/gabadi/crap4py) | the same CRAP formula per function, Python only, from an lcov file you pass it; a port of crap4go and crap4clj, conventional threshold 30 | `--max-crap N` exits non-zero when any function scores above N (functions with no coverage data never trip it) | CLI |
| [radon](https://radon.readthedocs.io/) | cyclomatic complexity per function, method or class; maintainability index and raw metrics per file; Halstead per file, or per function with `-f` | nothing by itself | CLI / library |
| [xenon](https://github.com/rubik/xenon) | radon's complexity ranks | CI fails past a chosen rank | CLI |
| [wily](https://wily.readthedocs.io/) | complexity and maintainability across git history | `wily rank --threshold N` exits non-zero when the ranked metric's total (maintainability index by default) is below N; otherwise it reports trends | CLI over git |
| [coverage.py / pytest-cov](https://coverage.readthedocs.io/) | which lines and branches the suite executed | a total-percent floor (`--fail-under`) | test plugin |
| [SonarQube](https://www.sonarsource.com/products/sonarqube/) | a multi-language platform: static analysis, duplication, coverage ingestion, quality gates | its own gate rules | server + scanner |
| crapkit | complexity times uncovered risk, one score per function, churn-ranked | staged complexity and the full verify verdict; agent edits receive an advisory | local CLI, optional stdio MCP server |

## Where the lines sit

A complexity rank alone calls a well-tested dispatcher and an untested one the same
problem; a coverage percent alone hides one untested `ccn 12` function inside a green
overall number. Each factor's tool is right about what it measures, and the risk lives in
the product of the two. crapkit gates that product, and it does not replace coverage.py:
it *reads* the report your own test command already
writes, in the same run.

crap4py is the closest neighbour by name and formula: Python only, an lcov file in, a
table out, and an optional `--max-crap` gate. crapkit reads fourteen configured language groups through
lizard, runs your own test command as a lane and joins its artifact in the same run, ranks
the result by churn, and holds it with a ratchet and the [gate surfaces](../README.md#the-gate). The other visible
difference is the default: crap4py quotes the conventional 30, crapkit's `target` is 6,
because 30 is a CRAP score that an untested `ccn 5` passes and 6 is a complexity ceiling
coverage cannot buy past. Set `target = 30` if you want the crap4j number; the ratchet
makes that unnecessary for adoption, since seeded debt is never a finding until it rises.

xenon is the closest neighbour in spirit, a threshold that fails CI. The differences are
the coverage term, the churn ranking, the ratchet (existing debt is marked and may only
shrink, so adoption never starts with a wall of red), and the agent surfaces: a
pre-commit hook, a GitHub Action that comments the verdict on the pull request, a
per-edit advisory in Claude Code, Cursor, GitHub Copilot CLI and VS Code (and the Claude
Agent SDK when it loads the plugin), and an MCP server any client can read.

SonarQube sits on the other side of a different line: a platform with a server,
projects, users and dashboards. If your organization runs one, crapkit does not replace
it: crapkit is one gate that runs in the repo, with nothing to host.

## Checked against them

crapkit's own numbers are checked against several of these tools on every push: its
`ccn` against radon's and mccabe's, its cognitive column against complexipy's, and its
coverage join against coverage.py's own line and branch data. Where crapkit reads a
construct differently on purpose, such as a Rust `match` gated per arm, a rulings row
says so and cites the line of the docs that states it. The
[accuracy suite](accuracy.md) lists every calculation, every check and every ruling.

## Using them together

Nothing here conflicts. radon and wily read the same tree crapkit reads; pytest-cov's
artifact is crapkit's input; a repo behind SonarQube can still ratchet its function-level
debt locally. The one integration worth naming: crapkit's lane runs your exact test
command, so whatever coverage configuration those tools taught you to write keeps
working unchanged.
