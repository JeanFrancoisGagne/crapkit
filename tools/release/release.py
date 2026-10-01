"""Bump, publish and verify crapkit's version surfaces from one table.

Twenty-one strings in eleven files say which version this is (pyproject, the
package, five README strings, the handbook's Action pin, the Claude Code and
Codex plugin manifests, the registry manifest twice, and the release tag nine
Codex marketplace lines pin across README, adoption, upgrading, the handbook,
the onboarding skill and agent-json.md, two of them inside doctor's quoted
no-install line), and
a release then has to reach six places (git tag, PyPI, GitHub release, plugin,
Pages, the MCP registry, plus Glama's sync). Eight releases re-scripted that
chain by hand and the surfaces drifted once. The table below is the one place
the surfaces are named; `check` refuses a tree whose surfaces disagree, `bump`
rewrites them all or nothing, `plan` prints the chain in the order the
contracts require, `run` executes one stage of it, and `verify` reads every
surface back through its live API rather than a cached page.

The version to release is an argument, never inferred: whether a change is a
patch or a minor is a judgment on visible behaviour, and it belongs to the
person shipping.

Stdlib only. `verify` and publishing stages read remote state. Only explicit
stage commands publish; `plan` and dry runs make no requests or writes.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import itertools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from contextlib import closing
from typing import Callable, NamedTuple

DASH = chr(0x2014)
NL = chr(10)
PACKAGE = "crapkit"
REPO_SLUG = "JeanFrancoisGagne/crapkit"
GITHUB_REPO = f"github.com/{REPO_SLUG}"
PYPI_UPLOAD_URL = "https://upload.pypi.org/legacy/"
RELEASE_DIST = ".crapkit/release-dist"
REGISTRY_SEARCH = "https://registry.modelcontextprotocol.io/v0/servers?search=crapkit"
REGISTRY_META = "io.modelcontextprotocol.registry/official"
REGISTRY_NAME = f"io.github.{REPO_SLUG}"
REGISTRY_REPOSITORY = f"https://github.com/{REPO_SLUG}"
PAGES_LATEST = f"https://api.github.com/repos/{REPO_SLUG}/pages/builds/latest"
GLAMA_SERVER = f"https://glama.ai/mcp/servers/{REPO_SLUG}"
GITHUB_API = "https://api.github.com/"
# Stage 2b runs these through this interpreter (`python -m build`, `python -m
# twine`) before the push, so `check` refuses an interpreter that cannot import one.
RELEASE_TOOLING = ("build", "twine")
# Seconds a live read may take. The registry's search answered in 78 and 87
# seconds on a cold cache (2026-09-23), so its reads get their own bound.
READ_TIMEOUT = 20
REGISTRY_READ_TIMEOUT = 120
# Seconds between readbacks of a surface that was just written.
READBACK_PAUSE = 5
# Reads of a just-written surface before it counts as unconfirmed: 55 seconds.
# PyPI outlasted four reads (15 seconds) on both 0.7.3 and 0.7.4.
READBACK_ATTEMPTS = 12
# Stands in a command for the `gh auth token` value. The command echo prints this
# text; only the argv handed to the process carries the token.
GH_TOKEN_ARG = "$(gh auth token)"
# GitHub refuses a release body longer than this many characters. Stage 2b
# creates the release after the tag, the push and PyPI, so `check` refuses a
# changelog section over it before stage 1 bumps anything.
GITHUB_BODY_LIMIT = 125_000
# This repository's py lane runs both unit and E2E suites. A verdict only
# compares failures with a baseline; publication also requires passing tests.
RELEASE_TEST_LANES = frozenset({"py"})


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
    Surface("docs/handbook.html", REPO_SLUG + "@v{v}", 1),
    # README's Route 4 CI job installs this release by number.
    Surface("README.md", PACKAGE + "=={v}", 1),
    Surface("plugin/.claude-plugin/plugin.json", '"version": "{v}"', 1),
    Surface("plugin/.codex-plugin/plugin.json", '"version": "{v}"', 1),
    Surface("server.json", '"version": "{v}"', 2),
    # The Codex marketplace lines pin the release tag: Codex reinstalls an
    # unpinned marketplace's plugins from main at every start.
    Surface("README.md", "--ref v{v}", 2),
    Surface("docs/adoption.md", "--ref v{v}", 1),
    Surface("docs/upgrading.md", "--ref v{v}", 1),
    Surface("docs/handbook.html", "--ref v{v}", 2),
    # The onboarding skill and agent-json.md also quote doctor's no-install
    # line, which names the Codex line at this release's tag.
    Surface("plugin/skills/crapkit-onboard/SKILL.md", "--ref v{v}", 2),
    Surface("docs/agent-json.md", "--ref v{v}", 1),
)

# The deploy suite (.github/workflows/deploy.yml) installs the candidate the way
# users do, through every channel and harness it models. A release waits for a
# green run of its release cadence at the tag commit, keyed on that commit's
# git tree id; the published cadence
# then repeats the install from the real surfaces once they hold it.
DEPLOY_WORKFLOW = "deploy.yml"
DEPLOY_RELEASE_CADENCE = "release"
DEPLOY_PUBLISHED_CADENCE = "published"

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
    "tests/unit/test_generated_guidance.py", "tests/unit/test_plugin_install_lines.py",
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
    """The unreleased heading once, then a section GitHub takes as a release body.
    The size is read only past the heading: `notes` raises on a missing section."""
    found = _read(root, "CHANGELOG.md").count(_heading(new, "unreleased") + NL)
    if found != 1:
        return [f"CHANGELOG.md: {_heading(new, 'unreleased')!r} x{found} (expected 1)"]
    size = len(notes(root, new))
    if size <= GITHUB_BODY_LIMIT:
        return []
    return [f"CHANGELOG.md: the {new} section is {size} characters; a GitHub release body "
            f"takes at most {GITHUB_BODY_LIMIT}"]


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


# --- preflight -------------------------------------------------------------------

def _module_origin(name: str) -> str | None:
    spec = importlib.util.find_spec(name)
    return spec.origin if spec else None


def _keyring_has(url: str) -> bool:
    """Twine's other credential source. Imported lazily: keyring is not stdlib and
    the rest of this tool must run without it."""
    try:
        import keyring
    except ImportError:
        return False
    try:
        return keyring.get_credential(url, None) is not None
    except keyring.errors.KeyringError:
        return False


def _twine_credential():
    """Twine's own order: the environment first, then keyring for the upload URL."""
    if os.environ.get("TWINE_USERNAME") and os.environ.get("TWINE_PASSWORD"):
        return True
    return _keyring_has(PYPI_UPLOAD_URL)


def _tooling_problems(locate: Callable) -> list[str]:
    missing = ", ".join(name for name in RELEASE_TOOLING if locate(name) is None)
    if not missing:
        return []
    return [f"the release interpreter cannot import {missing}; stage 2b runs "
            f"`python -m build` and `python -m twine` before the push, so install {missing} "
            "into the environment that runs release.py"]


def _credential_problems(credential: Callable) -> list[str]:
    if credential():
        return []
    return ["no PyPI credential is reachable: twine ignores .pypirc when --repository-url "
            "is passed, so set TWINE_USERNAME and TWINE_PASSWORD, or store the token in keyring"]


def preflight(*, locate: Callable | None = None,
              credential: Callable | None = None) -> list[str]:
    """What must be true before stage 1 builds or pushes anything.

    Every fault of the 0.7.2 release fired after PyPI and the GitHub release were
    already public, because nothing proved the machine first. Both checks here
    take milliseconds. A missing credential costs a published half-release when
    skipped; a missing build or twine stops stage 2b before the push, and check
    catches it before stage 1."""
    return (_tooling_problems(locate or _module_origin)
            + _credential_problems(credential or _twine_credential))


# --- the deploy preflight ------------------------------------------------------------

DEPLOY_KIT = "tools/deploy/candidate.py"


def gates_deploy(root: Path) -> bool:
    """A tree that carries the deploy kit is released only past its release cadence."""
    return (root / DEPLOY_KIT).is_file()


def deploy_preflight(root: Path, *, which: Callable | None = None,
                     token: Callable | None = None) -> list[str]:
    """check's deploy line: what stage 1 can know before there is a commit to test.

    `check` once demanded a green release-cadence run at the pre-bump HEAD, a
    commit the release never tags, so that run proved nothing about the release.
    The deploy stage now runs the cadence on the tag commit. It dispatches and
    watches the run through gh, and stage 2b reads the run back with gh's token,
    so a gh that is missing or logged out stops the release here, before the bump.
    A bare "gh" that is missing fails on Windows with a WinError that names no
    program, so the line names it."""
    if not gates_deploy(root):
        return []
    problem = _gh_problem(which or shutil.which, token or _gh_token)
    return [f"deploy gate: {problem}"] if problem else []


