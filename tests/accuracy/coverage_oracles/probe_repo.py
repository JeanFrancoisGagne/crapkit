"""The probes scored by crapkit: one repo, one `crapkit coverage` run, read with no crapkit import.

Every (producer, scenario) recording gets a directory of its own, `pNN/`, that
holds a copy of each probe the producer measured and a scope over it. The
recordings of one format merge into one artifact with every key moved under its
slot's `pNN/`, and one lane per format copies it into place
(kit.repos.copy_command). `live` adds recordings made in this session (the push
tier's own coverage.py 7.16.1 run) beside the committed ones.

measure() builds the repo once per session under a FileLock, runs
`crapkit coverage --export scored.tsv`, and hands back the scored rows keyed
(producer, scenario, probe path, start line). Nothing here imports crapkit: the
rows are read from the export's text.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import sys

from filelock import FileLock

import hang_guard
from accuracy.kit import drive, repos, tiers

HERE = Path(__file__).resolve().parent
PROBES = HERE / "probes"
RECORDED = HERE / "recorded"
SCENARIOS = ("call", "idle")
PYTHON = ("py/shapes.py",)
VITEST = ("js/shapes.js", "mjs/shapes.mjs", "ts/shapes.ts", "tsx/shapes.tsx", "jsx/shapes.jsx",
          "vue/Shapes.vue")
JEST = ("js/shapes.js", "ts/shapes.ts")
# producer -> (parser, the probes its recording measured)
PRODUCERS = {
    "coveragepy-7.16.1": ("coveragepy", PYTHON),
    "coveragepy-7.13.0": ("coveragepy", PYTHON),
    "coveragepy-7.10.6": ("coveragepy", PYTHON),
    "pytest-cov-5.0.0": ("coveragepy", PYTHON),
    "pytest-cov-7.1.0": ("coveragepy", PYTHON),
    "vitest-v8-5.0.1": ("istanbul", VITEST),
    "vitest-istanbul-5.0.1": ("istanbul", VITEST),
    "jest-babel-30.5.2": ("istanbul", JEST),
    "jest-v8-30.5.2": ("istanbul", JEST),
    "nyc-18.0.0": ("istanbul", ("js/shapes.js",)),
    "c8-12.0.0": ("istanbul", ("mjs/shapes.mjs",)),
}
LANGUAGES = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
             ".ts": "typescript", ".tsx": "tsx", ".vue": "vue"}
HEADER = "[crapkit]\ntarget = 6\nanalysis_workers = 1\n\n[exclude]\nglobs = [\"recorded/**\"]\n"


@dataclass(frozen=True)
class Slot:
    """One (producer, scenario) recording and the directory its copies live in."""
    producer: str
    scenario: str
    prefix: str
    parser: str
    probes: tuple
    artifact: Path


def slots(live: dict[tuple[str, str], Path] | None = None) -> list[Slot]:
    """Every committed recording, then the live ones, each with its own pNN/."""
    found = [(name, scenario, parser, probes, RECORDED / name / f"{scenario}.json")
             for name, (parser, probes) in PRODUCERS.items() for scenario in SCENARIOS]
    found += [(name, scenario, "coveragepy", PYTHON, path)
              for (name, scenario), path in sorted((live or {}).items())]
    return [Slot(name, scenario, f"p{number:02d}", parser, probes, path)
            for number, (name, scenario, parser, probes, path) in enumerate(found)]


def _rekeyed(files: dict, prefix: str, istanbul: bool) -> dict:
    """The recording's file members, each keyed under the slot's directory."""
    moved = {}
    for key, data in files.items():
        path = f"{prefix}/{key.replace(chr(92), '/')}"
        moved[path] = {**data, "path": path} if istanbul else data
    return moved


def merged(all_slots: list[Slot], parser: str) -> dict:
    """One artifact holding every slot's recording of one format, keys moved
    under each slot's pNN/. Two lanes read the whole matrix, so the run starts
    two lane commands, not one per recording."""
    ours = [slot for slot in all_slots if slot.parser == parser]
    parts = [(slot, json.loads(slot.artifact.read_bytes())) for slot in ours]
    if parser == "istanbul":
        return {k: v for slot, part in parts for k, v in _rekeyed(part, slot.prefix, True).items()}
    files = {k: v for slot, part in parts for k, v in _rekeyed(part["files"], slot.prefix, False).items()}
    return {"meta": {"format": 3, "branch_coverage": True}, "files": files}


def _languages(slot: Slot) -> list[str]:
    return sorted({LANGUAGES[Path(probe).suffix] for probe in slot.probes})


def _scope(slot: Slot) -> str:
    return (f'[[scope]]\nname = "{slot.prefix}"\npaths = ["{slot.prefix}"]\n'
            f"languages = {json.dumps(_languages(slot))}\n")


LANES = (("py", "coveragepy"), ("js", "istanbul"))


def _lanes(all_slots: list[Slot]) -> str:
    tables = []
    for name, parser in LANES:
        scopes = [slot.prefix for slot in all_slots if slot.parser == parser]
        tables.append(repos.lane_toml(name, f".crapkit/cov/{name}.json", parser, scopes,
                                      f"recorded/{name}.json"))
    return "\n".join(tables)


def files(all_slots: list[Slot]) -> dict[str, bytes]:
    """{repo path: bytes} for the probe copies, the two merged recordings and crapkit.toml."""
    toml = HEADER + "\n".join(map(_scope, all_slots)) + "\n" + _lanes(all_slots)
    tree = {"crapkit.toml": toml.encode()}
    for name, parser in LANES:
        tree[f"recorded/{name}.json"] = json.dumps(merged(all_slots, parser), sort_keys=True).encode()
    for slot in all_slots:
        tree.update({f"{slot.prefix}/{probe}": (PROBES / probe).read_bytes() for probe in slot.probes})
    return tree


def build(top: Path, tree: dict[str, bytes]) -> Path:
    """One commit holding the tree, dated at the kit's epoch."""
    for path, data in tree.items():
        target = top / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    repos.git(top, "init", "-q", "-b", "main")
    for key, value in repos.CONFIG:
        repos.git(top, "config", key, value)
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "probes", date=repos.EPOCH)
    return top


