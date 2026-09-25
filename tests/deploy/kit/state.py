"""A repo as an older crapkit left it, and the upgrade guide's steps that bring
it to the candidate.

SOURCES

    source = state.build(box, "0.7.6", cache=templates)   # built once per session
    repo = source.checkout(box)                           # this cell's own copy

A source is built by that release's own CLI, installed from the wheelhouse
into a venv of its own with pytest and pytest-cov (the lane init writes runs
`python -m pytest --cov`). It is built once per session per (version,
template, OS, Python, UTC day), behind a file lock the xdist workers share.
A source holds:

  - the py-pytest template plus calc/nested.py, a def three deep that
    analysis 11 renames, so `ratchet prune` has a mark to drop
  - `crapkit init`, coverage, seed and a commit, then a passing verify
  - a breach committed, refused by verify and overridden: an override record
    and the mark verify granted, committed
  - one open claim from `next-item --claim`
  - hand edits, each labelled a user edit in the transcript and in
    source.user_edits: alert_command (an override needs one), container_ok
    when the build runs in a container (docs/lanes.md#containers), and the
    test_retention_* keys a 0.7.x config accepted

source.facts holds what the old CLI answered to `runs --json`, `claims --json`
and `overrides --json` at the end of the build, so a cell compares what the
candidate answers with what the user saw before upgrading.

THE GUIDE

docs/upgrading.md, read from the stamped tree, never retyped:

    state.upgrade_line("uv tool")                   # the table's command, or GuideGap
    state.guide_commands("Measure before changing marks")
    state.guide_span("crapkit mutate --drop-pool")  # a command the prose names

and the walk that runs its steps in page order against a repo: measure
(doctor, then a coverage export), reseed (coverage, prune, seed) with the
review the guide asks for, commit, verify, and the checks a user makes after:
runs, claims and overrides kept, and a fresh MCP session on the new code.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from e2e import repo_templates
from kit import docsnip, repos, state_manifest, wheels
from kit.mcp_client import McpClient
from kit.transcript import Step

WINDOWS = os.name == "nt"
NESTED_PY = '''def outer(rows):
    def mid(row):
        def inner(value, low, high, strict):
            if value is None:
                return 0
            if value < low:
                return -1
            if value > high:
                return 1
            if strict and value == low:
                return -2
            if strict and value == high:
                return 2
            return 0
        return inner(row, 0, 10, True)
    return [mid(row) for row in rows]
'''
ROUTE_PY = '''def route(a, b, c, d, e):
    if a and b:
        return 1
    if c or d:
        return 2
    if e > 3:
        return 3
    if a and e:
        return 4
    return 5
'''
OVERRIDE_REASON = "a hotfix that cannot wait"
ALERT_KEY = 'alert_command = "python -c \\"import sys; sys.stdin.read()\\""\n'
CONTAINER_KEY = "container_ok = true\n"
RETENTION_KEYS = "test_retention_days = 14\ntest_retention_count = 20\n"
RETENTION_ERA = ("0.7.0", "0.8.0")
MAIN, LANE = "[crapkit]\n", "[[lane]]\n"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def in_era(version: str, era: tuple[str, str]) -> bool:
    return version_key(era[0]) <= version_key(version) < version_key(era[1])


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def scripts(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def launcher(directory: Path) -> Path:
    return directory / ("crapkit.exe" if WINDOWS else "crapkit")


def commit(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def write(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


# --- an old release's CLI --------------------------------------------------------

@dataclass(frozen=True)
class Cli:
    """One crapkit install: its launcher, and the directory that goes first on
    PATH so the lane's bare `python` is the one beside it."""
    bin: Path

    def env(self, box) -> dict[str, str]:
        return {"PATH": os.pathsep.join([str(self.bin), box.env["PATH"]])}

    def run(self, box, repo: Path, *args: str, expect: int | None = 0) -> Step:
        return box.run([str(launcher(self.bin)), *args], cwd=repo, env=self.env(box), expect=expect)


def pip_venv(box, python: str = "3.12", name: str = "venv") -> Path:
    """A fresh venv first on the sandbox PATH, the way `activate` puts it."""
    venv = box.root / name
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(scripts(venv))
    return venv


