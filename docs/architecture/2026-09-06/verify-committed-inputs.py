"""Verify a committed tree against a finalized passing capture.

Usage: python verify-committed-inputs.py RAW_VERIFICATION_DIRECTORY OUTPUT_DIRECTORY
Working bytes must match the completed run. Only the two named historical JSON
fixtures may differ in committed bytes, through Git's CRLF-to-LF normalization.
"""
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[3]
BASE = "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6"
JSON_FIXTURES = {"tests/fixtures/recorded/raw_mod.json", "tests/fixtures/recorded/raw_std.json"}
spec = importlib.util.spec_from_file_location("verification_capture", Path(__file__).with_name("capture-verification.py"))
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def captured_inputs(source, root=ROOT):
    binding = capture.read(source, "artifact-binding.json")
    for name, field in (("inputs-after.json", "inputs_sha256"), ("artifacts-after.json", "artifacts_sha256"),
                        ("stdout.json", "stdout_sha256"), ("result.json", "result_sha256"),
                        ("ledger-before.json", "ledger_before_sha256"), ("ledger-after.json", "ledger_after_sha256"),
                        ("command.json", "command_sha256")):
        capture.require(capture.sha256((source / name).read_bytes()) == binding[field], "capture binding differs: " + name)
    previous = capture.read(source, "result.json")
    capture.require(previous["returncode"] == 0 and not previous["changed_inputs"], "capture did not finish cleanly")
    expected = capture.read(source, "inputs-after.json")
    capture.require(bool(expected) and expected == capture.read(source, "inputs-before.json"), "captured inputs changed")
    run = capture.captured_run(source, root)
    capture.require(run["id"] == binding["run_id"] and run["commit_sha"] == binding["commit"], "capture run binding differs")
    artifacts = capture.read(source, "artifacts-after.json")
    capture.require(capture.artifact_evidence(run, root) == artifacts, "configured artifact paths or bytes differ")
    return expected, artifacts, binding


def archive_names(tree):
    return {member.name for member in tree.getmembers() if member.isfile()
            and not member.name.startswith("docs/architecture/")}


def committed_inputs(expected, commit, root=ROOT):
    capture.require(capture.input_hashes(root) == expected, "working input path set or bytes differ")
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=root)
    normalized = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        capture.require(archive_names(tree) == set(expected), "committed input path set differs from the completed run")
        for name, wanted in expected.items():
            committed = tree.extractfile(name).read()
            actual = capture.sha256(committed)
            if actual != wanted:
                working = (root / name).read_bytes()
                capture.require(name in JSON_FIXTURES and working.replace(b"\r\n", b"\n") == committed,
                                "committed bytes differ: " + name)
                normalized.append({"path": name, "working_sha256": wanted,
                                   "committed_sha256": actual, "change": "Git CRLF to LF normalization"})
    return normalized


def verify(source, out):
    expected, artifacts, binding = captured_inputs(source)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    normalized = committed_inputs(expected, commit)
    argv = [sys.executable, "-m", "crapkit", "verify", "--base", BASE,
            "--reuse-artifacts", "--no-tighten", "--json"]
    proof = {"commit": commit, "matched_files": len(expected),
             "exact_committed_files": len(expected) - len(normalized), "normalized_json_fixtures": normalized,
             "unchanged_artifacts": artifacts, "capture_binding": binding,
             "argv": argv, "started_at": datetime.now(timezone.utc).isoformat(),
             "meaning": "Complete working and committed input path sets match the passing capture, except the listed JSON line endings. "
                        "Coverage matches its recorded run digest; JUnit bytes were first hashed at the capture binding timestamp."}
    capture.write(out, "input-proof.json", proof)
    started = time.monotonic()
    with (out / "stdout.json").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        result = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr,
                                env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8"))
    capture.require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == commit,
                    "HEAD changed during reuse")
    capture.require(committed_inputs(expected, commit) == normalized, "inputs changed during reuse")
    capture.require(captured_inputs(source)[1] == artifacts, "artifacts changed during reuse")
    if result.returncode == 0:
        reused = capture.completed_run(out)
        capture.require(capture.artifact_evidence(reused, executed=False) == artifacts, "reuse consumed different artifacts")
    summary = {"returncode": result.returncode, "seconds": time.monotonic() - started,
               "commit": commit, "matched_files": len(expected), "inputs_and_artifacts_unchanged": True}
    capture.write(out, "result.json", summary)
    print(json.dumps(summary, indent=2))
    return result.returncode


def main():
    source = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    capture.require(source != out, "capture and post-commit output directories must differ")
    out.mkdir(parents=True, exist_ok=True)
    return verify(source, out)


if __name__ == "__main__":
    raise SystemExit(main())
