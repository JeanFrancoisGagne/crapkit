"""The wheelhouse lock and the image manifest.

    python tools/deploy/lock.py                  resolve pins.toml [wheelhouse] into wheelhouse.lock
    python tools/deploy/lock.py --check          the lock covers pins.toml; online, nothing newer on PyPI
    python tools/deploy/lock.py --check --offline   the coverage half only
    python tools/deploy/lock.py fetch --os linux --arch x86_64 --dest /opt/wheelhouse
    python tools/deploy/lock.py manifest --image crapkit-deploy:core [--image ...]
    python tools/deploy/lock.py manifest --check --image crapkit-deploy:core

image-manifest.lock holds one block per image, headed `# image: <tag>`. A
refresh replaces the blocks of the images it names and keeps the rest; a
check prints the diff between an image and its block and writes nothing.

Resolution runs `uv pip compile` once per requirement set and wheelhouse row,
so environment markers are read for the target platform, not the host. Each
pinned name==version then takes its best wheel for the row from PyPI's JSON
API, with the URL and sha256 PyPI serves. `fetch` downloads exactly those files
and refuses any whose sha256 differs, which is how a Linux image and a Windows
toolchain get the same wheelhouse. Resolving needs uv, network and the
`packaging` module; fetching needs only the standard library.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

import pins as pinsfile

HERE = Path(__file__).resolve().parent
LOCK = HERE / "wheelhouse.lock"
MANIFEST = HERE / "image-manifest.lock"
PYPI = "https://pypi.org/pypi"
UV_PLATFORM = {("linux", "x86_64"): "x86_64-manylinux_2_28", ("linux", "aarch64"): "aarch64-manylinux_2_28",
               ("windows", "x86_64"): "x86_64-pc-windows-msvc", ("macos", "aarch64"): "aarch64-apple-darwin"}
# glibc 2.17 through trixie's 2.41, newest first; macOS 11 through 15.
PLATFORMS = {
    ("linux", "x86_64"): [f"manylinux_2_{m}_x86_64" for m in range(41, 16, -1)] + ["manylinux2014_x86_64"],
    ("linux", "aarch64"): [f"manylinux_2_{m}_aarch64" for m in range(41, 16, -1)] + ["manylinux2014_aarch64"],
    ("windows", "x86_64"): ["win_amd64"],
    ("macos", "aarch64"): [f"macosx_{v}_0_{a}" for v in range(15, 10, -1) for a in ("arm64", "universal2")],
}


# --- resolving -------------------------------------------------------------

def requirement_sets(pins: dict, newest: str) -> list[list[str]]:
    """One resolution per old release, the newest release, lizard, and each tool set."""
    wheelhouse = pins["wheelhouse"]
    releases = [*wheelhouse["crapkit"], newest]
    return ([[f"crapkit[py]=={version}"] for version in releases]
            + [[f"lizard=={wheelhouse['lizard']}"]] + [list(tools) for tools in wheelhouse["sets"]])


def compile_set(row: dict, requirements: list[str]) -> list[tuple[str, str]]:
    """name==version pairs uv resolves for this row's interpreter and platform."""
    argv = ["uv", "pip", "compile", "--quiet", "--no-header", "--no-annotate", "--python-version",
            row["python"], "--python-platform", UV_PLATFORM[(row["os"], row["arch"])], "-"]
    out = subprocess.run(argv, input="\n".join(requirements), capture_output=True, text=True,
                         check=True).stdout
    return [tuple(line.split("==", 1)) for line in out.splitlines() if "==" in line]


def _json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def release_files(name: str, version: str, cache: dict) -> list[dict]:
    key = (name.lower(), version)
    if key not in cache:
        cache[key] = _json(f"{PYPI}/{name}/{version}/json")["urls"]
    return cache[key]


def supported_tags(row: dict) -> list:
    from packaging import tags
    version = tuple(int(part) for part in row["python"].split("."))
    platforms = PLATFORMS[(row["os"], row["arch"])]
    return [*tags.cpython_tags(version, platforms=platforms),
            *tags.compatible_tags(version, platforms=platforms)]


def _rank(filename: str, ranks: dict) -> int:
    from packaging.utils import parse_wheel_filename
    wheel_tags = parse_wheel_filename(filename)[3]
    return min((ranks[tag] for tag in wheel_tags if tag in ranks), default=len(ranks))


