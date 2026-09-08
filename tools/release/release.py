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

Stdlib only. `verify` and publishing stages read remote state. Only explicit
stage commands publish; `plan` and dry runs make no requests or writes.
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
import urllib.parse
import urllib.request
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
    "tests/unit/test_generated_guidance.py",
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
    done = subprocess.run(["gh", "release", "view", f"v{version}", "--repo", GITHUB_REPO,
                           "--json", "url", "--jq", ".url"],
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
                            | {"CHANGELOG.md", "SECURITY.md", "crapkit-ratchet.tsv"}))


def _upload_prefix(surface: str, version: str) -> tuple:
    return {"pypi": (PY, "-m", "twine", "upload", "--repository-url", PYPI_UPLOAD_URL, "--non-interactive"),
            "github": ("gh", "release", "upload", f"v{version}", "--repo", GITHUB_REPO)}[surface]


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
            (PY, "tools/docs/generate.py"),
            (PY, "-m", "pytest", "-q", "-n", "0", "-p", "no:randomly", "tests/unit/test_version_surface.py"),
            (PY, "-m", "crapkit", "coverage"), (PY, "-m", "crapkit", "ratchet", "seed"),
            (PY, "-m", "crapkit", "ratchet", "prune"),
            ("git", "add", "--", *RELEASE_FILES), ("git", "commit", "-q", "-m", f"Release {version}")),
            note="guard: clean main includes origin/main; publication waits for the tagged full verify"),
        Step("tag", "stage2a", (("git", "tag", f"v{version}"),)),
        Step("contracts", "stage2a", (contracts,), note=f"red: git tag -d v{version} and stop"),
        Step("verify", "verify", ((PY, "-m", "crapkit", "verify"),), background=True,
             note="its own background command: a foreground tool call dies at 600 s"),
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


def _canonical_origin(root: Path) -> None:
    pattern = r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)" + re.escape(REPO_SLUG) + r"(?:\.git)?/?"
    for mode in ((), ("--push",)):
        urls = _git(root, "remote", "get-url", *mode, "--all", "origin").splitlines()
        if len(urls) != 1 or re.fullmatch(pattern, urls[0], re.I) is None:
            raise ReleaseError(f"origin fetch and push must each resolve to one HTTPS or SSH URL for {GITHUB_REPO}")


def _guard_publish(root: Path, version: str) -> dict:
    receipt = _guard_receipt(root, version)
    after = receipt.get("verify_after")
    if type(after) is not int or after < 0:
        raise ReleaseError("run the verify stage before publishing this release")
    latest = _passing_run(root, receipt["head"], after)
    if receipt.get("verify_run") != latest:
        raise ReleaseError("run the verify stage before publishing this release")
    _canonical_origin(root)
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


def _check_artifacts(artifacts: list[Path]) -> None:
    if not artifacts:
        raise ReleaseError("release-dist contains no wheel or source archive")
    if any(path.is_symlink() or not path.is_file() for path in artifacts):
        raise ReleaseError("release artifacts must be regular files inside release-dist")


def _arguments(command: tuple, root: Path) -> list[str]:
    return [value for arg in command for value in
            (_release_files(root) if arg == f"{RELEASE_DIST}/*" else [arg])]


def _release_dist(root: Path) -> Path:
    expected = root.resolve() / RELEASE_DIST
    if expected.resolve() != expected:
        raise ReleaseError("release-dist must be a directory inside the release repository")
    return expected


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
        with urllib.request.urlopen(url, timeout=20) as response:
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
    if not confirm():
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


def _pages_state(receipt: dict) -> str:
    data = _remote_json(f"https://api.github.com/repos/{REPO_SLUG}/pages/builds/latest", absent=True)
    if data is None:
        return "absent"
    if not isinstance(data.get("commit"), str) or data.get("status") not in ("built", "building", "queued", "errored"):
        raise ReleaseError("cannot confirm Pages build commit and status")
    if data["commit"] != receipt["head"]:
        return "other commit"
    return data["status"]


def _pages_built(receipt: dict) -> bool:
    status = _pages_state(receipt)
    if status in ("building", "queued"):
        raise ReleaseError("Pages build is pending at this commit; wait, then rerun stage2b")
    return status == "built"


def _publish_pages(root: Path, receipt: dict, step: Step) -> None:
    if _pages_state(receipt) == "errored":
        _clear_pending(root, receipt, "pages")
    _publish_action(root, receipt, "pages", step.commands[0], lambda: _pages_built(receipt))


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


def _run_guarded(stage: str, steps: list, version: str, root: Path) -> None:
    receipt = _preflight(stage, root, version)
    before = _last_run(root) if stage == "verify" else 0
    if stage == "verify":
        receipt.pop("verify_run", None)
        _write_receipt(root, receipt)
    for step in steps:
        if stage == "stage2b":
            _publish_step(step, root, receipt)
        else:
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
