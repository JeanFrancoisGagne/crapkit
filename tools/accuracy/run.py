"""Run a tier of the accuracy suite, or one shard of it, and write its receipt.

    python tools/accuracy/run.py [--tier push|nightly|weekly|release] [--shard NAME]
                                 [--os-sensitive] [-n WORKERS] [--receipt PATH]
                                 [--checks DIR]
    python tools/accuracy/run.py merge RECEIPT... --out PATH
    python tools/accuracy/run.py image-tag
    python tools/accuracy/run.py kit-goldens --declare ID --kind KIND --reason TEXT
    python tools/accuracy/run.py doc-range PATH START END
    python tools/accuracy/run.py xplat RECEIPT...
    python tools/accuracy/run.py events --min N RECEIPT...
    python tools/accuracy/run.py oracle-versions [--tier push|nightly]

A check is one row of CHECKS in tools/accuracy/checks/<key>.py, the module a
packet owns:

    SHARD = "analysis"      # the nightly shard that runs this module's checks
    CHECKS = [
        {"name": "hand probes", "seconds": 5,
         "pytest": ["tests/accuracy/analysis_oracles/test_hand_probes.py"]},
        {"name": "PowerShell AST", "seconds": 8, "os": ["win32"],
         "pytest": ["tests/accuracy/analysis_oracles/test_complexity_oracles.py"]},
        {"name": "decode matrix", "seconds": 2, "os_sensitive": True,
         "pytest": ["tests/accuracy/analysis_oracles/test_decode_matrix.py"]},
        {"name": "wheel diff", "seconds": 60, "tiers": ["release"],
         "argv": ["python", "tools/accuracy/wheel_diff.py", "--corpus", "small"]},
    ]

`seconds` is the check's declared serial time on ubuntu in the push tier.
`os_sensitive: True` marks a check whose answer can change with the OS (byte
decoding, path spellings, argv splitting, a shell); `--os-sensitive` runs only
those, and the checks whose `os` names this platform, which is what CI's
Windows push job runs, since the rest answer the same on every OS and the
Ubuntu job already ran them. A
pytest check names test files or directories, and the tier's markers pick what
runs inside them (tests/accuracy/kit/tiers.py). One pytest session runs every
selected pytest check, at -n WORKERS when given, and a check's measured time is
the sum of its tests' times in the session's JUnit file, so it compares with
the declared serial seconds. A check whose target is not there fails without
running and its target stays out of the session: under -n N pytest reports a
missing target only as exit 5, the code of a session that found no tests,
while a tier that deselects every test of a check reads `empty` and passes.
An argv check runs by itself in the tiers it
names: exit 0 is a pass, 3 an infra miss, anything else a failure.

Exit codes: 0 when every check passed, 1 when any check failed, 3 when the only
problems were infra misses (an oracle not installed, a fetch that failed)
after one retry of the whole run.

The receipt (.crapkit/accuracy/<tier>[-<shard>]-<os>-<python>.json) holds each
check's declared and measured seconds and outcome, the oracle versions the
tests read, the image tag and digest, the sha256 of the pins, locks and corpus
files, the digests tests noted, the Hypothesis seed, event counts, skipped-file
counts and infra messages. With GITHUB_STEP_SUMMARY set, the time table is
appended to the job summary as well.

The other commands serve CI and packet authors: `merge` joins shard receipts,
`image-tag` names the accuracy image, `kit-goldens` redeclares the kit's seed
goldens, `doc-range` prints the header line a model cites a doc range with,
`xplat` fails when two cells' receipts carry different export digests,
`events` fails when a required strategy shape occurred fewer than N times, and
`oracle-versions` fails naming each installed oracle that is missing or is not
its pin (the weekly no-cache image rebuild runs it).
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
from itertools import chain
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from accuracy.kit import runlog, tiers  # noqa: E402

CHECKS_DIR = REPO / "tools" / "accuracy" / "checks"
PINS = REPO / "tools" / "accuracy" / "pins.toml"
FIELDS = frozenset({"name", "pytest", "argv", "seconds", "os", "tiers", "os_sensitive"})
EXIT = {"pass": 0, "fail": 1, "infra": 3}
OS_NAMES = {"win32": "windows", "linux": "linux", "darwin": "macos"}
# Everything the accuracy image is built from; its tag hashes these.
IMAGE_INPUTS = (
    "tools/accuracy/image/Dockerfile",
    "tools/accuracy/image/install-tools.sh",
    "tools/accuracy/image/entry.sh",
    "tools/accuracy/image/requirements-build.txt",
    "tools/accuracy/pins.toml",
    "tools/accuracy/requirements-push.txt",
    "tools/accuracy/requirements-nightly.txt",
    "tools/accuracy/node/push/package-lock.json",
    "tools/accuracy/node/nightly/package-lock.json",
    "tests/accuracy/corpus_goldens/corpus.toml",
)
# The files a receipt records the digest of, when they exist: the image
# inputs, the goldens lock and the retro ledger the release gate recomputes.
RECEIPT_FILES = IMAGE_INPUTS + (
    "tests/accuracy/change_control/goldens.lock",
    "tests/accuracy/suite_strength/retro/ledger.tsv",
    "tests/accuracy/kit/fixtures/seed-goldens.lock",
)
# A run.py started from inside a test must not hand the child that test's identity.
PARENT_ONLY = ("PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
               "PYTEST_XDIST_TESTRUNUID")


class CheckError(ValueError):
    """A checks module, or a set of receipts, that run.py cannot use."""


@dataclass(frozen=True)
class Check:
    key: str
    name: str
    shard: str
    seconds: float
    pytest: tuple = ()
    argv: tuple = ()
    os: tuple = ()
    tiers: tuple = ()
    os_sensitive: bool = False


@dataclass(frozen=True)
class Case:
    file: Path
    name: str
    seconds: float
    failed: bool


# --- loading checks ---------------------------------------------------------------

def _unknown(row: dict) -> str | None:
    extra = sorted(set(row) - FIELDS)
    return f"has unknown field {extra[0]}" if extra else None


def _no_seconds(row: dict) -> str | None:
    return None if "seconds" in row else "declares no seconds"


def _targets(row: dict) -> str | None:
    shape = (bool(row.get("pytest")), bool(row.get("argv")))
    return {(False, False): "names neither pytest targets nor argv",
            (True, True): "names both pytest targets and argv"}.get(shape)


def _node_ids(row: dict) -> str | None:
    named = [target for target in row.get("pytest", ()) if "::" in target]
    return f"names node id {named[0]}; a check names files or directories" if named else None


def _argv_tiers(row: dict) -> str | None:
    unnamed = row.get("argv") and not row.get("tiers")
    return "is an argv check: an argv check names its tiers" if unnamed else None


def _sensitive_flag(row: dict) -> str | None:
    if isinstance(row.get("os_sensitive", False), bool):
        return None
    return "sets os_sensitive to something other than True or False"


ROW_RULES = (_unknown, _no_seconds, _targets, _node_ids, _argv_tiers, _sensitive_flag)


def _check(key: str, shard: str, row: dict) -> Check:
    problem = next(filter(None, (rule(row) for rule in ROW_RULES)), None)
    if problem:
        raise CheckError(f"{key}: check {row.get('name', '?')!r} {problem}")
    return Check(key=key, name=row["name"], shard=shard, seconds=row["seconds"],
                 pytest=tuple(row.get("pytest", ())), argv=tuple(row.get("argv", ())),
                 os=tuple(row.get("os", ())), tiers=tuple(row.get("tiers", ())),
                 os_sensitive=row.get("os_sensitive", False))


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(f"accuracy_checks_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _checks_of(path: Path) -> list[Check]:
    module = _module(path)
    shard = getattr(module, "SHARD", "")
    return [_check(path.stem, shard, row) for row in getattr(module, "CHECKS", ())]


def _refuse_twin_names(checks: list[Check]) -> None:
    names = Counter((check.key, check.name) for check in checks)
    twin = next((pair for pair, count in names.items() if count > 1), None)
    if twin:
        raise CheckError(f"{twin[0]}: two checks are named {twin[1]}")


def _refuse_shared_targets(checks: list[Check]) -> None:
    owners: dict[str, Check] = {}
    for check in checks:
        for target in check.pytest:
            _claim(owners, target, check)


def _claim(owners: dict, target: str, check: Check) -> None:
    other = owners.setdefault(target, check)
    if other is not check:
        raise CheckError(f"{target} is named by both {other.key}: {other.name} and "
                         f"{check.key}: {check.name}")


def load_checks(directory: Path = CHECKS_DIR) -> list[Check]:
    """Every check the modules in `directory` declare; a missing directory has none."""
    paths = sorted(path for path in Path(directory).glob("*.py") if not path.stem.startswith("_"))
    checks = [check for path in paths for check in _checks_of(path)]
    _refuse_twin_names(checks)
    _refuse_shared_targets(checks)
    return checks


def _in_shard(check: Check, shard: str | None) -> bool:
    return shard is None or check.shard == shard


def _on_os(check: Check, platform: str) -> bool:
    return not check.os or any(platform.startswith(name) for name in check.os)


def _in_tier(check: Check, tier: str) -> bool:
    return not check.tiers or tier in check.tiers


def _wanted(check: Check, os_sensitive_only: bool) -> bool:
    """A check that names its OS is OS-sensitive whether or not it says so: on
    Windows it runs nowhere else."""
    return check.os_sensitive or bool(check.os) or not os_sensitive_only


def selected(checks: list[Check], tier: str, shard: str | None, platform: str,
             os_sensitive_only: bool = False) -> list[Check]:
    """The checks a tier runs in this shard on this platform: only the ones whose
    answer can change with the OS when `os_sensitive_only` holds."""
    keeps = (lambda check: _in_shard(check, shard), lambda check: _on_os(check, platform),
             lambda check: _in_tier(check, tier), lambda check: _wanted(check, os_sensitive_only))
    return [check for check in checks if all(keep(check) for keep in keeps)]


# --- running them -------------------------------------------------------------------

def _child_env(tier: str, log: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in PARENT_ONLY}
    paths = [str(REPO / "tests"), env.get("PYTHONPATH", "")]
    env.update({tiers.TIER_ENV: tier, runlog.LOG_ENV: str(log),
                "PYTHONPATH": os.pathsep.join(filter(None, paths))})
    return env


def _seed(tier: str):
    """A random run records its seed; push and release are derandomized."""
    return random.SystemRandom().randrange(2 ** 32) if tier == "nightly" else "derandomized"


def session_root(targets: list[str]) -> Path:
    """REPO when every target lies in it; else the targets' common directory, so
    pytest never walks the directories above a target that sits elsewhere."""
    resolved = [(REPO / target).resolve() for target in targets]
    if all(map(_inside_repo, resolved)):
        return REPO
    return Path(os.path.commonpath(list(map(_directory, resolved))))


def _inside_repo(path: Path) -> bool:
    return path == REPO or REPO in path.parents


def _directory(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def _pytest_argv(targets: list[str], root: Path, workers: int, junit: Path, seed,
                 tier: str | None = None) -> list[str]:
    """The push tier loads kit.push_only, so it sees the push lock's packages alone
    wherever it runs, as CI's accuracy-push job does."""
    argv = [sys.executable, "-m", "pytest", *targets, "--rootdir", str(root), "-q",
            "-p", "no:cacheprovider", "-p", "no:randomly", "-o", "junit_family=xunit1",
            "--junitxml", str(junit)]
    if workers:
        argv += ["-n", str(workers)]
    if isinstance(seed, int):
        argv.append(f"--hypothesis-seed={seed}")
    if tier == "push":
        argv += ["-p", "accuracy.kit.push_only"]
    return argv