def pip_install(box, *requirements: str) -> Step:
    """`python -m pip install` into the venv first on PATH, offline."""
    return box.run(["python", "-m", "pip", "install", "-q", *requirements], expect=0)


def source_version(version: str) -> str:
    """A release a scenario names, held to the wheelhouse the run installs from."""
    if version not in wheels.releases():
        raise AssertionError(f"crapkit {version} is not in tools/deploy/wheelhouse.lock: {wheels.releases()}")
    return version


def venv_cli(box, version: str, venv: Path, python: str = "3.12", extra: tuple[str, ...] = ()) -> Cli:
    """crapkit==version, pytest and pytest-cov in a fresh venv, offline."""
    box.run(["uv", "venv", "-q", "--python", box.toolchain.python(python), str(venv)], expect=0)
    box.run(["uv", "pip", "install", "-q", "--python", str(venv), f"crapkit=={version}", "pytest", "pytest-cov",
             *extra], expect=0)
    return Cli(scripts(venv))


# --- building a source -----------------------------------------------------------

def rewrite(path: Path, change) -> None:
    """Hand-edit a text file the way an editor does: its line endings kept."""
    data = path.read_bytes()
    newline = "\r\n" if b"\r\n" in data else "\n"
    path.write_text(change(data.decode("utf-8").replace("\r\n", "\n")), encoding="utf-8", newline=newline)


def _edit(repo: Path, anchor: str, text: str) -> None:
    config = repo / "crapkit.toml"
    body = config.read_text(encoding="utf-8")
    if anchor not in body:
        raise AssertionError(f"crapkit.toml has no {anchor.strip()!r} to add {text.strip()!r} under:\n{body}")
    rewrite(config, lambda now: now.replace(anchor, anchor + text, 1))


def _planned_edits(version: str) -> list[tuple[str, str, str]]:
    edits = [(MAIN, ALERT_KEY, "alert_command, which verify --override needs to record its audit")]
    if in_container():
        edits.append((LANE, CONTAINER_KEY, "container_ok on the py lane, per docs/lanes.md#containers"))
    if in_era(version, RETENTION_ERA):
        edits.append((MAIN, RETENTION_KEYS, "test_retention_days and test_retention_count, keys 0.7.x accepted"))
    return edits


def user_edits(box, repo: Path, version: str) -> list[str]:
    """The hand edits a user of `version` made to the config init wrote."""
    labels = []
    for anchor, text, label in _planned_edits(version):
        _edit(repo, anchor, text)
        box.transcript.note(f"user edit: {label}")
        labels.append(label)
    return labels


def _override(box, cli: Cli, repo: Path) -> None:
    """A breach verify refuses, then an override that records why."""
    write(repo, {"calc/route.py": ROUTE_PY})
    commit(box, repo, "calc: route, over its ceiling")
    cli.run(box, repo, "verify", expect=6)
    cli.run(box, repo, "verify", "--override", OVERRIDE_REASON)
    commit(box, repo, "sign the mark the override granted")


def facts(box, cli: Cli, repo: Path) -> dict:
    """What the CLI answers to runs, claims and overrides under --json."""
    return {name: json.loads(cli.run(box, repo, name, "--json").stdout) for name in ("runs", "claims", "overrides")}


def adopt(box, cli: Cli, repo: Path, version: str) -> dict:
    """Everything a source holds, written by `cli`; returns its facts."""
    write(repo, {"calc/nested.py": NESTED_PY})
    commit(box, repo, "calc: a helper nested three deep")
    cli.run(box, repo, "init")
    edits = user_edits(box, repo, version)
    for step in (["coverage"], ["ratchet", "seed"]):
        cli.run(box, repo, *step)
    commit(box, repo, "adopt crapkit")
    cli.run(box, repo, "verify")
    _override(box, cli, repo)
    claimed = json.loads(cli.run(box, repo, "next-item", "--claim").stdout)["item"]
    return {**facts(box, cli, repo), "user_edits": edits, "version": version,
            "claimed": {key: claimed[key] for key in ("path", "function", "start")}}


