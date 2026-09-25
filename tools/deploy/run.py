"""Build the deploy images and run deploy cells in them, the same way on a
laptop and in CI.

    python tools/deploy/run.py [--cadence push|nightly|weekly|release|published]
        [--cell ID ...] [--packet KEY] [--os linux|windows|macos]
        [--image cells|cells-arm64|core|full|full-latest|ci|gui] [--native] [--build-only]
        [--bake] [--online] [--repeat N] [--no-cache] [--cache local|gha] [--builder NAME]
        [--faketime HH:MM:SS|+400d] [-n N] [--out DIR]

Linux runs build the image, export the tree under test with export.py into
<out>/in, and run tests/deploy inside the image as uid 1000 with no network.
The build runs on the daemon's own builder when it runs the pinned BuildKit
version, else on a docker-container builder running the pinned BuildKit image
(always with `--cache gha`, which reads and writes the GitHub Actions cache).
An image whose label says it was built from the same Dockerfile, context files
and pins is not rebuilt; `--no-cache` rebuilds cold. Every image targets
linux/amd64 except cells-arm64, the cells target built for linux/arm64 (the
weekly lin-arm64 job, on deploy.yml's arm64 runner; an x86_64 host builds and
runs it under emulation).
full-latest (the weekly latest-harnesses job, with --online) adds every
harness at its newest release over full, rebuilt once an ISO week, and
writes <out>/latest-drift.txt. The tree under test is never in an image, so
a crapkit source change rebuilds nothing:

    docker run --rm --network none --user 1000:1000 -v <out>:/out -e CRAPKIT_DEPLOY=1 \\
        crapkit-deploy:<image> sh /out/in/entry.sh -m '<expr>' -n <N>

`--native` runs the same cells on this machine against the toolchain
toolchain.py installed (Windows and macOS always run native, and so does
lin-native-start on a bare Linux runner). `--repeat N` runs the selection
N times from fresh containers and fails when any cell's verdict differs
between runs. `--faketime` runs the container under libfaketime (lin-clock):
HH:MM:SS starts the clock at that UTC time today, +400d runs 400 days ahead.
A program that names no dynamic loader keeps the real clock: uv, Crush, act,
the Codex CLI, and the rg and sandbox helpers Codex, the Cursor agent and VS
Code ship (tests/deploy/kit/clock.py REAL_CLOCK lists each); a cell that
starts Claude Code 2.1.281, which never starts under libfaketime, is skipped
at that step. Output: <out>/junit*.xml, <out>/transcripts/, <out>/build.json
(build times and image sizes).

An x86_64 host builds and runs cells-arm64 under QEMU. When it cannot run an
arm64 container, run.py stops before the build; register the handler with

    docker run --privileged --rm tonistiigi/binfmt --install arm64

and run it again. Environment:

    CRAPKIT_DEPLOY_REPO            the repository images are tagged under
                                   (default crapkit-deploy); a second checkout
                                   building other pins on the same daemon sets
                                   its own, so neither replaces the other's tags
    CRAPKIT_DEPLOY_TOOLCHAIN_ROOT  --native: the toolchain toolchain.py put
                                   there, in place of the OS's cache directory
    CRAPKIT_DEPLOY_BASETEMP        --native: pytest's basetemp (C:\\dt on
                                   Windows); pytest empties it when it starts,
                                   so each native run at once needs its own

tools/deploy/README.md has what each image holds, their sizes and build
times, and the fake clock's limits.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import export
import pins as pinsfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCKERFILE = ROOT / "tests" / "deploy" / "docker" / "Dockerfile"
ENTRY = ROOT / "tests" / "deploy" / "docker" / "entry.sh"
DOCKERIGNORE = ROOT / "tests" / "deploy" / "docker" / "Dockerfile.dockerignore"
INPUTS_LABEL = "org.crapkit.deploy.inputs"
# The repository every image is tagged under. A second checkout building other
# pins on the same daemon names its own, so neither replaces the other's tags.
REPO_ENV = "CRAPKIT_DEPLOY_REPO"
CONTAINER_BUILDER = "crapkit-deploy"
DEFAULT_OUT = ROOT / ".crapkit" / "deploy-out"
CADENCES = {"push": "push", "nightly": "nightly", "weekly": "weekly", "published": "published",
            "release": "(push or nightly or weekly or online)"}


def image_tag(image: str) -> str:
    """crapkit-deploy:<image>, under $CRAPKIT_DEPLOY_REPO when set."""
    return f"{os.environ.get(REPO_ENV) or 'crapkit-deploy'}:{image}"


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


# xunit1 carries the <property> lines each cell records; pytest's default
# xunit2 warns once per cell that it may not.
JUNIT = ["-o", "junit_family=xunit1"]


def pytest_args(args) -> list[str]:
    selected = JUNIT + ["-m", marker_expression(args.cadence, args.os, None if args.native else args.image,
                                                args.online)]
    selected += [f"--deploy-cell={cell}" for cell in args.cell]
    selected += [f"--deploy-packet={args.packet}"] if args.packet else []
    return selected + (["-n", str(args.n)] if args.n else [])


# --- building -----------------------------------------------------------------

def iso_week(today: datetime.date | None = None) -> str:
    year, week, _ = (today or datetime.datetime.now(datetime.timezone.utc).date()).isocalendar()
    return f"{year}-W{week:02d}"


def build_args(pins: dict, image: str = "") -> dict[str, str]:
    """Every build arg an image's build passes. Only full-latest gets the
    weekly @latest args, so no pinned image's inputs move with the calendar."""
    args = pinsfile.build_args(pins)
    args["ACTIONS"] = " ".join(pinsfile.act_actions(pins))
    if image.endswith(pinsfile.LATEST):
        args.update(pinsfile.latest_args(pins, iso_week()))
    return args


