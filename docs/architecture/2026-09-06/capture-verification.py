"""Capture configured verification, or finalize an already completed capture.

Usage: python capture-verification.py [OUTPUT_DIRECTORY]
       python capture-verification.py --finalize OUTPUT_DIRECTORY
Finalization reads existing artifacts. It never runs tests or verification.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[3]
BASE = "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def write(out, name, value):
    (out / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


def input_hashes(root=ROOT):
    listing = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    result = {}
    for name in sorted(set(listing.decode("utf-8").split("\0"))):
        path = root / name
        if path.is_file() and not name.startswith("docs/architecture/"):
            result[name] = sha256(path.read_bytes())
    return result


def ledger(root=ROOT):
    database = root / ".crapkit/crap.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT id,commit_sha,tool_versions,kind,verdict_ok,findings,created_at "
            "FROM runs ORDER BY id DESC LIMIT 4")]


def completed_run(out, root=ROOT):
    receipt = read(out, "stdout.json")
    require(receipt["ok"] and not receipt["overridden"], "verify did not pass without overrides")
    database = root / ".crapkit/crap.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM runs WHERE id=?", (receipt["run_id"],)).fetchone()
    require(row is not None, "the reported run is absent from the store")
    run = dict(row)
    require(run["kind"] == "verify" and run["verdict_ok"] == 1 and run["findings"] == 0,
            "the reported run is not a passing verify")
    require(run["commit_sha"] == receipt["commit"], "receipt and stored commit disagree")
    lanes = run["lanes"]
    run["lanes"] = json.loads(zlib.decompress(lanes) if isinstance(lanes, bytes) else lanes)
    return run


def captured_run(out, root=ROOT):
    run = completed_run(out, root)
    before = {row["id"] for row in read(out, "ledger-before.json")}
    after = {row["id"]: row for row in read(out, "ledger-after.json")}
    require(run["id"] not in before and run["id"] in after, "run does not belong to this capture attempt")
    saved = after[run["id"]]
    require(all(saved[key] == run[key] for key in saved), "captured and current run ledger disagree")
    return run


def configured_lanes(root=ROOT):
    with (root / "crapkit.toml").open("rb") as stream:
        lanes = tomllib.load(stream)["lane"]
    require(bool(lanes), "configured verification must declare artifacts")
    return lanes


def junit_counts(data):
    tree = ET.fromstring(data)
    require(not list(tree.iter("failure")) and not list(tree.iter("error")),
            "JUnit contains failures or errors")
    cases = list(tree.iter("testcase"))
    require(bool(cases), "JUnit contains no testcases")
    for suite in tree.iter("testsuite"):
        if "tests" in suite.attrib:
            require(int(suite.attrib["tests"]) == len(list(suite.iter("testcase"))),
                    "JUnit declared count differs from its testcases")
    return {"tests": len(cases), "skipped": sum(case.find("skipped") is not None for case in cases)}


def artifact_evidence(run, root=ROOT, *, executed=True):
    lanes = configured_lanes(root)
    require({lane["name"] for lane in lanes} == set(run["lanes"]), "configured and measured lanes differ")
    records = []
    for lane in lanes:
        measured = run["lanes"][lane["name"]]
        expected_exit = 0 if executed else None
        require(measured["exit_code"] == expected_exit, "lane execution mode or exit code differs")
        records.extend(lane_artifacts(root, lane, measured))
    return records


def lane_artifacts(root, lane, measured):
    path = lane["artifact"]
    digest = sha256((root / path).read_bytes())
    require(digest == measured["artifact_sha256"], "coverage differs from the bytes consumed by verify")
    records = [{"path": path, "sha256": digest, "lane": lane["name"], "kind": "coverage"}]
    if lane.get("results_artifact"):
        path = lane["results_artifact"]
        data = (root / path).read_bytes()
        counts = junit_counts(data)
        require(not measured["failures"], "the recorded lane has failing tests")
        require(counts == {"tests": measured["tests_total"], "skipped": measured["tests_skipped"]},
                "JUnit counts differ from the completed run")
        records.append({"path": path, "sha256": sha256(data), "lane": lane["name"],
                        "kind": "junit", "counts": counts})
    return records


def finalize(out, root=ROOT, *, mode="late-finalization"):
    result = read(out, "result.json")
    require(result["returncode"] == 0 and not result["changed_inputs"], "capture did not finish cleanly")
    expected = read(out, "inputs-after.json")
    require(bool(expected) and expected == read(out, "inputs-before.json"), "captured inputs changed")
    require(input_hashes(root) == expected, "current input paths or bytes differ from the completed run")
    run = captured_run(out, root)
    records = artifact_evidence(run, root)
    require(input_hashes(root) == expected, "inputs changed during finalization")
    require(artifact_evidence(run, root) == records, "artifacts changed during finalization")
    write(out, "artifacts-after.json", records)
    binding = {"run_id": run["id"], "commit": run["commit_sha"], "mode": mode,
               "finished_at": result["finished_at"], "captured_at": datetime.now(timezone.utc).isoformat(),
               "inputs_sha256": sha256((out / "inputs-after.json").read_bytes()),
               "artifacts_sha256": sha256((out / "artifacts-after.json").read_bytes()),
               "stdout_sha256": sha256((out / "stdout.json").read_bytes()),
               "result_sha256": sha256((out / "result.json").read_bytes()),
               "ledger_before_sha256": sha256((out / "ledger-before.json").read_bytes()),
               "ledger_after_sha256": sha256((out / "ledger-after.json").read_bytes()),
               "command_sha256": sha256((out / "command.json").read_bytes()),
               "coverage_binding": "Exact digest recorded by the completed verify run.",
               "junit_binding": "Bytes first hashed at captured_at; complete zero-failure counts match that run. "
                                "The store does not record the original JUnit digest."}
    write(out, "artifact-binding.json", binding)
    return binding


def changed_inputs(before, after):
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def capture(out):
    argv = [sys.executable, "-m", "crapkit", "verify", "--base", BASE, "--no-tighten", "--json"]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    before = input_hashes()
    write(out, "inputs-before.json", before)
    write(out, "ledger-before.json", ledger())
    write(out, "command.json", {"argv": argv, "cwd": str(ROOT),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "versions": {name: version(name) for name in ("lizard", "pytest", "coverage", "pytest-cov")},
        "python": sys.version, "platform": sys.platform})
    start = time.monotonic()
    with (out / "stdout.json").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
    after = input_hashes()
    changed = changed_inputs(before, after)
    write(out, "inputs-after.json", after)
    write(out, "ledger-after.json", ledger())
    shutil.copy2(ROOT / ".crapkit/lane-py.log", out / "lane-py.log")
    summary = {"returncode": result.returncode, "seconds": time.monotonic() - start,
               "finished_at": datetime.now(timezone.utc).isoformat(), "changed_inputs": changed}
    write(out, "result.json", summary)
    print(json.dumps(summary, indent=2))
    if result.returncode == 0 and not changed:
        finalize(out, mode="run-completion")
    return result.returncode or bool(changed)


def main():
    finalizing = sys.argv[1:2] == ["--finalize"]
    arguments = sys.argv[2:] if finalizing else sys.argv[1:]
    out = Path(arguments[0]).resolve() if arguments else Path(__file__).parent / "verification"
    out.mkdir(parents=True, exist_ok=True)
    if finalizing:
        print(json.dumps(finalize(out), indent=2))
        return 0
    return capture(out)


if __name__ == "__main__":
    raise SystemExit(main())
