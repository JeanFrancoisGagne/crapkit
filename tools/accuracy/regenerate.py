"""Re-record the small corpus's artifacts, rebuild its history bundle, and rewrite its goldens.

    python tools/accuracy/regenerate.py record --python314 PATH [--node-modules DIR]
    python tools/accuracy/regenerate.py history
    python tools/accuracy/regenerate.py goldens [--corpus DIR]

`record` runs the small corpus's own recording suite (small/tests/) under the
pinned producers and rewrites small/recorded/: coverage.py 7.16.1 on CPython
3.14, because the corpus holds a PEP 750 t-string and a PEP 758 except clause
that only 3.14 imports, and vitest 5.0.1 with @vitest/coverage-istanbul from
tools/accuracy/node/nightly. Each artifact is written the way crapkit expects
a repo-relative one: file keys with forward slashes, the absolute root of the
temporary copy cut out, and the producers' clocks (coverage.py's
meta.timestamp, JUnit timestamps, times and hostnames) replaced by fixed
values, so recording twice gives the same bytes.

`history` rebuilds history/small.bundle: a synthetic 60-commit history with
fixed dates whose last commit holds exactly the small corpus. It has a rename,
a copy, a merge, a non-ASCII path, commits on both sides of a month end and one
commit touching more than 30 files.

`goldens` measures the small corpus and the history, and rewrites goldens/.
With `--corpus DIR` (a built full corpus) it also measures every member and
rewrites goldens/full.tsv, the row count and sha256 of each member's exports.
A golden that moves is a change the change-control packet must see declared:
the command prints what moved and the declare command to run next.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from accuracy.kit import corpus_run, goldens, repos  # noqa: E402

PACKET = REPO / "tests" / "accuracy" / "corpus_goldens"
SMALL = PACKET / "small"
BUNDLE = PACKET / "history" / "small.bundle"
GOLDENS = PACKET / "goldens"
NODE_MODULES = REPO / "tools" / "accuracy" / "node" / "nightly" / "node_modules"
FIXED_TIME = "2026-09-24T00:00:00"
DECLARE = ('python tools/accuracy/change_control.py declare <id> --kind '
           '<fix|definition|feature|none> --calcs "<calc>,..."')


# --- recording ---------------------------------------------------------------------------

def _run(argv: list[str], cwd: Path, env: dict | None = None) -> None:
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {done.returncode}:\n{done.stdout[-3000:]}"
                           f"\n{done.stderr[-3000:]}")


def _copy_corpus(scratch: Path) -> Path:
    copy = scratch / "small"
    shutil.copytree(SMALL, copy, ignore=shutil.ignore_patterns("recorded", "__pycache__"))
    return copy


def forward_keys(files: dict) -> dict:
    return {key.replace("\\", "/"): value for key, value in files.items()}


def coveragepy_canonical(data: dict) -> dict:
    """A coverage.py JSON report with forward-slash keys and a fixed clock."""
    meta = dict(data["meta"], timestamp=FIXED_TIME)
    return {**data, "meta": meta, "files": forward_keys(data["files"])}


def _cut_text(value: str, roots: tuple[str, ...]) -> str:
    """A path under the root becomes repo-relative with forward slashes; any
    other string only loses the root where it quotes one."""
    under = next((root for root in roots if value.startswith(root)), None)
    if under is not None:
        return value[len(under):].replace("\\", "/")
    for root in roots:
        value = value.replace(root, "")
    return value


_CUTTERS = {
    dict: lambda value, roots: {cut_root(key, roots): cut_root(item, roots)
                                for key, item in value.items()},
    list: lambda value, roots: [cut_root(item, roots) for item in value],
    str: _cut_text,
}


def cut_root(value, roots: tuple[str, ...]):
    """Every string in a parsed artifact with the recording's root removed."""
    cutter = _CUTTERS.get(type(value))
    return cutter(value, roots) if cutter else value