@dataclass(frozen=True)
class Row:
    """One scored.tsv row, as text: the export is read, never parsed by crapkit."""
    scope: str
    path: str
    name: str
    start: int
    end: int
    ccn: int
    cov: float
    flag: str
    crap: float
    remedy: str


def read_scored(text: str) -> list[Row]:
    lines = [line for line in text.split("\n") if line.strip()]
    header = lines[0].split("\t")
    at = {column: header.index(column) for column in
          ("scope", "path", "long_name", "start", "end", "ccn", "cov", "flag", "crap", "remedy")}
    rows = []
    for line in lines[1:]:
        cells = line.split("\t")
        rows.append(Row(cells[at["scope"]], cells[at["path"]], cells[at["long_name"]],
                        int(cells[at["start"]]), int(cells[at["end"]]), int(cells[at["ccn"]]),
                        float(cells[at["cov"]]), cells[at["flag"]], float(cells[at["crap"]]),
                        cells[at["remedy"]]))
    return rows


@dataclass(frozen=True)
class ProbeRun:
    root: Path
    slots: tuple
    rows: tuple
    code: int
    stderr: str

    def slot(self, producer: str, scenario: str) -> Slot:
        return next(s for s in self.slots if (s.producer, s.scenario) == (producer, scenario))

    def scored(self, producer: str, scenario: str) -> dict[tuple[str, int], Row]:
        """{(probe path, start): row} for one recording."""
        prefix = self.slot(producer, scenario).prefix + "/"
        return {(row.path[len(prefix):], row.start): row for row in self.rows
                if row.path.startswith(prefix)}


def _digest(tree: dict[str, bytes]) -> str:
    hashed = hashlib.sha256()
    for path in sorted(tree):
        hashed.update(path.encode() + b"\0" + hashlib.sha256(tree[path]).digest())
    return hashed.hexdigest()[:16]


def _run(top: Path) -> dict:
    driver = drive.Driver(top, date_now=repos.EPOCH + 86_400)
    result = driver.run("coverage", "--export", "scored.tsv")
    scored = (top / "scored.tsv").read_bytes().decode("utf-8") if result.code == 0 else ""
    return {"code": result.code, "stderr": result.stderr, "scored": scored}


def live_coveragepy(base: Path, scenario: str) -> Path:
    """This interpreter's coverage.py (7.16.1 on push) over a copy of the Python
    probe, the way regenerate.py records it; returns the JSON report."""
    work = base / f"live-coveragepy-{scenario}"
    report = work / "report.json"
    with FileLock(str(work) + ".lock"):
        if not report.is_file():
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(PROBES / "py", work / "py")
            _coverage(work, "run", "--rcfile=py/coveragerc", "--data-file=.coverage",
                      "py/drive.py", scenario)
            _coverage(work, "json", "--rcfile=py/coveragerc", "--data-file=.coverage", "-o",
                      "report.json")
    return report


def _coverage(work: Path, *args: str) -> None:
    tiers.require_process("coverage.py")
    done = hang_guard.run([sys.executable, "-m", "coverage", *args], cwd=work, text=True,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0, f"coverage {' '.join(args)}: {done.stdout}{done.stderr}"


def measure(base: Path, live: dict[tuple[str, str], Path] | None = None) -> ProbeRun:
    """The session's one scored probe repo, built under `base` once."""
    all_slots = slots(live)
    tree = files(all_slots)
    work = base / f"probe-repo-{_digest(tree)}"
    with FileLock(str(work) + ".lock"):
        saved = work / "run.json"
        if not saved.is_file():
            shutil.rmtree(work, ignore_errors=True)
            outcome = _run(build(work / "repo", tree))
            saved.write_text(json.dumps(outcome), encoding="utf-8")
        outcome = json.loads(saved.read_text(encoding="utf-8"))
    rows = tuple(read_scored(outcome["scored"])) if outcome["code"] == 0 else ()
    return ProbeRun(work / "repo", tuple(all_slots), rows, outcome["code"], outcome["stderr"])
