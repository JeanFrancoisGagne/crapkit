"""Mutation testing of the calculation modules, gated on a keyed survivor set.

    python tools/accuracy/mutation.py weekly --shard N --of M [--max-children K] [--cold]
    python tools/accuracy/mutation.py diff [--cap-minutes 30] [--cold]
    python tools/accuracy/mutation.py gate RESULTS.json... [--update]
    python tools/accuracy/mutation.py floors RESULTS.json...
    python tools/accuracy/mutation.py covered [--receipts DIR]
    python tools/accuracy/mutation.py tools [--max-children K]
    python tools/accuracy/mutation.py key < MUTANT.diff
    python tools/accuracy/mutation.py killer [PYTEST ARGS...]

mutmut 3.8.0 runs in the accuracy image (it forks, so Linux only). The calc
runs mutate the modules every tests/accuracy/*/calcs.tsv row names (a cli
module and the release tool only at the functions a row names, and never a
module `tools` mutates; weekly_modules). `weekly` judges one shard of them, and
`diff` all of them under a cap, reporting `incomplete`, never `pass`, when the
cap stops it. Neither judges a function whose stored verdicts still hold (see
"Carrying a verdict" below): a second run at an unchanged tree judges no
mutant. Both run
in a detached worktree of HEAD (.crapkit/accuracy/mutation/calc-stage) whose
[tool.mutmut] names every one of those modules, so one stats pass maps the
tests of all of them, and the suite, tests/unit and tests/accuracy at the push
tier with the dependent methods deselected, less each test an open defect row
of rulings.tsv names as failing on a clean tree and each COPY_BOUND test, which
fails or runs for hours inside mutmut's copy whatever the mutant, then write a
receipt under .crapkit/accuracy/mutation/ and run the gate. The checks that read
crapkit's own source as data read the stage's src/crapkit, named in
CRAPKIT_ACCURACY_SOURCE, since mutmut's copy of it holds trampolines.

mutmut first runs the whole suite once with no mutant active (its stats run) to
learn which tests reach which function, stops at the first test that fails, and
then judges no mutant at all. The stage's launcher runs it to the end instead:
a test that fails there fails whatever the mutant, so it is left out of every
mutant's tests and named in the stage's stats-failures.txt, which the run prints
and its receipt keeps under `stats_failures`.

A run mutmut ends with anything but 0 (a failed stats run, a crash, a signal,
SIGHUP included) judged nothing, the serial rerun of the unfinished mutants
included: the command writes no receipt, names how mutmut ended and each
in-process call the test kit logged as stuck in the stage's in-process-hangs.log,
and exits 4. So does a run where the diffs launcher died by a signal or the kit
logged a stuck call though mutmut ended with 0: a stuck call fails its test
whatever the mutant, and a crash on a degraded host can fail one too, and either
reads as a kill. No verdict of a mutant whose test process a signal ended (a
timeout or mutmut's `segfault`) carries to another run. mutmut runs its stats
pass in its own process, so every run names that file to the kit (HANGS_ENV),
and the kit then never ends the process on a stuck call. `covered` refuses a receipt holding a mutant its run never judged,
and a diff receipt covers a changed function only when it holds a mutant of
that function and its head holds the function's text as HEAD does (uncovered).

Carrying a verdict. Each weekly and diff receipt stores, beside its results,
what each function's verdicts rest on (carry_problem): the function's key (its
text with its decorators and its module's code outside any function), the
covering tests mutmut's stats pass mapped to it, every file those tests read,
each folder they list, whether they start a program, and the functions they
also run; and per run the environment key (Python, the installed packages,
dpkg's list, the launcher and the stage's pytest and mutmut tables), the tree
it judged and what the suite read outside any test. A later run carries those
verdicts only while every one of those still holds, any .py file outside the
mutated modules and the test modules is unchanged, and the verdicts are under
28 days old; a surviving or unfinished mutant's verdict holds only at the tree
it was judged at. A run with --cold carries nothing and compares what it
judges with what would have carried; a difference voids every older receipt.

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
Timeouts, and mutants whose test process a signal ended (mutmut's `segfault`),
get one serial rerun and never count as kills.

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
from dataclasses import asdict, dataclass, field
import datetime
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib

REPO = Path(__file__).resolve().parents[2]
# calc_modules reads the calcs.tsv tables through the kit (accuracy.kit.calcs).
sys.path.insert(0, str(REPO / "tests"))
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
# A test process that died by SIGSEGV or SIGKILL: a mutant that never finishes
# (endless recursion, a loop that fills memory until the kernel stops it).
SEGFAULT = "segfault"
# A mutant no test reaches lives as surely as one the tests run and miss.
ALIVE = frozenset({SURVIVED, NO_TESTS})
# The verdicts that neither kill nor keep a mutant: each gets one serial rerun.
UNFINISHED = frozenset({TIMEOUT, SEGFAULT})
# Every status that is a verdict on the mutant. Anything else ("not checked",
# "suspicious") means mutmut never judged it, and the run proves nothing.
JUDGED = KILLED | ALIVE | UNFINISHED | {"skipped"}
STATUS_BY_EXIT = {1: "killed", 3: "killed", 0: SURVIVED, 5: "no tests", 33: "no tests",
                  34: "skipped", 36: TIMEOUT, 37: "caught by type check", -24: TIMEOUT,
                  24: TIMEOUT, 152: TIMEOUT, 255: TIMEOUT, -11: SEGFAULT, -9: SEGFAULT,
                  None: "not checked"}
# The floors' suite: every test but those comparing crapkit with a copy of itself.
FLOOR_SUITE = "not golden and not change_control and not cross_surface"
# The killer suite also leaves out every test that spawns git, node, pwsh or the CLI.
INDEPENDENT_ONLY = f"not process and {FLOOR_SUITE}"
SEPARATOR = "ǁ"  # mutmut's class separator in a mangled method name


class MutationError(ValueError):
    """A table, receipt or argument this tool cannot use."""


class MissingReceipts(MutationError):
    """The receipts a release row reads are not on this machine: an infra miss, exit 3."""


class RunDied(MutationError):
    """mutmut ended before it judged the run's mutants: the run proves nothing and
    writes no receipt, exit 4."""


# Exit 1 is a check that failed; these name a run that measured nothing.
EXIT_BY_REFUSAL = {MissingReceipts: 3, RunDied: 4}


def _read(path: Path) -> str:
    """A file's text; every file this tool reads is UTF-8."""
    return path.read_bytes().decode()


def _write(path: Path, text: str) -> None:
    """Write UTF-8 text with the newlines as given, on every OS."""
    path.write_bytes(text.encode())


def _text(raw: bytes) -> str:
    """A child's output; a byte that is not UTF-8 reads as U+FFFD, never an error."""
    return raw.decode(errors="replace")


# accuracy.yml uploads each weekly shard's and each nightly diff run's receipt
# under this artifact name pattern; the release row reads them from RECEIPTS.
RECEIPT_ARTIFACTS = "mutation-receipt-*"


# --- keys ----------------------------------------------------------------------------------

_NUMBERING = re.compile(r"__mutmut_(?:\d+|orig)\b")


def normalized_diff(text: str) -> list[str]:
    """The changed lines of a unified diff, with the line numbers (the @@ headers),
    the file headers and mutmut's per-mutant numbering removed."""
    lines = []
    for line in text.split("\n"):
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        lines.append(_NUMBERING.sub("__mutmut", line.rstrip()))
    return lines


def mutant_key(diff_text: str) -> str:
    """sha256 over the normalized diff: the survivor's identity within its function."""
    return hashlib.sha256("\n".join(normalized_diff(diff_text)).encode()).hexdigest()


def split_name(mutant_name: str) -> tuple[str, str]:
    """(dotted module, qualified function) of a mutmut mutant name such as
    crapkit.score.x_crap__mutmut_3 or crapkit.store.xǁStoreǁwrite__mutmut_2."""
    prefix, _, number = mutant_name.partition("__mutmut_")
    module, _, mangled = prefix.rpartition(".")
    if not (prefix and module and number.isdigit()):
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


def mangled(path: str, qualname: str) -> str:
    """The name mutmut files a function under in its stats map and its meta files:
    crapkit.score.x_crap, crapkit.store.xǁStoreǁwrite."""
    owner, _, name = qualname.rpartition(".")
    tail = f"x{SEPARATOR}{owner}{SEPARATOR}{name}" if owner else f"x_{name}"
    return f"{_dotted(path)}.{tail}"


def mutmut_glob(path: str, qualname: str) -> str:
    """The mutmut mutant-name glob for one function: a calc run's filter."""
    return f"{mangled(path, qualname)}__mutmut_*"


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


def result(name: str, status: str, diff_text: str | None, repo: Path = REPO) -> Result:
    dotted, function = split_name(name)
    key = mutant_key(diff_text) if diff_text else ""
    return Result(name, module_path(dotted, repo), function, status, key)


def load_results(paths: list[Path]) -> list[Result]:
    rows = []
    for path in paths:
        rows += [Result(**row) for row in json.loads(_read(Path(path)))["results"]]
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
    return [line for line in _read(path).splitlines() if line.strip()]


def _check_header(path: Path, lines: list[str], columns: tuple[str, ...]) -> None:
    if not lines or tuple(lines[0].split("\t")) != columns:
        raise MutationError(f"{path}: the header must be {' '.join(columns)} (tab-separated)")


def _cells(path: Path, number: int, line: str, columns: tuple) -> dict:
    cells = line.split("\t")
    if len(cells) != len(columns):
        raise MutationError(f"{path}:{number}: {len(cells)} cells, the header has {len(columns)}")
    return dict(zip(columns, cells))


