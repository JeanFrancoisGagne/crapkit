"""Copy the final review and a reproducible evidence bundle to an output directory."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
PREFIX = "crapkit-architecture-final-2026-09-06"
MANIFEST = HERE / "bundle-manifest.json"
paths = sorted(path for path in HERE.rglob("*")
               if path.is_file() and path != MANIFEST and "__pycache__" not in path.parts)
records = [{"path": path.relative_to(HERE).as_posix(), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
validation = json.loads((HERE / "validation.json").read_text(encoding="utf-8"))
reference = json.loads((HERE / "final-candidates.json").read_text(encoding="utf-8"))["code_ref"]
MANIFEST.write_text(json.dumps({"code_ref": reference, "verified": validation["complete"],
                               "files": records}, indent=2), encoding="utf-8")
for source, suffix in (("final-review.html", ".html"), ("final-candidates.json", ".json"),
                       ("completion.tsv", "-completion.tsv")):
    shutil.copy2(HERE / source, OUT / (PREFIX + suffix))
bundle = OUT / (PREFIX + "-evidence.zip")
with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in [*paths, MANIFEST]:
        archive.write(path, arcname=path.relative_to(HERE).as_posix())
with zipfile.ZipFile(bundle) as archive:
    assert archive.testzip() is None
    for record in records:
        assert hashlib.sha256(archive.read(record["path"])).hexdigest() == record["sha256"]
print(json.dumps({"directory": str(OUT), "bundle": bundle.name,
                  "files": len(records) + 1, "verified": validation["complete"]}))
