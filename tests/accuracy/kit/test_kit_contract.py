"""The rules every accuracy packet follows, checked over the whole tree.

A rule over files that exist applies from the kit's first commit: a packet
that adds an oracle importing crapkit, a hand table without a source, or a
test that skips fails here on its first push. A rule that needs a packet's
output to exist (calcs.tsv rows, the small corpus, the generated mutmut list,
bugs.tsv) switches on when kit-close sets KIT_CLOSED; until then it checks
whatever rows exist.
"""
from collections import Counter
import ast
import importlib.util
import os
from pathlib import Path
import re
import sys
import tomllib

import pytest

import hang_guard
from accuracy.kit import calcs, closure, docrange, rulings, strategies, tiers

KIT_CLOSED = False
KIT = Path(__file__).resolve().parent
ACCURACY = KIT.parent
TESTS = ACCURACY.parent
REPO = TESTS.parent
TOOLS = REPO / "tools" / "accuracy"
DATA_DIRS = frozenset({"fixtures", "small", "recorded", "probes", "goldens", "__pycache__",
                       "node_modules"})
PUSH_BUDGET_SECONDS = 480


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix() if REPO in path.parents else path.as_posix()


def _is_code(path: Path, root: Path) -> bool:
    return not DATA_DIRS.intersection(path.relative_to(root).parts[:-1])


def _python_files(root: Path) -> list[Path]:
    """Every .py file under root outside the data directories."""
    return sorted(path for path in root.rglob("*.py") if _is_code(path, root))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes(), filename=str(path))


def _lines(path: Path) -> list[str]:
    text = path.read_bytes().decode("utf-8")
    return [line.removesuffix("\r") for line in text.split("\n") if line.strip()]


def _tsv(path: Path) -> list[dict]:
    lines = _lines(path) or [""]
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


# --- expected values reach no crapkit ---------------------------------------------------

def _expected_value_files() -> list[Path]:
    oracles = [path for path in ACCURACY.glob("*/oracles/**/*") if path.is_file()]
    models = list(ACCURACY.glob("*/model_*.py"))
    return sorted([*oracles, *models, KIT / "exact.py", KIT / "strategies.py"])


def test_expected_values_reach_no_crapkit_module():
    python = [path for path in _expected_value_files() if path.suffix == ".py"]

    assert [line for path in python for line in closure.crapkit_reach(path)] == []


def _literals(tree: ast.AST) -> list[str]:
    constants = (node for node in ast.walk(tree) if isinstance(node, ast.Constant))
    return [node.value for node in constants if isinstance(node.value, str)]


def _strings(path: Path) -> list[str]:
    """The string literals of a Python file; the whole text of any other file."""
    if path.suffix != ".py":
        return [path.read_bytes().decode("utf-8", "replace")]
    return _literals(_tree(path))


def _names_crapkit_source(path: Path) -> bool:
    return any("src/crapkit" in text for text in _strings(path))


def test_expected_value_files_name_no_crapkit_source_path():
    assert [_rel(path) for path in _expected_value_files() if _names_crapkit_source(path)] == []


# --- module names ---------------------------------------------------------------------------

def test_every_test_module_basename_is_unique():
    names = Counter(path.name for path in _python_files(TESTS)
                    if path.name not in ("conftest.py", "__init__.py"))

    assert sorted(name for name, count in names.items() if count > 1) == []


def _absolute_from(node) -> bool:
    return isinstance(node, ast.ImportFrom) and node.level == 0


