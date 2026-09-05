"""Bump, publish and verify crapkit's version surfaces from one table.

Seven strings in five files say which version this is (pyproject, the package,
three README lines, the plugin manifest, the registry manifest twice), and a
release then has to reach six places (git tag, PyPI, GitHub release, plugin,
Pages, the MCP registry, plus Glama's sync). Eight releases re-scripted that
chain by hand and the surfaces drifted once. The table below is the one place
the surfaces are named; `check` refuses a tree whose surfaces disagree, `bump`
rewrites them all or nothing, `plan` prints the chain in the order the
contracts require, `run` executes one stage of it, and `verify` reads every
surface back through its live API rather than a cached page.

The version to release is an argument, never inferred: whether a change is a
patch or a minor is a judgment on visible behaviour, and it belongs to the
person shipping.

Stdlib only. Nothing here talks to the network except `verify` and the stage
commands `run` spawns.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

DASH = chr(0x2014)
NL = chr(10)
PACKAGE = "crapkit"
REPO_SLUG = "JeanFrancoisGagne/crapkit"
REGISTRY_SEARCH = "https://registry.modelcontextprotocol.io/v0/servers?search=crapkit"
REGISTRY_META = "io.modelcontextprotocol.registry/official"


class ReleaseError(Exception):
    """A refusal the operator reads: which surface, which count, which version."""


class Surface(NamedTuple):
    path: str
    pattern: str  # `{v}` stands for the version
    count: int    # exact occurrences expected in the file


SURFACES = (
    Surface("pyproject.toml", 'version = "{v}"', 1),
    Surface("src/crapkit/__init__.py", '__version__ = "{v}"', 1),
    Surface("README.md", "crapkit {v}" + NL, 1),
    Surface("README.md", "rev: v{v}", 1),
    Surface("README.md", REPO_SLUG + "@v{v}", 2),
    Surface("plugin/.claude-plugin/plugin.json", '"version": "{v}"', 1),
    Surface("server.json", '"version": "{v}"', 2),
)

# The contract files stage 2a runs on the tagged tree. Two of them read the
# newest tag (the README rev contracts), which is why the tag comes first.
CONTRACT_FILES = (
    "tests/unit/test_docs_claims_contract.py", "tests/unit/test_version_metadata_cost.py",
    "tests/unit/test_version_surface.py", "tests/unit/test_fresh_user_docs_contract.py",
    "tests/unit/test_cli_docs_contract.py", "tests/unit/test_handle_docs_contract.py",
    "tests/unit/test_skills_contract.py", "tests/unit/test_ci_install_contract.py",
    "tests/unit/test_precommit_contract.py", "tests/unit/test_schema_contract.py",
    "tests/unit/test_json_schema_version.py", "tests/unit/test_action_contract.py",
    "tests/unit/test_demo_docs_contract.py", "tests/unit/test_registry_manifest.py",
)


class CheckReport(NamedTuple):
    current: str
    problems: list


# --- reading the tree -----------------------------------------------------------

def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8", newline=NL)


def current_version(root: Path) -> str:
    """The version pyproject declares; the other surfaces are checked against it."""
    match = re.search(r'^version = "([^"]+)"$', _read(root, "pyproject.toml"), re.M)
    if not match:
        raise ReleaseError("pyproject.toml declares no version line")
    return match.group(1)


def _parse(version: str) -> tuple:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as exc:
        raise ReleaseError(f"{version!r} is not a dotted integer version") from exc


def _heading(version: str, tail: str) -> str:
    return f"## {version} {DASH} {tail}"


# --- check -----------------------------------------------------------------------

def _surface_problems(root: Path, current: str) -> list:
    problems = []
    for surface in SURFACES:
        needle = surface.pattern.format(v=current)
        found = _read(root, surface.path).count(needle)
        if found != surface.count:
            problems.append(f"{surface.path}: {needle!r} x{found} (expected {surface.count})")
    return problems


def _changelog_problems(root: Path, new: str) -> list:
    found = _read(root, "CHANGELOG.md").count(_heading(new, "unreleased") + NL)
    if found == 1:
        return []
    return [f"CHANGELOG.md: {_heading(new, 'unreleased')!r} x{found} (expected 1)"]


def check(root: Path, new: str, current: str | None = None) -> CheckReport:
    """Every surface at the current version the exact number of times, the
    changelog carrying the new version's unreleased heading, and the new
    version after the current one. Reads only."""
    current = current or current_version(root)
    problems = []
    if _parse(new) <= _parse(current):
        problems.append(f"{new} is not after {current}")
    problems += _surface_problems(root, current)
    problems += _changelog_problems(root, new)
    return CheckReport(current, problems)


# --- bump ------------------------------------------------------------------------

def _rewrite_surfaces(root: Path, path: str, old: str, new: str) -> None:
    text = _read(root, path)
    for surface in SURFACES:
        if surface.path == path:
            text = text.replace(surface.pattern.format(v=old), surface.pattern.format(v=new))
    _write(root, path, text)


def bump(root: Path, new: str, *, date: str | None = None) -> list:
    """Rewrite every surface to `new` and date the changelog heading, or write
    nothing: `check` runs first and a refused tree stays untouched."""
    report = check(root, new)
    if report.problems:
        raise ReleaseError(NL.join(report.problems))
    date = date or datetime.date.today().isoformat()
    changed = []
    for path in dict.fromkeys(surface.path for surface in SURFACES):
        _rewrite_surfaces(root, path, report.current, new)
        changed.append(path)
    changelog = _read(root, "CHANGELOG.md").replace(_heading(new, "unreleased"), _heading(new, date))
    _write(root, "CHANGELOG.md", changelog)
    changed.append("CHANGELOG.md")
    return changed


# --- notes -----------------------------------------------------------------------

def notes(root: Path, version: str) -> str:
    """The changelog section for `version`: the lines between its heading and
    the next one. This is the GitHub release body."""
    lines = _read(root, "CHANGELOG.md").splitlines()
    prefix = f"## {version} {DASH} "
    body = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = line.startswith(prefix)
            continue
        if inside:
            body.append(line)
    if not body:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    return NL.join(body).strip() + NL


# --- verify: every surface through its live API ----------------------------------

class Row(NamedTuple):
    surface: str
    expected: str
    observed: str
    ok: bool


def _urlopen(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        raise ReleaseError(f"{url}: {exc}") from exc


def _git_tag() -> str:
    return subprocess.run(["git", "describe", "--tags", "--abbrev=0"], capture_output=True,
                          text=True, check=True).stdout.strip()


def _gh_release(version: str) -> str:
    done = subprocess.run(["gh", "release", "view", f"v{version}", "--json", "url", "--jq", ".url"],
                          capture_output=True, text=True)
    return done.stdout.strip()


def _row(surface: str, expected: str, observed: str) -> Row:
    return Row(surface, expected, observed, observed == expected)


def _pypi_row(version: str, fetch: Callable) -> Row:
    """The version-specific endpoint: the project-level JSON can serve a
    CDN-stale release list for minutes after an upload."""
    try:
        info = json.loads(fetch(f"https://pypi.org/pypi/{PACKAGE}/{version}/json"))["info"]
        return _row("PyPI", version, info["version"])
    except ReleaseError as exc:
        return Row("PyPI", version, f"unreachable ({exc})", False)


def _latest_registry_entry(fetch: Callable) -> dict | None:
    """The registry's `isLatest` entry for the package, or None when there is none."""
    servers = json.loads(fetch(REGISTRY_SEARCH))["servers"]
    latest = [s for s in servers if s.get("_meta", {}).get(REGISTRY_META, {}).get("isLatest")]
    return latest[0]["server"] if latest else None


