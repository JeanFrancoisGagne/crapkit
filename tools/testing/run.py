"""Run isolated unit and CLI workers with one evidence owner."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


SUITES = ("unit", "e2e")
UNIT_WORKERS = 4
E2E_WORKERS = 8
RETENTION_DAYS = 7
RETENTION_COUNT = 10
_RECEIPT = ".crapkit-test-run.json"
_STATUSES = ("removed", "planned", "active", "unproven", "changed", "failed")


@lru_cache(maxsize=1)
def _runtime():
    """The selected installation's locks and procs modules. Prefer the selected
    installation, including installed-wheel CI runs."""
    try:
        return importlib.import_module("crapkit.locks"), importlib.import_module("crapkit.procs")
    except ModuleNotFoundError as error:
        if error.name not in {"crapkit", "crapkit.locks", "crapkit.procs"}:
            raise
        if hasattr(sys.modules.get("crapkit"), "__version__"):
            raise ValueError("selected Crapkit installation lacks this test runner's lifecycle helpers; "
                             "install this checkout's dev extra") from error
    # Miniature runner fixtures deliberately put a different crapkit package on
    # PYTHONPATH. Keep that package selected for their tests, loading only the
    # runner's lifecycle helpers under a private name in this process.
    package = Path(__file__).resolve().parents[2] / "src/crapkit"
    spec = importlib.util.spec_from_file_location(
        "_crapkit_test_runner", package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return (importlib.import_module(spec.name + ".locks"),
            importlib.import_module(spec.name + ".procs"))


def _runtime_module(name: str):
    """A module from the installation that supplied the lifecycle helpers."""
    return importlib.import_module(_runtime()[0].__package__ + "." + name)


# Test evidence retention. This runner is the only writer of default
# .crapkit/test-runs/run-* directories, so it owns their cleanup too: every
# default run prunes the finished runs past --retention-days or
# --retention-count before it starts. Explicit --output is caller-managed.

def _same_place(path: Path, resolved: Path) -> bool:
    r"""Whether `resolve()` named the directory `path` already names.

    On Windows it names the same directory two other ways while a sibling
    process is creating or deleting it: the extended-length form with the
    `\\?\` prefix, and the NTFS tombstone under `$Extend\$Deleted` for a
    directory whose last handle is still open. Two direct runners sharing a
    repository hit both, and the guard read each as a redirect and refused.
    A symlink or junction elsewhere still resolves elsewhere, and is still
    refused.
    """
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(text) == os.path.normcase(str(path)) or "$Extend" in text


def _parent(root: Path) -> Path:
    path = root / ".crapkit" / "test-runs"
    if not _same_place(path, path.resolve()):
        raise _runtime_module("errors").ToolError(
            "test evidence retention refuses a redirected .crapkit/test-runs path")
    return path


def _lease(parent: Path, name: str) -> Path:
    return parent / ".leases" / (name + ".lock")


def _safe_run(path: Path, parent: Path) -> bool:
    return path.parent == parent and path.name.startswith("run-") and _same_place(path, path.resolve())


def _valid_receipt(value: dict, root: Path, path: Path) -> bool:
    expected = ("crapkit-test-run", 1, str(root), path.name)
    actual = tuple(value.get(key) for key in ("kind", "schema", "root", "name"))
    return type(value.get("schema")) is int and actual == expected


def _read_receipt(path: Path, root: Path) -> tuple[float, dict] | None:
    if not _safe_run(path, _parent(root)) or not path.is_dir():
        return None
    try:
        return _parse_receipt(path, root)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _parse_receipt(path: Path, root: Path) -> tuple[float, dict] | None:
    value = json.loads((path / _RECEIPT).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not _valid_receipt(value, root, path):
        return None
    return _receipt_time(value), value


def _receipt_time(value: dict) -> float:
    stamp = value.get("finished_at", value["created_at"])
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0:
        raise ValueError("invalid evidence timestamp")
    return float(stamp)


def _candidates(root: Path) -> list[tuple[float, Path]]:
    # Only a repository a runner used has test-runs. Without one, a linked
    # .crapkit is not this runner's to refuse.
    if not (root / ".crapkit" / "test-runs").is_dir():
        return []
    records = [(record[0], path) for path in _parent(root).iterdir()
               if (record := _read_receipt(path, root)) is not None]
    return sorted(records, reverse=True)


def _expired(index: int, stamp: float, keep: int, cutoff: float | None) -> bool:
    return bool(keep and index >= keep) or (cutoff is not None and stamp < cutoff)


def _prune_one(path: Path, root: Path, dry_run: bool, stamp: float) -> str:
    locks, errors = _runtime()[0], _runtime_module("errors")
    lease = _lease(_parent(root), path.name)
    if not lease.is_file() or lease.resolve() != lease:
        return "unproven"
    try:
        with locks.exclusive_lock(lease, label="test evidence"):
            return _remove_recognized(path, root, dry_run, stamp)
    except errors.ToolError:
        return "active"
    except OSError:
        # A file Windows will not delete (read-only, or held open) fails this
        # run alone; the other runs and the suites still go ahead.
        return "failed"


def _remove_recognized(path: Path, root: Path, dry_run: bool, stamp: float) -> str:
    record = _read_receipt(path, root)
    if record is None:
        return "unproven"
    if record[0] != stamp:
        return "changed"
    if dry_run:
        return "planned"
    _delete_run(path, record[1])
    return "removed"


def _delete_run(path: Path, receipt: dict) -> None:
    # rmtree can delete the receipt before it fails on a read-only or open
    # file; NTFS lists ".crapkit-test-run.json" first. Without the receipt the
    # run is no longer recognized, so no later prune would list or retry it.
    try:
        shutil.rmtree(path)
    except OSError:
        _write_receipt(path, receipt)
        raise


def _cutoff(keep: int, days: int) -> float | None:
    if keep < 0 or days < 0:
        raise ValueError("test evidence retention limits must be nonnegative")
    return time.time() - days * 86400 if days else None


def prune_test_runs(root: Path, *, keep: int = RETENTION_COUNT, days: int = RETENTION_DAYS,
                    dry_run: bool = False) -> dict:
    """Remove marked idle runs beyond either enabled retention limit.

    Active leases, redirected paths and unrecognized evidence are preserved.
    A run the filesystem refuses to delete is reported `failed`. Zero disables
    the corresponding limit. Explicit output has no marker and never
    participates. The stable lease remains after removal for safe reuse.
    """
    root = root.resolve()
    cutoff = _cutoff(keep, days)
    result = {key: [] for key in _STATUSES}
    for index, (stamp, path) in enumerate(_candidates(root)):
        if _expired(index, stamp, keep, cutoff):
            result[_prune_one(path, root, dry_run, stamp)].append(str(path))
    return result


def _write_receipt(path: Path, value: dict) -> None:
    temporary = path / (_RECEIPT + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path / _RECEIPT)


def _new_run(root: Path, parent: Path) -> tuple[Path, dict]:
    parent.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    receipt = {"kind": "crapkit-test-run", "schema": 1, "root": str(root),
               "name": path.name, "created_at": time.time()}
    return path, receipt


def _explicit_directory(root: Path, selected: Path) -> Path:
    output = (root / selected).resolve()
    if not output.is_relative_to(root):
        raise ValueError(f"test output must be inside {root}: {output}")
    return output


def _explicit_ownership(parent: Path, output: Path) -> tuple[Path, Path | None]:
    if output.is_relative_to(parent):
        parts = output.relative_to(parent).parts
        if parts and parts[0].startswith("run-"):
            return _lease(parent, parts[0]), parent / parts[0]
    name = "output-" + hashlib.sha256(str(output).encode("utf-8")).hexdigest()
    return _lease(parent, name), None


@contextmanager
def _explicit_run(root: Path, parent: Path, selected: Path):
    output = _explicit_directory(root, selected)
    lease, retained = _explicit_ownership(parent, output)
    with _runtime()[1].own_processes((lease,), label="test output") as owner:
        output.mkdir(parents=True, exist_ok=True)
        if retained is not None and _read_receipt(retained, root) is not None:
            (retained / _RECEIPT).unlink()
        yield output, owner


@contextmanager
def evidence_directory(root: Path, *, selected: Path | None = None,
                       keep: int = RETENTION_COUNT, days: int = RETENTION_DAYS):
    """Own test descendants and output; mark default runs for bounded retention."""
    root = root.resolve()
    parent = _parent(root)
    if selected is not None:
        with _explicit_run(root, parent, selected) as held:
            yield held
        return
    with _default_run(root, parent, keep, days) as held:
        yield held


@contextmanager
def _default_run(root: Path, parent: Path, keep: int, days: int):
    procs = _runtime()[1]  # an unusable installation refuses before a run directory exists
    output, receipt = _new_run(root, parent)
    with procs.own_processes((_lease(parent, output.name),), label="test evidence") as owner:
        _write_receipt(output, receipt)
        prune_test_runs(root, keep=keep, days=days)
        try:
            yield output, owner
        finally:
            receipt["finished_at"] = time.time()
            _write_receipt(output, receipt)


def test_commands(python: str = "python", workers: int = E2E_WORKERS,
                  unit_workers: int = UNIT_WORKERS) -> list[list[str]]:
    """The development and CI schedule, also rendered in contributor guidance."""
    unit = [python, "-m", "pytest", "tests/unit", "-p", "no:randomly"]
    if unit_workers > 1:
        unit += ["-n", str(unit_workers), "--dist", "worksteal"]
    return [unit,
            [python, "-m", "pytest", "tests/e2e", "-n", str(workers), "-p", "no:randomly", "--dist", "worksteal"]]


def _suite(command: list[str], root: Path, scratch: Path, name: str, coverage: bool,
           owner=None) -> int:
    environment = dict(os.environ)
    # The junit file and COVERAGE_FILE below are already per-run. pytest's cache
    # is not: it lives at the rootdir, so concurrent runners in one repo stage
    # and delete `pytest-cache-files-*` under a peer's collector. Windows keeps a
    # deleted directory listed until the last handle closes, so that peer gets a
    # durable FileNotFoundError. Nothing here reads the cache, so turn it off.
    command = [*command, "-p", "no:cacheprovider", f"--junitxml={scratch / (name + '.xml')}"]
    if coverage:
        # Nested pytest resolves this in its own cwd. Coverage's subprocess
        # startup keeps the absolute outer path for ordinary CLI children.
        environment["COVERAGE_FILE"] = str((scratch / (".coverage." + name)).relative_to(root))
        command += ["--cov=crapkit", "--cov-branch", "--cov-report="]
        config = environment.pop("COVERAGE_RCFILE", None)
        if config:
            command.append("--cov-config=" + config)
    return _runtime()[1].run_owned(command, owner=owner, cwd=root, env=environment).returncode


def _incomplete_suite(name: str, code: int, reason: str) -> ET.Element:
    suite = ET.Element("testsuite", name=name, tests="1", errors="1", failures="0")
    case = ET.SubElement(suite, "testcase", classname="test_runner", name=name + " evidence")
    ET.SubElement(case, "error", message=f"{name} exited {code}; {reason}")
    return suite


def _unfinished(text: str, code: int) -> str:
    """Why a session's report is not a completed pytest run, or "" when it is.

    junitparse owns the report's rules, the ones the lane and CI's probe apply
    to the same file. The runner adds one: pytest's exit agrees with the report,
    so 0, or 1 with a recorded failure.
    """
    junit, errors = _runtime_module("junitparse"), _runtime_module("errors")
    try:
        failed, _ = junit.suite_summary(text)
    except errors.ToolError as exc:
        return str(exc)
    return "" if _exit_agrees(code, failed) else "JUnit did not record a completed pytest run"


def _exit_agrees(code: int, failed: set) -> bool:
    return code == 0 or (code == 1 and bool(failed))


def _suite_xml(scratch: Path, name: str, code: int) -> tuple[list, bool]:
    try:
        text = (scratch / (name + ".xml")).read_text(encoding="utf-8")
        suite = ET.fromstring(text)
    except (OSError, UnicodeError, ET.ParseError) as exc:
        return [_incomplete_suite(name, code, str(exc))], False
    parts = list(suite) if suite.tag == "testsuites" else [suite]
    reason = _unfinished(text, code)
    if reason:
        return [*parts, _incomplete_suite(name, code, reason)], False
    return parts, True


def _junit(scratch: Path, output: Path, results: dict[str, int]) -> bool:
    combined = ET.Element("testsuites")
    complete = []
    for name, code in results.items():
        parts, valid = _suite_xml(scratch, name, code)
        individual = ET.Element("testsuites")
        individual.extend(parts)
        ET.ElementTree(individual).write(output.with_name(name + ".xml"),
                                         encoding="utf-8", xml_declaration=True)
        combined.extend(parts)
        complete.append(valid)
    ET.ElementTree(combined).write(output, encoding="utf-8", xml_declaration=True)
    return all(complete)


def _coverage(root: Path, scratch: Path, output: Path, suites, owner=None) -> None:
    environment = dict(os.environ, COVERAGE_FILE=str(scratch / ".coverage"))
    sources = [str(scratch / (".coverage." + name)) for name in suites]
    command = [sys.executable, "-m", "coverage"]
    run = _runtime()[1].run_owned
    run([*command, "combine", "--keep", *sources], cwd=root, env=environment,
        owner=owner).check_returncode()
    run([*command, "json", "--show-contexts", "-o", str(output)],
        cwd=root, env=environment, owner=owner).check_returncode()


def _retain_incomplete(scratch: Path, output: Path) -> None:
    retained = output / "incomplete" / scratch.name
    shutil.copytree(scratch, retained)
    print(f"incomplete test evidence retained at {retained}", file=sys.stderr)


def _retain_suite_data(scratch: Path, output: Path) -> None:
    for name in SUITES:
        source = scratch / (".coverage." + name)
        if source.is_file():
            shutil.copyfile(source, output / (name + ".coverage"))


def _collect(root: Path, scratch: Path, output: Path, results: dict[str, int], coverage: bool,
             owner=None) -> bool:
    _retain_suite_data(scratch, output)
    complete = _junit(scratch, output / "junit.xml", results)
    if not complete:
        _retain_incomplete(scratch, output)
        return False
    try:
        if coverage:
            _coverage(root, scratch, output / "py.json", results, owner)
    except (OSError, subprocess.CalledProcessError):
        _retain_incomplete(scratch, output)
        raise
    return True


def _clear_outputs(output: Path) -> None:
    for name in ("junit.xml", "py.json", "unit.xml", "e2e.xml", "unit.coverage", "e2e.coverage"):
        (output / name).unlink(missing_ok=True)


def run_suites(root: Path, *, coverage: bool = False, workers: int = E2E_WORKERS,
               output: Path | None = None, unit_workers: int = UNIT_WORKERS,
               suites: tuple[str, ...] = SUITES,
               keep: int = RETENTION_COUNT, days: int = RETENTION_DAYS) -> int:
    """Run the selected suites unless cancelled; stop descendants before releasing output."""
    root = root.resolve()
    schedule = _schedule(suites, workers, unit_workers)
    with evidence_directory(root, selected=output, keep=keep, days=days) as held:
        return _run_owned_suites(root, *held, coverage, schedule)

def _schedule(suites, workers: int, unit_workers: int) -> dict[str, list[str]]:
    """The selected suites' commands, in the order the shared schedule runs them."""
    commands = zip(SUITES, test_commands(sys.executable, workers, unit_workers))
    return {name: command for name, command in commands if name in suites}


def _run_owned_suites(root, output, owner, coverage, schedule) -> int:
    print(f"test evidence: {output}", flush=True)
    _clear_outputs(output)
    with tempfile.TemporaryDirectory(prefix="suites-", dir=output) as directory:
        scratch = Path(directory)
        results = _execute_suites(root, scratch, output, owner, coverage, schedule)
        complete = _collect(root, scratch, output, results, coverage, owner)
    return int(any(results.values()) or not complete)


def _execute_suites(root, scratch, output, owner, coverage, schedule) -> dict[str, int]:
    try:
        return {name: _suite(command, root, scratch, name, coverage, owner)
                for name, command in schedule.items()}
    except BaseException:
        _retain_incomplete(scratch, output)
        raise


def _known_failures() -> tuple:
    failures = [OSError, ET.ParseError, subprocess.CalledProcessError, ValueError]
    for package in ("crapkit", "_crapkit_test_runner"):
        if errors := sys.modules.get(package + ".errors"):
            failures.append(errors.CrapkitError)
        if processes := sys.modules.get(package + ".procs"):
            failures.append(processes.CommandCancelled)
    return tuple(failures)


def _retention_limit(text: str) -> int:
    """A retention flag's value. A bad one is a usage error, exit 2."""
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be an integer >= 0, got {text!r}")
    return value


def _retention_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--retention-days", type=_retention_limit, default=RETENTION_DAYS,
                        help="at each default run, remove finished default runs older than this "
                             "many days; 0 disables (default: 7)")
    parser.add_argument("--retention-count", type=_retention_limit, default=RETENTION_COUNT,
                        help="at each default run, keep this many recent finished default runs; "
                             "0 disables (default: 10)")
    parser.add_argument("--preview-retention", action="store_true",
                        help="print the default runs these limits would remove, as JSON, "
                             "and run no suite")


def _preview_retention(args: argparse.Namespace) -> int:
    planned = prune_test_runs(args.repo, keep=args.retention_count, days=args.retention_days,
                              dry_run=True)
    print(json.dumps(planned, indent=2, sort_keys=True))
    return 0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--workers", type=int, default=E2E_WORKERS)
    parser.add_argument("--unit-workers", type=int, default=UNIT_WORKERS,
                        help="unit processes (default: 4); use 1 for a serial reproduction")
    parser.add_argument("--suite", nargs="+", choices=SUITES, default=list(SUITES),
                        help="run only these pytest sessions (default: both); CI gives each "
                             "slow Windows session its own job")
    parser.add_argument("--output", type=Path,
                        help="replace evidence in this directory inside --repo; caller owns it "
                             "(default: a unique retained .crapkit/test-runs directory)")
    _retention_flags(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        if args.preview_retention:
            return _preview_retention(args)
        return run_suites(args.repo, coverage=args.coverage, workers=args.workers,
                          output=args.output, unit_workers=args.unit_workers,
                          suites=tuple(args.suite),
                          keep=args.retention_count, days=args.retention_days)
    except KeyboardInterrupt:
        print("test run cancelled; owned descendants stopped", file=sys.stderr)
        return 130
    except _known_failures() as exc:
        print(f"test evidence is incomplete: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
