"""Source files, lane output and the files crapkit writes, fed through the CLI
in every encoding a real repo holds.

A Python file saved in cp1252 or Latin-1 under a coding cookie is a program its
interpreter runs, and PowerShell 5.1 writes UTF-16. mutate read such a source
as UTF-8 with replacement and wrote it back as UTF-8, so every accented byte
outside the mutated line became EF BF BD and a mutant counted as killed for a
reason the mutation did not cause. brief's `source` read the same file with
replacement while the function name beside it read right. A lane prints what
its runner prints, in any bytes, and crapkit quotes it back. The HTML report,
SARIF, the TSV export and the marks file carry repo text out to other readers.

One parametrized test per site, each row a variation the utf8-author hunt ran:
the red ones with the green controls beside them.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import cli_runner
from foreign_bytes import APP, LANE_SCRIPT, answered, commit, repository, scored_repo, shown

run_cli = cli_runner(encoding="utf-8", errors="replace")


# --- a mutant written back: mutate_pool.run_one ----------------------------------
#
# utf8-author-shape-19, -boundary-11 and -history-6. The suite checks only the
# accented constant, never f, so each mutant of f survives in the UTF-8 file.
# The suite also logs every source it runs against, to show that a mutant
# changes only its own line and leaves every other byte as the file held it.

BODY = "NAME = 'café'\n\n\ndef f(x):\n    return x > 0\n"
SOURCE_ROWS = [
    pytest.param(b"# -*- coding: cp1252 -*-\n" + BODY.encode("cp1252"), id="source-cp1252-coding-cookie"),
    pytest.param(b"# -*- coding: latin-1 -*-\n" + BODY.encode("latin-1"), id="source-latin1-coding-cookie"),
    pytest.param(BODY.encode(), id="source-utf8-control"),
    pytest.param(b"\xef\xbb\xbf" + BODY.encode(), id="source-utf8-bom"),
    pytest.param(BODY.replace("\n", "\r\n").encode(), id="source-utf8-crlf"),
    pytest.param("# 渡辺 \U0001f680\n".encode() + BODY.encode(), id="source-utf8-cjk-emoji"),
]


def _suite(log: Path) -> bytes:
    return (b"import sys\nfrom pathlib import Path\nsys.path.insert(0, 'src')\n"
            b"with open(%r, 'ab') as seen:\n    seen.write(repr(Path('src/app.py').read_bytes()).encode() + b'\\n')\n"
            b"import app\nassert app.NAME == 'caf\\u00e9', app.NAME\n" % str(log))


def _mutation_repo(root: Path, source: bytes, log: Path) -> Path:
    toml = (b'[crapkit]\ntarget = 6\nmutation_command = "python t.py"\n\n'
            b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')
    repository(root)
    commit(root, {b"crapkit.toml": toml, b"t.py": _suite(log), b"src/app.py": source, b".gitignore": b".crapkit/\n"})
    return root


def _changed_lines(original: bytes, seen: bytes) -> list[int]:
    old, new = original.splitlines(keepends=True), seen.splitlines(keepends=True)
    assert len(old) == len(new), (original, seen)
    return [number for number, (a, b) in enumerate(zip(old, new)) if a != b]


@pytest.mark.parametrize("source", SOURCE_ROWS)
def test_a_mutant_changes_only_its_own_line_in_any_source_encoding(tmp_path, source):
    log = tmp_path / "seen.log"
    repo = _mutation_repo(tmp_path / "repo", source, log)

    res = run_cli(repo, "mutate", "--files", "src/app.py", "--json")

    answered(res)
    out = json.loads(res.stdout)
    assert (out["mutants"], out["killed"]) == (2, 0), shown(res)
    assert (repo / "src" / "app.py").read_bytes() == source
    mutated = source.splitlines().index(b"    return x > 0")
    seen = [ast.literal_eval(line.decode("ascii")) for line in log.read_bytes().splitlines()]
    assert seen[0] == source and len(seen) == 3, seen
    assert all(_changed_lines(source, each) == [mutated] for each in seen[1:]), seen


# --- a function's source handed to an agent: brief --json `source` --------------
#
# utf8-author-boundary-10. AGENTS.md tells an agent to edit from `source`, so
# it must read the character the file holds, as long_name beside it does.

BRIEF_ROWS = [
    pytest.param(APP.replace(b"    return 0\n", b"    return 0  # caf\xe9\n"), id="source-cp1252"),
    pytest.param(APP.decode().replace("    return 0\n", "    return 0  # café\n").encode("utf-16"),
                 id="source-utf16-bom"),
    pytest.param(b"\xef\xbb\xbf" + APP.replace(b"    return 0\n", "    return 0  # café\n".encode()),
                 id="source-utf8-bom"),
    pytest.param(APP.replace(b"    return 0\n", "    return 0  # café\n".encode()), id="source-utf8-control"),
]


# Python's tokenizer reads no UTF-16, so this lane reports pick() without
# parsing the file.
FIXED_LANE = (b'import json\nfn = {"start_line": 1, "executed_lines": [1, 2], "missing_lines": [], '
              b'"summary": {"covered_lines": 2, "num_statements": 2, "num_branches": 0, "covered_branches": 0}}\n'
              b'json.dump({"meta": {"branch_coverage": True}, "files": {"src/app.py": {"functions": {"pick": fn}}}}, '
              b'open("cov.json", "w"))\n')


@pytest.mark.parametrize("source", BRIEF_ROWS)
def test_brief_hands_an_agent_the_source_the_file_holds(tmp_path, source):
    repo = scored_repo(tmp_path / "repo", source)
    commit(repo, {b"lane.py": FIXED_LANE})
    answered(run_cli(repo, "coverage"))

    res = run_cli(repo, "brief", "src/app.py", "pick", "--json")

    answered(res)
    assert "return 0  # café" in json.loads(res.stdout)["source"], shown(res)


# --- a lane's own output: logs.command_log and lanes._log_lines -----------------
#
# utf8-author-shape-26 and -boundary-24, green on both trees. A lane's bytes
# stay raw in its log; a passing lane is a measured run, and a failing one is a
# refusal that names the lane.

NOISE = {
    "log-invalid-utf8": b"caf\xe9 \xff\n",
    "log-cp1252-console": b"O\x92Brien caf\xe9\n",
    "log-utf16": "李 café\n".encode("utf-16"),
    "log-nul-bytes": b"before\x00after\n",
    "log-valid-cjk-emoji": "渡辺 \U0001f680\n".encode(),
    "log-crlf": b"line one\r\nline two\r\n",
}


def _noisy_lane(noise: bytes, fail: bool) -> bytes:
    """A failing lane writes no artifact: one that exits nonzero but writes its
    coverage is a measured run with failing tests, not a failed lane."""
    shout = (b"import sys\nsys.stdout.buffer.write(%r); sys.stdout.flush()\n"
             b"sys.stderr.buffer.write(%r); sys.stderr.flush()\n" % (noise, noise))
    return shout + b"sys.exit(3)\n" if fail else LANE_SCRIPT + shout


@pytest.mark.parametrize("fail, code", [(False, 0), (True, 5)], ids=["passes", "fails"])
@pytest.mark.parametrize("noise", list(NOISE))
def test_a_lane_that_prints_any_bytes_is_measured_or_refused_by_name(tmp_path, noise, fail, code):
    repo = scored_repo(tmp_path / "repo")
    commit(repo, {b"lane.py": _noisy_lane(NOISE[noise], fail)})

    res = run_cli(repo, "coverage")

    answered(res, code)
    assert (code == 5) is ("lane 'unit'" in res.stderr), shown(res)
    assert NOISE[noise] in (repo / ".crapkit" / "lane-unit.log").read_bytes()


# --- files crapkit writes for other readers ------------------------------------
#
# utf8-author-shape-29 and -boundary-25, green on both trees. The HTML report,
# SARIF, the TSV export and --json all name 李雷Café in src/café.ts, and a
# `ratchet seed` over a CRLF marks file keeps its endings.

TS = "export function 李雷Café(kind: number): number {\n  if (kind) { return 1; }\n  return 0;\n}\n".encode()
TS_TOML = (b'[crapkit]\ntarget = 1\nworklist_floor = 1\n\n'
           b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
           b'[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\nartifact = "cov.json"\n'
           b'parser = "istanbul"\nscopes = ["src"]\n')
MAKE_COV = (b"import json, os\n"
            b"app = os.path.join(os.getcwd(), 'src', 'caf\\u00e9.ts')\n"
            b"loc = {'start': {'line': 1}, 'end': {'line': 4}}\n"
            b"cov = {app: {'path': app, 'fnMap': {'0': {'name': '\\u674e\\u96f7Caf\\u00e9', 'decl': loc, 'loc': loc}},\n"
            b"             'f': {'0': 0}, 'branchMap': {}, 'b': {}, 'statementMap': {}, 's': {}}}\n"
            b"json.dump(cov, open('cov.json', 'w'))\n")
NAME = "李雷Café"


@pytest.fixture(scope="module")
def written(tmp_path_factory) -> Path:
    repo = repository(tmp_path_factory.mktemp("writers") / "repo")
    commit(repo, {b"crapkit.toml": TS_TOML, "src/café.ts".encode(): TS, b"make_cov.py": MAKE_COV,
                  b".gitignore": b".crapkit/\ncov.json\nout/\n"})
    answered(run_cli(repo, "coverage", "--sarif", "out/crapkit.sarif"))
    return repo


def _html(repo: Path) -> str:
    answered(run_cli(repo, "report", "--out", "out/report.html"))
    page = (repo / "out" / "report.html").read_bytes().decode("utf-8")
    assert '<meta charset="utf-8">' in page
    return page


def _sarif(repo: Path) -> str:
    doc = json.loads((repo / "out" / "crapkit.sarif").read_bytes().decode("utf-8"))
    blob = json.dumps(doc, ensure_ascii=False)
    assert "src/caf%C3%A9.ts" in blob, blob  # an artifact location is a URI (RFC 3986)
    return blob


def _tsv(repo: Path) -> str:
    answered(run_cli(repo, "inventory", "--export", "out/inv.tsv"))
    return (repo / "out" / "inv.tsv").read_bytes().decode("utf-8")


def _json(repo: Path) -> str:
    res = run_cli(repo, "worklist", "--json")
    answered(res)
    return json.dumps(json.loads(res.stdout), ensure_ascii=False)


@pytest.mark.parametrize("read", [_html, _sarif, _tsv, _json], ids=["html-report", "sarif", "tsv-export", "json"])
def test_a_file_crapkit_writes_is_utf8_and_names_the_function(written, read):
    assert NAME in read(written)


def test_ratchet_seed_keeps_the_crlf_endings_of_the_marks_file(tmp_path):
    repo = repository(tmp_path / "repo")
    commit(repo, {b"crapkit.toml": TS_TOML, "src/café.ts".encode(): TS, b"make_cov.py": MAKE_COV,
                  b".gitignore": b".crapkit/\ncov.json\n"})
    answered(run_cli(repo, "coverage"))
    answered(run_cli(repo, "ratchet", "seed"))
    marks = repo / "crapkit-ratchet.tsv"
    marks.write_bytes(marks.read_bytes().replace(b"\n", b"\r\n"))

    answered(run_cli(repo, "ratchet", "seed"))

    data = marks.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), data
    assert NAME.encode() in data