def _ranked_wheels(files: list[dict], ranks: dict) -> list[tuple]:
    return [(_rank(f["filename"], ranks), f["filename"], f) for f in files if f["filename"].endswith(".whl")]


def _sdist(files: list[dict]) -> dict | None:
    return next((f for f in files if f["packagetype"] == "sdist"), None)


def best_file(files: list[dict], ranks: dict) -> dict | None:
    """The row's most specific compatible wheel, or the sdist when no wheel fits."""
    fitting = sorted(item for item in _ranked_wheels(files, ranks) if item[0] < len(ranks))
    return fitting[0][2] if fitting else _sdist(files)


def _entry(chosen: dict, name: str, version: str) -> dict:
    return {"name": chosen["filename"], "project": name, "version": version,
            "url": chosen["url"], "sha256": chosen["digests"]["sha256"], "rows": []}


def _lock_one(row: dict, name: str, version: str, ranks: dict, cache: dict, files: dict) -> None:
    chosen = best_file(release_files(name, version, cache), ranks)
    if chosen is None:
        raise SystemExit(f"lock: {name}=={version} has no file for {row['name']}")
    entry = files.setdefault(chosen["filename"], _entry(chosen, name, version))
    if row["name"] not in entry["rows"]:
        entry["rows"].append(row["name"])


def lock_row(row: dict, sets: list[list[str]], cache: dict, files: dict) -> None:
    ranks = {tag: index for index, tag in enumerate(supported_tags(row))}
    for requirements in sets:
        for name, version in compile_set(row, requirements):
            _lock_one(row, name, version, ranks, cache, files)


def newest_release(project: str) -> str:
    return _json(f"{PYPI}/{project}/json")["info"]["version"]


def resolve(pins: dict) -> dict:
    newest = newest_release("crapkit")
    sets = requirement_sets(pins, newest)
    files, cache = {}, {}
    for row in pins["wheelhouse"]["row"]:
        lock_row(row, sets, cache, files)
    return {"newest": {"crapkit": newest, "lizard": pins["wheelhouse"]["lizard"]},
            "file": sorted(files.values(), key=lambda entry: entry["name"])}


# --- the lock file ----------------------------------------------------------

def _toml_value(value) -> str:
    return json.dumps(value) if not isinstance(value, list) else "[" + ", ".join(map(json.dumps, value)) + "]"


def dumps(lock: dict) -> str:
    lines = ["# Generated by tools/deploy/lock.py from pins.toml. Do not edit by hand.",
             "[newest]", *(f"{key} = {_toml_value(value)}" for key, value in lock["newest"].items())]
    for entry in lock["file"]:
        lines += ["", "[[file]]", *(f"{key} = {_toml_value(entry[key])}" for key in
                                   ("name", "project", "version", "url", "sha256", "rows"))]
    return "\n".join(lines) + "\n"


def read(path: Path = LOCK) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# --- checking ---------------------------------------------------------------

def _row_projects(lock: dict, row: str) -> set[tuple[str, str]]:
    return {(entry["project"].lower(), entry["version"]) for entry in lock["file"] if row in entry["rows"]}


def coverage_problems(pins: dict, lock: dict) -> list[str]:
    """Every row holds every pinned crapkit release, the newest one and lizard."""
    wanted = [("crapkit", version) for version in [*pins["wheelhouse"]["crapkit"], lock["newest"]["crapkit"]]]
    wanted.append(("lizard", pins["wheelhouse"]["lizard"]))
    problems = []
    for row in pins["wheelhouse"]["row"]:
        held = _row_projects(lock, row["name"])
        problems += [f"{row['name']}: no {name}=={version}" for name, version in wanted
                     if (name, version) not in held]
    return problems


def drift_problems(lock: dict, newest=newest_release) -> list[str]:
    """A newer crapkit or lizard on PyPI than the lock names."""
    return [f"{project} {newest(project)} is on PyPI; the lock names {pinned}"
            for project, pinned in lock["newest"].items() if newest(project) != pinned]


def check(pins: dict, lock: dict, offline: bool) -> list[str]:
    problems = coverage_problems(pins, lock)
    return problems if offline else problems + drift_problems(lock)


# --- fetching ---------------------------------------------------------------

def row_names(pins: dict, os_name: str, arch: str) -> set[str]:
    return {row["name"] for row in pins["wheelhouse"]["row"] if (row["os"], row["arch"]) == (os_name, arch)}


