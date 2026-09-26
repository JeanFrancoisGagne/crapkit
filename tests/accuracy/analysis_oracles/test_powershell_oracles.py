"""PowerShell functions against the PowerShell Parser AST and PSComplexity 0.5.1.

Two outside oracles read the files crapkit reads:

- oracles/ps_ast.ps1 parses each file with PowerShell's own parser, one process
  per host for the whole file list, and lists every function with its name,
  span, parameters and McCabe numbers (NIST SP 500-235 sec. 4.1, one count per
  decision node). The Windows push cell runs it under Windows PowerShell 5.1
  and PowerShell 7 over the probes; the Linux nightly cell runs it under the
  image's pwsh 7.6.6 over the probes and the corpus members posh-git and
  powershell-build.
- oracles/pscomplexity_adapter.py runs PSComplexity 0.5.1 once per file list
  (Linux nightly, pwsh 7): ccn and cognitive per function. Its ccn must equal
  the parser counter's plus its ForEach-Object and Where-Object points, so the
  two oracles check each other as well.

Each function the parser lists is joined to crapkit's row by path and start
line and compared on name, end line, parameter count, ccn_std and ccn_mod
(parser) or ccn_std and cognitive (PSComplexity). A crapkit row that starts
where the parser starts no function is a problem too. Files a host's parser
rejects (Windows PowerShell 5.1 has no ??, ?: or &&) are skipped and counted.

A function a known crapkit defect covers is set aside for the columns that
defect moves, under its rulings row (SHAPES, and LOSSES for the shapes that
cost a function its row). Where a tool counts a construct differently from
crapkit's documented reading, a named transform turns the tool's raw value
into crapkit's, or the tool sets the function aside. Each rule is a rulings
row with a hand case that pins the tool's raw value and crapkit's.

The measured repo names its one scope by a directory rather than ".", so a
commit older than c24e6a4, where a "." scope owned no file, still reads the
set: retro row R16 replays test_powershell_matches_parser_ast at 19db739.
The pwsh pin names the image's Linux build; a Windows host's PowerShell is the
runner's own, and the run log records its version. No crapkit import.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

import hang_guard
from accuracy.analysis_oracles import analysis_corpora, analysis_tables
from accuracy.analysis_oracles.oracles import pscomplexity_adapter as pscx
from accuracy.kit import rulings, runlog, tiers

pytestmark = pytest.mark.process
PARSER = Path(__file__).resolve().parent / "oracles" / "ps_ast.ps1"
PROBES = "ps1"
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "ps"\npaths = ["{top}"]\n'
          'languages = ["powershell"]\ncoverage_optional = true\n')
# Host flags; Windows PowerShell's default execution policy refuses a script file.
HOSTS = {"powershell": ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"),
         "pwsh": ("-NoProfile", "-NonInteractive")}
# The edition and version each host reports (about_PowerShell_Editions).
EDITIONS = {"powershell": ("Desktop", "5.1."), "pwsh": ("Core", "7.")}
# The probe files a host's parser rejects: Windows PowerShell 5.1 has no ?? and no &&.
REJECTED = {"powershell": ["ps1/ps7.ps1"], "pwsh": []}


# --- the file sets -------------------------------------------------------------------------

def written(files: dict, root: Path) -> Path:
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return root


def with_config(files: dict, top: str) -> dict:
    """The files and a crapkit.toml whose one scope claims the directory `top`."""
    return {**files, "crapkit.toml": CONFIG.format(top=top)}


def probe_files() -> dict:
    return {path: data for path, data in analysis_tables.probe_files().items()
            if path.startswith(PROBES + "/")}


def member(root: Path, name: str) -> dict:
    files = analysis_corpora.member_files(root, name, (".ps1", ".psm1"))
    return {f"{name}/{path}": data for path, data in files.items()}


# --- the parser -------------------------------------------------------------------------------

def _program(host: str) -> str:
    found = shutil.which(host)
    if found is None:
        message = f"{host} is not on PATH: this cell reads the PowerShell parser through it"
        runlog.note("infra", message=message)
        pytest.fail(message, pytrace=False)
    return found


def start_parser(host: str, root: Path, paths: list) -> tuple:
    """One host process for the whole file list; parsed() waits for it."""
    tiers.require_process(host)
    work = Path(tempfile.mkdtemp(prefix=f"ps-ast-{host}-"))
    (work / "list.txt").write_bytes(("\n".join(paths) + "\n").encode("utf-8"))
    argv = [_program(host), *HOSTS[host], "-File", str(PARSER), "-Root", str(root),
            "-List", str(work / "list.txt"), "-Out", str(work / "out.json")]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace")
    return host, process, work / "out.json"


def parsed(pending: tuple) -> dict:
    """{path: file record} from a finished parser run, its host checked and logged."""
    host, process, out = pending
    stdout, stderr = hang_guard.communicate(process)
    assert process.returncode == 0 and out.is_file(), stdout + stderr
    document = json.loads(out.read_text(encoding="utf-8"))
    edition, version = document["host"]["edition"], document["host"]["version"]
    assert (edition, version[:len(EDITIONS[host][1])]) == EDITIONS[host], document["host"]
    runlog.note("oracle", name=f"PowerShell Parser AST ({host})", version=version)
    return {record["path"]: record for record in document["files"]}


# --- what crapkit and the tools say -----------------------------------------------------------

def params(fn: dict) -> int:
    return len(fn["header_params"]) + len(fn["block_params"])


def counted(name: str):
    return lambda fn, unit=None: fn["counts"][name] > 0


AST_COLUMNS = {"name": lambda fn, unit: fn["name"], "end": lambda fn, unit: fn["end"],
               "params": lambda fn, unit: params(fn),
               "ccn_std": lambda fn, unit: fn["ccn_std"], "ccn_mod": lambda fn, unit: fn["ccn_mod"]}
PSCX_COLUMNS = {"ccn_std": lambda fn, unit: unit.ccn, "cognitive": lambda fn, unit: unit.cognitive}


def crapkit_value(row: dict, column: str):
    """A row's column; the name is the long name's text before its parameter list."""
    return row["long_name"].split(" ", 1)[0] if column == "name" else row[column]


@dataclass(frozen=True)
class Shape:
    rule: str
    columns: tuple
    holds: object  # (fn, unit) -> bool


CCN = ("ccn_std", "ccn_mod")
COG = ("cognitive",)
# Known crapkit defects: the columns each moves on a function holding its shape.
SHAPES = (
    Shape("D12", ("params",), lambda fn, unit=None: bool(fn["block_params"])),
    Shape("D3", CCN, counted("script_block_arms")),
    Shape("AO-PS-SWITCH-MOD", ("ccn_mod",), counted("switches")),
    Shape("D2e", CCN, counted("coalesces")),
    Shape("D2f", COG, counted("coalesces")),
    Shape("AO-PS-KEYWORD-CASE", CCN + COG, counted("upper_decisions")),
    Shape("AO-PS-KEYWORD-CASE", COG, counted("upper_keywords")),
    Shape("AO-PS-OPERATOR-CASE", CCN + COG, counted("upper_operators")),
    Shape("AO-PS-DEFAULT-CASE", CCN, counted("upper_default")),
    Shape("AO-PS-SWITCH-TYPE", ("ccn_mod",), counted("switch_types")),
    Shape("AO-PS-SWITCH-TYPE-COG", COG, counted("switch_types")),
    Shape("AO-PS-SWITCH-BRACKET", CCN, counted("bracket_subjects")),
    Shape("AO-PS-PIPELINE-CHAIN", CCN, counted("chains")),
    Shape("AO-PS-CLASS-IN-FUNCTION", CCN, counted("classes")),
    Shape("AO-PS-CLASS-IN-FUNCTION-COG", COG, counted("classes")),
    Shape("AO-PS-COG-TRAP", COG, counted("traps")),
    Shape("AO-PS-COG-RECURSION", COG, counted("self_calls")),
    Shape("AO-PS-COG-RUNS-NOT", COG, counted("negated_runs")),
    Shape("AO-PS-COG-RUNS-LINES", COG, counted("split_runs")),
)
# Known crapkit defects that cost a function its row; a function around it is
# set aside whole, since crapkit may charge it the lost function's decisions.
LOSSES = {
    "AO-PS-SCOPED-NAME": lambda fn: ":" in fn["name"],
    "AO-PS-DOTTED-NAME": lambda fn: "." in fn["name"],
    "AO-PS-KEYWORD-CASE-DECL": lambda fn: fn["keyword"] != fn["keyword"].lower(),
    "AO-PS-STRAY-KEYWORD": counted("stray_keywords"),
}
# A crapkit row that starts on a line holding such a word, where no function starts.
PHANTOM = "AO-PS-STRAY-PHANTOM"


@dataclass(frozen=True)
class Tool:
    columns: dict  # column -> (fn, unit) -> raw value
    transforms: dict  # rule -> (columns, (fn, unit) -> delta)
    set_aside: dict = field(default_factory=dict)  # rule -> (columns, (fn, unit) -> bool)
    units: bool = False  # whether a function needs the tool's own unit to compare


def by_count(name: str, sign: int = 1):
    return lambda fn, unit: sign * fn["counts"][name]


def construct_points(construct: str):
    """Take off the cognitive points PSComplexity gave one construct kind."""
    return lambda fn, unit: -pscx.by_construct(unit, construct)


AST = Tool(AST_COLUMNS, {
    "AO-PSAST-XOR": (CCN, by_count("xor")),
    "AO-PSAST-WHERE-ALIAS": (CCN, by_count("question_commands")),
})
PSCX = Tool(PSCX_COLUMNS, {
    "AO-PSCX-FLOW-COMMAND": (("ccn_std",), by_count("flow_commands", -1)),
    "AO-PSCX-XOR": (("ccn_std",), by_count("xor")),
    "AO-PSCX-WHERE-ALIAS": (("ccn_std",), by_count("question_commands")),
    "AO-PSCX-COG-FLOW-COMMAND": (COG, construct_points("flow-command")),
    "AO-PSCX-COALESCE-COG": (COG, construct_points("null-coalesce")),
    "AO-PSCX-SCRIPT-BLOCK": (COG, by_count("nested_depth", -1)),
    "AO-PSCX-PAREN-RUN": (COG, by_count("paren_runs", -1)),
    "AO-PSCX-WHERE-ALIAS-COG": (COG, by_count("question_cost")),
}, {"AO-PSCX-CONDITION-NESTING": (COG, counted("in_conditions"))}, units=True)


def expected(tool: Tool, column: str, fn: dict, unit) -> object:
    """The tool's raw value with every transform on this column applied."""
    deltas = [delta(fn, unit) for on, delta in tool.transforms.values() if column in on]
    raw = tool.columns[column](fn, unit)
    return raw + sum(deltas) if deltas else raw


