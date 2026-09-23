"""Build separate base/candidate wheels and judge their actual CRAP verdict."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


SCHEDULE = Path(__file__).with_name("run.py")
SIDES = ("base", "candidate")
# What the join reads from each measurement job's hand-off before trusting it.
PROOF_FIELDS = ("wheel", "wheel_sha256", "commit", "suite_exit")
_OWNER = ContextVar("ci_command_owner", default=None)
JUNIT_PROBE = (
    "from pathlib import Path\nimport json\n"
    "from crapkit.junitparse import suite_summary\n"
    "failed, counts = suite_summary(Path('.crapkit/cov/junit.xml').read_text(encoding='utf-8'))\n"
    "print(json.dumps(dict(counts, failures=sorted(failed))))\n")


@lru_cache(maxsize=1)
def _processes():
    """Load driver ownership without changing the package selected by its children."""
    if sys.platform == "linux":
        return _linux_processes()
    package = Path(__file__).resolve().parents[2] / "src/crapkit"
    spec = importlib.util.spec_from_file_location(
        "_crapkit_ci_driver", package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(spec.name + ".procs")


def _linux_processes():
    spec = importlib.util.spec_from_file_location("_crapkit_ci_linux", Path(__file__).with_name("_ci_linux.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _commands():
    with _processes().own_processes(()) as owner:
        token = _OWNER.set(owner)
        try:
            yield
        finally:
            _OWNER.reset(token)


def _run(command, *, check=False, text=False, **kwargs):
    result = _processes().run_owned(command, owner=_OWNER.get(), **kwargs)
    if check:
        result.check_returncode()
    return result


def _git(root: Path, *args: str) -> str:
    return _run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment(python: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(("PYTHON", "COVERAGE_", "COV_CORE_")):
            del environment[key]
    environment["PATH"] = str(python.parent) + os.pathsep + environment.get("PATH", "")
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _package_files(path: Path) -> dict[str, str]:
    return {file.relative_to(path).as_posix(): _sha(file) for file in path.rglob("*.py")}


def installed_source(root: Path, python: Path, environment: dict) -> dict:
    """Prove the imported wheel contains exactly this checkout's Python source."""
    command = "import crapkit; print(crapkit.__file__)"
    done = _run([str(python), "-I", "-c", command], cwd=root,
                          env=environment, capture_output=True, text=True, check=True)
    package = Path(done.stdout.strip()).resolve().parent
    package.relative_to(python.parent.parent.resolve())
    expected = _package_files(root / "src/crapkit")
    if not expected or _package_files(package) != expected:
        raise ValueError("installed wheel source differs from the scored checkout")
    return {"package": str(package), "source_sha256": expected}


def _install_wheel(destination: Path, requirement: str) -> tuple[Path, dict]:
    """A fresh venv holding one wheel requirement and nothing from the caller's site."""
    venv = destination / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = _environment(python)
    _run([str(python), "-m", "pip", "install", requirement], env=environment, check=True)
    return python, environment


def install_revision(root: Path, destination: Path) -> tuple[Path, dict, dict]:
    """Build one wheel, install it in a clean environment, and verify its bytes."""
    dist = destination / "dist"
    _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(root)], check=True)
    wheel, = dist.glob("*.whl")
    python, environment = _install_wheel(destination, str(wheel) + "[dev]")
    proof = installed_source(root, python, environment)
    proof.update(wheel=wheel.name, wheel_sha256=_sha(wheel), commit=_git(root, "rev-parse", "HEAD"))
    return python, environment, proof


def reinstall_revision(root: Path, measured: Path, destination: Path) -> tuple[Path, dict, dict]:
    """Install the wheel a measurement job handed off, and prove it is this checkout's.

    The measurement ran on another runner. Its wheel must be the bytes it recorded
    and come from the commit checked out here, and the installed source must be
    this checkout's, before its coverage evidence stands for this revision. The
    join runs no suite, so the wheel goes in without the dev extra.
    """
    proof = _read_proof(measured)
    wheel = _measured_wheel(root, measured, proof)
    python, environment = _install_wheel(destination, str(wheel))
    proof["reinstalled"] = installed_source(root, python, environment)["package"]
    _copy_evidence(measured / "cov", root / ".crapkit/cov")
    return python, environment, proof


