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
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import closing
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


def _git_tag(root: Path) -> str:
    return _git(root, "describe", "--tags", "--abbrev=0")


def _gh_release(root: Path, version: str) -> str:
    done = subprocess.run(["gh", "release", "view", f"v{version}", "--json", "url", "--jq", ".url"],
                          cwd=root, capture_output=True, text=True)
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
    tag = git_tag() if git_tag else _git_tag(root)
    url = gh_release(version) if gh_release else _gh_release(root, version)
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


PY = sys.executable
RELEASE_FILES = tuple(sorted({surface.path for surface in SURFACES}
                            | {"CHANGELOG.md", "crapkit-ratchet.tsv"}))


def plan(version: str) -> list:
    """The chain as a list a person reads before running it. Order is what the
    contracts require: the tag before the contract files (two of them read the
    newest tag), verify before anything leaves the machine, PyPI before the
    registry (the registry validates the README PyPI serves)."""
    _parse(version)
    tool = (PY, "tools/release/release.py")
    contracts = (PY, "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly", *CONTRACT_FILES)
    return [
        Step("stage1", "stage1", (
            (*tool, "check", version), (*tool, "bump", version),
            (PY, "-m", "pip", "install", "-e", ".", "--no-deps", "-q"),
            (PY, "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly", "tests/unit/test_version_surface.py"),
            (PY, "-m", "crapkit", "coverage"), (PY, "-m", "crapkit", "ratchet", "seed"),
            (PY, "-m", "crapkit", "ratchet", "prune"),
            ("git", "add", "--", *RELEASE_FILES), ("git", "commit", "-q", "-m", f"Release {version}")),
            note="guard: clean tree and main pushed before the bump"),
        Step("tag", "stage2a", (("git", "tag", f"v{version}"),)),
        Step("contracts", "stage2a", (contracts,), note=f"red: git tag -d v{version} and stop"),
        Step("verify", "verify", ((PY, "-m", "crapkit", "verify"),), background=True,
             note="its own background command: a foreground tool call dies at 600 s"),
        Step("push", "stage2b", (("git", "push", "-q", "origin", "main", f"v{version}"),)),
        Step("pypi", "stage2b", (("@remove-dist",), (PY, "-m", "build", "-q"),
                                 (PY, "-m", "twine", "check", "dist/*"),
                                 (PY, "-m", "twine", "upload", "--non-interactive", "dist/*"))),
        Step("github release", "stage2b", (
            ("gh", "release", "create", f"v{version}", "dist/*", "--title", f"crapkit {version}",
             "--notes-file", f".crapkit/release-notes-{version}.md"),),
            note="the notes file is the changelog section, written by `notes` first"),
        Step("plugin", "stage2b", (("claude", "plugin", "update", "crapkit@crapkit"),)),
        Step("pages", "stage2b", (("gh", "api", "-X", "POST", f"repos/{REPO_SLUG}/pages/builds", "--jq", ".status"),)),
        Step("registry", "registry", (("mcp-publisher", "login", "github"), ("mcp-publisher", "publish")),
             note="device flow; the token lasts about 40 minutes, so publish right after login"),
        Step("glama", "glama", (),
             note="Sync Server on the Repository admin tab; the sync builds and publishes the "
                  "release with the GitHub notes on its own"),
        Step("surfaces", "surfaces", ((*tool, "verify", version),)),
    ]


# --- run one stage -------------------------------------------------------------------

def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if done.returncode:
        raise ReleaseError(done.stderr.strip() or f"git {' '.join(arguments)} failed")
    return done.stdout.strip()


def _clean_main(root: Path) -> str:
    if _git(root, "branch", "--show-current") != "main":
        raise ReleaseError("release requires the main branch")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError("release requires a clean tree, including untracked files")
    return _git(root, "rev-parse", "HEAD")


