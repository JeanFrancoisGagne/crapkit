"""The small corpus holds what the plan says it holds, a coverage run over it
exits 0, and every surface of three measured runs equals its committed golden.

What the corpus must hold is written here from outside crapkit's code: the
suffix table in README.md ("Languages"), and one file per past-bug shape the
accuracy plan lists. A golden is crapkit's own output, normalized: the golden
tests carry the `golden` marker and count as no independent method.
"""
from pathlib import Path

import pytest

from accuracy.corpus_goldens import golden_runs
from accuracy.kit import corpus_run, goldens, repos

SMALL = corpus_run.SMALL
MAX_BYTES = 3 * 1024 * 1024
# README.md "Languages", the Files column, read as written.
README_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".swift", ".go",
                   ".rs", ".sh", ".bash", ".ps1", ".psm1", ".c", ".cc", ".cpp", ".cxx", ".h",
                   ".hpp", ".m", ".mm", ".java", ".zig")
# One file per past-bug shape (the accuracy plan's corpus section), with a
# string that proves the shape is in it.
SHAPES = {
    "src/py/signatures.py": "def __init__(self, args, bufsize=-1",   # #72 Popen.__init__
    "src/py/generics.py": "def first[T](",                            # PEP 695
    "src/py/tstrings.py": 't"Dear {name},"',                          # PEP 750
    "src/py/excepts.py": "except ValueError, TypeError:",             # PEP 758
    "src/py/nested.py": "        def inner(cell):",                   # three-deep defs
    "src/py/oneline.py": "def twice(x): return x * 2",                # one-line defs
    "src/py/fstrings.py": "f\"{ {'n': item['name']}['n'] }\"",        # f-string brackets
    "src/py/pragmas.py": "# pragma: no cover",                        # pragma lines
    "src/py/matching.py": "match value:",                             # D4
    "src/web/templates.ts": "`${count > 1 ? `${count} items`",        # nested template
    "src/web/arrows.js": "export const up = (x) =>",                  # sibling arrows, shared span
    "src/web/generic.ts": "export function firstOf<T>(",              # D1 generic declaration
    "src/web/nullish.ts": "options.host ??= ",                        # D2
    "src/native/ifdef.c": "#ifdef _WIN32",                            # #ifdef twins
    "src/native/matcher.rs": '"zeta" => 6,',                          # 7-arm match
    "src/scripts/deep.sh": "                if [ -w \"$1\" ]; then",  # four-deep if
    "src/scripts/switch.ps1": "{ $_ -ge 1GB }",                       # D3 script-block arm
    "src/scripts/Params.psm1": "    param(",                          # D12 param() block
    "src/py/twins.py": 'def run(self, mode="fast"):',                 # a handle with a quote
    "src/py/données.py": "def café(",                                 # non-ASCII names
}
LINE_ENDINGS = {"src/py/crlf.py": b"\r\n", "src/py/cr_only.py": b"\r", "src/py/bom.py": b"\n"}


def _files() -> list[Path]:
    return sorted(path for path in SMALL.rglob("*") if path.is_file())


def test_the_small_corpus_is_three_megabytes_or_less():
    assert sum(path.stat().st_size for path in _files()) <= MAX_BYTES


def _without_file(names: list[str], suffixes: tuple[str, ...]) -> list[str]:
    return [suffix for suffix in suffixes if not any(name.endswith(suffix) for name in names)]


def test_every_readme_suffix_has_a_file_and_one_suffix_is_uppercase():
    names = [path.name for path in _files()]

    assert _without_file(names, README_SUFFIXES) == []
    assert [name for name in names if Path(name).suffix.isupper()] == ["Upper.PY"]


def test_every_past_bug_shape_has_its_file():
    missing = [path for path, text in SHAPES.items()
               if text not in (SMALL / path).read_text(encoding="utf-8")]

    assert missing == []


def test_the_line_ending_and_encoding_files_hold_their_bytes():
    endings = {path: (SMALL / path).read_bytes() for path in LINE_ENDINGS}

    assert endings["src/py/crlf.py"].count(b"\r\n") == endings["src/py/crlf.py"].count(b"\n") > 0
    assert b"\n" not in endings["src/py/cr_only.py"] and b"\r" in endings["src/py/cr_only.py"]
    assert endings["src/py/bom.py"].startswith(b"\xef\xbb\xbf")
    assert b"Caf\xe9" in (SMALL / "src" / "scripts" / "cp1252.ps1").read_bytes()


def test_every_vendor_slice_carries_its_member_license():
    table = golden_runs._table()["member"]
    missing = [f"{name}: {Path(member['license']).name}" for name, member in table.items()
               if member.get("slice")
               and not (SMALL / "vendor" / name / Path(member["license"]).name).is_file()]

    assert missing == []


@pytest.mark.process
def test_the_history_bundle_ends_at_the_small_corpus(tmp_path):
    clone = golden_runs.clone_history(tmp_path / "history")
    tree = repos.tree(clone)
    tree = {path: data for path, data in tree.items() if not path.startswith(".git/")}
    count = int(repos.git(clone, "rev-list", "--count", "HEAD").strip())

    assert tree == repos.tree(SMALL)
    assert count == 60


@pytest.mark.process
def test_small_corpus_coverage_run_exits_zero(small_corpus):
    """R24: a line two functions share once ended the run; the README says each
    such function scores untested and the run goes on (Flags, split-lines)."""
    assert small_corpus.codes["coverage.json"] == 0
    assert small_corpus.codes["inventory.txt"] == 0


def _problems(name: str, texts: dict[str, str]) -> list[str]:
    return goldens.compare(golden_runs.GOLDENS / name, texts)


@pytest.mark.process
@pytest.mark.golden
def test_the_small_run_equals_its_goldens(small_corpus):
    problems = _problems("small", golden_runs.normalized(small_corpus))

    assert problems == [], "\n".join([*problems, goldens.FIX])


@pytest.mark.process
@pytest.mark.golden
def test_the_session_equals_its_goldens(tmp_path_factory):
    run = golden_runs.session(golden_runs.shared_base(tmp_path_factory))
    problems = _problems("session", golden_runs.normalized(run))

    assert problems == [], "\n".join([*problems, goldens.FIX])


@pytest.mark.process
@pytest.mark.golden
def test_the_history_run_equals_its_goldens(tmp_path_factory):
    run = golden_runs.history(golden_runs.shared_base(tmp_path_factory))
    problems = _problems("history", golden_runs.normalized(run))

    assert problems == [], "\n".join([*problems, goldens.FIX])