def _heads(node) -> list[str]:
    """The absolute module names an import statement names."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module or ""] if _absolute_from(node) else []


def _bare_model_imports(path: Path) -> list[str]:
    heads = (head for node in ast.walk(_tree(path)) for head in _heads(node))
    return [f"{_rel(path)} imports {head}" for head in heads if head.startswith("model")]


def test_models_import_by_package_never_as_bare_model():
    assert [line for path in _python_files(ACCURACY) for line in _bare_model_imports(path)] == []


# --- no skip, no stray xfail, one home for Hypothesis settings ------------------------------

SKIPS = frozenset({"skip", "skipif", "xfail", "importorskip"})
# rulings.py makes the one allowed xfail; the guard probe and its test make the
# forbidden ones on purpose, in a session of their own.
MAY_SKIP = frozenset({"kit/rulings.py", "kit/test_kit_guard_probes.py", "kit/test_kit_guards.py"})


def _attributes(tree: ast.AST) -> set[str]:
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def _from_imported(tree: ast.AST) -> set[str]:
    imported = (node.names for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    return {alias.name for names in imported for alias in names}


def _used_names(tree: ast.AST) -> set[str]:
    """Every attribute name and every name imported with `from x import`."""
    return _attributes(tree) | _from_imported(tree)


def _skip_uses(path: Path) -> list[str]:
    return sorted(SKIPS.intersection(_used_names(_tree(path))))


def test_the_only_skip_or_xfail_is_a_rulings_row():
    offenders = {path.relative_to(ACCURACY).as_posix(): _skip_uses(path)
                 for path in _python_files(ACCURACY)}

    assert {name: uses for name, uses in offenders.items() if uses and name not in MAY_SKIP} == {}


def _settings_calls(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(_tree(path)):
        func = getattr(node, "func", None) if isinstance(node, ast.Call) else None
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        lines += [node.lineno] if name in ("settings", "register_profile", "load_profile") else []
    return lines


def test_hypothesis_settings_live_only_in_kit_settings():
    found = {_rel(path): _settings_calls(path) for path in _python_files(ACCURACY)
             if path != KIT / "settings.py"}

    assert {name: lines for name, lines in found.items() if lines} == {}


def _called(function: ast.AST) -> set[str]:
    calls = (node.func for node in ast.walk(function) if isinstance(node, ast.Call))
    return {getattr(func, "id", None) or getattr(func, "attr", None) for func in calls}


def _unpaired(function: ast.AST) -> bool:
    called = _called(function)
    return "assume" in called and "event" not in called


def _unpaired_assumes(path: Path) -> list[str]:
    functions = (node for node in ast.walk(_tree(path))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return [f"{_rel(path)}::{node.name}" for node in functions if _unpaired(node)]


def test_every_assume_is_paired_with_an_event():
    assert [line for path in _python_files(ACCURACY) for line in _unpaired_assumes(path)] == []


# --- tables name real tests and outside sources ---------------------------------------------

HAND_TABLES = ("*/hand_*.tsv", "*/probes/**/probes.tsv", "*/probes/**/ground_truth.tsv",
               "*/equivalence.tsv", "*/definitions.tsv")
NO_SOURCE = re.compile(r"^(|crapkit|observed|v?\d+(\.\d+)+)$", re.IGNORECASE)


def _hand_tables() -> list[Path]:
    return sorted({path for pattern in HAND_TABLES for path in ACCURACY.glob(pattern)})


def _source_problems(path: Path) -> list[str]:
    rows = _tsv(path)
    if rows and "source" not in rows[0]:
        return [f"{_rel(path)} has no source column"]
    return [f"{_rel(path)} row {number}: source {row['source']!r} is not an outside source"
            for number, row in enumerate(rows, start=2) if NO_SOURCE.match(row["source"].strip())]


def test_every_hand_table_cites_an_outside_source():
    assert [line for path in _hand_tables() for line in _source_problems(path)] == []


def _retro_ids() -> dict[str, str]:
    rows = ((path, row) for path in sorted(ACCURACY.glob("*/retro.tsv")) for row in _tsv(path))
    return {row["test"]: _rel(path) for path, row in rows if row.get("test")}


def _named_node_ids() -> dict[str, str]:
    """{node id: the table that names it} over rulings, retro and calcs rows."""
    named = {row.test: f"rulings {row.id}" for row in rulings.load().values() if row.test}
    named.update(_retro_ids())
    named.update({row.independent_test: f"calcs {row.calc}" for row in calcs.load()})
    return named


def _collected() -> set[str]:
    env = {**os.environ, tiers.COLLECT_ALL_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:randomly",
            "-p", "no:cacheprovider", "tests/accuracy"]
    done = hang_guard.run(argv, cwd=REPO, env=env, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    return {line.strip() for line in done.stdout.splitlines() if "::" in line}


def _is_collected(node: str, collected: set[str]) -> bool:
    return node in collected or any(item.startswith(node + "[") for item in collected)


@pytest.mark.process
def test_every_node_id_a_table_names_is_a_collected_test():
    named, collected = _named_node_ids(), _collected()

    assert "tests/accuracy/kit/test_kit_contract.py::test_the_contract_is_collected" in collected
    assert {node: table for node, table in named.items()
            if not _is_collected(node, collected)} == {}


def test_the_contract_is_collected():
    """The collect-only run above must see this node id."""


# --- calcs ----------------------------------------------------------------------------------

def test_every_calc_s_independent_test_reaches_no_crapkit():
    problems = []
    for row in calcs.load():
        test_file = REPO / row.independent_test.split("::")[0]
        problems += ([f"{row.calc}: no file {test_file}"] if not test_file.is_file()
                     else [f"{row.calc}: {line}" for line in closure.crapkit_reach(test_file)])
    assert problems == []


def _function_problem(row: calcs.Calc, function: str) -> str | None:
    path, _, qualname = function.partition(":")
    if path not in row.modules:
        return f"{row.calc}: {function} lies outside its modules {list(row.modules)}"
    if not (REPO / path).is_file():
        return f"{row.calc}: no module {path}"
    from accuracy.kit import reach
    return None if reach.body_lines(REPO / path, qualname) else f"{row.calc}: no function {function}"


def _function_problems(rows: list) -> list[str]:
    found = (_function_problem(row, function) for row in rows for function in row.functions)
    return [problem for problem in found if problem]


def test_every_calc_names_production_functions_that_exist():
    rows = calcs.load()

    assert _function_problems(rows) == []
    assert len(rows) >= 92 or not KIT_CLOSED, "kit-close needs every calc of the plan in calcs.tsv"


@pytest.mark.nightly
@pytest.mark.process
def test_the_golden_run_executes_every_calc_function(tmp_path):
    from accuracy.kit import corpus_run, reach
    functions = [function for row in calcs.load() for function in row.functions]
    corpus = corpus_run.SMALL if corpus_run.SMALL.is_dir() else corpus_run.SEED
    measured = reach.measured_lines(corpus, tmp_path) if functions else {}

    assert reach.unreached(functions, measured) == []


def _mutmut_paths() -> list[str]:
    with (REPO / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["tool"]["mutmut"]["paths_to_mutate"]


def test_the_mutmut_list_is_the_union_of_calc_modules():
    paths = _mutmut_paths()

    assert [path for path in paths if not (REPO / path).exists()] == []
    if KIT_CLOSED:
        assert sorted(paths) == calcs.modules(calcs.load())


# --- the small corpus, bugs and models -------------------------------------------------------

def _suffixes() -> list[str]:
    from crapkit.universe import LANGUAGE_EXTENSIONS
    return sorted({suffix for suffixes in LANGUAGE_EXTENSIONS.values() for suffix in suffixes})


def _missing_suffixes(names: list[str], wanted: list[str]) -> list[str]:
    return [suffix for suffix in wanted if not any(name.endswith(suffix) for name in names)]


def test_every_language_suffix_has_a_small_corpus_file():
    small = ACCURACY / "corpus_goldens" / "small"
    names = [path.name.lower() for path in small.rglob("*") if path.is_file()]
    wanted = _suffixes() if small.is_dir() or KIT_CLOSED else []

    assert _missing_suffixes(names, wanted) == []


def _events() -> set[str]:
    return {name for table in strategies.REQUIRED.values() for name in table}


def _property_bugs_without_events(rows: list[dict]) -> list[str]:
    properties = (row["id"] for row in rows if row.get("method") == "property")
    return [bug for bug in properties if bug not in _events()]


def test_every_property_bug_has_its_strategy_event():
    bugs = ACCURACY / "suite_strength" / "retro" / "bugs.tsv"

    assert bugs.is_file() or not KIT_CLOSED
    assert _property_bugs_without_events(_tsv(bugs) if bugs.is_file() else []) == []


def test_every_model_cites_doc_ranges_that_still_hash_to_their_pins():
    models = sorted(ACCURACY.glob("*/model_*.py"))

    assert [model.name for model in models if not docrange.cited(model)] == []
    assert [line for model in models for line in docrange.stale(model)] == []


# --- locks and the time budget ------------------------------------------------------------------

LOCKS = (TOOLS / "requirements-push.txt", TOOLS / "requirements-nightly.txt",
         TOOLS / "image" / "requirements-build.txt")


def _starts_requirement(line: str) -> bool:
    return bool(line) and not line[0].isspace() and not line.startswith("#")


def _requirement_blocks(path: Path) -> list[list[str]]:
    """Each requirement line with the --hash lines that follow it."""
    blocks: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _starts_requirement(line):
            blocks.append([line])
        elif blocks and line.strip().startswith("--hash="):
            blocks[-1].append(line)
    return blocks


def test_every_locked_requirement_carries_a_hash():
    unhashed = [f"{path.name}: {block[0]}" for path in LOCKS
                for block in _requirement_blocks(path) if len(block) < 2]

    assert unhashed == []


def test_colorama_is_locked_for_windows_in_both_tiers():
    """pytest needs colorama on Windows; a lock compiled on Linux without
    --universal drops it. It stays unmarked (radon needs it everywhere) or
    marked for win32, never marked away from Windows."""
    pattern = re.compile(r"^colorama==\S+( ; .*win32.*)? \\$", re.M)

    assert [path.name for path in LOCKS[:2]
            if not pattern.search(path.read_text(encoding="utf-8"))] == []


def _run_tool():
    spec = importlib.util.spec_from_file_location("accuracy_run_contract", TOOLS / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_declared_push_seconds_fit_the_budget():
    run = _run_tool()
    push = run.selected(run.load_checks(), "push", None, "linux")
    total = sum(check.seconds for check in push)

    assert total <= PUSH_BUDGET_SECONDS, (
        f"push checks declare {total} serial seconds on ubuntu; the budget is "
        f"{PUSH_BUDGET_SECONDS}: " + ", ".join(f"{c.key}: {c.name} {c.seconds}" for c in push))


def _check_targets() -> list[Path]:
    checks = _run_tool().load_checks()
    return [(REPO / target).resolve() for check in checks for target in check.pytest]


def _test_modules() -> list[Path]:
    return [path.resolve() for path in _python_files(ACCURACY) if path.name.startswith("test_")]


def _named_by(path: Path, targets: list[Path]) -> bool:
    return any(path == target or target in path.parents for target in targets)


def test_every_accuracy_test_module_is_named_by_a_check():
    """run.py runs only what a check names: a test module no check names never
    runs in CI, and its failures go unseen."""
    targets = _check_targets()

    unnamed = [path for path in _test_modules() if not _named_by(path, targets)]

    assert list(map(_rel, unnamed)) == []
