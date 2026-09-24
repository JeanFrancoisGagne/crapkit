"""Build the deploy images and run deploy cells in them, the same way on a
laptop and in CI.

    python tools/deploy/run.py [--cadence push|nightly|weekly|release|published]
        [--cell ID ...] [--packet KEY] [--os linux|windows|macos]
        [--image cells|core|full|ci|gui] [--native] [--build-only] [--bake]
        [--online] [--repeat N] [--no-cache] [--cache local|gha] [--builder NAME]
        [-n N] [--out DIR]

Linux runs build the image, export the tree under test with export.py into
<out>/in, and run tests/deploy inside the image as uid 1000 with no network.
The build runs on the daemon's own builder when it runs the pinned BuildKit
version, else on a docker-container builder running the pinned BuildKit image
(always with `--cache gha`, which reads and writes the GitHub Actions cache).
An image whose label says it was built from the same Dockerfile, context files
and pins is not rebuilt; `--no-cache` rebuilds cold. The tree under test is
never in an image, so a crapkit source change rebuilds nothing:

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
import hashlib
import json
import os
import shutil
import stat
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
DOCKERIGNORE = ROOT / "tests" / "deploy" / "docker" / "Dockerfile.dockerignore"
INPUTS_LABEL = "org.crapkit.deploy.inputs"
CONTAINER_BUILDER = "crapkit-deploy"
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
            "--platform", pins["images"]["platform"], "--target", image, "-f", str(DOCKERFILE),
            "-t", f"crapkit-deploy:{image}", "--load"]
    argv += [f"--build-arg={key}={value}" for key, value in build_args(pins).items()]
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
    inputs = {"image": image, "args": build_args(pins), "buildkit": pins["images"]["buildkit"],
              "platform": pins["images"]["platform"], "files": files}
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


def disk_usage() -> str:
    return subprocess.run(["docker", "system", "df"], capture_output=True, text=True).stdout


def build(pins: dict, image: str, cache: str, no_cache: bool, out: Path, requested: str | None = None) -> dict:
    """Build one target and record its time, its size, the builder and
    `docker system df` before and after in <out>/build.json. An image already
    built from the same inputs is kept, and the record says so."""
    started, tag = time.monotonic(), f"crapkit-deploy:{image}"
    inputs = inputs_fingerprint(pins, image)
    if unchanged(tag, inputs, no_cache):
        return _record(out, {"image": image, "skipped": "inputs unchanged", "size_bytes": image_size(tag),
                             "seconds": round(time.monotonic() - started, 1)})
    builder = choose_builder(pins, requested, cache)
    before = disk_usage()
    with (out / f"build-{image}.log").open("w", encoding="utf-8") as stream:
        subprocess.run(build_command(pins, image, cache, no_cache, builder, inputs), check=True, stdout=stream,
                       stderr=subprocess.STDOUT)
    return _record(out, {"image": image, "builder": builder, "seconds": round(time.monotonic() - started, 1),
                         "size_bytes": image_size(tag), "no_cache": no_cache, "df_before": before,
                         "df_after": disk_usage()})


def _record(out: Path, record: dict) -> dict:
    _append_json(out / "build.json", record)
    return record


def versions_command(image: str) -> list[str]:
    return ["docker", "run", "--rm", "--network", "none", f"crapkit-deploy:{image}", "versions"]


def check_versions(pins: dict, image: str, out: Path) -> list[str]:
    """What `versions` prints inside the image, held to pins.toml, offline."""
    printed = subprocess.run(versions_command(image), capture_output=True, text=True, check=True).stdout
    (out / f"versions-{image}.txt").write_text(printed, encoding="utf-8")
    return pinsfile.version_problems(pinsfile.expected_versions(pins, image), printed)


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
    parser.add_argument("--builder", default=None,
                        help="buildx builder, created with the pinned BuildKit image when absent (default: the "
                             "daemon's builder when it runs the pinned BuildKit, else " + CONTAINER_BUILDER + ")")
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


def hold_to_pins(pins: dict, image: str, out: Path) -> None:
    """Refuse a freshly built image whose tools differ from pins.toml, and
    untag it: its label would otherwise let the next run skip the build."""
    problems = check_versions(pins, image, out)
    for problem in problems:
        print(f"run: {problem}", file=sys.stderr)
    if problems:
        subprocess.run(["docker", "image", "rm", f"crapkit-deploy:{image}"], capture_output=True)
        raise SystemExit(f"run: crapkit-deploy:{image} does not match pins.toml; the tag is removed")


def _build(args, out: Path) -> bool:
    """Build the image and hold a new one to the pins; True when the
    invocation asked for nothing more."""
    pins = pinsfile.load()
    record = build(pins, args.image, args.cache, args.no_cache, out, args.builder)
    if not record.get("skipped"):
        hold_to_pins(pins, args.image, out)
    return args.build_only


def _image_tag(args, out: Path) -> str:
    return bake(args.image, out) if args.bake else f"crapkit-deploy:{args.image}"


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