def _case(element, root: Path) -> Case:
    failed = element.find("failure") is not None or element.find("error") is not None
    where = (root / element.get("file", "")).resolve()
    return Case(where, element.get("name", ""), float(element.get("time") or 0), failed)


def _cases(junit: Path, root: Path) -> list[Case]:
    if not junit.is_file():
        return []
    return [_case(element, root) for element in ElementTree.parse(junit).getroot().iter("testcase")]


def _infra_keys(notes: list[dict], root: Path) -> set:
    """(file, test name) for each test that noted an infra miss."""
    ids = runlog.infra_tests(notes)
    return {((root / node.split("::")[0]).resolve(), node.split("::")[-1]) for node in ids}


def _owns(check: Check, case: Case) -> bool:
    targets = [(REPO / target).resolve() for target in check.pytest]
    return any(case.file == target or target in case.file.parents for target in targets)


def _pytest_outcome(cases: list[Case], infra: set) -> str:
    """fail when a test failed for a reason other than a noted infra miss."""
    failed = {(case.file, case.name) for case in cases if case.failed}
    verdicts = (("fail", failed - infra), ("infra", failed), ("pass", cases))
    return next((name for name, found in verdicts if found), "empty")


def _broken(code: int, outcome: str) -> str:
    """A session that broke (a usage or collection error) fails the checks it did not run."""
    return "fail" if code not in (0, 1, 5) and outcome in ("pass", "empty") else outcome


