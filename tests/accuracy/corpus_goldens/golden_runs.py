"""The measured runs the corpus goldens pin, each measured once per session.

- small: kit.corpus_run's measurement of the small corpus (the inventory
  export, one coverage run and the kit's read surfaces).
- session: a private copy of that repo driven through every other surface.
  First the read side (brief, explain, coupling, claims, the ratchet report,
  rescore, the gate preview, two refusals, all 12 MCP tools and the CLI
  command each maps to), then a session that changes state: seed the ratchet,
  edit a function past its ceiling and add an untested one, verify with SARIF
  and an emitted baseline, verify again with --github, and read the runs, the
  digest and the trend after it. The Action's comment renders last, from the
  coverage, verify and worklist payloads.
- history: the history bundle cloned and measured like the small corpus, so
  churn, coupling, renames and the dormant list have commits to read.

Each measurement runs under a FileLock and leaves a manifest, so pytest-xdist
workers share one measurement per session the way kit.corpus_run does.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from filelock import FileLock

import hang_guard
from accuracy.kit import corpus_run, drive, goldens, repos

PACKET = Path(__file__).resolve().parent
REPO = PACKET.parents[2]
GOLDENS = PACKET / "goldens"
BUNDLE = PACKET / "history" / "small.bundle"
CORPUS_TOML = PACKET / "corpus.toml"
COMMENT = REPO / "tools" / "action" / "comment.py"
MANIFEST = "manifest.json"
EDITED = "src/py/grades.py"
FRESH = "src/py/fresh.py"

# (output name, argv, stdin) for the read side; {out} is the outputs directory.
READS = (
    ("brief.json", ("brief", "src/py/grades.py", "curve", "--json")),
    ("brief-twin.json", ("brief", "src/py/twins.py", "helper#2", "--json")),
    ("brief-batch.json", ("brief", "--batch", "3", "--json")),
    ("explain.json", ("explain", "src/py/grades.py", "curve", "--json")),
    ("explain-history.json", ("explain", "src/py/grades.py", "curve", "--history", "--json")),
    ("coupling.json", ("coupling", "--json")),
    ("claims.json", ("claims", "list", "--json")),
    ("ratchet-report.json", ("ratchet", "report", "--json")),
    ("overrides.json", ("overrides", "--json")),
    ("rescore.json", ("rescore", "src/py/grades.py", "src/web/arrows.js", "--json")),
    ("gate.json", ("rescore", "src/py/grades.py", "--gate", "--json")),
    ("worklist-batches.json", ("worklist", "--batches", "2", "--json")),
    ("next-item-top.json", ("next-item", "--top", "5")),
    ("refusal-name.json", ("brief", "src/py/grades.py", "no_such_function", "--json")),
    ("refusal-scope.json", ("worklist", "--scope", "no-such-scope", "--json")),
)

# The 12 MCP tools, each with the arguments a call passes, and the CLI argv the
# docs map it to (docs/agent-json.md "MCP server"). test_mcp_equals_cli compares
# the pairs; the argv here is written from the docs, not read from mcp_server.py.
MCP_CALLS = (
    ("get_next_item", {"top": 3}, ("next-item", "--top", "3")),
    ("list_worklist", {"top": 4, "scope": ["py"]},
     ("worklist", "--top", "4", "--scope", "py", "--json")),
    ("list_runs", {}, ("runs", "--json")),
    ("get_trend", {}, ("trend", "--json")),
    ("get_function_brief", {"path": "src/py/grades.py", "name": "curve"},
     ("brief", "src/py/grades.py", "curve", "--json")),
    ("get_function_history", {"path": "src/py/grades.py", "name": "letter"},
     ("explain", "src/py/grades.py", "letter", "--json")),
    ("check_config", {}, ("doctor", "--json")),
    ("list_coupled_files", {}, ("coupling", "--json")),
    ("list_duplicate_functions", {}, ("duplication", "--json")),
    ("get_ratchet_report", {}, ("ratchet", "report", "--json")),
    ("check_gate", {"path": "src/py/grades.py"}, ("rescore", "--gate", "src/py/grades.py",
                                                  "--json")),
    ("list_claims", {}, ("claims", "list", "--json")),
)

EDIT_LETTER = '''

def letter(score):
    if score >= 97:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    if score < 0:
        return "?"
    return "F"
'''
FRESH_SOURCE = '''"""Added after the baseline: no test imports it."""


def route(a, b, c, d):
    if a:
        return 1
    if b:
        return 2
    if c and d:
        return 3
    if c or d:
        return 4
    for item in (a, b):
        if item:
            return 5
    return 0
'''

# The session that changes state, after the reads. verify refuses a scope no
# lane measures, so the session first does what doctor asks for the legacy scope
# (coverage_optional) and scores again, then seeds the ratchet on that run.
PREPARE = (
    ("coverage-2.json", ("coverage", "--json", "--reuse-artifacts")),
    ("seed.txt", ("ratchet", "seed")),
)
# After the edit: the verdict and what reads the runs it wrote.
JUDGE = (
    ("verify.json", ("verify", "--json", "--reuse-artifacts", "--sarif", "{out}/verify.sarif",
                     "--emit-baseline", "{out}/baseline.tsv")),
    ("verify-github.txt", ("verify", "--github", "--reuse-artifacts")),
    ("runs-after.json", ("runs", "list", "--json")),
    ("digest-after.txt", ("digest",)),
    ("trend-after.json", ("trend", "--json")),
    ("worklist-after.json", ("worklist", "--json")),
)


@dataclass(frozen=True)
class Measured:
    """What corpus_run.CorpusRun is, for a run this module made."""
    root: Path
    outputs: Path
    date_now: int
    codes: dict

    def output(self, name: str) -> str:
        return (self.outputs / name).read_text(encoding="utf-8")

    def private_copy(self, dest: Path) -> Path:
        shutil.copytree(self.root, dest, symlinks=True)
        return dest

    @property
    def raw(self) -> Path:
        """What a run kept beside its outputs and out of its goldens."""
        return self.outputs.parent / "raw"


def _table() -> dict:
    return tomllib.loads(CORPUS_TOML.read_text(encoding="utf-8"))


def history_date_now() -> int:
    return int(_table()["history"]["git_test_date_now"])


def small(base: Path) -> corpus_run.CorpusRun:
    return corpus_run.measure(corpus_run.SMALL, base, corpus_run.date_now())


def _once(work: Path, measure) -> dict:
    """Run `measure(work)` once under a lock; later callers read its manifest."""
    work.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work) + ".lock"):
        manifest = work / MANIFEST
        if not manifest.is_file():
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            manifest.write_text(json.dumps(measure(work), indent=1), encoding="utf-8")
        return json.loads(manifest.read_text(encoding="utf-8"))


def _measured(manifest: dict) -> Measured:
    return Measured(Path(manifest["root"]), Path(manifest["outputs"]), manifest["date_now"],
                    manifest["codes"])


def _record(driver: drive.Driver, outputs: Path, name: str, argv: tuple) -> int:
    result = driver.run(*[part.replace("{out}", outputs.as_posix()) for part in argv])
    (outputs / name).write_text(result.stdout, encoding="utf-8")
    (outputs / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    return result.code


def golden_form(result: dict) -> dict:
    """An MCP result as a golden holds it: its text parsed when it is the JSON
    structuredContent repeats, so normalization reaches every value inside."""
    text = result.get("content", [{}])[0].get("text", "")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    return {"isError": result.get("isError"), "text": parsed,
            "structuredContent": result.get("structuredContent")}


def _mcp(driver: drive.Driver, outputs: Path, raw: Path) -> dict:
    """Each tool's result, raw under `raw` for the MCP-equals-CLI check and in
    golden form under `outputs`, and the CLI command the docs map it to."""
    results = driver.mcp([(name, arguments) for name, arguments, _ in MCP_CALLS])
    codes = {}
    for (name, _, argv), result in zip(MCP_CALLS, results):
        (raw / f"mcp-{name}.json").write_text(json.dumps(result), encoding="utf-8")
        (outputs / f"mcp-{name}.json").write_text(json.dumps(golden_form(result)),
                                                  encoding="utf-8")
        codes[f"cli-{name}.json"] = _record(driver, outputs, f"cli-{name}.json", argv)
    return codes


def _accept_legacy(root: Path) -> None:
    """Declare the legacy scope cc-only, as doctor's finding about it says to."""
    config = root / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    scope = 'name = "legacy"\npaths = ["src/legacy"]\nlanguages = ["python"]\n'
    config.write_text(text.replace(scope, scope + "coverage_optional = true\n"),
                      encoding="utf-8")


