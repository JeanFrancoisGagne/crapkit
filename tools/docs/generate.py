"""Refresh configuration schema and the marked operational facts in docs."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def _module(path: Path):
    spec = importlib.util.spec_from_file_location("test_schedule", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _block(text: str, name: str, value: str) -> str:
    start, end = f"<!-- generated:{name} -->", f"<!-- /generated:{name} -->"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + "\n" + value.rstrip() + "\n" + end + after


def _test_schedule(root: Path) -> str:
    schedule = _module(root / "tools/testing/run.py")
    lines = ["python tools/testing/run.py", *[" ".join(command) for command in schedule.test_commands()]]
    return "```sh\n" + "\n".join(lines) + "\n```"


def generated(root: Path) -> dict[str, str]:
    """Updated complete files, with authored explanations left in place."""
    from crapkit.config_contract import schema

    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    minor = ".".join(version.split(".")[:2])
    support = f"| Version | Supported |\n| --- | --- |\n| {minor}.x | Yes |\n| < {minor} | No. Upgrade. |"
    blocks = {"SECURITY.md": ("version-support", support),
              "CONTRIBUTING.md": ("test-schedule", _test_schedule(root)),
              "AGENTS.md": ("test-schedule", _test_schedule(root))}
    result = {name: _block((root / name).read_text(encoding="utf-8"), *block)
              for name, block in blocks.items()}
    result["crapkit.schema.json"] = json.dumps(schema(), indent=2) + "\n"
    return result


# From 3.14 argparse colours help and usage, into a pipe too when FORCE_COLOR is
# set. Older argparse has no `color` keyword.
PLAIN_HELP = {"color": False} if sys.version_info >= (3, 14) else {}


def _refresh(check: bool) -> list[str]:
    """The generated files whose content is stale, rewritten unless `check`."""
    stale = []
    for name, content in generated(ROOT).items():
        path = ROOT / name
        if path.read_text(encoding="utf-8") != content:
            stale.append(name)
            if not check:
                path.write_text(content, encoding="utf-8")
    return stale


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, **PLAIN_HELP)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = _refresh(args.check)
    if stale:
        print("generated files: " + ", ".join(stale))
    return int(args.check and bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