INTERRUPTED = 2  # pytest's exit for a session that stopped before its end


def _session_code(code: int, cases: list[Case]) -> int:
    """pytest's exit, or INTERRUPTED for exit 1 with no failed case in the report:
    a crapkit call stuck in C code ends a serial session with exit 1 before pytest
    writes its report (tests/e2e/cli_in_process.py), so no check ran to its end."""
    return INTERRUPTED if code == 1 and not any(case.failed for case in cases) else code


def _record(check: Check, seconds: float, outcome: str, tests: int | None) -> dict:
    return {"key": check.key, "name": check.name, "shard": check.shard,
            "declared": check.seconds, "seconds": round(seconds, 3), "outcome": outcome,
            "tests": tests}


def missing_targets(check: Check) -> list[str]:
    """The pytest targets of `check` whose path (the part before any ::) is not there."""
    return [target for target in check.pytest if not (REPO / target.split("::")[0]).exists()]


def _say_missing(check: Check, target: str) -> None:
    print(f"run.py: {check.key}: {check.name} names {target}, which is not there, so the check "
          f"fails without running; restore the file or fix the check's row in "
          f"tools/accuracy/checks/{check.key}.py", file=sys.stderr)


def _own_cases(check: Check, cases: list[Case]) -> list[Case]:
    return [case for case in cases if _owns(check, case)]