def _edit(root: Path) -> None:
    """Replace letter() with a ccn 7 one and add an untested ccn 8 module."""
    source = (root / EDITED).read_text(encoding="utf-8")
    start = source.index("\n\ndef letter(")
    end = source.index("\n\ndef curve(")
    (root / EDITED).write_text(source[:start] + EDIT_LETTER + source[end:], encoding="utf-8")
    (root / FRESH).write_text(FRESH_SOURCE, encoding="utf-8")


def comment(outputs: Path, coverage: Path, worklist: Path, changed: list[str]) -> int:
    """The Action's comment over saved payloads, as action.yml's comment step runs it."""
    (outputs / "changed.z").write_bytes("\0".join(changed).encode("utf-8"))
    argv = [sys.executable, str(COMMENT), "--coverage", str(coverage), "--coverage-exit", "0",
            "--verify", str(outputs / "verify.json"), "--verify-exit", "6",
            "--worklist", str(worklist), "--changed-z", str(outputs / "changed.z"),
            "--top", "5", "--out", str(outputs / "pr-comment.md"),
            "--json-out", str(outputs / "pr-comment.request.json")]
    return hang_guard.run(argv, cwd=outputs, text=True, encoding="utf-8").returncode


def _session(work: Path, run: corpus_run.CorpusRun) -> dict:
    root = run.private_copy(work / "repo")
    outputs, raw = work / "outputs", work / "raw"
    outputs.mkdir()
    raw.mkdir()
    driver = drive.Driver(root, date_now=run.date_now, spawn=True)
    codes = {"doctor.json": _record(driver, outputs, "doctor.json", ("doctor", "--json"))}
    codes.update({name: _record(driver, outputs, name, argv) for name, argv in READS})
    codes.update(_mcp(driver, outputs, raw))
    _accept_legacy(root)
    codes.update({name: _record(driver, outputs, name, argv) for name, argv in PREPARE})
    _edit(root)
    codes.update({name: _record(driver, outputs, name, argv) for name, argv in JUDGE})
    codes["pr-comment.md"] = comment(outputs, run.outputs / "coverage.json",
                                     run.outputs / "worklist.json", [EDITED, FRESH])
    return {"root": str(root), "outputs": str(outputs), "date_now": run.date_now, "codes": codes}