def cache_flags(cache: str, image: str) -> list[str]:
    if cache != "gha":
        return []
    scope = f"crapkit-deploy-{image}"
    return [f"--cache-from=type=gha,scope={scope}",
            f"--cache-to=type=gha,scope={scope},mode=min,ignore-error=true"]


def builder_command(pins: dict, name: str) -> list[str]:
    """A docker-container builder running the pinned BuildKit image."""
    return ["docker", "buildx", "create", "--name", name, "--driver", "docker-container",
            "--driver-opt", f"image={pins['images']['buildkit']}"]


def ensure_builder(pins: dict, name: str) -> str:
    """The named builder, created with the pinned BuildKit when it does not exist yet."""
    present = subprocess.run(["docker", "buildx", "inspect", name], capture_output=True).returncode == 0
    if not present:
        subprocess.run(builder_command(pins, name), check=True, capture_output=True)
    return name


def pinned_buildkit(pins: dict) -> str:
    """moby/buildkit:v0.33.0@sha256:... -> v0.33.0"""
    return pins["images"]["buildkit"].partition("@")[0].rsplit(":", 1)[1]


def _fields(text: str) -> dict[str, str]:
    pairs = (line.partition(":") for line in text.splitlines())
    return {key.strip(): value.strip() for key, _, value in pairs}


def daemon_builder(pins: dict) -> str | None:
    """The daemon's own builder when it runs the pinned BuildKit version. It
    writes the image straight into the daemon's store, where a docker-container
    builder sends the whole image as a tarball on every build."""
    context = subprocess.run(["docker", "context", "show"], capture_output=True, text=True).stdout.strip()
    fields = _fields(subprocess.run(["docker", "buildx", "inspect", context], capture_output=True, text=True).stdout)
    pinned = fields.get("Driver") == "docker" and fields.get("BuildKit version") == pinned_buildkit(pins)
    return context if pinned else None


def choose_builder(pins: dict, requested: str | None, cache: str) -> str:
    """A named builder as asked; else the daemon's when it runs the pinned
    BuildKit; else a docker-container builder running the pinned image. The
    GitHub Actions cache always takes the container builder."""
    if requested:
        return ensure_builder(pins, requested)
    daemon = daemon_builder(pins) if cache != "gha" else None
    return daemon or ensure_builder(pins, CONTAINER_BUILDER)


