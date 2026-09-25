"""Mutation testing of the calculation modules, gated on a keyed survivor set.

    python tools/accuracy/mutation.py weekly --shard N --of M [--max-children K]
    python tools/accuracy/mutation.py diff --since-weekly [--base SHA] [--cap-minutes 30]
    python tools/accuracy/mutation.py gate RESULTS.json... [--update]
    python tools/accuracy/mutation.py floors RESULTS.json...
    python tools/accuracy/mutation.py covered [--receipts DIR]
    python tools/accuracy/mutation.py tools [--max-children K]
    python tools/accuracy/mutation.py key < MUTANT.diff
    python tools/accuracy/mutation.py killer [PYTEST ARGS...]

mutmut 3.8.0 runs in the accuracy image (it forks, so Linux only). `weekly`
mutates one shard of the modules every tests/accuracy/*/calcs.tsv row names;
`diff` mutates only the functions changed since the last weekly run and stops
at its cap, reporting `incomplete`, never `pass`. Both run in a detached
worktree of HEAD (.crapkit/accuracy/mutation/calc-stage) whose [tool.mutmut]
names the modules and the suite, tests/unit and tests/accuracy at the push tier
with the dependent methods deselected, then write a receipt under
.crapkit/accuracy/mutation/ and run the gate.

The gate is a survivor set, not a rate. A survivor is keyed by (module,
function, sha256 of its mutant diff with line numbers and mutmut's numbering
removed), so a mutant keeps its key when lines above it move or mutmut numbers
it differently. tests/accuracy/suite_strength/mutation/survivors.tsv lists the
survivors someone looked at and gave a reason; equivalent.tsv lists the mutants
proven to behave like the original, each with the evidence line
`equivalence_evidence` writes after 10,000 examples. A survivor on neither list
fails the run. A listed survivor that now dies is reported for removal (and
removed with --update); an equivalent row that matches no mutant of a module
the run mutated fails, because its evidence then names nothing.

Every weekly shard also mutates score.crap, the canary: every one of its
mutants must die, or the shard's results are void. So is a run holding a mutant
mutmut never judged (`not checked` when its stats run failed, `suspicious`).
A mutant no test reaches (`no tests`) counts as a survivor. floors.tsv gives
each module group its kill-rate floor after equivalents, computed on the
independent-only suite (golden, change_control and cross_surface tests
deselected); a group below its floor fails the run as a new survivor does.
Timeouts get one serial rerun and never count as kills.

`tools` is the second config: tests/accuracy/kit/exact.py (floor 100 percent)
and the tools under tools/accuracy (floor 90 percent), each run against the
tests that exercise it, in a detached worktree of HEAD under
.crapkit/accuracy/mutation/tools-stage whose [tool.mutmut] table names them.
mutmut runs there through a launcher that names each module the way its tests
import it (see LAUNCHER).

`killer` is the suite crapkit.toml's mutation_command runs per mutant. It runs
pytest with the working directory's src/ and tests/ first on PYTHONPATH, so a
mutation worktree's code is the code under test even where an editable install
points at another checkout.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

REPO = Path(__file__).resolve().parents[2]
TABLES = REPO / "tests" / "accuracy" / "suite_strength" / "mutation"
RECEIPTS = Path(".crapkit") / "accuracy" / "mutation"
SURVIVOR_COLUMNS = ("module", "function", "diff_sha256", "reason", "added")
EQUIVALENT_COLUMNS = ("module", "function", "diff_sha256", "evidence", "strategy", "checked")
FLOOR_COLUMNS = ("group", "paths", "floor", "source")
CANARY = ("src/crapkit/score.py", "crap")
KILLED = frozenset({"killed", "caught by type check"})
SURVIVED = "survived"
NO_TESTS = "no tests"
TIMEOUT = "timeout"
# A mutant no test reaches lives as surely as one the tests run and miss.
ALIVE = frozenset({SURVIVED, NO_TESTS})
# Every status that is a verdict on the mutant. Anything else ("not checked",
# "suspicious", a crash) means mutmut never judged it, and the run proves nothing.
JUDGED = KILLED | ALIVE | {TIMEOUT, "skipped"}
STATUS_BY_EXIT = {1: "killed", 3: "killed", 0: SURVIVED, 5: "no tests", 33: "no tests",
                  34: "skipped", 36: TIMEOUT, 37: "caught by type check", -24: TIMEOUT,
                  24: TIMEOUT, 152: TIMEOUT, 255: TIMEOUT, -11: "segfault", -9: "segfault",
                  None: "not checked"}
# The floors' suite: every test but those comparing crapkit with a copy of itself.
FLOOR_SUITE = "not golden and not change_control and not cross_surface"
# The killer suite also leaves out every test that spawns git, node, pwsh or the CLI.
INDEPENDENT_ONLY = f"not process and {FLOOR_SUITE}"
SEPARATOR = "ǁ"  # mutmut's class separator in a mangled method name
WEEKLY_DAY, WEEKLY_HOUR = 5, 6  # Saturday 06:00 UTC, accuracy.yml's weekly schedule


class MutationError(ValueError):
    """A table, receipt or argument this tool cannot use."""


class MissingReceipts(MutationError):
    """The receipts a release row reads are not on this machine: an infra miss, exit 3."""


# accuracy.yml uploads each weekly shard's and each nightly diff run's receipt
# under this artifact name pattern; the release row reads them from RECEIPTS.
RECEIPT_ARTIFACTS = "mutation-receipt-*"


# --- keys ----------------------------------------------------------------------------------

_NUMBERING = re.compile(r"__mutmut_(?:\d+|orig)\b")


def normalized_diff(text: str) -> list[str]:
    """The changed lines of a unified diff, with the line numbers (the @@ headers),
    the file headers and mutmut's per-mutant numbering removed."""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith(("+++", "---", "@@")) or not line.startswith(("+", "-")):
            continue
        lines.append(_NUMBERING.sub("__mutmut", line.rstrip()))
    return lines