def _read_proof(measured: Path) -> dict:
    proof = json.loads((measured / "proof.json").read_text(encoding="utf-8"))
    missing = [field for field in PROOF_FIELDS if field not in proof]
    if missing:
        raise ValueError(f"{measured.name} proof lacks {', '.join(missing)}")
    return proof


def _measured_wheel(root: Path, measured: Path, proof: dict) -> Path:
    wheel = measured / proof["wheel"]
    if _sha(wheel) != proof["wheel_sha256"]:
        raise ValueError(f"{wheel.name} is not the wheel its measurement recorded")
    commit = _git(root, "rev-parse", "HEAD")
    if proof["commit"] != commit:
        raise ValueError(f"measurement of {proof['commit']} cannot stand for checkout {commit}")
    return wheel


def _copy_evidence(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)


def _measure(root: Path, python: Path, environment: dict, proof: dict) -> int:
    output = root / ".crapkit"
    output.mkdir(exist_ok=True)
    config = output / "wheel.coveragerc"
    paths = [str(root / "src/crapkit"), proof["package"]]
    mapped = "\n".join("    " + path.replace("$", "$$") for path in paths)
    config.write_text("[run]\nbranch = true\npatch = subprocess\n"
                      "source = crapkit\n[paths]\nsource =\n" + mapped + "\n", encoding="utf-8")
    measured_env = dict(environment, COVERAGE_RCFILE=str(config))
    schedule = _measurement_runner(root)
    proof["runner"] = {"path": str(schedule), "sha256": _sha(schedule),
                       "source": "revision" if schedule != SCHEDULE else "driver fixture fallback"}
    return _run([str(python), str(schedule), "--repo", str(root), "--coverage",
                           "--output", ".crapkit/cov"],
                          cwd=root, env=measured_env).returncode


def _measurement_runner(root: Path) -> Path:
    schedule = root / "tools/testing/run.py"
    if schedule.is_file():
        return schedule
    if (root / ".git").exists():
        raise ValueError("revision has no tools/testing/run.py; select a baseline with the shared runner")
    return SCHEDULE


def _crapkit(root: Path, python: Path, environment: dict, *args: str) -> tuple[int, dict]:
    result = _run([str(python), "-m", "crapkit", *args, "--json"],
                            cwd=root, env=environment, capture_output=True, text=True)
    print(result.stderr, file=sys.stderr, end="")
    if not result.stdout.strip():
        raise ValueError(f"crapkit produced no verdict, exit {result.returncode}")
    return result.returncode, json.loads(result.stdout)


def _copy_baseline(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)


def _ledger(root: Path) -> dict:
    with closing(sqlite3.connect(root / ".crapkit/crap.sqlite")) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT id,commit_sha,kind,verdict_ok,findings "
                               "FROM runs ORDER BY id DESC LIMIT 1").fetchone())


def _test_evidence(root: Path, python: Path, environment: dict) -> dict:
    """Use that revision's JUnit admission before explicit artifact reuse."""
    result = _run([str(python), "-c", JUNIT_PROBE], cwd=root, env=environment,
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"{root.name} test evidence is incomplete: {result.stderr.strip()}")
    return json.loads(result.stdout)