def build_command(pins: dict, image: str, cache: str, no_cache: bool, builder: str = CONTAINER_BUILDER,
                  inputs: str = "") -> list[str]:
    argv = ["docker", "buildx", "build", "--builder", builder, "--progress", "plain",
            "--platform", pinsfile.platform(pins, image), "--target", pinsfile.target(image), "-f", str(DOCKERFILE),
            "-t", image_tag(image), "--load"]
    argv += [f"--build-arg={key}={value}" for key, value in build_args(pins, image).items()]
    argv += [f"--label={INPUTS_LABEL}={inputs}"] if inputs else []
    argv += cache_flags(cache, image) + (["--no-cache"] if no_cache else [])
    return argv + [str(ROOT)]


# --- a no-change rebuild ----------------------------------------------------------
# A docker-container builder hands every build to the daemon as one tarball, so
# even a build that hits the cache on every layer spends minutes on --load. The
# image carries a hash of what its build reads; when the hash is unchanged the
# image is already what a build would produce and run.py skips the build.

def context_rules(text: str) -> list[tuple[bool, str]]:
    """Dockerfile.dockerignore as (included, path) lines in file order. The
    reader knows `*` and literal paths, the only forms the file uses."""
    lines = (raw.strip() for raw in text.splitlines())
    return [(line.startswith("!"), line.lstrip("!").rstrip("/")) for line in lines
            if line and not line.startswith("#")]


def in_context(relative: str, rules: list[tuple[bool, str]]) -> bool:
    """Docker's rule: the last line that matches the path decides."""
    verdict = True
    for included, pattern in rules:
        if pattern == "*" or relative == pattern or relative.startswith(pattern + "/"):
            verdict = included
    return verdict


def _files_under(path: Path) -> list[Path]:
    return [path] if path.is_file() else [child for child in path.rglob("*") if child.is_file()]


def _reincluded(rules: list[tuple[bool, str]]) -> list[str]:
    return [pattern for included, pattern in rules if included and pattern != "*"]


def _candidates(root: Path, rules: list[tuple[bool, str]]) -> list[Path]:
    """Every file under a path some line lets back in."""
    return sorted({path for pattern in _reincluded(rules) for path in _files_under(root / pattern)})


def context_files(root: Path = ROOT) -> list[Path]:
    """The files BuildKit reads from the build context."""
    rules = context_rules((root / DOCKERIGNORE.relative_to(ROOT)).read_text(encoding="utf-8"))
    return [path for path in _candidates(root, rules) if in_context(path.relative_to(root).as_posix(), rules)]