def root_forms(copy: Path) -> tuple[str, ...]:
    """The copy's root as absolute paths print it, each with its trailing separator."""
    forms = {str(copy), str(copy.resolve()), copy.as_posix(), copy.resolve().as_posix()}
    return tuple(sorted({form.rstrip("/\\") + sep for form in forms for sep in ("/", "\\")},
                        key=len, reverse=True))


_JUNIT_CLOCK = re.compile(r'\s(timestamp|hostname|time)="[^"]*"')


def junit_canonical(text: str, roots: tuple[str, ...]) -> str:
    """A JUnit file with its clock and host fixed and the recording root cut out."""
    for root in roots:
        text = text.replace(root, "")
    return _JUNIT_CLOCK.sub(lambda match: f' {match[1]}="{_fixed(match[1])}"', text)


def _fixed(attribute: str) -> str:
    return {"timestamp": FIXED_TIME, "hostname": "recorder", "time": "0.000"}[attribute]


def _write_json(path: Path, data) -> None:
    path.write_bytes((json.dumps(data, sort_keys=True) + "\n").encode("utf-8"))


def record_python(copy: Path, python: str, out: Path) -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_FILE": str(copy / ".cov")}
    _run([python, "-m", "coverage", "run", "--branch", "--source=src", "-m", "pytest",
          "tests/py", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
          f"--junitxml={copy / 'py-junit.xml'}", "-o", "junit_family=xunit2"], copy, env)
    _run([python, "-m", "coverage", "json", "-o", str(copy / "py.json")], copy, env)
    data = json.loads((copy / "py.json").read_text(encoding="utf-8"))
    _write_json(out / "py.json", coveragepy_canonical(data))
    junit = (copy / "py-junit.xml").read_text(encoding="utf-8")
    (out / "py-junit.xml").write_bytes(junit_canonical(junit, root_forms(copy)).encode("utf-8"))


def _link(target: Path, link: Path) -> None:
    """A directory link: a junction on Windows, which needs no privilege."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def record_js(copy: Path, node_modules: Path, out: Path) -> None:
    _link(node_modules, copy / "node_modules")
    reports = copy / ".cov-js"
    _run(["node", str(node_modules / "vitest" / "vitest.mjs"), "run", "--config",
          "tests/vitest.config.mjs", "--coverage", f"--coverage.reportsDirectory={reports}",
          "--reporter=default", "--reporter=junit",
          f"--outputFile.junit={copy / 'js-junit.xml'}"], copy)
    roots = root_forms(copy)
    data = json.loads((reports / "coverage-final.json").read_text(encoding="utf-8"))
    _write_json(out / "js.json", cut_root(data, roots))
    junit = (copy / "js-junit.xml").read_text(encoding="utf-8")
    (out / "js-junit.xml").write_bytes(junit_canonical(junit, roots).encode("utf-8"))
    os.rmdir(copy / "node_modules") if os.name == "nt" else (copy / "node_modules").unlink()


def record(python: str, node_modules: Path, out: Path = SMALL / "recorded") -> list[str]:
    """Record both lanes' artifacts into `out` (the small corpus's recorded/ by default)."""
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="crapkit-record-",
                                     ignore_cleanup_errors=True) as scratch:
        copy = _copy_corpus(Path(scratch))
        record_python(copy, python, out)
        record_js(copy, node_modules, out)
    return sorted(path.name for path in out.iterdir())


# --- the history bundle ----------------------------------------------------------------

DAY = 86_400
HISTORY_START = repos.EPOCH - 150 * DAY  # 2025-01-16


def _corpus_files() -> dict[str, bytes]:
    return repos.tree(SMALL)


def build_history(bundle: Path = BUNDLE) -> str:
    """Build the history spec and bundle its main branch; returns the tip commit."""
    from accuracy.corpus_goldens import history_spec
    spec = repos.Spec(steps=tuple(history_spec.steps(_corpus_files(), HISTORY_START, repos.EPOCH)))
    with tempfile.TemporaryDirectory(prefix="crapkit-history-",
                                     ignore_cleanup_errors=True) as scratch:
        built = repos.build(spec, Path(scratch) / "repo")
        bundle.parent.mkdir(parents=True, exist_ok=True)
        repos.git(built.top, "bundle", "create", str(bundle), "HEAD", "main")
        return repos.git(built.top, "rev-parse", "main").strip()


# --- goldens ------------------------------------------------------------------------------

def moved_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The files of one golden directory that are new, gone or different."""
    return sorted(name for name in {*before, *after} if before.get(name) != after.get(name))