def _gh_problem(which: Callable, token: Callable) -> str | None:
    if which("gh") is None:
        return (f"gh is not on PATH, so the deploy stage cannot dispatch {DEPLOY_WORKFLOW} or read "
                "its runs; install the GitHub CLI and run gh auth login")
    if not token():
        return (f"gh auth token returned nothing, so the deploy stage cannot dispatch "
                f"{DEPLOY_WORKFLOW} or read its runs; run gh auth login")
    return None


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


_GH_TOKEN: dict = {}


def _read_gh_token() -> str:
    tool = shutil.which("gh")
    if tool is None:
        return ""
    try:
        done = subprocess.run((tool, "auth", "token"), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _gh_token() -> str:
    """Read once: a release makes many readbacks and each would spawn `gh`."""
    if "token" not in _GH_TOKEN:
        _GH_TOKEN["token"] = _read_gh_token()
    return _GH_TOKEN["token"]


def _api_request(url: str) -> urllib.request.Request:
    """GitHub's Pages API answers 404, not 403, to an anonymous reader, so an
    unauthenticated readback reported a built site as absent and the stage could
    never confirm the build it had just requested. The publishing commands beside
    it are authenticated `gh`; the reads now carry the same credential. Only
    GitHub gets it: PyPI, the registry and Glama are read anonymously."""
    request = urllib.request.Request(url)
    token = _gh_token() if url.startswith(GITHUB_API) else ""
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return request


def _read_timeout(url: str) -> int:
    return REGISTRY_READ_TIMEOUT if url.startswith(REGISTRY_SEARCH) else READ_TIMEOUT


def _urlopen(url: str) -> str:
    try:
        with urllib.request.urlopen(_api_request(url), timeout=_read_timeout(url)) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        raise ReleaseError(f"{url}: {exc}") from exc


def _git_tag(root: Path) -> str:
    return _git(root, "describe", "--tags", "--abbrev=0")


def _first_line(done: subprocess.CompletedProcess) -> str:
    lines = done.stderr.strip().splitlines()
    return lines[0] if lines else f"exit {done.returncode}"


def _gh_release(root: Path, version: str) -> str:
    """The release URL, or "" when gh says the release does not exist. Any other
    failure raises: a logged-out gh exits 4 and knows nothing about the release."""
    try:
        done = subprocess.run(["gh", "release", "view", f"v{version}", "--repo", GITHUB_REPO,
                               "--json", "url", "--jq", ".url"],
                              cwd=root, capture_output=True, text=True)
    except OSError as exc:
        raise ReleaseError(f"cannot run gh: {exc}") from exc
    if done.returncode and "release not found" not in done.stderr:
        raise ReleaseError(f"gh failed: {_first_line(done)}")
    return done.stdout.strip()


def _github_row(version: str, read: Callable) -> Row:
    """A shell without gh, or a gh that fails for any reason but a missing
    release (logged out, say), cannot say whether the release exists, so the row
    says unconfirmed rather than `none`."""
    try:
        url = read()
    except ReleaseError as exc:
        return Row("GitHub release", f"v{version}", f"unconfirmed ({exc})", False)
    return Row("GitHub release", f"v{version}", url or "none", f"v{version}" in url)


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
    except (ValueError, KeyError, TypeError) as exc:
        # An HTML error page, or JSON without `info`: PyPI answered, but not with the release.
        return Row("PyPI", version, f"unreachable (not the version JSON: {exc!r})", False)


def _registry_pages(fetch: Callable):
    url, seen = REGISTRY_SEARCH, set()
    for _ in range(100):
        page = json.loads(fetch(url))
        yield page["servers"]
        cursor = page.get("metadata", {}).get("nextCursor")
        if not cursor:
            return
        if cursor in seen:
            raise ReleaseError("Registry repeated a pagination cursor")
        seen.add(cursor)
        url = REGISTRY_SEARCH + "&" + urllib.parse.urlencode({"cursor": cursor})
    raise ReleaseError("Registry pagination did not finish within 100 pages")


def _canonical_latest(entry: dict) -> bool:
    return (entry.get("server", {}).get("name") == REGISTRY_NAME
            and entry.get("_meta", {}).get(REGISTRY_META, {}).get("isLatest") is True)


def _latest_registry_entry(fetch: Callable) -> dict | None:
    """Find one latest canonical server after checking every search page."""
    latest = []
    for servers in _registry_pages(fetch):
        latest.extend(entry["server"] for entry in servers if _canonical_latest(entry))
    if len(latest) > 1:
        raise ReleaseError(f"Registry returned multiple latest entries for {REGISTRY_NAME}")
    return latest[0] if latest else None


def _registry_package_row(server: dict, version: str) -> Row:
    packages = server.get("packages", [])
    expected = {"registryType": "pypi", "identifier": PACKAGE, "version": version}
    matched = any(all(package.get(key) == value for key, value in expected.items()) for package in packages)
    return Row("registry package", f"pypi:{PACKAGE}@{version}", json.dumps(packages, sort_keys=True), matched)


def _registry_rows(version: str, fetch: Callable) -> list:
    try:
        server = _latest_registry_entry(fetch)
        if server is None:
            return [Row("registry", version, f"no latest entry for {REGISTRY_NAME}", False)]
        repo = (server.get("repository") or {}).get("url") or ""
        return [_row("registry", version, server.get("version", "")),
                _row("registry repository", REGISTRY_REPOSITORY, repo), _registry_package_row(server, version)]
    except (ReleaseError, KeyError, TypeError, ValueError, AttributeError) as exc:
        return [Row("registry", version, f"unconfirmed ({exc})", False)]


def _file_rows(root: Path, version: str) -> list:
    plugin = json.loads(_read(root, "plugin/.claude-plugin/plugin.json"))["version"]
    manifest = _read(root, "server.json").count(f'"version": "{version}"')
    readme = [s for s in SURFACES if s.path == "README.md"]
    held = sum(_read(root, "README.md").count(s.pattern.format(v=version)) == s.count for s in readme)
    return [_row("plugin.json", version, plugin),
            _row("server.json", "2 version fields", f"{manifest} version fields"),
            _row("README", f"{len(readme)} of {len(readme)} mentions", f"{held} of {len(readme)} mentions")]


def _tag_commit(root: Path, version: str) -> str:
    """Empty when the tag is not on this machine. `verify` is meant to run from
    anywhere, including a checkout that never cut the release, and a missing tag
    already has its own row; the Pages row says it cannot be checked."""
    try:
        return _git(root, "rev-list", "-n", "1", f"v{version}")
    except ReleaseError:
        return ""


def _answer(override: Callable | None, live: Callable):
    """One verify seam: the caller's answer when a test supplies one, else the
    live read."""
    return override() if override else live()


def _contains(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether the built commit carries the release commit. A commit is its own
    ancestor, so this also answers yes the moment the release itself is live."""
    try:
        _git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    except ReleaseError:
        return False
    return True


def _pages_row(commit: str, fetch: Callable, contains: Callable) -> Row:
    """Pages serves no version string on any page, so the proof that the site is
    this release is that its newest successful build carries the release commit.

    Carries, not equals. The newest build is whatever landed last, and main moves
    on after every release, so an equality check goes red on the next commit and
    stays there. `verify` said nothing about Pages at all before this."""
    if not commit:
        return Row("Pages", "built at or after the release", "unconfirmed (no local tag)", False)
    expected = f"built, carrying {commit[:11]}"
    try:
        build = json.loads(fetch(PAGES_LATEST))
        built = str(build.get("commit") or "")
        observed = f'{build.get("status")} @ {built[:11]}'
    except (ReleaseError, ValueError, TypeError) as exc:
        return Row("Pages", expected, f"unconfirmed ({exc})", False)
    return Row("Pages", expected, observed,
               build.get("status") == "built" and contains(commit, built))


def _glama_row(version: str, fetch: Callable) -> Row:
    """Glama renders the README of the revision it last synced, and the release
    step rewrites the README's action pin to the tag it just cut. So the pin on
    Glama's page is what says whether the sync ran for this release. Its sync is
    the one manual step in the chain, which is exactly why it needs a row."""
    pin = f"{PACKAGE}@v{version}"
    try:
        page = fetch(GLAMA_SERVER)
    except ReleaseError as exc:
        return Row("Glama", pin, f"unconfirmed ({exc})", False)
    return Row("Glama", pin, pin if pin in page else "an earlier revision", pin in page)


def verify(root: Path, version: str, *, fetch: Callable | None = None,
           git_tag: Callable | None = None, gh_release: Callable | None = None,
           tag_commit: Callable | None = None, contains: Callable | None = None) -> list:
    """One row per surface: what the release should say, what the live surface
    says. The fetchers are arguments so a test can answer for the network."""
    fetch = fetch or _urlopen
    tag = _answer(git_tag, lambda: _git_tag(root))
    commit = _answer(tag_commit, lambda: _tag_commit(root, version))
    read_github = (lambda: gh_release(version)) if gh_release else (lambda: _gh_release(root, version))
    rows = [_row("git tag", f"v{version}", tag), _github_row(version, read_github),
            _pypi_row(version, fetch)]
    rows += _registry_rows(version, fetch)
    carries = contains or (lambda ancestor, built: _contains(root, ancestor, built))
    rows += [_pages_row(commit, fetch, carries), _glama_row(version, fetch)]
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
RATCHET_FILE = "crapkit-ratchet.tsv"
RELEASE_FILES = tuple(sorted({surface.path for surface in SURFACES} | {"CHANGELOG.md", "SECURITY.md"}))


def _upload_prefix(surface: str, version: str) -> tuple:
    return {"pypi": (PY, "-m", "twine", "upload", "--repository-url", PYPI_UPLOAD_URL, "--non-interactive"),
            "github": ("gh", "release", "upload", f"v{version}", "--repo", GITHUB_REPO)}[surface]


def _accuracy_step(version: str) -> Step:
    branch = accuracy_branch(version)
    return Step("accuracy", "accuracy", (
        (PY, "tools/accuracy/run.py", "--tier", "release", "--receipt", accuracy_receipt(version)),
        ("git", "push", "-q", "origin", f"v{version}^{{commit}}:refs/heads/{branch}"),
        ("gh", "workflow", "run", "accuracy.yml", "--repo", GITHUB_REPO, "--ref", branch,
         "-f", "mode=release", "-f", f"release_key={version}"),
        ("gh", "run", "watch", ACCURACY_RUN_ARG, "--repo", GITHUB_REPO, "--exit-status"),
        ("git", "push", "-q", "origin", "--delete", branch)), background=True,
        note="the release tier here (30 min), then accuracy.yml's release mode on the tag "
             "commit through a scratch branch (up to 90 min); a rerun reuses a passing receipt "
             "and a run already passed or running. Stage 2b rereads the run from GitHub and "
             "rehashes the pins, corpus and retro ledger; it trusts neither report")


def plan(version: str) -> list:
    """The chain as a list a person reads before running it. Order is what the
    contracts require: the tag before the contract files (two of them read the
    newest tag), verify before anything leaves the machine, the accuracy and
    deploy stages on the tag commit before stage 2b publishes it, PyPI before
    the registry (the registry validates the README PyPI serves). Stage 1
    measures nothing: seed and prune run after the verify, against its run."""
    _parse(version)
    tool = (PY, "tools/release/release.py")
    contracts = (PY, "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly", *CONTRACT_FILES)
    return [
        Step("stage1", "stage1", (
            (*tool, "check", version), (*tool, "bump", version),
            (PY, "-m", "pip", "install", "-e", ".", "--no-deps", "-q"),
            (PY, "tools/docs/generate.py"),
            (PY, "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly", "tests/unit/test_version_surface.py"),
            ("git", "add", "--", *RELEASE_FILES), ("git", "commit", "-q", "-m", f"Release {version}")),
            note="guard: clean main includes origin/main; publication waits for the tagged full verify"),
        Step("tag", "stage2a", (("git", "tag", f"v{version}"),)),
        Step("contracts", "stage2a", (contracts,), note=f"red: git tag -d v{version} and stop"),
        Step("verify", "verify", ((PY, "-m", "crapkit", "verify"),), background=True,
             note="run it via `release.py run verify VERSION`: the bare `crapkit verify` stamps no"
                  " watermark and publication then refuses its evidence; a foreground call dies at 600 s"),
        Step("ratchet", "verify", ((PY, "-m", "crapkit", "ratchet", "seed"),
                                   (PY, "-m", "crapkit", "ratchet", "prune")),
             note=f"after a passing verify, against its run: a change verify, seed and prune make "
                  f"to {RATCHET_FILE} stops the release; the committed file goes back and the "
                  "computed one is saved under .crapkit/"),
        _accuracy_step(version),
        _deploy_step(version),
        Step("artifacts", "stage2b", ((PY, "-m", "build", "-q", "--outdir", RELEASE_DIST),
                                      (PY, "-m", "twine", "check", f"{RELEASE_DIST}/*")),
             note="build once, record wheel and sdist digests; retries verify and reuse these bytes"),
        Step("push", "stage2b", (("git", "push", "-q", "origin", "main", f"v{version}"),)),
        Step("pypi", "stage2b", ((*_upload_prefix("pypi", version), f"{RELEASE_DIST}/*"),)),
        Step("github release", "stage2b", (
            ("gh", "release", "create", f"v{version}", "--repo", GITHUB_REPO, "--verify-tag", "--title", f"crapkit {version}",
             "--notes-file", f".crapkit/release-notes-{version}.md"),
            (*_upload_prefix("github", version), f"{RELEASE_DIST}/*")),
            note="the notes file is the changelog section, written by `notes` first"),
        Step("plugin", "stage2b", (("claude", "plugin", "update", "crapkit@crapkit"),)),
        Step("pages", "stage2b", (("gh", "api", "--hostname", "github.com", "-X", "POST",
                                  f"repos/{REPO_SLUG}/pages/builds", "--jq", ".status"),)),
        Step("registry", "registry", (("mcp-publisher", "login", "github", "--token", GH_TOKEN_ARG),
                                      ("mcp-publisher", "publish")),
             note="logs in with the gh token, so no device flow; the registry session lasts "
                  "minutes, which is why login and publish run back to back"),
        Step("glama", "glama", (),
             note="Sync Server on the Repository admin tab; the sync builds and publishes the "
                  "release with the GitHub notes on its own"),
        Step("surfaces", "surfaces", ((*tool, "verify", version),)),
        Step("published", "surfaces", (("gh", "workflow", "run", DEPLOY_WORKFLOW, "--repo", GITHUB_REPO,
                                        "--ref", "main", "-f", f"cadence={DEPLOY_PUBLISHED_CADENCE}",
                                        "-f", f"ref=v{version}"),),
             note=f"the deploy suite installs v{version} from PyPI, the tag, pre-commit and the MCP "
                  "registry the way a user does; a red run there fails the release"),
    ]


# --- run one stage -------------------------------------------------------------------

def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if done.returncode:
        raise ReleaseError(done.stderr.strip() or f"git {' '.join(arguments)} failed")
    return done.stdout.strip()


def _status_records(root: Path) -> list[str]:
    output = _git(root, "status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z")
    return [record for record in output.split("\0") if record]


def _repo_state(root: Path) -> tuple[dict, int]:
    """Branch headers and the count of changed paths, from one git status. Every
    stage and every publish action checks this, and it used to cost three git
    processes: branch, status and rev-parse.

    Headers are the leading run only. Porcelain v2 prints every header before any
    entry, and a rename entry carries its original path as a field of its own,
    which can start with "# " too."""
    records = _status_records(root)
    headers = list(itertools.takewhile(lambda record: record.startswith("# "), records))
    return dict(header[2:].split(" ", 1) for header in headers), len(records) - len(headers)


def _clean_main(root: Path) -> str:
    headers, changed = _repo_state(root)
    if headers.get("branch.head") != "main":
        raise ReleaseError("release requires the main branch")
    if headers.get("branch.oid") == "(initial)":
        raise ReleaseError("release requires a commit on main")
    if changed:
        raise ReleaseError("release requires a clean tree, including untracked files")
    return headers["branch.oid"]


def _guard_bump(root: Path, version: str) -> dict:
    head = _clean_main(root)
    remote = _git(root, "ls-remote", "--exit-code", "origin", "refs/heads/main")
    included = subprocess.run(["git", "merge-base", "--is-ancestor", remote.split()[0], head],
                              cwd=root, capture_output=True)
    if included.returncode:
        raise ReleaseError("local main must include current origin/main before release preparation")
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
                "SELECT id,commit_sha,kind,verdict_ok,findings,lanes FROM runs ORDER BY id")]
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
    _passing_test_evidence(row["lanes"])
    return row["id"]


def _passing_test_evidence(stored) -> None:
    lanes = _test_lanes(stored)
    if not isinstance(lanes, dict) or set(lanes) != RELEASE_TEST_LANES:
        raise ReleaseError("passing test evidence is required for every release test lane")
    if not all(_passing_lane(lane) for lane in lanes.values()):
        raise ReleaseError("passing test evidence requires successful tests, counts and artifact digests")


def _test_lanes(stored):
    try:
        raw = zlib.decompress(stored).decode("utf-8") if isinstance(stored, bytes) else stored
        return json.loads(raw)
    except (TypeError, ValueError, zlib.error) as exc:
        raise ReleaseError("passing test evidence is unreadable in the verification ledger") from exc


def _passing_lane(lane) -> bool:
    return isinstance(lane, dict) and _passing_results(lane) and _test_artifact_digests(lane)


def _passing_results(lane: dict) -> bool:
    """Exit 0, an empty failure list and counts, read by crapkit's one reader of
    a lane's results: the crapkit that wrote the verify run this guard reads."""
    from crapkit.lane_results import read_results

    results, code = read_results(lane), lane.get("exit_code")
    return (type(code) is int and code == 0 and results.failures == frozenset()
            and _passing_test_counts(results))


def _passing_test_counts(results) -> bool:
    """At least one test ran and not every test was skipped; either count
    missing is no evidence."""
    return None not in (results.tests, results.skipped) and results.skipped < results.tests


def _test_artifact_digests(lane: dict) -> bool:
    digests = [lane.get(key) for key in ("artifact_sha256", "results_artifact_sha256")]
    return all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
               for value in digests)


def _canonical_origin(root: Path) -> None:
    pattern = r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)" + re.escape(REPO_SLUG) + r"(?:\.git)?/?"
    for mode in ((), ("--push",)):
        urls = _git(root, "remote", "get-url", *mode, "--all", "origin").splitlines()
        if len(urls) != 1 or re.fullmatch(pattern, urls[0], re.I) is None:
            raise ReleaseError(f"origin fetch and push must each resolve to one HTTPS or SSH URL for {GITHUB_REPO}")