def inputs_fingerprint(pins: dict, image: str, root: Path = ROOT) -> str:
    """What a build of `image` reads: the target, every build arg, the pinned
    BuildKit and platform, and the bytes of each file in the build context."""
    files = [[path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
             for path in context_files(root)]
    inputs = {"image": image, "args": build_args(pins, image), "buildkit": pins["images"]["buildkit"],
              "platform": pinsfile.platform(pins, image), "files": files}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()


def image_label(tag: str) -> str | None:
    template = '{{index .Config.Labels "' + INPUTS_LABEL + '"}}'
    done = subprocess.run(["docker", "image", "inspect", "-f", template, tag], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def unchanged(tag: str, inputs: str, no_cache: bool) -> bool:
    """The image at `tag` was built from exactly these inputs, and the run did
    not ask for a cold build."""
    return not no_cache and image_label(tag) == inputs


def image_size(tag: str) -> int | None:
    done = subprocess.run(["docker", "image", "inspect", "-f", "{{.Size}}", tag],
                          capture_output=True, text=True)
    return int(done.stdout.strip()) if done.returncode == 0 else None


def unpack(tag: str) -> None:
    """Run an image built for another platform once. Docker Desktop's image
    store unpacks such an image only on its first run and reports its
    compressed size until then (508 MB for the 1.90 GB cells-arm64); --load
    unpacks an image of the host's own platform."""
    flags = pinsfile.platform_flags(tag)
    if flags:
        subprocess.run(["docker", "run", "--rm", *flags, "--network", "none", "--entrypoint", "true", tag],
                       capture_output=True)


def disk_usage() -> str:
    return subprocess.run(["docker", "system", "df"], capture_output=True, text=True).stdout


QEMU_FIX = "docker run --privileged --rm tonistiigi/binfmt --install arm64"


def emulation_problem(pins: dict, image: str, runner=subprocess.run) -> str | None:
    """Why this Docker host cannot run the image's platform, or None. An x86_64
    host with no QEMU handler registered fails the cells-arm64 build ten minutes
    in with 'Exec format error'; one `docker run` of the pinned Debian says so
    first. The pinned platform is the host's own and needs no check."""
    platform = pinsfile.platform(pins, image)
    if platform == pins["images"]["platform"]:
        return None
    done = runner(["docker", "run", "--rm", "--platform", platform, pins["images"]["debian"], "true"],
                  capture_output=True, text=True)
    if done.returncode == 0:
        return None
    said = (done.stderr.strip().splitlines() or ["no output"])[-1]
    return (f"run: this Docker host cannot run {platform} containers ({said}); register QEMU once with "
            f"`{QEMU_FIX}`, or build {image} on an arm64 host")


BUILD_TAIL = 30


def build_failure(pins: dict, image: str, log: Path, code: int) -> str:
    """What a failed build tells its user: where the whole log is, its last
    lines, and, when an emulated platform can no longer run, that the QEMU
    handler went away during the build (its log says only 'Exec format error')."""
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-BUILD_TAIL:]
    said = [f"run: building {image} exited {code}; the whole log is {log}", *tail]
    problem = emulation_problem(pins, image)
    lost = [f"run: the QEMU handler went away during the build: {problem}"] if problem else []
    return "\n".join(said + lost)


def build(pins: dict, image: str, cache: str, no_cache: bool, out: Path, requested: str | None = None) -> dict:
    """Build one target and record its time, its size, the builder and
    `docker system df` before and after in <out>/build.json. An image already
    built from the same inputs is kept, and the record says so."""
    started, tag = time.monotonic(), image_tag(image)
    inputs = inputs_fingerprint(pins, image)
    if unchanged(tag, inputs, no_cache):
        return _record(out, {"image": image, "skipped": "inputs unchanged", "size_bytes": image_size(tag),
                             "seconds": round(time.monotonic() - started, 1)})
    problem = emulation_problem(pins, image)
    if problem:
        raise SystemExit(problem)
    builder = choose_builder(pins, requested, cache)
    before = disk_usage()
    with (out / f"build-{image}.log").open("w", encoding="utf-8") as stream:
        done = subprocess.run(build_command(pins, image, cache, no_cache, builder, inputs), stdout=stream,
                              stderr=subprocess.STDOUT)
    if done.returncode != 0:
        raise SystemExit(build_failure(pins, image, out / f"build-{image}.log", done.returncode))
    seconds = round(time.monotonic() - started, 1)
    unpack(tag)
    return _record(out, {"image": image, "builder": builder, "seconds": seconds, "size_bytes": image_size(tag),
                         "no_cache": no_cache, "df_before": before, "df_after": disk_usage()})


def _record(out: Path, record: dict) -> dict:
    _append_json(out / "build.json", record)
    return record


def versions_command(image: str) -> list[str]:
    tag = image_tag(image)
    return ["docker", "run", "--rm", "--network", "none", *pinsfile.platform_flags(tag), tag, "versions"]


def check_versions(pins: dict, image: str, out: Path) -> list[str]:
    """What `versions` prints inside the image, held to pins.toml, offline."""
    # UTF-8 whatever this host's code page is: Zed prints an en dash.
    printed = subprocess.run(versions_command(image), capture_output=True, check=True).stdout.decode("utf-8")
    (out / f"versions-{image}.txt").write_text(printed, encoding="utf-8")
    return pinsfile.version_problems(pinsfile.expected_versions(pins, image), printed)


def _append_json(path: Path, record: dict) -> None:
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    path.write_text(json.dumps([*records, record], indent=2) + "\n", encoding="utf-8")


def bake(image: str, out: Path) -> str:
    """crapkit-deploy:<image>-baked: the image plus <out>/in, for a run with no mount."""
    tag = image_tag(f"{image}-baked")
    dockerfile = f"FROM {image_tag(image)}\nCOPY --chown=1000:1000 . /opt/deploy/in/\n"
    subprocess.run(["docker", "build", "-q", *pinsfile.platform_flags(tag), "-t", tag, "-f", "-", str(out / "in")],
                   input=dockerfile, text=True, check=True, capture_output=True)
    return tag


# --- the tree under test -----------------------------------------------------------

def _writable_then_retry(function, path, _info) -> None:
    """git writes its objects read-only, and Windows refuses to delete those."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=_writable_then_retry)


def reset_out(out: Path) -> Path:
    """An empty <out>, world-writable because the container writes to it as
    uid 1000, whoever owns it here."""
    remove(out)
    out.mkdir(parents=True)
    os.chmod(out, 0o777)
    return out


def export_into(out: Path) -> Path:
    """<out>/in: the tree under test (export.py) and entry.sh."""
    (out / "in").mkdir()
    os.chmod(out / "in", 0o777)
    export.export(ROOT, out / "in")
    shutil.copyfile(ENTRY, out / "in" / "entry.sh")
    return out


def prepare_out(out: Path) -> Path:
    return export_into(reset_out(out))


def image_digest(tag: str) -> str:
    done = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag], capture_output=True,
                          text=True)
    return done.stdout.strip()


def container_command(tag: str, out: Path, selected: list[str], online: bool, run_index: int,
                      mounts: list[str] = ()) -> list[str]:
    """One fresh container per run. A baked tag carries <out>/in at /opt/deploy/in."""
    inside = "/opt/deploy/in" if tag.endswith("-baked") else "/out/in"
    argv = ["docker", "run", "--rm", "--user", "1000:1000", "-v", f"{out.resolve()}:/out", *mounts,
            "-e", "CRAPKIT_DEPLOY=1", "-e", f"CRAPKIT_DEPLOY_IMAGE={tag}",
            "-e", f"CRAPKIT_DEPLOY_IMAGE_DIGEST={image_digest(tag)}", "-e", f"CRAPKIT_DEPLOY_IN={inside}"]
    argv += pinsfile.platform_flags(tag) + ([] if online else ["--network", "none"])
    return argv + [tag, "sh", f"{inside}/entry.sh", *selected, f"--junitxml=/out/junit-{run_index}.xml"]


# --- the clock (lin-clock) ---------------------------------------------------------
# `--faketime SPEC` runs every dynamically linked process in the container under
# libfaketime, which the images hold (Debian's faketime package). It is loaded
# through /etc/ld.so.preload and set through /etc/faketimerc, two files mounted
# read-only, and not through LD_PRELOAD and FAKETIME: the kit builds each
# cell's environment from an allowlist, so a crapkit a cell starts would run on
# the real clock. Every such process gets the same offset, so the run's clock
# moves as the real one does. A program that names no dynamic loader never
# reads /etc/ld.so.preload and keeps the real clock: uv, Crush, act, the Codex
# CLI's musl build, and the rg and sandbox helpers that Codex, the Cursor agent
# and VS Code ship (tests/deploy/kit/clock.py REAL_CLOCK lists each by file
# name, and a kit test holds the list to each image's ELF headers). A Rust
# binary linked against glibc, such as Goose or prek, reads the moved clock.
# Claude Code 2.1.281 deadlocks under libfaketime before main, so the kit skips
# a cell at its first start of it (kit/clock.py CANNOT_START).

FAKETIME_LIBRARY = "/usr/lib/{triplet}/faketime/libfaketime.so.1"
TRIPLETS = {"linux/amd64": "x86_64-linux-gnu", "linux/arm64": "aarch64-linux-gnu"}
CLOCK_TIME = re.compile(r"\d\d:\d\d:\d\d")


def faketime_offset(spec: str, now: datetime.datetime) -> str:
    """What /etc/faketimerc holds for --faketime SPEC. HH:MM:SS starts the run's
    clock at that UTC time today, as a signed offset in seconds from `now`; any
    other SPEC is libfaketime's own (+400d is 400 days ahead)."""
    if not CLOCK_TIME.fullmatch(spec):
        return spec
    start = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(spec), datetime.timezone.utc)
    return f"{round((start - now).total_seconds()):+d}"