def _shape_rules(column: str, fn: dict, unit) -> list:
    return [shape.rule for shape in SHAPES if column in shape.columns and shape.holds(fn, unit)]


def _tool_rules(tool: Tool, column: str, fn: dict, unit) -> list:
    return [rule for rule, (on, holds) in tool.set_aside.items() if column in on and holds(fn, unit)]


def held(tool: Tool, column: str, fn: dict, unit) -> list:
    """The rules that set this column aside for fn."""
    return _shape_rules(column, fn, unit) + _tool_rules(tool, column, fn, unit)


def inside(outer: dict, inner: dict) -> bool:
    return outer is not inner and outer["start"] <= inner["start"] and inner["end"] <= outer["end"]


def _within(fn: dict, functions: list) -> list:
    """fn and every function inside it."""
    return [other for other in functions if other is fn or inside(fn, other)]


def _losses(fn: dict) -> set:
    return {rule for rule, holds in LOSSES.items() if holds(fn)}


def lost(fn: dict, functions: list) -> list:
    """The row-loss rules that hold for fn or for a function inside it."""
    return sorted(set().union(*(_losses(other) for other in _within(fn, functions))))


# --- the differential -------------------------------------------------------------------------

@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    skipped: list = field(default_factory=list)
    problems: list = field(default_factory=list)