def mutant_key(diff_text: str) -> str:
    """sha256 over the normalized diff: the survivor's identity within its function."""
    return hashlib.sha256("\n".join(normalized_diff(diff_text)).encode("utf-8")).hexdigest()


def split_name(mutant_name: str) -> tuple[str, str]:
    """(dotted module, qualified function) of a mutmut mutant name such as
    crapkit.score.x_crap__mutmut_3 or crapkit.store.xǁStoreǁwrite__mutmut_2."""
    prefix, _, number = mutant_name.rpartition("__mutmut_")
    module, _, mangled = prefix.rpartition(".")
    if not (prefix and module and number):
        raise MutationError(f"{mutant_name!r} is not a mutmut mutant name")
    return module, _unmangled(mangled, mutant_name)


def _unmangled(mangled: str, name: str) -> str:
    if mangled.startswith("x_"):
        return mangled[2:]
    parts = mangled.split(SEPARATOR)
    if len(parts) != 3 or parts[0] != "x":
        raise MutationError(f"{name!r} names no function mutmut mangles")
    return f"{parts[1]}.{parts[2]}"


def module_path(dotted: str, repo: Path = REPO) -> str:
    """The repo path of a dotted module: src/ first, then the tree itself."""
    relative = dotted.replace(".", "/") + ".py"
    for candidate in (f"src/{relative}", relative, f"tests/{relative}"):
        if (repo / candidate).is_file():
            return candidate
    raise MutationError(f"no file for module {dotted} under {repo}")


def _dotted(path: str) -> str:
    """The name mutmut gives a module: its path, with src/ (and here tests/) left off."""
    for prefix in ("src/", "tests/"):
        path = path.removeprefix(prefix)
    return path.removesuffix(".py").replace("/", ".")


def mutmut_glob(path: str, qualname: str) -> str:
    """The mutmut mutant-name glob for one function: the nightly diff run's filter."""
    dotted = _dotted(path)
    owner, _, name = qualname.rpartition(".")
    mangled = f"x{SEPARATOR}{owner}{SEPARATOR}{name}" if owner else f"x_{name}"
    return f"{dotted}.{mangled}__mutmut_*"


# --- results ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    name: str
    module: str
    function: str
    status: str
    key: str = ""

    @property
    def ident(self) -> tuple[str, str, str]:
        return self.module, self.function, self.key


def result(name: str, status: str, diff_text: str = "", repo: Path = REPO) -> Result:
    dotted, function = split_name(name)
    key = mutant_key(diff_text) if diff_text else ""
    return Result(name, module_path(dotted, repo), function, status, key)


def load_results(paths: list[Path]) -> list[Result]:
    rows = []
    for path in paths:
        rows += [Result(**row) for row in json.loads(Path(path).read_text("utf-8"))["results"]]
    return rows


# --- tables ------------------------------------------------------------------------------------

def read_table(path: Path, columns: tuple[str, ...]) -> list[dict]:
    """A tab-separated table with `columns` as its header; a missing file has no rows."""
    if not path.is_file():
        return []
    lines = _lines(path)
    _check_header(path, lines, columns)
    return [_cells(path, number, line, columns) for number, line in enumerate(lines[1:], 2)]


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check_header(path: Path, lines: list[str], columns: tuple[str, ...]) -> None:
    if not lines or tuple(lines[0].split("\t")) != columns:
        raise MutationError(f"{path}: the header must be {' '.join(columns)} (tab-separated)")


def _cells(path: Path, number: int, line: str, columns: tuple) -> dict:
    cells = line.split("\t")
    if len(cells) != len(columns):
        raise MutationError(f"{path}:{number}: {len(cells)} cells, the header has {len(columns)}")
    return dict(zip(columns, cells))


