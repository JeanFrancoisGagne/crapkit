"""Run two crapkit wheels on one corpus and map every value that moved.

    python tools/accuracy/wheel_diff.py diff --base-wheel SIDE --candidate-wheel SIDE
        [--corpus small|full] [--corpus-dir DIR] [--out DIR] [--wheelhouse DIR]
        [--expect-calcs "CALC,..."]
    python tools/accuracy/wheel_diff.py xplat RECEIPT RECEIPT [RECEIPT ...]

A SIDE is a wheel file, a CI hand-off directory (.crapkit/ci-measure/<side>:
one *.whl, or failure.json when that side's measurement stopped), or
`crapkit==VERSION`, fetched once from PyPI into the wheelhouse
(CRAPKIT_ACCURACY_WHEELHOUSE, or a per-user cache directory). When a side
handed off failure.json there is nothing to compare: `diff` prints one skip
line and exits 0.

Each wheel is unpacked, and every crapkit command runs with that directory
alone on PYTHONPATH, after a check that `import crapkit` resolves inside it.
Both sides share this interpreter and its lizard, so what moves is crapkit.

`--corpus small` commits tests/accuracy/corpus_goldens/small to a fresh repo
with fixed dates, freezes git's clock at corpus.toml's epoch, and runs
`inventory --export` and `coverage --export` (its lanes copy recorded
artifacts, so no test runner and no network). `--corpus full` runs
`inventory --export` on every member of a built full corpus (corpus.py build
or fetch), whose scopes are coverage-optional.

The moved-row map keys a row by (path, long name, occurrence), so a function
whose start line moves is one row with a moved `start`. Each moved column
names the calculation it belongs to (CALCS); a row only one side lists moves
"Function discovery and spans". `--out` receives moved.tsv, both sides'
exports and summary.json; `--expect-calcs` states the calcs a declared change
moved (empty: none), and any other set exits 1.

`xplat` compares the exports the cross-platform receipts noted (a
`corpus_goldens` export note, see test_xplat_digest): ints and labels exactly,
a float by its printed text, and a float that differs only in its last bits
when printed at full precision within 2 ulp. It names the first difference.

Exit codes: 0 compared, skipped or agreed; 1 an unexpected move or an xplat
difference; 3 a side could not be installed or run.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from packaging.version import Version

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from accuracy.kit import corpus_run, drive, repos, surfaces  # noqa: E402

EXIT_MOVED, EXIT_INFRA = 1, 3
KEY = ("path", "long_name", "occurrence")
DISCOVERY = "Function discovery and spans"
CALCS = {
    "scope": "File universe and scope ownership",
    "start": DISCOVERY, "end": DISCOVERY,
    "ccn_std": "ccn_std, ccn_mod and gated ccn", "ccn_mod": "ccn_std, ccn_mod and gated ccn",
    "ccn": "ccn_std, ccn_mod and gated ccn",
    "nloc": "nloc",
    "params": "Parameter list (params, packet.params)",
    "nesting": "Nesting depth",
    "cognitive": "Cognitive complexity",
    "cov": "Function coverage ratio",
    "flag": "Coverage flag",
    "crap": "CRAP score",
    "remedy": "Remedy label",
}
MOVED_COLUMNS = ("export", "path", "long_name", "occurrence", "column", "old", "new", "calc")
MAX_ULPS = 2
_INT = re.compile(r"-?\d+")
SMALL_COMMANDS = (("inventory.tsv", ("inventory", "--export", "{out}/inventory.tsv")),
                  ("scored.tsv", ("coverage", "--export", "{out}/scored.tsv")))
FULL_COMMANDS = (("inventory.tsv", ("inventory", "--export", "{out}/inventory.tsv")),)


class WheelDiffError(RuntimeError):
    """A side that cannot be installed or run: an infra failure, not a move."""


@dataclass(frozen=True)
class Skip:
    side: str
    reason: str


# --- the moved-row map ---------------------------------------------------------------

@dataclass(frozen=True)
class Moved:
    export: str
    path: str
    long_name: str
    occurrence: str
    column: str
    old: str
    new: str

    @property
    def calc(self) -> str:
        return CALCS.get(self.column, DISCOVERY)

    def cells(self) -> list[str]:
        return [self.export, self.path, self.long_name, self.occurrence, self.column,
                self.old, self.new, self.calc]


def keyed(text: str) -> dict[tuple, dict]:
    """An export's rows by (path, long name, occurrence), read the way
    docs/portable-records.md tells another tool to read them."""
    rows = surfaces.read_tsv(text)[1]
    return {tuple(row.get(name, "") for name in KEY): row for row in rows}


def _row_moves(export: str, key: tuple, old: dict, new: dict) -> list[Moved]:
    columns = sorted((set(old) & set(new)) - set(KEY))
    return [Moved(export, *key, column, old[column], new[column])
            for column in columns if old[column] != new[column]]


def _presence(export: str, key: tuple, old: dict | None, new: dict | None) -> list[Moved]:
    """A row one side lacks: one move in the `row` column."""
    return [Moved(export, *key, "row", "present" if old else "absent",
                  "present" if new else "absent")]


def moved(export: str, base: str, candidate: str) -> list[Moved]:
    """Every (row, column) whose value differs between two exports of one corpus."""
    old, new = keyed(base), keyed(candidate)
    found: list[Moved] = []
    for key in sorted(set(old) | set(new)):
        pair = (old.get(key), new.get(key))
        found += _row_moves(export, key, *pair) if all(pair) else _presence(export, key, *pair)
    return found


def moved_calcs(rows: list[Moved]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.calc] = counts.get(row.calc, 0) + 1
    return dict(sorted(counts.items()))


def moved_tsv(rows: list[Moved]) -> str:
    lines = ["\t".join(MOVED_COLUMNS)] + ["\t".join(row.cells()) for row in rows]
    return "\n".join(lines) + "\n"


def _names(text: str) -> set[str]:
    return {name.strip() for name in text.split(",") if name.strip()}


def expectation_problem(rows: list[Moved], expected: str | None) -> str | None:
    """None when the moved calcs are exactly the declared ones (or none were declared)."""
    if expected is None:
        return None
    wanted, found = _names(expected), set(moved_calcs(rows))
    if found == wanted:
        return None
    return (f"the wheels moved {sorted(found) or 'nothing'}, the change declares "
            f"{sorted(wanted) or 'nothing'}")


# --- sides ----------------------------------------------------------------------------------

def hand_off(directory: Path) -> Path | Skip:
    """The wheel a CI hand-off directory holds, or a Skip when it holds failure.json."""
    failure = directory / "failure.json"
    if failure.is_file():
        record = json.loads(failure.read_text(encoding="utf-8"))
        return Skip(directory.name, f"stopped at {record.get('phase')}: {record.get('error')}")
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise WheelDiffError(f"{directory} holds {len(wheels)} wheels and no failure.json")
    return wheels[0]


PYPI = "https://pypi.org/pypi/crapkit/{version}/json"


def _wheel_entry(release: dict, version: str) -> dict:
    wheels = [entry for entry in release.get("urls", []) if entry["packagetype"] == "bdist_wheel"]
    if not wheels:
        raise WheelDiffError(f"PyPI lists no wheel for crapkit {version}")
    return wheels[0]


def _fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except OSError as failed:
        raise WheelDiffError(f"fetching {url} failed: {failed}") from None


def download(version: str, wheelhouse: Path) -> Path:
    """crapkit's wheel for `version` from PyPI, checked against the sha256 PyPI
    publishes, fetched once into the wheelhouse."""
    entry = _wheel_entry(json.loads(_fetch(PYPI.format(version=version))), version)
    target = wheelhouse / entry["filename"]
    if not target.is_file():
        data = _fetch(entry["url"])
        if hashlib.sha256(data).hexdigest() != entry["digests"]["sha256"]:
            raise WheelDiffError(f"{entry['filename']} does not hash to PyPI's sha256")
        wheelhouse.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return target


def _serves_a_wheel(files: list) -> bool:
    return any(entry["packagetype"] == "bdist_wheel" and not entry.get("yanked")
               for entry in files)


def _kept(version: Version, at_most: str | None) -> bool:
    return not version.is_prerelease and (at_most is None or version <= Version(at_most))


def releases(count: int, at_most: str | None = None) -> list[str]:
    """The newest `count` crapkit releases PyPI serves an unyanked wheel for,
    newest first, none above `at_most`; pre-releases are left out."""
    listing = json.loads(_fetch(PYPI.replace("/{version}", "")))["releases"]
    served = [Version(name) for name, files in listing.items() if _serves_a_wheel(files)]
    final = sorted(filter(lambda version: _kept(version, at_most), served), reverse=True)
    return [str(version) for version in final[:count]]


def upload_date(version: str) -> str:
    """The UTC date (YYYY-MM-DD) PyPI received the release's wheel."""
    entry = _wheel_entry(json.loads(_fetch(PYPI.format(version=version))), version)
    return entry["upload_time_iso_8601"][:10]


