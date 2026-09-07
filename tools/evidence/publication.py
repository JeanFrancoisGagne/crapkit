"""Publish two historical evidence copies with named source inputs withheld.

Original archives remain outside the checkout and keep their measured bytes.
This script copies retained members unchanged and adds a separate publication
manifest. Original verification manifests describe the original archive only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = ("report.py", "cli/admin.py", "cli/verifying.py")
PLANS = (
    ("docs/architecture/2026-09-06-improvements/focused-evidence.zip",
     "e8be318b887bb846f1a6415d054e80edeb4d2adb8f903187134b085fa8a00267",
     ("configured-runtime/crapkit-0.6.0-py3-none-any.whl",
      "state-evidence/review/wheel-contract/acceptance-802257c-base.whl",
      "state-evidence/review/wheel-contract/acceptance-802257c-candidate.whl",
      "state-evidence/review/wheel-contract/crapkit-0.6.0-py3-none-any.whl",
      *(f"evidence-advisory-startup/source-{side}/src/crapkit/{name}"
        for side in ("A", "B") for name in SOURCE_FILES))),
    ("docs/architecture/2026-09-07-implementation/evidence.zip",
     "62cf4c7aca842e8e354afa0f2406b28ddfffa456620c423ef1b5164b739d8f13",
     ("measurements/duplication-self-input.json",)),
)
NOTICE = (
    "Public derivative of historical verification evidence. Some historical "
    "source/wheel inputs are withheld for repository anonymity. Retained members "
    "keep their original bytes. Original verification manifests and receipts "
    "describe the complete original archive, not this derivative. Original "
    "archives and omitted inputs remain preserved privately with unchanged hashes. "
    "This publication does not remove previously published Git history."
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def describe(name: str, data: bytes) -> dict:
    return {"path": name, "bytes": len(data), "sha256": digest(data)}


def original_members(source: Path, expected: str) -> tuple[bytes, dict]:
    raw = source.read_bytes()
    if digest(raw) != expected:
        raise ValueError("original archive differs from its recorded SHA256")
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("original archive has duplicate or damaged members")
        return raw, {name: archive.read(name) for name in names}


def publication_manifest(source: Path, raw: bytes, members: dict, omitted: tuple) -> dict:
    missing = set(omitted) - members.keys()
    if missing or len(set(omitted)) != len(omitted):
        raise ValueError("omission list differs from the original member inventory")
    return {"schema": 1, "notice": NOTICE,
            "original_archive": describe(source.name, raw),
            "original_member_count": len(members),
            "retained": [describe(name, data) for name, data in members.items() if name not in omitted],
            "omitted": [describe(name, members[name]) for name in omitted]}


def checked_entries(archive: zipfile.ZipFile, manifest: dict) -> dict:
    entries = {entry["path"]: entry for entry in manifest["retained"]}
    names = archive.namelist()
    if len(names) != len(set(names)) or set(names) != entries.keys() | {"PUBLICATION.json"}:
        raise ValueError("public archive member inventory differs from its publication manifest")
    if archive.read("PUBLICATION.json") != encoded(manifest):
        raise ValueError("embedded publication manifest differs")
    return entries


def verify_public(path: Path, manifest: dict) -> dict:
    with zipfile.ZipFile(path) as archive:
        entries = checked_entries(archive, manifest)
        for name, entry in entries.items():
            if describe(name, archive.read(name)) != entry:
                raise ValueError("retained member bytes differ: " + name)
    return {"archive": describe(path.name, path.read_bytes()), "publication": manifest}


def publish(source: Path, target: Path, omitted: tuple, expected: str) -> dict:
    if source.resolve() == target.resolve():
        raise ValueError("publication target must not replace the original")
    raw, members = original_members(source, expected)
    manifest = publication_manifest(source, raw, members, omitted)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for entry in manifest["retained"]:
            info = zipfile.ZipInfo(entry["path"], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[entry["path"]])
        archive.writestr(zipfile.ZipInfo("PUBLICATION.json", date_time=(1980, 1, 1, 0, 0, 0)), encoded(manifest))
    result = verify_public(target, manifest)
    target.with_suffix(".json").write_bytes(encoded(result))
    if source.read_bytes() != raw:
        raise ValueError("original archive changed during publication")
    return result


def check_saved(target: Path, expected: str, omitted: tuple) -> dict:
    saved = json.loads(target.with_suffix(".json").read_bytes())
    manifest = saved["publication"]
    if manifest["original_archive"]["sha256"] != expected:
        raise ValueError("publication names an unexpected original archive")
    if [entry["path"] for entry in manifest["omitted"]] != list(omitted):
        raise ValueError("publication omits a different set of inputs")
    if verify_public(target, manifest) != saved:
        raise ValueError("public archive differs from its saved receipt")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--originals", type=Path,
                        help="private directory containing the original repository-relative archive paths")
    action.add_argument("--check", action="store_true", help="check both public copies without private inputs")
    args = parser.parse_args()
    for relative, expected, omitted in PLANS:
        target = ROOT / relative.replace(".zip", "-public.zip")
        result = (check_saved(target, expected, omitted) if args.check else
                  publish(args.originals / relative, target, omitted, expected))
        print(json.dumps({"archive_path": str(target), **result["archive"],
                          "retained": len(result["publication"]["retained"]), "omitted": len(omitted)}))


if __name__ == "__main__":
    main()