def faketime_mounts(out: Path, spec: str | None, platform: str, now: datetime.datetime | None = None) -> list[str]:
    """The `docker run` flags that put a run under libfaketime, or none."""
    if not spec:
        return []
    clock = out / "faketime"
    clock.mkdir(exist_ok=True)
    offset = faketime_offset(spec, now or datetime.datetime.now(datetime.timezone.utc))
    library = FAKETIME_LIBRARY.format(triplet=TRIPLETS[platform])
    (clock / "ld.so.preload").write_text(library + "\n", encoding="utf-8", newline="\n")
    (clock / "faketimerc").write_text(offset + "\n", encoding="utf-8", newline="\n")
    print(f"run: --faketime {spec}: every dynamically linked process runs at libfaketime offset {offset} "
          "(a program with no dynamic loader, such as uv, Codex or Crush, keeps the real clock: "
          "tests/deploy/kit/clock.py REAL_CLOCK); a cell stops, skipped, at the first start of a release "
          "tests/deploy/kit/clock.py names (Claude Code 2.1.281)")
    return ["-v", f"{(clock / 'ld.so.preload').resolve()}:/etc/ld.so.preload:ro",
            "-v", f"{(clock / 'faketimerc').resolve()}:/etc/faketimerc:ro"]


# --- native runs -----------------------------------------------------------------

