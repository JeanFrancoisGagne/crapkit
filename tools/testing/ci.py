"""Build separate base/candidate wheels and judge their actual CRAP verdict."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


SCHEDULE = Path(__file__).with_name("run.py")
JUNIT_PROBE = (
    "from pathlib import Path\nimport json\n"
    "from crapkit.junitparse import suite_summary\n"
    "failed, counts = suite_summary(Path('.crapkit/cov/junit.xml').read_text(encoding='utf-8'))\n"
    "print(json.dumps(dict(counts, failures=sorted(failed))))\n")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
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
    done = subprocess.run([str(python), "-I", "-c", command], cwd=root,
                          env=environment, capture_output=True, text=True, check=True)
    package = Path(done.stdout.strip()).resolve().parent
    package.relative_to(python.parent.parent.resolve())
    expected = _package_files(root / "src/crapkit")
    if not expected or _package_files(package) != expected:
        raise ValueError("installed wheel source differs from the scored checkout")
    return {"package": str(package), "source_sha256": expected}


def install_revision(root: Path, destination: Path) -> tuple[Path, dict, dict]:
    """Build one wheel, install it in a clean environment, and verify its bytes."""
    dist = destination / "dist"
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(root)], check=True)
    wheel, = dist.glob("*.whl")
    venv = destination / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = _environment(python)
    subprocess.run([str(python), "-m", "pip", "install", str(wheel) + "[dev]"],
                   env=environment, check=True)
    proof = installed_source(root, python, environment)
    proof.update(wheel=wheel.name, wheel_sha256=_sha(wheel), commit=_git(root, "rev-parse", "HEAD"))
    return python, environment, proof


def _measure(root: Path, python: Path, environment: dict, proof: dict) -> int:
    output = root / ".crapkit"
    output.mkdir(exist_ok=True)
    config = output / "wheel.coveragerc"
    paths = [str(root / "src/crapkit"), proof["package"]]
    mapped = "\n".join("    " + path.replace("$", "$$") for path in paths)
    config.write_text("[run]\nbranch = true\npatch = subprocess\n"
                      "source = crapkit\n[paths]\nsource =\n" + mapped + "\n", encoding="utf-8")
    measured_env = dict(environment, COVERAGE_RCFILE=str(config))
    return subprocess.run([str(python), str(SCHEDULE), "--repo", str(root), "--coverage",
                           "--output", ".crapkit/cov"],
                          cwd=root, env=measured_env).returncode


def _crapkit(root: Path, python: Path, environment: dict, *args: str) -> tuple[int, dict]:
    result = subprocess.run([str(python), "-m", "crapkit", *args, "--json"],
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
    result = subprocess.run([str(python), "-c", JUNIT_PROBE], cwd=root, env=environment,
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
    subprocess.run(["git", "clone", "--shared", "--quiet", str(repo), str(directory)], check=True)
    _git(directory, "checkout", "--quiet", "--detach", revision)
    return directory


def _retain_revision(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    coverage = source / ".crapkit/cov"
    if coverage.is_dir():
        shutil.copytree(coverage, destination / "cov")
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


def _compare_checkouts(repo: Path, base_ref: str, scratch: Path, evidence: dict) -> int:
    base = _checkout(repo, scratch / "base", base_ref)
    candidate = _checkout(repo, scratch / "candidate", "HEAD")
    evidence["phase"] = "base-install"
    bp, be, evidence["base"] = install_revision(base, scratch / "base-install")
    evidence["phase"] = "candidate-install"
    cp, ce, evidence["candidate"] = install_revision(candidate, scratch / "candidate-install")
    evidence["phase"] = "measure"
    results = [_measure(base, bp, be, evidence["base"]),
               _measure(candidate, cp, ce, evidence["candidate"])]
    evidence["suite_exits"] = results
    evidence["phase"] = "verify"
    code, evidence["verdict"] = verify_pair(base, candidate, bp, cp, be, ce)
    evidence["phase"] = "complete"
    return code or int(bool(results[1]))


def compare(repo: Path, base_ref: str, output: Path) -> int:
    """Measure isolated installations, then persist the verdict and provenance."""
    output.mkdir(parents=True, exist_ok=True)
    retained = Path(tempfile.mkdtemp(prefix="attempt-", dir=output))
    evidence = {"phase": "checkout", "base": None, "candidate": None,
                "suite_exits": [], "verdict": None, "evidence_dir": retained.name}
    with tempfile.TemporaryDirectory(prefix="crapkit-ci-") as directory:
        scratch = Path(directory)
        try:
            return _compare_checkouts(repo, base_ref, scratch, evidence)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            evidence["error"] = str(exc)
            raise
        finally:
            _retain_evidence(scratch, output, evidence)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, default=Path(".crapkit/ci-verdict"))
    args = parser.parse_args(argv)
    try:
        return compare(args.repo.resolve(), args.base, args.output.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"isolated CI verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