def session(base: Path) -> Measured:
    run = small(base)
    work = base / f"session-{Path(run.root).parent.name}"
    return _measured(_once(work, lambda target: _session(target, run)))


def _bundle_key() -> str:
    return hashlib.sha256(BUNDLE.read_bytes()).hexdigest()[:16]


def clone_history(dest: Path) -> Path:
    """The history bundle checked out with no line-ending conversion."""
    repos.git(dest.parent, "clone", "-q", "-b", "main", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
              "-c", "core.quotePath=true", str(BUNDLE), dest.name)
    for key, value in repos.CONFIG:
        repos.git(dest, "config", key, value)
    return dest


def _history(work: Path) -> dict:
    root = clone_history(work / "repo")
    outputs = work / "outputs"
    outputs.mkdir()
    now = history_date_now()
    driver = drive.Driver(root, date_now=now, spawn=True)
    codes = {name: _record(driver, outputs, name, argv)
             for name, argv in corpus_run.WRITES + corpus_run.SURFACES}
    codes["coupling.json"] = _record(driver, outputs, "coupling.json", ("coupling", "--json"))
    codes["brief.json"] = _record(driver, outputs, "brief.json",
                                  ("brief", "src/web/arrows.js", "dispatch", "--json"))
    return {"root": str(root), "outputs": str(outputs), "date_now": now, "codes": codes}


def history(base: Path) -> Measured:
    work = base / f"history-{_bundle_key()}-{history_date_now()}"
    return _measured(_once(work, _history))


def interpreter_spellings() -> tuple[str, ...]:
    """Every way output can spell the interpreter crapkit ran under: crapkit
    names itself `<sys.executable> -m crapkit` when no console script started
    it (invocation._self), and that path is the machine's, not the calculation's."""
    python = os.environ.get(drive.PYTHON_ENV) or sys.executable
    forms = {python, str(Path(python).resolve())}
    forms |= {form.replace("\\", "/") for form in list(forms)}
    forms |= {form.replace("\\", "\\\\") for form in list(forms) if "\\" in form}
    return tuple(sorted(forms, key=len, reverse=True))


def normalized(run) -> dict[str, str]:
    """kit.goldens' normalized outputs, with the interpreter path replaced too."""
    texts = goldens.goldens_of(run)
    for spelling in interpreter_spellings():
        texts = {name: text.replace(spelling, "<python>") for name, text in texts.items()}
    return texts


RUNS = {"small": small, "session": lambda base: session(base),
        "history": lambda base: history(base)}


def all_goldens(base: Path) -> dict[str, dict[str, str]]:
    """{golden directory: {file: normalized text}} for the three runs."""
    return {name: normalized(measure(base)) for name, measure in RUNS.items()}


def stored(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes().decode("utf-8") for path in directory.iterdir()}


def shared_base(tmp_path_factory) -> Path:
    """The session's temp root every xdist worker shares (conftest's rule)."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


def git_head(root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True).stdout.strip()