def _column(outcome: Outcome, tool: Tool, where: tuple, row: dict, column: str) -> None:
    fn, unit = where[-2:]
    rules = held(tool, column, fn, unit)
    if rules:
        outcome.set_aside["+".join(rules)] += 1
        return
    outcome.compared += 1
    want, said = expected(tool, column, fn, unit), crapkit_value(row, column)
    if said != want:
        outcome.problems.append((*where[:2], fn["name"], column, said, want))


def _unjoined(tool: Tool, row, unit) -> bool:
    """crapkit has no row for the function, or the tool no unit it needs."""
    return row is None or (unit is None and tool.units)


def _function(outcome: Outcome, tool: Tool, where: tuple, row, functions: list) -> None:
    path, fn, unit = where
    rules = lost(fn, functions)
    if rules:
        outcome.set_aside["+".join(rules)] += 1
    elif _unjoined(tool, row, unit):
        outcome.problems.append((path, fn["start"], fn["name"], "unjoined", row is None, unit))
    else:
        for column in tool.columns:
            _column(outcome, tool, (path, fn["start"], fn, unit), row, column)


def _phantoms(outcome: Outcome, record: dict, rows: list) -> None:
    starts = {fn["start"] for fn in record["functions"]}
    for row in rows:
        if row["start"] in starts:
            continue
        if row["start"] in record["stray_lines"]:
            outcome.set_aside[PHANTOM] += 1
        else:
            outcome.problems.append((record["path"], row["start"], row["long_name"], "phantom"))


