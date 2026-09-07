"""Archive the implementation's checks without copying its temporary checkouts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


GROUPS = ("evidence-analysis-rerun", "evidence-execution-rerun", "state-evidence",
          "evidence-release-rerun", "evidence-advisory-startup", "contamination",
          "final-ci", "final-ci-complete", "contributing-doc-fix", "report-evidence",
          "self-refused", "configured-runtime")
DETAILS = ("state-evidence/review", "state-evidence/review/wheel-contract",
           "evidence-advisory-startup/path-control", "evidence-execution-rerun/final-cancellation")
COMPARED_INPUTS = ("evidence-advisory-startup/source-A/src", "evidence-advisory-startup/source-B/src")
CI_ARTIFACTS = ("*", "base/*", "candidate/*", "base/cov/*", "candidate/cov/*",
               "base/cov/incomplete/suites-*/*", "candidate/cov/incomplete/suites-*/*")
SELF_FINAL = ("*.py", "*.md", "*.json", "*.log", "*.txt", "*.sqlite*", "*.db", "*.xml", "*.coverage")
EXCLUDED = {"self-final/admit_ci_artifacts.py", "self-final/test_artifact_import.py",
            "self-final/IMPORTER-DRAFT.md"}
PATTERNS = (
    *(f"{group}/*" for group in (*GROUPS, *DETAILS)),
    *(f"{source}/**/*.py" for source in COMPARED_INPUTS),
    *(f"{group}/attempt-*/{part}" for group in ("final-ci", "final-ci-complete") for part in CI_ARTIFACTS),
    *(f"self-final/{part}" for part in SELF_FINAL),
)
ROOT_FILES = (
    "baseline-unit.log", "root-unit.log", "config-red.log", "config-focused.log",
    "config-focused-2.log", "schedule-red.log", "schedule-green.log",
    "ci-red.log", "ci-workflow-red.log", "ci-installed-test.log", "guidance-red.log",
    "reuse-docs.log", "contributing-ci-docs.log", "integrated-gate.log", "schedule-benchmark.json",
    "schedule-benchmark-0.xml", "schedule-benchmark-8.xml", "benchmark-schedule.py",
    "schedule-benchmark-0.log", "schedule-benchmark-8.log", "check-wheel.py",
    "wheel-install-proof.json", "wheel-install.log",
    "cache-fingerprint-integrated.log", "timing-instrumented.log", "timing-strict.log",
    "self-first-result.json", "self-first.stderr.log", "self-first-lane.log",
    "self-first-junit.xml", "self-first-input-match.json", "self-coverage-inputs.json",
    "release-dry-run.log", "final-ci.log", "final-ci-complete.log", "final-gate.log",
    "pytest-config-red.log", "final-fixture-repairs.log", "contributing-doc-checks.log",
    "cache-fingerprint-green.log", "suite-repairs.log",
    "summarize-ci.py", "run-final-verification.ps1",
)


def sources(work: Path):
    for pattern in (*PATTERNS, *ROOT_FILES):
        for path in sorted(work.glob(pattern)):
            if path.is_file() and path.relative_to(work).as_posix() not in EXCLUDED:
                yield path


def archive(work: Path, output: Path) -> None:
    manifest = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sources(work):
            relative = path.relative_to(work).as_posix()
            data = path.read_bytes()
            manifest.append({"path": relative, "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()})
            bundle.writestr(relative, data)
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Archived {len(manifest)} files in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("focused-evidence.zip"))
    args = parser.parse_args()
    archive(args.work.resolve(), args.output)


if __name__ == "__main__":
    main()
