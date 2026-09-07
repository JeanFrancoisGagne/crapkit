"""Validate completed checks and package the report's reproducible evidence."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def tests(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    failures = [f"{case.get('classname')}::{case.get('name')}" for case in cases
                if case.find("failure") is not None or case.find("error") is not None]
    skipped = sum(case.find("skipped") is not None for case in cases)
    return dict(tests=len(cases), passed=len(cases)-len(failures)-skipped,
                skipped=skipped, failures=failures)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_installed_source(wheels, repository):
    state = subprocess.run(["git", "-C", str(repository), "status", "--porcelain=v1", "-z",
                            "--untracked-files=all", "--", "src/crapkit"],
                           capture_output=True, check=True).stdout
    assert not state, "working source is not clean"
    revision = wheels["candidate"]["commit"]
    subprocess.run(["git", "-C", str(repository), "diff", "--exit-code", "--no-ext-diff",
                    "--no-textconv", revision, "--", "src/crapkit"], check=True)
    for path, checksum in wheels["candidate"]["source_sha256"].items():
        blob = subprocess.run(["git", "-C", str(repository), "show",
                               f"{revision}:src/crapkit/{path}"], capture_output=True, check=True).stdout
        assert hashlib.sha256(blob).hexdigest() == checksum, path


def validate(evidence, repository):
    source = read_json(evidence / "source-final/verdict.json")
    result = read_json(evidence / "source-final/result.json")
    wheels = read_json(evidence / "wheels-final/verdict.json")
    assert source["ok"] and result["exit"] == 0, "source verification has not passed"
    assert wheels["phase"] == "complete" and wheels["verdict"]["ok"], "wheel verification has not passed"
    assert wheels["suite_exits"][1] == 0, "candidate wheel suite failed"
    assert source["commit"] == wheels["candidate"]["commit"] == result["commit"]
    validate_installed_source(wheels, repository)
    assert source["ratchet_sha256"] == "a30319ff0282306906d8da2df982084025f9e647d999b9e158697778214b03ac"
    with sqlite3.connect(repository / ".crapkit/crap.sqlite") as connection:
        row = connection.execute("SELECT commit_sha, verdict_ok, findings FROM runs WHERE id=?",
                                 (source["run_id"],)).fetchone()
    assert row == (source["commit"], 1, 0), "source ledger disagrees with verdict"
    source_tests = {name: tests(evidence / "source-final" / f"{name}.xml") for name in ("unit", "e2e")}
    assert all(not suite["failures"] for suite in source_tests.values())
    return dict(production_commit=source["commit"], source_result=result, source_verdict=source,
                source_tests=source_tests, wheel_verdict=wheels["verdict"],
                installed_packages={name: wheels[name] for name in ("base", "candidate")},
                ratchet_unchanged=True)


def source_path(evidence, repository, name):
    return repository / name[5:] if name.startswith("repo:") else evidence / name


def validate_completed_rows(matrix):
    assert len(matrix["candidates"]) == matrix["audited_candidate_count"] == 17
    assert len(matrix["extra_work"]) == matrix["extra_row_count"] == 1
    rows = matrix["candidates"] + matrix["extra_work"]
    assert len({row["id"] for row in rows}) == len(rows), "duplicate completion row"
    assert all(row["implemented"] is True for row in rows), "implementation is incomplete"
    assert all(row["complete_suite_status"] == "passed" for row in rows), "complete suites are pending"
    passed = dict(linux_installed_wheels="passed", windows_source="passed")
    assert all(row["complete_suite_platforms"] == passed for row in rows), "platform verification is incomplete"


def validate_completion(evidence, manifest, verification):
    matrix = read_json(evidence / "completion-matrix.json")
    measured = verification["source_verdict"]["commit"]
    final = matrix["integration_verification"]["final_artifacts"]
    assert manifest["implementation_commit"] == matrix["implementation_commit"] == measured, "evidence commit differs"
    assert matrix["final_verified_commit"] == final["commit"] == measured, "final verified commit differs"
    assert final["status"] == "complete", "final verification is pending"
    assert final["windows_source_status"] == final["linux_wheels_status"] == "passed"
    assert matrix["integration_verification"]["windows_full_source_verify"]["status"] == "passed"
    assert matrix["integration_verification"]["linux_installed_wheel_base_candidate"]["status"] == "passed"
    validate_completed_rows(matrix)


def validate_companions(manifest):
    paths = {entry["path"] for entry in manifest["entries"]}
    assert {"report-draft.md", "completion-matrix.json", "decisions.tsv"} <= paths, "required companion missing"
    assert "evidence-manifest.json" not in paths, "manifest must not hash itself"


def package(evidence, repository, output, verification):
    manifest = read_json(evidence / "evidence-manifest.json")
    validate_completion(evidence, manifest, verification)
    validate_companions(manifest)
    output.mkdir(parents=True, exist_ok=True)
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(output / "evidence.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for entry in manifest["entries"]:
            path = source_path(evidence, repository, entry["path"])
            assert digest(path) == entry["sha256"], f"evidence changed: {entry['path']}"
            archive.write(path, entry["path"].replace("repo:", "repository/"))
        archive.write(output / "verification.json", "verification.json")
        archive.write(evidence / "evidence-manifest.json", "evidence-manifest.json")
    print(json.dumps(dict(archive=str(output / "evidence.zip"), bytes=(output / "evidence.zip").stat().st_size,
                         sha256=digest(output / "evidence.zip"), evidence_files=len(manifest["entries"]),
                         archived_files=len(manifest["entries"]) + 2)))


if __name__ == "__main__":
    evidence, repository, output = map(Path, sys.argv[1:])
    package(evidence, repository, output, validate(evidence, repository))