def rewrite_goldens() -> list[str]:
    from accuracy.corpus_goldens import golden_runs
    with tempfile.TemporaryDirectory(prefix="crapkit-goldens-",
                                     ignore_cleanup_errors=True) as scratch:
        found = golden_runs.all_goldens(Path(scratch))
    moved = []
    for name, texts in found.items():
        moved += [f"{name}/{file}" for file in moved_files(golden_runs.stored(GOLDENS / name),
                                                           texts)]
        goldens.write(GOLDENS / name, texts)
    return moved


def rewrite_printed() -> list[str]:
    """This OS's printed-commands golden; the other OS's file stays as it is."""
    from accuracy.corpus_goldens import printed_runs
    with tempfile.TemporaryDirectory(prefix="crapkit-printed-",
                                     ignore_cleanup_errors=True) as scratch:
        text = printed_runs.printed_text(printed_runs.measure(Path(scratch)))
    golden = printed_runs.golden_path()
    old = golden.read_bytes().decode("utf-8") if golden.is_file() else None
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_bytes(text.encode("utf-8"))
    return [] if old == text else [f"printed/{golden.name}"]


def measure_full(corpus: Path, names: list[str]) -> dict:
    """{(member, export): (rows, sha256)} for each named member of a built full corpus."""
    from accuracy.corpus_goldens import full_runs
    found = {}
    with tempfile.TemporaryDirectory(prefix="crapkit-full-",
                                     ignore_cleanup_errors=True) as scratch:
        for name in names:
            texts = full_runs.measure_member(corpus, name, Path(scratch) / name)
            found.update({(name, export): full_runs.digest(text) for export, text in texts.items()})
    return found


def rewrite_full(corpus: Path, golden: Path, names: list[str]) -> list[str]:
    """The full-corpus golden rewritten from the named members; returns what moved."""
    from accuracy.corpus_goldens import full_runs
    found = measure_full(corpus, names)
    before = full_runs.read_golden(golden)
    full_runs.write_golden(found, golden)
    return [f"full/{member}/{export}" for member, export in moved_files(before, found)]


def _rewrite_corpus(corpus: Path) -> list[str]:
    from accuracy.corpus_goldens import full_runs
    return rewrite_full(corpus, GOLDENS / "full.tsv", full_runs.member_names())


def _report_goldens(corpus: Path | None = None) -> list[str]:
    moved = rewrite_goldens() + rewrite_printed() + (_rewrite_corpus(corpus) if corpus else [])
    if not moved:
        return ["no golden moved"]
    return [f"moved {name}" for name in moved] + [f"declare what moved with `{DECLARE}`"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regenerate.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--python314", required=True)
    rec.add_argument("--node-modules", type=Path, default=NODE_MODULES)
    sub.add_parser("history")
    gold = sub.add_parser("goldens")
    gold.add_argument("--corpus", type=Path, help="a built full corpus: also rewrite goldens/full.tsv")
    return parser


def _this_tree_first() -> None:
    """This checkout's src/ first in this process, and src/ then tests/ first on
    the PYTHONPATH the commands it starts inherit, as change control's own run of
    this script gets them: the goldens measure this tree's crapkit, never the one
    this python has installed (in a linked worktree, another checkout's code or a
    release wheel)."""
    sys.path.insert(0, str(REPO / "src"))
    paths = (str(REPO / "src"), str(REPO / "tests"), os.environ.get("PYTHONPATH"))
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, paths))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _this_tree_first()
    commands = {
        "record": lambda: [f"recorded {name}" for name in record(args.python314,
                                                                 args.node_modules)],
        "history": lambda: [f"history/small.bundle main = {build_history()}"],
        "goldens": lambda: _report_goldens(args.corpus),
    }
    print("\n".join(commands[args.command]()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
