"""Check the harness profiles against what the real harnesses do.

    python tools/deploy/calibrate.py <harness> [<harness> ...]
    python tools/deploy/calibrate.py --all

Runs the calibrate-all cell in crapkit-deploy:full: each harness named (every
calibratable one with --all) starts crapkit through the shim the way its
profile's config tells it to, and the cell writes what the shim saw as
<out>/observed/<key>.toml: the directory and environment the server started
with, the protocol revision and client name it offered, the tool-name prefix
its model sees, and the harness's version. The pinned harnesses run offline;
Kiro, Devin (windsurf) and Qwen Code have no pin and run at their latest
release in a second, networked run that never fails the command.

calibrate.py copies each observed file to tests/deploy/profiles/observed/,
prints every field where the observation and the profile differ, and exits 1
when any does. Commit the observed files with the profile edits they prompt.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROFILES = ROOT / "tests" / "deploy" / "profiles"
OBSERVED = PROFILES / "observed"
DEFAULT_OUT = ROOT / ".crapkit" / "calibrate-out"
IMAGE = "full"
CELL = "calibrate-all"
# The profile fields an observation can settle. Anything else a cell writes
# (under [seen]) is evidence for a reader, never compared.
COMPARED = {"spawn": ("cwd", "env"), "initialize": ("protocol", "discover_first", "client_name"),
            "limits": ("tool_prefix",)}
# The calibrate-all cell starts every harness in the repo, so a start there
# reads as "workspace"; a profile whose server starts where the harness was
# started ("launch") agrees with it.
SAME_START = {("spawn", "cwd", "launch"): "workspace"}
# No pinned release: installed at their newest, on the network, and never blocking.
LATEST_ONLY = ("kiro", "qwen-code", "windsurf")
# A simulated harness that runs a pinned CLI another profile already calibrates.
SIMULATED = ("copilot-cloud-agent",)


# --- the comparison --------------------------------------------------------------

def _settled(profile: dict, section: str, field: str, seen) -> bool:
    """A field the observation speaks to: observed, one the profile pins down,
    and not one its [real_cli] calibrate_skip says the CLI cannot show (Cline's
    [spawn] describes its VS Code extension, the CLI starts elsewhere)."""
    pinned = profile.get(section, {}).get(field)
    skipped = f"{section}.{field}" in profile.get("real_cli", {}).get("calibrate_skip", [])
    return seen is not None and pinned is not None and not skipped


def _expected(profile: dict, section: str, field: str):
    """What an observation shows for the profile's value (SAME_START)."""
    value = profile[section][field]
    return SAME_START.get((section, field, value), value)


def drift(profile: dict, observed: dict) -> list[str]:
    """section.field: profile value -> observed value, for each field that differs."""
    lines = []
    for section, fields in COMPARED.items():
        for field in fields:
            seen = observed.get(section, {}).get(field)
            if _settled(profile, section, field, seen) and _expected(profile, section, field) != seen:
                lines.append(f"{section}.{field}: profile {profile[section][field]!r}, observed {seen!r}")
    return lines


SCALARS = {bool: lambda value: "true" if value else "false", str: json.dumps}


def _value(value) -> str:
    """One TOML value: a string, a bool, a number or a list of those."""
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    return SCALARS.get(type(value), str)(value)


def dump(data: dict) -> str:
    """An observed file: one [section] per dict, None values left out."""
    blocks = []
    for section, fields in data.items():
        lines = [f"{key} = {_value(value)}" for key, value in fields.items() if value is not None]
        blocks.append(f"[{section}]\n" + "".join(line + "\n" for line in lines))
    return "\n".join(blocks)