def cached(version: str, wheelhouse: Path) -> Path | None:
    found = sorted(wheelhouse.glob(f"crapkit-{version}-*.whl"))
    return found[0] if found else None


def resolve(spec: str, wheelhouse: Path) -> Path | Skip:
    """A side named on the command line, as a wheel path or a Skip."""
    if spec.startswith("crapkit=="):
        version = spec.split("==", 1)[1]
        return cached(version, wheelhouse) or download(version, wheelhouse)
    path = Path(spec)
    if path.is_dir():
        return hand_off(path)
    if not path.is_file():
        raise WheelDiffError(f"no wheel or hand-off at {spec}")
    return path


def unpack(wheel: Path, dest: Path) -> Path:
    """The wheel's contents under dest, checked to be the crapkit it holds."""
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(dest)
    done = subprocess.run([sys.executable, "-c", "import crapkit; print(crapkit.__file__)"],
                          env=drive.child_env({"PYTHONPATH": str(dest)}), capture_output=True,
                          text=True, cwd=dest.parent)
    where = Path(done.stdout.strip() or "?")
    if dest.resolve() not in where.resolve().parents:
        raise WheelDiffError(f"{wheel.name}: import crapkit resolved to {where}, not {dest}")
    return dest


# --- measuring one side -------------------------------------------------------------------