def _pytest_record(check: Check, cases: list[Case], infra: set, code: int) -> dict:
    """A check whose target is missing fails: it ran no test, and pytest under -n N
    reports a missing target only as exit 5, the code of a session with no tests."""
    mine = _own_cases(check, cases)
    gone = missing_targets(check)
    outcome = "fail" if gone else _broken(code, _pytest_outcome(mine, infra))
    record = _record(check, sum(case.seconds for case in mine), outcome, len(mine))
    return {**record, "missing": gone} if gone else record


def _session(targets: list[str], env: dict, junit: Path, workers: int, seed) -> tuple:
    """(exit code, cases, root) of one pytest session; no target, no session, since
    pytest handed no target collects the whole repository."""
    if not targets:
        return 0, [], REPO
    root = session_root(targets)
    code = subprocess.run(_pytest_argv(targets, root, workers, junit, seed, env.get(tiers.TIER_ENV)),
                          cwd=REPO, env=env).returncode
    return code, _cases(junit, root), root


def _missing(checks: list[Check]) -> set[str]:
    """The targets of `checks` that are not there, each named on stderr with its check."""
    gone = [(check, target) for check in checks for target in missing_targets(check)]
    for check, target in gone:
        _say_missing(check, target)
    return {target for _, target in gone}


def _present(checks: list[Check], missing: set[str]) -> list[str]:
    return sorted({target for check in checks for target in check.pytest} - missing)