def write_table(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    """A cell holding a tab or line break would read back as a different row, so it
    is refused before anything is written."""
    for row in rows:
        _check_cells(path, row, columns)
    body = ["\t".join(columns)] + ["\t".join(row[column] for column in columns) for row in rows]
    _write(path, "\n".join(body) + "\n")


def _check_cells(path: Path, row: dict, columns: tuple[str, ...]) -> None:
    for column in columns:
        if set(row[column]) & {"\t", "\n", "\r"}:
            raise MutationError(f"{path}: the {column} cell of {' '.join(_ident(row))} "
                                "holds a tab or line break")


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
    """The survivor-set rule over one run's results. A void run names no row to
    remove: a mutant mutmut never judged carries no key, so every listed row of
    its modules would read as gone, and `gate --update` would empty the tables."""
    void = unjudged_problem(results) or (canary_problem(results) if canary else "")
    alive = _survived(results)
    new = tuple(sorted(alive - _idents(survivors) - _idents(equivalents)))
    if void:
        return Verdict(new=new, void=void)
    every, modules = {row.ident for row in results}, _mutated_modules(results)
    mine = _idents(_in_run(equivalents, modules))
    return Verdict(new=new, gone=_gone(_idents(_in_run(survivors, modules)), alive),
                   killed_equivalents=_killed(mine, every, alive),
                   orphan_equivalents=tuple(sorted(mine - every)))


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
    for name, template in _LINES:
        lines += [template.format(*ident) for ident in getattr(verdict, name)]
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

def captured(argv: list, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    """argv's output as text; a byte that is not UTF-8 reads as U+FFFD, never an error."""
    fed = stdin.encode() if stdin is not None else None
    done = subprocess.run(argv, cwd=cwd, input=fed, capture_output=True)
    done.stdout, done.stderr = _text(done.stdout), _text(done.stderr)
    return done


def _git(repo: Path, *args: str) -> str:
    done = captured(["git", *args], repo)
    if done.returncode != 0:
        raise MutationError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


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
    method: the units mutmut mutates. A def starts at its first decorator, since
    a decorator changes what the function does."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, _first_line(node), node.end_lineno
        elif isinstance(node, ast.ClassDef):
            yield from ((f"{node.name}.{name}", start, end) for name, start, end in _functions(node))


def _first_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


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
        source = _read(repo / module)
        out += [(module, name) for name in touched_functions(source, lines)]
    return out


# --- shards and release coverage ------------------------------------------------------------------

def shard(modules: list[str], number: int, of: int) -> list[str]:
    """Shard `number` of `of` (1-based): round-robin over the sorted modules."""
    if not 1 <= number <= of:
        raise MutationError(f"shard {number} of {of} does not exist")
    return sorted(modules)[number - 1::of]


def receipt_problem(receipt: dict) -> str:
    """Why a receipt proves nothing, or "": a mutant its run never judged, as every
    mutant of a run whose stats pass died is. Receipts written before a dead run
    raised RunDied hold such rows."""
    return unjudged_problem([Result(**row) for row in receipt.get("results", [])])


def _covers(diff: dict) -> bool:
    return diff["complete"] and not receipt_problem(diff)


def function_texts(source: str) -> dict[str, str]:
    """The text of each top-level function and method, its decorators included,
    by qualified name; a name defined twice holds both texts, in order."""
    lines, texts = io.StringIO(source, newline="").readlines(), {}
    for name, first, last in _functions(ast.parse(source)):
        texts[name] = texts.get(name, "") + "".join(lines[first - 1:last])
    return texts


def function_text(repo: Path):
    """A reader of one function's text at a commit of `repo`, as function_texts
    gives it: "" when the module there holds no such function, None when this
    clone cannot read the module there. One git read per commit and module."""
    read: dict[tuple[str, str], dict[str, str] | None] = {}

    def text(commit: str, module: str, name: str) -> str | None:
        if (commit, module) not in read:
            read[commit, module] = _texts_at(repo, commit, module)
        texts = read[commit, module]
        return None if texts is None else texts.get(name, "")
    return text


def _texts_at(repo: Path, commit: str, module: str) -> dict[str, str] | None:
    try:
        return function_texts(_git(repo, "cat-file", "blob", f"{commit}:{module}"))
    except MutationError:
        return None


# A diff receipt's head is the full sha `git rev-parse HEAD` printed. Anything
# else (a ref, an empty head, which git reads as the index) names no commit.
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
NOT_MUTATED = "no complete diff run mutated it"


def _mutated(receipt: dict, module: str, name: str) -> bool:
    """Whether `receipt` holds a mutant of the function; every row of a receipt
    that passes _covers is judged."""
    return any((row["module"], row["function"]) == (module, name)
               for row in receipt.get("results", []))


def _why_not(receipt: dict, module: str, name: str, text) -> str:
    """"" when `receipt`, complete and judged, covers the function; else why not."""
    head = str(receipt.get("head", ""))
    if not _mutated(receipt, module, name):
        return f"the diff run at {head[:12]} made no mutant of it"
    if not _COMMIT.fullmatch(head):
        return "a diff receipt that mutated it names no head commit"
    return _changed_since(head, module, name, text)


def _changed_since(head: str, module: str, name: str, text) -> str:
    """"" when the function's text at `head` is its text at HEAD; else how not."""
    measured = text(head, module, name)
    if measured is None:
        return f"this clone cannot read {module} at the head of the diff run at {head[:12]}"
    if measured and measured == text("HEAD", module, name):
        return ""
    return f"changed again after the diff run at {head[:12]}"


def _why_uncovered(diffs: list[dict], module: str, name: str, text) -> str:
    """"" when one of `diffs` covers the function; else each listing receipt's
    reason, or NOT_MUTATED when none lists it."""
    reasons = []
    for receipt in diffs:
        if (module, name) not in map(tuple, receipt["functions"]):
            continue
        reason = _why_not(receipt, module, name, text)
        if not reason:
            return ""
        reasons.append(reason)
    return "; ".join(dict.fromkeys(reasons)) or NOT_MUTATED


def _of_kind(receipts: list[dict], kind: str) -> list[dict]:
    return [receipt for receipt in receipts if receipt.get("kind") == kind]


def receipts_in(directory: Path) -> tuple[list[dict], list[dict]]:
    """(weekly shard receipts, nightly diff receipts) saved under `directory`."""
    loaded = [json.loads(_read(path))
              for path in sorted(Path(directory).glob("*.json"))]
    return _of_kind(loaded, "weekly"), _of_kind(loaded, "diff")


def _missing_shards(weeklies: list[dict]) -> list[int]:
    of = max(receipt["of"] for receipt in weeklies)
    return sorted(set(range(1, of + 1)) - {receipt["shard"] for receipt in weeklies})


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
    _refuse_unjudged(weeklies)
    return heads[0]


def _refuse_unjudged(weeklies: list[dict]) -> None:
    for receipt in weeklies:
        problem = receipt_problem(receipt)
        if problem:
            raise MutationError(f"weekly shard {receipt['shard']}'s receipt proves nothing: {problem}")


def uncovered(changed: list[tuple[str, str]], diffs: list[dict], text) -> list[tuple[str, str]]:
    """("module:function", why) for each of the `changed` calc functions (see
    since) that no nightly diff receipt covers. The weekly run mutated the tree
    at its head in the scope its tables gave, so it covers none of them. A diff
    receipt covers a function only when its run finished and judged every mutant,
    it holds a mutant of the function, and the function's text at the receipt's
    head, decorators included, is its text at HEAD. mutmut 3.8 makes no mutant of
    a function decorated with anything but a lone staticmethod or classmethod, nor
    of one with nothing it mutates, so no receipt covers such a function.
    `text(commit, module, name)` reads a function's text (function_text)."""
    judged = list(filter(_covers, diffs))
    found = [(f"{module}:{name}", _why_uncovered(judged, module, name, text))
             for module, name in changed]
    return [(function, why) for function, why in found if why]


# --- equivalence evidence -------------------------------------------------------------------------

EXAMPLES = 10_000  # the plan: an equivalent carries 10,000-example evidence


def equivalence_evidence(original, mutant, strategy, examples: int = EXAMPLES) -> str:
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
    from accuracy.kit import calcs
    return calcs.modules(calcs.load(repo / "tests" / "accuracy"))


# Every mutmut call goes through the stage's launcher (see LAUNCHER below).
LAUNCHER_FILE = "mutmut_launch.py"
LAUNCH = (LAUNCHER_FILE,)


# The file each mutmut run names to the test kit (tests/e2e/cli_in_process.py,
# STAGE_HANGS_ENV): an in-process CLI call stuck in C code past its bound leaves
# its stacks there and the process lives, since mutmut's stats run is mutmut's
# own process and it times each mutant's child itself.
HANGS_ENV = "CRAPKIT_IN_PROCESS_HANGS"
HANGS_FILE = "in-process-hangs.log"


def _run_mutmut(repo: Path, args: list[str], budget: float | None, mutmut: tuple = LAUNCH,
            env: dict | None = None) -> int | None:
    """mutmut's exit status, or None when `budget` seconds ran out first. None is
    no exit status: subprocess reports a death by signal N as -N, so -1 is SIGHUP."""
    argv = [sys.executable, *mutmut, *args]
    env = {**(os.environ if env is None else env), HANGS_ENV: str(Path(repo).resolve() / HANGS_FILE)}
    try:
        return subprocess.run(argv, cwd=repo, timeout=budget, env=env).returncode
    except subprocess.TimeoutExpired:
        return None


def died(repo: Path, code: int | None, run: str = "mutmut's run") -> str:
    """Why mutmut's run in `repo` proves nothing, or "". mutmut ends a run it
    finished with 0, and None is the budget stopping it; any other end (a failed
    stats run, a crash, a signal) leaves its mutants unjudged. A run that finished
    proves nothing either when the test kit logged a call stuck past its bound:
    that call fails its test whatever the mutant, which reads as a kill."""
    if code is not None and code != 0:
        return (f"{run} in {repo} ended with {_ending(code)} before it judged its mutants, "
                f"so the run proves nothing and writes no receipt{_stuck_calls(repo)}")
    stuck = _stuck_calls(repo)
    return (f"{run} in {repo} logged an in-process call stuck past its bound, so the run "
            f"keeps no verdict and writes no receipt{stuck}") if stuck else ""


def _refuse_dead(repo: Path, code: int | None, run: str = "mutmut's run") -> None:
    """RunDied when mutmut's `run` in `repo` ended before it judged its mutants."""
    problem = died(repo, code, run)
    if problem:
        raise RunDied(problem)


def _ending(code: int) -> str:
    if code > 0:
        return f"exit {code}"
    try:
        return f"signal {signal.Signals(-code).name}"
    except ValueError:
        return f"signal {-code}"


def _stuck_calls(repo: Path) -> str:
    """The in-process calls the kit logged as stuck past their bound in this run."""
    log = Path(repo) / HANGS_FILE
    stuck = [line for line in _read(log).splitlines() if "past its" in line] if log.is_file() else []
    return "".join(f"\n  stuck: {line}" for line in stuck)


def _meta_statuses(repo: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for meta in sorted((repo / "mutants").rglob("*.meta")):
        codes = json.loads(_read(meta))["exit_code_by_key"]
        statuses.update({name: STATUS_BY_EXIT.get(code, "suspicious")
                         for name, code in codes.items()})
    return statuses


def parse_diffs(printed: str) -> dict[str, str]:
    """The launcher's `diffs` output, one JSON [name, diff] pair per line, as a map;
    anything else mutmut prints on the way is not a pair and is left out."""
    pairs = [json.loads(line) for line in printed.splitlines() if line.startswith('["')]
    return {name: diff for name, diff in pairs}


def _ask_diffs(repo: Path, names: list[str],
               mutmut: tuple) -> tuple[dict[str, str], subprocess.CompletedProcess]:
    done = captured([sys.executable, *mutmut, "diffs"], repo, "\n".join(names))
    return parse_diffs(done.stdout), done


def _answered(repo: Path, names: list[str], mutmut: tuple) -> tuple[dict[str, str], str]:
    """The diffs the launcher printed, and what it wrote to stderr. A launcher that
    died part way is asked once more for the names it never answered: a segfault at
    the end of a 75-minute diff run left 18 mutants with no diff. A second death
    stops the run."""
    found, done = _ask_diffs(repo, names, mutmut)
    if done.returncode == 0:
        return found, done.stderr
    _refuse_signal(repo, done.returncode)
    rest = [name for name in names if name not in found]
    print(f"mutation: the diffs launcher ended with {_ending(done.returncode)} after "
          f"{len(found)} of {len(names)} mutant(s); asking again for the other {len(rest)}")
    more, again = _ask_diffs(repo, rest, mutmut)
    _refuse_signal(repo, again.returncode)
    if again.returncode != 0:
        raise MutationError(f"the diffs launcher ended with {_ending(again.returncode)} again, "
                            f"after {len(more)} of the {len(rest)} mutant(s) it was asked for "
                            f"again: {again.stderr.strip()[-500:]}")
    return {**found, **more}, again.stderr


def _refuse_signal(repo: Path, code: int) -> None:
    """RunDied when the diffs launcher died by a signal: a process of the run that
    crashed may have crashed beside a verdict too, so the run keeps none."""
    if code < 0:
        raise RunDied(f"the diffs launcher in {repo} ended with {_ending(code)}: a process of the "
                      "run died by a signal, so the run keeps no verdict and writes no receipt")


def _diffs(repo: Path, names: list[str], mutmut: tuple) -> dict[str, str]:
    """Every named mutant's diff from one process: a `mutmut show` per mutant starts
    Python once each, about a second apiece, and a run can leave thousands alive."""
    if not names:
        return {}
    found, stderr = _answered(repo, names, mutmut)
    missing = [name for name in names if not found.get(name)]
    if missing:
        raise MutationError(f"no diff for {len(missing)} mutant(s) ({', '.join(missing[:3])}): "
                            f"{stderr.strip()[-500:]}")
    return found


def _wanted(name: str, globs: list[str] | None) -> bool:
    return globs is None or any(fnmatch.fnmatchcase(name, glob) for glob in globs)


def keyed_names(statuses: dict[str, str]) -> list[str]:
    """Survivors, unreached mutants and unfinished ones carry their key; a kill needs none."""
    return [name for name, status in statuses.items() if status in ALIVE | UNFINISHED]


def collect(repo: Path, wanted: list[str] | None = None, mutmut: tuple = LAUNCH) -> list[Result]:
    """Results from mutmut's meta files, for the mutant names `wanted` globs match."""
    statuses = {name: status for name, status in sorted(_meta_statuses(repo).items())
                if _wanted(name, wanted)}
    diffs = _diffs(repo, keyed_names(statuses), mutmut)
    return [result(name, status, diffs.get(name), repo) for name, status in statuses.items()]


def _rerun_timeouts(repo: Path, rows: list[Result], mutmut: tuple = LAUNCH,
                    env: dict | None = None) -> list[Result]:
    """One serial rerun per unfinished mutant; what still does not finish keeps its
    status. RunDied when mutmut ends the rerun as it may not end the first run."""
    names = [row.name for row in rows if row.status in UNFINISHED]
    if not names:
        return rows
    code = _run_mutmut(repo, ["run", "--max-children", "1", *names], None, mutmut, env)
    _refuse_dead(repo, code, "mutmut's serial rerun of the unfinished mutants")
    return collect(repo, [row.name for row in rows], mutmut)


def _receipt(kind: str, **fields) -> dict:
    return {"schema": 1, "kind": kind, "head": _git(REPO, "rev-parse", "HEAD").strip(),
            "created": _now().strftime(STAMP_FORMAT), **fields}


def _write_receipt(receipt: dict, name: str) -> Path:
    path = REPO / RECEIPTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    return path


def _globs_for(modules: list[str]) -> list[str]:
    return [f"{_dotted(module)}.*" for module in modules]


# --- the calc runs' scope ---------------------------------------------------------------------
# The plan's mutation section: a cli module is mutated only at the functions a
# calcs.tsv row names, and the modules the second config (`tools`) mutates
# against their own tests stay out of the weekly and nightly runs. The release
# tool is scoped like a cli module: only its accuracy gate is a calculation, and
# the rest of it publishes.
FUNCTION_SCOPED = ("src/crapkit/cli/", "tools/release/release.py")


def calc_functions(root: Path | None = None) -> dict[str, set[str]]:
    """The functions the calcs.tsv rows name, by module path: this tree's tables,
    or the ones in `root`'s packet folders."""
    from accuracy.kit import calcs
    named: dict[str, set[str]] = {}
    for row in calcs.load(root or REPO / "tests" / "accuracy"):
        for entry in row.functions:
            path, _, name = entry.partition(":")
            named.setdefault(path, set()).add(name)
    return named


def weekly_modules() -> list[str]:
    """The calc modules the weekly shards split, less the ones `tools` mutates."""
    return [module for module in calc_modules() if module not in TOOL_TARGETS]


def in_calc_scope(pairs: list[tuple[str, str]], named: dict[str, set[str]]) -> list[tuple[str, str]]:
    """The changed (module, function) pairs a calc run mutates."""
    return [(module, name) for module, name in pairs if module not in TOOL_TARGETS
            and (not module.startswith(FUNCTION_SCOPED) or name in named.get(module, ()))]


def scope_at(repo: Path, commit: str) -> tuple[set[str], dict[str, set[str]]]:
    """(calc modules, the functions named in each module) as the calcs.tsv tables
    at `commit` give them: the scope a run of this tool at `commit` mutated."""
    from accuracy.kit import calcs
    listed = _git(repo, "ls-tree", "-r", "--name-only", commit, "--", "tests/accuracy")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for path in re.findall(r"^tests/accuracy/[^/\n]+/calcs\.tsv$", listed, re.M):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            _write(root / path, _git(repo, "cat-file", "blob", f"{commit}:{path}"))
        tables = root / "tests" / "accuracy"
        return set(calcs.modules(calcs.load(tables))), calc_functions(tables)


def _defined(repo: Path, modules: list[str]) -> list[tuple[str, str]]:
    """(module, function) for every function `modules` define in this tree."""
    return [(module, name) for module in modules if (repo / module).is_file()
            for name, _, _ in _functions(ast.parse(_read(repo / module)))]


def scope_gained(repo: Path, base: str) -> list[tuple[str, str]]:
    """The functions in calc scope now that the tables at `base` left out: a run
    at `base` mutated none of them, changed or not."""
    modules, named = scope_at(repo, base)
    now = in_calc_scope(_defined(repo, calc_modules()), calc_functions())
    then = set(in_calc_scope([pair for pair in now if pair[0] in modules], named))
    return [pair for pair in now if pair not in then]


def since(base: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """What a calc run at `base` did not mutate as it stands: the calc functions a
    diff from `base` touches, and those that entered calc scope after `base`."""
    touched = in_calc_scope(changed_functions(REPO, base, calc_modules()), calc_functions())
    return touched, [pair for pair in scope_gained(REPO, base) if pair not in touched]


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
import json
import os
from pathlib import Path
import sys
import sysconfig
import tomllib


def canonical(relative: str) -> str:
    dotted = relative.removesuffix(".py").replace("/", ".")
    for prefix in ("src.", "tests."):
        dotted = dotted.removeprefix(prefix)
    return dotted


# --- mutmut ---
# The rest runs only as the stage's main script. multiprocessing's spawn method
# runs the parent's __main__ again in each child as __mp_main__, from the cwd
# mutmut gives the tests (mutants/); unguarded, a unit test's spawned worker
# started a second mutmut there.

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


def set_mutant_under_test(name):
    """mutmut's setter, except that stats mode stays in this process. mutmut mirrors
    the mode into MUTANT_UNDER_TEST, and a child a test starts (comment.py, the
    test runner, the CLI) inherits it: in stats mode a trampoline there looks for
    mutmut's settings in the child's working directory and stops the child, and
    the stats run fails. A child's hits were never recorded, so it runs the
    original code. A mutant's name still reaches children."""
    if name == "stats":
        _trampolines._mutant_under_test = name
        os.environ.pop("MUTANT_UNDER_TEST", None)
    else:
        _set_mutant(name)


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


# --- reach ---
# During the stats run an audit hook notes, for each test, each file it reads,
# each folder it lists and each program it starts, and the same for what runs
# outside any test (collection, a fixture wider than one test). The calc runs
# carry a stored verdict only while none of that changed. pytest's and mutmut's
# own reads are not the tests' and are left out; an import is left out unless it
# loads a test module, since the carry rule keys the other .py files itself.

OUTSIDE = ""
WATCHED = frozenset({"open", "os.listdir", "os.scandir", "subprocess.Popen", "os.system",
                     "os.posix_spawn", "os.exec", "os.spawn"})
LISTING = frozenset({"os.listdir", "os.scandir"})
TOOLING = ("_pytest", "pluggy", "pytest", "mutmut", "xdist", "coverage")
STDLIB = sysconfig.get_paths()["stdlib"]
LISTENING = []


def library(filename):
    """Whether a frame runs the standard library, which reads for its caller."""
    inside = filename.startswith(STDLIB) and "site-packages" not in filename
    return filename.startswith("<") or inside


def origin(frame):
    """(whether an import is reading, the module of the first caller outside the
    standard library)."""
    importing = False
    while frame is not None and library(frame.f_code.co_filename):
        importing = importing or frame.f_code.co_filename.startswith("<frozen importlib")
        frame = frame.f_back
    return importing, "" if frame is None else frame.f_globals.get("__name__", "")


def test_module(path):
    name = path.rpartition("/")[2]
    named = name.startswith("test_") or name.endswith("_test.py")
    return path.startswith("tests/") and name.endswith(".py") and named


def _text(path):
    return os.fsdecode(os.fspath(path))


def popen_program(args):
    """subprocess.Popen's (executable, args, cwd, env): its program and its folder."""
    command = args[0] or args[1]
    first = command if not isinstance(command, (str, bytes)) else command.split()
    return _text(first[0]), args[2]


PROGRAM_AT = {"os.spawn": 1}


def program(event, args):
    """(the program a start event runs, the folder it starts in or None for this one)."""
    if event == "subprocess.Popen":
        return popen_program(args)
    if event == "os.system":
        return _text(args[0]).split()[0], None
    return _text(args[PROGRAM_AT.get(event, 0)]), None


class Reach:
    """What each test of one stats run read, listed and started, by node id, under
    `roots` (mutants/ first, then the stage), as paths of the repo."""

    def __init__(self, roots):
        self.roots = [root for path in roots
                      for root in dict.fromkeys([os.path.abspath(path), os.path.realpath(path)])]
        self.where, self.seen, self.broken = OUTSIDE, {OUTSIDE: self.empty()}, False

    @staticmethod
    def empty():
        return {"reads": set(), "dirs": set(), "spawns": set()}

    def enter(self, nodeid):
        self.where = nodeid.removeprefix("mutants/")
        self.seen.setdefault(self.where, self.empty())

    def leave(self):
        self.where = OUTSIDE

    def audit(self, event, args, frame):
        """Note one event; a failure to note it marks the record broken, so the run
        stores no verdict, and never fails the test."""
        try:
            self.note_event(event, args, frame)
        except Exception:
            self.broken = True

    def note_event(self, event, args, frame):
        importing, module = origin(frame)
        if module.startswith(TOOLING):
            return
        if event == "open":
            self.read(args[0], importing)
        elif event in LISTING:
            self.listed("." if args[0] is None else args[0])
        else:
            self.started(*program(event, args))

    def read(self, path, importing):
        relative = self.relative(path)
        if relative and (test_module(relative) or not importing):
            self.note("reads", relative)

    def listed(self, path):
        relative = self.relative(path)
        if relative is not None:
            self.note("dirs", relative)

    def started(self, name, cwd):
        base = os.path.basename(name).lower().removesuffix(".exe")
        elsewhere = cwd is not None and self.relative(cwd) is None
        if base != "git" or not elsewhere:
            self.note("spawns", base or "?")

    def relative(self, path):
        """`path` as a repo path, "" for a root itself, None outside the roots."""
        if isinstance(path, int):
            return None
        full = os.path.normpath(os.path.join(os.getcwd(), _text(path)))
        return next((self.under(full, root) for root in self.roots
                     if full == root or full.startswith(root + os.sep)), None)

    @staticmethod
    def under(full, root):
        return full[len(root) + 1:].replace(os.sep, "/")

    def note(self, kind, value):
        self.seen[self.where][kind].add(value)

    def shown(self, where):
        return {kind: sorted(values) for kind, values in self.seen[where].items()}

    def merged(self, old):
        tests = {**old.get("tests", {}), **{node: self.shown(node) for node in self.seen if node}}
        before = old.get("outside", {})
        outside = {kind: sorted({*before.get(kind, ()), *values})
                   for kind, values in self.shown(OUTSIDE).items()}
        return {"outside": outside, "tests": tests}

    def save(self, path, whole):
        """Write the record: in place of the last one after a full stats run, over it
        after a run of the tests mutmut had not seen."""
        old = {} if whole or not path.is_file() else json.loads(path.read_bytes())
        broken = self.broken or old.get("broken", False)
        data = {"broken": True} if broken else self.merged(old)
        path.write_bytes(json.dumps(data, sort_keys=True).encode())


def dispatch(event, args):
    if LISTENING[0] is not None and event in WATCHED:
        LISTENING[0].audit(event, args, sys._getframe(1))


def listen(reach):
    """Send each audit event to `reach`, or to nothing for None. A hook, once added,
    stays for the life of the process, so it is added once."""
    if not LISTENING:
        sys.addaudithook(dispatch)
        LISTENING.append(None)
    LISTENING[0] = reach


def reach_plugin(reach):
    """The pytest plugin that tells `reach` which test runs: none while a fixture
    wider than one test sets up, since every test that uses it reads what it reads."""
    import pytest

    class Plugin:
        def pytest_runtest_logstart(self, nodeid, location):
            reach.enter(nodeid)

        def pytest_runtest_logfinish(self, nodeid, location):
            reach.leave()

        @pytest.hookimpl(wrapper=True)
        def pytest_fixture_setup(self, fixturedef, request):
            if fixturedef.scope == "function":
                return (yield)
            where, reach.where = reach.where, OUTSIDE
            try:
                return (yield)
            finally:
                reach.where = where

    return Plugin()


class Failures:
    """A pytest plugin that notes each test, and each file pytest could not
    collect, that fails."""

    def __init__(self):
        self.nodes = set()

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.nodes.add(report.nodeid)

    pytest_collectreport = pytest_runtest_logreport


def run_stats(self, *, tests):
    """mutmut's stats run, except that a failing test stops nothing. The run has no
    mutant active, so such a test fails whatever the mutant and tells none from the
    original; mutmut runs it with -x and then judges no mutant at all. Here it is
    left out of every mutant's tests and named in FAILURES, and the run goes on.
    What each test reads, lists and starts goes to REACH (see Reach)."""
    failures, execute, reach = Failures(), self.execute_pytest, Reach((MUTANTS, STAGE))
    plugin = reach_plugin(reach)

    def lenient(params, plugins=(), **kwargs):
        kept = [param for param in params if param != "-x"]
        return execute([*kept, "--continue-on-collection-errors"],
                       plugins=[*plugins, plugin, failures], **kwargs)

    self.execute_pytest = lenient
    listen(reach)
    try:
        code = _run_stats(self, tests=tests)
    finally:
        listen(None)
        del self.execute_pytest
    forget(failures.nodes)
    record(failures.nodes, whole=not tests)
    reach.save(REACH, whole=not tests)
    return 0 if code == 1 and failures.nodes else code


def forget(failed):
    """Leave each failed test out of the tests mutmut runs against any mutant."""
    for tests in _state().tests_by_mangled_function_name.values():
        tests.difference_update(failed)


def record(failed, whole):
    """Name the failed tests in FAILURES, one per line: in place of what it held
    after a full stats run, beside it after a run of the tests mutmut had not seen."""
    kept = set()
    if not whole and FAILURES.is_file():
        kept = set(FAILURES.read_bytes().decode().splitlines())
    FAILURES.write_bytes("".join(f"{node}\\n" for node in sorted(kept | set(failed))).encode())


def tests_for_mutant_names(mutant_names):
    """mutmut's own, except that the glob naming one function's mutants (the diff
    run's NAME__mutmut_*) finds the tests mutmut filed under NAME. mutmut matched
    that glob against the stats run's keys, which carry no __mutmut_ suffix, found
    no test, and ran its clean pass over the whole suite, stats failures included."""
    keyed = _state().tests_by_mangled_function_name
    tests = set()
    for name in mutant_names:
        function = name.removesuffix("__mutmut_*")
        tests |= set(keyed.get(function, ())) if function != name else _tests_for([name])
    return tests


if __name__ == "__main__":
    import sys
    import mutmut.mutation.trampoline as _trampolines
    import mutmut.utils.format_utils as names

    CONFIG = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    SOURCES = set(CONFIG["source_paths"])
    MUTANTS = Path("mutants").resolve()
    STAGE = Path.cwd().resolve()
    FAILURES = Path("stats-failures.txt").resolve()
    REACH = Path("stats-reach.json").resolve()
    _strip, _spec = names.strip_prefix, importlib.util.spec_from_file_location
    _set_mutant = _trampolines.set_mutant_under_test
    names.strip_prefix = strip_prefix
    importlib.util.spec_from_file_location = spec_from_file_location
    _trampolines.set_mutant_under_test = set_mutant_under_test
    if sys.argv[1:2] == ["diffs"]:
        diffs()
    else:
        from mutmut.runners.harness import PytestRunner
        from mutmut.state import state as _state
        _run_stats, PytestRunner.run_stats = PytestRunner.run_stats, run_stats
        import mutmut.__main__ as _main
        _tests_for, _main.tests_for_mutant_names = _main.tests_for_mutant_names, tests_for_mutant_names
        _main.cli()
'''


def present(targets: dict, repo: Path = REPO) -> dict:
    """The targets whose source this tree holds."""
    return {path: tests for path, tests in targets.items() if (repo / path).is_file()}


def _stage_keys(targets: dict, copies: list[str], deselect: tuple = ()) -> dict:
    """The [tool.mutmut] keys the stage sets: what mutmut mutates, runs, leaves out
    and copies. A left-out test goes to mutmut's own pytest arguments: in
    PYTEST_ADDOPTS it reached every pytest a test starts, and the kit contract's
    collect-only child then missed the tests the tables name."""
    tests = sorted({test for listed in targets.values() for test in listed})
    left_out = [word for node in deselect for word in ("--deselect", node)]
    return {"source_paths": sorted(targets), "pytest_add_cli_args_test_selection": tests,
            "pytest_add_cli_args": ["-p", "no:cacheprovider", "-m", FLOOR_SUITE, *left_out],
            "also_copy": copies}


def _stage_rows(ours: dict, kept: dict) -> dict:
    """The repo table's other keys first, as its tests read them (mutmut takes
    source_paths over paths_to_mutate), then the stage's own."""
    return {key: value for key, value in kept.items() if key not in ours} | ours


def _stage_table(targets: dict, copies: list[str], kept: dict | None = None,
                 written: str = "", deselect: tuple = ()) -> str:
    """The table: `written` (the repo table's generated blocks) as it stands, then
    one line per key."""
    rows = _stage_rows(_stage_keys(targets, copies, deselect), kept or {})
    lines = [f"{key} = {json.dumps(value)}\n" for key, value in rows.items()]
    return "".join(["[tool.mutmut]\n", written, *lines])


_GENERATED = re.compile(r"^# generated:([\w-]+)\n.*?^# /generated:\1\n", re.M | re.S)


def generated_blocks(table: str) -> str:
    """Every block tools/docs/generate.py writes in `table`, as written: its check
    (tests/unit/test_generated_guidance.py) runs in mutmut's copy too."""
    return "".join(match.group(0) for match in _GENERATED.finditer(table))


def stage_config(text: str, targets: dict, copies: list[str], deselect: tuple = ()) -> str:
    """The repo's pyproject.toml with a [tool.mutmut] table for `targets` in place of
    its own, copying `copies` beside the mutants and leaving out the `deselect`
    tests. The generated blocks of the repo's table stay as written, and the keys
    they hold are not written twice."""
    head, _, rest = text.partition("[tool.mutmut]")
    body, bracket, after = rest.partition("\n[")
    written = generated_blocks(body)
    kept = {key: value for key, value in tomllib.loads(text).get("tool", {}).get("mutmut", {}).items()
            if key not in tomllib.loads(written)}
    table = _stage_table(targets, copies, kept, written, tuple(deselect))
    return f"{head.rstrip()}\n\n{(bracket + after).strip()}\n\n{table}"


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
# mutmut's copy of src/ under the stage's mutants/ is rewritten with trampolines,
# so the checks that read crapkit's own source as data (the analysis src corpus)
# read the stage checkout's src/crapkit, the same commit as written.
SOURCE_ENV = "CRAPKIT_ACCURACY_SOURCE"


def calc_targets(modules: list[str]) -> dict:
    """The modules a calc run mutates, score.py (the canary's home) always among them."""
    return {module: CALC_TESTS for module in sorted({*modules, CANARY[0]})}


def calc_env(environ: dict) -> dict:
    """The push tier on this platform: every tier would bring in tests marked for
    another platform, which fail on this one and so judge no mutant. The src
    corpus reads the stage's own source (SOURCE_ENV). The tests the stage leaves
    out go to mutmut's own pytest arguments (stage_config), never to
    PYTEST_ADDOPTS, which every pytest a test starts would inherit."""
    env = {key: value for key, value in environ.items() if key != "CRAPKIT_ACCURACY_COLLECT_ALL"}
    return {**env, "CRAPKIT_ACCURACY_TIER": "push", "PYTHONDONTWRITEBYTECODE": "1",
            SOURCE_ENV: str(REPO / CALC_STAGE / "src" / "crapkit")}


RULING_COLUMNS = ("id", "calc", "oracle", "construct", "crapkit_value", "oracle_value", "ruling",
                  "outside_support", "docs_anchor", "test", "issue")


def _failing_tests(row: dict) -> list[str]:
    """The node ids an open defect row names as failing on a clean tree."""
    if row["ruling"] != "defect":
        return []
    return [value for value in row["crapkit_value"].split(",") if "::" in value]


def open_failures(rulings: Path | None = None) -> list[str]:
    """The tests this packet's open defect rulings (rulings.tsv beside the mutation
    tables) name, such as SS4: each fails on a clean tree in the image, so the calc
    stage deselects it until its row is fixed."""
    rows = read_table(rulings or TABLES.parent / "rulings.tsv", RULING_COLUMNS)
    return sorted({node for row in rows for node in _failing_tests(row)})


# Tests that fail or run for hours inside mutmut's copy whatever mutant is active,
# each for a reason the copy itself brings: they could judge no mutant, or would
# fail every mutant alike. The calc stage leaves them out with the open defects'
# tests; CI runs them on the tree. A test that fails in the copy for any other
# reason stops nothing: the launcher's stats run leaves it out (STATS_FAILURES).
COPY_BOUND = {
    "tests/unit/test_printed_text_is_ascii.py::"
    "test_no_literal_crapkit_prints_is_typed_with_a_non_ascii_character":
        "reads every module as text and looks up each literal's source, and in the copy a "
        "module holds every mutant's body, so the lookups ran past pytest's 10-minute dump",
    "tests/unit/test_analyze_one_pass.py::"
    "test_the_single_pass_reproduces_the_two_pass_record_for_every_committed_source":
        "analyzes every module of the copy twice, and each mutated module there holds every "
        "mutant's body: up to 6 minutes in one weekly shard's stats run, and with every weekly "
        "module mutated it ran past pytest's 10-minute dump, which crashed mutmut",
    "tests/unit/test_invariants.py::test_no_variable_or_flag_turns_the_checks_off":
        "reads invariants.py as text, and the copy's text holds mutmut's trampolines",
    "tests/unit/test_invariants.py::test_every_run_crapkit_stores_passes_the_row_check_first":
        "reads each module as text, and the copy holds every mutant's body beside the original",
    "tests/unit/test_cli_lazy_families.py::test_running_a_command_loads_its_family_and_no_other":
        "starts an interpreter on the copy, whose trampolines look for mutmut's settings in the "
        "child's working directory during the stats run and stop the child",
    "tests/accuracy/runtime_guards/test_guard_cost.py::test_the_row_check_over_a_large_repo_s_run":
        "times the row check against a ceiling, and in the copy every call it makes runs "
        "through a trampoline first",
    "tests/unit/test_config_shape.py::test_reading_the_config_module_costs_no_dataclasses_import":
        "lists the modules a child imports with crapkit.config, and on the copy every mutated "
        "module imports mutmut's trampoline, which imports dataclasses",
    "tests/unit/test_config_shape.py::"
    "test_the_probe_child_stays_untraced_under_coverages_subprocess_patch":
        "lists the modules a child imports with crapkit.config, and on the copy every mutated "
        "module imports mutmut's trampoline, which imports dataclasses",
    "tests/unit/test_version_metadata_cost.py::"
    "test_an_agreeing_distribution_answers_without_importing_metadata":
        "asks whether a child imported importlib.metadata, and on the copy every mutated module "
        "imports mutmut, which does",
    "tests/unit/test_version_metadata_cost.py::"
    "test_a_source_tree_with_nothing_installed_falls_back_to_the_package":
        "starts a child with -S on a copy of the package, and every mutated module there "
        "imports mutmut, which -S leaves off sys.path",
    "tests/unit/test_version_metadata_cost.py::"
    "test_a_dist_info_with_no_version_header_defers_to_metadata":
        "starts a child with -S on the copy, and every mutated module there imports mutmut, "
        "which -S leaves off sys.path",
}


def stage_deselected() -> list[str]:
    """What the calc stage leaves out: the tests open defect rulings name, and
    COPY_BOUND."""
    return sorted({*open_failures(), *COPY_BOUND})


def _prepare_stage(targets: dict, where: Path = TOOLS_STAGE, deselect: tuple = ()) -> Path:
    stage = _stage(REPO, REPO / where)
    pyproject = stage / "pyproject.toml"
    _write(pyproject, stage_config(_read(pyproject), targets, stage_copies(stage), deselect))
    _write(stage / LAUNCHER_FILE, LAUNCHER)
    return stage


def staged_run(where: Path, targets: dict, globs: list[str], env: dict, children: int,
               budget: float | None = None, deselect: tuple = ()) -> tuple[list[Result], bool]:
    """mutmut over `globs` in a stage whose [tool.mutmut] names `targets` and leaves
    out the `deselect` tests: the results, and whether the run finished inside
    `budget` seconds (a capped run reruns nothing). RunDied when mutmut ended
    before it judged them."""
    stage = _prepare_stage(targets, where, tuple(deselect))
    return mutmut_in(stage, globs, env, children, budget)


def mutmut_in(stage: Path, globs: list[str], env: dict, children: int,
              budget: float | None = None) -> tuple[list[Result], bool]:
    """mutmut over `globs` in a prepared `stage`, as staged_run gives it."""
    (stage / HANGS_FILE).unlink(missing_ok=True)
    code = _run_mutmut(stage, ["run", "--max-children", str(children), *globs], budget, env=env)
    _refuse_dead(stage, code)
    rows = collect(stage, globs)
    if code is None:
        return rows, False
    return _rerun_timeouts(stage, rows, env=env), True


# The launcher's stats run names here, in the stage, each test that failed with
# no mutant active; no mutant is run against it.
STATS_FAILURES = "stats-failures.txt"


def stats_failures(stage: Path) -> list[str]:
    """The tests the stage's last stats run left out, one node id per line of
    STATS_FAILURES; none before a stats run wrote it."""
    path = stage / STATS_FAILURES
    return _read(path).splitlines() if path.is_file() else []


def _left_out(where: Path) -> list[str]:
    """Print and return each test the stats run in stage `where` left out."""
    failed = stats_failures(REPO / where)
    for node in failed:
        print(f"mutation: {node} failed with no mutant active, so no mutant was run against it")
    return failed


# --- carrying a verdict to a new tree ----------------------------------------------------------
#
# A stored verdict carries to HEAD only while everything it rests on holds; see
# carry_problem for the rule, condition by condition. What the stats pass read
# comes from the launcher's REACH_FILE, and which tests reach which function
# from mutmut's own map.

REACH_FILE = "stats-reach.json"
STATS_MAP = "mutants/mutmut-stats.json"
# In a stage: the tree and environment its stats map and reach file were made at.
STATS_STAMP = "stats-stamp.json"
MAX_AGE = datetime.timedelta(days=28)
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CARRYING_KINDS = ("weekly", "diff")
# The kinds of a function's verdicts: a kill holds while what its tests read
# holds; a survivor, an unreached mutant or an unfinished one only at the tree
# it was judged at, since any new or moved test may reach it there.
WHOLE_TREE = frozenset({"alive", "unfinished"})
NO_REACH = {"reads": [], "dirs": [], "spawns": []}
NO_TESTS = {"tests": [], "reads": [], "dirs": [], "spawns": False, "reached": []}
NO_VERDICT = "no stored verdict names it"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fid(function: tuple[str, str]) -> str:
    return f"{function[0]}:{function[1]}"


def _test_name(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py")


def is_test_module(path: str) -> bool:
    """A file pytest collects tests from; conftest.py and helpers are not."""
    name = path.rpartition("/")[2]
    return path.startswith("tests/") and name.endswith(".py") and _test_name(name)


class Tree:
    """A commit's tracked files, path to git blob id, and a reader of a blob's text."""

    def __init__(self, blobs: dict[str, str], read):
        self.blobs, self._read = blobs, read
        self.ident = _sha(json.dumps(sorted(blobs.items())))

    def text(self, path: str) -> str:
        blob = self.blobs.get(path)
        return "" if blob is None else self._read(blob)


def _blob_entries(listed: str):
    for record in listed.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if fields[1:2] == ["blob"]:
            yield path, fields[2]


def head_tree(repo: Path) -> Tree:
    """HEAD's tracked files: what the stage checks out."""
    blobs = dict(_blob_entries(_git(repo, "ls-tree", "-r", "-z", "HEAD")))
    return Tree(blobs, lambda blob: _git(repo, "cat-file", "blob", blob))


def outside_code(source: str) -> str:
    """A module's lines outside every function mutmut mutates: imports, constants,
    class lines."""
    inside = {line for _, first, last in _functions(ast.parse(source))
              for line in range(first, last + 1)}
    lines = io.StringIO(source, newline="").readlines()
    return "".join(line for number, line in enumerate(lines, 1) if number not in inside)


def module_facts(source: str) -> dict:
    """sha256 of a module's code outside any function, and of each function's text."""
    texts = function_texts(source)
    return {"outside": _sha(outside_code(source)),
            "texts": {name: _sha(text) for name, text in texts.items()}}


def function_key(facts: dict, name: str) -> str:
    """A function's text with its decorators, and its module's code outside any function."""
    return _sha(f"{facts['texts'].get(name, '')}\n{facts['outside']}")


def listings(blobs: dict[str, str]) -> dict[str, frozenset]:
    """What each folder of a tree holds, by folder ("" is the root)."""
    found: dict[str, set] = {}
    for path in blobs:
        parts = path.split("/")
        for depth in range(len(parts)):
            found.setdefault("/".join(parts[:depth]), set()).add(parts[depth])
    return {folder: frozenset(names) for folder, names in found.items()}


def moved_paths(then: dict[str, str], now: dict[str, str]) -> list[str]:
    """Every path whose blob differs between two trees, added and removed ones included."""
    return sorted(path for path in then.keys() | now.keys() if then.get(path) != now.get(path))


@dataclass(frozen=True)
class Stored:
    """One function's stored verdicts: its receipt entry, the run's frame, the tree
    that run judged, and the result rows of its mutants."""
    entry: dict
    frame: dict
    blobs: dict
    rows: tuple


class Head:
    """HEAD as the carry rule reads it: its tree, its environment key and the time,
    with what it works out once per module and per stored tree."""

    def __init__(self, tree: Tree, env: str, now: datetime.datetime):
        self.tree, self.env, self.now = tree, env, now
        self.listed = listings(tree.blobs)
        self._facts: dict = {}
        self._moved: dict = {}
        self._listed: dict = {}
        self._frames: dict = {}

    def facts(self, module: str) -> dict:
        if module not in self._facts:
            self._facts[module] = module_facts(self.tree.text(module))
        return self._facts[module]

    def moved(self, stored: Stored) -> list[str]:
        ident = stored.frame["tree"]
        if ident not in self._moved:
            self._moved[ident] = moved_paths(stored.blobs, self.tree.blobs)
        return self._moved[ident]

    def listed_then(self, stored: Stored) -> dict:
        ident = stored.frame["tree"]
        if ident not in self._listed:
            self._listed[ident] = listings(stored.blobs)
        return self._listed[ident]

    def frame_problem(self, stored: Stored) -> str:
        ident = stored.entry["frame"]
        if ident not in self._frames:
            self._frames[ident] = frame_problem(stored, self)
        return self._frames[ident]


def carry_problem(stored: Stored, function: tuple[str, str], head: Head, select: bool = True) -> str:
    """"" when `function`'s stored verdicts hold at `head`, else the first reason
    they do not. They hold while the run's environment key is HEAD's, they are
    under MAX_AGE old, the function's key is unchanged, and, for a function with
    mutants: no surviving or unfinished verdict sees any change to the tree (and a
    run that selects what to judge carries no unfinished one at all); no .py file
    outside the mutated modules and the test modules changed; no mutated module
    changed outside its functions, gained or lost one, or changed a function
    mutmut keeps no map for; nothing the suite read or listed outside any test
    changed, nor anything at all when it started a program there; and no test that
    reaches the function moved, read or listed something that moved, started a
    program in a changed tree, or runs a function that changed."""
    return (_env_problem(stored, head) or _age_problem(stored, head)
            or _key_problem(stored, function, head) or _reach_problem(stored, head, select))


def _env_problem(stored: Stored, head: Head) -> str:
    if stored.frame["env"] == head.env:
        return ""
    return ("the environment changed: Python, the installed or system packages, mutmut, the "
            "launcher, or the stage's pytest and mutmut tables")


def _age_problem(stored: Stored, head: Head) -> str:
    judged = datetime.datetime.strptime(stored.entry["judged"], STAMP_FORMAT).replace(
        tzinfo=datetime.timezone.utc)
    if head.now - judged <= MAX_AGE:
        return ""
    return (f"its verdicts were judged {(head.now - judged).days} days ago, and none carries "
            f"past {MAX_AGE.days}")


def _key_problem(stored: Stored, function: tuple[str, str], head: Head) -> str:
    if function_key(head.facts(function[0]), function[1]) == stored.entry["key"]:
        return ""
    return "its text, or its module's code outside any function, changed"


def _reach_problem(stored: Stored, head: Head, select: bool) -> str:
    if not stored.entry["mutants"]:
        return ""
    return (_kind_problem(stored, head, select) or head.frame_problem(stored)
            or _test_problem(stored, head))


def _kind_problem(stored: Stored, head: Head, select: bool) -> str:
    kind = stored.entry["kind"]
    if select and kind == "unfinished":
        return "a mutant of it did not finish (a timeout or a signal), so it is judged again"
    moved = head.moved(stored)
    if kind in WHOLE_TREE and moved:
        return (f"a surviving or unfinished mutant's verdict holds only at the tree it was judged "
                f"at, and {moved[0]} changed")
    return ""


def frame_problem(stored: Stored, head: Head) -> str:
    """Why nothing the run of `stored` judged carries to `head`, or ""."""
    moved = head.moved(stored)
    return (_unmapped_problem(moved, stored.frame) or _modules_problem(stored.frame, head)
            or _outside_problem(stored, head, moved))


def _unmapped(path: str, frame: dict) -> bool:
    return path.endswith(".py") and not is_test_module(path) and path not in frame["modules"]


def _unmapped_problem(moved: list[str], frame: dict) -> str:
    for path in moved:
        if _unmapped(path, frame):
            return f"{path} changed, and no map says which tests reach its code"
    return ""


def _modules_problem(frame: dict, head: Head) -> str:
    for module, then in frame["modules"].items():
        problem = _module_problem(module, then, head.facts(module))
        if problem:
            return problem
    return ""


def _module_problem(module: str, then: dict, now: dict) -> str:
    if now["outside"] != then["outside"]:
        return f"{module} changed outside its functions"
    if set(now["texts"]) != set(then["texts"]):
        return f"{module} gained or lost a function"
    unplaced = _unplaced_moved(then, now)
    return (f"{module}:{unplaced[0]} changed, and mutmut keeps no map of the tests that reach "
            "it (it made no mutant of it)") if unplaced else ""


def _unplaced_moved(then: dict, now: dict) -> list[str]:
    unplaced = sorted(set(then["texts"]) - set(then["placed"]))
    return [name for name in unplaced if now["texts"][name] != then["texts"][name]]


def _relisted(folders: list[str], stored: Stored, head: Head) -> str:
    """The first of `folders` whose tracked entries differ between the two trees."""
    then = head.listed_then(stored)
    return next((folder for folder in folders if then.get(folder) != head.listed.get(folder)), None)


def _shown(folder: str) -> str:
    return f"{folder}/" if folder else "the root folder"


def _outside_problem(stored: Stored, head: Head, moved: list[str]) -> str:
    outside = stored.frame["outside"]
    read = sorted(set(outside["reads"]) & set(moved))
    if read:
        return f"{read[0]}, which the suite read outside any test, changed"
    folder = _relisted(outside["dirs"], stored, head)
    if folder is not None:
        return f"{_shown(folder)}, which the suite listed outside any test, changed"
    started = outside["spawns"] and moved
    return f"the suite started a program outside any test, and {moved[0]} changed" if started else ""


def _test_problem(stored: Stored, head: Head) -> str:
    entry, moved = stored.entry, head.moved(stored)
    if entry["spawns"] and moved:
        return f"a test that reaches it starts a program, and {moved[0]} changed"
    read = sorted(set(entry["reads"]) & set(moved))
    if read:
        return f"{read[0]}, which a test that reaches it reads, changed"
    folder = _relisted(entry["dirs"], stored, head)
    if folder is not None:
        return f"{_shown(folder)}, which a test that reaches it lists, changed"
    return _reached_problem(stored, head)


def _reached_problem(stored: Stored, head: Head) -> str:
    modules = stored.frame["modules"]
    for function in stored.entry["reached"]:
        module, _, name = function.partition(":")
        then = modules.get(module, {}).get("texts", {}).get(name)
        if then != head.facts(module)["texts"].get(name):
            return f"{function}, which a test that reaches it also runs, changed"
    return ""


# --- the store: receipts, read and written ------------------------------------------------------

def _created(receipt: dict) -> str:
    return str(receipt.get("created", ""))


def _voided_before(receipts: list[dict]) -> str:
    """When the newest cold run that judged a carried verdict otherwise was made."""
    return max((_created(receipt) for receipt in receipts if receipt.get("cold_mismatch")),
               default="")


def trusted(receipts: list[dict]) -> list[dict]:
    """The weekly and diff receipts a carry may read: none older than the newest
    cold run that judged a carried verdict otherwise."""
    void = _voided_before(receipts)
    return [receipt for receipt in receipts
            if receipt.get("kind") in CARRYING_KINDS and _created(receipt) >= void]


def _stored(entry: dict, carry: dict, rows: dict) -> Stored | None:
    named = [rows.get(name) for name in entry["mutants"]]
    if None in named:
        return None
    frame = carry["frames"][entry["frame"]]
    return Stored(entry, frame, carry["trees"][frame["tree"]], tuple(named))


def _stored_in(receipt: dict):
    carry = receipt.get("carry") or {}
    rows = {row["name"]: Result(**row) for row in receipt.get("results", [])}
    for function, entry in carry.get("functions", {}).items():
        stored = _stored(entry, carry, rows)
        if stored:
            yield function, stored


def stored_verdicts(receipts: list[dict]) -> dict[str, list[Stored]]:
    """Each function's stored verdicts in the trusted receipts, newest receipt first."""
    found: dict[str, list[Stored]] = {}
    for receipt in sorted(trusted(receipts), key=_created, reverse=True):
        for function, stored in _stored_in(receipt):
            found.setdefault(function, []).append(stored)
    return found


def loaded(directory: Path) -> list[dict]:
    """Every receipt saved in `directory`."""
    return [json.loads(_read(path)) for path in sorted(Path(directory).glob("*.json"))]


@dataclass
class Plan:
    """Which functions carry a stored verdict to HEAD, and why each other one is judged."""
    carried: dict = field(default_factory=dict)
    todo: list = field(default_factory=list)
    why: dict = field(default_factory=dict)


def _holding(candidates: list[Stored], function: tuple[str, str], head: Head,
             select: bool) -> tuple[Stored | None, str]:
    reasons = []
    for candidate in candidates:
        reason = carry_problem(candidate, function, head, select)
        if not reason:
            return candidate, ""
        reasons.append(reason)
    return None, reasons[0] if reasons else NO_VERDICT


def plan(functions: list, stored: dict[str, list[Stored]], head: Head, select: bool = True) -> Plan:
    """Each function carries the newest stored verdicts that hold at `head`; the
    rest are judged, each with the newest stored verdicts' reason."""
    found = Plan()
    for function in functions:
        holding, why = _holding(stored.get(_fid(function), []), function, head, select)
        if holding:
            found.carried[function] = holding
        else:
            found.todo.append(function)
            found.why[function] = why
    return found


# --- what a run's stats pass recorded -----------------------------------------------------------

def load_map(stage: Path) -> dict | None:
    """mutmut's map from each function it trampolined to the tests that reach it."""
    try:
        return json.loads(_read(stage / STATS_MAP))["tests_by_mangled_function_name"]
    except (OSError, ValueError, KeyError):
        return None


def load_reach(stage: Path) -> dict | None:
    """The launcher's record of what each test read, listed and started; None when
    there is none or a recording failed, and then no verdict of the run is stored."""
    try:
        reach = json.loads(_read(stage / REACH_FILE))
    except (OSError, ValueError):
        return None
    return None if reach.get("broken") or "tests" not in reach else reach


def placed(stage: Path, module: str) -> list[str]:
    """The functions of `module` mutmut made a mutant of, and so a trampoline: only
    their hits reach its map."""
    try:
        names = json.loads(_read(stage / "mutants" / f"{module}.meta")).get(
            "hash_by_function_name", {})
    except (OSError, ValueError):
        return []
    return sorted(_unmangled(name, name) for name in names)


@dataclass(frozen=True)
class Mapped:
    """One stats pass: the tests reaching each function, each test's reach, and the
    functions each test runs."""
    tests: dict
    reach: dict
    runs: dict


def _runs(tests: dict) -> dict:
    runs: dict = {}
    for function, nodes in tests.items():
        for node in nodes:
            runs.setdefault(node, set()).add(function)
    return runs


def mapped(stats: dict | None, reach: dict | None, names: dict) -> Mapped | None:
    """The stats pass as functions, from mutmut's names (`names`: mangled to fid)."""
    if stats is None or reach is None:
        return None
    tests = {names[key]: sorted(nodes) for key, nodes in stats.items() if key in names}
    return Mapped(tests, reach["tests"], _runs(tests))


def verdict_kind(rows: list[Result]) -> str:
    """killed, alive, unfinished, none (no mutant), or "" when a mutant was never
    judged and nothing of the function may be stored."""
    statuses = {row.status for row in rows}
    if not statuses <= JUDGED:
        return ""
    return "unfinished" if statuses & UNFINISHED else _settled_kind(statuses)


def _settled_kind(statuses: set[str]) -> str:
    return "alive" if statuses - KILLED else ("killed" if statuses else "none")


def _union(contexts: list[dict], kind: str) -> set:
    return {value for context in contexts for value in context[kind]}


def _contexts(function: tuple[str, str], found: Mapped | None) -> tuple[list, list] | None:
    """(the tests that reach the function, each one's reach); None with no map, or
    when a covering test has no reach record, since its reads are then unknown."""
    if found is None:
        return None
    tests = found.tests.get(_fid(function), [])
    contexts = [found.reach.get(node) for node in tests]
    return None if None in contexts else (tests, contexts)


def _runs_of(tests: list[str], found: Mapped) -> set[str]:
    return {function for node in tests for function in found.runs.get(node, ())}


def _reach_of(tests: list[str], contexts: list[dict], found: Mapped) -> dict:
    files = {node.split("::")[0] for node in tests}
    return {"tests": tests, "reads": sorted(_union(contexts, "reads") | files),
            "dirs": sorted(_union(contexts, "dirs")),
            "spawns": any(context["spawns"] for context in contexts),
            "reached": sorted(_runs_of(tests, found))}


def _mapped_entry(function: tuple[str, str], entry: dict, found: Mapped | None) -> dict | None:
    """The entry with what its covering tests rest on, or None (see _contexts)."""
    known = _contexts(function, found)
    return None if known is None else {**entry, **_reach_of(*known, found)}


def new_entry(function: tuple[str, str], rows: list[Result], finished: bool, found: Mapped | None,
              base: dict, head: Head) -> dict | None:
    """What to store for a function this run judged, or None: a mutant never
    judged, or no mutant in a run its cap stopped, stores nothing."""
    kind = verdict_kind(rows)
    if not kind or (kind == "none" and not finished):
        return None
    entry = {**base, "key": function_key(head.facts(function[0]), function[1]), "kind": kind,
             "mutants": _row_names(rows)}
    return {**entry, **NO_TESTS} if kind == "none" else _mapped_entry(function, entry, found)


def _row_names(rows: list[Result]) -> list[str]:
    return sorted(row.name for row in rows)


def _by_function(rows: list[Result]) -> dict:
    found: dict = {}
    for row in rows:
        found.setdefault((row.module, row.function), []).append(row)
    return found


def _names(modules: list[str], head: Head) -> dict[str, str]:
    return {mangled(module, name): f"{module}:{name}" for module in modules
            for name in head.facts(module)["texts"]}


def new_frame(stage: Path, head: Head, modules: list[str], reach: dict | None) -> dict:
    """What every verdict this run judged rests on beside its own tests."""
    return {"env": head.env, "tree": head.tree.ident,
            "modules": {module: {**head.facts(module), "placed": placed(stage, module)}
                        for module in modules},
            "outside": (reach or {}).get("outside", NO_REACH)}


def judged_section(stage: Path, head: Head, todo: list, rows: list[Result], finished: bool,
                   modules: list[str]) -> tuple[str, dict, dict]:
    """(frame id, frame, entries) for what this run judged in full."""
    reach = load_reach(stage)
    frame = new_frame(stage, head, modules, reach)
    ident = _sha(json.dumps(frame, sort_keys=True))
    found = mapped(load_map(stage), reach, _names(modules, head))
    base, by_function = {"frame": ident, "judged": head.now.strftime(STAMP_FORMAT)}, _by_function(rows)
    entries = {_fid(function): new_entry(function, by_function.get(function, []), finished, found,
                                         base, head) for function in todo}
    return ident, frame, {name: entry for name, entry in entries.items() if entry}


def carry_section(carried: dict, new: tuple | None, head: Head) -> dict:
    """The receipt's store: each function's entry, each run's frame, each tree."""
    section: dict = {"functions": {}, "frames": {}, "trees": {}}
    for function, stored in carried.items():
        _keep(section, _fid(function), stored.entry, stored.frame, stored.blobs)
    ident, frame, entries = new or ("", {}, {})
    for function, entry in entries.items():
        _keep(section, function, entry, frame, head.tree.blobs)
    return section


def _keep(section: dict, function: str, entry: dict, frame: dict, blobs: dict) -> None:
    section["functions"][function] = entry
    section["frames"][entry["frame"]] = frame
    section["trees"][frame["tree"]] = blobs


def _settled(status: str) -> bool:
    return status in KILLED | ALIVE


def _differs(before: str, after: str) -> bool:
    return _settled(before) and _settled(after) and (before in KILLED) != (after in KILLED)


def mismatches(carried: dict, rows: list[Result]) -> list[str]:
    """Each mutant a carried verdict called killed or alive that this run judged the
    other way: a cold run's check on the carry rule."""
    fresh = _statuses(rows)
    return sorted(row.name for stored in carried.values() for row in stored.rows
                  if _differs(row.status, fresh.get(row.name, "")))


def _statuses(rows: list[Result]) -> dict[str, str]:
    return {row.name: row.status for row in rows}


# --- the environment a verdict was judged in --------------------------------------------------------

def installed() -> list[str]:
    """Every installed distribution and its version but crapkit's own, whose code the
    tree holds."""
    import importlib.metadata
    found = {f"{dist.metadata['Name']}=={dist.version}"
             for dist in importlib.metadata.distributions()}
    return sorted(name for name in found if not name.lower().startswith("crapkit=="))


def system_packages() -> str:
    """dpkg's list of the image's system packages; "" where there is no dpkg."""
    dpkg = shutil.which("dpkg-query")
    if not dpkg:
        return ""
    return captured([dpkg, "-W", "-f=${Package}=${Version}\n"], Path.cwd()).stdout


def env_key(stage: Path) -> str:
    """sha256 over what judges a mutant beside the tree: Python, the installed and
    system packages (mutmut among them), the launcher, and the stage's pytest and
    mutmut tables."""
    tool = tomllib.loads(_read(stage / "pyproject.toml")).get("tool", {})
    facts = {"python": sys.version, "packages": installed(), "system": system_packages(),
             "launcher": LAUNCHER, "mutmut": tool.get("mutmut", {}), "pytest": tool.get("pytest", {})}
    return _sha(json.dumps(facts, sort_keys=True))


# --- a calc run: carry what holds, judge the rest ----------------------------------------------

@dataclass
class Outcome:
    rows: list
    complete: bool
    carry: dict
    judged: list
    mismatched: list
    env: str


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def scope_functions(modules: list[str], head: Head) -> list[tuple[str, str]]:
    """Every function of `modules` at HEAD a calc run mutates."""
    pairs = [(module, name) for module in modules for name in head.facts(module)["texts"]]
    return in_calc_scope(pairs, calc_functions())


def _todo(functions: list, found: Plan, cold: bool) -> list:
    """What to judge: what does not carry, or everything in a cold run; the canary
    too whenever anything is judged, so each judging run proves its suite kills."""
    todo = list(functions) if cold else list(found.todo)
    return todo + [CANARY] if todo and CANARY not in todo else todo


def _say_plan(found: Plan, todo: list) -> None:
    carried = [function for function in found.carried if function not in todo]
    print(f"mutation: {len(carried)} function(s) carry their stored verdicts; judging {len(todo)}")
    reasons: dict[str, int] = {}
    for function in todo:
        why = found.why.get(function, "a cold run, or the canary")
        reasons[why] = reasons.get(why, 0) + 1
    for why, count in sorted(reasons.items()):
        print(f"mutation: judging {count} function(s): {why}")


def _fresh_stats(stage: Path, head: Head) -> None:
    """Drop a stats map and reach file made at another tree or in another
    environment: mutmut maps again only the tests it has not seen, so a test that
    changed would keep its old map and its old reads."""
    stamp = json.dumps({"tree": head.tree.ident, "env": head.env})
    held = stage / STATS_STAMP
    if held.is_file() and _read(held) == stamp:
        return
    for name in (STATS_MAP, REACH_FILE, STATS_STAMP):
        (stage / name).unlink(missing_ok=True)


def _stamp_stats(stage: Path, head: Head) -> None:
    if (stage / STATS_MAP).is_file() and (stage / REACH_FILE).is_file():
        _write(stage / STATS_STAMP, json.dumps({"tree": head.tree.ident, "env": head.env}))


def _judge_todo(stage: Path, head: Head, todo: list, modules: list[str], children: int,
                budget: float | None) -> tuple[list[Result], bool, tuple | None]:
    if not todo:
        return [], True, None
    _fresh_stats(stage, head)
    rows, complete = mutmut_in(stage, [mutmut_glob(*function) for function in todo],
                               calc_env(dict(os.environ)), children, budget)
    _stamp_stats(stage, head)
    return rows, complete, judged_section(stage, head, todo, rows, complete, modules)


def calc_run(modules: list[str], children: int, budget: float | None = None, cold: bool = False,
             canary: bool = False) -> Outcome:
    """Judge every function of `modules` (and the canary, with `canary`) whose stored
    verdicts do not carry to HEAD, in the calc stage, and carry the rest."""
    targets = calc_targets(weekly_modules())
    stage = _prepare_stage(targets, CALC_STAGE, tuple(stage_deselected()))
    head = Head(head_tree(REPO), env_key(stage), _now())
    functions = _with_canary(scope_functions(modules, head), canary)
    found = plan(functions, stored_verdicts(loaded(REPO / RECEIPTS)), head)
    todo = _todo(functions, found, cold)
    _say_plan(found, todo)
    rows, complete, new = _judge_todo(stage, head, todo, sorted(targets), children, budget)
    return _run_outcome(found, todo, rows, complete, new, head)


def _with_canary(functions: list, canary: bool) -> list:
    return functions + [CANARY] if canary and CANARY not in functions else functions


def _run_outcome(found: Plan, todo: list, rows: list[Result], complete: bool, new: tuple | None,
             head: Head) -> Outcome:
    """The run's rows, carried and judged, and the store its receipt keeps."""
    carried = {function: stored for function, stored in found.carried.items() if function not in todo}
    kept = [row for stored in carried.values() for row in stored.rows]
    return Outcome(kept + rows, complete, carry_section(carried, new, head), todo,
                   mismatches(found.carried, rows), head.env)


def _say_mismatched(names: list[str]) -> int:
    for name in names:
        print(f"mutation: {name}: a fresh judgement differs from its carried verdict, so no "
              "receipt older than this one carries")
    return 1 if names else 0


def _tools(args) -> int:
    targets = present(TOOL_TARGETS)
    rows, _ = staged_run(TOOLS_STAGE, targets, _globs_for(list(targets)),
                         tools_env(dict(os.environ)), args.max_children)
    receipt = _receipt("tools", modules=sorted(targets), stats_failures=_left_out(TOOLS_STAGE),
                       results=[asdict(row) for row in rows])
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


def _held(carry: dict) -> list[list[str]]:
    """(module, function) of each function whose verdicts a receipt stores."""
    return [list(function.partition(":")[::2]) for function in sorted(carry["functions"])]


def _calc_fields(outcome: Outcome) -> dict:
    """What a weekly or diff receipt keeps of its run: the functions it judged, the
    functions whose verdicts it stores, its rows and its store."""
    return {"schema": 2, "env": outcome.env, "judged": [list(pair) for pair in outcome.judged],
            "functions": _held(outcome.carry), "deselected": stage_deselected(),
            "stats_failures": _left_out(CALC_STAGE) if outcome.judged else [],
            "cold_mismatch": outcome.mismatched, "results": [asdict(row) for row in outcome.rows],
            "carry": outcome.carry}


def _weekly(args) -> int:
    modules = shard(weekly_modules(), args.shard, args.of)
    outcome = calc_run(modules, args.max_children, None, args.cold, canary=True)
    receipt = _receipt("weekly", shard=args.shard, of=args.of, modules=modules,
                       **_calc_fields(outcome))
    _write_receipt(receipt, f"weekly-{args.shard}.json")
    rows = outcome.rows
    return _judge(rows, update=False) | _say_mismatched(outcome.mismatched)


def _diff_run(args) -> int:
    outcome = calc_run(weekly_modules(), os.cpu_count() or 2, args.cap_minutes * 60, args.cold)
    receipt = _receipt("diff", complete=outcome.complete, **_calc_fields(outcome))
    _write_receipt(receipt, f"diff-{receipt['head'][:12]}.json")
    if not outcome.complete:
        print(f"mutation: incomplete: the {args.cap_minutes:g}-minute cap stopped the run")
        return 1
    return _judge(outcome.rows, update=False, canary=False) | _say_mismatched(outcome.mismatched)


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
    touched, gained = since(head)
    missing = uncovered(touched + gained, diffs, function_text(REPO))
    entered = {f"{module}:{name}" for module, name in gained}
    for function, why in missing:
        how = "entered the calc scope after" if function in entered else "changed since"
        print(f"mutation: {function} {how} the weekly run at {head[:12]} and {why}")
    return 1 if missing else 0


def _key(args) -> int:
    print(mutant_key(sys.stdin.read()))
    return 0


def killer_env(cwd: Path, environ: dict) -> dict:
    """The environment the killer suite runs under: this tree's src/ and tests/ first,
    and the push tier whatever tier the caller runs."""
    paths = [str(cwd / "src"), str(cwd / "tests"), environ.get("PYTHONPATH")]
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
    weekly.add_argument("--cold", action="store_true", help="carry no stored verdict")
    diff = sub.add_parser("diff")
    diff.add_argument("--since-weekly", action="store_true",
                      help="no effect: every run judges what its stored verdicts do not carry")
    diff.add_argument("--cap-minutes", type=float, default=30)
    diff.add_argument("--cold", action="store_true", help="carry no stored verdict")
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
        return EXIT_BY_REFUSAL.get(type(refused), 1)


if __name__ == "__main__":
    sys.exit(main())