def _guard_bump(root: Path, version: str) -> dict:
    head = _clean_main(root)
    remote = _git(root, "ls-remote", "--exit-code", "origin", "refs/heads/main")
    if remote.split()[0] != head:
        raise ReleaseError("push main before starting the version bump")
    report = check(root, version)
    if report.problems:
        raise ReleaseError(NL.join(report.problems))
    return {"head": head}


def _release_tree(root: Path, version: str) -> str:
    head = _clean_main(root)
    problems = _surface_problems(root, version)
    if problems:
        raise ReleaseError(NL.join(problems))
    return head


def _guard_contracts(root: Path, version: str) -> dict:
    head = _release_tree(root, version)
    if _git(root, "tag", "--list", f"v{version}"):
        raise ReleaseError(f"v{version} already exists; this stage does not own that tag")
    return {"head": head, "version": version}


def _receipt_path(root: Path) -> Path:
    return root / ".crapkit" / "release-receipt.json"


def _guard_receipt(root: Path, version: str) -> dict:
    head = _release_tree(root, version)
    if _git(root, "rev-parse", f"refs/tags/v{version}^{{commit}}") != head:
        raise ReleaseError(f"v{version} does not point to HEAD")
    try:
        receipt = json.loads(_receipt_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError("run stage2a and verify before publishing; no readable release receipt") from exc
    if not isinstance(receipt, dict):
        raise ReleaseError("release receipt must be an object")
    if (receipt.get("head"), receipt.get("version"), receipt.get("contracts_sha256")) != (
            head, version, _contracts_digest()):
        raise ReleaseError("release receipt belongs to another HEAD or version")
    return receipt


def _contracts_digest() -> str:
    return hashlib.sha256(NL.join(CONTRACT_FILES).encode("utf-8")).hexdigest()


def _ledger_rows(root: Path) -> list:
    path = root / ".crapkit" / "crap.sqlite"
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT id,commit_sha,kind,verdict_ok,findings FROM runs ORDER BY id")]
    except sqlite3.Error as exc:
        raise ReleaseError(f"cannot read the verification ledger: {exc}") from exc


def _last_run(root: Path) -> int:
    rows = _ledger_rows(root)
    return rows[-1]["id"] if rows else 0


def _latest_verification(root: Path) -> dict:
    rows = [row for row in _ledger_rows(root) if row["kind"].startswith("verify")]
    return rows[-1] if rows else {}


def _passing_run(root: Path, head: str, after: int) -> int:
    row = _latest_verification(root)
    expected = (head, "verify", 1, 0)
    observed = tuple(row.get(key) for key in ("commit_sha", "kind", "verdict_ok", "findings"))
    if observed != expected or row.get("id", 0) <= after:
        raise ReleaseError("a new passing full verify at this HEAD is required in the runs ledger")
    return row["id"]


def _guard_publish(root: Path, version: str) -> dict:
    receipt = _guard_receipt(root, version)
    after = receipt.get("verify_after")
    if type(after) is not int or after < 0:
        raise ReleaseError("run the verify stage before publishing this release")
    latest = _passing_run(root, receipt["head"], after)
    if receipt.get("verify_run") != latest:
        raise ReleaseError("run the verify stage before publishing this release")
    return receipt


def _preflight(stage: str, root: Path, version: str) -> dict:
    guards = {"stage1": _guard_bump, "stage2a": _guard_contracts,
              "verify": _guard_receipt, "stage2b": _guard_publish, "registry": _guard_publish}
    return guards[stage](root, version) if stage in guards else {}


def _write_receipt(root: Path, receipt: dict) -> None:
    path = _receipt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True) + NL, encoding="utf-8")
    temporary.replace(path)


def _finish_stage(stage: str, root: Path, version: str, receipt: dict, before: int) -> None:
    if stage not in ("stage2a", "verify"):
        return
    if _release_tree(root, version) != receipt["head"]:
        raise ReleaseError("HEAD changed during the release stage; repeat its checks")
    receipt["contracts_sha256"] = _contracts_digest()
    if stage == "verify":
        receipt["verify_run"] = _passing_run(root, receipt["head"], before)
        receipt["verify_after"] = before
    _write_receipt(root, receipt)