def _run(root: Path, site: Path, out: Path, commands, date_now: int) -> dict[str, str]:
    driver = drive.Driver(root, date_now=date_now, spawn=True, env={"PYTHONPATH": str(site)})
    exports = {}
    for name, argv in commands:
        result = driver.run(*[part.replace("{out}", out.as_posix()) for part in argv])
        if result.code != 0:
            raise WheelDiffError(f"crapkit {' '.join(argv)} exited {result.code} under "
                                 f"{site.name}: {result.stderr[-800:]}")
        exports[name] = (out / name).read_bytes().decode("utf-8")
    return exports


def measure_tree(tree: Path, site: Path, work: Path, commands, date_now: int) -> dict[str, str]:
    """Commit `tree` to a fresh repo under work and run the commands there."""
    built = repos.build(repos.tree_spec(tree), work / "repo")
    out = work / "out"
    out.mkdir(parents=True)
    return _run(built.root, site, out, commands, date_now)


def _member_dirs(corpus_dir: Path) -> list[Path]:
    return sorted(path for path in corpus_dir.iterdir() if (path / "crapkit.toml").is_file())


def measure_full(corpus_dir: Path, site: Path, work: Path, date_now: int) -> dict[str, str]:
    """Each member's inventory export, named `<member>/inventory.tsv`."""
    exports = {}
    for member in _member_dirs(corpus_dir):
        found = measure_tree(member, site, work / member.name, FULL_COMMANDS, date_now)
        exports.update({f"{member.name}/{name}": text for name, text in found.items()})
    return exports


def measure(site: Path, work: Path, corpus: str, corpus_dir: Path | None) -> dict[str, str]:
    if corpus == "full":
        if corpus_dir is None:
            raise WheelDiffError("--corpus full needs --corpus-dir (corpus.py build or fetch)")
        return measure_full(corpus_dir, site, work, corpus_run.date_now())
    return measure_tree(corpus_run.SMALL, site, work, SMALL_COMMANDS, corpus_run.date_now())


def diff_exports(base: dict[str, str], candidate: dict[str, str]) -> list[Moved]:
    names = sorted(set(base) | set(candidate))
    return [row for name in names for row in moved(name, base.get(name, ""),
                                                   candidate.get(name, ""))]


# --- xplat -------------------------------------------------------------------------------

def _floats(left: str, right: str) -> tuple[float, float] | None:
    """Both texts as floats, or None when either is an int or no number."""
    if _INT.fullmatch(left) or _INT.fullmatch(right):
        return None
    try:
        return float(left), float(right)
    except ValueError:
        return None


def within_ulps(left: str, right: str, ulps: int = MAX_ULPS) -> bool:
    """Two float texts at most `ulps` units in the last place apart."""
    pair = _floats(left, right)
    if pair is None or not all(map(math.isfinite, pair)):
        return False
    a, b = pair
    return abs(a - b) <= ulps * math.ulp(max(abs(a), abs(b)))


def _cell_problem(where: str, left: str, right: str) -> str | None:
    if left == right or within_ulps(left, right):
        return None
    return f"{where}: {left!r} against {right!r}"


def export_problem(name: str, left: str, right: str) -> str | None:
    """The first cell two copies of one export disagree on beyond the xplat rule."""
    for row in moved(name, left, right):
        problem = _cell_problem(f"{name} {row.path} {row.long_name!r} #{row.occurrence} "
                                f"{row.column}", row.old, row.new)
        if problem:
            return problem
    return None