def _registry_rows(version: str, fetch: Callable) -> list:
    try:
        server = _latest_registry_entry(fetch)
    except ReleaseError as exc:
        return [Row("registry", version, f"unreachable ({exc})", False)]
    if server is None:
        return [Row("registry", version, "no isLatest entry", False)]
    repo = (server.get("repository") or {}).get("url") or ""
    return [_row("registry", version, server.get("version", "")),
            Row("registry repository", "present", repo or "missing", bool(repo))]


def _file_rows(root: Path, version: str) -> list:
    plugin = json.loads(_read(root, "plugin/.claude-plugin/plugin.json"))["version"]
    manifest = _read(root, "server.json").count(f'"version": "{version}"')
    readme = sum(_read(root, "README.md").count(s.pattern.format(v=version)) == s.count
                 for s in SURFACES if s.path == "README.md")
    return [_row("plugin.json", version, plugin),
            _row("server.json", "2 version fields", f"{manifest} version fields"),
            _row("README", "3 of 3 mentions", f"{readme} of 3 mentions")]


def verify(root: Path, version: str, *, fetch: Callable | None = None,
           git_tag: Callable | None = None, gh_release: Callable | None = None) -> list:
    """One row per surface: what the release should say, what the live surface
    says. The fetchers are arguments so a test can answer for the network."""
    fetch = fetch or _urlopen
    tag = (git_tag or _git_tag)()
    url = (gh_release or _gh_release)(version)
    rows = [_row("git tag", f"v{version}", tag),
            Row("GitHub release", f"v{version}", url or "none", f"v{version}" in url),
            _pypi_row(version, fetch)]
    rows += _registry_rows(version, fetch)
    rows += _file_rows(root, version)
    return rows


