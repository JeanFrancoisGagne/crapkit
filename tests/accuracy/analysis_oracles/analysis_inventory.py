"""crapkit's inventory rows for a set of source files, read through its CLI.

measure() writes the files into a fresh git repo whose one scope, `all`,
claims every language crapkit reads, runs `crapkit inventory --export`
through kit.drive and parses the export with read_export(). That reader is
written from docs/portable-records.md ("Reading exports in another tool"),
not from crapkit's own record code. This module imports no crapkit, so a test
that reads crapkit through it can still be a calc's independent test.

shared() measures one file set once per session under a lock, so pytest-xdist
workers that ask for the same set wait for the first and read its export.

A retro replay reruns a check at an older crapkit commit; retro_tree() below
writes the tree that commit can read, and only when a replay asks for it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib

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


# The README "Languages" table: each file suffix and the language key that claims it.
SUFFIX_LANGUAGE = {
    ".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".vue": "vue",
    ".swift": "swift", ".go": "go", ".rs": "rust", ".sh": "shell", ".bash": "shell",
    ".ps1": "powershell", ".psm1": "powershell", ".c": "cpp", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".h": "cpp", ".hpp": "cpp", ".m": "objectivec", ".mm": "objectivec",
    ".java": "java", ".zig": "zig",
}


def languages_of(paths) -> tuple:
    """The language keys the README table gives the suffixes of `paths`, in
    LANGUAGES order; every language when no path has a listed suffix. A set
    names only its own languages, so an older crapkit that knows fewer of them
    still accepts the config (a retro replay at a before commit)."""
    found = {SUFFIX_LANGUAGE.get(Path(path).suffix.lower()) for path in paths}
    return tuple(language for language in LANGUAGES if language in found) or LANGUAGES


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


# --- retro replays: a tree an older crapkit can read --------------------------------------
#
# Two limits of older commits refuse or empty a tree this module writes. build()
# works around each one only when its variable is set; a normal run sets neither.
#
# CRAPKIT_ACCURACY_ROOT_PATHS=entries. Before c24e6a4 a root scope path (".")
# owned no file, and from 0a4a55b to c24e6a4 it was a config error. Each root
# path is written as the tree's top-level entries instead: a directory claims
# the files under it and a loose file claims itself (docs/configuration.md
# "[[scope]]": a bare path also matches that exact file). An entry ties with
# another scope's path of the same length where "." never would, so this serves
# commits that match scopes in declaration order, the ones it exists for.
#
# CRAPKIT_ACCURACY_LANGUAGES=python,typescript. A commit refuses a config that
# names a language it has no reader for. The tree keeps only the source files
# of the listed languages (README table) and each scope only the listed keys.
ROOT_PATHS_ENV = "CRAPKIT_ACCURACY_ROOT_PATHS"
LANGUAGES_ENV = "CRAPKIT_ACCURACY_LANGUAGES"
CONFIG = "crapkit.toml"


def top_entries(files) -> list[str]:
    """The first component of every path but the config's, sorted, once each."""
    return sorted({path.split("/")[0] for path in files if path != CONFIG})


def is_root(declared: str) -> bool:
    """A declared scope path naming the repo root: ".", "./", ".\\", "/" or ""."""
    return declared.replace(chr(92), "/").strip("/") in ("", ".")


def rooted(paths: list, entries: list) -> list:
    """A scope's paths with its root paths replaced by `entries`."""
    rest = [path for path in paths if not is_root(path)]
    return paths if len(rest) == len(paths) else list(entries) + rest


def rewrite_lists(text: str, key: str, change) -> str:
    """Each one-line `key = [...]` of a TOML text passed through change(); a
    line change() leaves equal stays byte for byte."""
    return "\n".join(_rewrite_line(line, key, change) for line in text.split("\n"))


def _rewrite_line(line: str, key: str, change) -> str:
    if not line.startswith(key + " ="):
        return line
    found = tomllib.loads(line)[key]
    changed = change(found)
    return line if changed == found else f"{key} = {json.dumps(changed, ensure_ascii=False)}"


def _kept(path: str, keep) -> bool:
    language = SUFFIX_LANGUAGE.get(Path(path).suffix.lower())
    return language is None or language in keep


def _text(data) -> str:
    return data.decode("utf-8") if isinstance(data, bytes) else data


def _only(found: list, keep) -> list:
    return [key for key in found if key in keep]


def limited(files: dict, text: str, keep) -> tuple:
    """The files of the `keep` languages (and every non-source file), and the
    config text with each scope's languages cut to `keep`."""
    files = {path: data for path, data in files.items() if _kept(path, keep)}
    return files, rewrite_lists(text, "languages", lambda found: _only(found, keep))


def _keys(value: str) -> tuple:
    return tuple(key for key in value.split(",") if key)


def retro_tree(files: dict, environ=None) -> dict:
    """`files` as an older crapkit reads them under the two variables above,
    read from `environ` (os.environ when None); `files` itself when neither is set."""
    environ = os.environ if environ is None else environ
    keep = _keys(environ.get(LANGUAGES_ENV, ""))
    by_entries = environ.get(ROOT_PATHS_ENV) == "entries"
    return _retro(files, keep, by_entries) if keep or by_entries else files


def _retro(files: dict, keep: tuple, by_entries: bool) -> dict:
    text = _text(files.get(CONFIG, ""))
    if keep:
        files, text = limited(files, text, keep)
    if by_entries:
        entries = top_entries(files)
        text = rewrite_lists(text, "paths", lambda found: rooted(found, entries))
    return {**files, CONFIG: text} if CONFIG in files else files


# --- building and measuring --------------------------------------------------------------

def _write(top: Path, files: dict) -> None:
    for path, content in files.items():
        target = top / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def build(files: dict, top: Path) -> Path:
    """A one-commit git repo at `top` holding `files` ({posix path: str or bytes}),
    as retro_tree() writes them."""
    top.mkdir(parents=True)
    _write(top, retro_tree(files))
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
    tree = {"crapkit.toml": config(languages_of(files)), **files}
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