def select(lock: dict, rows: set[str]) -> list[dict]:
    return [entry for entry in lock["file"] if rows.intersection(entry["rows"])]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_one(entry: dict, dest: Path, opener=urllib.request.urlopen) -> Path:
    """Download one lock entry into dest, or keep a file already holding its bytes."""
    target = dest / entry["name"]
    if target.exists() and sha256(target) == entry["sha256"]:
        return target
    dest.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    with opener(entry["url"]) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    if sha256(partial) != entry["sha256"]:
        partial.unlink()
        raise SystemExit(f"lock: {entry['name']} does not match its pinned sha256")
    return partial.replace(target)


def fetch(lock: dict, rows: set[str], dest: Path, opener=urllib.request.urlopen) -> list[Path]:
    """dest holds exactly the rows' files: a wheel an older lock named is
    removed, or pip would still find it."""
    dest.mkdir(parents=True, exist_ok=True)
    kept = [fetch_one(entry, dest, opener) for entry in select(lock, rows)]
    for stale in set(dest.iterdir()) - set(kept):
        stale.unlink()
    return kept


# --- the image manifest -------------------------------------------------------

def manifest(image: str) -> str:
    """What `entry.sh manifest` prints inside `image`, offline: tool versions,
    dpkg-query -W, npm ls per prefix, the runner's pip freeze and the sha256 of
    every wheel and fetched binary."""
    argv = ["docker", "run", "--rm", "--network", "none", image, "manifest"]
    # UTF-8 whatever this host's code page is, so Windows records what Linux does.
    return subprocess.run(argv, capture_output=True, check=True).stdout.decode("utf-8")


MANIFEST_HEADER = "# image: "


def manifest_blocks(text: str) -> dict[str, str]:
    """image-manifest.lock as image tag -> the manifest recorded for it."""
    blocks, name = {}, None
    for line in text.splitlines(keepends=True):
        if line.startswith(MANIFEST_HEADER):
            name = line[len(MANIFEST_HEADER):].strip()
            blocks[name] = ""
        elif name is not None:
            blocks[name] += line
    return blocks


def manifest_text(blocks: dict[str, str]) -> str:
    return "".join(f"{MANIFEST_HEADER}{name}\n{body}" for name, body in sorted(blocks.items()))


def manifest_diff(image: str, recorded: str, built: str) -> list[str]:
    return list(difflib.unified_diff(recorded.splitlines(), built.splitlines(), f"image-manifest.lock [{image}]",
                                     image, lineterm=""))


def _recorded(path: Path) -> dict[str, str]:
    return manifest_blocks(path.read_text(encoding="utf-8")) if path.exists() else {}


def _check_manifest(recorded: dict[str, str], built: dict[str, str]) -> int:
    diff = [line for image, text in built.items() for line in manifest_diff(image, recorded.get(image, ""), text)]
    print("\n".join(diff), file=sys.stderr)
    return 1 if diff else 0


def _do_manifest(args) -> int:
    recorded = _recorded(args.manifest)
    built = {image: manifest(image) for image in args.image or ["crapkit-deploy:core"]}
    if args.check:
        return _check_manifest(recorded, built)
    args.manifest.write_text(manifest_text({**recorded, **built}), encoding="utf-8", newline="\n")
    return 0


# --- command line ------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", nargs="?", default="lock", choices=["lock", "fetch", "manifest"])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--os", dest="os_name", default="linux")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--image", action="append", help="manifest: an image tag (repeatable; default core)")
    parser.add_argument("--pins", type=Path, default=pinsfile.PINS)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    return parser


def _report(problems: list[str]) -> int:
    for problem in problems:
        print(f"lock: {problem}", file=sys.stderr)
    print("lock: OK" if not problems else f"lock: {len(problems)} problem(s)")
    return 1 if problems else 0


def _do_fetch(args, pins: dict) -> int:
    files = fetch(read(args.lock), row_names(pins, args.os_name, args.arch), args.dest)
    print(f"lock: {len(files)} file(s) in {args.dest}")
    return 0


def _do_lock(args, pins: dict) -> int:
    if args.check:
        return _report(check(pins, read(args.lock), args.offline))
    args.lock.write_text(dumps(resolve(pins)), encoding="utf-8", newline="\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pins = pinsfile.load(args.pins)
    if args.action == "fetch":
        return _do_fetch(args, pins)
    if args.action == "manifest":
        return _do_manifest(args)
    return _do_lock(args, pins)


if __name__ == "__main__":
    sys.exit(main())