def native_env(toolchain: Path, out: Path) -> dict[str, str]:
    env = dict(os.environ, CRAPKIT_DEPLOY="1", PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
               CRAPKIT_DEPLOY_TOOLCHAIN=str(toolchain), CRAPKIT_DEPLOY_OUT=str(out))
    return env


def _unpack(out: Path, work: Path) -> Path:
    src = work / "src"
    src.mkdir(parents=True)
    export.unpack_tar(out / "in" / "tree.tar", src)
    subprocess.run(["git", "-c", "init.defaultBranch=main", "clone", "-q", "--mirror",
                    str(out / "in" / "src.bundle"), str(work / "src.git")], check=True)
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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cadence", default="push", choices=sorted(CADENCES),
                        help="the cadence whose cells run (default push); release is push, nightly, weekly and "
                             "online together")
    parser.add_argument("--cell", action="append", default=[], metavar="ID",
                        help="run this cell; repeat for more")
    parser.add_argument("--packet", metavar="KEY", help="run one packet's cells, e.g. deploy-kit")
    parser.add_argument("--os", choices=["linux", "windows", "macos"],
                        help="run the cells marked for this OS (default: linux in a container, this machine's OS "
                             "with --native)")
    parser.add_argument("--image", default="core", choices=sorted(pinsfile.IMAGE_CHAIN),
                        help="the image to build and run in (default core); cells-arm64 is linux/arm64, "
                             "full-latest adds every harness at its newest release")
    parser.add_argument("--native", action="store_true",
                        help="run the cells on this machine against the toolchain toolchain.py installed")
    parser.add_argument("--build-only", action="store_true",
                        help="build the image, or keep it when its inputs are unchanged, and run nothing")
    parser.add_argument("--bake", action="store_true",
                        help="copy the exported tree into an <image>-baked image and run the cells from that copy")
    parser.add_argument("--online", action="store_true",
                        help="run the cells marked online, and only those, with the network on")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="run the selection N times from fresh containers; exit 1 when a verdict differs")
    parser.add_argument("--no-cache", action="store_true", help="build cold")
    parser.add_argument("--cache", default="local", choices=["local", "gha"],
                        help="BuildKit's layer cache: local (default) or gha, the GitHub Actions cache")
    parser.add_argument("--builder", default=None,
                        help="buildx builder, created with the pinned BuildKit image when absent (default: the "
                             "daemon's builder when it runs the pinned BuildKit, else " + CONTAINER_BUILDER + ")")
    parser.add_argument("--faketime", metavar="HH:MM:SS|SPEC",
                        help="run the container under libfaketime: HH:MM:SS starts the clock at that UTC time "
                             "today, any other value is libfaketime's own (+400d); a program with no dynamic "
                             "loader, such as uv, Codex or Crush, keeps the real clock, and a cell skips at a "
                             "release that cannot start under it "
                             "(tests/deploy/kit/clock.py)")
    parser.add_argument("-n", type=int, default=0, metavar="N",
                        help="pytest-xdist workers (default 0: one process)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, metavar="DIR",
                        help="where the JUnit files, transcripts and build records land (default "
                             ".crapkit/deploy-out)")
    return parser


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.faketime and args.native:
        parser.error("--faketime runs cells in an image; a native run has no libfaketime to load")
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
    clock = faketime_mounts(out, args.faketime, pinsfile.platform(pinsfile.load(), args.image))
    command = container_command(args.tag, out, pytest_args(args), args.online, run_index, clock)
    return subprocess.run(command).returncode