def _guard_verified(root: Path, version: str) -> dict:
    receipt = _guard_receipt(root, version)
    after = receipt.get("verify_after")
    if type(after) is not int or after < 0:
        raise ReleaseError("run the verify stage before publishing this release")
    latest = _passing_run(root, receipt["head"], after)
    if receipt.get("verify_run") != latest:
        raise ReleaseError("run the verify stage before publishing this release")
    _canonical_origin(root)
    return receipt


def _guard_publish(root: Path, version: str) -> dict:
    """One record per required check: the verify run, then the
    accuracy and deploy records, each at the tag commit."""
    receipt = _guard_verified(root, version)
    accuracy_gate(root, version, receipt["head"])
    deploy_gate(root, version, receipt)
    return receipt


def _preflight(stage: str, root: Path, version: str) -> dict:
    guards = {"stage1": _guard_bump, "stage2a": _guard_contracts,
              "verify": _guard_receipt, "accuracy": _guard_verified, "deploy": _guard_verified,
              "stage2b": _guard_publish, "registry": _guard_publish}
    return guards[stage](root, version) if stage in guards else {}


# --- the accuracy gate -----------------------------------------------------------------
#
# The accuracy stage runs the release tier here and accuracy.yml's release mode
# on GitHub, both on the tag commit. Stage 2b then believes neither report: it
# reads the run from GitHub itself and hashes the files the receipt vouches for.

