"""Import completed review evidence: python collect-evidence.py REVIEW_WORK_DIRECTORY.

Raw evidence stays in the supplied directory. Public copies normalize local paths;
the index records hashes of both forms. Existing replay sources remain untouched.
"""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORK = Path(sys.argv[1]).resolve()
OUT = HERE / "evidence"
FILES = {
    "": ["verify-first.log", "unit-identity-first.log", "e2e-identity-first.log",
         "unit-final.log", "e2e-final.log", "suite-repairs.log", "reader-report-integration.log",
         "migration-integrated-final.log", "unit-final-admission.log", "e2e-final-admission.log",
         "final-fixture-repairs.log", "unit-verified.log", "e2e-verified.log",
         "staged-hook-final.log"],
    "evidence-analysis": ["final-admission-red.txt", "final-admission-green.txt",
         "final-admission-complexity.txt", "final-reassessment-source.tsv",
         "final-root-focused.txt", "final-root-mutation.txt",
         "identity-followup-red.txt", "identity-followup-green.txt",
         "identity-followup-check.txt", "identity-measurements-interleaved.txt"],
    "evidence-execution": ["reader-identity-e2e.txt", "final-explain-identity.txt",
         "multiline-migration-probe.txt", "reader-migration-red.txt",
         "reader-snapshot-red.txt", "reader-stamp-red.txt", "reader-override-red.txt",
         "reader-migration-focused-green.txt", "reader-migration-claim-final.txt",
         "reader-migration-e2e.txt", "reader-migration-e2e-rerun.txt",
         "reader-migration-static.txt", "reader-migration-hook.txt"],
    "final-review": ["arrow-upstream-final.json", "arrow-upstream-final.txt",
         "prune-retention-hashes.json", "claim-prune-contract.txt"],
    "verification-final": ["command.json", "inputs-before.json", "inputs-after.json",
         "ledger-before.json", "ledger-after.json", "stdout.json", "stderr.log",
         "lane-py.log", "result.json", "artifacts-after.json", "artifact-binding.json", "function-coverage.json"],
}
FILES["verification-pass"] = FILES["verification-final"]
FILES["verification-final/artifacts"] = ["py.json", "junit.xml"]
FILES["verification-pass/artifacts"] = ["py.json", "junit.xml"]
FILES["commit-verification"] = ["input-proof.json", "stdout.json", "stderr.log", "result.json", "ledger-after.json"]
FILES[""] += ["strict-latency.log", "staged-hook-cache-final.log", "contributing-doc-checks.log"]
FILES["contributing-doc-fix"] = ["CONTRIBUTING.patch", "notes.md"]
FILES["cache-admission-coverage"] = [
    "README.md", "before.txt", "after.txt", "function-coverage.json",
    "coverage-before.json", "coverage-after.json", "test-before.py",
    "protected-before.json", "protected-after.json",
]
FILES["verification-proof-audit"] = [
    "README.md", "probe.py", "probe-before.txt", "probe.txt", "probe-result.json",
]
FILES["evidence-execution"] += [
    "advisory-latency-review.md", "advisory-latency.json", "advisory-latency.log",
    "advisory-imports.json", "advisory-imports.log", "measure-advisory-latency.py",
    "probe-advisory-imports.py",
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def portable(data):
    text = data.decode("utf-8-sig", errors="replace")
    mappings = [(WORK / name, f"<worktree:{name}>") for name in
                ("store", "analysis", "execution", "review-root", "identity-analysis", "identity-state")]
    mappings += [(WORK, "<review-work>"), (ROOT, "<repo>"),
                 (Path(sys.prefix), "<python-install>"), (Path.home(), "<user>")]
    for path, replacement in mappings:
        for spelling in (str(path), path.as_posix()):
            for escaped in (spelling.replace("\\", "\\\\\\\\"), spelling.replace("\\", "\\\\"), spelling):
                text = text.replace(escaped, replacement)
    return text.encode("utf-8")


OUT.mkdir(parents=True, exist_ok=True)
index_path = OUT / "index.json"
previous = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
records = {entry["path"]: entry for entry in previous}
for group, names in FILES.items():
    for name in names:
        source = WORK / group / name
        if not source.is_file():
            continue
        raw = source.read_bytes()
        target = OUT / group / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(portable(raw))
        key = target.relative_to(OUT).as_posix()
        records[key] = {"path": key, "raw_source": key, "raw_sha256": digest(raw)}

for path in sorted(OUT.rglob("*")):
    if not path.is_file() or path == index_path:
        continue
    key = path.relative_to(OUT).as_posix()
    record = records.setdefault(key, {"path": key})
    data = path.read_bytes()
    normalized = portable(data) if path.suffix in {".log", ".txt", ".json", ".tsv", ".patch"} else data
    if normalized != data:
        record.setdefault("raw_sha256", digest(data))
        path.write_bytes(normalized)
        data = normalized
    record.update(bytes=len(data), sha256=digest(data))
index_path.write_text(json.dumps(list(records.values()), indent=2), encoding="utf-8")
print(f"Indexed {len(records)} public evidence files; raw evidence retained in the input directory.")