def _pytest_records(checks: list[Check], env: dict, scratch: Path, workers: int, seed) -> list:
    targets = _present(checks, _missing(checks))
    code, cases, root = _session(targets, env, scratch / "junit.xml", workers, seed)
    infra = _infra_keys(runlog.read(Path(env[runlog.LOG_ENV])), root)
    code = _session_code(code, cases)
    return [_pytest_record(check, cases, infra, code) for check in checks]


def argv_outcome(code: int) -> str:
    return {0: "pass", 3: "infra"}.get(code, "fail")


def _argv_record(check: Check, env: dict) -> dict:
    argv = [sys.executable if word == "python" else word for word in check.argv]
    start = time.perf_counter()
    code = subprocess.run(argv, cwd=REPO, env=env).returncode
    return _record(check, time.perf_counter() - start, argv_outcome(code), None)


def overall(records: list[dict]) -> str:
    outcomes = {record["outcome"] for record in records}
    return next((worst for worst in ("fail", "infra") if worst in outcomes), "pass")


def _attempt(checks: list[Check], tier: str, workers: int, seed) -> tuple[list, dict]:
    with tempfile.TemporaryDirectory(prefix="crapkit-accuracy-run-") as scratch:
        env = _child_env(tier, Path(scratch) / "notes.jsonl")
        records = _pytest_records([c for c in checks if c.pytest], env, Path(scratch), workers,
                                  seed)
        records += [_argv_record(check, env) for check in checks if check.argv]
        return records, runlog.summarize(runlog.read(Path(env[runlog.LOG_ENV])))


def run_tier(checks: list[Check], tier: str, shard: str | None, workers: int = 0) -> dict:
    """Run the checks, once more when the first run met only infra misses."""
    seed = _seed(tier)
    records, notes = _attempt(checks, tier, workers, seed)
    attempts = 1
    if overall(records) == "infra":
        records, notes = _attempt(checks, tier, workers, seed)
        attempts = 2
    return receipt(tier, shard, records, notes, seed, attempts)


# --- receipts ------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_digests(repo: Path = REPO) -> dict[str, str]:
    return {name: _sha256(repo / name) for name in RECEIPT_FILES if (repo / name).is_file()}


def image_tag(repo: Path = REPO) -> str:
    """The first 12 hex of a sha256 over every image input's path and bytes."""
    hashed = hashlib.sha256()
    for name in IMAGE_INPUTS:
        path = repo / name
        hashed.update(name.encode("utf-8") + b"\0")
        hashed.update(path.read_bytes() if path.is_file() else b"<absent>")
    return hashed.hexdigest()[:12]


def _head() -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def _python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _check_key(record: dict) -> tuple:
    return record["key"], record["name"]


def receipt(tier: str, shard, records: list, notes: dict, seed, attempts: int) -> dict:
    return {
        "schema": 1, "tier": tier, "shard": shard,
        "os": OS_NAMES.get(sys.platform, sys.platform), "python": _python(), "head": _head(),
        "image": {"tag": os.environ.get("CRAPKIT_ACCURACY_IMAGE", ""),
                  "digest": os.environ.get("CRAPKIT_ACCURACY_IMAGE_DIGEST", "")},
        "digests": file_digests(), "exports": notes["exports"], "hypothesis_seed": seed,
        "checks": sorted(records, key=_check_key), "oracles": notes["oracles"],
        "events": notes["events"], "skipped_files": notes["skipped_files"],
        "infra": sorted(notes["infra"]), "outcome": overall(records), "attempts": attempts,
    }


def default_receipt(tier: str, shard: str | None) -> Path:
    parts = [tier, shard, OS_NAMES.get(sys.platform, sys.platform), _python()]
    return REPO / ".crapkit" / "accuracy" / ("-".join(filter(None, parts)) + ".json")


SAME = ("tier", "os", "python", "head", "hypothesis_seed", "image", "digests")


def _agree(receipts: list[dict]) -> None:
    for field in SAME:
        values = {json.dumps(item.get(field), sort_keys=True) for item in receipts}
        if len(values) > 1:
            raise CheckError(f"the receipts disagree on {field}: {sorted(values)}")