def verify_pair(base: Path, candidate: Path, base_python: Path, candidate_python: Path,
                base_env: dict, candidate_env: dict) -> tuple[int, dict]:
    """Carry the complete baseline ledger into the candidate's real verdict."""
    tests = {"base": _test_evidence(base, base_python, base_env),
             "candidate": _test_evidence(candidate, candidate_python, candidate_env)}
    code, baseline = _crapkit(base, base_python, base_env, "coverage", "--reuse-artifacts")
    if code:
        raise ValueError(f"base coverage failed: {baseline}")
    _copy_baseline(base / ".crapkit/crap.sqlite", candidate / ".crapkit/crap.sqlite")
    code, verdict = _crapkit(candidate, candidate_python, candidate_env, "verify",
                            "--reuse-artifacts", "--no-tighten", "--base", _git(base, "rev-parse", "HEAD"))
    if "ok" not in verdict:
        raise ValueError(f"candidate verification refused: {verdict}")
    ledger = _ledger(candidate)
    if (ledger["id"], ledger["kind"], bool(ledger["verdict_ok"]), ledger["commit_sha"]) != (
            verdict["run_id"], "verify", verdict["ok"], _git(candidate, "rev-parse", "HEAD")):
        raise ValueError("verification output disagrees with the runs ledger")
    return code, {**verdict, "ledger": ledger, "tests": tests}


def _checkout(repo: Path, directory: Path, ref: str) -> Path:
    revision = _git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    _run(["git", "clone", "--shared", "--quiet", str(repo), str(directory)], check=True)
    _git(directory, "checkout", "--quiet", "--detach", revision)
    return directory


