"""Record every coverage producer's artifact over the probes, or check that it still holds.

    python tests/accuracy/coverage_oracles/regenerate.py record [--producer NAME ...]
    python tests/accuracy/coverage_oracles/regenerate.py check [--producer NAME ...]

`record` runs each producer over a scratch copy of probes/ in both scenarios
(`call` drives every shape, `idle` only imports) and writes
recorded/<producer>/<scenario>.json in canonical form: keys and paths relative
to the probe root, the checkout root and the timestamp replaced, keys sorted.
Some producers write more files (lcov, coverage xml, JUnit), recorded beside it.

`check` runs them again into a temporary directory and compares each fresh
artifact with the committed one, never as raw bytes: once as per-function counts
(counts_table), once as crapkit's parsed FnCoverage, and once in canonical form.

Exit 0 when every producer matched, 1 when one differs, 3 when a producer could
not run here (node_modules not installed, no `uv`, no network for a venv).

The committed recordings were made in the accuracy image (Linux, node 22.23.3,
CPython 3.12.14): node producers read CRAPKIT_ACCURACY_NODE_ROOT, or
tools/accuracy/node; Python producers each get a venv built by `uv` under
CRAPKIT_ACCURACY_PRODUCERS (default ~/.cache/crapkit-accuracy/producers).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PROBES = HERE / "probes"
RECORDED = HERE / "recorded"
REPO = HERE.parents[2]
SCENARIOS = ("call", "idle")
FIXED_TIME = "1970-01-01T00:00:00"
ROOT_MARK = "<root>"


class ProducerMissing(RuntimeError):
    """A producer cannot run on this machine."""


@dataclass(frozen=True)
class Producer:
    """One coverage tool. `steps` are argv templates run from the probe root:
    {py} the producer's interpreter, {node} node, {out} the scenario's output
    directory, {s} the scenario. `files` maps a recorded suffix to the file a run
    leaves under {out}."""
    name: str
    kind: str
    steps: tuple
    files: dict
    packages: tuple = ()
    env: dict = field(default_factory=dict)


_COVERAGE_RUN = ("{py}", "-m", "coverage", "run", "--rcfile=py/coveragerc",
                 "--data-file={out}/.coverage", "py/drive.py", "{s}")


def _coverage_json(extra: tuple = ()) -> tuple:
    return ("{py}", "-m", "coverage", "json", "--rcfile=py/coveragerc",
            "--data-file={out}/.coverage", "-o", "{out}/artifact.json", *extra)


def _coveragepy(version: str) -> Producer:
    return Producer(f"coveragepy-{version}", "python", (_COVERAGE_RUN, _coverage_json()),
                    {".json": "artifact.json"}, (f"coverage=={version}",))


def _coverage_reports() -> Producer:
    """7.16.1 again, with the lcov and xml reports of the same data file."""
    lcov = ("{py}", "-m", "coverage", "lcov", "--rcfile=py/coveragerc",
            "--data-file={out}/.coverage", "-o", "{out}/artifact.lcov")
    xml = ("{py}", "-m", "coverage", "xml", "--rcfile=py/coveragerc",
           "--data-file={out}/.coverage", "-o", "{out}/artifact.xml")
    return Producer("coveragepy-reports-7.16.1", "python", (_COVERAGE_RUN, _coverage_json(), lcov, xml),
                    {".json": "artifact.json", ".lcov": "artifact.lcov", ".xml": "artifact.xml"},
                    ("coverage==7.16.1",))


def _pytest_cov(version: str, pytest: str, extra: tuple = (), suffix: str = "",
                rc: str = "py/coveragerc") -> Producer:
    run = ("{py}", "-m", "pytest", "-p", "no:cacheprovider", "-q", "py/test_drive.py", "-k",
           "test_{s}", "--cov", f"--cov-config={rc}", "--cov-branch",
           "--cov-report=json:{out}/artifact.json", "--junitxml={out}/junit.xml",
           "-o", "junit_family=xunit2", *extra)
    return Producer(f"pytest-cov-{version}{suffix}", "python", (run,),
                    {".json": "artifact.json", "-junit.xml": "junit.xml"},
                    (f"pytest-cov=={version}", f"pytest=={pytest}", "coverage==7.16.1"))


def _vitest(provider: str) -> Producer:
    run = ("{node}", "node_modules/vitest/vitest.mjs", "run", "--config",
           "harness/vitest.config.mjs", "--root", ".")
    files = {".json": "coverage-final.json"}
    if provider == "istanbul":
        files[".lcov"] = "lcov.info"
    return Producer(f"vitest-{provider}-5.0.1", "node", (run,), files,
                    env={"PROBE_PROVIDER": provider, "PROBE_REPORTERS": ",".join(
                        ["json", "lcovonly"] if provider == "istanbul" else ["json"])})


def _jest(provider: str) -> Producer:
    run = ("{node}", "node_modules/jest/bin/jest.js", "--ci")
    return Producer(f"jest-{provider}-30.5.2", "node", (run,), {".json": "coverage-final.json"},
                    env={"PROBE_PROVIDER": provider})


_NYC = Producer("nyc-18.0.0", "node", (("{node}", "node_modules/nyc/bin/nyc.js", "--reporter=json",
                                         "--report-dir={out}", "--temp-dir={out}/.nyc",
                                         "--include=js/shapes.js", "{node}",
                                         "harness/run-node.cjs", "{s}"),),
                {".json": "coverage-final.json"})
_C8 = Producer("c8-12.0.0", "node", (("{node}", "node_modules/c8/bin/c8.js", "--reporter=json",
                                       "--report-dir={out}", "--temp-directory={out}/.c8",
                                       "--include=mjs/shapes.mjs", "{node}",
                                       "harness/run-node.mjs", "{s}"),),
               {".json": "coverage-final.json"})

PRODUCERS = {producer.name: producer for producer in (
    _coveragepy("7.16.1"), _coveragepy("7.13.0"), _coveragepy("7.10.6"), _coverage_reports(),
    _pytest_cov("5.0.0", "8.3.5"), _pytest_cov("7.1.0", "9.1.1"),
    _pytest_cov("7.1.0", "9.1.1", ("--cov-context=test",), "-contexts", "py/coveragerc-contexts"),
    _vitest("v8"), _vitest("istanbul"), _jest("babel"), _jest("v8"), _NYC, _C8)}


# --- where the tools come from ----------------------------------------------------------------

def node_modules() -> Path:
    base = os.environ.get("CRAPKIT_ACCURACY_NODE_ROOT") or str(REPO / "tools" / "accuracy" / "node")
    found = Path(base) / "nightly" / "node_modules"
    if not (found / "vitest").is_dir():
        raise ProducerMissing(f"no nightly node_modules at {found}; run npm ci --prefix "
                              "tools/accuracy/node/nightly")
    return found


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _uv(*args: str) -> None:
    if shutil.which("uv") is None:
        raise ProducerMissing("uv is not on PATH; it builds the Python producers' venvs")
    done = subprocess.run(["uv", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise ProducerMissing(f"uv {' '.join(args)} failed: {done.stderr.strip()[-400:]}")


def python_for(producer: Producer) -> Path:
    """The producer's own interpreter: a venv holding exactly its pinned packages."""
    base = Path(os.environ.get("CRAPKIT_ACCURACY_PRODUCERS")
                or Path.home() / ".cache" / "crapkit-accuracy" / "producers")
    venv = base / f"{producer.name}-py{sys.version_info[0]}{sys.version_info[1]}"
    python = _venv_python(venv)
    if not python.is_file():
        _uv("venv", "--quiet", "--python", sys.executable, str(venv))
        _uv("pip", "install", "--quiet", "--python", str(python), *producer.packages)
    return python


