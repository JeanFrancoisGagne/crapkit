"""Measure what pytest-cov 7 stopped measuring, four ways.

Every `cmd_*` in `src/crapkit/cli/` is reached through `subprocess.run` by
tests/e2e and by nothing else, so whether the CLI is measured at all comes down
to whether coverage follows a child process. pytest-cov did that itself until
7.0.0 ("Dropped support for subprocesses measurement"); the job now belongs to
coverage's own `[run] patch = ["subprocess"]`, added in coverage 7.10.

This script builds two throwaway venvs, one per pytest-cov pin, and runs one
e2e file in each of them twice: once with the patch key on and once with it
off. The four statement counts for `src/crapkit/cli/admin.py` go to
`pytest_cov7_repro.json` beside this file, with the exact versions that
produced them. `tests/unit/test_notes_contract.py` reads that JSON and fails
when the published note disagrees with it.

The patch key is toggled through `COVERAGE_RCFILE`, which points at a temporary
`.coveragerc` written under the work directory. The repository tree is never
edited, so a run that dies halfway leaves nothing behind to clean up.

    python tools/notes/pytest_cov7_repro.py            # throwaway work dir
    python tools/notes/pytest_cov7_repro.py --workdir D:/scratch/cov7

The run costs two `pip install -e .[dev]` and four serial e2e runs. It needs
network for the pins and about ten minutes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_suffix(".json")

TARGET = "src/crapkit/cli/admin.py"
SUITE = "tests/e2e/test_init_doctor_e2e.py"
PINS = ("7.1.0", "6.3.0")
DISTS = ("pytest", "pytest-cov", "coverage", "pytest-xdist")


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run to completion, raising with the captured output when it fails."""
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)
    if done.returncode:
        raise SystemExit(f"{argv[0]} exited {done.returncode}\n"
                         f"{done.stdout}\n{done.stderr}")
    return done


def make_venv(root: Path, pin: str) -> Path:
    """A venv holding an editable crapkit[dev] and one pytest-cov pin."""
    home = root / f"venv-{pin}"
    _run([sys.executable, "-m", "venv", str(home)])
    py = home / ("Scripts" if os.name == "nt" else "bin") / "python"
    _run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    _run([str(py), "-m", "pip", "install", "-q", "-e", f"{REPO}[dev]"])
    _run([str(py), "-m", "pip", "install", "-q", f"pytest-cov=={pin}"])
    return py


def versions(py: Path) -> dict:
    """What that venv actually resolved, read from its own metadata."""
    code = ("import json, sys, importlib.metadata as md;"
            f"names = {DISTS!r};"
            "print(json.dumps(dict([(n, md.version(n)) for n in names]"
            " + [('python', sys.version.split()[0])])))")
    return json.loads(_run([str(py), "-c", code]).stdout)


def rcfile(root: Path, patch: bool) -> Path:
    """A coverage config differing from its twin in exactly one value."""
    path = root / f"coveragerc-{'on' if patch else 'off'}"
    body = "[run]\npatch = subprocess\n" if patch else "[run]\npatch =\n"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def admin_counts(cov_json: Path) -> dict:
    """Executed and total statements for the CLI module the e2e file drives."""
    report = json.loads(cov_json.read_text(encoding="utf-8"))
    for name, entry in report["files"].items():
        if name.replace("\\", "/").endswith(TARGET):
            summary = entry["summary"]
            return {"executed": summary["covered_lines"],
                    "total": summary["num_statements"]}
    raise SystemExit(f"{TARGET} is missing from {cov_json}")


def child_env(root: Path, tag: str, patch: bool) -> dict:
    """The environment for one measured run: the config and the data file."""
    env = dict(os.environ)
    env["COVERAGE_RCFILE"] = str(rcfile(root, patch))
    env["COVERAGE_FILE"] = str(root / f"data-{tag}")
    env["PYTHONPATH"] = str(REPO / "src")
    return env


def measure(py: Path, root: Path, pin: str, patch: bool) -> dict:
    """One e2e run under one pin and one setting of the patch key."""
    tag = f"{pin}-{'on' if patch else 'off'}"
    cov_json = root / f"cov-{tag}.json"
    argv = [str(py), "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly",
            SUITE, "--cov=crapkit", "--cov-branch",
            f"--cov-report=json:{cov_json}"]
    done = subprocess.run(argv, cwd=REPO, env=child_env(root, tag, patch),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    counts = admin_counts(cov_json)
    counts.update(pytest_cov=pin, patch_subprocess=patch,
                  pytest_exit=done.returncode)
    return counts


def collect(root: Path) -> dict:
    """Both pins, both settings, in one record."""
    record = {"target": TARGET, "suite": SUITE,
              "toggle": "COVERAGE_RCFILE pointing at a generated .coveragerc",
              "environments": {}, "conditions": {}}
    for pin in PINS:
        py = make_venv(root, pin)
        record["environments"][pin] = versions(py)
        for patch in (False, True):
            tag = f"pytest-cov-{pin}-patch-{'on' if patch else 'off'}"
            record["conditions"][tag] = measure(py, root, pin, patch)
            print(f"{tag}: {record['conditions'][tag]}", flush=True)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", help="keep the venvs here instead of a temp dir")
    args = ap.parse_args(argv)
    if args.workdir:
        root = Path(args.workdir)
        root.mkdir(parents=True, exist_ok=True)
        record = collect(root)
    else:
        with tempfile.TemporaryDirectory(prefix="crapkit-cov7-",
                                         ignore_cleanup_errors=True) as tmp:
            record = collect(Path(tmp))
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"wrote {OUT.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