def _retain_revision(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _copy_evidence(source / ".crapkit/cov", destination / "cov")
    ledger = source / ".crapkit/crap.sqlite"
    if ledger.is_file():
        _copy_baseline(ledger, destination / "crap.sqlite")


def _retain_evidence(scratch: Path, output: Path, evidence: dict) -> None:
    retained = output / evidence["evidence_dir"]
    try:
        for name in ("base", "candidate"):
            _retain_revision(scratch / name, retained / name)
    finally:
        text = json.dumps(evidence, indent=2) + "\n"
        (output / "verdict.json").write_text(text, encoding="utf-8")
        (retained / "verdict.json").write_text(text, encoding="utf-8")


def _side_ref(side: str, base_ref: str) -> str:
    return base_ref if side == "base" else "HEAD"


def _checkouts(repo: Path, base_ref: str, scratch: Path) -> dict[str, Path]:
    return {side: _checkout(repo, scratch / side, _side_ref(side, base_ref)) for side in SIDES}


def _installed(roots: dict, evidence: dict, install) -> dict[str, tuple[Path, dict]]:
    """Install each side in turn, recording its proof and the phase reached."""
    installs = {}
    for side in SIDES:
        evidence["phase"] = side + "-install"
        python, environment, evidence[side] = install(side, roots[side])
        installs[side] = (python, environment)
    return installs


def _judge(roots: dict, installs: dict, results: list[int], evidence: dict) -> int:
    evidence["suite_exits"] = results
    evidence["phase"] = "verify"
    (base_python, base_env), (candidate_python, candidate_env) = installs["base"], installs["candidate"]
    code, evidence["verdict"] = verify_pair(roots["base"], roots["candidate"], base_python,
                                            candidate_python, base_env, candidate_env)
    evidence["phase"] = "complete"
    return code or int(bool(results[1]))


def _compare_checkouts(repo: Path, base_ref: str, scratch: Path, evidence: dict) -> int:
    roots = _checkouts(repo, base_ref, scratch)
    installs = _installed(roots, evidence, lambda side, root: install_revision(
        root, scratch / (side + "-install")))
    evidence["phase"] = "measure"
    results = [_measure(roots[side], *installs[side], evidence[side]) for side in SIDES]
    return _judge(roots, installs, results, evidence)


def _join_checkouts(repo: Path, base_ref: str, measured: Path, scratch: Path, evidence: dict) -> int:
    roots = _checkouts(repo, base_ref, scratch)
    installs = _installed(roots, evidence, lambda side, root: reinstall_revision(
        root, measured / side, scratch / (side + "-install")))
    return _judge(roots, installs, [evidence[side]["suite_exit"] for side in SIDES], evidence)


# What stops a verdict step and becomes its recorded error, not a traceback.
_FAILURES = (OSError, ValueError, subprocess.CalledProcessError)


def measure(repo: Path, base_ref: str, side: str, measured: Path) -> int:
    """Measure one side's installed wheel and hand the join what it needs to judge it.

    A failing suite still hands off: whether a baseline failure stands and a
    candidate failure fails is the join's call, the same one compare makes. A
    step that stops the measurement hands off failure.json instead, naming the
    phase it reached and the error, because the join never runs after it.
    """
    record = {"phase": "checkout"}
    try:
        with tempfile.TemporaryDirectory(prefix="crapkit-ci-") as directory:
            _measure_side(repo, base_ref, side, Path(directory), measured / side, record)
    except _FAILURES as exc:
        _record_failure(measured / side, dict(record, error=str(exc)))
        raise
    return 0


def _measure_side(repo: Path, base_ref: str, side: str, scratch: Path, destination: Path,
                  record: dict) -> None:
    with _commands():
        root = _checkout(repo, scratch / side, _side_ref(side, base_ref))
        record["phase"] = "install"
        python, environment, proof = install_revision(root, scratch / "install")
        record["phase"] = "measure"
        proof["suite_exit"] = _measure(root, python, environment, proof)
    record["phase"] = "hand-off"
    _hand_off(root, scratch / "install/dist" / proof["wheel"], proof, destination)


def _hand_off(root: Path, wheel: Path, proof: dict, destination: Path) -> None:
    """Replace one side's hand-off: its coverage evidence, its wheel and their proof."""
    _clear_hand_off(destination)
    _copy_evidence(root / ".crapkit/cov", destination / "cov")
    shutil.copyfile(wheel, destination / wheel.name)
    (destination / "proof.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


def _record_failure(destination: Path, failure: dict) -> None:
    """Replace one side's hand-off with why its measurement stopped."""
    _clear_hand_off(destination)
    (destination / "failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")


def _clear_hand_off(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "cov").is_dir():
        shutil.rmtree(destination / "cov")
    for stale in [destination / "proof.json", destination / "failure.json", *destination.glob("*.whl")]:
        stale.unlink(missing_ok=True)


def join(repo: Path, base_ref: str, measured: Path, output: Path) -> int:
    """Judge the two hand-offs, then persist the verdict and provenance."""
    return _judged(output, lambda scratch, evidence: _join_checkouts(
        repo, base_ref, measured, scratch, evidence))


def compare(repo: Path, base_ref: str, output: Path) -> int:
    """Measure isolated installations, then persist the verdict and provenance."""
    return _judged(output, lambda scratch, evidence: _compare_checkouts(
        repo, base_ref, scratch, evidence))


def _judged(output: Path, judge) -> int:
    """Run one judgment under owned commands and retain its evidence either way."""
    output.mkdir(parents=True, exist_ok=True)
    retained = Path(tempfile.mkdtemp(prefix="attempt-", dir=output))
    evidence = {"phase": "checkout", "base": None, "candidate": None,
                "suite_exits": [], "verdict": None, "evidence_dir": retained.name}
    with tempfile.TemporaryDirectory(prefix="crapkit-ci-") as directory:
        scratch = Path(directory)
        try:
            with _commands():
                return judge(scratch, evidence)
        except _FAILURES as exc:
            evidence["error"] = str(exc)
            raise
        finally:
            _retain_evidence(scratch, output, evidence)


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, default=Path(".crapkit/ci-verdict"))
    parser.add_argument("--measured", type=Path, default=Path(".crapkit/ci-measure"),
                        help="where --measure hands off one side and --join reads both")
    split = parser.add_mutually_exclusive_group()
    split.add_argument("--measure", choices=SIDES,
                       help="measure one side's installed wheel and hand it off, one CI job per side")
    split.add_argument("--join", action="store_true",
                       help="judge both hand-offs after proving each wheel again")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_arguments(argv)
    try:
        return _dispatch(args)
    except _FAILURES as exc:
        print(f"isolated CI verification failed: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    if args.measure:
        return measure(repo, args.base, args.measure, args.measured.resolve())
    if args.join:
        return join(repo, args.base, args.measured.resolve(), args.output.resolve())
    return compare(repo, args.base, args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