def _file(outcome: Outcome, tool: Tool, record: dict, measured, units: dict) -> None:
    path = record["path"]
    rows = measured.in_file(path)
    by_start = {row["start"]: row for row in rows}
    for fn in record["functions"]:
        where = (path, fn, units.get((path, fn["start"])))
        _function(outcome, tool, where, by_start.get(fn["start"]), record["functions"])
    _phantoms(outcome, record, rows)


def differential(tool: Tool, read: dict, measured, units: dict | None = None) -> Outcome:
    """read is the parser's {path: record}; units PSComplexity's answers, for PSCX."""
    outcome = Outcome()
    for path, record in sorted(read.items()):
        if record["errors"]:
            outcome.skipped.append(path)
        else:
            _file(outcome, tool, record, measured, units or {})
    runlog.note("skipped_files", oracle="PowerShell Parser AST: files the parser rejects",
                count=len(outcome.skipped))
    return outcome


def _scored(path: str, record: dict, units: dict) -> list:
    return [(path, fn, units[(path, fn["start"])]) for fn in record["functions"]
            if (path, fn["start"]) in units]


def _joined(read: dict, units: dict):
    """(path, fn, unit) for every function of an accepted file that PSComplexity scored."""
    for path, record in sorted(read.items()):
        if not record["errors"]:
            yield from _scored(path, record, units)


def oracle_disagreements(read: dict, units: dict) -> list:
    """PSComplexity's ccn against the parser counter's plus its flow-command points."""
    return [(path, fn["start"], fn["name"], unit.ccn, fn["ccn_std"], fn["counts"]["flow_commands"])
            for path, fn, unit in _joined(read, units)
            if unit.ccn != fn["ccn_std"] + fn["counts"]["flow_commands"]]


# --- the Windows push cell: both hosts over the probes -----------------------------------------

@pytest.fixture(scope="module")
def windows_reads(measure_set, tmp_path_factory):
    """crapkit's rows and each Windows host's reading of the probes; the three
    processes run side by side."""
    files = probe_files()
    root = written(files, tmp_path_factory.mktemp("ps-probes"))
    pending = [start_parser(host, root, sorted(files)) for host in sorted(HOSTS)]
    measured = measure_set(with_config(files, PROBES))
    return measured, {entry[0]: parsed(entry) for entry in pending}


@pytest.mark.platform("win32")
@pytest.mark.parametrize("host", sorted(HOSTS))
def test_powershell_matches_parser_ast(host, windows_reads):
    measured, reads = windows_reads
    outcome = differential(AST, reads[host], measured)

    assert outcome.problems == []
    assert outcome.skipped == REJECTED[host]
    assert outcome.compared > 100


def _listed(read: dict, skip: list) -> dict:
    return {path: record["functions"] for path, record in read.items() if path not in skip}


@pytest.mark.platform("win32")
def test_windows_powershell_and_powershell_7_read_the_probes_alike(windows_reads):
    """Every file both parsers accept gives the same functions and counts."""
    _, reads = windows_reads

    assert _listed(reads["powershell"], REJECTED["powershell"]) == \
        _listed(reads["pwsh"], REJECTED["powershell"])


# --- each parser-counter rule's hand case, on the probes ---------------------------------------