def write_table(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    body = ["\t".join(columns)] + ["\t".join(row[column] for column in columns) for row in rows]
    path.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")


def _ident(row: dict) -> tuple[str, str, str]:
    return row["module"], row["function"], row["diff_sha256"]


# --- the gate ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    new: tuple = ()             # survivors on neither list: the run fails
    gone: tuple = ()            # listed survivors that now die: remove them
    killed_equivalents: tuple = ()  # equivalent rows whose mutant died: drop them
    orphan_equivalents: tuple = ()  # equivalent rows no mutant of a mutated module matches
    void: str = ""              # why the shard's results cannot be used

    @property
    def passed(self) -> bool:
        return not (self.new or self.orphan_equivalents or self.void)


def _canary_rows(results: list[Result]) -> list[Result]:
    return [row for row in results if (row.module, row.function) == CANARY]


def _alive_names(rows: list[Result]) -> list[str]:
    return [row.name for row in rows if row.status not in KILLED]


def canary_problem(results: list[Result]) -> str:
    """Why the canary voids the run, or "" when every score.crap mutant died."""
    mine = _canary_rows(results)
    if not mine:
        return "the canary score.crap was not mutated in this run"
    alive = _alive_names(mine)
    return f"canary mutants of score.crap survived: {', '.join(alive)}" if alive else ""


def _mutated_modules(results: list[Result]) -> set[str]:
    return {row.module for row in results}


def _in_run(rows: list[dict], modules: set[str]) -> list[dict]:
    return [row for row in rows if row["module"] in modules]


def _survived(results: list[Result]) -> set[tuple]:
    return {row.ident for row in results if row.status in ALIVE}


def _named(rows: list[Result], shown: int = 5) -> str:
    more = ", ..." if len(rows) > shown else ""
    return ", ".join(row.name for row in rows[:shown]) + more


def _counted(number: int) -> str:
    return "1 mutant was" if number == 1 else f"{number} mutants were"


def unjudged_problem(results: list[Result]) -> str:
    """Why the run proves nothing, or "": mutants mutmut never judged, as when
    its stats run failed and every mutant stayed `not checked`."""
    rows = [row for row in results if row.status not in JUDGED]
    if not rows:
        return ""
    said = ", ".join(sorted({row.status for row in rows}))
    return f"{_counted(len(rows))} never judged (mutmut says {said}): {_named(rows)}"


def _idents(rows: list[dict]) -> set[tuple]:
    return {_ident(row) for row in rows}


def _gone(listed: set, alive: set) -> tuple:
    return tuple(sorted(key for key in listed if key not in alive))


def _killed(proven: set, every: set, alive: set) -> tuple:
    return tuple(sorted(key for key in proven if key in every and key not in alive))


def gate(results: list[Result], survivors: list[dict], equivalents: list[dict],
         canary: bool = True) -> Verdict:
    """The survivor-set rule over one run's results."""
    alive, every = _survived(results), {row.ident for row in results}
    modules = _mutated_modules(results)
    mine = _idents(_in_run(equivalents, modules))
    return Verdict(new=tuple(sorted(alive - _idents(survivors) - _idents(equivalents))),
                   gone=_gone(_idents(_in_run(survivors, modules)), alive),
                   killed_equivalents=_killed(mine, every, alive),
                   orphan_equivalents=tuple(sorted(mine - every)),
                   void=unjudged_problem(results) or (canary_problem(results) if canary else ""))


def updated_survivors(survivors: list[dict], verdict: Verdict) -> list[dict]:
    """The survivors table with the rows whose mutants now die removed."""
    gone = set(verdict.gone)
    return [row for row in survivors if _ident(row) not in gone]


def updated_equivalents(equivalents: list[dict], verdict: Verdict) -> list[dict]:
    dropped = set(verdict.killed_equivalents)
    return [row for row in equivalents if _ident(row) not in dropped]


_LINES = (
    ("new", "new survivor {} {} {}: kill it with a test, or add a survivors.tsv row with its "
            "reason"),
    ("orphan_equivalents", "equivalent row {} {} {} matches no mutant this run made: remove it"),
    ("gone", "listed survivor {} {} {} now dies: remove its row (gate --update)"),
    ("killed_equivalents",
     "equivalent {} {} {} now dies: it was not equivalent (gate --update drops it)"),
)


def verdict_lines(verdict: Verdict) -> list[str]:
    lines = [f"void: {verdict.void}"] if verdict.void else []
    for field, template in _LINES:
        lines += [template.format(*ident) for ident in getattr(verdict, field)]
    return lines


# --- floors ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Floor:
    group: str
    rate: float | None
    floor: float
    killed: int
    counted: int

    @property
    def ok(self) -> bool:
        return self.rate is None or self.rate >= self.floor


def _member(module: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(module, pattern) for pattern in patterns)


def _group_rate(rows: list[Result], proven: set) -> tuple[int, int]:
    counted = [row for row in rows if row.ident not in proven]
    return sum(row.status in KILLED for row in counted), len(counted)


def _patterns(group: dict) -> list[str]:
    return [pattern.strip() for pattern in group["paths"].split(",")]


def _floor(group: dict, results: list[Result], proven: set) -> Floor:
    patterns = _patterns(group)
    killed, counted = _group_rate([row for row in results if _member(row.module, patterns)],
                                  proven)
    rate = round(100 * killed / counted, 2) if counted else None
    return Floor(group["group"], rate, float(group["floor"]), killed, counted)


def floors(results: list[Result], equivalents: list[dict], groups: list[dict]) -> list[Floor]:
    """Each group's kill rate after equivalents; a group this run did not mutate has
    rate None. A timeout counts against the rate: it is not a kill."""
    proven = _idents(equivalents)
    return [_floor(group, results, proven) for group in groups]


# --- what changed since the last weekly run --------------------------------------------------

def last_weekly(now: datetime.datetime) -> datetime.datetime:
    """The most recent Saturday 06:00 UTC at or before `now`: when accuracy.yml's
    weekly run last started."""
    now = now.astimezone(datetime.timezone.utc)
    back = (now.weekday() - WEEKLY_DAY) % 7
    start = (now - datetime.timedelta(days=back)).replace(hour=WEEKLY_HOUR, minute=0, second=0,
                                                         microsecond=0)
    return start if start <= now else start - datetime.timedelta(days=7)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise MutationError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def weekly_base(repo: Path, now: datetime.datetime) -> str:
    """The first-parent commit the last weekly run measured: the newest one
    committed before it started."""
    stamp = last_weekly(now).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = _git(repo, "rev-list", "-1", "--first-parent", f"--before={stamp}", "HEAD").strip()
    if not found:
        raise MutationError(f"no commit before the weekly run of {stamp}")
    return found


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def changed_lines(diff_text: str) -> set[int]:
    """The new-side line numbers a -U0 diff touches; a pure deletion touches the
    line it sits after."""
    lines: set[int] = set()
    for match in _HUNK.finditer(diff_text):
        start, count = int(match.group(1)), int(match.group(2) or 1)
        lines.update(range(start, start + count) if count else (start,))
    return lines


def _functions(tree: ast.AST):
    """(qualified name, first line, last line) of every top-level function and
    method: the units mutmut mutates."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node.lineno, node.end_lineno
        elif isinstance(node, ast.ClassDef):
            yield from ((f"{node.name}.{name}", start, end) for name, start, end in _functions(node))


def touched_functions(source: str, lines: set[int]) -> list[str]:
    spans = _functions(ast.parse(source))
    return [name for name, start, end in spans if lines.intersection(range(start, end + 1))]


def changed_functions(repo: Path, base: str, modules: list[str]) -> list[tuple[str, str]]:
    """(module, function) for every function of `modules` a diff from `base` touches."""
    out = []
    for module in modules:
        if not (repo / module).is_file():
            continue
        lines = changed_lines(_git(repo, "diff", "-U0", base, "--", module))
        source = (repo / module).read_text(encoding="utf-8")
        out += [(module, name) for name in touched_functions(source, lines)]
    return out


# --- shards and release coverage ------------------------------------------------------------------

def shard(modules: list[str], number: int, of: int) -> list[str]:
    """Shard `number` of `of` (1-based): round-robin over the sorted modules."""
    if not 1 <= number <= of:
        raise MutationError(f"shard {number} of {of} does not exist")
    return sorted(modules)[number - 1::of]


def _covered_pairs(diffs: list[dict]) -> set[tuple]:
    complete = [receipt for receipt in diffs if receipt.get("complete")]
    return {tuple(pair) for receipt in complete for pair in receipt.get("functions", [])}


def _of_kind(receipts: list[dict], kind: str) -> list[dict]:
    return [receipt for receipt in receipts if receipt.get("kind") == kind]


def receipts_in(directory: Path) -> tuple[list[dict], list[dict]]:
    """(weekly shard receipts, nightly diff receipts) saved under `directory`."""
    loaded = [json.loads(path.read_text(encoding="utf-8"))
              for path in sorted(Path(directory).glob("*.json"))]
    return _of_kind(loaded, "weekly"), _of_kind(loaded, "diff")


def _missing_shards(weeklies: list[dict]) -> list[int]:
    of = max(receipt.get("of", 0) for receipt in weeklies)
    return sorted(set(range(1, of + 1)) - {receipt.get("shard") for receipt in weeklies})


def weekly_head(weeklies: list[dict]) -> str:
    """The commit the last weekly run mutated: one head across every shard 1..of."""
    if not weeklies:
        raise MissingReceipts(f"no weekly mutation receipt here; download them with `gh run "
                              f"download RUN_ID --pattern '{RECEIPT_ARTIFACTS}' -D {RECEIPTS}`")
    heads = sorted({str(receipt.get("head")) for receipt in weeklies})
    if len(heads) != 1:
        raise MutationError(f"the weekly receipts measured {len(heads)} heads: {', '.join(heads)}")
    missing = _missing_shards(weeklies)
    if missing:
        raise MutationError(f"weekly shards {missing} have no receipt")
    return heads[0]


def uncovered(changed: list[tuple[str, str]], diffs: list[dict]) -> list[str]:
    """The calc functions changed since the weekly run that no complete nightly
    diff receipt mutated. The weekly run mutated the tree at its head, before
    any of these changes, so it covers none of them."""
    covered = _covered_pairs(diffs)
    return [f"{module}:{name}" for module, name in changed if (module, name) not in covered]


# --- equivalence evidence -------------------------------------------------------------------------

def equivalence_evidence(original, mutant, strategy, examples: int = 10_000) -> str:
    """Run both on `examples` argument tuples from `strategy`; the evidence line,
    or MutationError naming the first input on which they differ."""
    from hypothesis import given, settings

    seen = {"count": 0}

    @settings(max_examples=examples, derandomize=True, database=None, deadline=None)
    @given(strategy)
    def same(args):
        seen["count"] += 1
        if _outcome(original, args) != _outcome(mutant, args):
            raise MutationError(f"the mutant differs on {args!r}")

    same()
    return f"{seen['count']} examples, derandomized, no difference"


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as error:  # an exception type is part of what a caller sees
        return ("raises", type(error).__name__)


# --- running mutmut ---------------------------------------------------------------------------------

def calc_modules(repo: Path = REPO) -> list[str]:
    """Every module a calcs.tsv row names, the paths mutmut mutates."""
    sys.path.insert(0, str(repo / "tests"))
    from accuracy.kit import calcs
    return calcs.modules(calcs.load(repo / "tests" / "accuracy"))


# Every mutmut call goes through the stage's launcher (see LAUNCHER below).
LAUNCHER_FILE = "mutmut_launch.py"
LAUNCH = (LAUNCHER_FILE,)


def _run_mutmut(repo: Path, args: list[str], budget: float | None, mutmut: tuple = LAUNCH,
            env: dict | None = None) -> int:
    argv = [sys.executable, *mutmut, *args]
    try:
        return subprocess.run(argv, cwd=repo, timeout=budget, env=env).returncode
    except subprocess.TimeoutExpired:
        return -1


def _meta_statuses(repo: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for meta in sorted((repo / "mutants").rglob("*.meta")):
        codes = json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"]
        statuses.update({name: STATUS_BY_EXIT.get(code, "suspicious")
                         for name, code in codes.items()})
    return statuses


def parse_diffs(printed: str) -> dict[str, str]:
    """The launcher's `diffs` output, one JSON [name, diff] pair per line, as a map;
    anything else mutmut prints on the way is not a pair and is left out."""
    pairs = [json.loads(line) for line in printed.splitlines() if line.startswith('["')]
    return {name: diff for name, diff in pairs}


def _diffs(repo: Path, names: list[str], mutmut: tuple) -> dict[str, str]:
    """Every named mutant's diff from one process: a `mutmut show` per mutant starts
    Python once each, about a second apiece, and a run can leave thousands alive."""
    if not names:
        return {}
    done = subprocess.run([sys.executable, *mutmut, "diffs"], cwd=repo, input="\n".join(names),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = parse_diffs(done.stdout)
    missing = [name for name in names if not found.get(name)]
    if missing:
        raise MutationError(f"no diff for {len(missing)} mutant(s) ({', '.join(missing[:3])}): "
                            f"{done.stderr.strip()[-500:]}")
    return found


def _wanted(name: str, globs: list[str] | None) -> bool:
    return globs is None or any(fnmatch.fnmatchcase(name, glob) for glob in globs)


def keyed_names(statuses: dict[str, str]) -> list[str]:
    """Survivors, unreached mutants and timeouts carry their key; a kill needs none."""
    return [name for name, status in statuses.items() if status in ALIVE | {TIMEOUT}]


def collect(repo: Path, wanted: list[str] | None = None, mutmut: tuple = LAUNCH) -> list[Result]:
    """Results from mutmut's meta files, for the mutant names `wanted` globs match."""
    statuses = {name: status for name, status in sorted(_meta_statuses(repo).items())
                if _wanted(name, wanted)}
    diffs = _diffs(repo, keyed_names(statuses), mutmut)
    return [result(name, status, diffs.get(name, ""), repo) for name, status in statuses.items()]


def _rerun_timeouts(repo: Path, rows: list[Result], mutmut: tuple = LAUNCH,
                    env: dict | None = None) -> list[Result]:
    """One serial rerun per timeout; what still times out stays a timeout."""
    names = [row.name for row in rows if row.status == TIMEOUT]
    if not names:
        return rows
    _run_mutmut(repo, ["run", "--max-children", "1", *names], None, mutmut, env)
    return collect(repo, [row.name for row in rows], mutmut)


def _receipt(kind: str, **fields) -> dict:
    return {"schema": 1, "kind": kind, "head": _git(REPO, "rev-parse", "HEAD").strip(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}


def _write_receipt(receipt: dict, name: str) -> Path:
    path = REPO / RECEIPTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _globs_for(modules: list[str]) -> list[str]:
    return [f"{_dotted(module)}.*" for module in modules]


def _canary_globs() -> list[str]:
    return [mutmut_glob(*CANARY)]


# --- the second config: the accuracy tools and kit.exact -----------------------------------------

TOOLS_STAGE = RECEIPTS / "tools-stage"
# Each module the second config mutates, with the tests that exercise it. A
# target another packet brings is staged once its source is in the tree.
TOOL_TARGETS = {
    "tests/accuracy/kit/exact.py": ("tests/accuracy/kit/test_exact.py",),
    "tools/accuracy/retro.py": ("tests/accuracy/suite_strength/test_retro_tool.py",),
    "tools/accuracy/mutation.py": ("tests/accuracy/suite_strength/test_mutation_tool.py",
                                   "tests/accuracy/suite_strength/test_mutation_floors.py"),
    "tools/accuracy/run.py": ("tests/accuracy/kit/test_run_tool.py",
                              "tests/accuracy/kit/test_run_tool_commands.py",
                              "tests/accuracy/suite_strength/test_runner_targets.py"),
    "tools/accuracy/change_control.py": ("tests/accuracy/change_control",),
    "tools/accuracy/wheel_diff.py": ("tests/accuracy/corpus_goldens/test_wheel_diff_tool.py",),
}
LAUNCHER = '''"""mutmut 3.8.0 for the accuracy tools, started in the stage by tools/accuracy/mutation.py.

mutmut names a module by its path with `src.` left off, and a trampoline runs a
mutant only inside the module of that name. The kit imports as `accuracy.*`
from tests/, and tests load a tool by path under a name of their own, so here
`tests.` is left off too, and a mutated file loaded by path takes the name
mutmut gave it.
"""
import importlib.util
from pathlib import Path
import tomllib


def canonical(relative: str) -> str:
    dotted = relative.removesuffix(".py").replace("/", ".")
    for prefix in ("src.", "tests."):
        dotted = dotted.removeprefix(prefix)
    return dotted


# --- mutmut ---
import mutmut.utils.format_utils as names

CONFIG = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
SOURCES = set(CONFIG["source_paths"])
MUTANTS = Path("mutants").resolve()
_strip, _spec = names.strip_prefix, importlib.util.spec_from_file_location


def strip_prefix(text, *, prefix, strict=False):
    text = _strip(text, prefix=prefix, strict=strict)
    return _strip(text, prefix="tests.") if prefix == "src." else text


def _mutated(location):
    try:
        relative = Path(location).resolve().relative_to(MUTANTS).as_posix()
    except (TypeError, ValueError):
        return None
    return relative if relative in SOURCES else None


def spec_from_file_location(name, location=None, *args, **kwargs):
    relative = _mutated(location)
    return _spec(canonical(relative) if relative else name, location, *args, **kwargs)


names.strip_prefix = strip_prefix
importlib.util.spec_from_file_location = spec_from_file_location


def diffs():
    """`diffs`: the diff of every mutant named on stdin, one JSON [name, diff] line each."""
    import json
    import sys
    from mutmut.mutation.diff_apply import get_diff_for_mutant
    for name in sys.stdin.read().split():
        try:
            print(json.dumps([name, get_diff_for_mutant(name)]), flush=True)
        except Exception as missed:  # the caller names every mutant left without a diff
            print(f"{name}: {missed!r}", file=sys.stderr)


import sys
if sys.argv[1:2] == ["diffs"]:
    diffs()
else:
    from mutmut.__main__ import cli
    cli()
'''


def present(targets: dict, repo: Path = REPO) -> dict:
    """The targets whose source this tree holds."""
    return {path: tests for path, tests in targets.items() if (repo / path).is_file()}


def _stage_table(targets: dict, copies: list[str]) -> str:
    tests = sorted({test for listed in targets.values() for test in listed})
    return "\n".join((
        "[tool.mutmut]",
        f"source_paths = {json.dumps(sorted(targets))}",
        f"pytest_add_cli_args_test_selection = {json.dumps(tests)}",
        f"pytest_add_cli_args = {json.dumps(['-p', 'no:cacheprovider', '-m', FLOOR_SUITE])}",
        f"also_copy = {json.dumps(copies)}")) + "\n"


def stage_config(text: str, targets: dict, copies: list[str]) -> str:
    """The repo's pyproject.toml with a [tool.mutmut] table for `targets` in place of
    its own, copying `copies` beside the mutants."""
    head, _, rest = text.partition("[tool.mutmut]")
    tail = rest[rest.index("\n["):] if "\n[" in rest else ""
    return f"{head.rstrip()}\n\n{tail.strip()}\n\n{_stage_table(targets, copies)}"


def stage_copies(stage: Path) -> list[str]:
    """Every top-level entry HEAD tracks: the tools' tests read README.md, docs/
    and the kit's data as well as tests/ and tools/."""
    tracked = _git(stage, "ls-tree", "--name-only", "HEAD").splitlines()
    return sorted(set(tracked) - {"mutants"})


def _stage(repo: Path, stage: Path) -> Path:
    """A detached worktree of `repo`'s HEAD; mutants/ stays, so mutmut keeps its cache."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    if not (stage / ".git").exists():
        stage.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "-f", "--detach", str(stage), head)
    _git(stage, "checkout", "-q", "-f", "--detach", head)
    return stage


def tools_env(environ: dict) -> dict:
    """Every tier's tests with push-sized Hypothesis settings: the floors count what
    a nightly test kills too, at the cost of a push run."""
    return {**environ, "CRAPKIT_ACCURACY_TIER": "push", "CRAPKIT_ACCURACY_COLLECT_ALL": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}


# The weekly and nightly runs: the calc modules against tests/unit and the
# accuracy tests, in a stage of their own, so the repo's [tool.mutmut] (which
# names no tests and no marker) never decides the suite the floors count.
CALC_STAGE = RECEIPTS / "calc-stage"
CALC_TESTS = ("tests/unit", "tests/accuracy")


def calc_targets(modules: list[str]) -> dict:
    """The modules a calc run mutates, score.py (the canary's home) always among them."""
    return {module: CALC_TESTS for module in sorted({*modules, CANARY[0]})}


def calc_env(environ: dict) -> dict:
    """The push tier on this platform: every tier would bring in tests marked for
    another platform, which fail mutmut's stats run."""
    env = {key: value for key, value in environ.items() if key != "CRAPKIT_ACCURACY_COLLECT_ALL"}
    return {**env, "CRAPKIT_ACCURACY_TIER": "push", "PYTHONDONTWRITEBYTECODE": "1"}


def _prepare_stage(targets: dict, where: Path = TOOLS_STAGE) -> Path:
    stage = _stage(REPO, REPO / where)
    pyproject = stage / "pyproject.toml"
    pyproject.write_text(stage_config(pyproject.read_text(encoding="utf-8"), targets,
                                      stage_copies(stage)), encoding="utf-8")
    (stage / LAUNCHER_FILE).write_text(LAUNCHER, encoding="utf-8")
    return stage


def staged_run(where: Path, targets: dict, globs: list[str], env: dict, children: int,
               budget: float | None = None) -> tuple[list[Result], bool]:
    """mutmut over `globs` in a stage whose [tool.mutmut] names `targets`: the results,
    and whether the run finished inside `budget` seconds (a capped run reruns nothing)."""
    stage = _prepare_stage(targets, where)
    code = _run_mutmut(stage, ["run", "--max-children", str(children), *globs], budget, LAUNCH, env)
    rows = collect(stage, globs, LAUNCH)
    if code == -1:
        return rows, False
    return _rerun_timeouts(stage, rows, LAUNCH, env), True


def _tools(args) -> int:
    targets = present(TOOL_TARGETS)
    rows, _ = staged_run(TOOLS_STAGE, targets, _globs_for(list(targets)),
                         tools_env(dict(os.environ)), args.max_children)
    receipt = _receipt("tools", modules=sorted(targets), results=[asdict(row) for row in rows])
    _write_receipt(receipt, "tools.json")
    return _judge(rows, update=False, canary=False)


# --- commands ----------------------------------------------------------------------------------------

def _tables() -> tuple[list, list, list]:
    return (read_table(TABLES / "survivors.tsv", SURVIVOR_COLUMNS),
            read_table(TABLES / "equivalent.tsv", EQUIVALENT_COLUMNS),
            read_table(TABLES / "floors.tsv", FLOOR_COLUMNS))


def _floors_hold(results: list[Result], equivalents: list[dict], groups: list[dict]) -> bool:
    checked = floors(results, equivalents, groups)
    for floor in checked:
        _print_floor(floor)
    return all(floor.ok for floor in checked)


def _update(survivors: list[dict], equivalents: list[dict], verdict: Verdict) -> None:
    write_table(TABLES / "survivors.tsv", SURVIVOR_COLUMNS, updated_survivors(survivors, verdict))
    write_table(TABLES / "equivalent.tsv", EQUIVALENT_COLUMNS,
                updated_equivalents(equivalents, verdict))


def _judge(results: list[Result], update: bool, canary: bool = True) -> int:
    survivors, equivalents, groups = _tables()
    verdict = gate(results, survivors, equivalents, canary=canary)
    for line in verdict_lines(verdict):
        print(f"mutation: {line}")
    held = _floors_hold(results, equivalents, groups)
    if update:
        _update(survivors, equivalents, verdict)
    return 0 if verdict.passed and held else 1


def _print_floor(floor: Floor) -> None:
    rate = "not mutated" if floor.rate is None else f"{floor.rate}% ({floor.killed}/{floor.counted})"
    mark = "ok" if floor.ok else "BELOW"
    print(f"mutation: floor {floor.group}: {rate}, floor {floor.floor}% {mark}")


def _weekly(args) -> int:
    modules = shard(calc_modules(), args.shard, args.of)
    globs = _globs_for(modules) + _canary_globs()
    rows, _ = staged_run(CALC_STAGE, calc_targets(modules), globs, calc_env(dict(os.environ)),
                         args.max_children)
    receipt = _receipt("weekly", shard=args.shard, of=args.of, modules=modules,
                       results=[asdict(row) for row in rows])
    _write_receipt(receipt, f"weekly-{args.shard}.json")
    return _judge(rows, update=False)


def _run_changed(changed: list, budget: float) -> tuple[list[Result], bool]:
    """The results for the changed functions, and whether the run finished inside
    `budget` seconds."""
    if not changed:
        return [], True
    targets = calc_targets([path for path, _ in changed])
    return staged_run(CALC_STAGE, targets, [mutmut_glob(*pair) for pair in changed],
                      calc_env(dict(os.environ)), os.cpu_count() or 2, budget)


def _diff_receipt(base: str, changed: list, rows: list[Result], complete: bool) -> dict:
    return _receipt("diff", base=base, functions=[list(pair) for pair in changed],
                    complete=complete, results=[asdict(row) for row in rows])


def _diff_run(args) -> int:
    base = args.base or weekly_base(REPO, datetime.datetime.now(datetime.timezone.utc))
    changed = changed_functions(REPO, base, calc_modules())
    rows, complete = _run_changed(changed, args.cap_minutes * 60)
    receipt = _diff_receipt(base, changed, rows, complete)
    _write_receipt(receipt, f"diff-{receipt['head'][:12]}.json")
    if not complete:
        print(f"mutation: incomplete: the {args.cap_minutes:g}-minute cap stopped the run")
        return 1
    return _judge(rows, update=False, canary=False)


def _gate(args) -> int:
    return _judge(load_results(args.results), update=args.update, canary=not args.no_canary)


def _floors_cmd(args) -> int:
    _, equivalents, groups = _tables()
    results = load_results(args.results)
    checked = floors(results, equivalents, groups)
    for floor in checked:
        _print_floor(floor)
    return 0 if all(floor.ok for floor in checked) else 1


def _covered(args) -> int:
    weeklies, diffs = receipts_in(args.receipts)
    head = weekly_head(weeklies)
    missing = uncovered(changed_functions(REPO, head, calc_modules()), diffs)
    for line in missing:
        print(f"mutation: {line} changed since the weekly run at {head[:12]} and no "
              "complete diff run mutated it")
    return 1 if missing else 0


def _key(args) -> int:
    print(mutant_key(sys.stdin.read()))
    return 0


def killer_env(cwd: Path, environ: dict) -> dict:
    """The environment the killer suite runs under: this tree's src/ and tests/ first,
    and the push tier whatever tier the caller runs."""
    paths = [str(cwd / "src"), str(cwd / "tests"), environ.get("PYTHONPATH", "")]
    return {**environ, "PYTHONPATH": os.pathsep.join(filter(None, paths)),
            "PYTHONDONTWRITEBYTECODE": "1", "CRAPKIT_ACCURACY_TIER": "push"}


def killer_argv(extra: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", "tests/unit", "tests/accuracy", "-m", INDEPENDENT_ONLY,
            "-n", "4", "--dist", "worksteal", "-x", "-q", "-p", "no:randomly",
            "-p", "no:cacheprovider", *extra]


def _killer(args) -> int:
    cwd = Path.cwd()
    return subprocess.run(killer_argv(args.pytest), cwd=cwd,
                          env=killer_env(cwd, dict(os.environ))).returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mutation.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    weekly = sub.add_parser("weekly")
    weekly.add_argument("--shard", type=int, required=True)
    weekly.add_argument("--of", type=int, required=True)
    weekly.add_argument("--max-children", type=int, default=os.cpu_count() or 2)
    diff = sub.add_parser("diff")
    diff.add_argument("--since-weekly", action="store_true")
    diff.add_argument("--base")
    diff.add_argument("--cap-minutes", type=float, default=30)
    gate_p = sub.add_parser("gate")
    gate_p.add_argument("results", nargs="+", type=Path)
    gate_p.add_argument("--update", action="store_true")
    gate_p.add_argument("--no-canary", action="store_true")
    floors_p = sub.add_parser("floors")
    floors_p.add_argument("results", nargs="+", type=Path)
    covered = sub.add_parser("covered")
    covered.add_argument("--receipts", type=Path, default=REPO / RECEIPTS)
    tools = sub.add_parser("tools")
    tools.add_argument("--max-children", type=int, default=os.cpu_count() or 2)
    sub.add_parser("key")
    killer = sub.add_parser("killer")
    killer.add_argument("pytest", nargs=argparse.REMAINDER)
    return parser


COMMANDS = {"weekly": _weekly, "diff": _diff_run, "gate": _gate, "floors": _floors_cmd,
            "covered": _covered, "tools": _tools, "key": _key, "killer": _killer}


def _args(argv: list[str]) -> argparse.Namespace:
    """The parsed command. Every word after `killer` is pytest's, dashes included:
    argparse's REMAINDER refuses one that starts with a dash."""
    if argv[:1] == ["killer"]:
        return argparse.Namespace(command="killer", pytest=argv[1:])
    return _parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _args(sys.argv[1:] if argv is None else argv)
    try:
        return COMMANDS[args.command](args)
    except MutationError as refused:
        print(f"mutation.py: {refused}", file=sys.stderr)
        return 3 if isinstance(refused, MissingReceipts) else 1


if __name__ == "__main__":
    sys.exit(main())