# --- plan: the chain in contract order ---------------------------------------------

class Step(NamedTuple):
    name: str
    stage: str
    commands: tuple
    background: bool = False
    note: str = ""


PY = "python"


def plan(version: str) -> list:
    """The chain as a list a person reads before running it. Order is what the
    contracts require: the tag before the contract files (two of them read the
    newest tag), verify before anything leaves the machine, PyPI before the
    registry (the registry validates the README PyPI serves)."""
    _parse(version)  # digits and dots only: the version is spliced into shell commands below
    tool = f"{PY} tools/release/release.py"
    contracts = f"{PY} -m pytest -q -n 0 -p no:randomly " + " ".join(CONTRACT_FILES)
    return [
        Step("stage1", "stage1", (
            f"{tool} check {version}", f"{tool} bump {version}",
            f"{PY} -m pip install -e . --no-deps -q",
            f"{PY} -m pytest -q -n 0 -p no:randomly tests/unit/test_version_surface.py",
            f"{PY} -m crapkit coverage", f"{PY} -m crapkit ratchet seed", f"{PY} -m crapkit ratchet prune",
            "git add -A", f'git commit -q -m "Release {version}"'),
            note="guard: clean tree and main pushed before the bump"),
        Step("tag", "stage2a", (f"git tag v{version}",)),
        Step("contracts", "stage2a", (contracts,), note=f"red: git tag -d v{version} and stop"),
        Step("verify", "verify", (f"{PY} -m crapkit verify",), background=True,
             note="its own background command: a foreground tool call dies at 600 s"),
        Step("push", "stage2b", (f"git push -q origin main v{version}",)),
        Step("pypi", "stage2b", ("rm -rf dist", f"{PY} -m build -q", f"{PY} -m twine check dist/*",
                                 f"{PY} -m twine upload --non-interactive dist/*")),
        Step("github release", "stage2b", (
            f'gh release create v{version} dist/* --title "crapkit {version}" '
            f"--notes-file .crapkit/release-notes-{version}.md",),
            note="the notes file is the changelog section, written by `notes` first"),
        Step("plugin", "stage2b", ("claude plugin update crapkit@crapkit",)),
        Step("pages", "stage2b", (f"gh api -X POST repos/{REPO_SLUG}/pages/builds --jq .status",)),
        Step("registry", "registry", ("mcp-publisher login github", "mcp-publisher publish"),
             note="device flow; the token lasts about 40 minutes, so publish right after login"),
        Step("glama", "glama", (),
             note="Sync Server on the Repository admin tab; the sync builds and publishes the "
                  "release with the GitHub notes on its own"),
        Step("surfaces", "surfaces", (f"{tool} verify {version}",)),
    ]