# rule -> (probe file, start line, column); `rows` counts what starts on that line.
AST_HAND = {
    "AO-PSAST-XOR": ("ps1/shapes.ps1", 50, "ccn_std"),
    "AO-PSAST-WHERE-ALIAS": ("ps1/commands.ps1", 1, "ccn_std"),
    "D12": ("ps1/shapes.ps1", 17, "params"),
    "D3": ("ps1/shapes.ps1", 9, "ccn_std"),
    "AO-PS-SWITCH-MOD": ("ps1/shapes.ps1", 1, "ccn_mod"),
    "D2e": ("ps1/ps7.ps1", 1, "ccn_std"),
    "AO-PS-KEYWORD-CASE": ("ps1/shapes.ps1", 54, "ccn_std"),
    "AO-PS-OPERATOR-CASE": ("ps1/switches.ps1", 23, "ccn_std"),
    "AO-PS-DEFAULT-CASE": ("ps1/switches.ps1", 16, "ccn_std"),
    "AO-PS-SWITCH-TYPE": ("ps1/switches.ps1", 1, "ccn_mod"),
    "AO-PS-SWITCH-BRACKET": ("ps1/switches.ps1", 8, "ccn_std"),
    "AO-PS-PIPELINE-CHAIN": ("ps1/ps7.ps1", 5, "ccn_std"),
    "AO-PS-CLASS-IN-FUNCTION": ("ps1/classes.ps1", 1, "ccn_std"),
    "AO-PS-SCOPED-NAME": ("ps1/names.ps1", 1, "rows"),
    "AO-PS-DOTTED-NAME": ("ps1/names.ps1", 8, "rows"),
    "AO-PS-KEYWORD-CASE-DECL": ("ps1/names.ps1", 15, "rows"),
    "AO-PS-STRAY-KEYWORD": ("ps1/native.ps1", 1, "rows"),
    PHANTOM: ("ps1/native.ps1", 3, "rows"),
}


def starting(items: list, start: int) -> list:
    return [item for item in items if item["start"] == start]


def hand_rows(read: dict, measured, path: str, start: int) -> tuple:
    """(crapkit's rows, the parser's functions) that start on the line."""
    return (len(starting(measured.in_file(path), start)),
            len(starting(read[path]["functions"], start)))


def hand_value(tool: Tool, sample: tuple, case: tuple) -> tuple:
    """(crapkit's value, the tool's raw value, the transformed value) at a hand case."""
    measured, read, units = sample
    path, start, column = case
    if column == "rows":
        crapkit, raw = hand_rows(read, measured, path, start)
        return crapkit, raw, raw
    fn = starting(read[path]["functions"], start)[0]
    unit = units.get((path, start))
    row = starting(measured.in_file(path), start)[0]
    return crapkit_value(row, column), tool.columns[column](fn, unit), expected(tool, column, fn, unit)


def pin_case(tool: Tool, rule: str, values: tuple) -> None:
    """A tool rule pins its raw value and must reach crapkit's; a defect shape
    pins the tool's transformed value, a strict xfail while the defect stands."""
    crapkit, raw, want = values
    if rule in tool.transforms or rule in tool.set_aside:
        rulings.pin_ruling(rule, crapkit=crapkit, oracle=raw)
    else:
        rulings.pin_ruling(rule, crapkit=crapkit, oracle=want)


@pytest.mark.platform("win32")
@pytest.mark.parametrize("rule", [pytest.param(rule, marks=analysis_tables.marks(rule))
                                  for rule in sorted(AST_HAND)])
def test_each_parser_ast_rule_is_pinned_by_a_hand_case(rule, windows_reads):
    measured, reads = windows_reads
    values = hand_value(AST, (measured, reads["pwsh"], {}), AST_HAND[rule])

    pin_case(AST, rule, values)
    assert values[2] == values[0]


# --- the Linux nightly cell: pwsh 7.6.6 and PSComplexity over probes and corpus -----------------

def linux_sample(files: dict, top: str, measure_set, root: Path) -> tuple:
    """crapkit's rows, the parser's reading and PSComplexity's units for one set."""
    written(files, root)
    pending = start_parser("pwsh", root, sorted(files))
    units = pscx.measure(root, sorted(files))
    return measure_set(with_config(files, top)), parsed(pending), units


@pytest.fixture(scope="module")
def probes_linux(oracle, measure_set, tmp_path_factory):
    oracle("pwsh")
    oracle("PSComplexity")
    return linux_sample(probe_files(), PROBES, measure_set, tmp_path_factory.mktemp("ps-linux"))


@pytest.fixture(scope="module", params=["posh-git", "powershell-build"])
def corpus_linux(request, full_corpus, oracle, measure_set, tmp_path_factory):
    oracle("pwsh")
    oracle("PSComplexity")
    files = member(full_corpus, request.param)
    return linux_sample(files, request.param, measure_set, tmp_path_factory.mktemp("ps-corpus"))


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_pwsh_on_linux_matches_parser_ast_on_the_probes(probes_linux):
    measured, read, _ = probes_linux
    outcome = differential(AST, read, measured)

    assert (outcome.problems, outcome.skipped) == ([], [])
    assert outcome.compared > 100


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_corpus_matches_parser_ast(corpus_linux):
    measured, read, _ = corpus_linux
    outcome = differential(AST, read, measured)

    assert (outcome.problems, outcome.skipped) == ([], [])
    assert outcome.compared > 200


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_probes_match_pscomplexity(probes_linux):
    measured, read, units = probes_linux
    outcome = differential(PSCX, read, measured, units)

    assert oracle_disagreements(read, units) == []
    assert (outcome.problems, outcome.skipped) == ([], [])
    assert outcome.compared > 30


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_corpus_matches_pscomplexity(corpus_linux):
    measured, read, units = corpus_linux
    outcome = differential(PSCX, read, measured, units)

    assert oracle_disagreements(read, units) == []
    assert (outcome.problems, outcome.skipped) == ([], [])
    assert outcome.compared > 80


