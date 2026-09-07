"""Check the exported archive against its own manifest and companion files."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def check(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("evidence-manifest.json"))
        verification = json.loads(archive.read("verification.json"))
        matrix = json.loads(archive.read("completion-matrix.json"))
        names = archive.namelist()
        assert len(names) == len(set(names)) == len(manifest["entries"]) + 2
        for entry in manifest["entries"]:
            name = entry["path"].replace("repo:", "repository/")
            assert hashlib.sha256(archive.read(name)).hexdigest() == entry["sha256"], name
        commit = verification["source_verdict"]["commit"]
        assert matrix["final_verified_commit"] == manifest["implementation_commit"] == commit
        assert verification["source_verdict"]["ok"] and verification["wheel_verdict"]["ok"]
        return dict(commit=commit, archive=str(path), files=len(names),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), verified=True)


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1])), indent=2))