# --- run one stage -------------------------------------------------------------------

def _shell(command: str, root: Path, dry_run: bool) -> None:
    print(f"$ {command}")
    if dry_run:
        return
    subprocess.run(command, shell=True, cwd=root, check=True)


def _run_step(step: Step, root: Path, version: str, dry_run: bool) -> None:
    if step.name == "github release" and not dry_run:
        out = root / ".crapkit" / f"release-notes-{version}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(notes(root, version), encoding="utf-8", newline=NL)
    for command in step.commands:
        _shell(command, root, dry_run)


def _run_or_untag(step: Step, root: Path, version: str, dry_run: bool) -> None:
    """A red contract run deletes the tag it just made, so the tree never
    carries a tag the contracts refused."""
    try:
        _run_step(step, root, version, dry_run)
    except subprocess.CalledProcessError as exc:
        if step.name == "contracts":
            _shell(f"git tag -d v{version}", root, dry_run)
        raise ReleaseError(f"{step.name} failed: {exc}") from exc


def run(stage: str, version: str, root: Path, *, dry_run: bool = False) -> None:
    """Execute every step of one stage in order."""
    steps = [s for s in plan(version) if s.stage == stage]
    if not steps:
        raise ReleaseError(f"no stage {stage!r}; stages: stage1, stage2a, verify, stage2b, registry, surfaces")
    for step in steps:
        _run_or_untag(step, root, version, dry_run)


# --- the CLI ----------------------------------------------------------------------------

def _print_rows(rows: list) -> bool:
    width = max(len(r.surface) for r in rows)
    for r in rows:
        mark = "ok  " if r.ok else "MISMATCH"
        print(f"{mark:9}{r.surface:<{width}}  expected {r.expected}  observed {r.observed}")
    return all(r.ok for r in rows)


def _print_plan(version: str) -> None:
    for step in plan(version):
        tail = "  [background]" if step.background else ""
        print(f"[{step.stage}] {step.name}{tail}")
        for command in step.commands:
            print(f"    $ {command}")
        if step.note:
            print(f"    note: {step.note}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=tuple(ACTIONS))
    parser.add_argument("target", nargs="?", help="the version to release; for `run`, the stage")
    parser.add_argument("version", nargs="?", help="for `run`: the version")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--date", help="the changelog date `bump` writes (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    return parser


def _cmd_check(root: Path, version: str, args: argparse.Namespace) -> int:
    report = check(root, version)
    print(NL.join(report.problems) or f"ok: every surface at {report.current}, {version} next")
    return 1 if report.problems else 0


def _cmd_bump(root: Path, version: str, args: argparse.Namespace) -> int:
    print(NL.join(f"bumped {path}" for path in bump(root, version, date=args.date)))
    return 0


def _cmd_notes(root: Path, version: str, args: argparse.Namespace) -> int:
    print(notes(root, version), end="")
    return 0


def _cmd_verify(root: Path, version: str, args: argparse.Namespace) -> int:
    return 0 if _print_rows(verify(root, version)) else 1


def _cmd_plan(root: Path, version: str, args: argparse.Namespace) -> int:
    _print_plan(version)
    return 0


def _cmd_run(root: Path, version: str, args: argparse.Namespace) -> int:
    run(args.target, version, root, dry_run=args.dry_run)
    return 0


ACTIONS = {"check": _cmd_check, "bump": _cmd_bump, "notes": _cmd_notes,
           "verify": _cmd_verify, "plan": _cmd_plan, "run": _cmd_run}


def main(argv: list | None = None) -> int:
    args = _parser().parse_args(argv)
    version = args.version if args.action == "run" else args.target
    if not version:
        print("release.py: the version to release is an argument, never inferred", file=sys.stderr)
        return 2
    root = Path(args.repo).resolve()
    try:
        return ACTIONS[args.action](root, version, args)
    except ReleaseError as exc:
        print(f"release.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
