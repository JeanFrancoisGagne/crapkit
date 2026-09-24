"""One measured copy of a corpus per test session, shared by every test that reads it.

measure() builds the corpus into a git repo, exports its inventory, runs
`crapkit coverage` once over its recorded artifacts, then runs each read-only
command in SURFACES and writes what it printed to a file of its own. It works
under a FileLock and leaves a manifest behind, so pytest-xdist workers that ask
for the same corpus wait for the first one and then read its files. A test that
changes state (seeds marks, edits a file, runs verify) takes private_copy()
instead of touching the shared one.

The clock is frozen at the epoch the corpus names: corpus.toml's
`[small] git_test_date_now`, or one day after the kit's commit date when the
corpus has no corpus.toml. Every command runs spawned, because a corpus past 32
files reaches crapkit's analysis pool, which the in-process runner refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tomllib

from filelock import FileLock

from . import drive, repos

ACCURACY = Path(__file__).resolve().parents[1]
SMALL = ACCURACY / "corpus_goldens" / "small"
CORPUS_TOML = ACCURACY / "corpus_goldens" / "corpus.toml"
SEED = ACCURACY / "kit" / "fixtures" / "seed"
DEFAULT_NOW = repos.EPOCH + 86_400
MANIFEST = "manifest.json"

# The two commands that write a run, first: the inventory export, then the one
# coverage run. Then (file name, argv) for each read-only surface; `report` and
# `trend` fill rollups, which change no answer.
WRITES = (
    ("inventory.txt", ("inventory", "--export", "{out}/inventory.tsv")),
    ("coverage.json", ("coverage", "--json", "--export", "{out}/scored.tsv",
                       "--sarif", "{out}/coverage.sarif")),
)
SURFACES = (
    ("worklist.json", ("worklist", "--json")),
    ("worklist.txt", ("worklist",)),
    ("next-item.json", ("next-item",)),
    ("trend.json", ("trend", "--json")),
    ("runs.json", ("runs", "list", "--json")),
    ("report.txt", ("report", "--out", "{out}/report.html")),
    ("duplication.json", ("duplication", "--json")),
    ("doctor.json", ("doctor", "--json")),
)


def date_now(corpus_toml: Path = CORPUS_TOML) -> int:
    """The GIT_TEST_DATE_NOW epoch a corpus run freezes git's clock at."""
    if not corpus_toml.is_file():
        return DEFAULT_NOW
    table = tomllib.loads(corpus_toml.read_text(encoding="utf-8")).get("small", {})
    return int(table.get("git_test_date_now", DEFAULT_NOW))


def digest(corpus: Path) -> str:
    """A key over every byte and path of the corpus."""
    hashed = hashlib.sha256()
    for path, data in repos.tree(corpus).items():
        hashed.update(path.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return hashed.hexdigest()[:16]


@dataclass(frozen=True)
class CorpusRun:
    root: Path
    outputs: Path
    date_now: int
    codes: dict

    def output(self, name: str) -> str:
        return (self.outputs / name).read_text(encoding="utf-8")

    def private_copy(self, dest: Path) -> Path:
        """A copy of the measured repo, store included, for a test that changes it."""
        shutil.copytree(self.root, dest, symlinks=True)
        return dest


def _record(driver: drive.Driver, outputs: Path, name: str, argv: tuple) -> int:
    result = driver.run(*[part.replace("{out}", outputs.as_posix()) for part in argv])
    (outputs / name).write_text(result.stdout, encoding="utf-8")
    (outputs / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    return result.code


def _writes(driver: drive.Driver, outputs: Path) -> dict:
    """The inventory export and the one coverage run; either failing stops here."""
    codes = {}
    for name, argv in WRITES:
        codes[name] = _record(driver, outputs, name, argv)
        if codes[name] != 0:
            stderr = (outputs / f"{name}.stderr").read_text(encoding="utf-8")
            raise AssertionError(f"crapkit {' '.join(argv)} exited {codes[name]}: {stderr}")
    return codes


def _measure_once(corpus: Path, work: Path, now: int) -> dict:
    built = repos.build(repos.tree_spec(corpus), work / "repo")
    outputs = work / "outputs"
    outputs.mkdir()
    driver = drive.Driver(built.root, date_now=now, spawn=True)
    codes = _writes(driver, outputs)
    codes.update({name: _record(driver, outputs, name, argv) for name, argv in SURFACES})
    manifest = {"root": str(built.root), "outputs": str(outputs), "date_now": now,
                "codes": codes}
    (work / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def _load(work: Path, corpus: Path, now: int) -> dict:
    manifest_path = work / MANIFEST
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    shutil.rmtree(work, ignore_errors=True)
    return _measure_once(corpus, work, now)


def measure(corpus: Path, base: Path, now: int | None = None) -> CorpusRun:
    """The session's one measured copy of `corpus`, built under `base` once."""
    clock = DEFAULT_NOW if now is None else now
    work = base / f"corpus-{digest(corpus)}-{clock}"
    work.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work) + ".lock"):
        manifest = _load(work, corpus, clock)
    return CorpusRun(Path(manifest["root"]), Path(manifest["outputs"]), manifest["date_now"],
                     manifest["codes"])