@dataclass(frozen=True)
class Source:
    root: Path
    version: str
    python: str
    facts: dict

    @classmethod
    def load(cls, root: Path) -> "Source":
        record = json.loads((root / "state.json").read_text(encoding="utf-8"))
        return cls(root, record["version"], record["python"], record["facts"])

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def user_edits(self) -> list[str]:
        return self.facts["user_edits"]

    @property
    def run_ids(self) -> list[int]:
        return [run["id"] for run in self.facts["runs"]["runs"]]

    @property
    def claims(self) -> list[tuple[str, str]]:
        return [(claim["path"], claim["long_name"]) for claim in self.facts["claims"]["claims"]]

    @property
    def overrides(self) -> list[tuple[str, str, str]]:
        return [(row["path"], row["function"], row["reason"]) for row in self.facts["overrides"]["overrides"]]

    def checkout(self, box, name: str | None = None) -> Path:
        """This cell's private copy of the source repo."""
        return repo_templates.copy_of(self.repo, box.root / (name or f"repo-{self.version}"))


def key(version: str, template: str, python: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{version}-{template}-{os.name}-py{python}-{day}"


def _build(box, version: str, root: Path, cache: Path, template: str, python: str) -> Source:
    shutil.rmtree(root, ignore_errors=True)
    box.transcript.note(f"state: building {root.name} with crapkit {version}'s own CLI")
    cli = venv_cli(box, version, root / "venv", python)
    repo = repos.checkout(box, template, cache=cache, dest=root / "staging")
    record = {"version": version, "python": python, "facts": adopt(box, cli, repo, version)}
    repo.rename(root / "repo")
    (root / "state.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return Source.load(root)


def build(box, version: str, *, cache: Path, python: str = "3.12", template: str = "py-pytest") -> Source:
    """The session's source state for `version`, built now if no worker has built it."""
    root = Path(cache) / "states" / key(version, template, python)
    with repos.file_lock(root.parent / f"{root.name}.lock"):
        if (root / "state.json").exists():
            box.transcript.note(f"state: reusing {root.name}")
            return Source.load(root)
        return _build(box, version, root, Path(cache), template, python)


# --- the guide -------------------------------------------------------------------

GUIDE = "docs/upgrading.md"
ROW = re.compile(r"^\|\s*(?P<label>[^|`]+?)\s*\|\s*`(?P<command>[^`]+)`\s*\|\s*$")


class GuideGap(AssertionError):
    """docs/upgrading.md leaves out a step this user needs."""


def page() -> str:
    return (docsnip.root() / GUIDE).read_text(encoding="utf-8")


def upgrade_rows(text: str | None = None) -> dict[str, str]:
    """The upgrade table: installation -> the command its row gives."""
    rows = (ROW.match(line) for line in (text if text is not None else page()).splitlines())
    return {row["label"]: row["command"] for row in rows if row}


def upgrade_line(installation: str, text: str | None = None) -> str:
    rows = upgrade_rows(text)
    if installation not in rows:
        raise GuideGap(f"{GUIDE}: the upgrade table has no row for {installation!r}; its rows: {sorted(rows)}")
    return rows[installation]


def upgrade_command(prefix: str, text: str | None = None) -> str:
    """The table's command that starts with `prefix` ("pipx upgrade", "uvx"),
    whatever its row is labelled."""
    found = [command for command in upgrade_rows(text).values() if command.startswith(prefix)]
    if not found:
        raise GuideGap(f"{GUIDE}: the upgrade table gives no `{prefix} ...` command")
    return found[0]


@dataclass
class Gaps:
    """Guide steps a cell needed and did not find. The cell runs the tool's
    own command in their place, finishes every other check, and raises the
    gaps last, so a strict xfail(raises=GuideGap) holds only the doc gap."""
    found: list[str] = field(default_factory=list)

    def command(self, prefix: str, fallback: str) -> str:
        try:
            return upgrade_command(prefix)
        except GuideGap as gap:
            self.found.append(str(gap))
            return fallback

    def check(self, holds: bool, gap: str) -> None:
        if not holds:
            self.found.append(gap)

    def raise_any(self) -> None:
        if self.found:
            raise GuideGap("\n".join(self.found))


def guide_commands(heading: str, *, contains: str | None = None) -> list[str]:
    return docsnip.commands(docsnip.fence(GUIDE, heading, contains=contains))


def guide_span(text: str, body: str | None = None) -> str:
    """`text` as the guide's prose names it in a code span; GuideGap when it does not."""
    if f"`{text}`" not in (body if body is not None else page()):
        raise GuideGap(f"{GUIDE}: the page never names `{text}`")
    return text


def run_line(box, repo: Path, line: str, *, expect: int | None = 0, note: str = "") -> Step:
    """One documented command, run the way a user pastes it into their shell."""
    return box.script(line, cwd=repo, expect=expect, note=note or f"guide step: {line}")


def output(step: Step) -> str:
    return step.stdout + step.stderr


# --- doctor's failures, fixed the way doctor says ---------------------------------

SCOPE_BLOCK = re.compile(r"^\[\[scope\]\]\n(?:[^\[\n].*\n|\n)*", re.M)


def drop_files_placeholder(repo: Path) -> str:
    """doctor: "drop {files} so the template runs the whole suite"."""
    rewrite(repo / "crapkit.toml", lambda text: text.replace(" {files}", ""))
    return "dropped {files} from the scoped-test template, as doctor's FAIL line says"


def _without_repeats(text: str) -> str:
    seen, kept, last = set(), [], 0
    for block in SCOPE_BLOCK.finditer(text):
        kept.append(text[last:block.start()] + ("" if block[0] in seen else block[0]))
        seen.add(block[0])
        last = block.end()
    return "".join(kept) + text[last:]


def drop_duplicate_scope(repo: Path) -> str:
    """"duplicate scope name 'x'; each scope needs its own name", for a block pasted twice."""
    rewrite(repo / "crapkit.toml", _without_repeats)
    return "deleted the [[scope]] block pasted twice, which 0.7.0 and later refuse by name"


DOCTOR_FIXES = {"drop {files} so the template runs the whole suite": drop_files_placeholder,
                "each scope needs its own name": drop_duplicate_scope}


def _fixes_for(text: str) -> list:
    return [fix for phrase, fix in DOCTOR_FIXES.items() if phrase in text]


def resolve_doctor(box, repo: Path, doctor: Step) -> list[str]:
    """Apply the fix each failure names, as a user does; returns the edits.
    A failure no fix here knows fails the cell with doctor's own words."""
    fixes = _fixes_for(output(doctor))
    if not fixes:
        raise AssertionError(f"doctor refuses with no fix this cell knows how to apply:\n{output(doctor)}")
    edits = [fix(repo) for fix in fixes]
    for edit in edits:
        box.transcript.note(f"user edit: {edit}")
    return edits


# --- the walk ----------------------------------------------------------------------

def _doctor(box, repo: Path, line: str) -> Step:
    step = run_line(box, repo, line, expect=None)
    if step.exit:
        resolve_doctor(box, repo, step)
        step = run_line(box, repo, line, expect=0, note="guide step, after resolving doctor's failures")
    return step


def measure(box, repo: Path) -> list[Step]:
    """"Measure before changing marks": doctor (its failures resolved), then
    the coverage export the review compares marks with."""
    steps = []
    for line in guide_commands("Measure before changing marks"):
        runner = _doctor if line.split()[:2] == ["crapkit", "doctor"] else run_line
        steps.append(runner(box, repo, line))
    return steps


def marks_diff(box, repo: Path) -> str:
    return box.run(["git", "diff", "--no-color", "--", "crapkit-ratchet.tsv"], cwd=repo, expect=0).stdout


def mark_rows(repo: Path) -> list[str]:
    """The marks file's data rows: no stamp lines, no header."""
    lines = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#") and line != "path\tlong_name\tcrap"]


@dataclass
class Reseed:
    steps: list[Step]
    pruned: list[str]
    diff: str

    def step(self, words: str) -> Step:
        """The last step whose note names `words`: after a refusal, the rerun."""
        return [step for step in self.steps if words in step.note][-1]


def _reseed_line(box, repo: Path, line: str, reconcile) -> tuple[list[Step], list[str]]:
    """One fence command; a refusal goes to `reconcile(box, repo, step)`,
    then the command runs again and must pass. Returns its steps and the
    mark rows as they stood right before the run that passed."""
    before = mark_rows(repo)
    first = run_line(box, repo, line, expect=None if reconcile else 0)
    if not first.exit:
        return [first], before
    reconcile(box, repo, first)
    before = mark_rows(repo)
    return [first, run_line(box, repo, line, note=f"guide step, after the reviewed mapping: {line}")], before


def reseed(box, repo: Path, reconcile=None) -> Reseed:
    """"After upgrading, in each repo": the fence's commands one at a time.
    The rows prune removed are read from the marks file around prune's own
    passing run, for the review."""
    steps, pruned = [], []
    for line in guide_commands("Analysis version 11", contains="ratchet prune"):
        ran, before = _reseed_line(box, repo, line, reconcile)
        steps += ran
        pruned = [row for row in before if row not in mark_rows(repo)] if "ratchet prune" in line else pruned
    return Reseed(steps, pruned, marks_diff(box, repo))


def export_rows(path: Path) -> list[dict[str, str]]:
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def review(box, repo: Path, done: Reseed, export: Path) -> None:
    """The review the guide asks for before committing: prune removed exactly
    as many marks as its line names, and each names a function the fresh
    measurement no longer has."""
    prune = done.step("ratchet prune")
    named = re.search(r"pruned (\d+)", output(prune))
    assert named, f"prune named no count:\n{box.transcript.text()}"
    current = {(row["path"], row["long_name"]) for row in export_rows(export)}
    removed = [tuple(row.split("\t")[:2]) for row in done.pruned]
    assert len(removed) == int(named[1]), f"prune said {named[0]} but the diff removed {removed}"
    assert not [row for row in removed if row in current], f"prune removed a mark on a live function: {removed}"


def export_path(repo: Path) -> Path:
    """The file the guide's coverage export writes."""
    line = next(line for line in guide_commands("Measure before changing marks") if "--export" in line)
    return repo / line.split("--export", 1)[1].split()[0]


def verify(box, repo: Path, *, expect: int = 0) -> Step:
    step = run_line(box, repo, guide_span("crapkit verify"), expect=expect)
    if expect == 0:
        assert "verify OK" in output(step), box.transcript.text()
    return step


def delete_retention_keys(box, repo: Path) -> None:
    """"delete them": the guide's answer to doctor's retention WARN lines."""
    rewrite(repo / "crapkit.toml", lambda text: "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith("test_retention_")))
    box.transcript.note("user edit: deleted test_retention_days and test_retention_count, as the guide says")


RETENTION = ("test_retention_days", "test_retention_count")


def stamp_of(repo: Path) -> str:
    return (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()[0]


def analysis_of(stamp: str) -> int:
    return int(re.search(r"crapkit-analysis=(\d+)", stamp)[1])


def stamp_refusal(old: int, new: int) -> str:
    return f"[crapkit-analysis={old} lizard=1.24.0] but this run measures [crapkit-analysis={new} lizard=1.24.0]"


def upgrade_to(box, repo: Path, candidate, line: str) -> None:
    """The upgrade command, then the guide's `crapkit --version` check. An
    empty `line` is an upgrade the cell already ran (the Windows lock loop)."""
    if line:
        run_line(box, repo, line)
    printed = run_line(box, repo, guide_span("crapkit --version"))
    assert candidate.version in printed.stdout, box.transcript.text()


def refused_until_reseed(box, repo: Path, candidate) -> None:
    """Not a guide step: a verify before the reseed names both stamps."""
    old, new = analysis_of(stamp_of(repo)), analysis_version(candidate)
    if old == new:
        return
    refusal = box.run(["crapkit", "verify"], cwd=repo, expect=3, note="not a guide step: verify before the reseed")
    assert stamp_refusal(old, new) in output(refusal), box.transcript.text()


def reseed_and_verify(box, repo: Path, candidate, reconcile=None) -> Reseed:
    """The guide's reseed fence, the review, a commit, then verify."""
    done = reseed(box, repo, reconcile)
    review(box, repo, done, export_path(repo))
    assert stamp_of(repo) == f"# crapkit-analysis={analysis_version(candidate)} lizard=1.24.0", done.diff
    commit(box, repo, "reseed the marks under the candidate")
    verify(box, repo)
    return done


def retention_warnings(step: Step) -> list[str]:
    return [key for key in RETENTION if f"WARN crapkit.{key} is deprecated" in output(step)]


def saved_state(box, repo: Path, source: Source) -> None:
    """"Saved state and command behavior": runs list, then the retention keys."""
    listed = run_line(box, repo, guide_span("crapkit runs list"))
    assert all(f"run {run_id:>3} @" in listed.stdout for run_id in source.run_ids), listed.stdout
    if any("test_retention" in edit for edit in source.user_edits):
        delete_retention_keys(box, repo)
        assert not retention_warnings(box.run(["crapkit", "doctor"], cwd=repo, expect=0))
        commit(box, repo, "drop the retention keys crapkit ignores")


def after_upgrade(box, repo: Path, source: Source, candidate) -> None:
    """What the user checks once the guide is done: nothing the old CLI kept
    is gone, and a fresh MCP session runs the candidate."""
    kept(box, repo, source)
    assert server_info(box, repo)["version"] == candidate.version, box.transcript.text()


def nothing(*_args) -> None:
    return None


def walk(box, repo: Path, candidate, source: Source, line: str, *, upgraded=nothing, measured=nothing,
         reconcile=None) -> Reseed:
    """The guide in page order, from the upgrade command `line` on.
    `upgraded(box, repo)` runs after the version check,
    `measured(box, repo, doctor)` after doctor and the export, and
    `reconcile(box, repo, refusal)` when prune or seed asks for a reviewed mapping."""
    before = state_manifest.take(repo)
    upgrade_to(box, repo, candidate, line)
    upgraded(box, repo)
    doctor, _export = measure(box, repo)
    measured(box, repo, doctor)
    refused_until_reseed(box, repo, candidate)
    done = reseed_and_verify(box, repo, candidate, reconcile)
    saved_state(box, repo, source)
    after_upgrade(box, repo, source, candidate)
    state_manifest.check(box, before, state_manifest.take(repo))
    return done


# --- channels a source was installed through ----------------------------------------

ERA_PROJECTS = ("lizard", "pygments", "pathspec", "pip", "setuptools", "wheel")


def era_links(box, version: str) -> Path:
    """A find-links directory as the index stood when `version` was the newest
    release: that crapkit and its dependencies, nothing newer. uv and pipx
    record a pinned spec (`crapkit==0.6.0`) and then refuse to upgrade past it,
    so a user who installed a release when it was current is modelled with an
    unpinned install from this directory."""
    target = box.root / f"era-{version}"
    target.mkdir(exist_ok=True)
    wheelhouse = Path(box.toolchain["wheelhouse"])
    wanted = [wheels.release_wheel(wheelhouse, version)]
    wanted += [path for project in ERA_PROJECTS for path in wheelhouse.glob(f"{project}-*.whl")]
    for path in wanted:
        shutil.copy2(path, target / path.name)
    box.transcript.note(f"channel: the index as it stood at {version}: {sorted(path.name for path in wanted)}")
    return target


def tool_bin(box, argv: list[str]) -> Path:
    """The directory a tool installer puts launchers in, as the tool reports it."""
    return Path(box.run(argv, expect=0).stdout.strip())


def _era(box, version: str | None) -> dict[str, str]:
    """find-links for both installers pipx can drive: pipx 1.17 picks uv as
    its backend whenever uv is on PATH, and pip otherwise."""
    links = str(era_links(box, version)) if version else None
    return {"UV_FIND_LINKS": links, "PIP_FIND_LINKS": links} if links else {}


def uv_tool_install(box, version: str | None = None) -> Path:
    """`uv tool install crapkit` the day `version` was current (today when
    None); its bin dir goes on PATH."""
    box.run(["uv", "tool", "install", "-q", "--python", box.toolchain.python("3.12"), "crapkit"],
            env=_era(box, version), expect=0)
    directory = tool_bin(box, ["uv", "tool", "dir", "--bin"])
    box.prepend_path(directory)
    return directory


def pipx_install(box, version: str | None = None) -> Path:
    """`pipx install crapkit` the day `version` was current (today when
    None); its bin dir goes on PATH."""
    box.run(["pipx", "install", "--python", box.toolchain.python("3.12"), "crapkit"],
            env=_era(box, version), expect=0)
    directory = tool_bin(box, ["pipx", "environment", "--value", "PIPX_BIN_DIR"])
    box.prepend_path(directory)
    return directory


def suite_venv(box, python: str = "3.12") -> Path:
    """The repo's own test environment, which a tool install of crapkit does
    not provide: pytest and pytest-cov, first on PATH as `python`."""
    venv = pip_venv(box, python, name="suite-venv")
    pip_install(box, "pytest", "pytest-cov")
    return venv


# --- what the user checks afterwards ---------------------------------------------

def crapkit_json(box, repo: Path, *args: str, launcher: tuple[str, ...] = ("crapkit",)) -> dict:
    return json.loads(box.run([*launcher, *args, "--json"], cwd=repo, expect=0).stdout)


class KnownBug(AssertionError):
    """A failure a reported deploy bug explains. A cell that can meet one is
    marked xfail(strict=True, raises=state.KnownBug, reason="deploy-bug ..."),
    so any other failed assertion in it still fails the cell."""


def known_bug(holds: bool, bug: str, message: str) -> None:
    if not holds:
        raise KnownBug(f"{bug}: {message}")


def handed_out(box, repo: Path) -> dict:
    """The item `crapkit next-item` hands the next session, or {} when none."""
    return json.loads(box.run(["crapkit", "next-item"], cwd=repo, expect=0).stdout).get("item") or {}


def same_function(item: dict, claimed: dict) -> bool:
    return (item.get("path"), item.get("start")) == (claimed["path"], claimed["start"])


def claim_holds(box, repo: Path, source: Source) -> None:
    """The function the old CLI claimed is not handed to the next session,
    under its old name or the one the new reader gives it."""
    item = handed_out(box, repo)
    known_bug(not same_function(item, source.facts["claimed"]), "deploy-upgrade-1",
              f"claimed under {source.version} as {source.facts['claimed']['function']!r}, "
              f"handed out again as {item.get('function')!r}")


def kept(box, repo: Path, source: Source, launcher: tuple[str, ...] = ("crapkit",)) -> None:
    """Runs, open claims and overrides the old CLI listed, listed again by the
    CLI the user runs now (`launcher`, the one on PATH by default)."""
    listed = {name: crapkit_json(box, repo, name, launcher=launcher) for name in ("runs", "claims", "overrides")}
    ids = [run["id"] for run in listed["runs"]["runs"]]
    claims = {(claim["path"], claim["long_name"]) for claim in listed["claims"]["claims"]}
    overrides = {(row["path"], row["function"], row["reason"]) for row in listed["overrides"]["overrides"]}
    assert not set(source.run_ids) - set(ids), f"runs {source.run_ids} before, {ids} after"
    assert not set(source.claims) - claims, f"claims {source.claims} before, {sorted(claims)} after"
    assert not set(source.overrides) - overrides, f"overrides {source.overrides} before, {sorted(overrides)} after"


def server_info(box, repo: Path, argv: list[str] | None = None) -> dict:
    """serverInfo from a fresh MCP session's initialize."""
    with McpClient.in_box(box, argv or ["crapkit", "mcp"], cwd=repo) as client:
        return client.initialize()["serverInfo"]


def analysis_version(candidate) -> int:
    """The candidate's analysis version, read from its stamped source."""
    source = (Path(candidate.staged) / "src" / "crapkit" / "analyze.py").read_text(encoding="utf-8")
    return int(re.search(r"^ANALYSIS_VERSION = (\d+)", source, re.M)[1])
