"""Index final report companions only after completion metadata passes admission."""
import hashlib
import json
from pathlib import Path
import sys

from finalize_evidence import read_json, source_path, validate_completion

COMPANIONS = ("report-draft.md", "completion-matrix.json", "decisions.tsv")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_preserved_entries(evidence, repository, entries):
    for entry in entries:
        if entry["path"] not in COMPANIONS:
            path = source_path(evidence, repository, entry["path"])
            assert digest(path) == entry["sha256"], f"evidence changed: {entry['path']}"


def add_companions(evidence, repository):
    manifest = read_json(evidence / "evidence-manifest.json")
    matrix = read_json(evidence / "completion-matrix.json")
    validate_completion(evidence, manifest, {"source_verdict": {"commit": matrix["implementation_commit"]}})
    check_preserved_entries(evidence, repository, manifest["entries"])
    entries = [entry for entry in manifest["entries"] if entry["path"] not in COMPANIONS]
    for name in COMPANIONS:
        path = evidence / name
        entries.append(dict(path=name, candidates=["ALL"], kind="final report companion",
                            purpose="Source-of-truth report, completion state or decision chronology",
                            bytes=path.stat().st_size, sha256=digest(path)))
    manifest["entries"] = sorted(entries, key=lambda entry: entry["path"])
    manifest["curation"].update(file_count=len(entries), total_bytes=sum(entry["bytes"] for entry in entries))
    manifest["companion_status"] = "Final report, matrix and decision log indexed. The manifest is archived separately without hashing itself."
    (evidence / "evidence-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dict(measured_commit=matrix["implementation_commit"], companions=list(COMPANIONS), entries=len(entries))


if __name__ == "__main__":
    roots = tuple(map(Path, sys.argv[1:])) or (Path(__file__).resolve().parent, Path("C:/Users/jfgag/crapkit"))
    print(json.dumps(add_companions(*roots)))