def _compare(out: Path, repeat: int) -> int:
    runs = [verdicts(out / f"junit-{index}.xml") for index in range(repeat)]
    changed = differing(runs)
    for cell in changed:
        print(f"run: {cell} changed verdict across runs: {[run.get(cell, 'absent') for run in runs]}")
    return 1 if changed else 0


def hold_to_pins(pins: dict, image: str, out: Path) -> None:
    """Refuse a freshly built image whose tools differ from pins.toml, and
    untag it: its label would otherwise let the next run skip the build."""
    problems = check_versions(pins, image, out)
    for problem in problems:
        print(f"run: {problem}", file=sys.stderr)
    if problems:
        subprocess.run(["docker", "image", "rm", image_tag(image)], capture_output=True)
        raise SystemExit(f"run: {image_tag(image)} does not match pins.toml; the tag is removed")


def report_latest(pins: dict, image: str, out: Path) -> list[str]:
    """For full-latest, each harness whose newest release prints other than its
    pin, printed and written to <out>/latest-drift.txt for the job summary."""
    if not image.endswith(pinsfile.LATEST):
        return []
    drift = pinsfile.latest_drift(pins, (out / f"versions-{image}.txt").read_text(encoding="utf-8"))
    (out / "latest-drift.txt").write_text("".join(line + "\n" for line in drift), encoding="utf-8")
    for line in drift or ["every harness's newest release is its pin"]:
        print(f"run: latest: {line}")
    return drift


def _build(args, out: Path) -> bool:
    """Build the image and hold a new one to the pins; True when the
    invocation asked for nothing more. full-latest is held to its pinned
    tools and reports what its @latest ones print, built or not."""
    pins = pinsfile.load()
    record = build(pins, args.image, args.cache, args.no_cache, out, args.builder)
    if not record.get("skipped") or args.image.endswith(pinsfile.LATEST):
        hold_to_pins(pins, args.image, out)
    report_latest(pins, args.image, out)
    return args.build_only


def _image_tag(args, out: Path) -> str:
    return bake(args.image, out) if args.bake else image_tag(args.image)


def _verdict(out: Path, repeat: int, codes: list[int]) -> int:
    if repeat > 1:
        return _compare(out, repeat) or max(codes)
    return codes[0]


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    out = reset_out(args.out.resolve())
    if not args.native and _build(args, out):
        return 0
    export_into(out)
    args.tag = None if args.native else _image_tag(args, out)
    codes = [_run_once(args, out, index) for index in range(args.repeat)]
    return _verdict(out, args.repeat, codes)


if __name__ == "__main__":
    sys.exit(main())
