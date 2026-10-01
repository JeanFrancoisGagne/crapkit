"""tools/deploy/run.py labels each image with a hash of what its build read.

run.py skips a build when the image's label matches the fingerprint it
computes for the context. The context files went into that hash in the order
of a Path sort, and WindowsPath sorts case-folded: the real context put
agent-sdk-requirements.txt before Dockerfile on Windows and after it on
Linux. One context had one fingerprint per OS, and Docker Desktop serves
Windows and its WSL distros from one daemon, so an image labeled from one OS
was built again when run.py ran from the other.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import pins as pinsfile  # noqa: E402
import run  # noqa: E402

PINS = pinsfile.load()
CONTEXT = "tests/deploy/docker/"
IGNORE = "*\n!tests/deploy/docker/\n"
# WindowsPath folds case, so agents.txt sorted before Dockerfile there; PosixPath
# compares part by part, so kit/ sorted before kit-notes.md.
FILES = {"Dockerfile.dockerignore": IGNORE, "Dockerfile": "FROM scratch\n", "agents.txt": "a\n",
         "kit/entry.sh": "exit 0\n", "kit-notes.md": "notes\n"}


def _context(root: Path) -> None:
    for name, text in FILES.items():
        path = root / CONTEXT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (root / "outside.py").write_bytes(b"x = 1\n")


def _fingerprint_by_name(image: str) -> str:
    """The fingerprint computed here, its files in str order of their names."""
    files = [[CONTEXT + name, hashlib.sha256(FILES[name].encode("utf-8")).hexdigest()]
             for name in sorted(FILES, key=lambda name: CONTEXT + name)]
    inputs = {"image": image, "args": run.build_args(PINS, image), "buildkit": PINS["images"]["buildkit"],
              "platform": pinsfile.platform(PINS, image), "files": files}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()


def test_the_inputs_fingerprint_orders_the_context_by_posix_name(tmp_path):
    _context(tmp_path)

    assert run.inputs_fingerprint(PINS, "core", tmp_path) == _fingerprint_by_name("core")