def _exports(receipt: dict) -> dict[str, str]:
    return {name: value["text"] for name, value in (receipt.get("exports") or {}).items()
            if isinstance(value, dict) and "text" in value}


def _cell(receipt: dict) -> str:
    return f"{receipt.get('os')}-{receipt.get('python')}"


def _pair_problems(first: dict, other: dict) -> list[str]:
    left, right = _exports(first), _exports(other)
    missing = sorted(set(left) ^ set(right))
    if missing:
        return [f"{_cell(first)} and {_cell(other)} noted different exports: {missing}"]
    found = (export_problem(name, left[name], right[name]) for name in sorted(left))
    return [f"{_cell(first)} vs {_cell(other)}: {problem}" for problem in found if problem]


def xplat(receipts: list[dict]) -> list[str]:
    """Every receipt's exports against the first's; one line per disagreement."""
    if not receipts or not _exports(receipts[0]):
        return ["no receipt noted a corpus export"]
    return [line for other in receipts[1:] for line in _pair_problems(receipts[0], other)]


# --- commands -----------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wheel_diff.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    diff = sub.add_parser("diff")
    diff.add_argument("--base-wheel", required=True)
    diff.add_argument("--candidate-wheel", required=True)
    diff.add_argument("--corpus", choices=("small", "full"), default="small")
    diff.add_argument("--corpus-dir", type=Path)
    diff.add_argument("--out", type=Path)
    diff.add_argument("--wheelhouse", type=Path, default=default_wheelhouse())
    diff.add_argument("--expect-calcs")
    receipts = sub.add_parser("xplat")
    receipts.add_argument("receipts", nargs="+", type=Path)
    return parser


WHEELHOUSE_ENV = "CRAPKIT_ACCURACY_WHEELHOUSE"


def default_wheelhouse() -> Path:
    """CRAPKIT_ACCURACY_WHEELHOUSE (a CI cache), else a per-user cache directory."""
    named = os.environ.get(WHEELHOUSE_ENV)
    if named:
        return Path(named)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    return Path(base) / "crapkit-accuracy" / "wheelhouse"


def _sides(args) -> tuple:
    return tuple(resolve(spec, args.wheelhouse) for spec in (args.base_wheel,
                                                           args.candidate_wheel))


def _write_out(out: Path, exports: tuple, rows: list[Moved]) -> None:
    for side, found in zip(("base", "candidate"), exports):
        for name, text in found.items():
            target = out / side / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
    (out / "moved.tsv").write_bytes(moved_tsv(rows).encode("utf-8"))
    summary = {"moved_rows": len(rows), "calcs": moved_calcs(rows)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")


def skip_line(sides: tuple) -> str | None:
    """The one line `diff` prints when a side handed off failure.json."""
    skipped = [side for side in sides if isinstance(side, Skip)]
    if not skipped:
        return None
    return f"wheel diff skipped: a side handed off failure.json ({skipped[0].reason})"


def _measure_sides(sides: tuple, args) -> tuple:
    with tempfile.TemporaryDirectory(prefix="crapkit-wheel-diff-",
                                     ignore_cleanup_errors=True) as scratch:
        work = Path(scratch)
        return tuple(measure(unpack(wheel, work / f"{side}-site"), work / side, args.corpus,
                             args.corpus_dir)
                     for side, wheel in zip(("base", "candidate"), sides))


def _diff(args) -> tuple[int, list[str]]:
    sides = _sides(args)
    skipped = skip_line(sides)
    if skipped:
        return 0, [skipped]
    exports = _measure_sides(sides, args)
    rows = diff_exports(*exports)
    if args.out:
        _write_out(args.out, exports, rows)
    return _verdict(rows, args.expect_calcs)


def _verdict(rows: list[Moved], expected: str | None) -> tuple[int, list[str]]:
    lines = [f"{len(rows)} value(s) moved"] + [f"  {calc}: {count}"
                                              for calc, count in moved_calcs(rows).items()]
    lines += [f"  {row.path} {row.long_name} {row.column}: {row.old} -> {row.new}"
              for row in rows[:10]]
    problem = expectation_problem(rows, expected)
    return (EXIT_MOVED, lines + [problem]) if problem else (0, lines)


def _xplat(args) -> tuple[int, list[str]]:
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in args.receipts]
    problems = xplat(receipts)
    if problems:
        return EXIT_MOVED, problems
    return 0, [f"{len(receipts)} receipts agree on {len(_exports(receipts[0]))} export(s)"]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, lines = {"diff": _diff, "xplat": _xplat}[args.command](args)
    except WheelDiffError as failed:
        print(f"wheel_diff.py: {failed}", file=sys.stderr)
        return EXIT_INFRA
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