# --- each PSComplexity rule's hand case, on the probes -----------------------------------------

PSCX_HAND = {
    "AO-PSCX-FLOW-COMMAND": ("ps1/commands.ps1", 5, "ccn_std"),
    "AO-PSCX-XOR": ("ps1/shapes.ps1", 50, "ccn_std"),
    "AO-PSCX-WHERE-ALIAS": ("ps1/commands.ps1", 1, "ccn_std"),
    "AO-PSCX-COG-FLOW-COMMAND": ("ps1/commands.ps1", 5, "cognitive"),
    "AO-PSCX-COALESCE-COG": ("ps1/ps7.ps1", 1, "cognitive"),
    "AO-PSCX-SCRIPT-BLOCK": ("ps1/commands.ps1", 9, "cognitive"),
    "AO-PSCX-PAREN-RUN": ("ps1/runs.ps1", 16, "cognitive"),
    "AO-PSCX-WHERE-ALIAS-COG": ("ps1/commands.ps1", 13, "cognitive"),
    "AO-PSCX-CONDITION-NESTING": ("ps1/ps7.ps1", 14, "cognitive"),
    "D2f": ("ps1/ps7.ps1", 1, "cognitive"),
    "AO-PS-SWITCH-TYPE-COG": ("ps1/switches.ps1", 1, "cognitive"),
    "AO-PS-CLASS-IN-FUNCTION-COG": ("ps1/classes.ps1", 1, "cognitive"),
    "AO-PS-COG-TRAP": ("ps1/commands.ps1", 20, "cognitive"),
    "AO-PS-COG-RECURSION": ("ps1/commands.ps1", 25, "cognitive"),
    "AO-PS-COG-RUNS-NOT": ("ps1/runs.ps1", 1, "cognitive"),
    "AO-PS-COG-RUNS-LINES": ("ps1/runs.ps1", 8, "cognitive"),
}
# crapkit reads the ?? hand case 2 until calc-bug analysis-oracles-D2 is fixed.
HELD = {"AO-PSCX-COALESCE-COG": "D2f"}


@pytest.mark.nightly
@pytest.mark.platform("linux")
@pytest.mark.parametrize("rule", [pytest.param(rule, marks=analysis_tables.marks(HELD.get(rule, rule)))
                                  for rule in sorted(PSCX_HAND)])
def test_each_pscomplexity_rule_is_pinned_by_a_hand_case(rule, probes_linux):
    measured, read, units = probes_linux
    path, start, column = PSCX_HAND[rule]
    values = hand_value(PSCX, probes_linux, PSCX_HAND[rule])
    if rule in HELD:
        rulings.pin_ruling(HELD[rule], crapkit=values[0], oracle=values[2])
    fn = starting(read[path]["functions"], start)[0]

    pin_case(PSCX, rule, values)
    assert values[2] == values[0] or rule in held(PSCX, column, fn, units.get((path, start)))


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_pscomplexity_coalescing_moves_to_the_sonar_count(probes_linux):
    """Sonar Cognitive Complexity v1.7 worked by hand: `return $a ?? 0` holds no
    increment, the paper ignoring null-coalescing operators. PSComplexity reads
    1; the transforms reach 0 whatever crapkit says."""
    _, raw, want = hand_value(PSCX, probes_linux, PSCX_HAND["AO-PSCX-COALESCE-COG"])

    assert (raw, want) == (1, 0)


def _rules() -> list:
    named = {shape.rule for shape in SHAPES} | set(LOSSES) | {PHANTOM}
    return sorted(named | set(AST.transforms) | set(PSCX.transforms) | set(PSCX.set_aside))


def test_every_powershell_rule_has_a_hand_case():
    assert sorted({*AST_HAND, *PSCX_HAND}) == _rules()