def _dist_path(root: Path) -> Path:
    expected = root.resolve() / "dist"
    if expected.resolve() != expected:
        raise ReleaseError("dist must be a directory inside the release repository")
    return expected


def _remove_dist(root: Path) -> None:
    target = _dist_path(root)
    if target.exists():
        shutil.rmtree(target)


def _dist_artifacts(root: Path) -> list[str]:
    artifacts = sorted(path for path in _dist_path(root).glob("*")
                       if path.name.endswith((".whl", ".tar.gz")))
    _check_artifacts(artifacts)
    return [str(path.relative_to(root.resolve())) for path in artifacts]


def _check_artifacts(artifacts: list[Path]) -> None:
    if not artifacts:
        raise ReleaseError("dist contains no wheel or source archive")
    if any(path.is_symlink() or not path.is_file() for path in artifacts):
        raise ReleaseError("release artifacts must be regular files inside dist")


def _arguments(command: tuple, root: Path) -> list[str]:
    return [value for arg in command for value in
            (_dist_artifacts(root) if arg == "dist/*" else [arg])]


def _execute(command: tuple, root: Path, dry_run: bool) -> None:
    print(f"$ {subprocess.list2cmdline(command)}")
    if dry_run:
        return
    if command == ("@remove-dist",):
        _remove_dist(root)
        return
    subprocess.run(_arguments(command, root), cwd=root, check=True)


def _stage1_files(root: Path) -> None:
    changed = _git(root, "diff", "--name-only", "HEAD", "-z").split("\0")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    unexpected = (set(changed) | set(untracked)) - set(RELEASE_FILES) - {""}
    if unexpected:
        raise ReleaseError("unexpected release edits: " + ", ".join(sorted(unexpected)))


def _run_step(step: Step, root: Path, version: str, dry_run: bool) -> None:
    if step.name == "github release" and not dry_run:
        out = root / ".crapkit" / f"release-notes-{version}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(notes(root, version), encoding="utf-8", newline=NL)
    for command in step.commands:
        if step.stage == "stage1" and not dry_run:
            _stage1_files(root)
        _execute(command, root, dry_run)


def _run_or_untag(step: Step, root: Path, version: str, dry_run: bool,
                  head: str = "") -> None:
    """A red contract run deletes the tag it just made, so the tree never
    carries a tag the contracts refused."""
    try:
        _run_step(step, root, version, dry_run)
    except subprocess.CalledProcessError as exc:
        if step.name == "contracts":
            subprocess.run(["git", "update-ref", "-d", f"refs/tags/v{version}", head], cwd=root)
        raise ReleaseError(f"{step.name} failed: {exc}") from exc


def _run_guarded(stage: str, steps: list, version: str, root: Path) -> None:
    receipt = _preflight(stage, root, version)
    before = _last_run(root) if stage == "verify" else 0
    if stage == "verify":
        receipt.pop("verify_run", None)
        _write_receipt(root, receipt)
    for step in steps:
        _run_or_untag(step, root, version, False, receipt.get("head", ""))
    _finish_stage(stage, root, version, receipt, before)


def run(stage: str, version: str, root: Path, *, dry_run: bool = False) -> None:
    """Check repository proof, then execute a stage; a dry run only prints it."""
    root = root.resolve()
    steps = [s for s in plan(version) if s.stage == stage]
    if not steps:
        raise ReleaseError(f"no stage {stage!r}; stages: stage1, stage2a, verify, stage2b, registry, surfaces")
    if dry_run:
        for step in steps:
            _run_step(step, root, version, True)
        return
    _run_guarded(stage, steps, version, root)


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
            print(f"    $ {subprocess.list2cmdline(command)}")
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