ACCURACY_TIER = "tools/accuracy/run.py"
ACCURACY_WORKFLOW = "accuracy.yml"
# The oracle pins, the corpus the goldens come from, and the retro ledger. The
# receipt records their sha256; stage 2b recomputes each from the tree.
ACCURACY_DIGESTS = ("tools/accuracy/pins.toml", "tests/accuracy/corpus_goldens/corpus.toml",
                    "tests/accuracy/suite_strength/retro/ledger.tsv")
ACCURACY_OUTCOMES = frozenset({"pass", "empty"})  # empty: the row has no test in this tier
ACCURACY_WATCH_SECONDS = 90 * 60
ACCURACY_RUN_ARG = "$(the dispatched accuracy run)"


class Watched(NamedTuple):
    """A workflow a release stage runs at the tag commit and watches to its end."""
    workflow: str
    stage: str      # the stage a rerun names
    seconds: int    # how long the watch waits


ACCURACY_WATCH = Watched(ACCURACY_WORKFLOW, "accuracy", ACCURACY_WATCH_SECONDS)


def accuracy_receipt(version: str) -> str:
    return f".crapkit/release-accuracy-{version}.json"


def accuracy_title(version: str) -> str:
    """accuracy.yml's run-name for a release dispatch: `accuracy <mode> <release_key>`."""
    return f"accuracy release {version}"


def accuracy_branch(version: str) -> str:
    """The scratch branch that carries the tag commit to GitHub before stage 2b pushes
    the tag: a dispatched run needs its commit on the remote."""
    return f"accuracy-release/{version}"


def _rerun(version: str, stage: str = "accuracy") -> str:
    return f"rerun `python tools/release/release.py run {stage} {version}`"


def gates_accuracy(root: Path) -> bool:
    """A tree that carries the accuracy suite is released only past it."""
    return (root / ACCURACY_TIER).is_file()


