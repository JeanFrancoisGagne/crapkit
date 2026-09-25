"""crapkit's inventory rows for a set of source files, read through its CLI.

measure() writes the files into a fresh git repo whose one scope, `all`,
claims every language crapkit reads, runs `crapkit inventory --export`
through kit.drive and parses the export with read_export(). That reader is
written from docs/portable-records.md ("Reading exports in another tool"),
not from crapkit's own record code. This module imports no crapkit, so a test
that reads crapkit through it can still be a calc's independent test.

shared() measures one file set once per session under a lock, so pytest-xdist
workers that ask for the same set wait for the first and read its export.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil

from filelock import FileLock

from accuracy.kit import drive, repos

# Every language key crapkit's config accepts (README "Languages").
LANGUAGES = ("python", "typescript", "tsx", "javascript", "vue", "swift", "go", "rust",
             "shell", "cpp", "objectivec", "java", "zig", "powershell")
MARKER = "@crapkit-record-v1"
MARKER_PREFIX = "@crapkit-record-"
INTS = ("start", "end", "ccn_std", "ccn_mod", "ccn", "nloc", "params", "nesting", "cognitive",
        "occurrence")
GIT_CONFIG = "[core]\n\tautocrlf = false\n\teol = lf\n\tquotePath = true\n"


def config(languages=LANGUAGES, extra: str = "") -> str:
    """A crapkit.toml whose one root scope claims every file of `languages`."""
    return ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "all"\npaths = ["."]\n'
            f"languages = {json.dumps(list(languages))}\ncoverage_optional = true\n{extra}")


# --- the export reader, from docs/portable-records.md ----------------------------------

class ExportError(ValueError):
    """An export row the documented format does not allow."""


def _marked(rest: str) -> list[str]:
    values = json.loads(rest)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ExportError("a marked row holds a JSON array of strings")
    return values


def fields(line: str) -> list[str]:
    """One physical row's fields. A row whose first field is the version marker
    holds a JSON array after one tab; an unknown version is refused."""
    head, tab, rest = line.partition("\t")
    if head == MARKER and tab:
        return _marked(rest)
    if head.startswith(MARKER_PREFIX):
        raise ExportError(f"unknown record encoding {head!r}")
    return line.split("\t")


def _typed(header: list[str], values: list[str]) -> dict:
    if len(values) != len(header):
        raise ExportError(f"a row has {len(values)} fields, the header {len(header)}")
    row = dict(zip(header, values))
    row.update({name: int(row[name]) for name in INTS if name in row})
    return row


def read_export(text: str) -> list[dict]:
    """Every row of an inventory export: split at LF, drop one trailing CR."""
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    header = lines[0].split("\t")
    return [_typed(header, fields(line)) for line in lines[1:] if line]


# --- naming a row ------------------------------------------------------------------------

ANONYMOUS = "(anonymous)"


def bare(long_name: str) -> str:
    """The identifier a probe names: the text before the parameter list (a
    parenthesis, or the first space where the long name has no parenthesis, as
    in Go, Rust, Swift, Zig and PowerShell), last component after `::` or `.`.
    A function lizard could not name reads `(anonymous)`."""
    if long_name.startswith(ANONYMOUS):
        return ANONYMOUS
    head = long_name.split("(")[0].strip()
    head = head.split()[0] if head else head
    return head.replace("::", ".").split(".")[-1].strip()


@dataclass(frozen=True)
class Measured:
    rows: tuple
    code: int
    stderr: str
    root: str

    def in_file(self, path: str) -> list[dict]:
        return [row for row in self.rows if row["path"] == path]

    def named(self, path: str, name: str) -> list[dict]:
        return [row for row in self.in_file(path) if bare(row["long_name"]) == name]


# --- building and measuring --------------------------------------------------------------

def _write(top: Path, files: dict) -> None:
    for path, content in files.items():
        target = top / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def build(files: dict, top: Path) -> Path:
    """A one-commit git repo at `top` holding `files` ({posix path: str or bytes})."""
    top.mkdir(parents=True)
    _write(top, files)
    repos.git(top, "init", "-q", "-b", "main")
    with (top / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write(GIT_CONFIG)
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "probes", date=repos.EPOCH)
    return top


# lizard's stock Python reader in place of crapkit's corrected one: the launch
# code stubs the reader's registration before crapkit's analysis module binds
# it, and keeps the analysis serial, since a pool worker would register again.
STOCK_PYTHON = ("-c", "; ".join((
    "import contextlib, sys",
    "import crapkit.lizardpython as reader",
    "reader.register = lambda: None",
    "import crapkit.analyze as analyze",
    "analyze._pool_for = lambda *args, **kwargs: contextlib.nullcontext(None)",
    "from crapkit.cli import main",
    "sys.exit(main(sys.argv[2:]))")))
PLAIN = ("-m",)


def run_inventory(root: Path, out: Path, spawn: bool = False, launch: tuple = PLAIN,
                  env: dict | None = None) -> Measured:
    done = drive.Driver(root, spawn=spawn, launch=launch, env=env).run(
        "inventory", "--export", str(out))
    rows = read_export(out.read_bytes().decode("utf-8")) if out.is_file() else []
    return Measured(tuple(rows), done.code, done.stderr, str(root))


def measure(files: dict, work: Path, spawn: bool = False, launch: tuple = PLAIN,
            env: dict | None = None) -> Measured:
    """crapkit's inventory of `files` plus a crapkit.toml, unless `files` brings one.
    `env` adds to the child's environment."""
    tree = {"crapkit.toml": config(), **files}
    return run_inventory(build(tree, work / "repo"), work / "inventory.tsv", spawn, launch, env)


def _key(files: dict, launch: tuple = PLAIN) -> str:
    hashed = hashlib.sha256("|".join(launch).encode("utf-8"))
    for path in sorted(files):
        content = files[path]
        data = content.encode("utf-8") if isinstance(content, str) else content
        hashed.update(path.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return hashed.hexdigest()[:16]


def _saved(work: Path) -> Measured | None:
    manifest = work / "measured.json"
    if not manifest.is_file():
        return None
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    return Measured(tuple(saved["rows"]), saved["code"], saved["stderr"], saved["root"])


def _save(work: Path, measured: Measured) -> Measured:
    record = {"rows": list(measured.rows), "code": measured.code, "stderr": measured.stderr,
              "root": measured.root}
    (work / "measured.json").write_text(json.dumps(record), encoding="utf-8")
    return measured


def shared(files: dict, base: Path, launch: tuple = PLAIN) -> Measured:
    """measure() once per file set under `base`, whichever worker asks first.
    Spawned, since a set past 16 files reaches crapkit's analysis pool."""
    work = base / f"analysis-{_key(files, launch)}"
    base.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work) + ".lock"):
        found = _saved(work)
        if found is None:
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            found = _save(work, measure(files, work, spawn=True, launch=launch))
    return found