def load(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _headless(real_cli: dict) -> bool:
    """A pinned command a :core or :full container runs."""
    return bool(real_cli.get("command")) and real_cli.get("image") in ("core", "full")


def _calibrated(path: Path) -> bool:
    if path.stem in LATEST_ONLY:
        return True
    return path.stem not in SIMULATED and _headless(load(path)["real_cli"])


def calibratable(root: Path = PROFILES) -> list[str]:
    """Profiles whose [real_cli] names a headless check a container can run."""
    return [path.stem for path in sorted(root.glob("*.toml")) if _calibrated(path)]


# --- running the cell ---------------------------------------------------------------

def selection(keys: list[str]) -> list[str]:
    """pytest arguments for the calibrate-all cell of these harnesses."""
    return ["-o", "junit_family=xunit1", "-m", "weekly", f"--deploy-cell={CELL}", "-k", " or ".join(keys)]


def run_in_image(keys: list[str], online: bool, out: Path) -> int:
    """The calibrate-all cell for `keys` in the full image; observed files land in <out>/observed."""
    sys.path.insert(0, str(HERE))
    import pins as pinsfile
    import run

    run.reset_out(out)
    pins = pinsfile.load()
    if not run.build(pins, IMAGE, "local", False, out).get("skipped"):
        run.hold_to_pins(pins, IMAGE, out)
    run.export_into(out)
    command = run.container_command(f"crapkit-deploy:{IMAGE}", out, selection(keys), online, 0)
    return subprocess.run(command).returncode


def observe(runner, keys: list[str], online: bool, out: Path) -> Path:
    """One run of the cell for `keys`, into an out directory holding no older observation."""
    shutil.rmtree(out / "observed", ignore_errors=True)
    runner(keys, online, out)
    return out / "observed"


def collect(written: Path, keys: list[str], observed: Path = OBSERVED) -> list[Path]:
    """Copy the observation each harness in `keys` got into the repo's observed/ directory."""
    observed.mkdir(parents=True, exist_ok=True)
    found = [written / f"{key}.toml" for key in keys if (written / f"{key}.toml").is_file()]
    return [Path(shutil.copy2(path, observed / path.name)) for path in found]


def verdict(profile: dict, observed: dict) -> list[str]:
    """What fails an observation: a harness that never started crapkit (Kiro
    before a login) settles nothing, else each field that drifted."""
    if not observed.get("seen", {}).get("starts"):
        return ["the harness never started crapkit; its cell's transcript says why"]
    return drift(profile, observed)


def report(copied: list[Path], profiles: Path = PROFILES) -> list[str]:
    """Print each harness's verdict; the harnesses whose observation fails."""
    differs = []
    for path in copied:
        lines = verdict(load(profiles / path.name), load(path))
        print(f"{path.stem}: " + ("matches its profile" if not lines else "fails calibration"))
        for line in lines:
            print(f"  {line}")
        differs += [path.stem] if lines else []
    return differs


def unobserved(keys: list[str], copied: list[Path]) -> list[str]:
    """The harnesses whose cell wrote no observation, each printed with where to look."""
    seen = {path.stem for path in copied}
    missing = [key for key in keys if key not in seen]
    for key in missing:
        print(f"{key}: no observation; the cell failed before the harness started crapkit (see its transcript)")
    return missing


def batches(keys: list[str]) -> list[tuple[list[str], bool]]:
    """(keys, online): the pinned harnesses offline, the latest-only ones on the network."""
    split: dict[bool, list[str]] = {False: [], True: []}
    for key in keys:
        split[key in LATEST_ONLY].append(key)
    return [(split[online], online) for online in (False, True) if split[online]]


def parse(argv: list[str] | None, known: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("harness", nargs="*", help="profile keys (tests/deploy/profiles/<key>.toml)")
    parser.add_argument("--all", action="store_true", help="every calibratable profile")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    unknown = sorted(set(args.harness) - set(known))
    if unknown or not (args.all or args.harness):
        parser.error(f"name harnesses from {known} or pass --all" + (f"; unknown: {unknown}" if unknown else ""))
    return args


def main(argv: list[str] | None = None, runner=run_in_image, root: Path = PROFILES) -> int:
    """Exit 1 when a pinned harness disagrees with its profile or was never
    observed; a latest-only harness is reported and never fails the run."""
    known = calibratable(root)
    args = parse(argv, known)
    keys = known if args.all else args.harness
    copied = []
    for index, (batch, online) in enumerate(batches(keys)):
        written = observe(runner, batch, online, args.out / str(index))
        copied += collect(written, batch, root / "observed")
    failing = set(report(copied, root)) | set(unobserved(keys, copied))
    return 1 if failing - set(LATEST_ONLY) else 0


if __name__ == "__main__":
    sys.exit(main())