def _summed(maps) -> dict:
    total: Counter = Counter()
    for counts in maps:
        total.update(counts)
    return dict(total)


def _union(maps, what: str) -> dict:
    merged: dict = {}
    for entries in maps:
        for name, value in entries.items():
            if merged.setdefault(name, value) != value:
                raise CheckError(f"the receipts disagree on {what} {name}")
    return merged


def _column(receipts: list[dict], field: str, empty) -> list:
    return [item.get(field) or empty for item in receipts]


def _notes(receipts: list[dict]) -> dict:
    return {"exports": _union(_column(receipts, "exports", {}), "export"),
            "oracles": _union(_column(receipts, "oracles", {}), "oracle"),
            "events": _summed(_column(receipts, "events", {})),
            "skipped_files": _summed(_column(receipts, "skipped_files", {})),
            "infra": sorted(chain.from_iterable(_column(receipts, "infra", ())))}


def merge(receipts: list[dict]) -> dict:
    """One receipt for the shards of one tier on one OS and Python."""
    _agree(receipts)
    checks = sorted(chain.from_iterable(_column(receipts, "checks", ())), key=_check_key)
    attempts = max(_column(receipts, "attempts", 1))
    return {**receipts[0], **_notes(receipts), "shard": None, "checks": checks,
            "attempts": attempts, "outcome": overall(checks)}


def table(saved: dict) -> str:
    """The time table: declared against measured seconds per check."""
    shard = f" {saved['shard']}" if saved.get("shard") else ""
    lines = [f"### accuracy {saved['tier']}{shard} ({saved['os']}, {saved['python']}): "
             f"{saved['outcome']}", "", "| check | declared s | measured s | outcome |",
             "|---|---:|---:|---|"]
    lines += [f"| {c['key']}: {c['name']} | {c['declared']:g} | {c['seconds']:.2f} | "
              f"{c['outcome']} |" for c in saved["checks"]]
    return "\n".join(lines) + "\n"


def _publish(saved: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    text = table(saved)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


# --- commands --------------------------------------------------------------------------

def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="Run an accuracy tier.")
    parser.add_argument("--tier", choices=tiers.TIERS, default="push")
    parser.add_argument("--shard", help="run only the checks modules whose SHARD is this")
    parser.add_argument("--os-sensitive", action="store_true",
                        help="run only the checks whose answer can change with the OS "
                             "(CI's Windows push job)")
    parser.add_argument("-n", "--workers", type=int, default=0,
                        help="pytest-xdist workers for the pytest session")
    parser.add_argument("--receipt", type=Path, help="where to write the receipt")
    parser.add_argument("--checks", type=Path, default=CHECKS_DIR, help=argparse.SUPPRESS)
    return parser


def _run_main(argv: list[str]) -> int:
    args = _run_parser().parse_args(argv)
    checks = selected(load_checks(args.checks), args.tier, args.shard, sys.platform,
                      args.os_sensitive)
    saved = run_tier(checks, args.tier, args.shard, args.workers)
    _publish(saved, args.receipt or default_receipt(args.tier, args.shard))
    return EXIT[saved["outcome"]]