# --- one run --------------------------------------------------------------------------------

def _link(target: Path, link: Path) -> None:
    """node_modules beside the probes: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def stage(work: Path, producer: Producer) -> Path:
    """A scratch probe root: the probes, and for node producers node_modules and
    jest's config at the root (jest on Windows reads --config only there)."""
    root = work / "probes"
    shutil.copytree(PROBES, root)
    if producer.kind == "node":
        _link(node_modules(), root / "node_modules")
        shutil.copyfile(root / "harness" / "jest.config.cjs", root / "jest.config.cjs")
    return root


def _argv(step: tuple, values: dict) -> list[str]:
    return [part.format(**values) for part in step]


def _tool(producer: Producer) -> str:
    if producer.kind == "node":
        return shutil.which("node") or "node"
    return str(python_for(producer))


def run(producer: Producer, root: Path, scenario: str, out: Path) -> Path:
    """Run one scenario; `out` holds what it wrote."""
    out.mkdir(parents=True, exist_ok=True)
    tool = _tool(producer)
    values = {"py": tool, "node": tool, "out": out.as_posix(), "s": scenario}
    env = {**os.environ, **producer.env, "PROBE_SCENARIO": scenario, "PROBE_OUT": out.as_posix()}
    for step in producer.steps:
        done = subprocess.run(_argv(step, values), cwd=root, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if done.returncode != 0:
            raise RuntimeError(f"{producer.name} {scenario}: {' '.join(step)} exited "
                               f"{done.returncode}:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}")
    return out


# --- canonical form -----------------------------------------------------------------------------

def _rooted(text: str, root: str) -> str:
    return text.replace("\\", "/").replace(root.replace("\\", "/").rstrip("/") + "/", "")


def _istanbul_file(data: dict, root: str) -> dict:
    kept = {key: value for key, value in data.items() if key not in ("inputSourceMap", "hash")}
    kept["path"] = _rooted(kept.get("path", ""), root)
    return kept


def canonical(artifact: dict, root: str) -> dict:
    """Keys and paths relative to the probe root, the volatile fields fixed."""
    if "meta" in artifact and "files" in artifact:
        meta = {**artifact["meta"], "timestamp": FIXED_TIME}
        files = {_rooted(key, root): value for key, value in artifact["files"].items()}
        return {**artifact, "meta": meta, "files": files}
    return {_rooted(key, root): _istanbul_file(value, root) for key, value in artifact.items()}


_VOLATILE_ATTRIBUTES = re.compile(r'\b(timestamp|time|hostname)="[^"]*"')


def canonical_text(text: str, root: str) -> str:
    """lcov, xml and JUnit: the root marked, times and the host name fixed."""
    posix = root.replace("\\", "/").rstrip("/")
    marked = text.replace(root, ROOT_MARK).replace(posix, ROOT_MARK).replace("\r\n", "\n")
    return _VOLATILE_ATTRIBUTES.sub(lambda match: f'{match.group(1)}="0"', marked)


def _recorded_bytes(suffix: str, produced: Path, root: Path) -> bytes:
    if suffix == ".json":
        artifact = canonical(json.loads(produced.read_bytes()), str(root))
        return (json.dumps(artifact, sort_keys=True, indent=1) + "\n").encode()
    return canonical_text(produced.read_bytes().decode("utf-8"), str(root)).encode()


def record_one(producer: Producer, root: Path, scenario: str, out: Path, dest: Path) -> None:
    run(producer, root, scenario, out)
    dest.mkdir(parents=True, exist_ok=True)
    for suffix, name in producer.files.items():
        dest.joinpath(scenario + suffix).write_bytes(_recorded_bytes(suffix, out / name, root))


# --- checking a fresh run against the committed one ---------------------------------------

def _counts(path: Path) -> list:
    import counts_table
    artifact = json.loads(path.read_bytes())
    if "files" in artifact:
        return sorted(counts_table.coveragepy_rows(artifact), key=repr)
    return sorted(counts_table.istanbul_rows(artifact, "line"), key=repr)


def _fn_coverage(path: Path, kind: str) -> dict:
    from crapkit.coverage_istanbul import parse_istanbul_both_file
    from crapkit.coverage_py import parse_coveragepy_both_file
    if kind == "python":
        return parse_coveragepy_both_file(path, path_prefix="")[0]
    return parse_istanbul_both_file(path, repo_root="")[0]


def differences(producer: Producer, fresh: Path, committed: Path) -> list[str]:
    """What changed between two recordings of one scenario, as three readings."""
    if not committed.is_file():
        return [f"{committed} is not recorded yet; run regenerate.py record"]
    found = []
    if _counts(fresh) != _counts(committed):
        found.append(f"{committed.name}: per-function counts differ")
    if _fn_coverage(fresh, producer.kind) != _fn_coverage(committed, producer.kind):
        found.append(f"{committed.name}: crapkit's parsed FnCoverage differs")
    if json.loads(fresh.read_bytes()) != json.loads(committed.read_bytes()):
        found.append(f"{committed.name}: the canonical artifact differs")
    return found


def check_one(producer: Producer, root: Path, scenario: str, out: Path) -> list[str]:
    fresh = out / "canonical"
    record_one(producer, root, scenario, out, fresh)
    committed = RECORDED / producer.name / f"{scenario}.json"
    return [f"{producer.name}: {line}"
            for line in differences(producer, fresh / f"{scenario}.json", committed)]


def _each(names: list[str], act) -> list[str]:
    problems = []
    for name in names:
        producer = PRODUCERS[name]
        with tempfile.TemporaryDirectory(prefix="probe-") as scratch:
            root = stage(Path(scratch), producer)
            for scenario in SCENARIOS:
                problems += act(producer, root, scenario, Path(scratch) / f"out-{scenario}") or []
    return problems


def _record(producer: Producer, root: Path, scenario: str, out: Path) -> None:
    record_one(producer, root, scenario, out, RECORDED / producer.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("record", "check"))
    parser.add_argument("--producer", action="append", choices=sorted(PRODUCERS))
    args = parser.parse_args(argv)
    sys.path.append(str(HERE))
    act = _record if args.action == "record" else check_one
    try:
        problems = _each(args.producer or sorted(PRODUCERS), act)
    except ProducerMissing as missing:
        print(f"regenerate: {missing}", file=sys.stderr)
        return 3
    print("\n".join(problems) or f"regenerate: {args.action} ok", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