def _read_accuracy_receipt(root: Path, version: str) -> dict:
    try:
        saved = json.loads((root / accuracy_receipt(version)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"no readable release accuracy receipt at {accuracy_receipt(version)}; "
                           f"{_rerun(version)}") from exc
    if not isinstance(saved, dict):
        raise ReleaseError(f"{accuracy_receipt(version)} must be a JSON object; {_rerun(version)}")
    return saved


def _head_problems(saved: dict, head: str, version: str) -> list:
    made = str(saved.get("head"))
    if made == head:
        return []
    return [f"the release accuracy receipt was made at {made[:12]} and the release is at "
            f"{head[:12]}; {_rerun(version)}"]


def _tier_problems(saved: dict, version: str) -> list:
    tier = saved.get("tier")
    if tier == "release" and saved.get("outcome") == "pass":
        return []
    return [f"the release accuracy receipt records the {tier} tier with outcome "
            f"{saved.get('outcome')}, not a passing release tier; {_rerun(version)}"]


def _row_line(row, version: str) -> str | None:
    fields = row if isinstance(row, dict) else {"key": row, "outcome": "unreadable"}
    if fields.get("outcome") in ACCURACY_OUTCOMES:
        return None
    return (f"release accuracy row `{fields.get('key')}: {fields.get('name')}` "
            f"{fields.get('outcome')}: fix what it names, then {_rerun(version)}")


def _rows(saved: dict) -> list:
    rows = saved.get("checks")
    return rows if isinstance(rows, list) else []


def _row_problems(saved: dict, version: str) -> list:
    rows = _rows(saved)
    lines = [_row_line(row, version) for row in rows]
    if not rows:
        return [f"the release accuracy receipt records no check; {_rerun(version)}"]
    return [line for line in lines if line]


def _file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _digest_problems(root: Path, saved: dict, version: str) -> list:
    """Each vouched-for file hashed from the tree, never taken from the receipt."""
    recorded = saved.get("digests") if isinstance(saved.get("digests"), dict) else {}
    problems = []
    for name in ACCURACY_DIGESTS:
        found, claimed = _file_sha256(root / name), recorded.get(name)
        if found != claimed:
            problems.append(f"{name} hashes to {str(found)[:12]} here and the release accuracy "
                            f"receipt says {str(claimed)[:12]}; {_rerun(version)}")
    return problems


def receipt_problems(root: Path, saved: dict, head: str, version: str) -> list:
    """Every reason the local release-tier receipt cannot vouch for this release."""
    return (_head_problems(saved, head, version) + _tier_problems(saved, version)
            + _row_problems(saved, version) + _digest_problems(root, saved, version))


def _accuracy_runs(head: str) -> list:
    """accuracy.yml's dispatched runs at `head`, as GitHub reports them."""
    url = (f"{GITHUB_API}repos/{REPO_SLUG}/actions/workflows/{ACCURACY_WORKFLOW}/runs"
           f"?head_sha={head}&event=workflow_dispatch&per_page=100")
    runs = _remote_json(url).get("workflow_runs")
    if not isinstance(runs, list):
        raise ReleaseError(f"cannot read accuracy.yml runs at {head[:12]}: no workflow_runs list")
    return [run for run in runs if isinstance(run, dict)]


def release_runs(runs: list, version: str, head: str) -> list:
    """The release-mode runs for `version` at `head`: the run-name carries mode and version."""
    return [run for run in runs
            if (run.get("display_title"), run.get("head_sha")) == (accuracy_title(version), head)]


def passing_run(runs: list, version: str, head: str) -> dict | None:
    done = ("completed", "success")
    return next((run for run in release_runs(runs, version, head)
                 if (run.get("status"), run.get("conclusion")) == done), None)


def _remote_problems(version: str, head: str) -> list:
    if passing_run(_accuracy_runs(head), version, head):
        return []
    return [f"GitHub holds no successful accuracy.yml run named `{accuracy_title(version)}` at "
            f"{head[:12]}; {_rerun(version)}"]


def accuracy_gate(root: Path, version: str, head: str) -> None:
    """Stage 2b's refusal: the local receipt must be this tree's passing release tier,
    and GitHub must hold a successful release-mode run at the tag commit."""
    if not gates_accuracy(root):
        return
    saved = _read_accuracy_receipt(root, version)
    problems = receipt_problems(root, saved, head, version) + _remote_problems(version, head)
    if problems:
        raise ReleaseError(NL.join(problems))


# --- the accuracy stage: the local tier, then the remote run -------------------------------

def _local_problems(root: Path, version: str, head: str) -> list:
    try:
        saved = _read_accuracy_receipt(root, version)
    except ReleaseError as missing:
        return [str(missing)]
    return receipt_problems(root, saved, head, version)


def _local_accuracy(step: Step, root: Path, version: str, head: str) -> None:
    """The release tier, unless this tree already holds its passing receipt: a rerun
    after a remote timeout does not repeat half an hour of local checks."""
    if not _local_problems(root, version, head):
        return
    failure = _attempt(root, step.commands[0])
    problems = _local_problems(root, version, head)
    if problems or failure:
        raise ReleaseError(NL.join(problems) or f"the release tier failed: {failure}")


def _unfinished(runs: list) -> dict | None:
    return next((run for run in runs if run.get("status") != "completed"), None)


def _dispatched(listed: Callable, known: set, missing: str, pause: Callable = time.sleep) -> dict:
    """The run a dispatch just created: GitHub lists it within seconds. `listed`
    reads the stage's release runs at the tag commit; `missing` is the refusal
    when none appears."""
    for attempt in range(READBACK_ATTEMPTS):
        fresh = [run for run in listed() if run.get("id") not in known]
        if fresh:
            return fresh[0]
        pause(READBACK_PAUSE)
    raise ReleaseError(missing)


def _watch(run: dict, root: Path, version: str, watched: Watched = ACCURACY_WATCH) -> None:
    argv = [_executable("gh"), "run", "watch", str(run["id"]), "--repo", GITHUB_REPO,
            "--exit-status", "--interval", "60"]
    page = f"https://{GITHUB_REPO}/actions/runs/{run['id']}"
    rerun = _rerun(version, watched.stage)
    try:
        done = subprocess.run(argv, cwd=root, timeout=watched.seconds)
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(f"{page} still runs after {watched.seconds // 60} minutes; {rerun} to "
                           "keep waiting on it, which dispatches nothing new") from exc
    if done.returncode:
        raise ReleaseError(f"the release run of {watched.workflow} failed: {page}; fix it, then "
                           f"{rerun}")


def _run_to_watch(step: Step, root: Path, version: str, head: str, runs: list) -> dict:
    """A release run already going at the tag commit, else a new dispatch's run."""
    run = _unfinished(release_runs(runs, version, head))
    if run is not None:
        return run
    _run_or_untag(step._replace(commands=step.commands[1:3]), root, version, False)
    return _dispatched(lambda: release_runs(_accuracy_runs(head), version, head),
                       {old.get("id") for old in runs},
                       f"accuracy.yml was dispatched but no run named `{accuracy_title(version)}` "
                       f"appeared at {head[:12]}; {_rerun(version)}")


def _remote_accuracy(step: Step, root: Path, version: str, head: str) -> None:
    """Watch a release-mode run of accuracy.yml at the tag commit to its end,
    dispatching one only when none passed or is running there. The scratch branch
    goes afterwards, whichever attempt pushed it; a branch already gone is fine."""
    runs = _accuracy_runs(head)
    if not passing_run(runs, version, head):
        _watch(_run_to_watch(step, root, version, head, runs), root, version)
    _attempt(root, step.commands[-1])


def _run_accuracy(step: Step, root: Path, version: str, receipt: dict) -> None:
    _local_accuracy(step, root, version, receipt["head"])
    _remote_accuracy(step, root, version, receipt["head"])
    accuracy_gate(root, version, receipt["head"])


# --- the deploy record -------------------------------------------------------------------------
#
# The deploy stage runs deploy.yml's release cadence on the tag commit through a
# scratch branch, as the accuracy stage runs accuracy.yml. Its record in the
# release receipt is keyed on that commit's sha and on its git tree id
# (`git rev-parse vVERSION^{tree}`): the stage dispatches the run with that tree
# id, and the workflow's scope job refuses a checkout whose HEAD^{tree} differs,
# so the run's name carries the tree it tested. A tree id names the committed
# content, so a Windows checkout that holds CRLF on disk and the runner's LF
# checkout read one key. The record once held candidate.py's hash of the
# working-tree bytes: the 0.8.1 Windows checkout hashed to 2d2b705b, the runner
# to fedbb54a, and the scope job refused every run. Stage 2b checks the record's
# sha against the receipt's head and reads the run back from GitHub. This proves
# the suite tested the release's source, not the bytes PyPI gets: the deploy kit
# builds its own wheel from the tree.

DEPLOY_RUN_ARG = "$(the deploy run)"
TREE_ARG = re.compile(r"tree=\$\(git rev-parse (\S+\^\{tree\})\)")
# The longest release-cadence entry in tests/deploy/MAP.toml has a 100-minute
# timeout, and every entry starts once the 5-minute scope job ends. The rest is
# room for a runner queue.
DEPLOY_WATCH = Watched(DEPLOY_WORKFLOW, "deploy", 150 * 60)


def deploy_title(tree: str) -> str:
    """deploy.yml's run-name for a release dispatch: `deploy release <tree id>`."""
    return f"deploy {DEPLOY_RELEASE_CADENCE} {tree}"


def deploy_branch(version: str) -> str:
    """The scratch branch that carries the tag commit to GitHub before stage 2b
    pushes the tag: a dispatched run needs its commit on the remote."""
    return f"deploy-release/{version}"


def deploy_key(root: Path, version: str) -> str:
    """The tag commit's git tree id: the deploy record's key."""
    try:
        return _git(root, "rev-parse", "--verify", "--quiet", f"v{version}^{{tree}}")
    except ReleaseError as exc:
        raise ReleaseError(f"v{version} names no tree to key the deploy record on; "
                           f"{_rerun(version, 'stage2a')}") from exc


def _deploy_step(version: str) -> Step:
    branch = deploy_branch(version)
    return Step("deploy", "deploy", (
        ("git", "push", "-q", "origin", f"v{version}^{{commit}}:refs/heads/{branch}"),
        ("gh", "workflow", "run", DEPLOY_WORKFLOW, "--repo", GITHUB_REPO, "--ref", branch,
         "-f", f"cadence={DEPLOY_RELEASE_CADENCE}", "-f", f"tree=$(git rev-parse v{version}^{{tree}})"),
        ("gh", "run", "rerun", DEPLOY_RUN_ARG, "--failed", "--repo", GITHUB_REPO),
        ("gh", "run", "watch", DEPLOY_RUN_ARG, "--repo", GITHUB_REPO, "--exit-status"),
        ("git", "push", "-q", "origin", "--delete", branch)), background=True,
        note="runs deploy.yml's release cadence on the tag commit through a scratch branch (up to "
             "150 min), named for the commit's git tree id; its scope job refuses a checkout "
             "whose tree differs. The receipt keeps the record, keyed on the commit and the tree "
             "id. A rerun reuses a run already passed or running, and after a red or cancelled "
             "run under that key reruns only its failed and cancelled jobs")


def _is_tree_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None


def _deploy_runs(head: str) -> list:
    """deploy.yml's dispatched runs at `head`, as GitHub reports them. The same
    query as `_accuracy_runs`, which the accuracy calc owns and mutates."""
    url = (f"{GITHUB_API}repos/{REPO_SLUG}/actions/workflows/{DEPLOY_WORKFLOW}/runs"
           f"?head_sha={head}&event=workflow_dispatch&per_page=100")
    runs = _remote_json(url).get("workflow_runs")
    if not isinstance(runs, list):
        raise ReleaseError(f"cannot read {DEPLOY_WORKFLOW} runs at {head[:12]}: no workflow_runs list")
    return [run for run in runs if isinstance(run, dict)]


def _record_runs(runs: list, record: dict) -> list:
    """The release-cadence runs at the record's commit named for its tree id."""
    wanted = (deploy_title(record["tree"]), record["sha"])
    return [run for run in runs if (run.get("display_title"), run.get("head_sha")) == wanted]


def _green(found: list) -> bool:
    return any(run.get("conclusion") == "success" for run in found)


def _run_label(run: dict) -> str:
    return f"{run.get('conclusion') or 'not finished'} {run.get('html_url', '')}".rstrip()


def _record_problems(record, head: str, version: str) -> list:
    redo = _rerun(version, "deploy")
    if not isinstance(record, dict):
        return [f"deploy gate: the release receipt holds no deploy record; {redo}"]
    made = str(record.get("sha"))
    if made != head:
        return [f"deploy gate: the deploy record was made at {made[:12]} and the release is at "
                f"{head[:12]}; {redo}"]
    if not _is_tree_id(record.get("tree")):
        return [f"deploy gate: the deploy record names no git tree id; {redo}"]
    return []


def _run_problems(record: dict, version: str) -> list:
    found = _record_runs(_deploy_runs(record["sha"]), record)
    if _green(found):
        return []
    seen = f" (runs by that name: {', '.join(map(_run_label, found))})" if found else ""
    return [f"deploy gate: GitHub holds no successful {DEPLOY_WORKFLOW} run named "
            f"`{deploy_title(record['tree'])}` at {record['sha'][:12]}{seen}; "
            f"{_rerun(version, 'deploy')}"]


def deploy_problems(version: str, receipt: dict) -> list:
    """Every reason the receipt's deploy record cannot vouch for this release."""
    problems = _record_problems(receipt.get("deploy"), receipt["head"], version)
    if problems:
        return problems
    try:
        return _run_problems(receipt["deploy"], version)
    except ReleaseError as exc:
        return [f"deploy gate: {exc}"]


def deploy_gate(root: Path, version: str, receipt: dict) -> None:
    """Stage 2b's refusal beside the accuracy gate: the receipt's deploy record
    names the release commit, and GitHub holds a green release-cadence run of
    deploy.yml at that commit, named for the record's tree id. A tree without
    the deploy kit is not gated."""
    problems = deploy_problems(version, receipt) if gates_deploy(root) else []
    if problems:
        raise ReleaseError(NL.join(problems))


def _for_run(command: tuple, run: dict) -> tuple:
    return tuple(str(run["id"]) if arg == DEPLOY_RUN_ARG else arg for arg in command)


def _newest_red(runs: list) -> dict | None:
    """The newest finished run under the record's key that did not pass."""
    red = [run for run in runs if run.get("status") == "completed" and run.get("conclusion") != "success"]
    return max(red, key=lambda run: run.get("id") or 0, default=None)


def _rerun_failed(step: Step, root: Path, version: str, record: dict, red: dict) -> dict:
    """Rerun only the failed and cancelled jobs of a red run under the same key.
    Every deploy entry needs only the scope job and downloads nothing another
    job wrote, so a green entry from the earlier attempt still holds for this
    tree. The branch goes back first: the rerun checks the commit out again."""
    commands = (step.commands[0], _for_run(step.commands[2], red))
    _run_or_untag(step._replace(commands=commands), root, version, False)
    return _dispatched(lambda: [run for run in _record_runs(_deploy_runs(record["sha"]), record)
                                if run.get("id") == red.get("id") and run.get("status") != "completed"],
                       set(), f"{_run_label(red)} was rerun but never left its finished state; "
                              f"{_rerun(version, 'deploy')}")


def _deploy_to_watch(step: Step, root: Path, version: str, record: dict, runs: list) -> dict:
    """A release run already going for this record, else the newest red one
    rerun, else a new dispatch's run."""
    run = _unfinished(runs)
    if run is not None:
        return run
    red = _newest_red(runs)
    if red is not None:
        return _rerun_failed(step, root, version, record, red)
    _run_or_untag(step._replace(commands=step.commands[:2]), root, version, False)
    return _dispatched(lambda: _record_runs(_deploy_runs(record["sha"]), record),
                       {old.get("id") for old in runs},
                       f"{DEPLOY_WORKFLOW} was dispatched but no run named "
                       f"`{deploy_title(record['tree'])}` appeared at {record['sha'][:12]}; "
                       f"{_rerun(version, 'deploy')}")


def _remote_deploy(step: Step, root: Path, version: str, record: dict) -> None:
    """Watch the record's release run to its end, dispatching one only when none
    ran under its key. The scratch branch goes afterwards, whichever attempt
    pushed it; a branch already gone is fine."""
    runs = _record_runs(_deploy_runs(record["sha"]), record)
    if not _green(runs):
        _watch(_deploy_to_watch(step, root, version, record, runs), root, version, DEPLOY_WATCH)
    _attempt(root, step.commands[-1])


def _run_deploy(step: Step, root: Path, version: str, receipt: dict) -> None:
    """Run the release cadence on the tag commit and keep the record stage 2b
    reads, keyed on the commit and its tree id."""
    record = {"sha": receipt["head"], "tree": deploy_key(root, version)}
    _remote_deploy(step, root, version, record)
    receipt["deploy"] = record
    deploy_gate(root, version, receipt)
    _write_receipt(root, receipt)


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


def _check_artifacts(artifacts: list[Path]) -> None:
    if not artifacts:
        raise ReleaseError("release-dist contains no wheel or source archive")
    if any(path.is_symlink() or not path.is_file() for path in artifacts):
        raise ReleaseError("release artifacts must be regular files inside release-dist")


def _executable(name: str) -> str:
    """Windows `CreateProcess` searches PATH but appends only `.exe`, so a bare
    `claude` never resolved the npm `claude.CMD` shim and the plugin step died
    with WinError 2 once PyPI and the GitHub release were already public.
    `shutil.which` honours PATHEXT. An unresolved name passes through, so the
    failure still names the command that is missing."""
    return shutil.which(name) or name


def _arguments(command: tuple, root: Path) -> list[str]:
    expanded = [value for arg in command for value in _expanded(arg, root)]
    return [_executable(expanded[0]), *expanded[1:]]


def _expanded(arg: str, root: Path) -> list[str]:
    if arg == f"{RELEASE_DIST}/*":
        return _release_files(root)
    if arg == GH_TOKEN_ARG:
        return [_login_token()]
    tree = TREE_ARG.fullmatch(arg)
    if tree:
        return [f"tree={_git(root, 'rev-parse', tree.group(1))}"]
    return [arg]


def _login_token() -> str:
    """The registry login's credential. Without it mcp-publisher falls back to
    GitHub's device flow and waits for a person to type a code."""
    token = _gh_token()
    if not token:
        raise ReleaseError("gh auth token returned nothing; run gh auth login, then rerun the registry stage")
    return token


def _inside(root: Path, rel: str) -> Path:
    """`rel` under the release repository, never through a link that leads out
    of it: the stages delete these directories before they rebuild them."""
    expected = root.resolve() / rel
    if expected.resolve() != expected:
        raise ReleaseError(f"{rel} must be a directory inside the release repository")
    return expected


def _release_dist(root: Path) -> Path:
    return _inside(root, RELEASE_DIST)


def _release_files(root: Path) -> list[str]:
    paths = sorted(_release_dist(root).glob("*"))
    _check_artifacts(paths)
    return [str(path.relative_to(root)) for path in paths]


def _artifact_manifest(root: Path, version: str) -> dict:
    paths = [root / rel for rel in _release_files(root)]
    _release_names([path.name for path in paths], version)
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _release_names(names: list[str], version: str) -> None:
    wheels = [name for name in names if re.fullmatch(rf"{PACKAGE}-{re.escape(version)}-.+\.whl", name)]
    if len(names) != 2 or len(wheels) != 1 or f"{PACKAGE}-{version}.tar.gz" not in names:
        raise ReleaseError(f"release-dist must contain one {version} wheel and its source archive")


def _local_artifacts(root: Path, receipt: dict) -> dict:
    current = _artifact_manifest(root, receipt["version"])
    if current != receipt.get("artifacts"):
        raise ReleaseError("local release artifact digests differ from the receipt; restore the confirmed bytes")
    return current


def _prepare_artifacts(root: Path, receipt: dict, step: Step) -> None:
    if "artifacts" in receipt:
        _local_artifacts(root, receipt)
        return
    target = _release_dist(root)
    if target.exists():
        shutil.rmtree(target)
    _run_or_untag(step._replace(commands=step.commands[:1]), root, receipt["version"], False)
    manifest = _artifact_manifest(root, receipt["version"])
    _run_or_untag(step._replace(commands=step.commands[1:]), root, receipt["version"], False)
    if _artifact_manifest(root, receipt["version"]) != manifest:
        raise ReleaseError("release artifacts changed during twine check; nothing was published")
    _guard_publish(root, receipt["version"])
    receipt["artifacts"] = manifest
    _write_receipt(root, receipt)


def _remote_json(url: str, *, absent: bool = False) -> dict | None:
    try:
        with urllib.request.urlopen(_api_request(url), timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        if absent and exc.code == 404:
            return None
        raise ReleaseError(f"cannot confirm {url}: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"cannot confirm {url}: {exc}") from exc
    if not isinstance(result, dict):
        raise ReleaseError(f"cannot confirm {url}: expected a JSON object")
    return result


def _sha256(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ReleaseError("published artifact has no valid SHA256 digest")
    return value.lower()


def _remote_items(data: dict, key: str) -> list:
    items = data[key]
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ReleaseError(f"cannot confirm publication metadata: {key} must be a list of objects")
    return items


def _digest_map(items: list, name_key: str, digest: Callable) -> dict:
    result = {}
    for item in items:
        name = item[name_key]
        if not isinstance(name, str) or name in result:
            raise ReleaseError("cannot confirm publication metadata: invalid or duplicate filename")
        result[name] = digest(item)
    return result


def _pypi_files(version: str) -> dict:
    data = _remote_json(f"https://pypi.org/pypi/{PACKAGE}/{version}/json", absent=True)
    if data is None:
        return {}
    try:
        if data["info"]["version"] != version:
            raise ReleaseError("PyPI returned another version")
        return _digest_map(_remote_items(data, "urls"), "filename", lambda item: _sha256(item["digests"]["sha256"]))
    except (KeyError, TypeError) as exc:
        raise ReleaseError("cannot confirm PyPI artifact metadata") from exc


def _github_release(version: str) -> dict | None:
    data = _remote_json(f"https://api.github.com/repos/{REPO_SLUG}/releases/tags/v{version}", absent=True)
    if data is not None and (data.get("tag_name"), data.get("draft")) != (f"v{version}", False):
        raise ReleaseError("GitHub release is a draft or names another tag")
    return data


def _download_sha256(url: str) -> str:
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseError(f"cannot confirm asset bytes at {url}: {exc}") from exc
    return digest.hexdigest()


def _github_digest(asset: dict) -> str:
    digest = asset.get("digest")
    if digest is None:
        return _download_sha256(asset["browser_download_url"])
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ReleaseError("GitHub asset has an unsupported digest")
    return _sha256(digest.removeprefix("sha256:"))


def _github_files(version: str) -> dict:
    data = _github_release(version)
    if data is None:
        return {}
    try:
        return _digest_map(_remote_items(data, "assets"), "name", _github_digest)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReleaseError("cannot confirm GitHub asset metadata") from exc


def _matching_files(expected: dict, observed: dict, surface: str) -> set:
    for name in expected.keys() & observed.keys():
        if expected[name] != observed[name]:
            raise ReleaseError(f"{surface} digest mismatch for {name}; no artifact was overwritten")
    return expected.keys() & observed.keys()


def _file_confirmed(receipt: dict, surface: str, name: str) -> bool:
    readers = {"pypi": _pypi_files, "github": _github_files}
    observed = readers[surface](receipt["version"])
    return name in _matching_files(receipt["artifacts"], observed, surface)


def _pending(receipt: dict) -> list:
    value = receipt.get("pending", [])
    if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
        raise ReleaseError("release receipt pending actions must be a list of names")
    return value


def _clear_pending(root: Path, receipt: dict, key: str) -> None:
    receipt["pending"] = [name for name in _pending(receipt) if name != key]
    _write_receipt(root, receipt)


def _record_publication(root: Path, receipt: dict, key: str) -> None:
    if key == "push":
        receipt["push_confirmed"] = receipt["head"]
    _clear_pending(root, receipt, key)


def _attempt(root: Path, command: tuple) -> Exception | None:
    try:
        _execute(command, root, False)
    except (subprocess.CalledProcessError, OSError) as exc:
        return exc
    return None


def _settled(confirm: Callable, pause: Callable = time.sleep, attempts: int = READBACK_ATTEMPTS) -> bool:
    """Read a just-written surface until it answers, or give up.

    PyPI's version JSON and GitHub's release API both take seconds to serve what
    was just uploaded, so the first read after a publish usually misses. Re-reading
    is free and republishes nothing, and the alternative is what the 0.7.2 release
    did: record the action as unconfirmed and stop, once per artifact, six times.
    The pre-check before the command is deliberately not retried, because there the
    honest answer is usually no."""
    for attempt in range(attempts):
        if confirm():
            return True
        if attempt + 1 < attempts:
            pause(READBACK_PAUSE)
    return False


def _publish_action(root: Path, receipt: dict, key: str, command: tuple, confirm: Callable) -> None:
    if confirm():
        _record_publication(root, receipt, key)
        return
    if key in _pending(receipt):
        raise ReleaseError(f"{key}: prior publication is still unconfirmed; see tools/release/README.md for recovery")
    _guard_publish(root, receipt["version"])
    _local_artifacts(root, receipt)
    receipt["pending"] = [*_pending(receipt), key]
    _write_receipt(root, receipt)
    failure = _attempt(root, command)
    if not _settled(confirm):
        raise ReleaseError(f"{key}: publication outcome is unconfirmed; rerun only after remote readback settles")
    _record_publication(root, receipt, key)
    if failure:
        raise ReleaseError(f"{key}: command failed but publication is confirmed; rerun stage2b to continue") from failure


def _pushed(root: Path, receipt: dict) -> bool:
    tag = f"refs/tags/v{receipt['version']}"
    text = _git(root, "ls-remote", "origin", "refs/heads/main", tag, tag + "^{}")
    refs = dict(line.split()[::-1] for line in text.splitlines())
    target = refs.get(tag + "^{}", refs.get(tag))
    if target is not None and target != receipt["head"]:
        raise ReleaseError("remote release tag points to another commit")
    main = refs.get("refs/heads/main")
    published = receipt.get("push_confirmed") == receipt["head"]
    return target == receipt["head"] and (main == receipt["head"] or published)


def _publish_push(root: Path, receipt: dict) -> None:
    command = ("git", "push", "-q", "origin", "main", f"v{receipt['version']}")
    _publish_action(root, receipt, "push", command, lambda: _pushed(root, receipt))


def _publish_files(root: Path, receipt: dict, surface: str) -> None:
    prefix = _upload_prefix(surface, receipt["version"])
    for name in sorted(receipt["artifacts"]):
        command = (*prefix, f"{RELEASE_DIST}/{name}")
        _publish_action(root, receipt, f"{surface}:{name}", command,
                        lambda: _file_confirmed(receipt, surface, name))


def _publish_github(root: Path, receipt: dict, step: Step) -> None:
    version = receipt["version"]
    out = root / ".crapkit" / f"release-notes-{version}.md"
    out.write_text(notes(root, version), encoding="utf-8", newline=NL)
    _publish_action(root, receipt, "github:create", step.commands[0],
                    lambda: _github_release(version) is not None)
    _publish_files(root, receipt, "github")


def _publish_plugin(root: Path, receipt: dict, step: Step) -> None:
    if receipt.get("plugin_updated") is True:
        return
    _run_or_untag(step, root, receipt["version"], False)
    receipt["plugin_updated"] = True
    _write_receipt(root, receipt)


def _pages_state(root: Path, receipt: dict) -> str:
    """The newest build's status when its commit carries the release head, else
    "other commit". Carries, not equals: main moves on right after a release and
    Pages builds the tip, so an equality test told a 0.7.3 rerun the site was at
    another commit while `verify` (the same ancestry test) printed the row ok."""
    data = _remote_json(f"https://api.github.com/repos/{REPO_SLUG}/pages/builds/latest", absent=True)
    if data is None:
        return "absent"
    if not isinstance(data.get("commit"), str) or data.get("status") not in ("built", "building", "queued", "errored"):
        raise ReleaseError("cannot confirm Pages build commit and status")
    if not _contains(root, receipt["head"], data["commit"]):
        return "other commit"
    return data["status"]


def _pages_built(root: Path, receipt: dict) -> bool:
    status = _pages_state(root, receipt)
    if status in ("building", "queued"):
        raise ReleaseError("Pages build is pending at this commit; wait, then rerun stage2b")
    return status == "built"


def _publish_pages(root: Path, receipt: dict, step: Step) -> None:
    if _pages_state(root, receipt) == "errored":
        _clear_pending(root, receipt, "pages")
    _publish_action(root, receipt, "pages", step.commands[0], lambda: _pages_built(root, receipt))


def _publish_step(step: Step, root: Path, receipt: dict) -> None:
    handlers = {"artifacts": lambda: _prepare_artifacts(root, receipt, step),
                "push": lambda: _publish_push(root, receipt),
                "pypi": lambda: _publish_files(root, receipt, "pypi"),
                "github release": lambda: _publish_github(root, receipt, step),
                "plugin": lambda: _publish_plugin(root, receipt, step),
                "pages": lambda: _publish_pages(root, receipt, step)}
    handlers[step.name]()


def _execute(command: tuple, root: Path, dry_run: bool) -> None:
    print(f"$ {subprocess.list2cmdline(command)}")
    if dry_run:
        return
    subprocess.run(_arguments(command, root), cwd=root, check=True)


def _stage1_files(root: Path) -> None:
    changed = _git(root, "diff", "--name-only", "HEAD", "-z").split("\0")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    unexpected = (set(changed) | set(untracked)) - set(RELEASE_FILES) - {""}
    if unexpected:
        raise ReleaseError("unexpected release edits: " + ", ".join(sorted(unexpected)))


def _run_step(step: Step, root: Path, dry_run: bool) -> None:
    if not step.commands:
        print(f"{step.name} is a manual step: {step.note}")
    for command in step.commands:
        if step.stage == "stage1" and not dry_run:
            _stage1_files(root)
        _execute(command, root, dry_run)


def _run_or_untag(step: Step, root: Path, version: str, dry_run: bool,
                  head: str = "") -> None:
    """A red contract run deletes the tag it just made, so the tree never
    carries a tag the contracts refused."""
    try:
        _run_step(step, root, dry_run)
    except subprocess.CalledProcessError as exc:
        if step.name == "contracts":
            subprocess.run(["git", "update-ref", "-d", f"refs/tags/v{version}", head], cwd=root)
        raise ReleaseError(f"{step.name} failed: {exc}") from exc


def _marks(root: Path) -> bytes | None:
    path = root / RATCHET_FILE
    return path.read_bytes() if path.is_file() else None


def _put_back_marks(root: Path, marks: bytes | None) -> None:
    path = root / RATCHET_FILE
    if marks is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(marks)


def _save_marks(root: Path, version: str, marks: bytes) -> str:
    saved = f".crapkit/release-marks-{version}.tsv"
    (root / saved).write_bytes(marks)
    return saved


def _marks_stop(version: str, run: int, saved: str) -> str:
    """The refusal, with the commands that carry the computed marks into the
    release commit. `git add` comes first because `git commit -- PATH` refuses a
    marks file the release commit does not track yet."""
    return NL.join((
        f"verify, seed and prune change {RATCHET_FILE} against verify run {run}, so the release "
        f"commit lacks marks its own tree earns. The committed file is back in place and the "
        f"computed one is saved at {saved}. Carry it into the release commit:",
        f"    $ git tag -d v{version}",
        f"    $ cp {saved} {RATCHET_FILE}",
        f"    $ git add -- {RATCHET_FILE}",
        "    $ git commit --amend --no-edit",
        f"then rerun `release.py run stage2a {version}` and `release.py run verify {version}`"))


def _check_ratchet(step: Step, root: Path, receipt: dict, before: int, committed: bytes | None) -> None:
    """Stage 1 once ran a full coverage lane only to feed `ratchet seed` and
    `ratchet prune`, and the verify stage then ran the same lane again. The verify
    run is that lane at the tagged tree, so seed and prune now work from it.

    `committed` is the marks file as the stage found it, read before the verify:
    a green verify already tightens and drops marks, so a read taken after it
    would miss that change. Any change by verify, seed or prune stops the release,
    because the release commit must carry the marks. The committed bytes go back
    either way, and the computed ones wait under .crapkit/ for the operator."""
    try:
        run = _passing_run(root, receipt["head"], before)
        _run_or_untag(step, root, receipt["version"], False)
        computed = _marks(root)
    finally:
        _put_back_marks(root, committed)
    if computed != committed:
        saved = _save_marks(root, receipt["version"], computed)
        raise ReleaseError(_marks_stop(receipt["version"], run, saved))


def _stage_step(step: Step, root: Path, version: str, receipt: dict, before: int,
                marks: bytes | None = None) -> None:
    if step.stage == "stage2b":
        _publish_step(step, root, receipt)
    elif step.name == "accuracy":
        _run_accuracy(step, root, version, receipt)
    elif step.name == "deploy":
        _run_deploy(step, root, version, receipt)
    elif step.name == "ratchet":
        _check_ratchet(step, root, receipt, before, marks)
    else:
        _run_or_untag(step, root, version, False, receipt.get("head", ""))


def _run_guarded(stage: str, steps: list, version: str, root: Path) -> None:
    receipt = _preflight(stage, root, version)
    before, marks = 0, None
    if stage == "verify":
        # The guard proved a clean tree, so these are HEAD's committed marks.
        before, marks = _last_run(root), _marks(root)
        receipt.pop("verify_run", None)
        _write_receipt(root, receipt)
    for step in steps:
        _stage_step(step, root, version, receipt, before, marks)
    _finish_stage(stage, root, version, receipt, before)


def _stage_steps(stage: str, version: str) -> list:
    """The plan's steps for one stage. The refusal names the stages from the plan,
    so a stage the plan gains is one the refusal lists."""
    chain = plan(version)
    steps = [s for s in chain if s.stage == stage]
    if not steps:
        stages = ", ".join(dict.fromkeys(s.stage for s in chain))
        raise ReleaseError(f"no stage {stage!r}; stages: {stages}")
    return steps


def run(stage: str, version: str, root: Path, *, dry_run: bool = False) -> None:
    """Check repository proof, then execute a stage; a dry run only prints it."""
    root = root.resolve()
    steps = _stage_steps(stage, version)
    if dry_run:
        for step in steps:
            _run_step(step, root, True)
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


# From 3.14 argparse colours help and usage, into a pipe too when FORCE_COLOR is
# set. Older argparse has no `color` keyword.
PLAIN_HELP = {"color": False} if sys.version_info >= (3, 14) else {}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0],
                                     **PLAIN_HELP)
    parser.add_argument("action", choices=tuple(ACTIONS))
    parser.add_argument("target", nargs="?", help="the version to release; for `run`, the stage")
    parser.add_argument("version", nargs="?", help="for `run`: the version")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--date", help="the changelog date `bump` writes (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    return parser


def _cmd_check(root: Path, version: str, args: argparse.Namespace) -> int:
    report = check(root, version)
    problems = report.problems + preflight() + deploy_preflight(root)
    print(NL.join(problems) or f"ok: every surface at {report.current}, {version} next")
    return 1 if problems else 0


def _cmd_bump(root: Path, version: str, args: argparse.Namespace) -> int:
    print(NL.join(f"bumped {path}" for path in bump(root, version, date=args.date)))
    return 0


def _cmd_notes(root: Path, version: str, args: argparse.Namespace) -> int:
    """The release body, as UTF-8 whatever the console's code page: under a
    cp1252 stdout the first CJK character in a section ended the preview with
    UnicodeEncodeError and an empty file. Stage 2b writes its notes file as UTF-8."""
    sys.stdout.reconfigure(encoding="utf-8")
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