def _merge_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py merge")
    parser.add_argument("receipts", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    loaded = [json.loads(path.read_text(encoding="utf-8")) for path in args.receipts]
    _publish(merge(loaded), args.out)
    return 0


def _image_tag_main(argv: list[str]) -> int:
    print(image_tag())
    return 0


def _kit_goldens_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py kit-goldens",
                                     description="Remeasure the kit's seed corpus, rewrite its "
                                                 "goldens and declare the change.")
    parser.add_argument("--declare", required=True, metavar="ID")
    parser.add_argument("--kind", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--calcs", default="")
    parser.add_argument("--base", type=Path, default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from accuracy.kit import goldens
    change = {"id": args.declare, "date": time.strftime("%Y-%m-%d", time.gmtime()),
              "kind": args.kind, "calcs": args.calcs, "reason": args.reason}
    for path in goldens.regenerate_seed(args.base, change):
        print(f"relocked {path} under {args.declare}")
    return 0


def _doc_range_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py doc-range")
    parser.add_argument("path")
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    args = parser.parse_args(argv)
    from accuracy.kit import docrange
    print(docrange.header(args.path, args.start, args.end))
    return 0


def _load_receipts(paths: list[str]) -> list[dict]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]


def _cell(saved: dict) -> str:
    return "-".join(filter(None, (saved.get("os"), saved.get("python"), saved.get("shard"))))


def _export_names(receipts: list[dict]) -> list[str]:
    return sorted({name for saved in receipts for name in saved.get("exports", {})})


def _export_line(name: str, receipts: list[dict]) -> str | None:
    values = [saved.get("exports", {}).get(name, "<missing>") for saved in receipts]
    if len(set(values)) == 1:
        return None
    cells = ", ".join(f"{_cell(saved)} {value}" for saved, value in zip(receipts, values))
    return f"{name} differs: {cells}"


def export_differences(receipts: list[dict]) -> list[str]:
    """One line per export whose digest is not the same in every receipt."""
    found = (_export_line(name, receipts) for name in _export_names(receipts))
    return [line for line in found if line]


def _xplat_main(argv: list[str]) -> int:
    receipts = _load_receipts(argv)
    differences = export_differences(receipts)
    agreed = f"{len(_export_names(receipts))} exports agree across {len(receipts)} receipts"
    print("\n".join(f"xplat: {line}" for line in differences or [agreed]))
    return 1 if differences else 0


def _refuse(prefix: str, problems: list[str]) -> int:
    """Print each problem on stderr; exit 1 when there is any."""
    for problem in problems:
        print(f"{prefix}: {problem}", file=sys.stderr)
    return 1 if problems else 0


def rare_events(receipts: list[dict], floor: int) -> list[str]:
    """Each required strategy shape that occurred fewer than `floor` times."""
    from accuracy.kit import strategies
    counts = _summed(_column(receipts, "events", {}))
    required = sorted({name for table in strategies.REQUIRED.values() for name in table})
    return [f"{name} occurred {counts.get(name, 0)} times, fewer than {floor}"
            for name in required if counts.get(name, 0) < floor]


def _events_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py events")
    parser.add_argument("--min", type=int, default=50, dest="floor")
    parser.add_argument("receipts", nargs="+")
    args = parser.parse_args(argv)
    return _refuse("events", rare_events(_load_receipts(args.receipts), args.floor))


def _oracle_line(name: str, pins: dict) -> tuple[str, str | None]:
    """(the table line, the problem or None) for one pinned oracle."""
    from accuracy.kit import oracles
    try:
        found = oracles.locate(name, pins)
    except oracles.OracleMissing as missing:
        return f"{name}: missing", str(missing)
    return f"{name}: {found.version}", oracles.drift(found, pins[name])


def _checked_pins(pins: dict, tier: str) -> list[str]:
    """Every installed oracle a tier reads: push pins only, or all but producers."""
    return [name for name, pin in sorted(pins.items())
            if pin.kind != "producer" and (tier == "nightly" or pin.tier == "push")]


def _oracle_versions_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py oracle-versions")
    parser.add_argument("--tier", choices=("push", "nightly"), default="nightly")
    parser.add_argument("--pins", type=Path, default=PINS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from accuracy.kit import oracles
    pins = oracles.load_pins(args.pins)
    results = [_oracle_line(name, pins) for name in _checked_pins(pins, args.tier)]
    print("\n".join(line for line, _ in results))
    return _refuse("oracle-versions", [problem for _, problem in results if problem])


COMMANDS = {"merge": _merge_main, "image-tag": _image_tag_main, "kit-goldens": _kit_goldens_main,
            "doc-range": _doc_range_main, "xplat": _xplat_main, "events": _events_main,
            "oracle-versions": _oracle_versions_main}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = COMMANDS.get(args[0]) if args else None
    try:
        return command(args[1:]) if command else _run_main(args)
    except ValueError as refused:  # a malformed checks module, receipts that disagree, a bad declare
        print(f"run.py: {refused}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
