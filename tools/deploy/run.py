"""Build the deploy images and run deploy cells in them, the same way on a
laptop and in CI.

    python tools/deploy/run.py [--cadence push|nightly|weekly|release|published]
        [--cell ID ...] [--packet KEY] [--os linux|windows|macos]
        [--image cells|core|full|ci|gui] [--native] [--build-only] [--bake]
        [--online] [--repeat N] [--no-cache] [--cache local|gha] [-n N] [--out DIR]

Linux runs build the image (daemon build cache on a laptop, the GitHub Actions
cache with `--cache gha`), export the tree under test with export.py into
<out>/in, and run tests/deploy inside the image as uid 1000 with no network:

    docker run --rm --network none --user 1000:1000 -v <out>:/out -e CRAPKIT_DEPLOY=1 \\
        crapkit-deploy:<image> sh /out/in/entry.sh -m '<expr>' -n <N>

`--native` runs the same cells on this machine against the toolchain
toolchain.py installed (Windows and macOS always run native). `--repeat N`
runs the selection N times from fresh containers and fails when any cell's
verdict differs between runs. Output: <out>/junit*.xml, <out>/transcripts/,
<out>/build.json (build times and image sizes).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import export
import pins as pinsfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCKERFILE = ROOT / "tests" / "deploy" / "docker" / "Dockerfile"
ENTRY = ROOT / "tests" / "deploy" / "docker" / "entry.sh"
DEFAULT_OUT = ROOT / ".crapkit" / "deploy-out"
CADENCES = {"push": "push", "nightly": "nightly", "weekly": "weekly", "published": "published",
            "release": "(push or nightly or weekly or online)"}


# --- what to run ----------------------------------------------------------------

def image_markers(image: str) -> str:
    return "(" + " or ".join(f"image_{layer}" for layer in pinsfile.IMAGE_CHAIN[image]) + ")"


def marker_expression(cadence: str, os_name: str, image: str | None, online: bool) -> str:
    """The -m expression: the kit's own tests always, then the cadence's cells
    for this OS and image. Network cells run only in an --online invocation."""
    parts = [CADENCES[cadence], os_name]
    if image:
        parts.append(image_markers(image))
    parts.append("online" if online else "not online")
    return f"kit or ({' and '.join(parts)})"


def pytest_args(args) -> list[str]:
    selected = ["-m", marker_expression(args.cadence, args.os, None if args.native else args.image,
                                        args.online)]
    selected += [f"--deploy-cell={cell}" for cell in args.cell]
    selected += [f"--deploy-packet={args.packet}"] if args.packet else []
    return selected + (["-n", str(args.n)] if args.n else [])


# --- building -----------------------------------------------------------------

def build_args(pins: dict) -> dict[str, str]:
    args = pinsfile.build_args(pins)
    args["ACTIONS"] = " ".join([pins["actions"]["checkout"], pins["actions"]["setup_python"]])
    return args


def cache_flags(cache: str, image: str) -> list[str]:
    if cache != "gha":
        return []
    scope = f"crapkit-deploy-{image}"
    return [f"--cache-from=type=gha,scope={scope}",
            f"--cache-to=type=gha,scope={scope},mode=min,ignore-error=true"]


def build_command(pins: dict, image: str, cache: str, no_cache: bool) -> list[str]:
    argv = ["docker", "buildx", "build", "--progress", "plain", "--platform", pins["images"]["platform"],
            "--target", image, "-f", str(DOCKERFILE), "-t", f"crapkit-deploy:{image}", "--load"]
    argv += [f"--build-arg={key}={value}" for key, value in build_args(pins).items()]
    argv += cache_flags(cache, image) + (["--no-cache"] if no_cache else [])
    return argv + [str(ROOT)]


def image_size(tag: str) -> int | None:
    done = subprocess.run(["docker", "image", "inspect", "-f", "{{.Size}}", tag],
                          capture_output=True, text=True)
    return int(done.stdout.strip()) if done.returncode == 0 else None


def disk_usage() -> str:
    return subprocess.run(["docker", "system", "df"], capture_output=True, text=True).stdout


def build(pins: dict, image: str, cache: str, no_cache: bool, out: Path) -> dict:
    before = disk_usage()
    started = time.monotonic()
    log = out / f"build-{image}.log"
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(build_command(pins, image, cache, no_cache), check=True, stdout=stream,
                       stderr=subprocess.STDOUT)
    record = {"image": image, "seconds": round(time.monotonic() - started, 1),
              "size_bytes": image_size(f"crapkit-deploy:{image}"), "no_cache": no_cache,
              "df_before": before, "df_after": disk_usage()}
    _append_json(out / "build.json", record)
    return record


def _append_json(path: Path, record: dict) -> None:
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    path.write_text(json.dumps([*records, record], indent=2) + "\n", encoding="utf-8")


def bake(image: str, out: Path) -> str:
    """crapkit-deploy:<image>-baked: the image plus <out>/in, for a run with no mount."""
    tag = f"crapkit-deploy:{image}-baked"
    dockerfile = f"FROM crapkit-deploy:{image}\nCOPY --chown=1000:1000 . /opt/deploy/in/\n"
    subprocess.run(["docker", "build", "-q", "-t", tag, "-f", "-", str(out / "in")],
                   input=dockerfile, text=True, check=True, capture_output=True)
    return tag


# --- the tree under test -----------------------------------------------------------

def prepare_out(out: Path) -> Path:
    """<out>/in holds the export and entry.sh. The directory is world-writable
    because the container writes to it as uid 1000, whoever owns it here."""
    shutil.rmtree(out, ignore_errors=True)
    (out / "in").mkdir(parents=True)
    for path in (out, out / "in"):
        os.chmod(path, 0o777)
    export.export(ROOT, out / "in")
    shutil.copyfile(ENTRY, out / "in" / "entry.sh")
    return out


def image_digest(tag: str) -> str:
    done = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag], capture_output=True,
                          text=True)
    return done.stdout.strip()


def container_command(tag: str, out: Path, selected: list[str], online: bool, run_index: int) -> list[str]:
    """One fresh container per run. A baked tag carries <out>/in at /opt/deploy/in."""
    inside = "/opt/deploy/in" if tag.endswith("-baked") else "/out/in"
    argv = ["docker", "run", "--rm", "--user", "1000:1000", "-v", f"{out.resolve()}:/out",
            "-e", "CRAPKIT_DEPLOY=1", "-e", f"CRAPKIT_DEPLOY_IMAGE={tag}",
            "-e", f"CRAPKIT_DEPLOY_IMAGE_DIGEST={image_digest(tag)}", "-e", f"CRAPKIT_DEPLOY_IN={inside}"]
    argv += [] if online else ["--network", "none"]
    return argv + [tag, "sh", f"{inside}/entry.sh", *selected, f"--junitxml=/out/junit-{run_index}.xml"]


# --- native runs -----------------------------------------------------------------

def native_env(toolchain: Path, out: Path) -> dict[str, str]:
    env = dict(os.environ, CRAPKIT_DEPLOY="1", PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
               CRAPKIT_DEPLOY_TOOLCHAIN=str(toolchain), CRAPKIT_DEPLOY_OUT=str(out))
    return env


def _unpack(out: Path, work: Path) -> Path:
    src = work / "src"
    src.mkdir(parents=True)
    with tarfile.open(out / "in" / "tree.tar") as tar:
        tar.extractall(src, filter="tar")
    subprocess.run(["git", "clone", "-q", "--mirror", str(out / "in" / "src.bundle"), str(work / "src.git")],
                   check=True)
    return src


def native_command(runner: str, src: Path, basetemp: Path, selected: list[str], run_index: int,
                   out: Path) -> list[str]:
    return [runner, "-m", "pytest", "tests/deploy", "-p", "no:cacheprovider", "-p", "no:randomly",
            "--basetemp", str(basetemp), f"--junitxml={out / f'junit-{run_index}.xml'}", *selected]


def run_native(args, out: Path, run_index: int) -> int:
    import toolchain as toolchainfile
    chain = toolchainfile.read(toolchainfile.default_root())
    work = out / f"native-{run_index}"
    src = _unpack(out, work)
    subprocess.run([chain["runner_python"], str(src / "tools/deploy/candidate.py"), "--tree",
                    str(out / "in" / "tree.tar"), "--out", str(work / "candidate")], check=True)
    env = native_env(toolchainfile.default_root() / "toolchain.json", out)
    env.update(CRAPKIT_DEPLOY_CANDIDATE=str(work / "candidate"), CRAPKIT_DEPLOY_MIRROR=str(work / "src.git"),
               CRAPKIT_DEPLOY_SRC=str(src))
    command = native_command(chain["runner_python"], src, toolchainfile.basetemp(), pytest_args(args),
                             run_index, out)
    return subprocess.run(command, cwd=src, env=env).returncode


# --- verdicts --------------------------------------------------------------------

def verdicts(junit: Path) -> dict[str, str]:
    """test id -> passed / failed / skipped, from one JUnit file."""
    outcomes = {}
    for case in ElementTree.parse(junit).iter("testcase"):
        kinds = {child.tag for child in case} & {"failure", "error", "skipped"}
        outcomes[f"{case.get('classname')}::{case.get('name')}"] = (
            "failed" if kinds & {"failure", "error"} else "skipped" if kinds else "passed")
    return outcomes


def differing(runs: list[dict[str, str]]) -> list[str]:
    """Cells whose verdict is not the same in every run."""
    ids = sorted(set().union(*runs)) if runs else []
    return [cell for cell in ids if len({run.get(cell, "absent") for run in runs}) > 1]


# --- command line -----------------------------------------------------------------

def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cadence", default="push", choices=sorted(CADENCES))
    parser.add_argument("--cell", action="append", default=[])
    parser.add_argument("--packet")
    parser.add_argument("--os", choices=["linux", "windows", "macos"])
    parser.add_argument("--image", default="core", choices=sorted(pinsfile.IMAGE_CHAIN))
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--bake", action="store_true")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--cache", default="local", choices=["local", "gha"])
    parser.add_argument("-n", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.os = args.os or host_os(args.native)
    return args


def host_os(native: bool) -> str:
    """Containers are Linux; a native run is whatever this machine is."""
    if not native:
        return "linux"
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def _run_once(args, out: Path, run_index: int) -> int:
    if args.native:
        return run_native(args, out, run_index)
    command = container_command(args.tag, out, pytest_args(args), args.online, run_index)
    return subprocess.run(command).returncode


def _compare(out: Path, repeat: int) -> int:
    runs = [verdicts(out / f"junit-{index}.xml") for index in range(repeat)]
    changed = differing(runs)
    for cell in changed:
        print(f"run: {cell} changed verdict across runs: {[run.get(cell, 'absent') for run in runs]}")
    return 1 if changed else 0


def _build(args, out: Path) -> bool:
    """Build the image; True when the invocation asked for nothing more."""
    build(pinsfile.load(), args.image, args.cache, args.no_cache, out)
    if args.build_only:
        return True
    args.tag = bake(args.image, out) if args.bake else f"crapkit-deploy:{args.image}"
    return False


def _verdict(out: Path, repeat: int, codes: list[int]) -> int:
    if repeat > 1:
        return _compare(out, repeat) or max(codes)
    return codes[0]


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    out = args.out.resolve()
    prepare_out(out)
    if not args.native and _build(args, out):
        return 0
    codes = [_run_once(args, out, index) for index in range(args.repeat)]
    return _verdict(out, args.repeat, codes)


if __name__ == "__main__":
    sys.exit(main())
