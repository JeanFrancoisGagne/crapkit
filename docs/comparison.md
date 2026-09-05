# crapkit next to crap4py, radon, xenon, wily and SonarQube

Evaluators arrive with one of these already installed, so this page says what each tool
answers and where crapkit overlaps them: mostly it does not. crapkit's one idea is the
join: complexity and coverage multiplied into one per-function number,
`ccn^2 x (1 - cov)^3 + ccn`, ranked by churn and held down by a ratchet.

| Tool | What it measures | What it gates | Runs as |
| --- | --- | --- | --- |
| [crap4py](https://github.com/gabadi/crap4py) | the same CRAP formula per function, Python only, from an lcov file you pass it; a port of crap4go, conventional threshold 30 | nothing by itself; it prints the table | CLI |
| [radon](https://radon.readthedocs.io/) | cyclomatic complexity, maintainability index, Halstead and raw metrics, per function | nothing by itself | CLI / library |
| [xenon](https://github.com/rubik/xenon) | radon's complexity ranks | CI fails past a chosen rank | CLI |
| [wily](https://wily.readthedocs.io/) | complexity and maintainability across git history | nothing; it reports trends | CLI over git |
| [coverage.py / pytest-cov](https://coverage.readthedocs.io/) | which lines and branches the suite executed | a total-percent floor (`--fail-under`) | test plugin |
| [SonarQube](https://www.sonarsource.com/products/sonarqube/) | a multi-language platform: static analysis, duplication, coverage ingestion, quality gates | its own gate rules | server + scanner |
| crapkit | complexity times uncovered risk, one score per function, churn-ranked | commits, pull requests and agent edits over a per-function ceiling | CLI, no server |

## Where the lines actually sit

A complexity rank alone calls a well-tested dispatcher and an untested one the same
problem; a coverage percent alone hides one untested `ccn 12` function inside a green
overall number. Each factor's tool is right about what it measures, and the risk lives in
the product of the two. That product is the whole reason crapkit exists, and it is why
crapkit does not replace coverage.py: it *reads* the report your own test command already
writes, in the same run.

crap4py is the closest neighbour by name and formula: one release (0.1.1, June 2026, MIT),
Python only, an lcov file in and a table out. crapkit reads fifteen languages through
lizard, runs your own test command as a lane and joins its artifact in the same run, ranks
the result by churn, and holds it with a ratchet and four gates. The other visible
difference is the default: crap4py quotes the conventional 30, crapkit's `target` is 6,
because 30 is a CRAP score that an untested `ccn 5` passes and 6 is a complexity ceiling
coverage cannot buy past. Set `target = 30` if you want the crap4j number; the ratchet
makes that unnecessary for adoption, since seeded debt is never a finding until it rises.

xenon is the closest neighbour in spirit, a threshold that fails CI. The differences are
the coverage term, the churn ranking, the ratchet (existing debt is marked and may only
shrink, so adoption never starts with a wall of red), and the agent surfaces: a
pre-commit hook, a GitHub Action that comments the verdict on the pull request, a
per-edit advisory for Claude Code, and an MCP server any client can read.

SonarQube sits on the other side of a different line: a platform with a server,
projects, users and dashboards. If your organization runs one, crapkit is not a
replacement and does not try to be; it is the small sharp version of one gate, close to
the repo, with nothing to host.

## Using them together

Nothing here conflicts. radon and wily read the same tree crapkit reads; pytest-cov's
artifact is crapkit's input; a repo behind SonarQube can still ratchet its function-level
debt locally. The one integration worth naming: crapkit's lane runs your exact test
command, so whatever coverage configuration those tools taught you to write keeps
working unchanged.
